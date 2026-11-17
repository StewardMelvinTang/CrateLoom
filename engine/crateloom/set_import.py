"""Turn a DJ mix into a reviewable tracklist and an ordered local playlist."""
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import nullcontext
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import threading
import time
import urllib.parse
import uuid

from . import acquisitions, download_http, playlist_sources, preparation, providers, scan_audio, service_links, shazam_scan, soundcloud_download, youtube_audio
from .metadata_matching import clean_name, normalized, search_identity
from . import set_discovery, set_scan_jobs
from .library_imports import ImportCancelled

MAX_TRACKS = 150
SPOTIFY_TOKEN = None
AUTO_STOP = threading.Event()
AUTO_WAKE = threading.Event()
AUTO_RUNNING = None
MAX_STALLED_ATTEMPTS = 4
INSTALL_RECOGNIZER = 'Install the ShazamIO scanner with scripts/setup-shazam-macos.sh, then restart CrateLoom.'
# Searches for one entry (base and "extended mix" queries) run side by side.
SEARCH_POOL = ThreadPoolExecutor(max_workers=8, thread_name_prefix='set-search')
INDEX_CACHE = {}
INDEX_LOCK = threading.Lock()
INDEX_TTL = 5
# Mix downloads left running for a saved report's recognition pass, by (library root, report id).
PREFETCHES = {}
PREFETCH_LOCK = threading.Lock()
PASS_LOCKS = {}
PASS_LOCKS_GUARD = threading.Lock()
# A recognized hit belongs to a published row when it starts within this many seconds of it.
NEAR_ROW = 30
LAST_ROW_SPAN = 480
TIMESTAMP = re.compile(r'^\s*(?:\[|\()?((?:\d{1,3}:)?\d{1,3}:\d{2})(?:\]|\))?\s*[–—-]?\s+(.+)$')
TRAILING_TIMESTAMP = re.compile(r'^\s*(.+?)\s+(?:[–—-]\s*)?[\[(]?((?:\d{1,3}:)?\d{1,3}:\d{2})[\])]?\s*$')
SEPARATOR = re.compile(r'\s+[-–—]\s+')
LINK = re.compile(r'https://[^\s<>]+')
FREE_DOWNLOAD = '45d0cbc5-d65b-4e77-bdfd-8a75207cb5c5'
VERSION_CREDIT = re.compile(r'\b(edit|remix|mix|flip|refunk|rework|bootleg|version|blend)$', re.I)
# Read on normalized text, where "M/V" becomes "m v". A lyric video or visualizer carries the song's own audio.
MUSIC_VIDEO = re.compile(r'\b(?:official\s+(?:music\s+)?video|music\s+video|official\s+mv|mv|m\s+v|short\s+film|video\s+oficial|clip\s+officiel)\b')


def music_video(title, song=''):
    """Whether a video title labels a music video; a song named "Video Games" or "MV" is not one."""
    return bool(MUSIC_VIDEO.search(normalized(title))) and not MUSIC_VIDEO.search(normalized(song))
AUDIO_LABEL = re.compile(r'\b(?:official\s+)?audio\b')
VIDEO_VARIANT = re.compile(r'\b(sped up|slowed|nightcore|karaoke|cover|reaction|remastered|live|instrumental|acoustic|a ?cappella|acapella|vocals|stems|8d|reverb|bass boosted)\b', re.I)
FEATURE_CREDIT = re.compile(r'\s+(?:[\[(]\s*)?(?:ft|feat|featuring)\.?\s+', re.I)


def schema(db):
    db.executescript("""
        CREATE TABLE IF NOT EXISTS dj_sets (
            id TEXT PRIMARY KEY, name TEXT NOT NULL, watch TEXT, warning TEXT,
            playlist_id TEXT REFERENCES playlists(id)
        );
        CREATE TABLE IF NOT EXISTS dj_set_items (
            set_id TEXT NOT NULL REFERENCES dj_sets(id), position INTEGER NOT NULL, detail TEXT NOT NULL,
            track_id TEXT REFERENCES tracks(id), download_id TEXT REFERENCES acquisitions(id),
            error TEXT, cue_pending INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY(set_id, position)
        );
        CREATE INDEX IF NOT EXISTS dj_set_items_download ON dj_set_items(download_id);
        CREATE INDEX IF NOT EXISTS dj_set_items_track ON dj_set_items(track_id);
        CREATE TABLE IF NOT EXISTS dj_set_scan (
            set_id TEXT PRIMARY KEY REFERENCES dj_sets(id),
            offset REAL, duration REAL, path TEXT, retry_at REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS dj_set_auto_scan (
            set_id TEXT PRIMARY KEY REFERENCES dj_set_scan(set_id),
            enabled INTEGER NOT NULL, failures INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS dj_set_scan_work (
            set_id TEXT PRIMARY KEY REFERENCES dj_set_scan(set_id), plan TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS dj_set_history (
            set_id TEXT PRIMARY KEY REFERENCES dj_sets(id), created_at REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS dj_set_youtube_access (
            set_id TEXT PRIMARY KEY REFERENCES dj_sets(id), browser TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS dj_set_recognition (
            set_id TEXT PRIMARY KEY REFERENCES dj_sets(id), started_at REAL NOT NULL, completed_at REAL,
            warning TEXT, confirmed INTEGER NOT NULL DEFAULT 0, added INTEGER NOT NULL DEFAULT 0
        );
    """)


def timestamp(value):
    parts = [int(part) for part in value.split(':')]
    if parts[-1] >= 60 or (len(parts) == 3 and parts[-2] >= 60):
        return None
    return sum(part * 60 ** i for i, part in enumerate(reversed(parts)))


def listed_item(at, label, evidence):
    links = LINK.findall(label)
    label = LINK.sub('', label).strip(' \t|-–—')
    pair = SEPARATOR.split(label, maxsplit=1)
    if len(pair) == 2 and all(pair):
        artist, title = pair
    else:
        artist, title = '', label
    if not title:
        return None
    return normalize_credit({'at': at, 'artist': artist[:500], 'title': title[:500], 'evidence': [evidence],
            'listed_links': links[:3], 'unknown': bool(re.fullmatch(r'(?:ID|unknown)(?:\s*[-–—x/]\s*ID)?[\s?]*', label, re.I))})


def normalize_credit(item):
    # Published lists may put a remix credit between the song and performer.
    parts = SEPARATOR.split(item['title'], maxsplit=1)
    if len(parts) == 2 and item['artist'] and VERSION_CREDIT.search(parts[0]):
        item.update(title=f"{item['artist']} ({parts[0]})"[:500], artist=parts[1], credit_confirmed=True)
    return item


def parse_tracklist(description, evidence, *, include_uncredited=False):
    rows = []
    for line in description.splitlines():
        line = re.sub(r'^\s*(?:\d{1,3}[.)]\s*|[•*]\s*)','',line).strip()
        match = TIMESTAMP.match(line)
        if match:
            stamp,label = match[1],match[2]
        else:
            match = TRAILING_TIMESTAMP.match(line)
            if not match:
                continue
            label,stamp = match[1],match[2]
        at = timestamp(stamp)
        item = listed_item(at, label, evidence)
        if at is not None and item and (item['artist'] or item['unknown'] or include_uncredited):
            rows.append(item)
    return rows


def number(value):
    return value if type(value) in (int, float) and math.isfinite(value) and value >= 0 else None


def fingerprint_tracks(payload):
    rows = []
    for hit in payload['hits']:
        at, end = number(hit['start']), number(hit['end'])
        title = service_links.clean(hit['title'])
        artist = service_links.clean(hit['artist'])
        if at is None or end is None or end <= at or not title or not artist:
            continue
        track_id = hit['key']
        links = [{'label': 'Shazam', 'url': 'https://www.shazam.com/track/' + track_id}] if re.fullmatch(r'[0-9]{1,20}', track_id) else []
        rows.append({'at':at, 'played':end-at, 'title':title, 'artist':artist,
                     'score':None, 'duration':None, 'isrc':None, 'evidence':['Fingerprint'],
                     'listed_links':[], 'links':links, 'unknown':False,
                     'needs_review':hit['samples'] < 2})
    return rows


def track_identity(artist, title):
    identity = search_identity(title, artist)
    suffix = re.search(r'\(([^()]+)\)$', identity['title'])
    if not identity['version'] and suffix and VERSION_CREDIT.search(suffix[1]):
        identity['title'] = identity['title'][:suffix.start()].rstrip()
        identity['version'] = suffix[1]
    return identity


def key(artist, title):
    identity = track_identity(artist, title)
    song, *guests = FEATURE_CREDIT.split(identity['title'], maxsplit=1)
    credit = ' '.join(FEATURE_CREDIT.split(identity['artist']) + guests)
    return normalized(credit), normalized(song), normalized(identity['version'])


def credit_options(item):
    artist, title = item['artist'], item['title']
    options = [(artist, title)]
    if not item.get('credit_confirmed') and any(e in ('Video description', 'Video chapter') for e in item['evidence']):
        options.append((title, artist))
    return options


def expected_length(item):
    """The song's own length: the listed one, else the catalog's."""
    return item.get('duration') or item.get('catalog_duration')


def video_match(item, videos):
    matches, suggestions = [], []
    expected_duration = expected_length(item)
    for video in videos:
        title = clean_name(video['title'])
        # "Artist - Topic" channels hold YouTube's audio-only uploads of released recordings.
        topic = bool(re.search(r'\s+-\s+Topic$', video.get('channel') or '', re.I))
        channel = re.sub(r'\s+-\s+Topic$', '', video.get('channel') or '', flags=re.I)
        url = 'https://www.youtube.com/watch?v=' + video['id']
        if not item['artist']:
            # A row published without an artist is only offered for review, never matched.
            words = key('', item['title'])[1].split()
            if words and set(words).issubset(normalized(title + ' ' + channel).split()) and not any(s['url'] == url for s in suggestions):
                suggestions.append({'title': video['title'], 'url': url, 'duration': video['duration'], 'channel': channel,
                                    'verified': video.get('channel_is_verified') is True, 'title_only': True})
            continue
        for artist, song in credit_options(item):
            expected = key(artist, song)
            possible = {key('', title)}
            if channel:
                possible.add(key(channel, title))
            label = normalized(title + ' ' + channel)
            if not set(expected[1].split()).issubset(label.split()):
                continue
            if not set(expected[0].split()).intersection(label.split()):
                continue
            if not any(s['url'] == url for s in suggestions):
                suggestions.append({'title': video['title'], 'url': url, 'duration': video['duration'],
                                    'channel': channel, 'verified': video.get('channel_is_verified') is True, 'topic': topic})
            if expected not in possible or (VIDEO_VARIANT.search(title) and not VIDEO_VARIANT.search(song)):
                continue
            if expected_duration and abs(video['duration'] - expected_duration) > max(5, expected_duration * .04):
                continue
            official = video.get('channel_is_verified') is True and normalized(channel) == expected[0]
            # Prefer the audio-only upload, then a recording from the artist, and avoid music video intros.
            score = 6 * topic + 4 * official + (2 if normalized(title) == normalized(song) else 0) - 5 * music_video(video['title'], song)
            matches.append((score, url, artist, song))
    # Two different interpretations of the same row need user review.
    if len({(artist, song) for _, _, artist, song in matches}) > 1:
        matches = []
    return sorted(matches, key=lambda match: -match[0]), suggestions


def source_priority(item, option):
    expected = track_identity(item['artist'], item['title'])
    title = normalized(option['title'])
    if option.get('reference_only') or (VIDEO_VARIANT.search(title) and not VIDEO_VARIANT.search(item['title'])):
        return None
    if expected['version'] and not set(normalized(expected['version']).split()).issubset(title.split()):
        return None
    if not expected['version'] and re.search(r'\b(remix|bootleg|rework|mashup)\b', title):
        return None
    duration, original = option.get('duration'), expected_length(item)
    extended = bool(re.search(r'\bextended\b', title))
    if duration and original and not original * .65 <= duration <= original * (2.2 if extended else 1.4):
        return None
    video = music_video(option['title'], item['title'])
    # The audio-only "Topic" upload is the released recording without a video's intro or outro.
    score = 40 * extended + 12 * bool(option.get('topic')) + 8 * bool(AUDIO_LABEL.search(title)) + 4 * option.get('verified', False) - 20 * video
    if duration and original and not extended:
        score -= abs(duration - original) / original * 12
    return score, -(duration or 900)


