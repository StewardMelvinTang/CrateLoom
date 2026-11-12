"""Cache album artwork independently of source resolution and audio downloads."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import re
import threading
import urllib.parse

from . import download_http, preparation
from .metadata_matching import normalized, search_identity

POOL = ThreadPoolExecutor(max_workers=3, thread_name_prefix='set-artwork')
LOCK = threading.Lock()
PENDING = set()


def artwork_url(value):
    if not isinstance(value, str):
        return None
    if re.fullmatch(r'spotify:image:[0-9a-fA-F]{40}', value):
        value = 'https://i.scdn.co/image/' + value.rsplit(':', 1)[-1]
    try:
        url = download_http.public_url(value)
        host = urllib.parse.urlsplit(url).hostname or ''
        if host in ('i.scdn.co', 'image-cdn-ak.spotifycdn.com') or host.endswith(('.mzstatic.com', '.ytimg.com', '.sndcdn.com')):
            return url
    except ValueError:
        pass
    return None


def cache(api, url):
    url = artwork_url(url)
    if not url:
        return None
    target = api.ROOT / 'artwork' / (hashlib.sha256(url.encode()).hexdigest() + '.image')
    if not target.is_file():
        with download_http.open_url(url) as response:
            data = response.read(5 * 1024 * 1024 + 1)
        if len(data) > 5 * 1024 * 1024:
            raise ValueError('Artwork is too large')
        api.atomic(target, data)
    return preparation.validate_image(api, target)


def find(api, item):
    if item.get('artwork_url'):
        path = cache(api, item['artwork_url'])
        if path:
            return path
    identity = search_identity(item['title'], item['artist'])
    terms = urllib.parse.urlencode({'term': identity['artist'] + ' ' + identity['title'], 'entity': 'song', 'limit': 8})
    payload = json.loads(preparation.request('https://itunes.apple.com/search?' + terms))
    for row in payload.get('results', []):
        artist, title = normalized(row.get('artistName', '')), normalized(row.get('trackName', ''))
        expected_artist, expected_title = normalized(identity['artist']), normalized(identity['title'])
        artist_matches = artist == expected_artist or set(artist.split()).issubset(expected_artist.split())
        if not expected_artist or not expected_title or not artist_matches or title != expected_title:
            continue
        duration = item.get('duration')
        if duration and abs(row.get('trackTimeMillis', 0) / 1000 - duration) > max(15, duration * .08):
            continue
        return cache(api, row.get('artworkUrl100', '').replace('100x100bb', '600x600bb'))
    return None


def apply(api, db, track_id, path):
    track = api.track(db, track_id)
    if not (track.get('metadata') or {}).get('artwork_path'):
        preparation.dispatch(api, db, 'metadata.save', {'id': track_id, 'values': {}, 'artwork_path': path})


def enrich(api, sid):
    with api.connect() as db:
        rows = db.execute('SELECT rowid AS entry_id,detail FROM dj_set_items WHERE set_id=?', (sid,)).fetchall()
    for row in rows:
        item = json.loads(row['detail'])
        if item.get('unknown') or (item.get('artwork_path') and Path(item['artwork_path']).is_file()):
            continue
        try:
            path = find(api, item)
        except (ValueError, OSError, KeyError):
            continue
        if not path:
            continue
        with api.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            current = db.execute('SELECT detail,track_id FROM dj_set_items WHERE set_id=? AND rowid=?', (sid, row['entry_id'])).fetchone()
            if not current:
                continue
            latest = json.loads(current['detail'])
            latest['artwork_path'] = path
            db.execute('UPDATE dj_set_items SET detail=? WHERE set_id=? AND rowid=?', (json.dumps(latest), sid, row['entry_id']))
            if current['track_id']:
                apply(api, db, current['track_id'], path)
    from .set_import import report
    with api.connect() as db:
        return report(db, sid)


def queue(api, sid):
    key = (str(api.ROOT), sid)
    with LOCK:
        if key in PENDING:
            return
        PENDING.add(key)
    def work():
        try:
            enrich(api, sid)
        finally:
            with LOCK:
                PENDING.discard(key)
    POOL.submit(work)
