"""M0 feasibility probe; requires the optional librosa==0.11.0 environment.

Generates a synthetic 120 BPM click track in a temporary directory, decodes it
with FFmpeg, and estimates tempo and beats locally. This is not a music accuracy test.
"""
import array
import json
import math
import resource
import subprocess
import tempfile
import time
import wave
from pathlib import Path

import librosa
import numpy as np

rate = 22050
duration = 30
expected = np.arange(0.5, duration, 0.5)
samples = array.array('h', [0]) * (rate * duration)
for beat in expected:
    offset = round(beat * rate)
    for i in range(round(0.02 * rate)):
        samples[offset + i] = round(20000 * math.sin(2 * math.pi * 1000 * i / rate) * math.exp(-i / 100))

with tempfile.TemporaryDirectory() as temp:
    wav = Path(temp) / '120bpm.wav'
    with wave.open(str(wav), 'wb') as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(rate)
        out.writeframes(samples.tobytes())
    start = time.monotonic()
    raw = subprocess.run(['ffmpeg', '-v', 'error', '-i', str(wav), '-ac', '1', '-ar', str(rate), '-f', 'f32le', '-'], check=True, capture_output=True).stdout
    audio = np.frombuffer(raw, dtype='<f4')
    tempo, beats = librosa.beat.beat_track(y=audio, sr=rate, hop_length=256, units='time')
    elapsed = time.monotonic() - start
    tempo = float(np.asarray(tempo).item())
    beat_errors = [float(np.min(np.abs(expected - beat))) for beat in beats]
    print(json.dumps({'provider': 'librosa 0.11.0', 'fixture': '30 s generated 120 BPM click track',
                      'estimated_bpm': round(tempo, 3), 'detected_beats': len(beats),
                      'median_nearest_click_error_ms': round(float(np.median(beat_errors)) * 1000, 2) if beat_errors else None,
                      'elapsed_seconds_including_first_use': round(elapsed, 2),
                      'peak_rss_bytes_macos': resource.getrusage(resource.RUSAGE_SELF).ru_maxrss}, indent=2))
    if not 114 <= tempo <= 126 or len(beats) < 45 or np.median(beat_errors) > 0.06:
        raise SystemExit('Synthetic beat feasibility check failed')
