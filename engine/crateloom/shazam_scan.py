"""Prepare local or YouTube mix audio for the optional ShazamIO runtime."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
import time

from . import download_http, service_links, youtube_audio, set_scan_jobs
from .library_imports import ImportCancelled
from .acquisitions import AudioTransfer
from .shazam_worker import BATCH_SECONDS, MAX_DURATION, RATE_LIMIT_COOLDOWN, REQUEST_INTERVAL

WORKERS = set()
LOCK = threading.Lock()
CLOSING = threading.Event()
SCAN_LOCK = threading.Lock()
MEDIA_CACHE = None
NEXT_SCAN_AT = 0


class ScanBusy(ValueError):
    pass


def shutdown(api):
    with LOCK:
        CLOSING.set()
        workers = list(WORKERS)
    for worker in workers:
        api.stop_worker(worker)


def check_running():
    set_scan_jobs.check()
    if CLOSING.is_set():
        raise ValueError('Scan stopped when CrateLoom closed.')


def interpreter():
    path = os.environ.get('CRATELOOM_SHAZAM_PYTHON')
    if path and Path(path).is_file() and shutil.which('ffmpeg'):
        return path


def scan(api, watch, path, *, offset=0, expected_duration=None, batch=True, plan=None):
    global NEXT_SCAN_AT
    control = set_scan_jobs.current()
    if not SCAN_LOCK.acquire(blocking=False):
        if not control or not control.job_id:
            raise ScanBusy('A Shazam scan is already running.')
        control.publish('Waiting for the current audio section')
        while not SCAN_LOCK.acquire(timeout=.2):
            check_running()
    try:
        delay = NEXT_SCAN_AT-time.monotonic()
        if delay > 0:
            if not control or not control.job_id:
                raise ScanBusy(f'Shazam is cooling down. Try again in {int(delay)+1} seconds.')
            control.publish('Waiting for the recognition service')
            while NEXT_SCAN_AT>time.monotonic():
                check_running()
                control.stop.wait(min(.2,NEXT_SCAN_AT-time.monotonic()))
        result = scan_batch(api, watch, path, offset=offset, expected_duration=expected_duration,
                            batch_seconds=BATCH_SECONDS if batch else MAX_DURATION,
                            plan=(plan or {}) if batch else None)
        NEXT_SCAN_AT = time.monotonic()+max(REQUEST_INTERVAL,result.get('cooldown') or 0)
        return result
    finally:
        SCAN_LOCK.release()


def video_audio(api, watch):
    from .scan_audio import obtain
    return obtain(api,watch,check_running)


def scan_batch(api, watch, path, *, offset, expected_duration, batch_seconds, plan):
    python = interpreter()
    if not python:
        raise ValueError('Install the ShazamIO scanner with scripts/setup-shazam-macos.sh, then restart CrateLoom.')
    try:
        source = Path(path).expanduser().resolve(strict=True) if path else video_audio(api,watch)
    except (FileNotFoundError, download_http.SourceHTTPError) as error:
        raise ValueError(str(error)) from None
    if not source.is_file():
        raise ValueError('Choose a local audio file.')
    return run_worker(api,python,source,offset,expected_duration,batch_seconds,plan)


def run_worker(api, python, source, offset, expected_duration, batch_seconds, plan):
    try:
        duration, _, _ = api.probe(source)
    except (subprocess.SubprocessError, ValueError):
        raise ValueError('Could not read the mix audio.') from None
    if duration > MAX_DURATION:
        raise ValueError('Choose a mix up to 3 hours long.')
    if expected_duration is not None and abs(duration-expected_duration) > 1:
        raise ValueError('The mix length changed. Start a new scan.')
    if offset >= duration:
        raise ValueError('The saved scan position is outside the mix. Start a new scan.')
    feedback = tempfile.TemporaryDirectory(prefix='crateloom-scan-progress-')
    feedback_path = Path(feedback.name)/'progress.json'
    env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parent.parent),CRATELOOM_SCAN_PROGRESS=str(feedback_path))
    control = set_scan_jobs.current()
    if control:
        control.partial={'hits':[],'scanned_until':offset,'duration':duration,**({'plan':plan} if plan is not None else {})}
        control.publish('Identifying tracks',completed=offset,total=duration,unit='seconds')
    last_feedback = None
    def receive_progress():
        nonlocal last_feedback
        try:
            stamp = feedback_path.stat().st_mtime_ns
            if stamp==last_feedback:
                return
            data = json.loads(feedback_path.read_text())
            last_feedback = stamp
            if control:
                control.partial=data['partial']
                progress=data['progress']
                if not control.stop.is_set():
                    control.publish(progress['stage'],**{k:v for k,v in progress.items() if k!='stage'})
        except (OSError,ValueError,KeyError):
            pass
    try:
        with LOCK:
            check_running()
            if WORKERS:
                raise ValueError('A Shazam scan is already running.')
            command = [python, '-m', 'crateloom.shazam_worker', str(source), str(duration), str(offset), str(batch_seconds)]
            if plan is not None:
                command.append('fast')
            worker = subprocess.Popen(command, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                      text=True, start_new_session=os.name=='posix')
            WORKERS.add(worker)
    except BaseException:
        feedback.cleanup()
        raise
    with worker:
        try:
            deadline = time.monotonic()+45*60
            sent = False
            while True:
                check_running()
                receive_progress()
                if time.monotonic()>deadline:
                    raise subprocess.TimeoutExpired(command,45*60)
                try:
                    stdout, stderr = worker.communicate(json.dumps(plan) if plan is not None and not sent else None, timeout=.2)
                    receive_progress()
                    check_running()
                    break
                except subprocess.TimeoutExpired:
                    sent = True
        except ImportCancelled:
            api.stop_worker(worker)
            receive_progress()
            raise
        except subprocess.TimeoutExpired:
            api.stop_worker(worker)
            raise ValueError('The Shazam scan timed out. Try a shorter mix.') from None
        finally:
            with LOCK:
                WORKERS.discard(worker)
            feedback.cleanup()
    if worker.returncode:
        raise ValueError(stderr.strip().splitlines()[-1] if stderr.strip() else 'ShazamIO stopped unexpectedly.')
    report = json.loads(stdout)
    return dict(report,duration=duration)
