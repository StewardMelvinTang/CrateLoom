"""Optional access to creator-enabled SoundCloud originals."""
import json
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from . import download_http
from .service_links import identify
from .youtube_audio import search_entries

ORIGIN = 'https://api.soundcloud.com'
TRACK_PATH = r'/tracks/(?:\d+|soundcloud:tracks:\d+)'
SESSION_TOKEN = None
MEDIA_CACHE = {}
MEDIA_LOCK = threading.Lock()


def configure(token):
    global SESSION_TOKEN
    if not isinstance(token, str) or (token and not re.fullmatch(r'[!-~]{1,4096}', token)):
        raise ValueError('Enter a valid SoundCloud access token.')
    with MEDIA_LOCK:
        SESSION_TOKEN = token or None
        MEDIA_CACHE.clear()


def configured():
    return bool(SESSION_TOKEN or os.environ.get('CRATELOOM_SOUNDCLOUD_TOKEN', '').strip())


def search(api, query):
    rows = search_entries(api, 'scsearch6:' + query[:500], 'title,uploader,duration,webpage_url', 'SoundCloud')
    for row in rows:
        try:
            reference = identify(download_http.public_url(row.get('webpage_url')))
        except ValueError:
            continue
        if reference and reference[0] == 'SoundCloud':
            yield dict(row, url=reference[1])


def request(path, *, allow_redirect=False):
    token = SESSION_TOKEN or os.environ.get('CRATELOOM_SOUNDCLOUD_TOKEN', '').strip()
    if not token:
        raise ValueError('Open SoundCloud to use the creator’s Download file button, then add the local file.')
    req = urllib.request.Request(ORIGIN + path, headers={'Authorization': 'OAuth ' + token, 'Accept': 'application/json'})
    try:
        with urllib.request.build_opener(download_http.NoRedirect()).open(req, timeout=12) as response:
            return json.loads(response.read(1024 * 1024))
    except urllib.error.HTTPError as error:
        with error:
            if allow_redirect and error.code in (301, 302, 303, 307, 308) and error.headers.get('Location'):
                return {'location': urllib.parse.urljoin(req.full_url, error.headers['Location'])}
        raise ValueError(f'SoundCloud returned HTTP {error.code}. Check access or open the track in SoundCloud.') from None
    except (OSError, ValueError):
        raise ValueError('SoundCloud did not return a usable download.') from None


def take_cached(page):
    with MEDIA_LOCK:
        cached = MEDIA_CACHE.pop(page, None)
    if cached and cached[0] > time.monotonic():
        return cached[1]


def resolve(page, *, cache=False):
    with MEDIA_LOCK:
        session = SESSION_TOKEN
    path = '/resolve?' + urllib.parse.urlencode({'url': page})
    track = request(path, allow_redirect=True)
    if isinstance(track, dict) and track.get('location'):
        parsed = urllib.parse.urlsplit(track['location'])
        if parsed.scheme != 'https' or parsed.netloc != 'api.soundcloud.com' or not re.fullmatch(TRACK_PATH, parsed.path):
            raise ValueError('Choose a single SoundCloud track.')
        track = request(parsed.path)
    if not isinstance(track, dict) or track.get('downloadable') is not True or track.get('sharing', 'public') != 'public':
        raise ValueError('The creator has not enabled this SoundCloud download.')
    parsed = urllib.parse.urlsplit(track.get('download_url') or '')
    if parsed.scheme != 'https' or parsed.netloc != 'api.soundcloud.com' or not re.fullmatch(TRACK_PATH + '/download', parsed.path):
        raise ValueError('SoundCloud did not provide an original file link.')
    target = request(parsed.path, allow_redirect=True)
    if not isinstance(target, dict):
        raise ValueError('SoundCloud did not provide an original file link.')
    media = download_http.public_url(target.get('location') or target.get('redirectUri') or target.get('redirect_uri'))
    info = download_http.inspect(media)
    if cache:
        with MEDIA_LOCK:
            if session != SESSION_TOKEN:
                return info, media
            while len(MEDIA_CACHE) >= 64:
                MEDIA_CACHE.pop(next(iter(MEDIA_CACHE)))
            MEDIA_CACHE[page] = (time.monotonic() + 60, media)
    return info, media


def find(artist, title, identity_key, duration):
    query = urllib.parse.urlencode({'q': f'{artist} {title}', 'limit': 15})
    rows = request('/tracks?' + query)
    if isinstance(rows, dict):
        rows = rows.get('collection', [])
    expected = identity_key(artist, title)
    matches = {}
    for track in rows if isinstance(rows, list) else []:
        if not isinstance(track, dict) or track.get('downloadable') is not True:
            continue
        name = track.get('title') or ''
        publisher = track.get('publisher_metadata') or {}
        credit = publisher.get('artist') or track.get('metadata_artist') or ''
        if identity_key(credit, name) != expected:
            continue
        length = track.get('duration')
        if duration and (type(length) not in (int, float) or abs(length / 1000 - duration) > max(5, duration * .035)):
            continue
        try:
            reference = identify(download_http.public_url(track.get('permalink_url')))
        except ValueError:
            continue
        if reference and reference[0] == 'SoundCloud':
            matches[reference[1]] = None
    return next(iter(matches)) if len(matches) == 1 else None