def audio_version(api, item, listed, page, search=None):
    """The audio-only upload to use instead of a playlist's linked music video, or None to keep the video.

    The linked video counts as a music video when its title says so, or when the song's audio-only
    upload ("Artist - Topic", or an "Official Audio" upload) is clearly shorter. A video already from
    a Topic channel, or one no audio upload contradicts, is kept.
    """
    if listed.get('topic') or not item.get('artist') or item.get('needs_review'):
        return None
    search = search or (lambda query: youtube_audio.search(api, query))
    marked = music_video(listed.get('raw_title') or '', item['title'])
    length = listed.get('duration')
    if type(length) not in (int, float) or length <= 0:
        return None
    probe = dict(item, duration=None, catalog_duration=None)

    def audio_uploads(query):
        try:
            videos = search(query)
        except ValueError:
            return []
        videos = [video for video in videos if not page[2].endswith('=' + video['id'])]
        _, found = video_match(probe, videos)
        return [option for option in found if not option.get('title_only')
                and (option.get('topic') or AUDIO_LABEL.search(normalized(option['title'])))
                and not music_video(option['title'], item['title']) and not re.search(r'\bextended\b', normalized(option['title']))
                and source_priority(probe, option) is not None
                # A video may be a shortened edit, but not a different piece altogether.
                and length * .5 <= option['duration'] <= length + max(8, length * .06)]

    query = ' '.join((item['artist'], item['title']))
    audio = audio_uploads(query)
    if not audio and marked:
        # A labelled music video is worth one more search aimed at the audio upload.
        audio = audio_uploads(query + ' audio')
    audio.sort(key=lambda option: (not option.get('topic'), not option.get('verified')))
    for option in audio[:3]:
        if not marked and length - option['duration'] <= max(8, option['duration'] * .06):
            # The same length as the song: the linked video has no intro or outro worth avoiding.
            return None
        try:
            replacement, _, _ = youtube_audio.resolve(api, service_links.identify(option['url']))
        except (ValueError, OSError):
            continue
        # The playlist's own link stays under its service name; this records why it was not downloaded.
        item['music_video_skipped'] = page[1]
        return replacement
    return None


def recommend_sources(item):
    options = list({option['url']: option for option in item.get('source_options', [])}.values())
    ranked = []
    for option in options:
        option.pop('recommended', None)
        option.pop('recommendation_reason', None)
        if option.get('title_only'):
            # Neither compatible nor recommended: nothing may pick it without the owner.
            option.pop('compatible', None)
            continue
        priority = source_priority(item, option)
        option['compatible'] = priority is not None
        if priority is not None:
            ranked.append((*priority, option))
    ranked.sort(key=lambda candidate: (-candidate[0], -candidate[1]))
    if ranked:
        best = ranked[0][2]
        best['recommended'] = True
        best['recommendation_reason'] = ('Extended mix' if re.search(r'\bextended\b', normalized(best['title']))
                                         else 'Audio-only upload' if best.get('topic') else 'Audio version / closest duration')
    options.sort(key=lambda option: not option.get('recommended', False))
    item['source_options'] = options[:10]
    return next((option for option in item['source_options'] if option.get('recommended')), None)


def in_thread(fn, *args):
    """Run on the search pool with the caller's cancellation, so a stopped scan kills its yt-dlp."""
    control = set_scan_jobs.current()
    def work():
        if control is None:
            return fn(*args)
        with control.activate(register=False):
            return fn(*args)
    return SEARCH_POOL.submit(work)


def search_video_options(api, item, watch, search=None):
    """Search YouTube for an entry. Returns ranked matches and the versions to review."""
    search = search or (lambda query: youtube_audio.search(api, query))
    if item['artist']:
        queries = [' '.join((item['artist'], item['title']))]
        for artist, song in credit_options(item):
            main_artist, *guests = FEATURE_CREDIT.split(artist, maxsplit=1)
            if guests:
                queries.append(' '.join((main_artist, song)))
    else:
        queries = [item['title']]
    versioned = bool(track_identity(item['artist'], item['title'])['version'])
    matches, suggestions = [], []
    for query in dict.fromkeys(queries):
        # The extended version is looked for at the same time instead of after the first search.
        extended = None if versioned or not item['artist'] else in_thread(search, query + ' extended mix')
        try:
            videos = search(query)
        except BaseException:
            if extended:
                extended.cancel()
            raise
        if extended:
            try:
                more = extended.result()
            except ValueError:
                more = []
            if not any(re.search(r'\bextended\b', video['title'], re.I) for video in videos):
                videos = videos + more
        if watch:
            videos = [video for video in videos if not watch.endswith('=' + video['id'])]
        matches, found = video_match(item, videos)
        suggestions.extend(option for option in found if option not in suggestions)
        if matches:
            break
    return matches, suggestions


def choose_video_source(api, item, matches):
    """Resolve and register the recommended version or the best match."""
    recommended = recommend_sources(item)
    error = None
    if item.get('needs_review') and sum(source_priority(item, option) is not None for option in item.get('source_options', [])) != 1:
        return None, error
    choices = [(recommended['url'], item['artist'], item['title'])] if recommended else []
    choices += [(url, artist, song) for _, url, artist, song in matches if url not in [choice[0] for choice in choices]]
    for url, artist, song in choices[:10]:
        try:
            source, _, _ = youtube_audio.resolve(api, service_links.identify(url))
        except (ValueError, OSError) as exc:
            error = str(exc)
            continue
        item.update(artist=artist, title=song, credit_confirmed=True)
        return providers.register(api, source), None
    return None, error


def find_video_source(api, item, watch):
    matches, suggestions = search_video_options(api, item, watch)
    item['source_options'] = suggestions
    return choose_video_source(api, item, matches)


def search_soundcloud_options(api, item):
    """Search SoundCloud; adds the found versions to the entry's options and returns the exact matches."""
    query_identity = track_identity(item['artist'], item['title'])
    version = query_identity['version']
    query = ' '.join((query_identity['title'], version or query_identity['artist']))
    matches = []
    for entry in soundcloud_download.search(api, query):
        title = re.sub(r'\bedition\b', 'edit', entry['title'].split(' | ', 1)[0], flags=re.I)
        uploader = entry.get('uploader') or ''
        candidate_key = key(uploader, title)
        for artist, song in credit_options(item):
            expected = key(artist, song)
            editor = VERSION_CREDIT.sub('', expected[2]).strip()
            by_editor = editor and normalized(uploader) == editor and candidate_key[1:] == expected[1:]
            if not (by_editor or candidate_key == expected or key('', title) == expected):
                continue
            duration = item.get('duration')
            if duration and abs(entry['duration'] - duration) > max(5, duration * .04):
                continue
            matches.append((entry, artist, song))
            break
    item['source_options'] = [*item.get('source_options', []), *[
        {'title': entry['title'], 'url': entry['url'], 'duration': entry['duration'], 'reference_only': not soundcloud_download.configured()}
        for entry, _, _ in matches]]
    recommend_sources(item)
    return matches


def find_soundcloud_source(api, item):
    matches = search_soundcloud_options(api, item)
    if not matches or not soundcloud_download.configured():
        return None
    if item.get('needs_review') and sum(option.get('compatible', False) for option in item['source_options']) != 1:
        return None
    entry, artist, song = matches[0]
    item.update(artist=artist, title=song, credit_confirmed=True)
    return soundcloud_source(api, entry['url'])


def soundcloud_source(api, url):
    info, _ = soundcloud_download.resolve(url, cache=True)
    return providers.register(api, {'kind': 'direct', 'provider': 'SoundCloud', 'adapter': 'soundcloud',
        'url': url, 'filename': info['filename'], 'declared_bytes': info['declared_bytes']})


def merge_tracklists(listed, fingerprints):
    rows = sorted(listed, key=lambda item: item['at'])
    published = list(rows)
    for hit in sorted(fingerprints, key=lambda item: item['at']):
        identity = key(hit['artist'], hit['title'])
        matches = [row for row in published if abs(row['at'] - hit['at']) <= 30 and key(row['artist'], row['title'])[:2] == identity[:2]]
        if matches:
            row = min(matches, key=lambda item: abs(item['at'] - hit['at']))
            if key(row['artist'], row['title']) == identity:
                if 'Fingerprint' not in row['evidence']:
                    row.update({k: hit[k] for k in ('duration', 'isrc', 'links', 'score')})
                    row['evidence'].append('Fingerprint')
            else:
                row['version_warning'] = 'Fingerprint matched a different version.'
                row['needs_review'] = True
            continue
        prior = next((row for row in reversed(rows) if row['evidence'] == ['Fingerprint']
                      and key(row['artist'], row['title']) == identity
                      and 0 <= hit['at'] - row['at'] <= (row['played'] or 0) + 15), None)
        if prior:
            end = max(prior['at'] + (prior['played'] or 0), hit['at'] + (hit['played'] or 0))
            prior['played'] = end - prior['at']
            if not published and not hit['needs_review']:
                prior['needs_review'] = False
            continue
        if published:
            # An extra fingerprint may be an acapella, sample, or transition.
            hit['needs_review'] = True
        rows.append(hit)
    return sorted(rows, key=lambda item: item['at'])


class AudioPrefetch:
    """Fetch the mix audio while the comments are read; only the scan's own thread publishes progress."""
    def __init__(self, api, watch):
        self.parent = set_scan_jobs.current()
        self.control = set_scan_jobs.ScanControl(api)
        self.control.youtube_browser = self.parent.youtube_browser if self.parent else None
        self.path = self.error = None
        self.owner = None
        self.thread = threading.Thread(target=self.run, args=(api, watch), name='set-audio-prefetch', daemon=True)
        self.thread.start()

    def check(self):
        # A prefetch kept for a saved report outlives the scan job; only the report stops it then.
        if self.parent and not self.owner:
            self.parent.check()
        self.control.check()
        shazam_scan.check_running()

    def run(self, api, watch):
        try:
            with self.control.activate():
                self.path = scan_audio.obtain(api, watch, self.check)
        except BaseException as error:
            self.error = error
        finally:
            self.release()

    def keep_for(self, api, sid):
        """Leave the download running for the report's queued recognition pass, stoppable through the report."""
        with PREFETCH_LOCK:
            self.owner = (str(Path(api.ROOT).resolve()), sid)
            PREFETCHES[self.owner] = self
        if not self.thread.is_alive():
            self.release()

    def release(self):
        with PREFETCH_LOCK:
            if self.owner and PREFETCHES.get(self.owner) is self:
                PREFETCHES.pop(self.owner)

    def cancel(self):
        self.control.stop.set()

    def result(self):
        shown = None
        try:
            while self.thread.is_alive():
                set_scan_jobs.check()
                progress = dict(self.control.progress)
                if progress != shown and progress.get('stage') != 'Preparing scan':
                    shown = progress
                    set_scan_jobs.progress(progress.pop('stage'), **progress)
                self.thread.join(.1)
        except BaseException:
            self.cancel()
            raise
        if self.error is not None:
            if isinstance(self.error, ImportCancelled):
                set_scan_jobs.check()
            raise self.error
        return self.path


def stop_prefetch(root, sid):
    with PREFETCH_LOCK:
        prefetch = PREFETCHES.pop((str(Path(root).resolve()), sid), None)
    if prefetch:
        prefetch.cancel()


