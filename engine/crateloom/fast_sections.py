"""Fast, local energy/texture boundaries; these are editable estimates, not semantic labels."""
import array
import json
import math
import os
from pathlib import Path
import subprocess
import sys

from .analysis_worker import write_json

VERSION = 'energy-texture-sections-1'


def estimate(energy, texture, duration, tempo=None, origin=0):
    if not energy or duration <= 0:
        return []
    step = duration / len(energy)
    radius = max(1, round(4 / step))
    def mean(values, start, end):
        selected = values[max(0, start):min(len(values), end)]
        return sum(selected) / max(1, len(selected))
    scale = max(energy) or 1
    energy = [value / scale for value in energy]
    changes = []
    for i in range(radius, len(energy) - radius):
        strength = abs(mean(energy, i, i + radius) - mean(energy, i - radius, i))
        strength += .4 * abs(mean(texture, i, i + radius) - mean(texture, i - radius, i))
        if strength >= .12:
            changes.append((strength, i * step))
    minimum = 8
    selected = [0, duration]
    phrase = 16 * 60 / tempo if tempo and 40 <= tempo <= 240 else None
    for strength, at in sorted(changes, reverse=True):
        if phrase:
            snapped = origin + round((at - origin) / phrase) * phrase
            if abs(snapped - at) <= 3:
                at = snapped
        if minimum <= at <= duration - minimum and all(abs(at - old) >= minimum for old in selected):
            selected.append(at)
        if len(selected) >= 32:
            break
    selected.sort()
    sections = []
    for index, (start, end) in enumerate(zip(selected, selected[1:])):
        level = mean(energy, int(start / step), int(end / step))
        previous = mean(energy, int(max(0, start - 8) / step), int(start / step))
        label = 'Intro' if index == 0 and len(selected) > 2 else 'Outro' if index == len(selected) - 2 and len(selected) > 2 else 'Break' if level < .25 else 'Up' if level > previous + .15 else 'Down' if previous > level + .15 else 'Other'
        sections.append({'start_us': round(start * 1e6), 'end_us': round(end * 1e6), 'label': label})
    return sections


def run(source, output, status, expected_duration):
    write_json(status, {'state': 'running', 'stage': 'Finding energy and texture changes'})
    rate = 8000
    proc = subprocess.run(['ffmpeg', '-nostdin', '-v', 'error', '-i', source, '-map', '0:a:0', '-ac', '1', '-ar', str(rate), '-f', 's16le', '-'], capture_output=True, check=True, timeout=120)
    samples = array.array('h')
    samples.frombytes(proc.stdout)
    if sys.byteorder != 'little':
        samples.byteswap()
    if not samples:
        raise ValueError('The audio decoder produced no samples')
    energy, texture = [], []
    for start in range(0, len(samples), rate // 2):
        chunk = samples[start:start + rate // 2]
        energy.append(math.sqrt(sum(value * value for value in chunk) / len(chunk)))
        texture.append(sum((left < 0) != (right < 0) for left, right in zip(chunk, chunk[1:])) / len(chunk))
    tempo, origin = None, 0
    cached = os.environ.get('CRATELOOM_FAST_BEAT_CACHE')
    if cached and Path(cached).is_file():
        data = json.loads(Path(cached).read_text())
        tempo = data.get('tempo')
        origin = next(iter(data.get('beats', [])), 0)
    duration = float(expected_duration)
    suggestions = estimate(energy, texture, duration, tempo, origin)
    write_json(output, {'version': VERSION, 'duration': duration, 'suggestions': suggestions,
                       'model': 'Local energy and texture changes', 'weight_license': 'Not applicable',
                       'warnings': ['Fast estimates follow energy and texture changes. Review boundaries and labels; vocals, verses and choruses are not identified.']})
    write_json(status, {'state': 'succeeded', 'stage': 'Complete'})


if __name__ == '__main__':
    try:
        run(*sys.argv[1:5])
    except Exception as error:
        write_json(sys.argv[3], {'state': 'failed', 'stage': 'Failed', 'error': str(error)})
        raise SystemExit(1)
