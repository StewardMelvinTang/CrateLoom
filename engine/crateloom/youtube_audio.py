"""Resolve audio-only YouTube formats; the existing queue transfers the bytes."""
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import time
import zipfile
from urllib.parse import parse_qs, urlsplit

from . import download_http, service_links
from .metadata_matching import search_identity

NAMES = ('YouTube', 'YouTube Music')
FIELDS = '%(.{id,title,artist,duration,url,ext,protocol,format_id,filesize,filesize_approx,http_headers,is_live,availability})j'
MEDIA_CACHE = {}
MEDIA_LOCK = threading.Lock()
MEDIA_TTL = 120
# Every yt-dlp search shares these slots, so background lookups cannot flood YouTube.
SEARCH_SLOTS = threading.BoundedSemaphore(4)
SEARCH_CACHE = {}
SEARCH_LOCK = threading.Lock()
SEARCH_TTL = 60*60
SEARCH_CACHE_SIZE = 512
# The onefile helper unpacks itself on every launch (about 3 s). The same release's
# unpacked build starts at once; it is extracted once, after its checksum is verified.
YT_DLP_VERSION = '2026.10.26'
ONEDIR_ARCHIVE = 'yt-dlp_macos.zip'
ONEDIR_SHA256 = '20d98fa608998eff87d1d35a6e9fb65e4b874639a6cc89addc19da3becde3ef1'
ONEDIR_EXECUTABLE = 'yt-dlp_macos'
ONEDIR_MAX_BYTES = 512*1024*1024
ONEDIR_LOCK = threading.Lock()
ONEDIR = {'path':None,'failed':None,'thread':None}


def take_cached(api, source):
    key = (str(api.ROOT), source['url'], source['format_id'])
    with MEDIA_LOCK:
        cached = MEDIA_CACHE.pop(key, None)
    if cached and cached[0] > time.monotonic() and abs(cached[1] - source['duration']) <= 1:
        return cached[2], dict(cached[3])


def tools_root():
    return Path.home()/'Library/Caches/CrateLoom/tools'


def onedir_target():
    # Named by the pinned archive too: engines pinned to different builds of one release
    # (a re-signed release archive, a development checkout) never share or replace a folder.
    return tools_root()/f'yt-dlp-{YT_DLP_VERSION}-{ONEDIR_SHA256[:12]}-dir'


def onedir_archives():
    archives = []
    if getattr(sys, 'frozen', False):
        # The app bundle ships the archive as data in Contents/Resources, beside Contents/MacOS.
        binary = Path(sys.executable).resolve()
        archives += [binary.parent.parent/'Resources'/ONEDIR_ARCHIVE, binary.with_name(ONEDIR_ARCHIVE)]
    archives.append(tools_root()/YT_DLP_VERSION/ONEDIR_ARCHIVE)
    return archives


def onedir_ready(folder):
    binary = folder/ONEDIR_EXECUTABLE
    try:
        verified = (folder/'.crateloom-verified').read_text().strip() == ONEDIR_SHA256
    except OSError:
        return None
    # The bootloader cannot start without its Python library, which a cache cleaner may remove.
    return str(binary) if verified and binary.is_file() and os.access(binary, os.X_OK) and (folder/'_internal'/'Python').is_file() else None


def note_onedir(reason):
    """Diagnostics only: the onefile helper is used and nothing is shown to the owner."""
    ONEDIR['failed'] = reason
    try:
        tools_root().mkdir(parents=True, exist_ok=True)
        (tools_root()/'yt-dlp-onedir.log').write_text(f'{time.strftime("%Y-%m-%d %H:%M:%S")} {reason}\n')
    except OSError:
        pass


