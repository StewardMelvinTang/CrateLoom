"""Acquire the same public mix through bounded audio-format fallbacks."""
import json
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import threading
import time
from urllib.parse import parse_qs, urlsplit

from . import download_http, service_links, set_scan_jobs, youtube_audio
from .acquisitions import AudioTransfer
from .shazam_worker import MAX_DURATION

EXTENSIONS = ('m4a','webm','opus','ogg','mp3','aac','mp4')
# One acquisition per mix: a second caller waits, then reuses the saved audio.
OBTAIN_LOCKS = {}
OBTAIN_GUARD = threading.Lock()
# Mix downloads in progress. Background searches wait for zero so they do not compete for bandwidth.
ACQUIRING = 0
IDLE = threading.Condition()


def watch_lock(key):
    with OBTAIN_GUARD:
        return OBTAIN_LOCKS.setdefault(key, threading.Lock())


def wait_until_idle(timeout):
    """True once no mix audio is being acquired; False if that takes longer than timeout seconds."""
    with IDLE:
        return IDLE.wait_for(lambda: ACQUIRING <= 0, timeout)


def acquiring(change):
    global ACQUIRING
    with IDLE:
        ACQUIRING += change
        if ACQUIRING <= 0:
            IDLE.notify_all()


class ScanTransfer(AudioTransfer):
    def __init__(self, api, check):
        super().__init__(api)
        self.check_cancel = check
        self.control = set_scan_jobs.current()
        self.started = self.last_data = time.monotonic()
        self.received = 0
        self.total = None

    def update(self, jid, **fields):
        received = fields.get('downloaded_bytes',self.received)
        if received > self.received:
            self.last_data = time.monotonic()
        self.received = received
        self.total = fields.get('total_bytes',self.total)
        if self.control:
            self.control.publish('Downloading mix audio',completed=self.received,total=self.total,unit='bytes')

    def check(self):
        if self.control:
            self.control.check()
        self.check_cancel()
        elapsed = time.monotonic()-self.started
        rate = self.received/max(elapsed,1)
        misses_deadline = self.total and (self.total-self.received)/max(rate,1)>600-elapsed
        if time.monotonic()-self.last_data > 25 or (elapsed > 35 and (rate < 16*1024 or misses_deadline)):
            raise ValueError('This audio connection stalled. Trying another format.')


def restricted(error):
    return youtube_audio.failure_kind(error) in ('unavailable','age','region')


def isolate_audio(api, source):
    info = api.import_process(['ffprobe','-v','error','-show_streams','-of','json',str(source)],30)
    streams = json.loads(info.stdout).get('streams',[])
    audio = next((s for s in streams if s.get('codec_type')=='audio'),None)
    if not audio:
        raise ValueError('This stream has no audio track.')
    if not any(s.get('codec_type')=='video' and not s.get('disposition',{}).get('attached_pic') for s in streams):
        return source
    set_scan_jobs.progress('Preparing mix audio')
    extension = 'webm' if audio.get('codec_name') in ('opus','vorbis') else 'm4a'
    target = source.with_name('audio.'+extension)
    api.import_process(['ffmpeg','-nostdin','-v','error','-i',str(source),'-map','0:a:0','-vn','-c:a','copy',str(target)],90)
    source.unlink()
    return target.replace(source.with_name('mix.'+extension))


def validate(api, source, expected=None):
    if not source.is_file() or not 0 < source.stat().st_size <= download_http.MAX_BYTES:
        raise ValueError('The mix audio was incomplete or exceeded the size limit.')
    result = api.import_process(['ffprobe','-v','error','-show_format','-show_streams','-of','json',str(source)],30)
    info = json.loads(result.stdout)
    duration = float(info.get('format',{}).get('duration',0))
    streams = info.get('streams',[])
    if not any(s.get('codec_type')=='audio' for s in streams) or any(s.get('codec_type')=='video' and not s.get('disposition',{}).get('attached_pic') for s in streams):
        raise ValueError('The service did not return an audio-only mix.')
    if not 0 < duration <= MAX_DURATION:
        raise ValueError('Audio scans support mixes up to 3 hours long.')
    if expected and abs(expected-duration)>max(2,expected*.001):
        raise ValueError('The downloaded mix length did not match this video.')
    return duration


