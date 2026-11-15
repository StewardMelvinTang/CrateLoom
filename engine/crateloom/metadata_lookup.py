"""Independent catalog searches with incremental results and bounded caches."""
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import json
import threading
import time
import urllib.parse

from . import providers
from .metadata_matching import primary_artist, query_plan, rank_candidates

POOL = ThreadPoolExecutor(max_workers=4, thread_name_prefix='metadata-catalog')
GATES = {'MusicBrainz': providers.MUSICBRAINZ_LOCK, 'Apple Music': threading.Lock()}
LAST = {'MusicBrainz': 0.0, 'Apple Music': 0.0}
INTERVALS = {'MusicBrainz': 1.1, 'Apple Music': 3.1}


def cached_request(api, request, provider, url, refresh=False):
    key = hashlib.sha256(('catalog-v3:' + url).encode()).hexdigest()
    path = api.ROOT / 'metadata-cache' / (key + '.json')
    stale = None
    try:
        saved = json.loads(path.read_text())
        field = 'recordings' if provider == 'MusicBrainz' else 'results'
        if isinstance(saved.get('payload', {}).get(field), list):
            stale = saved['payload']
            ttl = 7 * 86400 if stale[field] else 300
            if not refresh and time.time() - saved['saved_at'] < ttl:
                return stale, True, None
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        pass
    try:
        if provider == 'MusicBrainz':
            # One pacer for the whole engine: set source lookups share the same rate limit.
            with providers.musicbrainz_turn():
                providers.pace_musicbrainz(INTERVALS[provider])
                payload = json.loads(request(url, timeout=6))
        else:
            with GATES[provider]:
                delay = INTERVALS[provider] - (time.monotonic() - LAST[provider])
                if delay > 0:
                    time.sleep(delay)
                LAST[provider] = time.monotonic()
                payload = json.loads(request(url, timeout=6))
        field = 'recordings' if provider == 'MusicBrainz' else 'results'
        if not isinstance(payload, dict) or not isinstance(payload.get(field), list):
            raise ValueError('Invalid catalog response')
        api.atomic(path, json.dumps({'saved_at': time.time(), 'payload': payload}).encode())
        return payload, False, None
    except Exception:
        if stale is not None:
            return stale, True, provider + ' is unavailable; showing saved catalog results.'
        raise ValueError(provider + ' could not be reached. Refresh to try again.')


def apple_candidates(payload, duration, identity):
    candidates = []
    for row in payload.get('results', []):
        if row.get('kind') != 'song' or not isinstance(row.get('trackId'), int):
            continue
        album = row.get('collectionName', '')
        recording = {'id': str(row['trackId']), 'title': row.get('trackName', ''),
                     'artist-credit': [{'name': row.get('artistName', '')}],
                     'length': row.get('trackTimeMillis'),
                     'releases': [{'id': str(row['collectionId']) if row.get('collectionId') else None,
                                   'title': album, 'date': row.get('releaseDate', '')[:10], 'status': 'Official'}]}
        # Karaoke/tribute catalogs often retain the original artist in their title.
        if any(word in album.casefold() for word in ('karaoke', 'tribute', 'made famous', 'originally performed')):
            recording['disambiguation'] = 'Instrumental'
        for candidate in rank_candidates({'recordings': [recording]}, duration, identity):
            candidate.update(provider='Apple Music', source_url=row.get('trackViewUrl', ''),
                             artwork_available=bool(row.get('artworkUrl100')), artwork_url=row.get('artworkUrl100'))
            candidate['values']['genre'] = row.get('primaryGenreName', '')
            candidates.append(candidate)
    return candidates


def search(api, request, identity, duration, refresh=False, progress=lambda result: None):
    def musicbrainz():
        recordings = {}
        cached = True
        warnings = []
        for terms in query_plan(identity, duration):
            url = 'https://musicbrainz.org/ws/2/recording/?' + urllib.parse.urlencode({'query': terms, 'fmt': 'json', 'limit': 50})
            try:
                payload, hit, warning = cached_request(api, request, 'MusicBrainz', url, refresh)
            except ValueError as error:
                if not recordings:
                    raise
                warnings.append(str(error))
                break
            cached &= hit
            if warning:
                warnings.append(warning)
            for recording in payload['recordings']:
                if recording.get('id'):
                    recordings.setdefault(recording['id'], recording)
            candidates = rank_candidates({'recordings': list(recordings.values())}, duration, identity)
            if any(c['match_status'] != 'rejected' for c in candidates):
                break
        return rank_candidates({'recordings': list(recordings.values())}, duration, identity), cached, warnings

    def apple():
        terms = ' '.join(filter(None, [primary_artist(identity['artist']), identity['title'], identity['version']]))
        url = 'https://itunes.apple.com/search?' + urllib.parse.urlencode({'term': terms, 'entity': 'song', 'limit': 50, 'country': 'US'})
        payload, cached, warning = cached_request(api, request, 'Apple Music', url, refresh)
        return apple_candidates(payload, duration, identity), cached, [warning] if warning else []

    result = {'candidates': [], 'query': identity, 'cached': True, 'partial': False, 'warnings': [], 'providers': []}
    futures = {POOL.submit(fn): name for name, fn in [('MusicBrainz', musicbrainz), ('Apple Music', apple)]}
    for future in as_completed(futures):
        provider = futures[future]
        try:
            candidates, cached, warnings = future.result()
            result['candidates'].extend(candidates)
            result['cached'] &= cached
            result['warnings'].extend(warnings)
            result['providers'].append({'name': provider, 'state': 'cached' if cached else 'completed'})
        except Exception as error:
            result['cached'] = False
            result['warnings'].append(str(error))
            result['providers'].append({'name': provider, 'state': 'unavailable'})
        result['partial'] = bool(result['warnings'])
        result['candidates'].sort(key=lambda c: (c['match_status'] == 'rejected', -c['rank']))
        progress(json.loads(json.dumps(result)))
    if all(provider['state'] == 'unavailable' for provider in result['providers']):
        raise ValueError('Metadata catalogs could not be reached. Check your connection and refresh. Saved fields are still available.')
    return result