def video_info(api, watch, recognize=False):
    from . import youtube_tracklist
    set_scan_jobs.progress('Reading published tracklist')
    data = None
    # Page metadata does not depend on whether an audio client passes YouTube's challenge.
    try:
        data = youtube_tracklist.read(watch)
    except (OSError,ValueError):
        set_scan_jobs.check()
    if data is None:
        binary = youtube_audio.executable()
        try:
            if not binary:
                raise ValueError('The extractor is unavailable.')
            result = youtube_audio.run_helper(api, binary, [
                '--ignore-config', '--no-plugin-dirs', '--no-remote-components', '--proxy', '',
                '--no-playlist', '--no-warnings', '--socket-timeout', '8', '--retries', '0',
                '--extractor-retries', '0', '--skip-download', '--ignore-no-formats-error',
                *youtube_audio.runtime_args(),'--print', '%(.{id,title,description,chapters,duration,is_live})j', '--', watch,
            ], 25)
            data = json.loads(result.stdout)
            expected = urllib.parse.parse_qs(urllib.parse.urlsplit(watch).query)['v'][0]
            if not isinstance(data,dict) or data.get('id') != expected:
                raise ValueError('The metadata did not belong to this video.')
        except (OSError,ValueError,subprocess.SubprocessError) as error:
            raise ValueError('The published tracklist is unavailable. Checking the mix audio next.') from error
    if data.get('is_live'):
        raise ValueError('Live mixes are not supported until the broadcast ends.')
    credited = parse_tracklist(data.get('description') or '','Video description')
    chapter_credits = [listed_item(c.get('start_time'),c.get('title') or '','Video chapter') for c in data.get('chapters') or []]
    if not credited and not any(c and c['artist'] for c in chapter_credits):
        # The audio is probably needed: start fetching it while the comments are read.
        prefetch = AudioPrefetch(api, watch) if shazam_scan.interpreter() else None
        try:
            candidates = []
            for comment in youtube_tracklist.read_comments(api,watch):
                rows = parse_tracklist(comment,'Published comment tracklist')
                times = [row['at'] for row in rows]
                if (len(rows)>=3 and times==sorted(set(times)) and times[0]<=120
                        and all(b-a>=15 for a,b in zip(times,times[1:]))
                        and (not data.get('duration') or times[-1]<data['duration'])):
                    candidates.append(rows)
        except BaseException:
            if prefetch:
                prefetch.cancel()
            raise
        if candidates:
            data['comment_tracklist'] = max(candidates,key=len)
            if prefetch and not recognize:
                # A published list is enough; stop before the audio is kept.
                prefetch.cancel()
                prefetch = None
        if prefetch:
            data['audio_prefetch'] = prefetch
    return data


def scan(api, watch, path, pasted, recognize):
    notes, listed = [], []
    info = {}
    control = set_scan_jobs.current()
    prefetch = None
    cancelled = False
    try:
        if pasted.strip():
            set_scan_jobs.progress('Reading pasted tracklist')
            listed = parse_tracklist(pasted, 'Pasted tracklist', include_uncredited=True)
            if not listed:
                notes.append('No timestamped entries were found in the pasted tracklist.')
        if watch and (not listed or recognize):
            try:
                info = video_info(api, watch, recognize)
            except ValueError as error:
                if str(error).startswith('Live mixes') and not listed:
                    raise
                notes.append(str(error))
            prefetch = info.pop('audio_prefetch', None)
        if not pasted.strip():
            listed = parse_tracklist(info.get('description') or '', 'Video description',include_uncredited=True)
            chapter_rows = [listed_item(number(c.get('start_time')), c.get('title') or '', 'Video chapter')
                            for c in info.get('chapters') or []]
            listed.extend(c for c in chapter_rows if c and c['at'] is not None
                          and not any(abs(c['at'] - row['at']) <= 2 for row in listed))
            if not any(row['artist'] for row in listed) and info.get('comment_tracklist'):
                listed = info['comment_tracklist']
        set_scan_jobs.progress('Tracklist checked',completed=len(listed),total=len(listed) or None,unit='entries')
    except ImportCancelled:
        if prefetch:
            prefetch.cancel()
        if not listed:
            raise
        cancelled = True
    name = service_links.clean(info.get('title')) or (Path(path).stem if path else 'Imported DJ Set')
    if not listed:
        return fingerprint_scan(api, name, watch, path, notes, prefetch)
    has_tracklist = any(row['artist'] and not row['unknown'] for row in listed)
    recognition = bool(recognize or (watch and not has_tracklist)) and not cancelled
    if recognition and not shazam_scan.interpreter():
        notes.append('The mix audio was not scanned. ' + INSTALL_RECOGNIZER)
        recognition = False
    if prefetch and not recognition:
        prefetch.cancel()
    try:
        rows = sorted(listed, key=lambda item: item['at'])
        length = number(info.get('duration'))
        if length:
            rows = [row for row in rows if row['at'] < length]
        if not rows:
            raise ValueError(' '.join(notes) if notes else 'No published entries or audio matches were available for this mix.')
        if len(rows)>MAX_TRACKS:
            raise ValueError(f'This set has more than {MAX_TRACKS} track entries. Split it into smaller sets.')
        with control.lock if control else nullcontext():
            cancelled = cancelled or bool(control and control.stop.is_set())
            if cancelled:
                notes.append('Scan stopped. Published entries were kept.')
                recognition = False
            else:
                set_scan_jobs.progress('Saving set report',completed=len(rows),total=len(rows) or None,unit='entries')
            saved = save_report(api,name,watch,rows,' '.join(notes) or None,recognition=path if recognition and path else recognition)
            if control:
                control.result=saved
    except BaseException:
        if prefetch:
            prefetch.cancel()
        raise
    if prefetch:
        # A prefetch that keeps running leaves the audio ready for the recognition pass;
        # cancelling or deleting the report stops it.
        if recognition:
            prefetch.keep_for(api, saved['id'])
        else:
            prefetch.cancel()
    return find_sources(api, saved)


def find_sources(api, saved):
    """Start source discovery for a saved report; the returned report shows the queued entries."""
    if not set_discovery.schedule(api, saved['id']):
        return saved
    with api.connect_existing() as db:
        latest = report(db, saved['id'])
    control = set_scan_jobs.current()
    if control:
        with control.lock:
            control.result = latest
    return latest


def fingerprint_scan(api, name, watch, path, notes, prefetch=None):
    """Save a report for a mix without a published list as soon as its audio is ready.

    Recognition then runs as the automatic continuation, so the report and its
    progress are visible while Shazam is still listening.
    """
    control = set_scan_jobs.current()
    try:
        if not watch and not path:
            raise ValueError(' '.join(notes) if notes else 'No published entries or audio matches were available for this mix.')
        if not shazam_scan.interpreter():
            raise ValueError(' '.join([*notes, INSTALL_RECOGNIZER]))
        try:
            if prefetch:
                source = prefetch.result()
            elif path:
                source = Path(path).expanduser().resolve(strict=True)
            else:
                source = shazam_scan.video_audio(api, watch)
            if not Path(source).is_file():
                raise ValueError('Choose a local audio file.')
        except (FileNotFoundError, download_http.SourceHTTPError) as error:
            raise ValueError(' '.join([*notes, str(error)])) from None
        except ValueError as error:
            raise ValueError(' '.join([*notes, str(error)])) from error
        set_scan_jobs.progress('Checking mix audio',completed=0,total=1,unit='steps')
        try:
            duration, _, _ = api.probe(source)
        except (subprocess.SubprocessError, ValueError, OSError):
            raise ValueError('Could not read the mix audio.') from None
        if not number(duration) or duration > shazam_scan.MAX_DURATION:
            raise ValueError('Choose a mix up to 3 hours long.')
    except BaseException:
        if prefetch:
            prefetch.cancel()
        raise
    with control.lock if control else nullcontext():
        if control and control.stop.is_set():
            raise ImportCancelled('Scan cancelled')
        set_scan_jobs.progress('Saving set report',completed=0,total=None,unit='entries')
        saved = save_report(api,name,watch,[],' '.join(notes) or None,checkpoint=(0,duration,path,time.time()),
                            plan={'samples':{},'serial':False})
        if control:
            control.result=saved
        return saved


def fingerprint_timeline(rows, length, *, covered=0):
    """Show observed spans and substantial gaps without inventing song boundaries."""
    timeline = []
    for row in rows:
        start = row['at']
        if start - covered >= 20:
            timeline.append(unrecognized(covered, start))
        end = start + (row.get('played') or 0)
        if length:
            end = min(end, length)
        if end > start:
            row['end'] = end
        timeline.append(row)
        covered = max(covered, end)
    if length and length - covered >= 20:
        timeline.append(unrecognized(covered, length))
    return timeline


def unrecognized(start, end):
    return {'at': start, 'end': end, 'title': 'Unidentified audio', 'artist': '',
            'evidence': [], 'listed_links': [], 'unknown': True}


def save_report(api, name, watch, rows, warning, *, checkpoint=None, plan=None, automatic=True, recognition=False):
    """Save a new report. ``recognition`` (True, or a local file path) also queues an audio pass over the list."""
    sid = str(uuid.uuid4())
    with api.connect() as db:
        db.execute('INSERT INTO dj_sets VALUES (?,?,?,?,NULL)', (sid, name[:100], watch, warning))
        db.execute('INSERT INTO dj_set_history VALUES (?,?)',(sid,time.time()))
        control = set_scan_jobs.current()
        if control and control.youtube_browser:
            db.execute('INSERT INTO dj_set_youtube_access VALUES (?,?)',(sid,control.youtube_browser))
        db.executemany('INSERT INTO dj_set_items(set_id,position,detail) VALUES (?,?,?)',
                       [(sid, i, json.dumps(row)) for i, row in enumerate(rows)])
        if checkpoint:
            db.execute('INSERT INTO dj_set_scan VALUES (?,?,?,?,?)', (sid,*checkpoint))
            if plan is not None:
                store_scan_plan(db,sid,plan)
            if checkpoint[0] is not None:
                if automatic:
                    queue_auto_scan(db,sid,watch)
                    AUTO_WAKE.set()
                else:
                    db.execute('INSERT INTO dj_set_auto_scan(set_id,enabled) VALUES (?,0)',(sid,))
        elif recognition:
            start_recognition(db,sid,watch,recognition if isinstance(recognition,str) else None)
        result = report(db, sid)
    return result


def published(item):
    """A row from the published, pasted or playlist list, as opposed to one added from the audio."""
    evidence = item.get('evidence') or []
    return bool(evidence) and evidence != ['Fingerprint'] and number(item.get('at')) is not None


def start_recognition(db, sid, watch, path=None):
    """Queue one audio pass that is merged into the existing list. The caller holds the transaction."""
    starts = sorted({json.loads(row[0])['at'] for row in db.execute('SELECT detail FROM dj_set_items WHERE set_id=?',(sid,))
                     if published(json.loads(row[0]))})
    # Sample inside each listed track first, a little after it starts.
    hints = sorted({int(at + min(45, .4*((starts[i+1] if i+1<len(starts) else at+LAST_ROW_SPAN)-at))) for i,at in enumerate(starts)})
    db.execute("""INSERT INTO dj_set_scan VALUES (?,0,NULL,?,?) ON CONFLICT(set_id) DO UPDATE SET
        offset=0,duration=NULL,path=excluded.path,retry_at=excluded.retry_at""",(sid,path,time.time()))
    store_scan_plan(db,sid,{'samples':{},'serial':False,'hints':hints})
    db.execute("""INSERT INTO dj_set_recognition(set_id,started_at) VALUES (?,?) ON CONFLICT(set_id) DO UPDATE SET
        started_at=excluded.started_at,completed_at=NULL,warning=NULL""",(sid,time.time()))
    queue_auto_scan(db,sid,watch)
    AUTO_WAKE.set()


def get_set(db, sid):
    found = db.execute('SELECT * FROM dj_sets WHERE id=?', (sid,)).fetchone()
    if found is None:
        raise ValueError('Set report not found.')
    return dict(found)


def delete_report(db, sid):
    get_set(db,sid)
    set_discovery.cancel(db,sid)
    control = set_scan_jobs.for_database(db,sid)
    if control:
        # Stop the scan before its report disappears; its thread then ends as cancelled.
        control.cancel()
    stop_prefetch(Path(db.execute('PRAGMA database_list').fetchone()[2]).parent,sid)
    db.execute('BEGIN IMMEDIATE')
    get_set(db,sid)
    for table in ('dj_set_scan_work','dj_set_auto_scan','dj_set_scan','dj_set_recognition','dj_set_items','dj_set_youtube_access','dj_set_history','dj_sets'):
        column = 'id' if table=='dj_sets' else 'set_id'
        db.execute(f'DELETE FROM {table} WHERE {column}=?',(sid,))
    db.commit()
    AUTO_WAKE.set()
    return {'deleted':sid}


