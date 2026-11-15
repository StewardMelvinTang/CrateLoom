"""Prepare local or YouTube mix audio for the optional ShazamIO runtime."""
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
import time

from . import download_http, set_scan_jobs
from .errors import detailed
from .library_imports import ImportCancelled
from .shazam_worker import MAX_DURATION, RATE_LIMIT_COOLDOWN, REQUEST_INTERVAL
from .shazam_fast import MAX_COOLDOWN

WORKERS = set()
LOCK = threading.Lock()
CLOSING = threading.Event()
SCAN_LOCK = threading.Lock()
NEXT_SCAN_AT = 0
# The worker heartbeats every 10 s, so a progress file unchanged this long means it hung.
WORKER_TIMEOUT = 240
WORKER_HARD_CAP = 3*60*60
PARTIAL_INTERVAL = 10
PACING_FILE = 'shazam-pacing.json'
PACING_LOCK = threading.Lock()
STOPPED = 'Audio recognition stopped unexpectedly. Try again.'
INSTALL = 'Install the ShazamIO scanner with scripts/setup-shazam-macos.sh, then restart CrateLoom.'


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


def scan(api, watch, path, *, offset=0, expected_duration=None, batch=True, plan=None, on_partial=None):
    global NEXT_SCAN_AT
    control = set_scan_jobs.current()
    if not SCAN_LOCK.acquire(blocking=False):
        if not control or not control.job_id:
            raise ScanBusy('A Shazam scan is already running.')
        control.publish('Waiting for the current audio section')
        while not SCAN_LOCK.acquire(timeout=.2):
            check_running()
    try:
        # Shazam's cooldown outlasts this process: another report or a restarted engine waits too.
        NEXT_SCAN_AT = max(NEXT_SCAN_AT, saved_cooldown(api))
        delay = NEXT_SCAN_AT-time.monotonic()
        if delay > 0:
            if not control or not control.job_id:
                raise ScanBusy(f'Shazam is cooling down. Try again in {int(delay)+1} seconds.')
            control.publish('Waiting for Shazam',resume_at=time.time()+delay)
            while NEXT_SCAN_AT>time.monotonic():
                check_running()
                control.stop.wait(min(.2,NEXT_SCAN_AT-time.monotonic()))
        # The sampled plan sets its own batch length; a linear scan reads the whole mix.
        result = scan_batch(api, watch, path, offset=offset, expected_duration=expected_duration,
                            batch_seconds=MAX_DURATION, plan=(plan or {}) if batch else None, on_partial=on_partial)
        NEXT_SCAN_AT = time.monotonic()+max(REQUEST_INTERVAL,result.get('cooldown') or 0)
        failure = result.get('failure')
        if isinstance(failure,dict) and failure.get('code') == 'rate_limited':
            save_cooldown(api,result.get('cooldown') or RATE_LIMIT_COOLDOWN)
        return result
    finally:
        SCAN_LOCK.release()


def video_audio(api, watch):
    from .scan_audio import obtain
    return obtain(api,watch,check_running)


def scan_batch(api, watch, path, *, offset, expected_duration, batch_seconds, plan, on_partial=None):
    python = interpreter()
    if not python:
        raise ValueError(INSTALL)
    try:
        source = Path(path).expanduser().resolve(strict=True) if path else video_audio(api,watch)
    except (FileNotFoundError, download_http.SourceHTTPError) as error:
        raise ValueError(str(error)) from None
    if not source.is_file():
        raise ValueError('Choose a local audio file.')
    return run_worker(api,python,source,offset,expected_duration,batch_seconds,plan,on_partial)


def load_pacing(api):
    try:
        value = json.loads((Path(api.ROOT)/PACING_FILE).read_text())
    except (OSError,ValueError):
        return None
    return value if isinstance(value,dict) else None


def save_pacing(api, pacing):
    if not isinstance(pacing,dict):
        return
    state = {key:pacing.get(key) for key in ('interval','episodes','limited_at')}
    try:
        with PACING_LOCK:
            # A cooldown still running stays saved; a worker cannot have started inside it.
            until = (load_pacing(api) or {}).get('limited_until')
            if type(until) in (int,float) and math.isfinite(until) and until > time.time():
                state['limited_until'] = until
            api.atomic(Path(api.ROOT)/PACING_FILE,json.dumps(state).encode())
    except OSError:
        pass


def save_cooldown(api, seconds):
    try:
        with PACING_LOCK:
            state = load_pacing(api) or {}
            state['limited_until'] = time.time()+min(seconds,MAX_COOLDOWN)
            api.atomic(Path(api.ROOT)/PACING_FILE,json.dumps(state).encode())
    except OSError:
        pass


def saved_until(root):
    """The epoch time the saved rate-limit cooldown of the library at ``root`` ends, or 0."""
    try:
        value = json.loads((Path(root)/PACING_FILE).read_text())
    except (OSError,ValueError):
        return 0
    until = value.get('limited_until') if isinstance(value,dict) else None
    if type(until) not in (int,float) or not math.isfinite(until):
        return 0
    now = time.time()
    return min(until,now+MAX_COOLDOWN) if until > now else 0


def saved_cooldown(api):
    """The monotonic time a saved rate-limit cooldown ends, or 0."""
    remaining = saved_until(api.ROOT)-time.time()
    return time.monotonic()+remaining if remaining > 0 else 0


