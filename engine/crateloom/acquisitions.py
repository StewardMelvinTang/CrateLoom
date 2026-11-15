"""Persistent, bounded downloads. Only verified audio enters the library."""
from concurrent.futures import ThreadPoolExecutor, as_completed
import datetime
import errno
import hashlib
from http.client import IncompleteRead
import json
import os
from pathlib import Path
import re
import subprocess
import threading
import time
import uuid
from urllib.parse import urlsplit

from . import download_http, playlist_sources, providers, preparation, soundcloud_download, youtube_audio
from .library_imports import ImportCancelled
from .metadata_matching import normalized

MANAGERS = {}
MANAGER_LOCK = threading.Lock()
ACTIVE = ('queued','downloading','verifying','importing','cancelling')
TERMINAL_RETRY = ('failed','cancelled','interrupted')
QUEUE_CAPACITY = 25
# Preparing a set may queue every entry of a playlist; retries stay within the same bound.
SET_CAPACITY = playlist_sources.MAX_ENTRIES + QUEUE_CAPACITY
WORKER_COUNT = 4
PER_SOURCE = 3
RANGE_SIZE = 512 * 1024


def source_group(source):
    return source.get('adapter') or urlsplit(source['url']).hostname


def is_dash(probe):
    """True for a fragmented MP4 as YouTube serves it (major brand 'dash')."""
    tags = (probe.get('format') or {}).get('tags') or {}
    return str(tags.get('major_brand', '')).strip().lower() == 'dash'


def repack_m4a(api, path):
    """Copy the audio stream of a fragmented MP4 into a regular M4A in place, without re-encoding.

    Returns the new size and SHA-256 of the file.
    """
    path = Path(path)
    staged = path.with_name(path.name + '.repack.m4a')
    try:
        api.import_process(['ffmpeg', '-v', 'error', '-y', '-i', str(path), '-map', '0:a:0', '-c', 'copy',
                            '-map_metadata', '0', '-movflags', '+faststart', '-f', 'ipod', str(staged)], 120)
        os.replace(staged, path)
    finally:
        staged.unlink(missing_ok=True)
    digest = hashlib.sha256()
    with path.open('rb') as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(block)
    return path.stat().st_size, digest.hexdigest()


class RangeUnavailable(ValueError):
    pass


def now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def public_job(row):
    job = dict(row)
    review = json.loads(job.pop('review'))
    source = review['source']
    job.update(title=review['fields']['title'], artist=review['fields'].get('artist',''),
               version=review.get('version',''), source_url=source.get('display_url') if source.get('adapter')=='youtube' else download_http.display_url(source['url']),
               filename=source['filename'], quality=json.loads(job['quality']) if job.get('quality') else None,
               duplicate=bool(job['duplicate']))
    return job