def list_reports(db):
    rows = [dict(row) for row in db.execute("""SELECT s.id,s.name,s.playlist_id,s.watch,s.warning,h.created_at,
        count(i.position) AS entries,count(i.track_id) AS imported,
        coalesce(sum(i.download_id IS NOT NULL AND i.track_id IS NULL),0) AS with_download,
        c.offset,c.duration,coalesce(a.enabled,0) AS automatic
        FROM dj_sets s LEFT JOIN dj_set_history h ON h.set_id=s.id
        LEFT JOIN dj_set_scan c ON c.set_id=s.id
        LEFT JOIN dj_set_auto_scan a ON a.set_id=s.id
        LEFT JOIN dj_set_items i ON i.set_id=s.id
        GROUP BY s.id ORDER BY s.rowid DESC""")]
    for row in rows:
        # Agree with the report itself, which also recognises older partial scans.
        state = scan_checkpoint(db,row) if row['offset'] is None else None
        if state:
            row.update(offset=state['offset'],duration=state['duration'])
        del row['watch'],row['warning']
    return rows


def scan_checkpoint(db, saved):
    sid = saved['id']
    state = db.execute('SELECT offset,duration,path,retry_at FROM dj_set_scan WHERE set_id=?', (sid,)).fetchone()
    if state:
        work = db.execute('SELECT plan FROM dj_set_scan_work WHERE set_id=?',(sid,)).fetchone()
        return dict(state, **({'plan':json.loads(work['plan'])} if work else {}))
    if not saved['watch'] or not (saved['warning'] or '').startswith('Partial scan.'):
        return None
    # Older YouTube reports have no checkpoint. Resume at the last saved range.
    items = [json.loads(row[0]) for row in db.execute('SELECT detail FROM dj_set_items WHERE set_id=?', (sid,))]
    if items and all(row.get('unknown') or row.get('evidence') == ['Fingerprint'] for row in items):
        end = max((row.get('end') or 0 for row in items), default=0)
        if end:
            return {'offset':end,'duration':None,'path':None,'retry_at':0}


def store_scan_plan(db, sid, plan):
    db.execute("""INSERT INTO dj_set_scan_work VALUES (?,?)
        ON CONFLICT(set_id) DO UPDATE SET plan=excluded.plan""",(sid,json.dumps(plan)))


def shazam_cooldown(db):
    """The epoch time Shazam's saved cooldown for this library ends, or 0; it holds every report."""
    return shazam_scan.saved_until(Path(db.execute('PRAGMA database_list').fetchone()[2]).parent)


def release_backoff(db, sid):
    # An explicit resume skips the automatic stall backoff, but never Shazam's own cooldown,
    # which lasts up to 15 minutes after repeated rate limits.
    db.execute('UPDATE dj_set_scan SET retry_at=min(retry_at,?) WHERE set_id=?',
               (max(time.time()+shazam_scan.RATE_LIMIT_COOLDOWN,shazam_cooldown(db)),sid))


FAILURES = {
    'bad_reply': 'Shazam sent a reply CrateLoom could not read.',
    'unreachable': 'CrateLoom could not reach Shazam. The scan continues when the connection is back.',
    'tls': 'The secure connection to Shazam failed.',
    'decode': 'Part of the mix audio could not be decoded.',
    'internal': 'Audio recognition stopped unexpectedly. Try again.',
}
UNREACHABLE_COOLDOWN = 15


def failure_message(failure, cooldown):
    """The owner's sentence for a worker failure code; the code itself stays internal."""
    if failure.get('code') == 'rate_limited':
        minutes = max(1, math.ceil(cooldown/60))
        return f'Shazam is limiting requests from this Mac. The scan continues automatically in about {minutes} minute{"s" if minutes != 1 else ""}.'
    return FAILURES.get(failure.get('code'), FAILURES['internal'])


def recognition_hits(result):
    """Recognized rows with their stable key: the Shazam key and the rounded start."""
    hits = []
    for hit in result.get('hits') or []:
        try:
            rows = fingerprint_tracks({'hits':[hit]})
        except (KeyError, TypeError, AttributeError):
            continue
        if rows and isinstance(hit.get('key'), str):
            hits.append((f"{hit['key']}@{round(rows[0]['at'])}", rows[0]))
    return hits


def pass_lock(api, sid):
    with PASS_LOCKS_GUARD:
        return PASS_LOCKS.setdefault((str(Path(api.ROOT).resolve()), sid), threading.Lock())


def resume_scan(api, sid, *, manual=False):
    # One pass per report at a time, from reading the checkpoint to saving it: a second
    # caller (Resume racing the automatic continuation) then starts from the saved samples.
    lock = pass_lock(api, sid)
    if not lock.acquire(blocking=False):
        control = set_scan_jobs.current()
        if not control or not control.job_id:
            raise shazam_scan.ScanBusy('This audio scan is already running.')
        control.publish('Waiting for the current audio section')
        while not lock.acquire(timeout=.2):
            shazam_scan.check_running()
    try:
        return resume_pass(api, sid, manual=manual)
    finally:
        lock.release()


def resume_pass(api, sid, *, manual=False):
    with api.connect() as db:
        saved = get_set(db,sid)
        if manual:
            release_backoff(db,sid)
        state = scan_checkpoint(db,saved)
        access = db.execute('SELECT browser FROM dj_set_youtube_access WHERE set_id=?',(sid,)).fetchone()
    control = set_scan_jobs.current()
    if control and access:
        control.youtube_browser = access['browser']
    if not state or state['offset'] is None:
        raise ValueError('This report has no unfinished audio scan.')
    delay = math.ceil(state['retry_at']-time.time())
    if delay > 0:
        raise ValueError(f'Shazam is cooling down. Resume in {delay} seconds.')
    offset = state['offset']
    # Only the position identifies a pass; its sample plan is rewritten while it runs.
    checkpoint = (state['offset'],state['duration'],state['path'])
    options = {'plan':state['plan']} if 'plan' in state else {}
    if control:
        control.failure = None

    def store_partial(partial):
        # Keep each answered sample, so a stop or a crash never asks Shazam again.
        plan = partial.get('plan') if isinstance(partial,dict) else None
        if not isinstance(plan,dict):
            return
        with control.lock if control else nullcontext():
            if control and control.stop.is_set():
                return
            with api.connect_existing() as db:
                db.execute('BEGIN IMMEDIATE')
                current = db.execute('SELECT offset,duration,path FROM dj_set_scan WHERE set_id=?',(sid,)).fetchone()
                if current and tuple(current) == checkpoint:
                    store_scan_plan(db,sid,plan)
                db.commit()
    try:
        result = shazam_scan.scan(api, saved['watch'], state['path'], offset=offset, expected_duration=state['duration'],
                                  on_partial=store_partial, **options)
    except ImportCancelled:
        if not control or not control.partial:
            raise
        result = control.partial
    duration = result['duration']
    next_offset = number(result.get('scanned_until'))
    scanned_end = next_offset if next_offset is not None else duration
    failure = result.get('failure') if isinstance(result.get('failure'),dict) else None
    cooldown = number(result.get('cooldown')) or 0
    if failure and failure.get('code') == 'unreachable':
        cooldown = max(cooldown,UNREACHABLE_COOLDOWN)
    if control:
        control.failure = failure
    note = None
    if result.get('warning'):
        note = result['warning']
    if failure:
        note = failure_message(failure,cooldown)
    stopped = bool(control and control.stop.is_set())
    added = []
    with api.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        if stopped and not db.execute('SELECT 1 FROM dj_sets WHERE id=?',(sid,)).fetchone():
            raise ImportCancelled('Scan cancelled')
        current = scan_checkpoint(db,get_set(db,sid))
        if not current or (current['offset'],current['duration'],current['path']) != checkpoint:
            raise ValueError('This scan has already moved forward. Reopen the saved report.')
        merging = db.execute('SELECT 1 FROM dj_set_recognition WHERE set_id=?',(sid,)).fetchone()
        if merging:
            hits = [(rk,row) for rk,row in recognition_hits(result) if offset <= row['at'] < scanned_end]
            confirmed, inserted, added = merge_recognition(db,sid,hits)
            if stopped:
                note = 'Scan stopped. Completed matches and audio samples were kept.'
            # The published list keeps its own warning; recognition reports separately.
            db.execute("""UPDATE dj_set_recognition SET confirmed=confirmed+?,added=added+?,warning=?,
                completed_at=? WHERE set_id=?""",(confirmed,inserted,note,time.time() if next_offset is None else None,sid))
        else:
            matches = [row for row in fingerprint_tracks(result) if offset <= row['at'] < scanned_end]
            warning = None if 'plan' in result else 'Shazam timestamps are approximate. Check track versions and transitions.'
            if note:
                warning = 'Partial scan. ' + note
            if stopped:
                warning = 'Scan stopped. Completed matches and audio samples were kept.'
            tail = db.execute('SELECT position,detail FROM dj_set_items WHERE set_id=? ORDER BY position DESC LIMIT 1', (sid,)).fetchone()
            covered = offset
            position = 0
            if tail:
                position = tail['position']+1
                previous = json.loads(tail['detail'])
                covered = previous.get('end') or offset
                if (matches and covered == matches[0]['at'] and previous.get('evidence') == ['Fingerprint']
                        and previous.get('links') and previous['links'] == matches[0]['links']):
                    continued = matches.pop(0)
                    covered = min(scanned_end, continued['at']+continued['played'])
                    previous.update(end=covered, played=covered-previous['at'], needs_review=False)
                    db.execute('UPDATE dj_set_items SET detail=? WHERE set_id=? AND position=?',
                               (json.dumps(previous),sid,tail['position']))
            rows = fingerprint_timeline(matches,scanned_end,covered=covered)
            for i,row in enumerate(rows):
                inserted = db.execute('INSERT INTO dj_set_items(set_id,position,detail) VALUES (?,?,?)',(sid,position+i,json.dumps(row)))
                if not row.get('unknown'):
                    added.append(inserted.lastrowid)
            if (next_offset is None and not note and not stopped and not db.execute("""SELECT 1 FROM dj_set_items
                    WHERE set_id=? AND json_extract(detail,'$.unknown')=0 LIMIT 1""",(sid,)).fetchone()):
                warning = 'The audio was scanned successfully, but no recordings were identified. The unidentified range is kept for review.'
            db.execute('UPDATE dj_sets SET warning=? WHERE id=?', (warning,sid))
        db.execute("""INSERT INTO dj_set_scan VALUES (?,?,?,?,?)
            ON CONFLICT(set_id) DO UPDATE SET offset=excluded.offset,duration=excluded.duration,
                path=excluded.path,retry_at=excluded.retry_at""",
                   (sid,next_offset,duration,state['path'],time.time()+cooldown))
        if 'plan' in result:
            store_scan_plan(db,sid,result['plan'])
        saved_report = report(db,sid)
    if added:
        set_discovery.schedule(api,sid,added)
    return saved_report


def same_identity(item, identity):
    if item['artist']:
        return key(item['artist'], item['title']) == identity
    # A row published without an artist is confirmed by its title and version.
    return bool(item['title']) and key('', item['title'])[1:] == identity[1:]


