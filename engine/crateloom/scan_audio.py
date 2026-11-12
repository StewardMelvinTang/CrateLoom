"""Acquire the same public mix through bounded audio-format fallbacks."""
import json
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import time
from urllib.parse import parse_qs, urlsplit

from . import download_http, service_links, set_scan_jobs, youtube_audio
from .acquisitions import AudioTransfer
from .shazam_worker import MAX_DURATION

EXTENSIONS = ('m4a','webm','opus','ogg','mp3','aac','mp4')


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
        if (last_data[1] and time.monotonic()-last_data[0]>25) or (elapsed>60 and not last_data[1]):
            raise ValueError('The alternate audio connection did not make progress.')
    args = [binary,'--ignore-config','--no-plugin-dirs','--no-remote-components','--proxy','',
            '--no-playlist','--no-warnings','--no-colors','--socket-timeout','10','--retries','2',
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
    check()
    folder.mkdir(parents=True,exist_ok=True)
    last_error = None
    failures = []
    def record(kind, attempt):
        failures.append({'attempt':attempt,'result':kind})
        api.atomic(folder/'attempts.json',json.dumps({'watch':watch,'attempts':failures}).encode())
    with tempfile.TemporaryDirectory(prefix='transfer-',dir=folder) as temp:
        temporary = Path(temp)
        for attempt in range(4):
            check()
            target = temporary/str(attempt)
            target.mkdir()
            try:
                set_scan_jobs.progress('Connecting to mix audio' if attempt==0 else 'Trying another audio connection',attempt=attempt+1)
                if attempt in (1,2):
                    kwargs = {'scan':True} if attempt==1 else {}
                    review, url, headers = youtube_audio.resolve(api,reference,**kwargs)
                    if review['duration']>MAX_DURATION:
                        raise ValueError('Audio scans support mixes up to 3 hours long.')
                    extension = Path(review['filename']).suffix.lstrip('.')
                    source = target/('mix.'+extension)
                    transfer = ScanTransfer(api,check)
                    transfer.transfer(None,review,url,headers,source,transfer.check,ranges=True)
                    expected = review['duration']
                else:
                    control = set_scan_jobs.current()
                    client = ('web' if control and control.youtube_browser else 'web_safari') if attempt==3 else None
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
                record('completed',attempt+1)
                return destination
            except (ValueError,OSError,subprocess.SubprocessError) as error:
                last_error = error
                kind = youtube_audio.failure_kind(error)
                record(kind,attempt+1)
                if kind=='unavailable' or '3 hours' in str(error):
                    break
    kinds = {item['result'] for item in failures}
    if 'unavailable' in kinds:
        message = 'This video is private, removed, or restricted to channel members. Open it on YouTube to check access.'
    elif 'age' in kinds:
        message = 'YouTube requires age-verified access to this mix. Choose your signed-in browser under Options, then retry.'
    elif 'region' in kinds:
        message = 'The uploader has blocked this mix in your region. Available published entries are kept.'
    elif 'challenge' in kinds:
        message = 'YouTube is requesting a signed-in session. Choose your browser under Options, then retry.'
    else:
        message = 'The public audio connections did not complete. Your available tracklist is kept; retry the audio scan when the connection recovers.'
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
