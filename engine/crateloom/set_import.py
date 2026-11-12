"""Turn a DJ mix into a reviewable tracklist and an ordered local playlist."""
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import nullcontext
import json
import math
from pathlib import Path
import re
import sqlite3
import subprocess
import threading
import time
import urllib.parse
import uuid

from . import acquisitions, download_http, playlist_sources, preparation, providers, service_links, shazam_scan, soundcloud_download, youtube_audio
from .metadata_matching import clean_name, normalized, search_identity
from . import set_scan_jobs
from .library_imports import ImportCancelled

MAX_TRACKS = 150
SPOTIFY_TOKEN = None
AUTO_STOP = threading.Event()
AUTO_WAKE = threading.Event()
AUTO_RUNNING = None
MAX_STALLED_ATTEMPTS = 4
TIMESTAMP = re.compile(r'^\s*(?:\[|\()?((?:\d{1,3}:)?\d{1,3}:\d{2})(?:\]|\))?\s*[–—-]?\s+(.+)$')
TRAILING_TIMESTAMP = re.compile(r'^\s*(.+?)\s+(?:[–—-]\s*)?[\[(]?((?:\d{1,3}:)?\d{1,3}:\d{2})[\])]?\s*$')
SEPARATOR = re.compile(r'\s+[-–—]\s+')
LINK = re.compile(r'https://[^\s<>]+')
FREE_DOWNLOAD = '45d0cbc5-d65b-4e77-bdfd-8a75207cb5c5'
VERSION_CREDIT = re.compile(r'\b(edit|remix|mix|flip|refunk|rework|bootleg|version|blend)$', re.I)
VIDEO_VARIANT = re.compile(r'\b(sped up|slowed|nightcore|karaoke|cover|reaction|remastered|live|instrumental)\b', re.I)
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


def video_match(item, videos):
    matches, suggestions = [], []
    expected_duration = item.get('duration')
    for video in videos:
        title = clean_name(video['title'])
        channel = re.sub(r'\s+-\s+Topic$', '', video.get('channel') or '', flags=re.I)
        url = 'https://www.youtube.com/watch?v=' + video['id']
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
                                    'channel': channel, 'verified': video.get('channel_is_verified') is True})
            if expected not in possible or (VIDEO_VARIANT.search(title) and not VIDEO_VARIANT.search(song)):
                continue
            if expected_duration and abs(video['duration'] - expected_duration) > max(5, expected_duration * .04):
                continue
            official = video.get('channel_is_verified') is True and normalized(channel) == expected[0]
            # Prefer a recording from the artist and avoid music video intros when possible.
            score = 4 * official + (2 if normalized(title) == normalized(song) else 0)
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
    duration, original = option.get('duration'), item.get('duration')
    extended = bool(re.search(r'\bextended\b', title))
    if duration and original and not original * .65 <= duration <= original * (2.2 if extended else 1.4):
        return None
    video = bool(re.search(r'\b(music video|official video|mv)\b', title))
    score = 40 * extended + 8 * bool(re.search(r'\b(audio|topic)\b', title)) + 4 * option.get('verified', False) - 20 * video
    if duration and original and not extended:
        score -= abs(duration - original) / original * 12
    return score, -(duration or 900)


def recommend_sources(item):
    options = list({option['url']: option for option in item.get('source_options', [])}.values())
    ranked = []
    for option in options:
        option.pop('recommended', None)
        option.pop('recommendation_reason', None)
        priority = source_priority(item, option)
        option['compatible'] = priority is not None
        if priority is not None:
            ranked.append((*priority, option))
    ranked.sort(key=lambda candidate: (-candidate[0], -candidate[1]))
    if ranked:
        best = ranked[0][2]
        best['recommended'] = True
        best['recommendation_reason'] = 'Extended mix' if re.search(r'\bextended\b', normalized(best['title'])) else 'Audio version / closest duration'
    options.sort(key=lambda option: not option.get('recommended', False))
    item['source_options'] = options[:10]
    return next((option for option in item['source_options'] if option.get('recommended')), None)


def find_video_source(api, item, watch):
    queries = [' '.join((item['artist'], item['title']))]
    for artist, song in credit_options(item):
        main_artist, *guests = FEATURE_CREDIT.split(artist, maxsplit=1)
        if guests:
            queries.append(' '.join((main_artist, song)))
    matches, suggestions = [], []
    for query in dict.fromkeys(queries):
        videos = youtube_audio.search(api, query)
        if not track_identity(item['artist'], item['title'])['version'] and not any(re.search(r'\bextended\b', video['title'], re.I) for video in videos):
            try:
                videos += youtube_audio.search(api, query + ' extended mix')
            except ValueError:
                pass
        if watch:
            videos = [video for video in videos if not watch.endswith('=' + video['id'])]
        matches, found = video_match(item, videos)
        suggestions.extend(option for option in found if option not in suggestions)
        if matches:
            break
    item['source_options'] = suggestions
    recommended = recommend_sources(item)
    error = None
    if item.get('needs_review') and sum(source_priority(item, option) is not None for option in suggestions) != 1:
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


