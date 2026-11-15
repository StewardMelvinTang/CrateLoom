"""Webview-compatible playback copies; original audio is never changed."""
import hashlib
import os
from pathlib import Path
import subprocess
import threading
import time
import uuid

LOCK = threading.Lock()
PATH_LOCKS = {}
MAX_CACHE_BYTES = 4 * 1024 ** 3


def prune(folder, keep):
    """Evict least-recently-used playback copies until the cache fits its cap."""
    copies = []
    for path in folder.glob('*.wav'):
        try:
            stat = path.stat()
        except OSError:
            continue
        if path.name.endswith('.partial.wav'):
            # Left by an interrupted conversion; a live one is minutes old at most.
            if time.time() - stat.st_mtime > 3600:
                path.unlink(missing_ok=True)
        else:
            copies.append((stat.st_mtime, stat.st_size, path))
    total = sum(size for _, size, _ in copies)
    for _, size, path in sorted(copies, key=lambda copy: copy[0]):
        if total <= MAX_CACHE_BYTES:
            break
        if path != keep:
            path.unlink(missing_ok=True)
            total -= size


def prepare(api, track):
    source = Path(track['path'])
    try:
        before = source.stat()
    except FileNotFoundError:
        raise ValueError('Audio file is missing; restore or relink it before playing.') from None
    key = hashlib.sha256(f'{source}:{before.st_mtime_ns}:{before.st_size}:pcm-v1'.encode()).hexdigest()
    target = api.ROOT / 'playback-cache' / (key + '.wav')
    with LOCK:
        lock = PATH_LOCKS.setdefault(str(target), threading.Lock())
    with lock:
        if target.is_file():
            # Mark the hit as recently used so pruning keeps what is being played.
            try:
                os.utime(target)
            except OSError:
                pass
        else:
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
    try:
        prune(target.parent, target)
    except OSError:
        pass
    return {'track_id': track['id'], 'path': str(target)}
