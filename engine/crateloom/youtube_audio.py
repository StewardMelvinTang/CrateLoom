"""Resolve audio-only YouTube formats; the existing queue transfers the bytes."""
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import threading
import time
from urllib.parse import parse_qs, urlsplit

from . import download_http, service_links
from .metadata_matching import search_identity

NAMES = ('YouTube', 'YouTube Music')
FIELDS = '%(.{id,title,artist,duration,url,ext,protocol,format_id,filesize,filesize_approx,http_headers,is_live,availability})j'
MEDIA_CACHE = {}
MEDIA_LOCK = threading.Lock()
MEDIA_TTL = 120


def take_cached(api, source):
    key = (str(api.ROOT), source['url'], source['format_id'])
    with MEDIA_LOCK:
        cached = MEDIA_CACHE.pop(key, None)
    if cached and cached[0] > time.monotonic() and abs(cached[1] - source['duration']) <= 1:
        return cached[2], dict(cached[3])


def executable():
    if os.environ.get('CRATELOOM_YT_DLP'):
        return shutil.which(os.environ['CRATELOOM_YT_DLP'])
    if getattr(sys, 'frozen', False):
        bundled = Path(sys.executable).with_name('yt-dlp')
        if bundled.is_file():
            return str(bundled)
    managed = Path.home()/'Library/Caches/CrateLoom/tools/2026.10.26/yt-dlp'
    return str(managed) if managed.is_file() and os.access(managed,os.X_OK) else shutil.which('yt-dlp')


BROWSERS = {'safari':'Safari','chrome':'Chrome','firefox':'Firefox','edge':'Edge'}
SESSION_ERRORS = re.compile(
    r'could not find \w+ cookies database|cookies? (?:database|file|store)[^\n]*(?:not permitted|permission denied|locked)'
    r'|(?:not permitted|permission denied)[^\n]*cookies|(?:failed|unable|cannot|could not)(?: to)? (?:decrypt|load cookies|read cookies|copy)'
    r'|cookies are no longer valid|keychain|safe storage|unsupported browser')
UNDECRYPTED = re.compile(r'(?:failed|unable|cannot|could not)(?: to)? decrypt|find-generic-password|safe storage')


def selected_browser():
    from . import set_scan_jobs
    control = set_scan_jobs.current()
    browser = control.youtube_browser if control else None
    return browser if browser in BROWSERS else None


def runtime_args():
    deno = shutil.which('deno')
    args = ['--js-runtimes','deno:'+deno] if deno else []
    browser = selected_browser()
    if browser:
        args += ['--cookies-from-browser',browser]
    return args


def error_text(error):
    parts = []
    while error is not None:
        parts.extend([str(error),str(getattr(error,'stderr','') or '')])
        error = error.__cause__
    return '\n'.join(parts)


def diagnostic(error):
    """One sanitized line for the attempt log: no query strings, no home path."""
    lines = [line.strip() for line in error_text(error).splitlines() if line.strip()]
    line = next((item for item in reversed(lines) if item.lower().startswith('error:')),lines[0] if lines else '')
    line = re.sub(r'(https?://[^\s?#\'"]+)[^\s\'"]*',r'\1',line)
    return line.replace(str(Path.home()),'~')[:300]


def failure_kind(error):
    """Do not mistake a client's warning or bot challenge for a removed video."""
    everything = error_text(error).lower()
    errors = [line for line in everything.splitlines() if line.lstrip().startswith('error:')]
    detail = '\n'.join(errors) if errors else everything
    # A browser session that cannot be read fails before YouTube is contacted. A
    # denied keychain request is only a warning: the extractor then continues
    # signed out, so its final error would otherwise look like an account problem.
    if SESSION_ERRORS.search(detail) or UNDECRYPTED.search(everything):
        return 'session'
    if any(word in detail for word in ('confirm your age','age-restricted','age restricted','inappropriate for some users')):
        return 'age'
    if any(word in detail for word in ('not available in your country','geo-restricted','not made this video available in your country')):
        return 'region'
    if any(word in detail for word in ('private video','video has been removed','video has been deleted','members-only','join this channel')):
        return 'unavailable'
    if any(word in detail for word in ("not a bot",'sign in','sign-in','http error 429','too many requests')):
        return 'challenge'
    if any(word in detail for word in ('http error 403','http 403','expired')):
        return 'expired'
    return 'connection'