def file_sha256(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as source:
        for block in iter(lambda: source.read(1024*1024), b''):
            digest.update(block)
    return digest.hexdigest()


def extract_onedir(archive, destination):
    """Unpack into a new folder, keeping execute bits; reject paths that leave it."""
    root = destination.resolve()
    total = 0
    with zipfile.ZipFile(archive) as bundle:
        for info in bundle.infolist():
            name = info.filename
            parts = Path(name).parts
            if not name or name.startswith('/') or '..' in parts or '\\' in name:
                raise ValueError(f'unsafe archive path {name!r}')
            mode = info.external_attr >> 16
            target = root/name
            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            total += info.file_size
            if total > ONEDIR_MAX_BYTES:
                raise ValueError('archive larger than expected')
            target.parent.mkdir(parents=True, exist_ok=True)
            if stat.S_ISLNK(mode):
                link = bundle.read(info).decode()
                if Path(link).is_absolute() or not (target.parent/link).resolve().is_relative_to(root):
                    raise ValueError(f'unsafe archive link {name!r}')
                os.symlink(link, target)
                continue
            with bundle.open(info) as source, target.open('xb') as output:
                shutil.copyfileobj(source, output, 1024*1024)
            target.chmod(0o755 if mode & 0o111 else 0o644)


def leftover_prefix(kind):
    # Per pinned build, like the lock: an engine pinned to another build never removes these.
    return f'.{onedir_target().name}-{kind}-'


def lock_path():
    return tools_root()/f'.{onedir_target().name}.lock'


def leftovers():
    """Extraction folders and replaced builds left by an engine that stopped part-way."""
    prefixes = (leftover_prefix('extract'), leftover_prefix('stale'))
    try:
        return [path for path in tools_root().iterdir() if path.name.startswith(prefixes)]
    except OSError:
        return []


def remove_leftovers():
    # Only called with the extraction lock held: no other extractor is running, so all of them are stale.
    for path in leftovers():
        shutil.rmtree(path, ignore_errors=True)


def install_onedir():
    target = onedir_target()
    ready = onedir_ready(target)
    if ready:
        if leftovers():
            with open(lock_path(), 'w') as lock:
                try:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except OSError:
                    return ready
                remove_leftovers()
        return ready
    archives = [path for path in onedir_archives() if path.is_file()]
    if not archives:
        raise ValueError('no archive')
    tools_root().mkdir(parents=True, exist_ok=True)
    # Another engine may be extracting at the same time; the lock file serializes them.
    with open(lock_path(), 'w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        remove_leftovers()
        ready = onedir_ready(target)
        if ready:
            return ready
        archive = next((path for path in archives if file_sha256(path) == ONEDIR_SHA256), None)
        if archive is None:
            raise ValueError('checksum mismatch for ' + ', '.join(str(path) for path in archives))
        temporary = Path(tempfile.mkdtemp(prefix=leftover_prefix('extract'), dir=tools_root()))
        try:
            extract_onedir(archive, temporary)
            binary = temporary/ONEDIR_EXECUTABLE
            if not binary.is_file() or not os.access(binary, os.X_OK):
                raise ValueError('archive has no executable')
            # Run it once before adopting it: a build macOS refuses to start must never replace the working helper.
            check = subprocess.run([str(binary), '--version'], capture_output=True, text=True, timeout=60)
            if check.returncode or check.stdout.strip() != YT_DLP_VERSION:
                raise ValueError(f'extracted helper reported {check.stdout.strip()[:40]!r} (exit {check.returncode})')
            (temporary/'.crateloom-verified').write_text(ONEDIR_SHA256)
            if target.exists() or target.is_symlink():
                # Only a folder of this same pin is replaced, and only when it no longer passes the check.
                stale = target.with_name(f'{leftover_prefix("stale")}{os.getpid()}')
                target.rename(stale)
                shutil.rmtree(stale, ignore_errors=True)
            temporary.rename(target)
        finally:
            shutil.rmtree(temporary, ignore_errors=True)
    ready = onedir_ready(target)
    if not ready:
        raise ValueError('extracted helper is not executable')
    return ready


def prepare_onedir():
    """Background only: hashing and extracting take seconds, and no caller waits for them."""
    try:
        path = install_onedir()
    except Exception as error:
        note_onedir(f'{type(error).__name__}: {error}'[:500])
        return
    with ONEDIR_LOCK:
        ONEDIR['path'] = path


def onedir_executable():
    """The unpacked build once it is ready; until then None, while it is prepared in the background."""
    if sys.platform != 'darwin':
        return None
    with ONEDIR_LOCK:
        if ONEDIR['path'] and Path(ONEDIR['path']).is_file():
            return ONEDIR['path']
        ready = onedir_ready(onedir_target())
        busy = ONEDIR['thread'] is not None and ONEDIR['thread'].is_alive()
        if not busy and ((ready and leftovers()) or (not ready and ONEDIR['failed'] is None)):
            ONEDIR['thread'] = threading.Thread(target=prepare_onedir, name='yt-dlp-onedir', daemon=True)
            ONEDIR['thread'].start()
        ONEDIR['path'] = ready
        return ready


def executable():
    if os.environ.get('CRATELOOM_YT_DLP'):
        return shutil.which(os.environ['CRATELOOM_YT_DLP'])
    fast = onedir_executable()
    if fast:
        return fast
    if getattr(sys, 'frozen', False):
        bundled = Path(sys.executable).with_name('yt-dlp')
        if bundled.is_file():
            return str(bundled)
    managed = Path.home()/'Library/Caches/CrateLoom/tools/2026.10.26/yt-dlp'
    return str(managed) if managed.is_file() and os.access(managed,os.X_OK) else shutil.which('yt-dlp')


# What the PyInstaller bootloader or dyld print when the unpacked build is damaged, not yt-dlp's own errors.
STARTUP_FAILURE = re.compile(r'\[PYI-\d+:|failed to load python shared library|error loading python lib|library not loaded'
                             r'|no module named|modulenotfounderror', re.I)


def drop_onedir(binary, error):
    """Give up a verified unpacked build that no longer starts, for the onefile helper.

    Returns the helper to retry with once, or None when ``binary`` was not that
    build or the failure was yt-dlp's own.
    """
    if not binary or Path(binary).parent.name != onedir_target().name:
        return None
    if not isinstance(error, OSError) and not STARTUP_FAILURE.search(error_text(error)):
        return None
    with ONEDIR_LOCK:
        if ONEDIR['path'] == binary:
            ONEDIR['path'] = None
    note_onedir(f'unpacked helper failed to start: {diagnostic(error)}')
    try:
        # The next engine start extracts it again; this process keeps the onefile helper.
        (Path(binary).parent/'.crateloom-verified').unlink()
    except OSError:
        pass
    fallback = executable()
    return fallback if fallback and fallback != binary else None


def run_helper(api, binary, args, timeout):
    """Run yt-dlp; a damaged unpacked build falls back once, silently, to the onefile helper."""
    try:
        return api.import_process([binary, *args], timeout, process_group=True)
    except (OSError, subprocess.CalledProcessError) as error:
        fallback = drop_onedir(binary, error)
        if not fallback:
            raise
    return api.import_process([fallback, *args], timeout, process_group=True)


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
    while not SEARCH_SLOTS.acquire(timeout=.2):
        getattr(api, 'check_import_cancel', lambda: None)()
    try:
        result = run_helper(api, binary, [
            '--ignore-config', '--no-plugin-dirs', '--no-remote-components',
            '--proxy', '', '--flat-playlist', '--skip-download', '--no-warnings',
            '--socket-timeout', '8', '--retries', '0', '--extractor-retries', '0',
            '--print', '%(.{' + fields + '})j', '--', query,
        ], 18)
        videos = [json.loads(line) for line in result.stdout.splitlines()]
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        raise ValueError(f'{provider} search is temporarily unavailable. Try again or add a source link.') from error
    finally:
        SEARCH_SLOTS.release()
    return [entry for entry in videos if isinstance(entry, dict) and isinstance(entry.get('title'), str)
            and type(entry.get('duration')) in (int, float) and 30 <= entry['duration'] <= 900]


def search(api, query):
    query = query[:500]
    now = time.monotonic()
    with SEARCH_LOCK:
        cached = SEARCH_CACHE.get(query)
        if cached and cached[0] > now:
            return [dict(video) for video in cached[1]]
    videos = search_entries(api, 'ytsearch15:' + query, 'id,title,channel,duration,channel_is_verified', 'YouTube')
    videos = [video for video in videos if isinstance(video.get('id'), str) and re.fullmatch(r'[\w-]{11}', video['id'], re.ASCII)]
    with SEARCH_LOCK:
        for key in [key for key, entry in SEARCH_CACHE.items() if entry[0] <= now]:
            SEARCH_CACHE.pop(key)
        while len(SEARCH_CACHE) >= SEARCH_CACHE_SIZE:
            SEARCH_CACHE.pop(next(iter(SEARCH_CACHE)))
        SEARCH_CACHE[query] = (now + SEARCH_TTL, [dict(video) for video in videos])
    return videos


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
        result = run_helper(api, binary, [
            '--ignore-config', '--no-plugin-dirs', '--no-remote-components',
            '--proxy', '', '--no-playlist', '--no-warnings', '--socket-timeout', '10',
            '--retries', '0', '--extractor-retries', '0', '--skip-download',
            *runtime_args(), '-f', selection, '--print', FIELDS, '--', watch,
        ], 35)
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