def merge_recognition(db, sid, hits):
    """Merge recognized hits into a published list. The caller holds the transaction.

    Existing rows are never deleted or moved (except by ``reorder_rows`` when the
    list is still in time order), and their track, download and playlist links are
    never touched. Returns (confirmed, inserted, entry ids to search sources for).
    """
    rows = db.execute('SELECT rowid AS entry_id,position,detail,track_id,download_id FROM dj_set_items WHERE set_id=? ORDER BY position',(sid,)).fetchall()
    entries = [{'entry_id':row['entry_id'],'position':row['position'],'item':json.loads(row['detail']),
                'linked':bool(row['track_id'] or row['download_id']),'changed':False} for row in rows]
    listed = sorted((entry for entry in entries if published(entry['item'])), key=lambda entry: entry['item']['at'])
    # A listed track lasts until the next listed start, by time rather than by position.
    for index, entry in enumerate(listed):
        item = entry['item']
        entry['end'] = listed[index+1]['item']['at'] if index+1 < len(listed) else item['at']+(number(item.get('duration')) or LAST_ROW_SPAN)+NEAR_ROW
    added_rows = [entry for entry in entries if entry['item'].get('evidence') == ['Fingerprint']]
    consumed = {}
    for entry in entries:
        for rk in entry['item'].get('recognized_rk') or []:
            name, _, at = rk.rpartition('@')
            consumed.setdefault(name, []).append(float(at))

    def mark(entry, rk):
        entry['item'].setdefault('recognized_rk', []).append(rk)
        entry['changed'] = True

    def closest(group, at):
        return min(group, key=lambda entry: abs(entry['item']['at']-at))
    confirmed, new, touched = 0, [], []
    for rk, hit in sorted(hits, key=lambda pair: pair[1]['at']):
        name = rk.rpartition('@')[0]
        # The same recording near the same time was merged by an earlier pass.
        if any(abs(at-hit['at']) <= NEAR_ROW for at in consumed.get(name, [])):
            continue
        consumed.setdefault(name, []).append(hit['at'])
        identity = key(hit['artist'], hit['title'])
        end = hit['at']+(hit.get('played') or 0)
        near = [entry for entry in listed if not entry['item'].get('unknown') and (abs(entry['item']['at']-hit['at']) <= NEAR_ROW
                or (entry['item']['at'] < end and hit['at'] < entry['end']))]
        same = [entry for entry in near if same_identity(entry['item'], identity)]
        if same:
            entry = closest(same, hit['at'])
            item = entry['item']
            if 'Fingerprint' not in item['evidence']:
                item['evidence'].append('Fingerprint')
            item['recognized'] = {'at':hit['at'], 'played':hit.get('played')}
            for field in ('duration','isrc','links'):
                if not item.get(field) and hit.get(field):
                    item[field] = hit[field]
            mark(entry, rk)
            confirmed += 1
            continue
        version = [entry for entry in near if item_key(entry['item'])[:2] == identity[:2]]
        if version:
            entry = closest(version, hit['at'])
            entry['item']['version_warning'] = 'Fingerprint matched a different version.'
            mark(entry, rk)
            continue
        container = next((entry for entry in reversed(listed) if entry['item']['at'] <= hit['at']+20 and hit['at'] < entry['end']), None)
        if container:
            item = container['item']
            if item.get('unknown') and not container['linked']:
                item.update(title=hit['title'], artist=hit['artist'], unknown=False, needs_review=True,
                            recognized={'at':hit['at'], 'played':hit.get('played')})
                item['evidence'] = [*item.get('evidence', []), 'Fingerprint']
                if not item.get('links') and hit.get('links'):
                    item['links'] = hit['links']
                touched.append(container['entry_id'])
            else:
                others = item.setdefault('recognized_other', [])
                if not any(key(other['artist'], other['title']) == identity for other in others):
                    others.append({'at':hit['at'], 'title':hit['title'], 'artist':hit['artist']})
            mark(container, rk)
            continue
        # Outside every listed track: a gap of at least 20 s, or after the last one.
        previous = next((entry for entry in reversed(added_rows) if item_key(entry['item']) == identity
                         and 0 <= hit['at']-entry['item']['at'] <= (entry['item'].get('played') or 0)+15), None)
        if previous:
            item = previous['item']
            item['played'] = max(item['at']+(item.get('played') or 0), end)-item['at']
            item['recognized'] = {'at':item['at'], 'played':item['played']}
            mark(previous, rk)
            continue
        row = dict(hit, needs_review=True, recognized={'at':hit['at'], 'played':hit.get('played')}, recognized_rk=[rk])
        entry = {'entry_id':None, 'position':None, 'item':row, 'linked':False, 'changed':True}
        new.append(entry)
        added_rows.append(entry)
    for entry in entries:
        if entry['changed']:
            db.execute('UPDATE dj_set_items SET detail=? WHERE set_id=? AND rowid=?', (json.dumps(entry['item']), sid, entry['entry_id']))
    if new:
        times = [entry['item'].get('at') for entry in entries]
        chronological = all(number(at) is not None for at in times) and times == sorted(times)
        position = max((entry['position'] for entry in entries), default=-1)+1
        for entry in new:
            entry['position'] = position
            entry['entry_id'] = db.execute('INSERT INTO dj_set_items(set_id,position,detail) VALUES (?,?,?)',
                                           (sid, position, json.dumps(entry['item']))).lastrowid
            touched.append(entry['entry_id'])
            position += 1
        if chronological:
            # The owner has not reordered this list: keep it in time order.
            ordered = sorted(entries+new, key=lambda entry: (entry['item']['at'], entry['position']))
            reorder_rows(db, sid, [str(entry['entry_id']) for entry in ordered])
    return confirmed, len(new), touched


def item_key(item):
    return key(item.get('artist') or '', item.get('title') or '')


def queue_auto_scan(db, sid, watch):
    if watch:
        db.execute("""UPDATE dj_set_auto_scan SET enabled=0 WHERE set_id IN
            (SELECT id FROM dj_sets WHERE watch=? AND id!=?)""",(watch,sid))
    db.execute("""INSERT INTO dj_set_auto_scan(set_id,enabled) VALUES (?,1)
        ON CONFLICT(set_id) DO UPDATE SET enabled=1,failures=0""",(sid,))


def set_youtube_access(db, sid, browser):
    if browser not in (None,*youtube_audio.BROWSERS):
        raise ValueError('Choose a supported YouTube browser session.')
    db.execute('DELETE FROM dj_set_youtube_access WHERE set_id=?',(sid,))
    if browser:
        db.execute('INSERT INTO dj_set_youtube_access VALUES (?,?)',(sid,browser))


def set_auto_scan(db, sid, enabled, access=False):
    db.execute('BEGIN IMMEDIATE')
    saved = get_set(db,sid)
    if access is not False:
        # The owner can change which session a paused scan resumes with.
        set_youtube_access(db,sid,access)
    state = scan_checkpoint(db,saved)
    if not state or state['offset'] is None:
        raise ValueError('This report has no unfinished audio scan.')
    if enabled:
        db.execute('INSERT OR IGNORE INTO dj_set_scan VALUES (?,?,?,?,?)',
                   (sid,state['offset'],state['duration'],state['path'],state['retry_at']))
        release_backoff(db,sid)
        queue_auto_scan(db,sid,saved['watch'])
    else:
        db.execute('UPDATE dj_set_auto_scan SET enabled=0 WHERE set_id=?',(sid,))
    db.commit()
    AUTO_WAKE.set()
    return report(db,sid)


def cancel_scan(api, db, sid):
    db.execute('BEGIN IMMEDIATE')
    get_set(db,sid)
    db.execute('INSERT INTO dj_set_auto_scan(set_id,enabled) SELECT set_id,0 FROM dj_set_scan WHERE set_id=? ON CONFLICT(set_id) DO UPDATE SET enabled=0',(sid,))
    db.commit()
    control = set_scan_jobs.for_report(api,sid)
    if control and not control.stop.is_set():
        control.cancel()
    stop_prefetch(api.ROOT,sid)
    AUTO_WAKE.set()
    result = report(db,sid)
    if result.get('scan',{}).get('running'):
        result['scan']['cancelling']=True
    return result


def auto_scan_step(api):
    global AUTO_RUNNING
    if shazam_scan.saved_until(api.ROOT) > time.time():
        # Shazam's saved cooldown holds every report; the report shows it as the wait.
        return False
    with api.connect_existing() as db:
        job = db.execute("""SELECT a.set_id,a.failures,c.offset,
            coalesce((SELECT count(*) FROM json_each(w.plan,'$.samples')),0) AS samples
            FROM dj_set_auto_scan a
            JOIN dj_set_scan c ON c.set_id=a.set_id
            LEFT JOIN dj_set_scan_work w ON w.set_id=a.set_id
            WHERE a.enabled=1 AND c.offset IS NOT NULL AND c.retry_at<=?
            ORDER BY c.retry_at,a.rowid LIMIT 1""",(time.time(),)).fetchone()
    if not job or AUTO_STOP.is_set() or set_scan_jobs.for_report(api,job['set_id']):
        return False
    sid = job['set_id']
    AUTO_RUNNING = sid
    error = None
    samples = job['samples']
    failure = None
    control = set_scan_jobs.ScanControl(api, sid)
    try:
        with control.activate():
            with api.connect_existing() as db:
                enabled = db.execute('SELECT enabled FROM dj_set_auto_scan WHERE set_id=?',(sid,)).fetchone()
            if not enabled or not enabled['enabled']:
                return False
            control.check()
            result = resume_scan(api,sid)
        offset = result['scan']['offset']
        samples = result['scan']['samples']
        failure = control.failure
    except shazam_scan.ScanBusy:
        return False
    except ImportCancelled:
        return False
    except (ValueError, OSError, subprocess.SubprocessError) as exc:
        error = str(exc)
        offset = job['offset']
    except Exception:
        # A malformed worker reply counts as a stalled attempt. It must not
        # end the continuation thread for every other saved set.
        error = 'The audio scan stopped unexpectedly.'
        offset = job['offset']
    finally:
        AUTO_RUNNING = None
    if control.stop.is_set():
        return True
    if AUTO_STOP.is_set():
        return False
    code = failure.get('code') if failure else None
    # Shazam's own cooldown is waited out by resume_scan; it is not a stalled scan.
    progressed = offset is None or offset > job['offset'] or samples > job['samples'] or code == 'rate_limited'
    failures = 0 if progressed else job['failures']+1
    if code == 'tls':
        # A broken secure connection is not retried automatically.
        failures = MAX_STALLED_ATTEMPTS
    with api.connect_existing() as db:
        updated = db.execute("""UPDATE dj_set_auto_scan SET failures=?,enabled=?
            WHERE set_id=? AND enabled=1""",(failures,int(offset is not None and failures<MAX_STALLED_ATTEMPTS),sid))
        if updated.rowcount and not progressed and code != 'tls':
            delay = shazam_scan.RATE_LIMIT_COOLDOWN * 2**(failures-1)
            db.execute('UPDATE dj_set_scan SET retry_at=max(retry_at,?) WHERE set_id=?',(time.time()+delay,sid))
            if error:
                if db.execute('SELECT 1 FROM dj_set_recognition WHERE set_id=?',(sid,)).fetchone():
                    db.execute('UPDATE dj_set_recognition SET warning=? WHERE set_id=?',(error,sid))
                else:
                    db.execute('UPDATE dj_sets SET warning=? WHERE id=?',('Partial scan. '+error,sid))
    return True


def start_auto_scans(api):
    try:
        # Source searches left waiting when CrateLoom closed continue without Shazam.
        set_discovery.sweep(api)
    except sqlite3.Error:
        pass
    if not shazam_scan.interpreter():
        return
    with api.connect() as db:
        # Adopt the most recent unfinished scan when upgrading an existing library.
        if not db.execute('SELECT 1 FROM dj_set_auto_scan LIMIT 1').fetchone():
            latest = db.execute("""SELECT s.id,s.watch FROM dj_sets s JOIN dj_set_scan c ON c.set_id=s.id
                WHERE c.offset IS NOT NULL ORDER BY s.rowid DESC LIMIT 1""").fetchone()
            if latest:
                queue_auto_scan(db,latest['id'],latest['watch'])

    def work():
        while not AUTO_STOP.is_set():
            try:
                if auto_scan_step(api):
                    continue
            except sqlite3.Error:
                pass
            AUTO_WAKE.wait(3)
            AUTO_WAKE.clear()

    threading.Thread(target=work, name='set-auto-scan', daemon=True).start()


def build_library_index(db):
    library = {}
    for track in db.execute('SELECT id,title,artist,path,duration FROM tracks'):
        library.setdefault(key(track['artist'], track['title']), []).append(track)
    return library


def library_index(db):
    """The library by match key for display, reused for a few seconds while no track is added or removed.

    A credit edit is not seen until the cache expires, so anything that links a
    track to an entry builds a fresh index instead.
    """
    database = db.execute('PRAGMA database_list').fetchone()[2]
    stamp = (database, *db.execute('SELECT count(*),max(rowid) FROM tracks').fetchone())
    now = time.monotonic()
    with INDEX_LOCK:
        cached = INDEX_CACHE.get(database)
        if cached and cached[0] == stamp and now-cached[1] < INDEX_TTL:
            return cached[2]
    library = build_library_index(db)
    with INDEX_LOCK:
        INDEX_CACHE[database] = (stamp, now, library)
        while len(INDEX_CACHE) > 8:
            INDEX_CACHE.pop(next(iter(INDEX_CACHE)))
    return library


def matching_track(library, item):
    duration = item.get('duration')
    matches = {
        track['id'] for artist, title in credit_options(item)
        for track in library.get(key(artist, title), [])
        if (not duration or abs(track['duration'] - duration) <= max(5, duration * .035))
        and Path(track['path']).is_file()
    }
    return next(iter(matches)) if len(matches) == 1 else None