def segmented(api, watch, folder, check, client=None):
    binary = youtube_audio.executable()
    if not binary:
        raise ValueError('Install yt-dlp and Deno to scan public YouTube audio.')
    metadata = []
    started = time.monotonic()
    last_data = [started,0]
    # Reading a browser session can wait on a macOS keychain or privacy request.
    browser = youtube_audio.selected_browser()
    first_byte = 150 if browser else 60
    def line(value):
        if value.startswith('scan-progress:'):
            try:
                data = json.loads(value[len('scan-progress:'):])
            except ValueError:
                return
            received = data.get('downloaded_bytes') or 0
            if received > last_data[1]:
                last_data[:]=[time.monotonic(),received]
            set_scan_jobs.progress('Downloading alternate audio stream',completed=received,total=data.get('total_bytes'),unit='bytes')
        elif value.startswith('{'):
            try:
                metadata.append(json.loads(value))
            except ValueError:
                pass
    def poll():
        check()
        size = 0
        for path in folder.iterdir():
            try:
                if path.is_file():
                    size += path.stat().st_size
            except FileNotFoundError:
                continue
        if size > download_http.MAX_BYTES+1024*1024:
            raise ValueError('This mix exceeds the audio size limit.')
        elapsed = time.monotonic()-started
        if (last_data[1] and time.monotonic()-last_data[0]>25) or (elapsed>first_byte and not last_data[1]):
            if browser and not last_data[1]:
                raise ValueError(f'Could not read cookies from {browser}: no response to the macOS keychain or privacy request.')
            raise ValueError('The alternate audio connection did not make progress.')
    args = [binary,'--ignore-config','--no-plugin-dirs','--no-remote-components','--proxy','',
            '--no-playlist',*([] if browser else ['--no-warnings']),'--no-colors','--socket-timeout','10','--retries','2',
            '--fragment-retries','2','--extractor-retries','1','--retry-sleep','1','--abort-on-unavailable-fragments',
            '--format','bestaudio[abr<=96][vcodec=none]/bestaudio[vcodec=none]/best[height<=360]/worst','--max-filesize','512M','--match-filter','!is_live & duration <= 10800',
            '--concurrent-fragments','3','--newline','--progress','--progress-delta','.5',
            '--progress-template','download:scan-progress:%(progress)j',
            '--print','after_move:%(.{id,duration,filepath,ext})j','--output',str(folder/'mix.%(ext)s'),
            *youtube_audio.runtime_args()]
    if client:
        args += ['--extractor-args','youtube:player_client='+client]
    set_scan_jobs.run_process(api,[*args,'--',watch],600,line,poll)
    vid = parse_qs(urlsplit(watch).query)['v'][0]
    data = next((row for row in metadata if row.get('id')==vid),None)
    if not data or data.get('ext') not in EXTENSIONS:
        raise ValueError('No complete audio-only stream was returned for this video.')
    source = Path(data.get('filepath','')).resolve()
    if source.parent!=folder.resolve() or source.name!='mix.'+data['ext']:
        raise ValueError('The audio service returned an unexpected file.')
    return isolate_audio(api,source), data.get('duration')


def obtain(api, watch, check):
    reference = service_links.identify(watch)
    if not reference or reference[0] not in youtube_audio.NAMES:
        raise ValueError('Choose a single public YouTube mix.')
    watch = reference[2]
    vid = parse_qs(urlsplit(watch).query)['v'][0]
    lock = watch_lock((str(api.ROOT), vid))
    while not lock.acquire(timeout=.2):
        check()
    try:
        return saved_audio(api, watch, vid) or download(api, reference, watch, vid, check)
    finally:
        lock.release()