class AudioTransfer:
    """Transfer a reviewed source, optionally reporting progress to a job."""

    def __init__(self, api):
        self.api = api
        self.extra_streams = threading.BoundedSemaphore(2)

    def update(self, jid, **fields):
        pass

    def transfer_ranges(self, jid, source, url, headers, partial, check):
        total = source['declared_bytes']
        if total > download_http.MAX_BYTES:
            raise ValueError('This download exceeds the 512 MB limit')
        deadline = time.monotonic() + 600
        blocks = [(start,min(start+RANGE_SIZE,total)-1) for start in range(0,total,RANGE_SIZE)]
        first_url, validator = None, None
        abandoned = threading.Event()

        def request_range(start, end):
            check()
            if abandoned.is_set() or time.monotonic() > deadline:
                raise RangeUnavailable('Range transfer stopped')
            request = {**headers,'Range':f'bytes={start}-{end}'}
            if validator:
                request['If-Match'] = validator
            with download_http.open_url(url,headers=request) as response:
                if response.status != 206 or response.headers.get('Content-Range') != f'bytes {start}-{end}/{total}':
                    raise RangeUnavailable('Source does not provide the requested byte range')
                try:
                    info = download_http.content_info(response,source['url'])
                except ValueError as error:
                    raise RangeUnavailable('Source did not return a supported audio range') from error
                if first_url and (info['resolved_url'] != first_url or (validator and info.get('etag') != validator)):
                    raise RangeUnavailable('Source changed during the range transfer')
                if not urlsplit(info['resolved_url']).hostname.endswith('.googlevideo.com'):
                    raise RangeUnavailable('Unexpected range source')
                if response.headers.get('Content-Encoding','identity').lower() != 'identity':
                    raise RangeUnavailable('Source encoded the byte range')
                data = bytearray()
                while len(data) < end-start+2:
                    check()
                    if abandoned.is_set() or time.monotonic() > deadline:
                        raise RangeUnavailable('Range transfer stopped')
                    block = response.read1(min(64*1024,end-start+2-len(data)))
                    if not block:
                        break
                    data.extend(block)
                if len(data) != end - start + 1:
                    raise RangeUnavailable('Source returned an incomplete byte range')
                return data, info

        def read_range(start, end):
            try:
                return request_range(start,end)
            except (OSError, IncompleteRead, download_http.SourceConnectionError):
                return request_range(start,end)

        first, info = read_range(*blocks[0])
        first_url = info['resolved_url']
        etag = info.get('etag')
        validator = etag if etag and not etag.startswith('W/') else None
        received = len(first)
        self.update(jid,stage='Downloading',total_bytes=total,downloaded_bytes=received)
        extras = 0
        while extras < 2 and self.extra_streams.acquire(blocking=False):
            extras += 1
        try:
            with partial.open('xb') as output, ThreadPoolExecutor(max_workers=1+extras) as pool:
                os.fchmod(output.fileno(),0o600)
                output.write(first)
                pending = iter(blocks[1:])
                def submit():
                    if block := next(pending, None):
                        futures[pool.submit(read_range,*block)] = block[0]
                futures = {}
                for _ in range(1+extras):
                    submit()
                last_report = time.monotonic()
                try:
                    while futures:
                        future = next(as_completed(futures))
                        offset = futures.pop(future)
                        data, _ = future.result()
                        output.seek(offset)
                        output.write(data)
                        received += len(data)
                        if time.monotonic() - last_report >= .25:
                            self.update(jid,downloaded_bytes=received)
                            last_report = time.monotonic()
                        submit()
                finally:
                    abandoned.set()
                    for future in futures:
                        future.cancel()
                output.flush()
                os.fsync(output.fileno())
        finally:
            for _ in range(extras):
                self.extra_streams.release()
        if received != total:
            raise RangeUnavailable('Source returned incomplete audio')
        return info, received, self.api.file_hash(partial)

    def transfer(self, jid, source, url, headers, partial, check, *, ranges):
        youtube_audio.wait_until_ready(url, check, lambda: self.update(jid,stage='Waiting for YouTube…'))
        if ranges and (source.get('declared_bytes') or 0) >= 2*RANGE_SIZE:
            try:
                return self.transfer_ranges(jid, source, url, headers, partial, check)
            except download_http.SourceHTTPError as error:
                if error.code not in (400, 403, 412, 416, 501):
                    raise
            except (RangeUnavailable, OSError, IncompleteRead, download_http.SourceConnectionError):
                pass
            partial.unlink(missing_ok=True)
            self.update(jid,downloaded_bytes=0)

        received = 0
        digest = hashlib.sha256()
        deadline = time.monotonic() + 600
        last_report = 0
        with download_http.open_url(url,headers=headers) as response, partial.open('xb') as output:
            os.fchmod(output.fileno(),0o600)
            info = download_http.content_info(response,source['url'])
            total = info['declared_bytes']
            if response.status != 200:
                raise ValueError('The source did not return a complete audio file')
            if source.get('declared_bytes') is not None and total is not None and total != source['declared_bytes']:
                raise ValueError('The source size changed after review. Resolve the link again.')
            if source.get('etag') and info.get('etag') and source['etag'] != info['etag']:
                raise ValueError('The source changed after review. Resolve the link again.')
            self.update(jid,stage='Downloading',total_bytes=total)
            while True:
                check()
                if time.monotonic() > deadline:
                    raise ValueError('Download timed out after 10 minutes. Retry or choose another source.')
                block = response.read1(64*1024)
                if not block:
                    break
                received += len(block)
                if received > download_http.MAX_BYTES:
                    raise ValueError('This download exceeds the 512 MB limit')
                output.write(block)
                digest.update(block)
                if time.monotonic()-last_report >= .25:
                    self.update(jid,downloaded_bytes=received)
                    last_report=time.monotonic()
            output.flush()
            os.fsync(output.fileno())
            expected = total if total is not None else source.get('declared_bytes')
            if expected is not None and expected != received:
                raise ValueError('The download ended early. Retry to fetch the complete file.')
        return info, received, digest.hexdigest()