def find_soundcloud_source(api, item):
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


def video_info(api, watch):
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
            result = api.import_process([
                binary, '--ignore-config', '--no-plugin-dirs', '--no-remote-components', '--proxy', '',
                '--no-playlist', '--no-warnings', '--socket-timeout', '8', '--retries', '0',
                '--extractor-retries', '0', '--skip-download', '--ignore-no-formats-error',
                *youtube_audio.runtime_args(),'--print', '%(.{id,title,description,chapters,duration,is_live})j', '--', watch,
            ], 25, process_group=True)
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
        candidates = []
        for comment in youtube_tracklist.read_comments(api,watch):
            rows = parse_tracklist(comment,'Published comment tracklist')
            times = [row['at'] for row in rows]
            if (len(rows)>=3 and times==sorted(set(times)) and times[0]<=120
                    and all(b-a>=15 for a,b in zip(times,times[1:]))
                    and (not data.get('duration') or times[-1]<data['duration'])):
                candidates.append(rows)
        if candidates:
            data['comment_tracklist'] = max(candidates,key=len)
    return data


def scan(api, watch, path, pasted, recognize):
    notes, listed, fingerprints = [], [], []
    info, result = {}, {}
    control = set_scan_jobs.current()
    cancelled = False
    try:
        if pasted.strip():
            set_scan_jobs.progress('Reading pasted tracklist')
            listed = parse_tracklist(pasted, 'Pasted tracklist', include_uncredited=True)
            if not listed:
                notes.append('No timestamped entries were found in the pasted tracklist.')
        if watch and (not listed or recognize):
            try:
                info = video_info(api, watch)
            except ValueError as error:
                notes.append(str(error))
        if not pasted.strip():
            listed = parse_tracklist(info.get('description') or '', 'Video description',include_uncredited=True)
            chapter_rows = [listed_item(number(c.get('start_time')), c.get('title') or '', 'Video chapter')
                            for c in info.get('chapters') or []]
            listed.extend(c for c in chapter_rows if c and c['at'] is not None
                          and not any(abs(c['at'] - row['at']) <= 2 for row in listed))
            if not any(row['artist'] for row in listed) and info.get('comment_tracklist'):
                listed = info['comment_tracklist']
        set_scan_jobs.progress('Tracklist checked',completed=len(listed),total=len(listed) or None,unit='entries')
        has_tracklist = any(row['artist'] and not row['unknown'] for row in listed)
        if recognize or (watch and not has_tracklist):
            try:
                result = shazam_scan.scan(api,watch,path,batch=False) if listed else shazam_scan.scan(api,watch,path)
            except ValueError as error:
                notes.append(str(error))
    except ImportCancelled:
        cancelled = True
        result = control.partial if control and control.partial else {}
    name = service_links.clean(info.get('title')) or (Path(path).stem if path else 'Imported DJ Set')
    has_tracklist = any(row['artist'] and not row['unknown'] for row in listed)
    if result:
        fingerprints = fingerprint_tracks(result)
        if result.get('warning'):
            notes.append('Partial scan. ' + result['warning'])
    scanned_duration = number(result.get('duration'))
    scanned_until = number(result.get('scanned_until'))
    cooldown = number(result.get('cooldown')) or 0
    plan = result.get('plan')
    fingerprint_only = not listed and (bool(fingerprints) or scanned_until is not None or scanned_duration is not None)
    rows = fingerprints if fingerprint_only else merge_tracklists(listed, fingerprints)
    length = number(info.get('duration')) or scanned_duration
    if length:
        rows = [row for row in rows if row['at'] < length]
    if not rows and scanned_until is None and scanned_duration is None:
        if cancelled or control and control.stop.is_set():
            raise ImportCancelled('Scan cancelled')
        raise ValueError(' '.join(notes) if notes else 'No published entries or audio matches were available for this mix.')
    if not fingerprints and scanned_duration is not None and scanned_until is None:
        notes.append('The audio was scanned successfully, but no recordings were identified. The unidentified range is kept for review.')
    if len(rows)>MAX_TRACKS:
        raise ValueError(f'This set has more than {MAX_TRACKS} track entries. Split it into smaller sets.')
    if fingerprint_only:
        rows = fingerprint_timeline(rows,scanned_until if scanned_until is not None else length)
        if not plan:
            notes.append('Shazam timestamps are approximate. Check track versions and transitions.')
    with control.lock if control else nullcontext():
        cancelled = cancelled or bool(control and control.stop.is_set())
        if cancelled:
            notes.append('Scan stopped. Published entries, completed matches and audio samples were kept.')
        else:
            set_scan_jobs.progress('Saving set report',completed=len(rows),total=len(rows) or None,unit='entries')
        checkpoint = (scanned_until,length,path,time.time()+cooldown) if fingerprint_only else None
        saved = save_report(api,name,watch,rows,' '.join(notes) or None,checkpoint=checkpoint,plan=plan,
                            automatic=not cancelled or bool(control and control.closing))
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