def report(db, sid):
    result = get_set(db, sid)
    state = scan_checkpoint(db,result)
    access = db.execute('SELECT browser FROM dj_set_youtube_access WHERE set_id=?',(sid,)).fetchone()
    result['youtube_browser'] = access['browser'] if access else None
    if state:
        result['scan'] = {field:state[field] for field in ('offset','duration','retry_at')}
        auto = db.execute('SELECT enabled,failures FROM dj_set_auto_scan WHERE set_id=?',(sid,)).fetchone()
        result['scan'].update(automatic=bool(auto and auto['enabled']), running=AUTO_RUNNING==sid,
                              stalled=bool(auto and auto['failures']>=MAX_STALLED_ATTEMPTS),
                              fast='plan' in state,samples=len(state.get('plan',{}).get('samples',{})),
                              sampled_until=sampled_until(state))
        control = set_scan_jobs.for_database(db,sid)
        if control:
            result['scan'].update(running=True,cancelling=control.stop.is_set(),progress=dict(control.progress))
        elif result['scan']['automatic'] and state['offset'] is not None:
            # Shazam's saved cooldown holds every report, also after this report's own retry time.
            limited = shazam_cooldown(db)
            resume_at = max(state['retry_at'], limited)
            if resume_at > time.time():
                # Only Shazam's own cooldown is a wait for Shazam; after an attempt that made no
                # progress (YouTube, the network, a decode error) the backoff is a plain wait.
                stage = 'Waiting before the next attempt' if auto['failures'] and state['retry_at'] > limited else 'Waiting for Shazam'
                result['scan']['retry_at'] = resume_at
                result['scan']['progress'] = waiting_progress(state, stage, resume_at)
        result['scan']['pending_matches']=sum(bool(match) for match in state.get('plan',{}).get('samples',{}).values())
        result['provisional'] = provisional(state, control)
    else:
        result['provisional'] = []
    result['recognition'] = recognition_status(db, sid, result.get('scan'))
    result['discovery'] = set_discovery.status(db, sid)
    _, searching = set_discovery.active(db, sid)
    rows = db.execute("""SELECT i.*,i.rowid AS entry_id,a.state AS download_state,a.error AS download_error,t.path AS track_path,
        t.duration AS track_duration,t.metadata AS track_metadata,
        a.downloaded_bytes,a.total_bytes
        FROM dj_set_items i LEFT JOIN acquisitions a ON a.id=i.download_id
        LEFT JOIN tracks t ON t.id=i.track_id
        WHERE i.set_id=? ORDER BY i.position""", (sid,))
    library = None
    result['tracks'] = []
    for row in rows:
        item = normalize_credit(json.loads(row['detail']))
        # The links stay internal; the flag says "Create playlist" acts on the entry without an artist.
        item['listed_link'] = bool(item.pop('listed_links', None))
        item.pop('recognized_rk', None)
        found = item.pop('discovery', None)
        search_state = found.get('state') if isinstance(found, dict) and found.get('state') in set_discovery.STATES else None
        if search_state == 'searching' and row['entry_id'] not in searching:
            search_state = 'queued'
        item['source_search'] = search_state
        playback_id = None
        if row['track_path'] and Path(row['track_path']).is_file():
            playback_id = row['track_id']
        elif not row['track_id'] and item['artist'] and not item.get('unknown') and not item.get('needs_review'):
            if library is None:
                library = library_index(db)
            playback_id = matching_track(library, item)
        if row['track_duration']:
            item['duration'] = row['track_duration']
        metadata = json.loads(row['track_metadata']) if row['track_metadata'] else {}
        item['artwork_path'] = metadata.get('artwork_path') or item.get('artwork_path')
        if playback_id and not row['track_id']:
            local = db.execute('SELECT duration,metadata FROM tracks WHERE id=?', (playback_id,)).fetchone()
            item['duration'] = local['duration']
            item['artwork_path'] = (json.loads(local['metadata']) if local['metadata'] else {}).get('artwork_path') or item['artwork_path']
        best = recommend_sources(item)
        if not item.get('duration') and best and best.get('duration'):
            item['duration'] = best['duration']
        item.update(entry_id=str(row['entry_id']), position=row['position'], track_id=row['track_id'], download_id=row['download_id'],
                    playback_track_id=playback_id, download_state=row['download_state'], error=row['error'] or row['download_error'],
                    downloaded_bytes=row['downloaded_bytes'], total_bytes=row['total_bytes'])
        result['tracks'].append(item)
    return result


def sampled_times(state):
    plan = state.get('plan')
    samples = plan.get('samples') if isinstance(plan, dict) else None
    if not isinstance(samples, dict):
        return None
    taken = []
    for at in samples:
        try:
            taken.append(float(at))
        except (TypeError, ValueError):
            continue
    return taken


def sampled_until(state):
    """How far a sampled pass has listened. Its offset stays at the batch start until the batch ends."""
    taken = sampled_times(state)
    if taken is None:
        return None
    return max(taken, default=state['offset'] or 0)


def waiting_progress(state, stage, resume_at):
    """Progress for a scan that waits to continue, in the shape its worker reports while running.

    A sampled (fast) pass keeps its offset at the start of the batch until the batch ends, so its
    position is the furthest sample taken, not the offset; the offset alone would read 0:00.
    """
    taken = sampled_times(state)
    if taken is None:
        return {'stage':stage,'completed':state['offset'],'total':state['duration'],'unit':'seconds','resume_at':resume_at}
    samples = state['plan']['samples']
    found = {match.get('key') for match in samples.values() if isinstance(match, dict) and match.get('key')}
    return {'stage':stage,'completed':len(taken),'total':None,'unit':'samples','position':max(taken, default=state['offset'] or 0),
            'duration':state['duration'],'tracks_found':len(found),'resume_at':resume_at}


def provisional(state, control):
    """Hits heard so far in the running pass, from its offset on; they are not rows and carry no actions."""
    if state['offset'] is None:
        return []
    plan = control.partial.get('plan') if control and isinstance(control.partial, dict) else None
    if not isinstance(plan, dict):
        plan = state.get('plan')
    samples = plan.get('samples') if isinstance(plan, dict) else None
    if not isinstance(samples, dict):
        return []
    try:
        heard = {float(at):match for at, match in samples.items() if float(at) >= state['offset']}
    except (TypeError, ValueError):
        return []
    if not heard:
        return []
    from .shazam_fast import timeline
    from .shazam_worker import WINDOW
    try:
        hits = timeline(state['offset'], state['duration'] or max(heard)+WINDOW, heard)
    except (TypeError, KeyError, AttributeError):
        return []
    return [{'at':hit['start'], 'end':hit['end'], 'title':hit['title'], 'artist':hit['artist'], 'samples':hit['samples']}
            for hit in hits if isinstance(hit.get('title'), str) and isinstance(hit.get('artist'), str)]


def recognition_status(db, sid, scan):
    row = db.execute('SELECT completed_at,warning,confirmed,added FROM dj_set_recognition WHERE set_id=?', (sid,)).fetchone()
    if not row:
        return {'state':'none', 'confirmed':0, 'added':0, 'warning':None}
    if row['completed_at'] or not scan or scan['offset'] is None:
        name = 'complete'
    elif scan.get('running'):
        name = 'running'
    elif scan.get('stalled'):
        name = 'failed'
    else:
        name = 'running' if scan.get('automatic') else 'paused'
    return {'state':name, 'confirmed':row['confirmed'], 'added':row['added'], 'warning':row['warning']}


def attach_track(db, sid, position, tid, *, cue_pending=False, api=None):
    playlist = get_set(db, sid)['playlist_id']
    db.execute('UPDATE dj_set_items SET track_id=?,error=NULL,cue_pending=? WHERE set_id=? AND position=?',
               (tid, cue_pending, sid, position))
    db.execute("""INSERT INTO playlist_tracks(playlist_id,track_id,position) VALUES (?,?,?)
        ON CONFLICT(playlist_id,track_id) DO UPDATE SET position=min(position,excluded.position)""",
               (playlist, tid, position))
    item = json.loads(db.execute('SELECT detail FROM dj_set_items WHERE set_id=? AND position=?', (sid, position)).fetchone()[0])
    if item.get('artwork_path') and Path(item['artwork_path']).is_file():
        if api is None:
            # Engine callers pass the running module; under ``python -m`` this import would load a second copy.
            from . import __main__ as api
        from .set_artwork import apply
        apply(api, db, tid, item['artwork_path'])


def attach_download(db, download_id, track_id, duplicate, *, api=None):
    row = db.execute('SELECT set_id,position FROM dj_set_items WHERE download_id=?', (download_id,)).fetchone()
    if row:
        attach_track(db, row['set_id'], row['position'], track_id, cue_pending=not duplicate, api=api)


def add_local(api, sid, entry_id, path):
    with api.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        if not get_set(db, sid)['playlist_id']:
            raise ValueError('Create the set playlist before adding a file.')
        slot = db.execute('SELECT position FROM dj_set_items WHERE set_id=? AND rowid=?', (sid, entry_id)).fetchone()
        if not slot:
            raise ValueError('Set entry not found.')
        position = slot['position']
        row = source_slot(db, sid, position)
        if not can_set_source(row):
            raise ValueError('This set entry is already linked or unavailable.')
        track, duplicate = api.import_track(db, path)
        db.execute('UPDATE dj_set_items SET download_id=NULL WHERE set_id=? AND position=?', (sid, position))
        attach_track(db, sid, position, track['id'], cue_pending=not duplicate, api=api)
        return report(db, sid)


def source_slot(db, sid, position):
    return db.execute("""SELECT i.*, a.state AS download_state FROM dj_set_items i
        LEFT JOIN acquisitions a ON a.id=i.download_id
        WHERE i.set_id=? AND i.position=?""", (sid, position)).fetchone()


def can_set_source(row):
    return row is not None and not row['track_id'] and (
        not row['download_id'] or row['download_state'] in acquisitions.TERMINAL_RETRY
    )


def reorder(db, sid, ids):
    db.execute('BEGIN IMMEDIATE')
    reorder_rows(db, sid, ids)
    return report(db, sid)


def reorder_rows(db, sid, ids):
    """Apply a new entry order and the matching playlist order. The caller holds the transaction."""
    saved = get_set(db, sid)
    rows = db.execute('SELECT rowid AS entry_id,track_id FROM dj_set_items WHERE set_id=? ORDER BY position', (sid,)).fetchall()
    existing = {str(row['entry_id']): row for row in rows}
    if not isinstance(ids, list) or len(ids) != len(existing) or len(set(ids)) != len(ids) or set(ids) != set(existing):
        raise ValueError('The set changed. Refresh it before reordering.')
    db.execute('UPDATE dj_set_items SET position=-position-1 WHERE set_id=?', (sid,))
    for position, entry_id in enumerate(ids):
        db.execute('UPDATE dj_set_items SET position=? WHERE set_id=? AND rowid=?', (position, sid, existing[entry_id]['entry_id']))
    if saved['playlist_id']:
        ordered = list(dict.fromkeys(existing[entry_id]['track_id'] for entry_id in ids if existing[entry_id]['track_id']))
        extra = [row[0] for row in db.execute('SELECT track_id FROM playlist_tracks WHERE playlist_id=? ORDER BY position', (saved['playlist_id'],)) if row[0] not in ordered]
        seen = set()
        for position, entry_id in enumerate(ids):
            tid = existing[entry_id]['track_id']
            if not tid or tid in seen:
                continue
            seen.add(tid)
            db.execute('UPDATE playlist_tracks SET position=? WHERE playlist_id=? AND track_id=?', (position, saved['playlist_id'], tid))
        for position, tid in enumerate(extra, len(ids)):
            db.execute('UPDATE playlist_tracks SET position=? WHERE playlist_id=? AND track_id=?', (position, saved['playlist_id'], tid))


def reference_for(api, item, identity):
    isrc = item.get('isrc')
    if not isinstance(isrc, str) or not re.fullmatch(r'[A-Z]{2}[A-Z0-9]{3}\d{7}', isrc):
        isrc = None
    found = providers.recording_search(api, identity, item.get('duration'), isrc)
    exact = {}
    for candidate in found['candidates']:
        values = candidate['values']
        if key(values['artist'], values['title']) == key(item['artist'], item['title']):
            exact.setdefault(candidate['recording_id'], candidate)
    return next(iter(exact.values())) if len(exact) == 1 else None