def access_message(kinds, browser=None):
    """Say which layer failed and what the owner can do about it."""
    name = BROWSERS.get(browser)
    if 'unavailable' in kinds:
        return 'This video is private, removed, or restricted to channel members. Open it on YouTube to check access.'
    if 'session' in kinds and name:
        hint = {
            'safari':'macOS protects Safari data: allow CrateLoom under System Settings > Privacy & Security > Full Disk Access, reopen CrateLoom, then retry. Chrome or Firefox need no extra permission.',
            'firefox':'Open Firefox, sign in to YouTube, then retry. The most recently used Firefox profile is read.',
        }.get(browser,f'Open {name}, sign in to YouTube, then retry and allow the macOS keychain request. The most recently used {name} profile is read.')
        return f'CrateLoom could not read the YouTube session from {name}. {hint}'
    if 'age' in kinds:
        if name:
            return f'YouTube still requires age verification with the {name} session. Sign in to YouTube in {name} with an age-verified account, check that this video plays there, then retry.'
        return 'YouTube requires age-verified access to this mix. Choose your signed-in browser under YouTube access, then retry.'
    if 'region' in kinds:
        return 'The uploader has blocked this mix in your region. Available published entries are kept.'
    if 'challenge' in kinds:
        if name:
            return f'YouTube did not accept the {name} session. Open this video in {name} while signed in, then retry.'
        return 'YouTube is requesting a signed-in session. Choose your browser under YouTube access, then retry.'
    if name:
        return f'The mix audio could not be downloaded with the {name} session. Your available tracklist is kept; retry the audio scan when the connection recovers.'
    return 'The public audio connections did not complete. Your available tracklist is kept; retry the audio scan when the connection recovers.'


def search_entries(api, query, fields, provider):
    binary = executable()
    if not binary:
        raise ValueError(f'{provider} search needs yt-dlp.')
    try:
        result = api.import_process([
            binary, '--ignore-config', '--no-plugin-dirs', '--no-remote-components',
            '--proxy', '', '--flat-playlist', '--skip-download', '--no-warnings',
            '--socket-timeout', '8', '--retries', '0', '--extractor-retries', '0',
            '--print', '%(.{' + fields + '})j', '--', query,
        ], 18, process_group=True)
        videos = [json.loads(line) for line in result.stdout.splitlines()]
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        raise ValueError(f'{provider} search is temporarily unavailable. Try again or add a source link.') from error
    return [entry for entry in videos if isinstance(entry, dict) and isinstance(entry.get('title'), str)
            and type(entry.get('duration')) in (int, float) and 30 <= entry['duration'] <= 900]


def search(api, query):
    videos = search_entries(api, 'ytsearch15:' + query[:500], 'id,title,channel,duration,channel_is_verified', 'YouTube')
    return [video for video in videos if isinstance(video.get('id'), str) and re.fullmatch(r'[\w-]{11}', video['id'], re.ASCII)]