def save_report(api, name, watch, rows, warning, *, checkpoint=None, plan=None, automatic=True):
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
        result = report(db, sid)
    return result


def get_set(db, sid):
    found = db.execute('SELECT * FROM dj_sets WHERE id=?', (sid,)).fetchone()
    if found is None:
        raise ValueError('Set report not found.')
    return dict(found)


def delete_report(db, sid):
    get_set(db,sid)
    control = set_scan_jobs.for_database(db,sid)
    if control:
        # Stop the scan before its report disappears; its thread then ends as cancelled.
        control.cancel()
    db.execute('BEGIN IMMEDIATE')
    get_set(db,sid)
    for table in ('dj_set_scan_work','dj_set_auto_scan','dj_set_scan','dj_set_items','dj_set_youtube_access','dj_set_history','dj_sets'):
        column = 'id' if table=='dj_sets' else 'set_id'
        db.execute(f'DELETE FROM {table} WHERE {column}=?',(sid,))
    db.commit()
    AUTO_WAKE.set()
    return {'deleted':sid}


def list_reports(db):
    return [dict(row) for row in db.execute("""SELECT s.id,s.name,s.playlist_id,h.created_at,
        count(i.position) AS entries,count(i.track_id) AS imported,
        coalesce(sum(i.download_id IS NOT NULL AND i.track_id IS NULL),0) AS with_download,
        c.offset,c.duration,coalesce(a.enabled,0) AS automatic
        FROM dj_sets s LEFT JOIN dj_set_history h ON h.set_id=s.id
        LEFT JOIN dj_set_scan c ON c.set_id=s.id
        LEFT JOIN dj_set_auto_scan a ON a.set_id=s.id
        LEFT JOIN dj_set_items i ON i.set_id=s.id
        GROUP BY s.id ORDER BY s.rowid DESC""")]


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


def release_backoff(db, sid):
    # An explicit resume waits for Shazam's own cooldown at most, not the automatic stall backoff.
    db.execute('UPDATE dj_set_scan SET retry_at=min(retry_at,?) WHERE set_id=?',
               (time.time()+shazam_scan.RATE_LIMIT_COOLDOWN,sid))


def resume_scan(api, sid, *, manual=False):
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
    options = {'plan':state['plan']} if 'plan' in state else {}
    control = set_scan_jobs.current()
    try:
        result = shazam_scan.scan(api, saved['watch'], state['path'], offset=offset, expected_duration=state['duration'], **options)
    except ImportCancelled:
        if not control or not control.partial:
            raise
        result = control.partial
    duration = result['duration']
    next_offset = number(result.get('scanned_until'))
    scanned_end = next_offset if next_offset is not None else duration
    matches = [row for row in fingerprint_tracks(result) if offset <= row['at'] < scanned_end]
    cooldown = number(result.get('cooldown')) or 0
    warning = None if 'plan' in result else 'Shazam timestamps are approximate. Check track versions and transitions.'
    if result.get('warning'):
        warning = 'Partial scan. ' + result['warning']
    if control and control.stop.is_set():
        warning = 'Scan stopped. Completed matches and audio samples were kept.'
    with api.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        if control and control.stop.is_set() and not db.execute('SELECT 1 FROM dj_sets WHERE id=?',(sid,)).fetchone():
            raise ImportCancelled('Scan cancelled')
        if scan_checkpoint(db,get_set(db,sid)) != state:
            raise ValueError('This scan has already moved forward. Reopen the saved report.')
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
        db.executemany('INSERT INTO dj_set_items(set_id,position,detail) VALUES (?,?,?)',
                       [(sid, position+i, json.dumps(row)) for i,row in enumerate(rows)])
        db.execute("""INSERT INTO dj_set_scan VALUES (?,?,?,?,?)
            ON CONFLICT(set_id) DO UPDATE SET offset=excluded.offset,duration=excluded.duration,
                path=excluded.path,retry_at=excluded.retry_at""",
                   (sid,next_offset,duration,state['path'],time.time()+cooldown))
        if 'plan' in result:
            store_scan_plan(db,sid,result['plan'])
        db.execute('UPDATE dj_sets SET warning=? WHERE id=?', (warning,sid))
        return report(db,sid)


