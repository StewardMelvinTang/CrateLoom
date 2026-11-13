"""Cancellable scan contexts shared by extraction, downloads and recognition."""
from contextlib import contextmanager
from pathlib import Path
import subprocess
import tempfile
import threading
import time

from .library_imports import ImportCancelled

LOCAL = threading.local()
LOCK = threading.RLock()
CONTROLS = {}


class ScanControl:
    def __init__(self, api, report_id=None, stop=None):
        self.api = api
        self.report_id = report_id
        self.job_id = None
        # Background work shares one stop Event per report instead of one per job.
        self.stop = stop or threading.Event()
        self.lock = threading.RLock()
        self.publisher = lambda fields: None
        self.progress = {'stage': 'Preparing scan', 'completed': 0, 'total': None, 'unit': 'steps'}
        self.partial = None
        self.playlist_partial = None
        self.result = None
        self.closing = False
        self.youtube_browser = None
        # The worker's classified failure from the last recognition pass, if any.
        self.failure = None

    def attach(self, job_id, publisher):
        self.job_id = job_id
        self.publisher = publisher
        with LOCK:
            CONTROLS[job_id] = self
        publisher({'cancellable': True, 'progress': dict(self.progress)})

    def check(self):
        if self.stop.is_set():
            raise ImportCancelled('Scan cancelled')

    def publish(self, stage, **progress):
        with self.lock:
            self.check()
            self.progress = {'stage': stage, 'completed': 0, 'total': None, 'unit': 'steps', **progress}
            self.publisher({'stage': stage, 'progress': dict(self.progress)})

    def cancel(self, closing=False):
        from .preparation import JOBS
        with self.lock:
            if not self.stop.is_set():
                self.closing = closing
            self.stop.set()
            # A job that already finished keeps its final state; overwriting it
            # would leave the scan shown as stopping forever.
            job = JOBS.get(self.job_id)
            if job is None or job.get('state') in ('running', 'cancelling'):
                self.publisher({'state': 'cancelling', 'stage': 'Stopping scan'})

    @contextmanager
    def activate(self, register=True):
        # ``register=False`` lends this control's cancellation to a helper thread
        # without listing it again as the report's running scan.
        previous = getattr(LOCAL, 'control', None)
        previous_cancel = getattr(self.api.IMPORT_CONTEXT, 'cancel', None)
        LOCAL.control = self
        self.api.IMPORT_CONTEXT.cancel = self.stop
        register = register and self.report_id
        if register:
            with LOCK:
                CONTROLS[(str(Path(self.api.ROOT).resolve()), self.report_id)] = self
        try:
            yield self
        finally:
            LOCAL.control = previous
            self.api.IMPORT_CONTEXT.cancel = previous_cancel
            if register:
                with LOCK:
                    key = (str(Path(self.api.ROOT).resolve()), self.report_id)
                    if CONTROLS.get(key) is self:
                        CONTROLS.pop(key, None)

    def run(self, fn):
        with self.activate():
            self.check()
            return fn()

    def finish(self):
        with LOCK:
            CONTROLS.pop(self.job_id, None)


def current():
    return getattr(LOCAL, 'control', None)


def check():
    control = current()
    if control:
        control.check()


def progress(stage, **fields):
    control = current()
    if control:
        control.publish(stage, **fields)


def for_report(api, report_id):
    with LOCK:
        return CONTROLS.get((str(Path(api.ROOT).resolve()), report_id))


def for_database(db, report_id):
    database = db.execute('PRAGMA database_list').fetchone()[2]
    with LOCK:
        return CONTROLS.get((str(Path(database).resolve().parent), report_id))


def start(api, fn, report_id=None):
    from .preparation import launch
    if report_id and for_report(api,report_id):
        raise ValueError('This audio scan is already running.')
    return launch(fn, control=ScanControl(api, report_id))


def cancel(job_id):
    from .preparation import JOBS
    with LOCK:
        control = CONTROLS.get(job_id)
    if control:
        control.cancel()
    if job_id not in JOBS or not JOBS[job_id].get('cancellable'):
        raise ValueError('This scan is no longer available.')
    return dict(JOBS[job_id])


def shutdown():
    with LOCK:
        controls = list(set(CONTROLS.values()))
    for control in controls:
        control.cancel(closing=True)
    # Source discovery is not a scan job, but it runs yt-dlp under the same cancellation.
    from .set_discovery import stop_all
    stop_all()


def run_process(api, args, timeout, on_line=lambda line: None, on_poll=lambda: None):
    """Drain tool output to bounded files while keeping cancellation responsive."""
    with tempfile.TemporaryDirectory(prefix='crateloom-scan-process-') as folder:
        output, errors = Path(folder)/'stdout', Path(folder)/'stderr'
        with output.open('wb') as out, errors.open('wb') as err:
            worker = subprocess.Popen(args, stdout=out, stderr=err, start_new_session=True)
            position = 0
            pending = ''
            deadline = time.monotonic()+timeout
            try:
                while True:
                    check()
                    api.check_import_cancel()
                    on_poll()
                    if output.stat().st_size > 2*1024*1024 or errors.stat().st_size > 2*1024*1024:
                        raise ValueError('The audio service returned too much diagnostic data.')
                    with output.open('rb') as reader:
                        reader.seek(position)
                        chunk = reader.read()
                        position = reader.tell()
                    pending += chunk.decode('utf-8', errors='replace')
                    lines = pending.split('\n')
                    pending = lines.pop()
                    for line in lines:
                        on_line(line)
                    if worker.poll() is not None:
                        if pending:
                            on_line(pending)
                        if worker.returncode:
                            raise subprocess.CalledProcessError(worker.returncode,args,output.read_text(errors='replace'),errors.read_text(errors='replace'))
                        return
                    if time.monotonic()>deadline:
                        raise subprocess.TimeoutExpired(args,timeout)
                    time.sleep(.1)
            finally:
                if worker.poll() is None:
                    api.stop_worker(worker)
