"""Isolated offline analysis worker. Writes results to files, never the library DB."""
import base64
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

VERSION = 'librosa-0.11.0-grid-6'

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

def steady_grid(np, beats, envelope, frame, duration):
    """Fit one constant tempo to the tracked beats.

    The tracker reports a coarse tempo (its candidates are several BPM apart)
    and places each beat on a nearby onset. Most DJ material holds one tempo,
    so a straight grid fitted through those beats is both more precise and
    what the track will be mixed against. Returns None when too little was
    tracked; `stable` says whether the track actually follows the grid.
    """
    beats = np.asarray(beats, dtype=float)
    if len(beats) < 16 or duration <= 0:
        return None
    steps = np.diff(beats)
    period = float(np.median(steps))
    if not .15 <= period <= 3:
        return None
    # Count beats by the typical step so a skipped beat does not shift the rest.
    index = np.concatenate(([0], np.cumsum(np.maximum(1, np.rint(steps / period))))).astype(int)
    span = max(8, min(64, len(beats) // 4))
    # The median slope over long spans ignores an occasional slipped beat.
    interval = float(np.median((beats[span:] - beats[:-span]) / (index[span:] - index[:-span])))
    if not .15 <= interval <= 3:
        return None
    # Dominant phase: a slip moves later beats to another phase, so vote.
    phases = (beats - index * interval) % interval
    votes, edges = np.histogram(phases, bins=48, range=(0, interval))
    centre = (edges[np.argmax(votes)] + edges[np.argmax(votes) + 1]) / 2
    near = np.abs((phases - centre + interval / 2) % interval - interval / 2) <= interval * .15
    start = centre + float(np.median((phases[near] - centre + interval / 2) % interval - interval / 2)) if near.any() else centre
    # Refine against the onset envelope itself, pivoting mid-track so a small
    # tempo change does not swing the whole grid away from its phase.
    pivot = start + round((duration / 2 - start) / interval) * interval
    frames = np.arange(len(envelope))
    def score(value, anchor):
        grid = np.arange(anchor - np.floor(anchor / value) * value, duration, value)
        return float(np.interp(grid / frame, frames, envelope).mean()) if len(grid) else 0.
    def best_shift(value):
        return max(((score(value, pivot + shift), shift) for shift in np.linspace(-.02, .02, 9)), key=lambda item: item[0])
    best = max(((*best_shift(value), value) for value in interval * (1 + np.linspace(-.003, .003, 61))), key=lambda item: item[0])
    strength, shift, interval = best
    bpm = 60 / interval
    # Produced music is almost always an integer tempo; prefer it when the
    # audio supports it equally well.
    whole = round(bpm)
    if whole and abs(bpm - whole) <= .05:
        rounded, rounded_shift = best_shift(60 / whole)
        if rounded >= strength * .985:
            interval, shift, bpm = 60 / whole, rounded_shift, float(whole)
    pivot += shift
    origin = pivot + round((beats[0] - pivot) / interval) * interval
    while origin < 0:
        origin += interval
    # A tracker that slips onto the off-beat for a passage still follows the
    # same tempo, so beats on either half count. Drifting tempo spreads them out.
    half = interval / 2
    agreement = float(np.mean(np.abs((beats - origin + half / 2) % half - half / 2) <= .035))
    return {'bpm': round(float(bpm), 3), 'origin': round(float(origin), 6), 'agreement': round(agreement, 3),
            'stable': bool(agreement >= .8 and len(beats) >= 32 and duration >= 30)}


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
    envelope = librosa.onset.onset_strength(y=y, sr=rate, hop_length=256)
    tempo, beats = librosa.beat.beat_track(onset_envelope=envelope, sr=rate, hop_length=256, units='time')
    bpm = float(np.asarray(tempo).item())
    limit = min(len(y) / rate, float(expected_duration))
    beat_values = [value for value in (round(float(x), 6) for x in beats) if 0 <= value < limit]
    stage('Fitting tempo grid')
    grid = steady_grid(np, beat_values, envelope, 256 / rate, limit)
    if grid and not 0 <= grid['origin'] < limit:
        grid = None
    if grid and grid['stable']:
        bpm = grid['bpm']
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
              'beats': beat_values, 'grid': grid, 'downbeats': None, 'sections': None,
              'waveform': {'signal':signal, 'peaks': [round(float(v), 4) for v in peaks], 'rgb': [[round(float(v), 3) for v in row] for row in colours]},
              'warnings': ['Beat positions are estimates; downbeats, meter and song sections are not detected.']
                          + ([] if grid and grid['stable'] else ['The tempo is not steady enough for a straight grid; detected beats are shown.'])}
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