def queue_auto_scan(db, sid, watch):
    if watch:
        db.execute("""UPDATE dj_set_auto_scan SET enabled=0 WHERE set_id IN
            (SELECT id FROM dj_sets WHERE watch=? AND id!=?)""",(watch,sid))
    db.execute("""INSERT INTO dj_set_auto_scan(set_id,enabled) VALUES (?,1)
        ON CONFLICT(set_id) DO UPDATE SET enabled=1,failures=0""",(sid,))


def set_auto_scan(db, sid, enabled):
    db.execute('BEGIN IMMEDIATE')
    saved = get_set(db,sid)
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
    AUTO_WAKE.set()
    result = report(db,sid)
    if result.get('scan',{}).get('running'):
        result['scan']['cancelling']=True
    return result


def auto_scan_step(api):
    global AUTO_RUNNING
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
    progressed = offset is None or offset > job['offset'] or samples > job['samples']
    failures = 0 if progressed else job['failures']+1
    with api.connect_existing() as db:
        updated = db.execute("""UPDATE dj_set_auto_scan SET failures=?,enabled=?
            WHERE set_id=? AND enabled=1""",(failures,int(offset is not None and failures<MAX_STALLED_ATTEMPTS),sid))
        if updated.rowcount and not progressed:
            delay = shazam_scan.RATE_LIMIT_COOLDOWN * 2**(failures-1)
            db.execute('UPDATE dj_set_scan SET retry_at=max(retry_at,?) WHERE set_id=?',(time.time()+delay,sid))
            if error:
                db.execute('UPDATE dj_sets SET warning=? WHERE id=?',('Partial scan. '+error,sid))
    return True


def start_auto_scans(api):
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


def library_index(db):
    library = {}
    for track in db.execute('SELECT id,title,artist,path,duration FROM tracks'):
        library.setdefault(key(track['artist'], track['title']), []).append(track)
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
    if state:
        result['scan'] = {field:state[field] for field in ('offset','duration','retry_at')}
        auto = db.execute('SELECT enabled,failures FROM dj_set_auto_scan WHERE set_id=?',(sid,)).fetchone()
        result['scan'].update(automatic=bool(auto and auto['enabled']), running=AUTO_RUNNING==sid,
                              stalled=bool(auto and auto['failures']>=MAX_STALLED_ATTEMPTS),
                              fast='plan' in state,samples=len(state.get('plan',{}).get('samples',{})))
        control = set_scan_jobs.for_database(db,sid)
        if control:
            result['scan'].update(running=True,cancelling=control.stop.is_set(),progress=dict(control.progress))
        result['scan']['pending_matches']=sum(bool(match) for match in state.get('plan',{}).get('samples',{}).values())
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
        item.pop('listed_links', None)
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
    return report(db, sid)


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