def add_link(item, label, url):
    links = item.setdefault('links', [])
    if not any(link.get('url') == url for link in links):
        links.append({'label': label, 'url': url})


def catalog_sources(item, catalog_links):
    """Add a recording's catalog links to the entry; returns the creator free-download URLs among them."""
    free_links = []
    for type_id, url in catalog_links:
        try:
            url = download_http.public_url(url)
            platform = service_links.identify(url)
        except ValueError:
            continue
        if type_id == FREE_DOWNLOAD:
            free_links.append(url)
        if platform:
            add_link(item, platform[0], platform[1])
            if platform[0] == 'SoundCloud' and soundcloud_download.configured():
                free_links.append(platform[1])
        elif type_id == FREE_DOWNLOAD:
            add_link(item, 'Creator download', download_http.display_url(url))
    return free_links


def listed_service(item):
    """Whether the published entry links its own YouTube or SoundCloud page."""
    for url in item.get('listed_links') or []:
        try:
            platform = service_links.identify(download_http.public_url(url))
        except ValueError:
            continue
        if platform and (platform[0] in youtube_audio.NAMES or platform[0] == 'SoundCloud'):
            return True
    return False


def direct_source(api, item, urls, watch):
    for url in dict.fromkeys(urls):
        try:
            platform = service_links.identify(download_http.public_url(url))
            if platform and platform[2] == watch:
                continue
            if platform and platform[0] == 'SoundCloud':
                return soundcloud_source(api, platform[1])
            # Only the official free-file route or an inline creator file is eligible automatically.
            url = download_http.public_url(url)
            if platform and platform[0] in youtube_audio.NAMES and url in item['listed_links']:
                resolved, _, _ = youtube_audio.resolve(api, platform)
                return providers.register(api, resolved)
            if platform:
                continue
            info = download_http.inspect(url)
            return providers.register(api, dict(info, kind='direct', provider='Direct HTTPS', can_acquire=True))
        except (ValueError, OSError):
            pass
    return None


def unsourced(item, lookup_error):
    if item.get('source_options'):
        if all(option.get('reference_only') for option in item['source_options']):
            return None, 'Track page found. Check it for a download, then add the file.'
        return None, 'Review the suggested versions below.'
    return None, lookup_error or 'No matching audio source found. Add a track link or local file.'


def sourced(api, item, source, reference):
    artwork = item.get('artwork_path')
    if reference and reference.get('release_id'):
        try:
            artwork = providers.cover(api, reference['id'])['path']
        except ValueError:
            pass
    if source.get('duration') and item.get('duration') and abs(source['duration'] - item['duration']) > max(5, item['duration'] * .04):
        reference = None
    if reference is None and item.get('duration') and not item.get('needs_review') and not source.get('duration'):
        reference = providers.register(api, {'kind': 'reference', 'duration': item['duration']})
    fields = dict(reference.get('values', {}) if reference else {}, title=item['title'], artist=item['artist'])
    return {'source_id': source['id'], 'reference_id': reference['id'] if reference else None,
            'values': fields, 'artwork_path': artwork}, None


def discovered_source(api, item, watch, discovered):
    """Use what discovery already found: no catalog lookup and no search.

    Returns None when every found source failed to resolve, so the caller searches again.
    """
    reference = None
    if isinstance(discovered.get('reference'), dict):
        reference = providers.register(api, dict(discovered['reference'], kind='reference'))
    urls = [*item['listed_links'], *discovered.get('free_links', [])] if not item.get('needs_review') else []
    source = direct_source(api, item, urls, watch)
    error, attempted = None, False
    if source is None:
        found = [match for match in discovered.get('matches') or [] if isinstance(match, list) and len(match) == 4 and match[0] not in item['listed_links']]
        videos = [(score or 0, url, artist, song) for url, artist, song, score in found if 'soundcloud.com/' not in url]
        clouds = [(url, artist, song) for url, artist, song, _ in found if 'soundcloud.com/' in url]
        recommended = recommend_sources(item)
        review = item.get('needs_review') and sum(source_priority(item, option) is not None for option in item.get('source_options', [])) != 1
        attempted = bool(recommended or videos) and not review
        source, error = choose_video_source(api, item, videos)
        if source is None and clouds and soundcloud_download.configured() and not item.get('needs_review'):
            attempted = True
            url, artist, song = clouds[0]
            try:
                source = soundcloud_source(api, url)
                item.update(artist=artist, title=song, credit_confirmed=True)
            except ValueError as failure:
                error = str(failure)
    if source is None:
        return None if attempted else unsourced(item, error)
    return sourced(api, item, source, reference)


def source_for(api, item, explicit_url, watch=None, discovered=None):
    if explicit_url:
        platform = service_links.identify(explicit_url)
        if platform and platform[2] == watch:
            raise ValueError('Choose the individual track link instead of the full DJ set.')
        if platform and platform[0] == 'SoundCloud':
            source = soundcloud_source(api, platform[1])
        else:
            resolved = providers.resolve(api, explicit_url)
            source = next((candidate for candidate in resolved['candidates'] if candidate['kind'] == 'direct'), None)
            if source is None:
                raise ValueError('This source has no downloadable audio. Add a direct track link or local file.')
        return {'source_id': source['id'], 'values': {'title': item['title'], 'artist': item['artist']},
                'artwork_path': item.get('artwork_path')}, None
    if item.get('links'):
        # Earlier prepares may have stored the same link more than once.
        unique = {}
        for link in item['links']:
            unique.setdefault(link.get('url'), link)
        item['links'] = list(unique.values())
    if item['at'] is None and item['listed_links']:
        page = service_links.identify(item['listed_links'][0])
        if page and page[0] in youtube_audio.NAMES:
            try:
                source, _, _ = youtube_audio.resolve(api, page)
            except (ValueError, OSError):
                pass
            else:
                # A playlist often links the music video; its intro or outro does not belong in a DJ library.
                source = audio_version(api, item, source, page) or source
                source = providers.register(api, source)
                values = {'title': item['title'], 'artist': item['artist'] or source['values']['artist']}
                return {'source_id': source['id'], 'values': values, 'artwork_path': item.get('artwork_path')}, None
    if discovered:
        found = discovered_source(api, item, watch, discovered)
        if found is not None:
            return found
    identity = search_identity(item['title'], item['artist'])
    reference, catalog_links, lookup_error = None, [], None
    # The entry's own YouTube or SoundCloud link is a better source than any catalog lookup.
    if identity['artist'] and not item.get('needs_review') and not listed_service(item):
        try:
            reference = reference_for(api, item, identity)
            if reference and re.fullmatch(r'[0-9a-f-]{36}', reference['recording_id'] or ''):
                data = providers.musicbrainz('recording/' + reference['recording_id'], {'inc': 'url-rels', 'fmt': 'json'})
                catalog_links = [(rel.get('type-id'), (rel.get('url') or {}).get('resource'))
                                 for rel in data.get('relations', []) if not rel.get('ended')]
        except ValueError as error:
            lookup_error = str(error)
    if reference:
        add_link(item, 'MusicBrainz', reference['source_url'])
        # Without a listed length the catalog's lets the search tell a music video from the song.
        if not item.get('duration') and isinstance(reference.get('duration'), (int, float)) and reference['duration'] > 0:
            item['catalog_duration'] = reference['duration']
    free_links = catalog_sources(item, catalog_links)
    urls = [*item['listed_links'], *free_links] if not item.get('needs_review') else []
    if not item.get('needs_review') and soundcloud_download.configured() and not any('soundcloud.com/' in url for url in urls):
        try:
            found = soundcloud_download.find(item['artist'], item['title'], key, item.get('duration'))
            if found:
                urls.append(found)
                add_link(item, 'SoundCloud', found)
        except ValueError as error:
            lookup_error = str(error)
    source = direct_source(api, item, urls, watch)
    if source is None:
        try:
            source, video_error = find_video_source(api, item, watch)
            if video_error:
                lookup_error = video_error
        except ValueError as error:
            lookup_error = str(error)
    if source is None:
        try:
            source = find_soundcloud_source(api, item)
        except ValueError as error:
            lookup_error = lookup_error or str(error)
    if source is None:
        return unsourced(item, lookup_error)
    return sourced(api, item, source, reference)


def merged_fields(item):
    """The fields a source lookup may change; everything else in the stored entry is kept."""
    return {name: item[name] for name in ('artist', 'title', 'credit_confirmed', 'links', 'source_options') if name in item}


def merged_update(item, credit):
    """A merge_detail callable: the lookup's fields applied to the latest entry without undoing writes made meanwhile.

    Links are a union, and the credit changes only while the entry still has the
    ``credit`` (artist, title) the lookup started from.
    """
    def update(latest):
        fields = merged_fields(item)
        current = normalize_credit(dict(latest))
        if (current.get('artist') or '', current.get('title') or '') != credit:
            for name in ('artist', 'title', 'credit_confirmed'):
                fields.pop(name, None)
        links = [dict(link) for link in fields.get('links') or []]
        for link in latest.get('links') or []:
            if isinstance(link, dict) and link.get('url') and not any(kept.get('url') == link['url'] for kept in links):
                links.append(dict(link))
        if links or 'links' in fields:
            fields['links'] = links
        return fields
    return update


def prepare_set(api, sid, name, override=None):
    manager = acquisitions.manager(api)
    with api.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        saved = get_set(db, sid)
        if override and not saved['playlist_id']:
            raise ValueError('Create the set playlist before adding a file.')
        if not saved['playlist_id']:
            playlist = preparation.dispatch(api, db, 'playlists.save', {'name': name or saved['name']})
            db.execute('UPDATE dj_sets SET playlist_id=? WHERE id=?', (playlist['id'], sid))
        rows = db.execute('SELECT *,rowid AS entry_id FROM dj_set_items WHERE set_id=? ORDER BY position', (sid,)).fetchall()
        # Attaching needs the current credits: an edit keeps the count and rowids, so the report's cache cannot see it.
        # A chosen source (override) never matches the library, so it skips the index.
        library = None if override and override[1] else build_library_index(db)
    # Discovery stops once the playlist exists (a failed attempt leaves it running); the lookups below reuse what it found.
    set_discovery.cancel(api, sid)
    lookups = []
    titled = []
    explicit = override[1] if override else None
    for row in rows:
        pos, entry_id = row['position'], row['entry_id']
        if row['track_id'] or (override and override[0] != entry_id) or (not override and row['download_id']):
            continue
        item = normalize_credit(json.loads(row['detail']))
        if not explicit and (item.get('unknown') or (not item['artist'] and not item['listed_links'])):
            # A title-only entry discovery had not finished is searched now; its versions are offered, never chosen.
            if (not override and not item.get('unknown') and set_discovery.title_only(item)
                    and not set_discovery.fresh(item, ('choose', 'none'))):
                titled.append((entry_id, item))
            continue
        match = matching_track(library, item) if not explicit and not item.get('needs_review') else None
        if match:
            with api.connect_existing() as db:
                db.execute('BEGIN IMMEDIATE')
                current = db.execute('SELECT position,track_id,download_id FROM dj_set_items WHERE set_id=? AND rowid=?', (sid, entry_id)).fetchone()
                if current and not current['track_id'] and not current['download_id']:
                    attach_track(db, sid, current['position'], match, api=api)
            continue
        # A row searched without a result is not searched again once the catalog was asked as well.
        searched = set_discovery.fresh(item, ('ready', 'choose')) or (set_discovery.fresh(item, ('none',)) and item['discovery'].get('catalog'))
        discovered = item.get('discovery') if not explicit and searched else None
        lookups.append((entry_id,item,discovered,(item.get('artist') or '', item.get('title') or '')))
    # Entries with a found source resolve first, so their downloads start before slower catalog lookups.
    lookups.sort(key=lambda lookup: 0 if lookup[2] and lookup[2]['state'] == 'ready' else 1 if lookup[2] else 2)
    with ThreadPoolExecutor(max_workers=4, thread_name_prefix='set-source') as pool:
        pending = {pool.submit(source_for, api, item, explicit, saved['watch'], discovered):(entry_id,item,credit) for entry_id,item,discovered,credit in lookups}
        # Queued after the lookups, so downloads start first; the pool waits for them before the report.
        for entry_id, item in titled:
            pool.submit(set_discovery.search_now, api, sid, entry_id, item, saved['watch'])
        for future in as_completed(pending):
            entry_id,item,credit = pending.pop(future)
            try:
                params, error = future.result()
                with api.connect_existing() as db:
                    db.execute('BEGIN IMMEDIATE')
                    slot = db.execute('SELECT position FROM dj_set_items WHERE set_id=? AND rowid=?', (sid, entry_id)).fetchone()
                    if not slot:
                        continue
                    current = source_slot(db, sid, slot['position'])
                    if not can_set_source(current) or (not explicit and current['download_id']):
                        continue
                    # Merge into the latest entry: artwork or discovery written meanwhile is kept.
                    latest = set_discovery.merge_detail(db, sid, entry_id, None, merged_update(item, credit), linked=True, transaction=False)
                    if params and latest and latest.get('artwork_path'):
                        params['artwork_path'] = latest['artwork_path']
                    jid = manager.enqueue_in(db, params, capacity=acquisitions.SET_CAPACITY) if params else None
                    db.execute('UPDATE dj_set_items SET download_id=?,error=? WHERE set_id=? AND position=?',
                               (jid or current['download_id'], error, sid, slot['position']))
                if jid:
                    manager.ensure_worker()
            except Exception as error:
                # One entry's lookup must not fail the whole preparation.
                if not isinstance(error, (ValueError, OSError)):
                    error = ValueError('This source could not be checked. Try again.')
                with api.connect_existing() as db:
                    db.execute('BEGIN IMMEDIATE')
                    slot = db.execute('SELECT position FROM dj_set_items WHERE set_id=? AND rowid=?', (sid, entry_id)).fetchone()
                    if not slot:
                        continue
                    current = source_slot(db, sid, slot['position'])
                    if can_set_source(current) and (explicit or not current['download_id']):
                        db.execute('UPDATE dj_set_items SET error=? WHERE set_id=? AND position=?',
                                   (str(error), sid, slot['position']))
    with api.connect() as db:
        return report(db, sid)


