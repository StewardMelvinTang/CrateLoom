"""Read overlapping slices of a mix and query ShazamIO in an isolated runtime."""
import asyncio
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import wave

RATE = 16000
WINDOW = 12
STEP = 10
MIN_TAIL = 6
MAX_DURATION = 3 * 60 * 60
REQUEST_INTERVAL = 2
RATE_LIMIT_COOLDOWN = 90


def report_progress(progress, partial):
    target = os.environ.get('CRATELOOM_SCAN_PROGRESS')
    if target:
        path = Path(target)
        temporary = path.with_suffix('.tmp')
        temporary.write_text(json.dumps({'progress':progress,'partial':partial}))
        temporary.replace(path)


def wav_bytes(pcm):
    buffer = io.BytesIO()
    with wave.open(buffer, 'wb') as wav:
        wav.setparams((1, 2, RATE, 0, 'NONE', 'not compressed'))
        wav.writeframes(pcm)
    return buffer.getvalue()


def windows(stream, start=0):
    window_bytes = WINDOW * RATE * 2
    step_bytes = STEP * RATE * 2
    data = stream.read(window_bytes)
    while len(data) >= MIN_TAIL * RATE * 2:
        yield start, len(data) / (RATE * 2), wav_bytes(data)
        if len(data) < window_bytes:
            break
        data = data[step_bytes:] + stream.read(step_bytes)
        start += STEP


def add_match(hits, start, length, result):
    track = result.get('track')
    if not isinstance(track, dict):
        return
    title, artist, key = (track.get(field) for field in ('title', 'subtitle', 'key'))
    if not all(isinstance(value, str) and value.strip() for value in (title, artist, key)):
        return
    # Use one step per result. Overlap supplies more audio to the recognizer
    # without attributing the same seconds to adjacent matches.
    end = start + min(length, STEP)
    if hits and hits[-1]['key'] == key and hits[-1]['end'] == start:
        hits[-1]['end'] = end
        hits[-1]['samples'] += 1
    else:
        hits.append(dict(key=key, start=start, end=end, title=title, artist=artist, samples=1))


async def scan(source, duration, offset=0, batch_seconds=MAX_DURATION):
    from aiohttp_retry import ExponentialRetry
    from shazamio import Shazam
    from shazamio.client import HTTPClient
    from shazamio.exceptions import RateLimited

    if not 0 <= offset < duration <= MAX_DURATION:
        raise ValueError('Choose a mix up to 3 hours long.')
    hits = []
    end = min(duration, offset+batch_seconds)
    decode_end = min(duration, end+WINDOW-STEP)
    checked = 0
    report_progress({'stage':'Identifying tracks','completed':offset,'total':duration,'unit':'seconds','samples':0},
                    {'hits':[],'scanned_until':offset,'duration':duration})
    args = ['ffmpeg', '-nostdin', '-v', 'error', '-ss', str(offset), '-i', str(source), '-t', str(decode_end-offset),
            '-map', '0:a:0', '-ac', '1', '-ar', str(RATE), '-f', 's16le', '-']
    with subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL) as decoder:
        try:
            async with HTTPClient(retry_options=ExponentialRetry(attempts=1)) as client:
                shazam = Shazam(http_client=client)
                next_request = 0
                clock = asyncio.get_running_loop()
                for start, length, clip in windows(decoder.stdout, offset):
                    await asyncio.sleep(max(0, next_request - clock.time()))
                    next_request = clock.time() + REQUEST_INTERVAL
                    try:
                        result = await asyncio.wait_for(shazam.recognize(clip), timeout=18)
                    except Exception as error:
                        message = ('Shazam is rate limiting requests. Try again later.' if isinstance(error, RateLimited)
                                   else 'ShazamIO could not reach Shazam or read its response. Try again later.')
                        return {'hits':hits,'scanned_until':start,'warning':message,
                                'cooldown':RATE_LIMIT_COOLDOWN if isinstance(error, RateLimited) else 0}
                    if not isinstance(result, dict):
                        raise ValueError('ShazamIO returned an invalid response.')
                    add_match(hits, start, length, result)
                    checked += 1
                    through = min(end,start+min(length,STEP))
                    report_progress({'stage':'Identifying tracks','completed':through,'total':duration,'unit':'seconds',
                                     'samples':checked,'tracks_found':len(hits)},
                                    {'hits':hits,'scanned_until':through,'duration':duration})
            if decoder.wait() != 0:
                raise ValueError('FFmpeg could not decode the mix audio.')
        finally:
            if decoder.poll() is None:
                decoder.kill()
                decoder.wait()
    if end < duration:
        return {'hits':hits,'scanned_until':end,'cooldown':REQUEST_INTERVAL}
    return {'hits':hits}


if __name__ == '__main__':
    try:
        offset = float(sys.argv[3]) if len(sys.argv) > 3 else 0
        batch_seconds = int(sys.argv[4]) if len(sys.argv) > 4 else MAX_DURATION
        if len(sys.argv) > 5 and sys.argv[5] == 'fast':
            from .shazam_fast import scan as scan_fast
            result = scan_fast(Path(sys.argv[1]),float(sys.argv[2]),offset,json.load(sys.stdin))
        else:
            result = scan(Path(sys.argv[1]),float(sys.argv[2]),offset,batch_seconds)
        print(json.dumps(asyncio.run(result)))
    except (ValueError, OSError, ImportError) as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1)
    except subprocess.SubprocessError:
        print('FFmpeg could not decode the mix audio.', file=sys.stderr)
        raise SystemExit(1)