def resolve(api, reference, format_id=None, *, scan=False):
    if not reference:
        raise ValueError('Choose a single YouTube or YouTube Music link.')
    provider, page, watch = reference
    binary = executable()
    if not binary:
        raise ValueError('Install yt-dlp and Deno, then find the source again.')
    if provider not in NAMES or service_links.identify(download_http.public_url(page)) != reference:
        raise ValueError('Choose a single YouTube or YouTube Music link.')
    if format_id is not None and not re.fullmatch(r'[0-9]{1,5}', format_id):
        raise ValueError('The reviewed YouTube format is invalid. Find the source again.')
    selection = (format_id or 'bestaudio') + '[ext=m4a][protocol=https][vcodec=none]'
    if scan and format_id is None:
        selection = 'bestaudio[ext=webm][abr<=96][protocol=https][vcodec=none]/bestaudio[ext=webm][protocol=https][vcodec=none]/bestaudio[protocol=https][vcodec=none]'
    try:
        result = api.import_process([
            binary, '--ignore-config', '--no-plugin-dirs', '--no-remote-components',
            '--proxy', '', '--no-playlist', '--no-warnings', '--socket-timeout', '10',
            '--retries', '0', '--extractor-retries', '0', '--skip-download',
            *runtime_args(), '-f', selection, '--print', FIELDS, '--', watch,
        ], 35, process_group=True)
        data = json.loads(result.stdout)
    except subprocess.TimeoutExpired as error:
        raise ValueError(f'{provider} lookup timed out. Try again.') from error
    except subprocess.CalledProcessError as error:
        detail = (error.stderr or '').lower()
        if any(s in detail for s in ('sign in', 'sign-in', 'private video', 'members-only', 'not available in your country')):
            message = f'{provider} requires access to this video. Use an audio file you can access.'
        else:
            message = f'{provider} could not provide audio for this link. Try again or update yt-dlp.'
        raise ValueError(message) from error
    except (OSError, ValueError) as error:
        raise ValueError(f'{provider} audio lookup failed. Check yt-dlp and retry.') from error
    if not isinstance(data, dict):
        raise ValueError(f'{provider} returned an invalid audio description.')
    media = download_http.public_url(data.get('url'))
    host = urlsplit(media).hostname
    duration = data.get('duration')
    selected = data.get('format_id')
    vid = parse_qs(urlsplit(watch).query)['v'][0]
    if (data.get('id') != vid or data.get('ext') not in (('m4a','webm','opus','ogg') if scan else ('m4a',)) or data.get('protocol') != 'https'
            or not isinstance(selected, str) or not re.fullmatch(r'[0-9]{1,5}', selected)
            or (format_id and selected != format_id) or not host.endswith('.googlevideo.com')
            or data.get('is_live') or data.get('availability') not in ('public', 'unlisted', None)
            or type(duration) not in (int, float) or not 0 < duration < 86400):
        raise ValueError(f'{provider} did not return a supported audio-only format for this video.')
    size = data.get('filesize')
    if type(size) is int and size > download_http.MAX_BYTES:
        raise ValueError('This download exceeds the 512 MB limit')
    size = size if type(size) is int and size > 0 else None
    offered_headers = data.get('http_headers')
    agent = offered_headers.get('User-Agent') if isinstance(offered_headers, dict) else None
    headers = {'User-Agent': agent} if isinstance(agent, str) and len(agent) <= 512 and agent.isascii() and agent.isprintable() else {}
    title = service_links.clean(data.get('title')) or vid
    identity = search_identity(title, service_links.clean(data.get('artist')))
    # The signed CDN address is intentionally separate from the serializable review.
    source = {'kind': 'direct', 'adapter': 'youtube', 'provider': provider, 'can_acquire': True,
            'url': page, 'display_url': page, 'filename': title + '.' + data['ext'], 'format_id': selected,
            'duration': duration, 'declared_bytes': size, 'version': identity['version'],
            'values': {'title': identity['title'] or title, 'artist': identity['artist']}}
    if format_id is None:
        with MEDIA_LOCK:
            stamp = time.monotonic()
            for key, cached in list(MEDIA_CACHE.items()):
                if cached[0] <= stamp:
                    MEDIA_CACHE.pop(key)
            while len(MEDIA_CACHE) >= 128:
                MEDIA_CACHE.pop(next(iter(MEDIA_CACHE)))
            MEDIA_CACHE[str(api.ROOT), page, selected] = (stamp + MEDIA_TTL, duration, media, dict(headers))
    return source, media, headers


def refresh(api, source):
    reference = service_links.identify(download_http.public_url(source['url']))
    current, media, headers = resolve(api, reference, source['format_id'])
    if abs(current['duration'] - source['duration']) > 1:
        raise ValueError('The video duration changed after review. Find the source again.')
    return media, headers
