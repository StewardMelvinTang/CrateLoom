"""Change the audio behind a library track: another version found online, a pasted link, or a local file.

The track keeps its id, metadata, cover, cues, manual sections and playlist places. Its analysis is
cleared so tempo, key and the beat grid are measured again on the new audio, and the library's details
are marked as not yet written to the new file.
"""
import json
import os
from pathlib import Path
import re
import subprocess
import threading
import uuid

from . import download_http, preparation, providers, service_links, set_import, youtube_audio

METHODS = ('library.source_options', 'library.change_source', 'library.replace_file', 'library.job', 'library.preview_source')
PREVIEW_CACHE_BYTES = 1024 ** 3
PREVIEW_LOCKS = {}
PREVIEW_LOCK = threading.Lock()


def replace_audio(api, db, tid, source):
    """Point track ``tid`` at the audio file ``source``. Returns (track, previous path)."""
    t = api.track(db, tid)
    path = Path(source).expanduser().resolve(strict=True)
    if not path.is_file():
        raise ValueError('Choose an audio file')
    if str(path) == t['path']:
        raise ValueError('That file is already this track’s audio.')
    if db.execute('SELECT 1 FROM tracks WHERE path=? AND id!=?', (str(path), tid)).fetchone():
        raise ValueError('That file already belongs to another library track.')
    try:
        duration, stream, _ = api.probe(path)
        digest, signature = api.file_hash(path), api.audio_hash(path)
    except subprocess.CalledProcessError as error:
        raise ValueError('Cannot read audio. The file may be damaged or unsupported.') from error
    except subprocess.TimeoutExpired as error:
        raise ValueError('Audio inspection timed out. Try again.') from error
    other = db.execute('SELECT title FROM tracks WHERE audio_hash=? AND id!=? AND abs(duration-?)<0.001 LIMIT 1',
                       (signature, tid, duration)).fetchone()
    if other:
        raise ValueError(f'That audio is already in your library as “{other["title"]}”.')
    # The library's details stay; the new file does not carry them yet, so they become pending writes.
    state = preparation.values(api, t)
    state['pending'] = {key: value for key, value in state['values'].items() if isinstance(value, str) and value.strip()}
    state['artwork_pending'] = bool(state.get('artwork_path'))
    state.pop('artwork_supported', None)
    db.execute('''UPDATE tracks SET path=?,duration=?,codec=?,sample_rate=?,file_hash=?,audio_hash=?,
                  analysis=NULL,detail_analysis=NULL,structure_analysis=NULL,metadata=? WHERE id=?''',
               (str(path), duration, stream.get('codec_name', 'unknown'), stream.get('sample_rate'), digest, signature,
                json.dumps(state), tid))
    # Detected sections belong to the old audio; the ones you placed yourself are kept, like cues.
    db.execute("DELETE FROM sections WHERE track_id=? AND origin!='manual'", (tid,))
    return api.track(db, tid), t['path']


def discard_previous(api, db, previous):
    """Delete a replaced file only when CrateLoom downloaded it and no track uses it. Your own files stay."""
    try:
        path = Path(previous).resolve()
        downloads = (Path(api.ROOT) / 'downloads').resolve()
        if path.parent != downloads or db.execute('SELECT 1 FROM tracks WHERE path=?', (str(path),)).fetchone():
            return False
        path.unlink(missing_ok=True)
        return True
    except OSError:
        return False


def current_source(db, tid):
    row = db.execute("SELECT quality FROM acquisitions WHERE track_id=? AND state='completed' ORDER BY updated_at DESC LIMIT 1", (tid,)).fetchone()
    try:
        url = json.loads(row['quality']).get('source_url') if row and row['quality'] else None
    except ValueError:
        url = None
    return url if isinstance(url, str) and url.startswith('https://') else None


def video_id(url):
    match = re.search(r'[?&]v=([\w-]{11})', url or '')
    return match[1] if match else None


def options(api, t, query, current):
    """Other versions on YouTube, the likeliest original audio first."""
    artist, title = t['artist'].strip(), t['title'].strip()
    item = {'artist': artist, 'title': title, 'evidence': [], 'credit_confirmed': True, 'duration': None}
    text = (query or '').strip()[:300] or ' '.join(filter(None, (artist, title)))
    if not text:
        raise ValueError('Enter what to search for.')
    videos = youtube_audio.search(api, text)
    if not query and artist:
        # A second search aimed at audio-only uploads, which the plain search often ranks low.
        try:
            seen = {video['id'] for video in videos}
            videos += [video for video in youtube_audio.search(api, text + ' audio') if video['id'] not in seen]
        except ValueError:
            pass
    found = set_import.video_match(item, videos)[1] if artist and not query else []
    matched = {option['url'] for option in found}
    rows = []
    for option in found:
        priority = set_import.source_priority(item, option)
        rows.append(dict(option, matched=True, compatible=priority is not None, priority=priority[0] if priority else None))
    for video in videos:
        url = 'https://www.youtube.com/watch?v=' + video['id']
        if url not in matched:
            channel = re.sub(r'\s+-\s+Topic$', '', video.get('channel') or '', flags=re.I)
            rows.append({'title': video['title'], 'url': url, 'duration': video['duration'], 'channel': channel,
                         'verified': video.get('channel_is_verified') is True, 'matched': False, 'compatible': False, 'priority': None,
                         'topic': bool(re.search(r'\s+-\s+Topic$', video.get('channel') or '', re.I))})
    playing = video_id(current)
    for row in rows:
        row['current'] = bool(playing) and video_id(row['url']) == playing
        row['music_video'] = set_import.music_video(row['title'], title)
        row['difference'] = round(row['duration'] - t['duration'], 1) if isinstance(row.get('duration'), (int, float)) else None
        row.pop('title_only', None)
    rows.sort(key=lambda row: (not row['compatible'], -(row['priority'] or 0), not row['matched']))
    best = next((row for row in rows if row['compatible'] and not row['current']), None)
    if best:
        best['recommended'] = True
        best['recommendation_reason'] = ('Audio-only upload' if best.get('topic') else 'Official audio' if set_import.AUDIO_LABEL.search(set_import.normalized(best['title']))
                                         else 'Closest match')
    for row in rows:
        row.pop('priority', None)
    return {'query': text, 'current': current, 'options': rows[:30]}