def failure_warning(code, cooldown):
    if code == 'rate_limited':
        minutes = max(1,math.ceil((cooldown or RATE_LIMIT_COOLDOWN)/60))
        return f'Shazam is limiting requests from this Mac. The scan continues automatically in about {minutes} minute{"s" if minutes!=1 else ""}.'
    return {'bad_reply':'Shazam sent a reply CrateLoom could not read.',
            'unreachable':'CrateLoom could not reach Shazam. The scan continues when the connection is back.',
            'tls':'The secure connection to Shazam failed.'}.get(code,STOPPED)


def explain(result):
    """Replace the worker's failure code with the engine's sentence."""
    failure = result.get('failure')
    if isinstance(failure,dict) and isinstance(failure.get('code'),str):
        result['warning'] = failure_warning(failure['code'],result.get('cooldown'))
    return result


def worker_failure(stdout, stderr):
    """A stopped worker reports a code; its stderr is diagnostic text only."""
    failure = None
    for line in reversed((stdout or '').strip().splitlines()):
        try:
            failure = json.loads(line).get('failure')
            break
        except (ValueError,AttributeError):
            continue
    lines = (stderr or '').strip().splitlines()
    last = lines[-1].strip() if lines else ''
    if not isinstance(failure,dict):
        return detailed(STOPPED, last or None)
    detail = '; '.join(str(part) for part in (failure.get('detail'),last) if part)
    message = failure.get('message')
    if failure.get('code') == 'decode' and isinstance(message,str) and message.strip():
        return detailed(message.strip()[:300], detail)
    if failure.get('code') == 'runtime':
        return detailed(INSTALL, detail)
    return detailed(STOPPED, detail)


def run_worker(api, python, source, offset, expected_duration, batch_seconds, plan, on_partial=None):
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
    partial = None
    started = last_activity = time.monotonic()
    streamed = [None, started]
    def stream(progress):
        tracks = progress.get('tracks_found') if isinstance(progress,dict) else None
        if on_partial is None or (tracks == streamed[0] and time.monotonic()-streamed[1] < PARTIAL_INTERVAL):
            return
        streamed[:] = [tracks, time.monotonic()]
        try:
            on_partial(partial)
        except ImportCancelled:
            raise
        except Exception:
            # A failed intermediate save never stops the scan; the final result is saved anyway.
            pass
    def receive_progress(streaming=True):
        nonlocal last_feedback, partial, last_activity
        try:
            stamp = feedback_path.stat().st_mtime_ns
            if stamp==last_feedback:
                return
            data = json.loads(feedback_path.read_text())
            last_feedback = stamp
            last_activity = time.monotonic()
            partial = data['partial']
            progress=data['progress']
            if control:
                control.partial=partial
                if not control.stop.is_set():
                    control.publish(progress['stage'],**{k:v for k,v in progress.items() if k!='stage'})
        except (OSError,ValueError,KeyError,TypeError):
            return
        if streaming:
            stream(progress)
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
    timed_out = False
    request = json.dumps(dict(plan,pacing=load_pacing(api) or {})) if plan is not None else None
    with worker:
        try:
            sent = False
            while True:
                check_running()
                receive_progress()
                now = time.monotonic()
                if now-last_activity>WORKER_TIMEOUT or now-started>WORKER_HARD_CAP:
                    raise subprocess.TimeoutExpired(command,WORKER_TIMEOUT)
                try:
                    stdout, stderr = worker.communicate(request if not sent else None, timeout=.2)
                    receive_progress()
                    check_running()
                    break
                except subprocess.TimeoutExpired:
                    sent = True
        except ImportCancelled:
            api.stop_worker(worker)
            receive_progress(streaming=False)
            if isinstance(partial,dict) and plan is not None:
                save_pacing(api,partial.get('pacing'))
            raise
        except subprocess.TimeoutExpired:
            api.stop_worker(worker)
            receive_progress(streaming=False)
            timed_out = True
        finally:
            with LOCK:
                WORKERS.discard(worker)
            feedback.cleanup()
    if isinstance(partial,dict) and plan is not None:
        save_pacing(api,partial.get('pacing'))
    if timed_out:
        # Keep what the worker already reported instead of discarding the whole scan.
        try:
            kept = bool(partial['hits'] or partial['scanned_until']>offset or partial.get('plan',{}).get('samples'))
        except (TypeError,KeyError,AttributeError):
            kept = False
        if not kept:
            raise ValueError('The Shazam scan timed out. Try a shorter mix.')
        return dict(partial,duration=duration,warning='The Shazam scan timed out before the end of the mix. The matches found so far were kept.')
    def keep_partial():
        # The last progress may be newer than the last streamed save: write it before failing.
        if on_partial is not None and isinstance(partial,dict):
            try:
                on_partial(partial)
            except Exception:
                pass
    if worker.returncode:
        keep_partial()
        raise worker_failure(stdout,stderr)
    try:
        report = json.loads(stdout)
        if not isinstance(report,dict) or not isinstance(report.get('hits'),list):
            raise ValueError('The worker reply has no hits.')
    except ValueError as error:
        keep_partial()
        raise detailed(STOPPED,f'{type(error).__name__}: {error}') from None
    if plan is not None:
        save_pacing(api,report.get('pacing'))
    return explain(dict(report,duration=duration))