def apply_recommended(api, sid, choices):
    """Start every recommended source in one job, reusing one library index."""
    result = None
    for entry_id, url in choices:
        try:
            result = prepare_set(api, sid, None, (entry_id, url))
        except (ValueError, OSError):
            # The entry changed meanwhile; the others still start.
            continue
    if result is None:
        with api.connect_existing() as db:
            result = report(db, sid)
    return result


def recognize_audio(api, db, sid, p):
    """Queue an audio pass over a published list ("Recognize audio too")."""
    browser = p.get('youtube_browser') or None
    db.execute('BEGIN IMMEDIATE')
    try:
        saved = get_set(db, sid)
        try:
            reference = service_links.identify(saved['watch']) if saved['watch'] else None
        except ValueError:
            reference = None
        if not reference or reference[0] not in youtube_audio.NAMES or reference[2] != saved['watch']:
            raise ValueError('Audio recognition needs a single YouTube mix.')
        if not shazam_scan.interpreter():
            raise ValueError(INSTALL_RECOGNIZER)
        if set_scan_jobs.for_report(api, sid):
            raise ValueError('This audio scan is already running.')
        state = scan_checkpoint(db, saved)
        if state and state['offset'] is not None:
            raise ValueError('This report already has an unfinished audio scan.')
        done = db.execute('SELECT completed_at FROM dj_set_recognition WHERE set_id=?', (sid,)).fetchone()
        if done and done['completed_at']:
            raise ValueError('The mix audio was already recognized for this report.')
        if not any(published(json.loads(row[0])) for row in db.execute('SELECT detail FROM dj_set_items WHERE set_id=?', (sid,))):
            raise ValueError('This report already comes from the mix audio.')
        set_youtube_access(db, sid, browser)
        start_recognition(db, sid, saved['watch'])
        db.commit()
    except BaseException:
        db.rollback()
        raise
    AUTO_WAKE.set()
    return report(db, sid)


def dispatch(api, db, method, p):
    global SPOTIFY_TOKEN
    if method == 'sets.capabilities':
        return {'fingerprinting': shazam_scan.interpreter() is not None,
                'spotify_connected': bool(SPOTIFY_TOKEN or os.environ.get('CRATELOOM_SPOTIFY_TOKEN','').strip()),
                'soundcloud_connected': soundcloud_download.configured()}
    if method == 'sets.configure_spotify':
        token = p.get('token')
        if not isinstance(token, str) or (token and not re.fullmatch(r'[!-~]{1,4096}', token)):
            raise ValueError('Enter a valid Spotify access token.')
        SPOTIFY_TOKEN = token or None
        return {'connected': bool(SPOTIFY_TOKEN)}
    if method == 'sets.configure_soundcloud':
        soundcloud_download.configure(p.get('token'))
        return {'connected': soundcloud_download.configured()}
    if method == 'sets.list':
        return list_reports(db)
    if method == 'sets.delete':
        return delete_report(db,p.get('id'))
    if method == 'sets.pending_analysis':
        return [row[0] for row in db.execute("""SELECT DISTINCT i.track_id
            FROM dj_set_items i JOIN tracks t ON t.id=i.track_id
            WHERE i.cue_pending=1 AND t.duration<=900 ORDER BY i.rowid""")]
    if method == 'sets.get':
        result = report(db, p.get('id'))
        discovery = result['discovery']
        if (not result['playlist_id'] and not discovery['pending'] and not discovery['running']
                and any(row['source_search'] == 'queued' for row in result['tracks'])):
            # Entries left waiting with no search behind them (a failed write, an interrupted
            # "Create playlist") are queued again instead of waiting until the next start.
            if set_discovery.sweep(api, result['id']):
                result = report(db, result['id'])
        return result
    if method == 'sets.auto_scan':
        enabled = p.get('enabled')
        if type(enabled) is not bool:
            raise ValueError('Invalid automatic scan setting.')
        return set_auto_scan(db,p.get('id'),enabled,(p['youtube_browser'] or None) if 'youtube_browser' in p else False)
    if method == 'sets.cancel_task':
        with set_scan_jobs.LOCK:
            control = set_scan_jobs.CONTROLS.get(p.get('task_id'))
        task = set_scan_jobs.cancel(p.get('task_id'))
        saved = task.get('result')
        if not saved and control:
            # The worker may have saved its report without having published it to the job yet.
            with control.lock:
                saved = control.result
        stopped = saved['id'] if saved and saved.get('scan') and saved['scan']['offset'] is not None else control.report_id if control else None
        if stopped and db.execute('SELECT 1 FROM dj_sets WHERE id=?',(stopped,)).fetchone():
            task['result']=cancel_scan(api,db,stopped)
        return task
    if method == 'sets.cancel_scan':
        return cancel_scan(api,db,p.get('id'))
    if method == 'sets.resume_scan':
        sid = p.get('id')
        get_set(db,sid)
        return set_scan_jobs.start(api,lambda:resume_scan(api,sid,manual=True),sid)
    if method == 'sets.scan':
        url, path, pasted = p.get('url', ''), p.get('path'), p.get('tracklist', '')
        recognize = p.get('fingerprint', False)
        browser = p.get('youtube_browser') or None
        if browser not in (None,'safari','chrome','firefox','edge'):
            raise ValueError('Choose a supported YouTube browser session.')
        if not isinstance(pasted, str) or len(pasted)>100_000 or type(recognize) is not bool:
            raise ValueError('Invalid tracklist or scan option.')
        if not isinstance(url,str) or (path is not None and (not isinstance(path,str) or url or not recognize)):
            raise ValueError('Choose one mix link or one audio file.')
        if url:
            url = download_http.public_url(service_links.strip_tracking(url))
        if not url and not path and not pasted.strip():
            raise ValueError('Add a mix link, audio file, or tracklist.')
        def begin_scan():
            set_scan_jobs.current().youtube_browser = browser
            watch = None
            if url:
                set_scan_jobs.progress('Reading source information')
                playlist = playlist_sources.identify(url)
                if playlist:
                    control = set_scan_jobs.current()
                    try:
                        name, rows, warning = playlist_sources.read(api,playlist,SPOTIFY_TOKEN)
                    except ImportCancelled:
                        if not control.playlist_partial:
                            raise
                        name,rows = control.playlist_partial
                        warning = 'Scan stopped. The entries already read were kept.'
                    with control.lock:
                        if control.stop.is_set():
                            warning = 'Scan stopped. The entries already read were kept.'
                        else:
                            set_scan_jobs.progress('Saving playlist entries',completed=len(rows),total=len(rows),unit='entries')
                        saved = save_report(api,name,playlist[1],rows,warning)
                        control.result=saved
                    return find_sources(api,saved)
                reference = service_links.identify(url)
                if not reference or reference[0] not in youtube_audio.NAMES:
                    raise ValueError('Choose a public playlist or a single YouTube mix.')
                watch = reference[2]
                if not pasted.strip():
                    with api.connect() as worker_db:
                        queued = worker_db.execute("""SELECT s.id FROM dj_sets s JOIN dj_set_auto_scan a ON a.set_id=s.id
                            JOIN dj_set_scan c ON c.set_id=s.id WHERE s.watch=? AND a.enabled=1 AND c.offset IS NOT NULL LIMIT 1""",(watch,)).fetchone()
                        if queued:
                            return report(worker_db,queued['id'])
            return scan(api,watch,path,pasted,recognize)
        return set_scan_jobs.start(api,begin_scan)
    sid = p.get('id')
    get_set(db, sid)
    if method == 'sets.reorder':
        return reorder(db, sid, p.get('entry_ids'))
    if method == 'sets.artwork':
        from .set_artwork import enrich
        return preparation.launch(lambda: enrich(api, sid))
    if method == 'sets.recognize':
        return recognize_audio(api, db, sid, p)
    if method == 'sets.apply_recommended':
        if not get_set(db, sid)['playlist_id']:
            raise ValueError('Create the playlist first.')
        choices = []
        for row in db.execute('SELECT rowid AS entry_id,position,detail FROM dj_set_items WHERE set_id=? ORDER BY position', (sid,)):
            if not can_set_source(source_slot(db, sid, row['position'])):
                continue
            best = recommend_sources(json.loads(row['detail']))
            if best:
                choices.append((row['entry_id'], best['url']))
        if not choices:
            return []
        # One job for every entry, so the library is indexed once instead of once per entry.
        task = preparation.launch(lambda: apply_recommended(api, sid, choices))
        return [{'entry_id': str(entry_id), **task} for entry_id, _ in choices]
    if method == 'sets.prepare':
        name = p.get('name', '')
        if not isinstance(name, str) or len(name.strip()) > 100:
            raise ValueError('Playlist name must be at most 100 characters.')
        return preparation.launch(lambda: prepare_set(api, sid, name.strip()))
    if method == 'sets.add_source':
        if p.get('entry_id') is not None:
            row_id = db.execute('SELECT position FROM dj_set_items WHERE set_id=? AND rowid=?', (sid, p['entry_id'])).fetchone()
            if not row_id:
                raise ValueError('Set entry not found.')
            pos = row_id['position']
        else:
            pos = p.get('position')
        url = download_http.public_url(p.get('url'))
        row = source_slot(db, sid, pos)
        if not can_set_source(row):
            raise ValueError('This set entry is already linked or unavailable.')
        if json.loads(row['detail']).get('unknown'):
            raise ValueError('Use a local file to identify this entry.')
        entry_id = db.execute('SELECT rowid FROM dj_set_items WHERE set_id=? AND position=?', (sid, pos)).fetchone()[0]
        return preparation.launch(lambda: prepare_set(api, sid, None, (entry_id, url)))
    if method == 'sets.add_local':
        path, pos = p.get('path'), p.get('position')
        if p.get('entry_id') is not None:
            slot = db.execute('SELECT position FROM dj_set_items WHERE set_id=? AND rowid=?', (sid, p['entry_id'])).fetchone()
            if not slot:
                raise ValueError('Set entry not found.')
            pos = slot['position']
        if not isinstance(path, str) or not path or type(pos) is not int:
            raise ValueError('Choose a local audio file for a set entry.')
        entry = db.execute('SELECT rowid FROM dj_set_items WHERE set_id=? AND position=?', (sid, pos)).fetchone()
        if not entry:
            raise ValueError('Set entry not found.')
        entry_id = entry[0]
        return preparation.launch(lambda: add_local(api, sid, entry_id, path))
    raise ValueError('Unknown set import method.')