def saved_audio(api, watch, vid):
    folder = api.ROOT/'set-audio'/vid
    manifest = folder/'source.json'
    try:
        saved = json.loads(manifest.read_text())
        source = folder/saved['filename']
        if (saved['watch']==watch and saved['filename'] in {'mix.'+ext for ext in EXTENSIONS}
                and source.is_file() and source.stat().st_size==saved['size'] and api.file_hash(source)==saved['hash']):
            set_scan_jobs.progress('Using saved mix audio',completed=1,total=1,unit='steps')
            folder.touch()
            return source
    except (OSError,ValueError,KeyError):
        pass


def download(api, reference, watch, vid, check):
    check()
    acquiring(1)
    try:
        return transfer_mix(api, reference, watch, vid, check)
    finally:
        acquiring(-1)


def transfer_mix(api, reference, watch, vid, check):
    folder = api.ROOT/'set-audio'/vid
    manifest = folder/'source.json'
    folder.mkdir(parents=True,exist_ok=True)
    last_error = None
    failures = []
    browser = youtube_audio.selected_browser()
    # Only the extractor's own transfer carries a browser session. The direct CDN
    # routes are anonymous, so they are skipped when the owner chose a session.
    routes = [('native',None),('native','web')] if browser else [('native',None),('direct-webm',None),('direct-m4a',None),('native','web_safari')]
    def record(kind, attempt, route, error=None):
        entry = {'attempt':attempt,'route':route,'result':kind}
        if error is not None:
            entry['detail'] = youtube_audio.diagnostic(error)
        failures.append(entry)
        api.atomic(folder/'attempts.json',json.dumps({'watch':watch,'browser':browser,'attempts':failures}).encode())
    with tempfile.TemporaryDirectory(prefix='transfer-',dir=folder) as temp:
        temporary = Path(temp)
        for attempt,(route,client) in enumerate(routes):
            check()
            target = temporary/str(attempt)
            target.mkdir()
            try:
                stage = 'Trying another audio connection' if attempt else f'Reading the YouTube session from {youtube_audio.BROWSERS[browser]}' if browser else 'Connecting to mix audio'
                set_scan_jobs.progress(stage,attempt=attempt+1)
                if route!='native':
                    kwargs = {'scan':True} if route=='direct-webm' else {}
                    review, url, headers = youtube_audio.resolve(api,reference,**kwargs)
                    if review['duration']>MAX_DURATION:
                        raise ValueError('Audio scans support mixes up to 3 hours long.')
                    extension = Path(review['filename']).suffix.lstrip('.')
                    source = target/('mix.'+extension)
                    transfer = ScanTransfer(api,check)
                    transfer.transfer(None,review,url,headers,source,transfer.check,ranges=True)
                    expected = review['duration']
                else:
                    source, expected = segmented(api,watch,target,check,client=client)
                check()
                set_scan_jobs.progress('Checking mix audio',completed=0,total=1,unit='steps')
                duration = validate(api,source,expected)
                digest = api.file_hash(source)
                check()
                destination = folder/source.name
                source.replace(destination)
                api.atomic(manifest,json.dumps({'watch':watch,'filename':destination.name,'size':destination.stat().st_size,
                                               'hash':digest,'duration':duration}).encode())
                prune(api.ROOT/'set-audio',folder)
                record('completed',attempt+1,route)
                return destination
            except (ValueError,OSError,subprocess.SubprocessError) as error:
                last_error = error
                kind = youtube_audio.failure_kind(error)
                record(kind,attempt+1,route,error)
                # An unreadable session or a removed video fails the same way on every route.
                if kind in ('unavailable','session') or '3 hours' in str(error):
                    break
    message = youtube_audio.access_message({item['result'] for item in failures},browser)
    if '3 hours' in str(last_error):
        message = 'Audio scans support mixes up to 3 hours long.'
    raise ValueError(message) from last_error


def prune(root, keep):
    folders = sorted((p for p in root.iterdir() if p.is_dir() and not p.is_symlink() and re.fullmatch(r'[\w-]{11}',p.name)),key=lambda p:p.stat().st_mtime,reverse=True)
    size = 0
    for index, folder in enumerate(folders):
        size += sum(p.stat().st_size for p in folder.iterdir() if p.is_file())
        if folder!=keep and (index>=4 or size>1024*1024*1024):
            shutil.rmtree(folder)