def source_for(api, item, explicit_url, watch=None):
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
                source = providers.register(api, source)
                values = {'title': item['title'], 'artist': item['artist'] or source['values']['artist']}
                return {'source_id': source['id'], 'values': values, 'artwork_path': item.get('artwork_path')}, None
    identity = search_identity(item['title'], item['artist'])
    reference, catalog_links, lookup_error = None, [], None
    if identity['artist'] and not item.get('needs_review'):
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
    urls = [*item['listed_links'], *free_links] if not item.get('needs_review') else []
    if not item.get('needs_review') and soundcloud_download.configured() and not any('soundcloud.com/' in url for url in urls):
        try:
            found = soundcloud_download.find(item['artist'], item['title'], key, item.get('duration'))
            if found:
                urls.append(found)
                add_link(item, 'SoundCloud', found)
        except ValueError as error:
            lookup_error = str(error)
    source = None
    for url in dict.fromkeys(urls):
        try:
            platform = service_links.identify(download_http.public_url(url))
            if platform and platform[2] == watch:
                continue
            if platform and platform[0] == 'SoundCloud':
                source = soundcloud_source(api, platform[1])
                break
            # Only the official free-file route or an inline creator file is eligible automatically.
            url = download_http.public_url(url)
            if platform and platform[0] in youtube_audio.NAMES and url in item['listed_links']:
                resolved, _, _ = youtube_audio.resolve(api, platform)
                source = providers.register(api, resolved)
                break
            if platform:
                continue
            info = download_http.inspect(url)
            source = providers.register(api, dict(info, kind='direct', provider='Direct HTTPS', can_acquire=True))
            break
        except (ValueError, OSError):
            pass
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
        if item.get('source_options'):
            if all(option.get('reference_only') for option in item['source_options']):
                return None, 'Track page found. Check it for a download, then add the file.'
            return None, 'Review the suggested versions below.'
        return None, lookup_error or 'No matching audio source found. Add a track link or local file.'
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
        library = library_index(db)
    lookups = []
    explicit = override[1] if override else None
    for row in rows:
        pos, entry_id = row['position'], row['entry_id']
        if row['track_id'] or (override and override[0] != entry_id) or (not override and row['download_id']):
            continue
        item = normalize_credit(json.loads(row['detail']))
        if not explicit and (item.get('unknown') or (not item['artist'] and not item['listed_links'])):
            continue
        match = matching_track(library, item) if not explicit and not item.get('needs_review') else None
        if match:
            with api.connect() as db:
                db.execute('BEGIN IMMEDIATE')
                current = db.execute('SELECT position,track_id,download_id FROM dj_set_items WHERE set_id=? AND rowid=?', (sid, entry_id)).fetchone()
                if current and not current['track_id'] and not current['download_id']:
                    attach_track(db, sid, current['position'], match, api=api)
            continue
        lookups.append((entry_id,item))
    with ThreadPoolExecutor(max_workers=4, thread_name_prefix='set-source') as pool:
        pending = {pool.submit(source_for, api, item, explicit, saved['watch']):(entry_id,item) for entry_id,item in lookups}
        for future in as_completed(pending):
            entry_id,item = pending.pop(future)
            try:
                params, error = future.result()
                with api.connect() as db:
                    db.execute('BEGIN IMMEDIATE')
                    slot = db.execute('SELECT position,detail FROM dj_set_items WHERE set_id=? AND rowid=?', (sid, entry_id)).fetchone()
                    if not slot:
                        continue
                    current = source_slot(db, sid, slot['position'])
                    if not can_set_source(current) or (not explicit and current['download_id']):
                        continue
                    latest = json.loads(slot['detail'])
                    if latest.get('artwork_path'):
                        item['artwork_path'] = latest['artwork_path']
                        if params:
                            params['artwork_path'] = latest['artwork_path']
                    jid = manager.enqueue_in(db, params, capacity=acquisitions.SET_CAPACITY) if params else None
                    db.execute('UPDATE dj_set_items SET detail=?,download_id=?,error=? WHERE set_id=? AND position=?',
                               (json.dumps(item), jid or current['download_id'], error, sid, slot['position']))
                if jid:
                    manager.ensure_worker()
            except Exception as error:
                # One entry's lookup must not fail the whole preparation.
                if not isinstance(error, (ValueError, OSError)):
                    error = ValueError('This source could not be checked. Try again.')
                with api.connect() as db:
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


def dispatch(api, db, method, p):
    global SPOTIFY_TOKEN
    if method == 'sets.capabilities':
        return {'fingerprinting': shazam_scan.interpreter() is not None}
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
        return report(db, p.get('id'))
    if method == 'sets.auto_scan':
        enabled = p.get('enabled')
        if type(enabled) is not bool:
            raise ValueError('Invalid automatic scan setting.')
        return set_auto_scan(db,p.get('id'),enabled)
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
            url = download_http.public_url(url)
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
                        return saved
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
    if method == 'sets.apply_recommended':
        if not get_set(db, sid)['playlist_id']:
            raise ValueError('Create the playlist first.')
        tasks = []
        for row in db.execute('SELECT rowid AS entry_id,position,detail FROM dj_set_items WHERE set_id=? ORDER BY position', (sid,)):
            if not can_set_source(source_slot(db, sid, row['position'])):
                continue
            item = json.loads(row['detail'])
            best = recommend_sources(item)
            if best:
                entry_id, url = row['entry_id'], best['url']
                try:
                    task = preparation.launch(lambda eid=entry_id, link=url: prepare_set(api, sid, None, (eid, link)))
                except ValueError:
                    # The request queue is full. Report what started; the rest stay eligible.
                    if not tasks:
                        raise
                    break
                tasks.append({'entry_id': str(entry_id), **task})
        return tasks
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
