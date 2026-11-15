"""Apply ranked catalog matches and verified covers, with a durable batch journal."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import threading
import time
import uuid

from . import metadata_lookup, preparation
from .metadata_matching import catalog_identity

ACTIVE = ('running', 'cancelling')
MANAGERS = {}
MANAGERS_LOCK = threading.Lock()
RUNNER = ThreadPoolExecutor(max_workers=1, thread_name_prefix='metadata-batch')


def fingerprint(track):
    metadata = track.get('metadata')
    if isinstance(metadata, str):
        metadata = json.loads(metadata)
    # Opening the editor stores the file's current tags (and embedded cover) as
    # an untouched baseline. Only staged edits count as a change, so reading
    # metadata never invalidates a queued item.
    metadata = metadata if isinstance(metadata, dict) else {}
    staged = {'pending': metadata.get('pending') or {}, 'artwork_pending': bool(metadata.get('artwork_pending')),
              'artwork_path': metadata.get('artwork_path') if metadata.get('artwork_pending') else None,
              'source': metadata.get('source'), 'automatic': metadata.get('automatic')}
    return hashlib.sha256(json.dumps({'title': track.get('title'), 'artist': track.get('artist'), 'file_hash': track.get('file_hash'),
                                     'staged': staged}, sort_keys=True).encode()).hexdigest()


def review_token(track):
    return hashlib.sha256(json.dumps({key: track.get(key) for key in ('title', 'artist', 'file_hash')}, sort_keys=True).encode()).hexdigest()


def review(api, track):
    try:
        saved = json.loads((api.ROOT / 'metadata-review' / (track['id'] + '.json')).read_text())
        return saved if saved.get('token') == review_token(track) else None
    except (OSError, ValueError):
        return None


def candidates_for_apply(result, identity):
    # Unknown artists and weak matches stay available in Review instead of
    # letting a common title pick an unrelated recording automatically.
    if not identity['artist']:
        return []
    candidates = [c for c in result.get('candidates', [])
                  if c.get('match_status') != 'rejected' and c.get('rank', 0) >= 85
                  and c.get('title_similarity', 0) >= .9 and c.get('artist_similarity', 0) >= .9
                  and c.get('values', {}).get('album') and c.get('recording_id')]
    return sorted(candidates, key=lambda c: (c.get('match_status') != 'strong', -c['rank'],
                                           not c.get('artwork_available', False), c['values'].get('date') or '9999'))


class BatchManager:
    def __init__(self, api):
        self.api = api
        self.root = api.ROOT
        self.path = self.root / 'metadata-batch' / 'latest.json'
        self.lock = threading.RLock()
        self.stop = threading.Event()
        self.job = None
        try:
            self.job = json.loads(self.path.read_text())
            if self.job['state'] in ACTIVE:
                self.job['state'] = 'interrupted'
                for item in self.job['items']:
                    if item['state'] in ('queued', 'running'):
                        item.update(state='cancelled', error='The app closed before this track finished.')
                self.save()
        except (OSError, ValueError, KeyError, TypeError):
            self.job = None

    def save(self):
        self.api.atomic(self.path, json.dumps(self.job).encode())

    def snapshot(self):
        if self.job is None:
            return None
        job = json.loads(json.dumps(self.job))
        job['processed'] = sum(item['state'] not in ('queued', 'running', 'cancelled') for item in job['items'])
        for state in ('applied', 'needs_review', 'failed', 'skipped', 'cancelled'):
            job[state] = sum(item['state'] == state for item in job['items'])
        # Counted only from this run's own writes; journals from before the
        # option existed have no such fields and report zero.
        job['written'] = sum(item.get('written') is True for item in job['items'])
        job['write_failed'] = sum(bool(item.get('write_error')) for item in job['items'])
        for item in job['items']:
            item.pop('baseline', None)
        return job

    def dispatch(self, db, method, params):
        with self.lock:
            if method == 'metadata.batch.latest':
                return self.snapshot()
            if method == 'metadata.batch.start':
                if self.job and self.job['state'] in ACTIVE:
                    raise ValueError('Metadata analysis is already running.')
                ids = params.get('track_ids')
                if not isinstance(ids, list) or not 1 <= len(ids) <= 5000 or any(not isinstance(tid, str) for tid in ids) or len(set(ids)) != len(ids):
                    raise ValueError('Choose between 1 and 5000 distinct library tracks.')
                write_tags = params.get('write_tags', False)
                if not isinstance(write_tags, bool):
                    raise ValueError('write_tags must be true or false.')
                if write_tags:
                    # Fail before any lookup instead of once per track.
                    preparation.tag_writer_python()
                items = []
                for tid in ids:
                    row = db.execute('SELECT id,title,artist,metadata,file_hash FROM tracks WHERE id=?', (tid,)).fetchone()
                    if not row:
                        raise ValueError('A selected track is no longer in the library.')
                    items.append({'id': tid, 'title': row['title'], 'state': 'queued', 'error': None, 'baseline': fingerprint(dict(row))})
                self.stop.clear()
                self.job = {'id': str(uuid.uuid4()), 'state': 'running', 'total': len(items),
                            'started_at': time.time(), 'write_tags': write_tags, 'items': items}
                self.save()
                pending = self.snapshot()
                RUNNER.submit(self.run)
                return pending
            if not self.job or params.get('id') != self.job['id']:
                raise ValueError('Metadata batch expired. Start another analysis.')
            if method == 'metadata.batch.get':
                return self.snapshot()
            if method == 'metadata.batch.cancel':
                if self.job['state'] in ACTIVE:
                    self.stop.set()
                    self.job['state'] = 'cancelling'
                    self.save()
                return self.snapshot()
            raise ValueError('Unknown metadata batch method')

    def update(self, index, **changes):
        with self.lock:
            self.job['items'][index].update(changes)
            self.save()

    def store_review(self, track, result, state, reason=None, selected=None):
        candidates = result.get('candidates', [])
        if selected:
            candidates = [selected] + [c for c in candidates if c != selected]
        self.api.atomic(self.root / 'metadata-review' / (track['id'] + '.json'), json.dumps({
            'token': review_token(track), 'state': state, 'reason': reason,
            'query': result.get('query'), 'warnings': result.get('warnings', []), 'candidates': candidates[:50],
        }).encode())

    def process(self, index):
        staged_revision = self.apply(index)
        # Outside apply()'s error handling: nothing that goes wrong while
        # writing may turn an applied track into a failed one.
        if staged_revision and self.job.get('write_tags'):
            self.write_applied(index, self.job['items'][index]['id'], staged_revision)

    def apply(self, index):
        """Stage the best match and cover; return the staged revision if applied."""
        item = self.job['items'][index]
        result = {}
        track = None
        try:
            with self.api.connect() as db:
                track = self.api.track(db, item['id'])
            if fingerprint(track) != item['baseline']:
                self.update(index, state='skipped', error='Metadata changed after this batch started.')
                return
            if track['missing']:
                self.update(index, state='skipped', error='Relink the missing audio file before automatic analysis.')
                return
            state = preparation.values(self.api, track)
            identity = catalog_identity(state['values']['title'], state['values']['artist'])
            if self.stop.is_set():
                self.update(index, state='cancelled')
                return
            result = metadata_lookup.search(self.api, preparation.request, identity, track['duration'])
            if self.stop.is_set():
                self.update(index, state='cancelled')
                return
            eligible = candidates_for_apply(result, identity)
            title, artist = state['values']['title'], state['values']['artist']
            if not eligible and title.strip() and artist.strip():
                # Playlists written "Song - Artist" were imported with the two fields reversed. The
                # same strict rules then decide; a match also puts title and artist back in place.
                swapped = catalog_identity(artist, title)
                if self.stop.is_set():
                    self.update(index, state='cancelled')
                    return
                reversed_result = metadata_lookup.search(self.api, preparation.request, swapped, track['duration'])
                reversed_eligible = candidates_for_apply(reversed_result, swapped)
                if reversed_eligible:
                    identity, result, eligible = swapped, reversed_result, reversed_eligible
            if self.stop.is_set():
                self.update(index, state='cancelled')
                return
            if not eligible:
                reason = 'No sufficiently similar release with an identified artist and album was found.'
                self.store_review(track, result, 'needs_review', reason)
                self.update(index, state='needs_review', error=reason)
                return
            self.update(index, stage='Fetching album cover')
            cover = None
            selected = None
            tried = set()
            for candidate in eligible:
                if self.stop.is_set():
                    break
                # Do not trade a substantially worse recording match for artwork.
                if candidate['rank'] < eligible[0]['rank'] - 8:
                    continue
                key = (candidate.get('provider', 'MusicBrainz'), candidate.get('release_id'))
                if key in tried:
                    continue
                tried.add(key)
                try:
                    cover = preparation.fetch_cover(self.api, candidate, candidate.get('artwork_url'))
                    selected = candidate
                    break
                except Exception:
                    if len(tried) >= 5:
                        break
            if self.stop.is_set():
                self.update(index, state='cancelled')
                return
            if not cover:
                reason = 'A matching release was found, but no usable album cover could be downloaded. Review the matches or choose an image.'
                self.store_review(track, result, 'needs_review', reason)
                self.update(index, state='needs_review', error=reason)
                return
            source = {key: selected[key] for key in ('provider', 'recording_id', 'release_id') if selected.get(key)}
            source.setdefault('provider', 'MusicBrainz')
            # A catalog date the tag writer cannot store is dropped, not fatal.
            fields = {key: value for key, value in selected['values'].items() if value and key in preparation.FIELDS
                      and (key != 'date' or preparation.DATE.fullmatch(value))}
            # Keep network work outside the transaction. The lock makes Cancel
            # and this final metadata write mutually exclusive.
            with self.lock:
                if self.stop.is_set():
                    self.update(index, state='cancelled')
                    return
                with self.api.connect() as db:
                    db.execute('BEGIN IMMEDIATE')
                    current = self.api.track(db, track['id'])
                    if fingerprint(current) != item['baseline']:
                        self.update(index, state='skipped', error='Metadata changed while analysis was running; your edits were kept.')
                        return
                    updated = preparation.dispatch(self.api, db, 'metadata.save', {
                        'id': track['id'], 'values': fields, 'artwork_path': cover, 'source': source,
                    })
                    metadata = updated['metadata']
                    metadata['automatic'] = {'job_id': self.job['id'], 'rank': selected['rank'],
                                             'match_status': selected['match_status'], 'applied_at': time.time(), 'reviewed': False}
                    db.execute('UPDATE tracks SET metadata=? WHERE id=?', (json.dumps(metadata), track['id']))
                staged_revision = preparation.revision(metadata, updated['file_hash'])
                self.update(index, state='applied', provider=source['provider'], rank=selected['rank'])
                try:
                    self.store_review(updated, result, 'applied', selected=selected)
                except OSError:
                    try:
                        self.update(index, error='Applied metadata and cover. Saved match details could not be cached.')
                    except OSError:
                        pass
                return staged_revision
        except Exception as error:
            if track:
                try:
                    self.store_review(track, result, 'failed', str(error))
                except OSError:
                    pass
            self.update(index, state='cancelled' if self.stop.is_set() else 'failed', error=str(error))

    def write_applied(self, index, track_id, staged_revision):
        # Only reached for a track this run just applied. The staged metadata
        # stays whatever happens here; a failed write is recorded, not undone.
        changes = {'written': False}
        try:
            with self.lock:
                if self.stop.is_set():
                    self.update(index, **changes)
                    return
                self.update(index, stage='Writing tags to the file')
            # The write runs outside the lock so Stop and progress stay responsive;
            # a write that has started always runs to its verified end.
            with self.api.connect() as db:
                # The revision makes an edit saved since the apply win over this write.
                outcome = preparation.dispatch(self.api, db, 'metadata.write', {'id': track_id, 'revision': staged_revision})
            changes = {'written': True, 'backup': outcome.get('backup')}
            if 'artwork_embedded' in outcome:
                changes['artwork_embedded'] = outcome['artwork_embedded']
            if outcome.get('note'):
                changes['write_note'] = outcome['note']
        except Exception as error:
            changes = {'written': False, 'write_error': str(error) or 'Tag write failed'}
        try:
            self.update(index, **changes)
        except OSError:
            # The journal could not be saved; the in-memory item is already updated.
            pass

    def run(self):
        try:
            self.run_workers()
        except Exception as error:
            with self.lock:
                self.job.update(state='failed', error=str(error), finished_at=time.time())
                try:
                    self.save()
                except OSError:
                    pass

    def run_workers(self):
        def worker():
            while True:
                with self.lock:
                    if self.stop.is_set():
                        return
                    index = next((i for i, item in enumerate(self.job['items']) if item['state'] == 'queued'), None)
                    if index is None:
                        return
                    self.update(index, state='running', stage='Finding best metadata')
                self.process(index)
        with ThreadPoolExecutor(max_workers=2, thread_name_prefix='metadata-track') as pool:
            futures = [pool.submit(worker) for _ in range(2)]
            for future in futures:
                future.result()
        with self.lock:
            for item in self.job['items']:
                if item['state'] in ('queued', 'running'):
                    item['state'] = 'cancelled'
            self.job.update(state='cancelled' if self.stop.is_set() else 'completed', finished_at=time.time())
            self.save()


def manager(api):
    key = str(Path(api.ROOT).resolve())
    with MANAGERS_LOCK:
        if key not in MANAGERS:
            MANAGERS[key] = BatchManager(api)
        return MANAGERS[key]


def shutdown():
    with MANAGERS_LOCK:
        for batch in MANAGERS.values():
            with batch.lock:
                if batch.job and batch.job['state'] in ACTIVE:
                    batch.stop.set()
                    batch.job['state'] = 'cancelling'
                    batch.save()
