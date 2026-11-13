"""Find likely sources for set entries while the owner reads the report.

Discovery only searches. It never resolves media, reads a browser session,
enqueues or downloads: "Create playlist" reuses a fresh result instead of
searching again, and the existing automatic download rules are unchanged.
"""
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import re
import sqlite3
import threading
import time

from . import download_http, providers, scan_audio, service_links, set_import, set_scan_jobs, soundcloud_download, youtube_audio
from .library_imports import ImportCancelled
from .metadata_matching import normalized, search_identity

VERSION = 1
FRESH = 6*3600
EXECUTOR = ThreadPoolExecutor(3, thread_name_prefix='set-discovery')
# Catalog enrichment is paced at one MusicBrainz request a second, so it never holds a search thread.
CATALOG = ThreadPoolExecutor(1, thread_name_prefix='set-discovery-catalog')
LOCK = threading.Lock()
WORK = {}
CLOSING = threading.Event()
STATES = ('queued','searching','ready','choose','none','failed')
RETRY_WRITE = 1


class Work:
    """One report's discovery: a shared stop Event and the entries still to search."""
    def __init__(self):
        self.stop = threading.Event()
        self.pending = set()
        self.running = set()
        # Catalog enrichments still queued or running: they share the stop Event, so
        # the Work stays listed (and cancellable) until they finish.
        self.catalog = 0


def root_of(owner):
    """The library folder of the engine module or of an open connection."""
    if hasattr(owner, 'ROOT'):
        return str(Path(owner.ROOT).resolve())
    return str(Path(owner.execute('PRAGMA database_list').fetchone()[2]).resolve().parent)


def discovery_key(item):
    return [item.get('artist') or '', item.get('title') or '', list(item.get('listed_links') or [])]


def title_only(item):
    return not item.get('artist') and not item.get('listed_links') and len(normalized(item.get('title') or '').split()) >= 2


def eligible(item, row=None):
    if row is not None and (row['track_id'] or row['download_id']):
        return False
    if item.get('unknown') or not item.get('title'):
        return False
    return bool(item.get('artist') or item.get('listed_links') or title_only(item))


def fresh(item, states=('ready',)):
    found = item.get('discovery')
    return (isinstance(found, dict) and found.get('v') == VERSION and found.get('state') in states
            and found.get('key') == discovery_key(item) and time.time()-(found.get('at') or 0) < FRESH)


def state(key, name, **fields):
    return {'v':VERSION, 'state':name, 'key':key, 'at':time.time(), 'matches':[], 'reference':None,
            'free_links':[], 'error':None, **fields}


def merge_detail(db, sid, entry_id, expected_key, fields, *, linked=False, transaction=True):
    """Merge fields into an entry's latest detail, by rowid, as a compare-and-set.

    The write is skipped when the entry is gone, already has a track or download
    (unless ``linked``), or no longer has ``expected_key``. ``fields`` may be a
    function of the latest detail that returns the fields, or None to skip.
    """
    if transaction:
        db.execute('BEGIN IMMEDIATE')
    try:
        row = db.execute('SELECT detail,track_id,download_id FROM dj_set_items WHERE set_id=? AND rowid=?', (sid, entry_id)).fetchone()
        latest = None
        if row and (linked or not (row['track_id'] or row['download_id'])):
            latest = json.loads(row['detail'])
            if expected_key is not None and discovery_key(set_import.normalize_credit(dict(latest))) != expected_key:
                latest = None
        if latest is not None:
            update = fields(latest) if callable(fields) else fields
            if update is None:
                latest = None
            else:
                latest.update(update)
                db.execute('UPDATE dj_set_items SET detail=? WHERE set_id=? AND rowid=?', (json.dumps(latest), sid, entry_id))
        if transaction:
            db.commit()
        return latest
    except BaseException:
        if transaction:
            db.rollback()
        raise


def schedule(api, sid, entry_ids=None):
    """Queue eligible entries in set order; returns how many were queued."""
    if CLOSING.is_set():
        return 0
    root = root_of(api)
    with api.connect_existing() as db:
        # Once the playlist exists, finding sources is the owner's "find tracks" step, never a background one.
        if not db.execute('SELECT 1 FROM dj_sets WHERE id=? AND playlist_id IS NULL', (sid,)).fetchone():
            return 0
        rows = db.execute('SELECT rowid AS entry_id,detail,track_id,download_id FROM dj_set_items WHERE set_id=? ORDER BY position', (sid,)).fetchall()
    wanted = None if entry_ids is None else {str(entry_id) for entry_id in entry_ids}
    chosen = []
    with LOCK:
        work = WORK.get((root, sid))
        if work is None:
            work = WORK[root, sid] = Work()
        for row in rows:
            entry_id = row['entry_id']
            if (wanted is not None and str(entry_id) not in wanted) or entry_id in work.pending or entry_id in work.running:
                continue
            item = set_import.normalize_credit(json.loads(row['detail']))
            if not eligible(item, row) or fresh(item):
                continue
            work.pending.add(entry_id)
            chosen.append((entry_id, discovery_key(item)))
        if not work.pending and not work.running and not work.catalog:
            WORK.pop((root, sid), None)
    if not chosen:
        return 0
    try:
        with api.connect_existing() as db:
            db.execute('BEGIN IMMEDIATE')
            for entry_id, key in chosen:
                merge_detail(db, sid, entry_id, key, {'discovery':state(key, 'queued')}, transaction=False)
            db.commit()
    except BaseException:
        with LOCK:
            work.pending.difference_update(entry_id for entry_id, _ in chosen)
        raise
    for entry_id, _ in chosen:
        EXECUTOR.submit(run, api, root, sid, entry_id, work)
    return len(chosen)


