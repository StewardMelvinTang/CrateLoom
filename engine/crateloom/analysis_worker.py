"""Isolated offline analysis worker. Writes results to files, never the library DB."""
import base64
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

VERSION = 'librosa-0.11.0-rgb-5'

def write_json(path, value):
    path = Path(path)
    fd, tmp = tempfile.mkstemp(prefix='.analysis-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            json.dump(value, f, separators=(',', ':'))
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp): os.unlink(tmp)

def run(source, output, status, expected_duration):
    def stage(name): write_json(status, {'state': 'running', 'stage': name})
    stage('Loading analyzer')
    import numpy as np
    import librosa
    import scipy
    import numba
    if (librosa.__version__, np.__version__, scipy.__version__, numba.__version__) != ('0.11.0', '2.5.4', '1.18.1', '0.68.1'):
        raise RuntimeError('Install the pinned librosa 0.11.0 environment using scripts/setup-analysis-macos.sh')
    stage('Decoding audio')
    rate = 22050
    proc = subprocess.run(['ffmpeg', '-v', 'error', '-i', source, '-map', '0:a:0', '-ac', '1', '-ar', str(rate), '-f', 'f32le', '-'], check=True, capture_output=True, timeout=900)
    y = np.frombuffer(proc.stdout, dtype='<f4').copy()
    if not len(y): raise ValueError('The audio decoder produced no samples')
    stage('Estimating beats')
    tempo, beats = librosa.beat.beat_track(y=y, sr=rate, hop_length=256, units='time')
    bpm = float(np.asarray(tempo).item())
    beat_values = [value for value in (round(float(x), 6) for x in beats) if 0 <= value < min(len(y) / rate, float(expected_duration))]
    stage('Building frequency waveform')
    # STFT-derived low/mid/high energy (not section or instrument labels).
    hop = 512
    spec = np.abs(librosa.stft(y, n_fft=2048, hop_length=hop))
    freqs = librosa.fft_frequencies(sr=rate, n_fft=2048)
    bands = []
    for low, high in [(20, 250), (250, 2000), (2000, rate / 2 + 1)]:
        selected = spec[(freqs >= low) & (freqs < high)]
        bands.append(np.sqrt(np.mean(selected ** 2, axis=0)) if len(selected) else np.zeros(spec.shape[1]))
    # Fine RMS windows retain micro-dynamics on mastered/clipped recordings.
    # Normalize the display envelope per track, never the playback audio.
    peak_hop = 16
    starts = np.arange(0, len(y), peak_hop)
    counts = np.minimum(peak_hop, len(y) - starts)
    energy = np.sqrt(np.add.reduceat(y * y, starts) / counts)
    maximum = float(np.max(energy)) if len(energy) else 0
    peaks = energy / maximum if maximum > 0 else np.zeros_like(energy)
    # Spectral color is independently sampled at STFT frame resolution; it is
    # not expanded per amplitude window, keeping the cache much smaller.
    colours = np.stack(bands, axis=1)
    scale = float(np.max(colours)) if colours.size else 0
    if scale > 0: colours = np.sqrt(colours / scale)
    else: colours = np.zeros_like(colours)
    stage('Building signal detail')
    signal_hop = 8
    indices = np.arange(0, len(y), signal_hop)
    normalizer = max(float(np.max(np.abs(y))), 1e-9)
    extrema = np.stack((np.minimum.reduceat(y, indices), np.maximum.reduceat(y, indices)), axis=1)
    packed = np.rint(np.clip(extrema / normalizer, -1, 1) * 32767).astype('<i2')
    signal = {'encoding':'i16le-minmax', 'hop':signal_hop, 'rate':rate,
              'data':base64.b64encode(packed.tobytes()).decode('ascii')}
    result = {'version': VERSION, 'duration': len(y) / rate, 'tempo': round(bpm, 3) if bpm > 0 and np.isfinite(bpm) else None,
              'beats': beat_values, 'downbeats': None, 'sections': None,
              'waveform': {'signal':signal, 'peaks': [round(float(v), 4) for v in peaks], 'rgb': [[round(float(v), 3) for v in row] for row in colours]},
              'warnings': ['Beat positions are estimates; downbeats, meter and song sections are not detected.']}
    write_json(output, result)
    write_json(status, {'state': 'succeeded', 'stage': 'Complete'})

if __name__ == '__main__':
    try:
        run(*sys.argv[1:5])
    except Exception as exc:
        message = str(exc)
        if isinstance(exc, (ImportError, ModuleNotFoundError)):
            message += '. Run scripts/setup-analysis-macos.sh and restart the app.'
        write_json(sys.argv[3], {'state': 'failed', 'stage': 'Failed', 'error': message})
        print(str(exc), file=sys.stderr)
        raise SystemExit(1)