def prune_previews(folder, keep):
    """Keep the preview cache under its cap, dropping the least recently played first."""
    entries = []
    for path in folder.glob('*.m4a'):
        try:
            stat = path.stat()
        except OSError:
            continue
        entries.append((stat.st_mtime, stat.st_size, path))
    total = sum(size for _, size, _ in entries)
    for _, size, path in sorted(entries):
        if total <= PREVIEW_CACHE_BYTES:
            break
        if path != keep:
            path.unlink(missing_ok=True)
            total -= size


def preview(api, t, url, convert=False):
    """A local file to listen to before choosing: the track's own audio, or a YouTube version fetched once."""
    if not url:
        if t['missing']:
            raise ValueError('This track’s audio file is missing.')
        if convert or t.get('codec') in ('vorbis', 'opus'):
            from .playback import prepare
            return {'preview_path': prepare(api, t)['path'], 'duration': t['duration']}
        return {'preview_path': t['path'], 'duration': t['duration']}
    vid = video_id(url)
    platform = service_links.identify(download_http.public_url(url)) if vid else None
    if not vid or not platform or platform[0] not in youtube_audio.NAMES:
        raise ValueError('Only YouTube versions can be previewed here.')
    folder = Path(api.ROOT) / 'source-previews'
    target = folder / f'{vid}.m4a'
    with PREVIEW_LOCK:
        lock = PREVIEW_LOCKS.setdefault(str(target), threading.Lock())
    with lock:
        if target.is_file():
            try:
                os.utime(target)
            except OSError:
                pass
        else:
            from .acquisitions import AudioTransfer
            source, media, headers = youtube_audio.resolve(api, platform)
            folder.mkdir(parents=True, exist_ok=True)
            partial = folder / f'.{vid}-{uuid.uuid4().hex[:8]}.partial'
            try:
                AudioTransfer(api).transfer('preview', source, media, headers, partial, lambda: None, ranges=True)
                os.replace(partial, target)
            finally:
                partial.unlink(missing_ok=True)
    try:
        prune_previews(folder, target)
    except OSError:
        pass
    return {'preview_path': str(target), 'url': url}


def resolve_link(api, url):
    url = download_http.public_url(url.strip())
    platform = service_links.identify(url)
    if platform and platform[0] in youtube_audio.NAMES:
        source, _, _ = youtube_audio.resolve(api, platform)
        return providers.register(api, source)
    if platform and platform[0] == 'SoundCloud':
        return set_import.soundcloud_source(api, platform[1])
    if platform:
        raise ValueError(f'{platform[0]} links cannot be downloaded. Paste a YouTube, SoundCloud or direct audio link.')
    resolved = providers.resolve(api, url)
    source = next((candidate for candidate in resolved.get('candidates', []) if candidate.get('kind') == 'direct'), None)
    if source is None:
        raise ValueError('This link has no downloadable audio. Paste a YouTube, SoundCloud or direct audio link.')
    return source


def change(api, tid, url):
    from . import acquisitions
    with api.connect() as db:
        t = api.track(db, tid)
    source = resolve_link(api, url)
    return acquisitions.manager(api).enqueue({'source_id': source['id'], 'replace_track_id': tid,
                                              'values': {'title': t['title'].strip() or 'Untitled', 'artist': t['artist']}})


def dispatch(api, db, method, p):
    if method == 'library.job':
        if p.get('id') not in preparation.JOBS:
            raise ValueError('This request expired; try again.')
        return dict(preparation.JOBS[p['id']])
    tid = p.get('id') if isinstance(p.get('id'), str) else ''
    t = api.track(db, tid)
    if method == 'library.source_options':
        query = p.get('query')
        if query is not None and (not isinstance(query, str) or len(query) > 300):
            raise ValueError('Keep the search within 300 characters.')
        current = current_source(db, tid)
        return preparation.launch(lambda: options(api, t, query, current))
    if method == 'library.change_source':
        url = p.get('url')
        if not isinstance(url, str) or not url.strip() or len(url) > 4096:
            raise ValueError('Paste a YouTube, SoundCloud or direct audio link.')
        db.commit()
        return preparation.launch(lambda: change(api, tid, url))
    if method == 'library.preview_source':
        url = p.get('url')
        if url is not None and (not isinstance(url, str) or len(url) > 4096):
            raise ValueError('Choose a version to preview.')
        return preparation.launch(lambda: preview(api, t, url, p.get('convert') is True))
    if method == 'library.replace_file':
        path = p.get('path')
        if not isinstance(path, str) or not path:
            raise ValueError('Choose an audio file')
        updated, previous = replace_audio(api, db, tid, path)
        db.commit()
        return dict(updated, previous_deleted=discard_previous(api, db, previous))
    raise ValueError('Unknown library method')
