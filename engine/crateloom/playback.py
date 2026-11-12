"""Webview-compatible playback copies; original audio is never changed."""
import hashlib
import os
from pathlib import Path
import subprocess
import threading
import uuid

LOCK = threading.Lock()
PATH_LOCKS = {}


def prepare(api, track):
    source = Path(track['path'])
    before = source.stat()
    key = hashlib.sha256(f'{source}:{before.st_mtime_ns}:{before.st_size}:pcm-v1'.encode()).hexdigest()
    target = api.ROOT / 'playback-cache' / (key + '.wav')
    with LOCK:
        lock = PATH_LOCKS.setdefault(str(target), threading.Lock())
    with lock:
        if not target.is_file():
            target.parent.mkdir(parents=True, exist_ok=True)
            partial = target.with_name(uuid.uuid4().hex + '.partial.wav')
            try:
                subprocess.run(['ffmpeg', '-nostdin', '-v', 'error', '-i', str(source), '-map', '0:a:0', '-vn', '-ac', '2', '-ar', '44100', '-c:a', 'pcm_s16le', str(partial)], check=True, capture_output=True, timeout=180)
                after = source.stat()
                if (before.st_mtime_ns, before.st_size) != (after.st_mtime_ns, after.st_size):
                    raise ValueError('The audio changed while preparing playback. Select the track again.')
                os.replace(partial, target)
            finally:
                partial.unlink(missing_ok=True)
    return {'track_id': track['id'], 'path': str(target)}