def sweep(api, sid=None):
    """Queue again what an earlier engine, or a search that could not save, left waiting or half searched."""
    with api.connect_existing() as db:
        found = db.execute("""SELECT i.set_id,group_concat(i.rowid) AS entries FROM dj_set_items i
            JOIN dj_sets s ON s.id=i.set_id AND s.playlist_id IS NULL
            WHERE i.track_id IS NULL AND i.download_id IS NULL AND (? IS NULL OR i.set_id=?)
            AND json_extract(i.detail,'$.discovery.state') IN ('queued','searching') GROUP BY i.set_id""", (sid, sid)).fetchall()
    return sum(schedule(api, row['set_id'], row['entries'].split(',')) for row in found)


def cancel(owner, sid):
    with LOCK:
        work = WORK.pop((root_of(owner), sid), None)
    if work:
        work.stop.set()


def stop_all():
    CLOSING.set()
    with LOCK:
        works = list(WORK.values())
        WORK.clear()
    for work in works:
        work.stop.set()


def active(owner, sid):
    with LOCK:
        work = WORK.get((root_of(owner), sid))
        return (set(work.pending), set(work.running)) if work else (set(), set())


def status(db, sid):
    pending, running = active(db, sid)
    return {'pending':len(pending), 'running':len(running)}


def run(api, root, sid, entry_id, work):
    with LOCK:
        if entry_id not in work.pending:
            return
        work.pending.discard(entry_id)
        if work.stop.is_set():
            return
        work.running.add(entry_id)
    try:
        process(api, sid, entry_id, work.stop, work)
    except Exception:
        # One entry's search must never stop the others.
        pass
    finally:
        with LOCK:
            work.running.discard(entry_id)
            release(root, sid, work)


def release(root, sid, work):
    """Forget a report's Work once nothing of it is left. The caller holds LOCK."""
    if not work.pending and not work.running and not work.catalog and WORK.get((root, sid)) is work:
        WORK.pop((root, sid), None)


def catalog(api, root, sid, entry_id, key, item, work):
    try:
        enrich(api, sid, entry_id, key, item, work.stop)
    except Exception:
        pass
    finally:
        with LOCK:
            work.catalog -= 1
            release(root, sid, work)


def process(api, sid, entry_id, stop, work=None):
    with api.connect_existing() as db:
        saved = db.execute('SELECT watch FROM dj_sets WHERE id=?', (sid,)).fetchone()
        row = db.execute('SELECT detail,track_id,download_id FROM dj_set_items WHERE set_id=? AND rowid=?', (sid, entry_id)).fetchone()
    if not saved or not row:
        return
    item = set_import.normalize_credit(json.loads(row['detail']))
    if not eligible(item, row):
        return
    key = discovery_key(item)
    control = set_scan_jobs.ScanControl(api, stop=stop)
    with control.activate():
        with api.connect_existing() as db:
            if merge_detail(db, sid, entry_id, key, {'discovery':state(key, 'searching')}) is None:
                return
        try:
            result = discover(api, item, saved['watch'])
        except ImportCancelled:
            return
        except (ValueError, OSError) as error:
            result = state(key, 'failed', error=str(error)[:300])
        except Exception:
            result = state(key, 'failed', error='The source search stopped unexpectedly.')
    if stop.is_set():
        return
    fields = {'discovery':result}
    if 'source_options' in item:
        fields['source_options'] = item['source_options']
    written = save(api, sid, entry_id, key, fields)
    if written is not None and result['state'] != 'failed' and not result.get('listed') and wants_catalog(item):
        if work is None:
            CATALOG.submit(enrich, api, sid, entry_id, key, item, stop)
            return
        with LOCK:
            work.catalog += 1
        CATALOG.submit(catalog, api, root_of(api), sid, entry_id, key, item, work)


def save(api, sid, entry_id, key, fields):
    """Write a finished search; a busy library gets one more try so the result is not lost.

    If both fail the entry stays 'searching', which the report shows as queued and
    ``sweep`` queues again.
    """
    try:
        with api.connect_existing() as db:
            return merge_detail(db, sid, entry_id, key, fields)
    except sqlite3.OperationalError:
        time.sleep(RETRY_WRITE)
        with api.connect_existing() as db:
            return merge_detail(db, sid, entry_id, key, fields)


