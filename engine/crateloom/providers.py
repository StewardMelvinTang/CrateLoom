"""Recording references and explicit direct-audio source resolution."""
import json
from pathlib import Path
import re
import threading
import time
import urllib.parse
import urllib.error
import uuid

from . import download_http, playlist_sources, preparation, service_links, youtube_audio
from .metadata_matching import normalized, search_identity, split_version, query_plan, rank_candidates

CANDIDATES = {}
LOCK = threading.Lock()
CANDIDATE_TTL = 3600
MAX_CANDIDATES = 250
# A set prepare registers a source and its references for every entry before it
# queues them, so recent entries are only dropped at a limit sized for a full set.
CANDIDATE_HOLD = 900
CANDIDATE_LIMIT = MAX_CANDIDATES + 20 * playlist_sources.MAX_ENTRIES
# MusicBrainz allows one request per second; the lock paces every caller in turn.
MUSICBRAINZ_LOCK = threading.Lock()
MUSICBRAINZ_INTERVAL = 1.1
LAST_MUSICBRAINZ_REQUEST = 0


def capabilities(api):
    youtube_ready = bool(youtube_audio.executable())
    return {'download_directory': str(api.ROOT/'downloads'), 'max_bytes': download_http.MAX_BYTES, 'providers': [
        {'id': 'direct', 'name': 'Direct audio URL', 'state': 'available', 'url_resolution': True, 'search': False, 'metadata': False, 'acquisition': True, 'authentication': 'None; public HTTPS only', 'formats': ['MP3','FLAC','WAV','AIFF','M4A','OGG']},
        {'id': 'musicbrainz', 'name': 'MusicBrainz', 'state': 'reference', 'url_resolution': False, 'search': True, 'metadata': True, 'acquisition': False, 'authentication': 'None', 'formats': []},
        *[{'id': key, 'name': name, 'state': 'available' if youtube_ready and name in youtube_audio.NAMES else 'reference',
           'url_resolution': True, 'search': False, 'metadata': True,
           'acquisition': youtube_ready and name in youtube_audio.NAMES,
           'authentication': 'Public audio via yt-dlp' if youtube_ready and name in youtube_audio.NAMES else 'None for public references',
           'formats': ['M4A'] if youtube_ready and name in youtube_audio.NAMES else []}
          for key,name in [('youtube','YouTube'),('youtube_music','YouTube Music'),('soundcloud','SoundCloud'),('spotify','Spotify')]],
    ]}


def register(api, candidate):
    with LOCK:
        now = time.monotonic()
        for key in list(CANDIDATES):
            if now-CANDIDATES[key]['at'] > CANDIDATE_TTL:
                CANDIDATES.pop(key)
        while len(CANDIDATES) >= MAX_CANDIDATES:
            oldest = next(iter(CANDIDATES))
            if len(CANDIDATES) < CANDIDATE_LIMIT and now-CANDIDATES[oldest]['at'] < CANDIDATE_HOLD:
                break
            CANDIDATES.pop(oldest)
        cid = str(uuid.uuid4())
        candidate = dict(candidate, id=cid)
        CANDIDATES[cid] = {'root': str(api.ROOT), 'at': now, 'value': candidate}
        return candidate


def get(api, cid, kind):
    with LOCK:
        item = CANDIDATES.get(cid)
        if not item or item['root'] != str(api.ROOT) or time.monotonic()-item['at'] > CANDIDATE_TTL or item['value']['kind'] != kind:
            raise ValueError('Source review expired. Find the source again.')
        return dict(item['value'])


def musicbrainz(path, params):
    global LAST_MUSICBRAINZ_REQUEST
    with MUSICBRAINZ_LOCK:
        url = 'https://musicbrainz.org/ws/2/' + path + '?' + urllib.parse.urlencode(params)
        for attempt in range(2):
            delay = MUSICBRAINZ_INTERVAL-(time.monotonic()-LAST_MUSICBRAINZ_REQUEST)
            if delay > 0:
                time.sleep(delay)
            LAST_MUSICBRAINZ_REQUEST = time.monotonic()
            try:
                return json.loads(preparation.request(url))
            except urllib.error.HTTPError as error:
                retry_after = error.headers.get('Retry-After', '') if error.headers else ''
                if attempt or error.code not in (429, 502, 503, 504) or (retry_after.isdecimal() and int(retry_after) > 5):
                    break
                time.sleep(max(2, int(retry_after)) if retry_after.isdecimal() else 2)
            except (ValueError, OSError):
                break
        raise ValueError('MusicBrainz is unavailable. Retry later or paste a direct audio URL.')


