"""Preview-grid completion; detected analysis remains unchanged."""
import math


def complete_beats(detected, duration, bpm=None, origin=None, offset=0):
    if duration <= 0:
        return []
    if bpm and 20 <= bpm <= 400 and origin is not None:
        interval = 60 / bpm
        start = origin + offset
        first, last = math.ceil((-start-1e-7)/interval), math.ceil((duration-start)/interval)
        return [round(start+i*interval, 6) for i in range(first, last) if 0 <= round(start+i*interval, 6) < duration]
    raw = [b for i, b in enumerate(detected) if math.isfinite(b) and (i == 0 or b > detected[i-1])]
    deltas = sorted(b-a for a, b in zip(raw, raw[1:]) if .15 <= b-a <= 3)
    period = deltas[len(deltas)//2] if deltas else 60/bpm if bpm and 20 <= bpm <= 400 else 0
    if not raw or not period:
        return [round(b+offset, 6) for b in raw if 0 <= b+offset < duration]
    result = []
    def add(b):
        value = round(b+offset, 6)
        if 0 <= value < duration:
            result.append(value)
    for i in range(math.ceil((-raw[0]-offset-1e-7)/period), 0):
        add(raw[0]+i*period)
    add(raw[0])
    for a, b in zip(raw, raw[1:]):
        gap = b-a
        steps = max(1, math.floor(gap/period+.5)) if gap > period*1.75 else 1
        for j in range(1, steps+1):
            add(a+gap*j/steps)
    i = 1
    while raw[-1]+i*period+offset < duration:
        add(raw[-1]+i*period)
        i += 1
    return result