def search_now(api, sid, entry_id, item, watch):
    """Search a title-only entry while its playlist is created: versions are offered, never chosen."""
    key = discovery_key(item)
    try:
        result = discover(api, item, watch)
    except ImportCancelled:
        return
    except (ValueError, OSError) as error:
        result = state(key, 'failed', error=str(error)[:300])
    except Exception:
        result = state(key, 'failed', error='The source search stopped unexpectedly.')
    if result['state'] == 'ready':
        # Never reached for a title-only entry; kept so nothing here is ever picked automatically.
        result = state(key, 'choose' if item.get('source_options') else 'none')
    fields = {'discovery':result}
    if 'source_options' in item:
        fields['source_options'] = item['source_options']
    try:
        save(api, sid, entry_id, key, fields)
    except sqlite3.Error:
        pass


def wait_for_mix():
    """Leave the connection to the mix download while one is in progress."""
    while not scan_audio.wait_until_idle(.5):
        set_scan_jobs.check()


def search(api, query):
    wait_for_mix()
    set_scan_jobs.check()
    return youtube_audio.search(api, query)


def listed_source(item, watch):
    for url in item.get('listed_links') or []:
        try:
            platform = service_links.identify(download_http.public_url(url))
        except ValueError:
            continue
        if not platform or platform[2] == watch:
            continue
        # Without a token a SoundCloud page is only a reference, so it is not a usable source.
        if platform[0] in youtube_audio.NAMES or (platform[0] == 'SoundCloud' and soundcloud_download.configured()):
            return platform[1] if platform[0] == 'SoundCloud' else url
    return None


def discover(api, item, watch):
    """Search for an entry's source without registering, resolving or enqueueing anything."""
    key = discovery_key(item)
    link = listed_source(item, watch)
    if link:
        return state(key, 'ready', matches=[[link, item['artist'], item['title'], None]], listed=True)
    matches, error = [], None
    try:
        found, suggestions = set_import.search_video_options(api, item, watch, search=lambda query: search(api, query))
    except ValueError as failure:
        found, suggestions, error = [], [], failure
    item['source_options'] = suggestions
    recommended = set_import.recommend_sources(item)
    matches = [[url, artist, song, score] for score, url, artist, song in found]
    if title_only(item) or not item.get('artist'):
        # Without an artist nothing is chosen automatically, only offered for review.
        if error and not suggestions:
            raise error
        return state(key, 'choose' if suggestions else 'none')
    if not (recommended or matches) and soundcloud_download.configured():
        try:
            matches += [[entry['url'], artist, song, None] for entry, artist, song in set_import.search_soundcloud_options(api, item)]
        except ValueError as failure:
            error = error or failure
        recommended = set_import.recommend_sources(item)
    options = item.get('source_options') or []
    if error and not options and not matches:
        raise error
    choosable = any(not option.get('reference_only') for option in options)
    if item.get('needs_review'):
        # The same rule prepare uses: only a sole compatible version is picked without review.
        compatible = sum(bool(option.get('compatible')) and not option.get('reference_only') for option in options)
        name = 'ready' if compatible == 1 else 'choose' if choosable else 'none'
    else:
        name = 'ready' if recommended or matches else 'choose' if choosable else 'none'
    return state(key, name, matches=matches)


def wants_catalog(item):
    return bool(item.get('artist')) and not item.get('needs_review') and bool(search_identity(item['title'], item['artist'])['artist'])


def enrich(api, sid, entry_id, key, item, stop):
    """Add catalog links and creator free-download URLs after the search state is written."""
    if stop.is_set():
        return
    control = set_scan_jobs.ScanControl(api, stop=stop)
    relations = []
    try:
        with control.activate(), providers.background_requests():
            reference = set_import.reference_for(api, item, search_identity(item['title'], item['artist']))
            if reference and re.fullmatch(r'[0-9a-f-]{36}', reference.get('recording_id') or ''):
                data = providers.musicbrainz('recording/' + reference['recording_id'], {'inc':'url-rels', 'fmt':'json'})
                relations = [(rel.get('type-id'), (rel.get('url') or {}).get('resource'))
                             for rel in data.get('relations', []) if not rel.get('ended')]
    except (ImportCancelled, ValueError, OSError):
        return
    if stop.is_set():
        return
    found = {'links':[]}
    if reference:
        set_import.add_link(found, 'MusicBrainz', reference['source_url'])
    free_links = set_import.catalog_sources(found, relations)
    stored = {name:value for name, value in reference.items() if name != 'id'} if reference else None

    def update(latest):
        current = latest.get('discovery')
        if not isinstance(current, dict) or current.get('key') != key:
            return None
        for link in found['links']:
            set_import.add_link(latest, link['label'], link['url'])
        # ``catalog`` records that the catalog was asked, so "Create playlist" need not ask again.
        return {'links':latest.get('links', []), 'discovery':dict(current, reference=stored, free_links=free_links, catalog=True)}
    with api.connect_existing() as db:
        merge_detail(db, sid, entry_id, key, update)