def credit_split(identity):
    """Find the artist in a query typed without a separator ("daft punk one more time").

    One loose catalog request looks for recordings whose artist and title
    together use exactly the typed words; the typed text is then split there.
    """
    words = normalized(identity['title']).split()
    if identity['artist'] or not 3 <= len(words) <= 12:
        return identity
    terms = ' '.join(words)
    try:
        payload = musicbrainz('recording/', {'query': f'recording:({terms}) AND artist:({terms})', 'fmt': 'json', 'limit': 25})
    except ValueError:
        return identity
    for item in payload.get('recordings', []):
        credits = item.get('artist-credit') or []
        artist = normalized(credits[0].get('name', '')) if credits and isinstance(credits[0], dict) else ''
        title = normalized(split_version(item.get('title', ''))[0])
        if not artist or not title:
            continue
        for first, second in ((artist, title), (title, artist)):
            if terms == f'{first} {second}' or (first == artist and terms.startswith(artist + ' ' + title + ' ')):
                # Keep the owner's own spelling for the title and any version words.
                count = len(artist.split())
                raw = identity['title'].split()
                if len(raw) != len(words):
                    return search_identity(item['title'], credits[0]['name'])
                return search_identity(' '.join(raw[count:] if first == artist else raw[:len(raw) - count]),
                                       ' '.join(raw[:count] if first == artist else raw[len(raw) - count:]))
    return identity


def recording_search(api, text, duration=None, isrc=None):
    identity = search_identity(text) if isinstance(text, str) else text
    if not identity['title']:
        raise ValueError('Enter a song title or direct audio URL')
    if isinstance(text, str) and not isrc:
        identity = credit_split(identity)
    collected = {}
    queries = ['isrc:'+isrc] if isrc else query_plan(identity, duration)
    for terms in queries:
        payload = musicbrainz('recording/', {'query': terms, 'fmt': 'json', 'limit': 30})
        for item in payload.get('recordings', []):
            if item.get('id'):
                collected.setdefault(item['id'], item)
        ranked = [c for c in rank_candidates({'recordings': list(collected.values())}, duration, identity) if c['match_status'] != 'rejected']
        if ranked:
            break
    references = [register(api, dict(candidate, kind='reference', provider='MusicBrainz', can_acquire=False)) for candidate in ranked[:15]]
    return {'kind': 'references', 'query': identity, 'candidates': references, 'message': None if references else 'No reliable recording reference found. Try artist — title, or paste a direct audio URL.'}


def resolve(api, text):
    if not isinstance(text, str) or not text.strip() or len(text) > 4096:
        raise ValueError('Enter a song name or HTTPS URL')
    text = text.strip()
    if not re.match(r'^[a-zA-Z][a-zA-Z0-9+.-]*:', text):
        if len(text) > 300:
            raise ValueError('Keep song searches within 300 characters')
        return recording_search(api, text)
    url = download_http.public_url(text)
    reference = service_links.identify(url)
    if reference:
        reason = None
        if reference[0] in youtube_audio.NAMES:
            try:
                source, _, _ = youtube_audio.resolve(api, reference)
                return {'kind':'direct','candidates':[register(api,source)],'message':None}
            except ValueError as error:
                reason = str(error)
        candidate = service_links.resolve(reference)
        if reason:
            candidate['guidance'] = reason + ' You can also use a direct audio file.'
        return {'kind':'service_reference','candidates':[register(api,candidate)],'message':None}
    info = download_http.inspect(url)
    identity = search_identity(info['filename'])
    candidate = register(api, dict(info, kind='direct', provider='Direct HTTPS', can_acquire=True,
                                  display_url=download_http.display_url(info['resolved_url']),
                                  values={'title': identity['title'], 'artist': identity['artist']}, version=identity['version']))
    return {'kind': 'direct', 'candidates': [candidate], 'message': None}


def cover(api, reference_id):
    reference = get(api, reference_id, 'reference')
    release = reference.get('release_id')
    if not isinstance(release, str) or not re.fullmatch(r'[0-9a-fA-F-]{36}', release):
        raise ValueError('This reference has no release cover')
    target = api.ROOT/'artwork'/('release-'+release+'-1200.image')
    try:
        if not target.exists():
            api.atomic(target, preparation.request('https://coverartarchive.org/release/'+release+'/front-1200', 5*1024*1024))
        path = preparation.validate_image(api, target)
    except Exception as error:
        raise ValueError('No usable cover found for this release. You can add artwork after import.') from error
    return {'path': path, 'reference_id': reference_id}


def dispatch(api, method, params):
    if method == 'providers.capabilities':
        return capabilities(api)
    if method == 'providers.resolve':
        text = params.get('query')
        return preparation.launch(lambda: resolve(api, text))
    if method == 'providers.cover':
        return preparation.launch(lambda: cover(api, params.get('reference_id')))
    if method == 'providers.job':
        return preparation.dispatch(api, None, 'metadata.job', params)
    raise ValueError('Unknown provider method')