class Manager(AudioTransfer):
    def __init__(self, api):
        super().__init__(api)
        self.lock = threading.Lock()
        self.ready = threading.Condition(self.lock)
        self.import_lock = threading.Lock()
        self.closing = threading.Event()
        self.stops = {}
        self.threads = []
        self.active_sources = {}
        self.cooldowns = {}
        self.slow_sources = {}
        self.retried = set()
        self.work = api.ROOT/'acquisition-work'
        self.work.mkdir(parents=True, exist_ok=True)
        with api.connect() as db:
            stale = db.execute("SELECT id,attempt FROM acquisitions WHERE state IN ('downloading','verifying','importing','cancelling')").fetchall()
            for row in stale:
                if re.fullmatch(r'[0-9a-f-]{36}', row['id']):
                    (self.work/f"{row['id']}-{row['attempt']}.part").unlink(missing_ok=True)
            db.execute("UPDATE acquisitions SET state='interrupted',stage='Interrupted',error='Download interrupted. Retry to start again; any saved file is shown below.',updated_at=? WHERE state IN ('downloading','verifying','importing','cancelling')", (now(),))

    def ensure_worker(self, *, wake=True):
        with self.ready:
            if self.closing.is_set():
                return
            self.threads = [worker for worker in self.threads if worker.is_alive()]
            while len(self.threads) < WORKER_COUNT:
                worker = threading.Thread(target=self.run, name='audio-acquisition', daemon=True)
                self.threads.append(worker)
                worker.start()
            if wake:
                self.ready.notify_all()

    def update(self, jid, **fields):
        fields['updated_at'] = now()
        with self.api.connect_existing() as db:
            db.execute('UPDATE acquisitions SET '+','.join(k+'=?' for k in fields)+' WHERE id=?', (*fields.values(),jid))

    def list(self):
        with self.api.connect() as db:
            rows = db.execute("""SELECT * FROM acquisitions AS a
                WHERE NOT EXISTS(SELECT 1 FROM hidden_acquisition_history WHERE id=a.id)
                ORDER BY (a.state IN ('downloading','verifying','importing','cancelling')) DESC,
                    (a.state='queued') DESC, CASE WHEN a.state='queued' THEN a.rowid END, a.rowid DESC
                LIMIT 1100""")
            return [public_job(row) for row in rows]

    def clear_completed(self):
        with self.api.connect() as db:
            return db.execute("""INSERT OR IGNORE INTO hidden_acquisition_history(id)
                SELECT id FROM acquisitions WHERE state='completed'""").rowcount

    def clear_failed(self):
        with self.api.connect() as db:
            return db.execute("""INSERT OR IGNORE INTO hidden_acquisition_history(id)
                SELECT id FROM acquisitions WHERE state IN ('failed','cancelled','interrupted')""").rowcount

    def get(self, jid):
        with self.api.connect() as db:
            row = db.execute('SELECT * FROM acquisitions WHERE id=?',(jid,)).fetchone()
            if row is None:
                raise ValueError('Download job not found')
            return public_job(row)

    def enqueue(self, params):
        with self.api.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            jid = self.enqueue_in(db, params)
        self.ensure_worker()
        return self.get(jid)

    def enqueue_in(self, db, params, *, capacity=QUEUE_CAPACITY):
        source = providers.get(self.api, params.get('source_id'), 'direct')
        reference = providers.get(self.api, params['reference_id'], 'reference') if params.get('reference_id') else None
        fields = params.get('values',{})
        if not isinstance(fields,dict) or any(k not in preparation.FIELDS or not isinstance(v,str) or len(v)>500 or '\x00' in v for k,v in fields.items()):
            raise ValueError('Review fields must be text up to 500 characters')
        if not fields.get('title','').strip():
            raise ValueError('Enter a title before downloading')
        version = params.get('version','')
        if not isinstance(version,str) or len(version)>200 or '\x00' in version:
            raise ValueError('Keep the version name within 200 characters')
        if version.strip() and normalized(version) not in normalized(fields['title']) and len(fields['title'].strip())+len(version.strip())+3>500:
            raise ValueError('Keep the combined title and version within 500 characters')
        art = params.get('artwork_path')
        if art is not None:
            art = Path(art).resolve(strict=True)
            if art.parent != (self.api.ROOT/'artwork').resolve():
                raise ValueError('Choose a release cover before downloading')
            art = str(art)
        review = {'source':source,'reference':reference,'fields':fields,'version':version,'artwork_path':art}
        jid = str(uuid.uuid4());stamp = now()
        count = db.execute("SELECT count(*) FROM acquisitions WHERE state IN ('queued','downloading','verifying','importing','cancelling')").fetchone()[0]
        if count >= capacity:
            raise ValueError('The download queue is full. Wait or cancel a queued item.')
        db.execute("INSERT INTO acquisitions(id,state,stage,review,created_at,updated_at,total_bytes) VALUES (?,'queued','Queued',?,?,?,?)",(jid,json.dumps(review),stamp,stamp,source.get('declared_bytes')))
        return jid

    def cancel(self, jid):
        with self.lock:
            with self.api.connect() as db:
                row = db.execute('SELECT state FROM acquisitions WHERE id=?',(jid,)).fetchone()
                if row is None:
                    raise ValueError('Download job not found')
                if row['state']=='queued':
                    db.execute("UPDATE acquisitions SET state='cancelled',stage='Cancelled',updated_at=? WHERE id=?",(now(),jid))
                elif row['state'] in ACTIVE:
                    if jid in self.stops:
                        self.stops[jid].set()
                    db.execute("UPDATE acquisitions SET state='cancelling',stage='Stopping…',updated_at=? WHERE id=? AND state NOT IN ('completed','failed','cancelled','interrupted')",(now(),jid))
        return self.get(jid)

    def retry(self, jid):
        with self.api.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT state FROM acquisitions WHERE id=?',(jid,)).fetchone()
            if row is None or row['state'] not in TERMINAL_RETRY:
                raise ValueError('Only a failed, cancelled or interrupted download can be retried')
            if db.execute("SELECT count(*) FROM acquisitions WHERE state IN ('queued','downloading','verifying','importing','cancelling')").fetchone()[0] >= SET_CAPACITY:
                raise ValueError('The download queue is full. Wait or cancel a queued item.')
            # A retried download is live again, so it returns to the visible list.
            db.execute('DELETE FROM hidden_acquisition_history WHERE id=?',(jid,))
            db.execute("UPDATE acquisitions SET state='queued',stage='Queued',attempt=attempt+1,downloaded_bytes=0,error=NULL,path=NULL,quality=NULL,track_id=NULL,duplicate=0,updated_at=? WHERE id=?",(now(),jid))
        self.ensure_worker()
        return self.get(jid)

    def run(self):
        while not self.closing.is_set():
            with self.ready:
                if self.closing.is_set():
                    return
                stamp = time.monotonic()
                self.cooldowns = {group:until for group,until in self.cooldowns.items() if until > stamp}
                self.slow_sources = {group:until for group,until in self.slow_sources.items() if until > stamp}
                with self.api.connect_existing() as db:
                    rows = db.execute("SELECT * FROM acquisitions WHERE state='queued' ORDER BY rowid").fetchall()
                    for row in rows:
                        group = source_group(json.loads(row['review'])['source'])
                        limit = 1 if group in self.slow_sources else PER_SOURCE
                        if row['id'] not in self.stops and group not in self.cooldowns and self.active_sources.get(group, 0) < limit:
                            job = dict(row)
                            job['allow_ranges'] = group not in self.slow_sources
                            db.execute("UPDATE acquisitions SET state='downloading',stage='Connecting…',updated_at=? WHERE id=?", (now(),job['id']))
                            break
                    else:
                        job = None
                if job is None:
                    wait = min(self.cooldowns.values()) - stamp if self.cooldowns else None
                    self.ready.wait(timeout=wait)
                    continue
                stop = self.stops[job['id']] = threading.Event()
                self.active_sources[group] = self.active_sources.get(group, 0) + 1
            try:
                if not self.process(job, stop):
                    self.retried.discard((job['id'],job['attempt']))
            finally:
                with self.ready:
                    self.stops.pop(job['id'], None)
                    self.active_sources[group] -= 1
                    if not self.active_sources[group]:
                        del self.active_sources[group]
                    self.ready.notify_all()

    def pause_source(self, job, source, error, stop):
        key = (job['id'],job['attempt'])
        group = source_group(source)
        delay = max(2, error.retry_after or 5)
        with self.ready:
            until = time.monotonic() + delay
            self.cooldowns[group] = max(until, self.cooldowns.get(group, 0))
            self.slow_sources[group] = self.cooldowns[group] + 45
            if key in self.retried or delay > 120 or stop.is_set() or self.closing.is_set():
                return False
            self.retried.add(key)
            self.update(job['id'],state='queued',stage='Waiting for provider…',downloaded_bytes=0)
            return True

    def process(self, job, stop):
        api = self.api;jid = job['id'];partial = self.work/f"{jid}-{job['attempt']}.part"
        published = None;committed = False
        api.IMPORT_CONTEXT.cancel = stop
        def check():
            if stop.is_set() or self.closing.is_set():
                raise ImportCancelled()
        try:
            review = json.loads(job['review']);source = review['source'];check()
            headers = {}
            transfer_url = source['url']
            from_youtube = source.get('adapter') == 'youtube'
            from_soundcloud = source.get('adapter') == 'soundcloud'
            cached = youtube_audio.take_cached(api,source) if from_youtube else None
            cached_soundcloud = soundcloud_download.take_cached(source['url']) if from_soundcloud else None
            if from_soundcloud:
                if cached_soundcloud:
                    transfer_url = cached_soundcloud
                else:
                    _, transfer_url = soundcloud_download.resolve(source['url'])
            if from_youtube and not cached:
                self.update(jid,stage='Resolving audio…')
                transfer_url, headers = youtube_audio.refresh(api,source)
                check()
            elif cached:
                transfer_url, headers = cached
            if source.get('etag') and not source['etag'].startswith('W/'):
                headers['If-Match'] = source['etag']
            elif source.get('last_modified'):
                headers['If-Unmodified-Since'] = source['last_modified']
            use_ranges = from_youtube and job['allow_ranges']
            try:
                info, received, digest = self.transfer(jid, source, transfer_url, headers, partial, check, ranges=use_ranges)
            except download_http.SourceHTTPError as error:
                if not (cached or cached_soundcloud) or error.code not in (401,403,404,410):
                    raise
                partial.unlink(missing_ok=True)
                self.update(jid,stage='Refreshing audio link…',downloaded_bytes=0)
                if from_youtube:
                    transfer_url, headers = youtube_audio.refresh(api,source)
                else:
                    _, transfer_url = soundcloud_download.resolve(source['url'])
                info, received, digest = self.transfer(jid, source, transfer_url, headers, partial, check, ranges=use_ranges)
            check();self.update(jid,state='verifying',stage='Verifying audio',downloaded_bytes=received)
            probe = json.loads(api.import_process(['ffprobe','-v','error','-show_format','-show_streams','-of','json',str(partial)],30).stdout)
            repacked = False
            if from_youtube and is_dash(probe):
                # YouTube serves fragmented MP4 (DASH). WebKit reports such files as twice their
                # length and plays silence past the end, so the audio is copied, unchanged,
                # into a regular M4A.
                self.update(jid,stage='Repacking audio')
                received, digest = repack_m4a(api,partial)
                repacked = True
                check()
                probe = json.loads(api.import_process(['ffprobe','-v','error','-show_format','-show_streams','-of','json',str(partial)],30).stdout)
            streams = probe.get('streams',[]);audio = next((s for s in streams if s.get('codec_type')=='audio'),None)
            if audio is None or any(s.get('codec_type')=='video' and not s.get('disposition',{}).get('attached_pic') for s in streams):
                raise ValueError('Choose an audio file without a video stream')
            container = set(probe.get('format',{}).get('format_name','').split(','))
            extension = next((suffix for name,suffix in [('mp3','.mp3'),('flac','.flac'),('wav','.wav'),('aiff','.aiff'),('ogg','.ogg'),('m4a','.m4a'),('mov','.m4a')] if name in container),None)
            if not extension:
                raise ValueError('Supported downloads are MP3, FLAC, WAV, AIFF, M4A and OGG; no conversion was performed')
            duration = float(probe['format'].get('duration',0))
            if not 0 < duration < 86400:
                raise ValueError('The downloaded audio duration is invalid')
            if from_youtube and (extension!='.m4a' or abs(duration-source['duration'])>max(2,source['duration']*.01)):
                raise ValueError('The downloaded audio differs from the reviewed YouTube format.')
            reference = review.get('reference')
            if reference and reference.get('duration') and abs(reference['duration']-duration)>max(12,duration*.08):
                raise ValueError('Audio duration differs from the chosen recording. Choose the correct version or resolve without that reference.')
            quality = {'codec':audio.get('codec_name') or 'unknown','container':extension[1:],'sample_rate':int(audio.get('sample_rate') or 0),
                       'channels':audio.get('channels'),'bit_rate':int(audio.get('bit_rate') or probe['format'].get('bit_rate') or 0),
                       'duration':duration,'bytes':received,'sha256':digest,'source_url':source['url'] if from_youtube else download_http.display_url(source['url']),
                       'resolved_url':source['url'] if from_youtube or from_soundcloud else download_http.display_url(info['resolved_url']),'downloaded_at':now(),'converted':False,'repacked':repacked,
                       'reference_url':reference.get('source_url') if reference else None,'version_verified':False}
            check()
            name = re.sub(r'[^\w .()-]','_',Path(source['filename']).stem).strip(' .')[:100] or 'audio'
            name = name.encode('utf-8')[:160].decode('utf-8','ignore')
            target = api.ROOT/'downloads'/f"track-{jid}-{job['attempt']}-{name}{extension}";target.parent.mkdir(parents=True,exist_ok=True)
            # Publish the verified file on the same volume without copying it.
            try:
                os.link(partial,target)
                published = target
            except OSError as error:
                if error.errno not in (errno.EXDEV, errno.ENOTSUP):
                    raise
                fd = os.open(target,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
                published = target
                with os.fdopen(fd,'wb') as output, partial.open('rb') as input_file:
                    for block in iter(lambda: input_file.read(1024*1024),b''):
                        check()
                        output.write(block)
                    output.flush()
                    os.fsync(output.fileno())
            self.update(jid,state='importing',stage='Importing',path=str(target),quality=json.dumps(quality));check()
            with self.import_lock, api.connect_existing() as db:
                check()
                track,duplicate = api.import_track(db,str(target));check()
                if track['file_hash'] != digest and not duplicate:
                    raise ValueError('The downloaded file changed during verification')
                if not duplicate:
                    fields = {k:v.strip() for k,v in review['fields'].items() if v.strip()}
                    version = review.get('version','').strip()
                    if version and normalized(version) not in normalized(fields['title']):
                        fields['title'] += ' ('+version+')'
                    params = {'id':track['id'],'values':fields}
                    if review.get('artwork_path'):
                        params['artwork_path'] = review['artwork_path']
                    preparation.dispatch(api,db,'metadata.save',params)
                check()
                db.execute("UPDATE acquisitions SET state='completed',stage=?,track_id=?,path=?,quality=?,duplicate=?,updated_at=? WHERE id=?",('Already in library' if duplicate else 'Imported',track['id'],track['path'],json.dumps(quality),int(duplicate),now(),jid))
                from .set_import import attach_download
                attach_download(db, jid, track['id'], duplicate, api=api)
            committed = True
        except ImportCancelled:
            interrupted = self.closing.is_set()
            self.update(jid,state='interrupted' if interrupted else 'cancelled',stage='Interrupted' if interrupted else 'Cancelled',error='Interrupted; retry to start again.' if interrupted else None,path=None)
        except download_http.SourceHTTPError as error:
            if error.code == 429 or (error.code == 503 and error.retry_after is not None):
                if self.pause_source(job,source,error,stop):
                    return True
            if stop.is_set() or self.closing.is_set():
                interrupted = self.closing.is_set()
                self.update(jid,state='interrupted' if interrupted else 'cancelled',stage='Interrupted' if interrupted else 'Cancelled',path=None)
            else:
                self.update(jid,state='failed',stage='Failed',error=str(error),path=None)
        except subprocess.CalledProcessError:
            self.update(jid,state='failed',stage='Failed',error='The downloaded file is not readable audio. Choose the artist’s audio download link.',path=None)
        except Exception as error:
            self.update(jid,state='failed',stage='Failed',error=str(error) or 'Download failed',path=None)
        finally:
            api.IMPORT_CONTEXT.cancel = None
            cleanup = [partial]
            if published and (not committed or duplicate):
                cleanup.append(published)
            for path in cleanup:
                try:
                    path.unlink(missing_ok=True)
                except OSError:
                    # A cleanup failure must not undo a committed library import
                    # or prevent the next queue item from running.
                    self.update(jid,error='Audio imported, but a temporary copy could not be removed: '+str(path) if committed else 'Download stopped, but a temporary file could not be removed: '+str(path))

    def close(self):
        self.closing.set()
        with self.ready:
            for stop in self.stops.values():
                stop.set()
            self.ready.notify_all()
        deadline = time.monotonic() + 3
        for worker in self.threads:
            worker.join(timeout=max(0,deadline-time.monotonic()))


def manager(api):
    with MANAGER_LOCK:
        key = str(api.ROOT)
        if key not in MANAGERS:
            MANAGERS[key] = Manager(api)
        return MANAGERS[key]


def shutdown():
    for item in list(MANAGERS.values()):
        item.close()


def dispatch(api, method, params):
    item = manager(api)
    if method == 'acquisitions.list':
        result = item.list();item.ensure_worker(wake=False);return result
    if method == 'acquisitions.clear_completed':
        return {'cleared': item.clear_completed()}
    if method == 'acquisitions.clear_failed':
        return {'cleared': item.clear_failed()}
    if method == 'acquisitions.enqueue':
        return item.enqueue(params)
    if method == 'acquisitions.cancel':
        return item.cancel(params.get('id'))
    if method == 'acquisitions.retry':
        return item.retry(params.get('id'))
    raise ValueError('Unknown acquisition method')
