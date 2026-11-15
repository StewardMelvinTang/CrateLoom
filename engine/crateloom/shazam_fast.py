"""Coarse-to-fine mix recognition: local preparation runs ahead of one paced lookup at a time."""
import asyncio
import math
from pathlib import Path
import random
import subprocess
import time

from .shazam_worker import (RATE, WINDOW, MIN_TAIL, MAX_DURATION, REQUEST_INTERVAL, classify, failure_detail,
                            wav_bytes, report_progress)

COARSE_STEP = 60
REFINE_STEP = 20
# Shazam throttled at 2 s, so pacing never starts faster; it speeds up only after clean runs.
MIN_INTERVAL = 1.2
MAX_INTERVAL = 8
SPEEDUP_RUN = 15
SPEEDUP_STEP = .1
LIMIT_PAUSE = 60
MAX_COOLDOWN = 900
EPISODE_MEMORY = 3600
HEARTBEAT = 10
PREPARE_AHEAD = 2
HINT_COVER = 30
AGREE_SECONDS = 3
BAD_REPLY_LIMIT = 3
BAD_REPLY_PAUSE = (3, 5)
REQUEST_TIMEOUT = 18


class BadReply(Exception):
    pass


def estimated_start(at, match):
    offset = match.get('offset') if match else None
    return at-offset if type(offset) in (int,float) else None


def next_samples(start, end, samples, hints=()):
    hinted = [at for at in hints if start <= at <= end-MIN_TAIL and at not in samples]
    if hinted:
        return hinted
    anchors = list(range(int(start), int(end-MIN_TAIL)+1, COARSE_STEP))
    tail = max(int(start), int(end-WINDOW))
    if end-start >= MIN_TAIL and (not anchors or tail-anchors[-1] >= REFINE_STEP):
        anchors.append(tail)
    # A published list already places one sample per entry; an anchor next to one adds nothing.
    covered = (lambda at: any(abs(at-other) <= HINT_COVER for other in samples)) if hints else (lambda at: at in samples)
    missing = [at for at in anchors if not covered(at)]
    if missing:
        return missing
    ordered = sorted(samples)
    for left, right in zip(ordered, ordered[1:]):
        if right-left <= REFINE_STEP:
            continue
        a, b = samples[left], samples[right]
        if a and b and a['key']==b['key']:
            continue
        # Shazam's offset into the right-hand recording places its start, the earliest the
        # track can enter. One lookup just after that start checks the track is already
        # playing there; if a DJ cued it in later, the rest of the interval is bisected.
        begins = estimated_start(right, b)
        if begins is not None and left < begins <= right:
            if right-begins > REFINE_STEP:
                missing.append(int(begins+REFINE_STEP/2))
            continue
        # Probe an entirely unidentified coarse interval once. Refine further
        # where there is positive evidence for at least one recording.
        if a or b or (left in anchors and right in anchors):
            missing.append((left+right)//2)
    return missing


def boundary(left, right, samples):
    begins = estimated_start(right, samples[right])
    a = samples[left]
    if begins is not None and left < begins <= right and not (a and a['key']==samples[right]['key']):
        return begins
    return (left+right)/2


def timeline(start, end, samples):
    hits = []
    ordered = sorted(samples)
    starts = []
    for index, at in enumerate(ordered):
        match = samples[at]
        if match is None:
            continue
        left = boundary(ordered[index-1], at, samples) if index else start
        right = boundary(at, ordered[index+1], samples) if index+1<len(ordered) else end
        begins = estimated_start(at, match)
        # Neighbouring samples join one hit; across an unidentified sample they join
        # only when both lookups place the recording at the same start.
        agrees = begins is not None and hits and any(abs(begins-other) <= AGREE_SECONDS for other in starts[-1])
        if hits and hits[-1]['key']==match['key'] and (hits[-1]['end']==left or agrees):
            hits[-1]['end'] = right
            hits[-1]['samples'] += 1
        else:
            hits.append(dict(match,start=left,end=right,samples=1))
            starts.append([])
        if begins is not None:
            starts[-1].append(begins)
    for hit, estimates in zip(hits, starts):
        # Two lookups that place the recording at the same start confirm it.
        agree = any(abs(a-b) <= AGREE_SECONDS for i, a in enumerate(estimates) for b in estimates[i+1:])
        hit['confirmed'] = hit['samples'] >= 2 or agree
    return hits


def decode(source, at):
    try:
        result = subprocess.run(['ffmpeg','-nostdin','-v','error','-ss',str(at),'-i',str(source),
                                 '-t',str(WINDOW),'-map','0:a:0','-ac','1','-ar',str(RATE),'-f','s16le','-'],
                                capture_output=True,check=True,timeout=30)
    except subprocess.SubprocessError:
        raise ValueError('FFmpeg could not decode the mix audio.') from None
    return wav_bytes(result.stdout) if len(result.stdout)>=MIN_TAIL*RATE*2 else None


def recording(response):
    # A reply without either field is a server error body, not a miss.
    if not isinstance(response,dict) or ('track' not in response and 'matches' not in response):
        raise BadReply('Shazam returned a reply without a result.')
    track = response.get('track')
    if not isinstance(track,dict):
        return None
    match = {'key':track.get('key'),'title':track.get('title'),'artist':track.get('subtitle')}
    if not all(isinstance(value,str) and value.strip() for value in match.values()):
        return None
    match = {field:value[:300] for field,value in match.items()}
    matches = response.get('matches')
    first = matches[0] if isinstance(matches,list) and matches and isinstance(matches[0],dict) else {}
    for field, name, limit in (('offset','offset',4*60*60),('skew','timeskew',1)):
        value = first.get(name)
        if type(value) in (int,float) and math.isfinite(value) and abs(value) <= limit:
            match[field] = float(value)
    if match.get('offset',0) < 0:
        match.pop('offset')
    return match


def pacing_state(value, now=None):
    now = time.time() if now is None else now
    floor = min(MIN_INTERVAL, REQUEST_INTERVAL)
    state = {'interval':REQUEST_INTERVAL,'episodes':0,'limited_at':None}
    if isinstance(value,dict):
        interval, episodes, limited = value.get('interval'), value.get('episodes'), value.get('limited_at')
        if type(interval) in (int,float) and math.isfinite(interval):
            state['interval'] = min(MAX_INTERVAL, max(floor, float(interval)))
        recent = False
        if type(limited) in (int,float) and math.isfinite(limited) and limited <= now:
            state['limited_at'] = float(limited)
            recent = now-limited < EPISODE_MEMORY
            # Throttling episodes are forgotten after an hour without one.
            if type(episodes) is int and episodes > 0 and recent:
                state['episodes'] = min(episodes, 16)
        if not recent:
            # A slowdown is forgotten with its episode; a pace learned from clean runs is kept.
            state['interval'] = min(state['interval'], REQUEST_INTERVAL)
    return state


def cooldown_for(episodes):
    return min(MAX_COOLDOWN, 90*2**max(0,episodes-1))


async def scan(source, duration, offset, plan, batch_seconds=MAX_DURATION):
    from aiohttp_retry import ExponentialRetry
    from shazamio import Shazam
    from shazamio.client import HTTPClient

    if not Path(source).is_file():
        raise ValueError('FFmpeg could not decode the mix audio.')
    end = min(duration,offset+batch_seconds)
    samples = {int(at):match for at,match in plan.get('samples',{}).items()}
    serial = bool(plan.get('serial'))
    hints = sorted({int(at) for at in plan.get('hints') or () if type(at) in (int,float) and math.isfinite(at)})
    pacing = pacing_state(plan.get('pacing'))
    floor = min(MIN_INTERVAL, REQUEST_INTERVAL)
    clock = asyncio.get_running_loop()
    next_request = 0
    waiting_until = None

    def current_plan(finished=False):
        if finished:
            return {'samples':{},'serial':serial,**({'hints':[at for at in hints if at>=end]} if any(at>=end for at in hints) else {})}
        return {'samples':samples,'serial':serial,**({'hints':hints} if hints else {})}

    def publish():
        anchors = range(int(offset),int(end-MIN_TAIL)+1,COARSE_STEP)
        refining = all(at in samples for at in anchors)
        stage = 'Waiting for Shazam' if waiting_until else 'Refining transitions' if refining else 'Identifying tracks'
        report_progress({'stage':stage,'completed':len(samples),'total':None,'unit':'samples',
                         'position':max(samples,default=offset),'duration':duration,
                         'tracks_found':len({match['key'] for match in samples.values() if match}),
                         **({'resume_at':waiting_until} if waiting_until else {})},
                        {'hits':[],'scanned_until':offset,'duration':duration,'plan':current_plan(),'pacing':dict(pacing)})

    def failed(code, error, cooldown):
        result = {'hits':[],'scanned_until':offset,'plan':current_plan(),'pacing':dict(pacing),
                  'failure':{'code':code,'detail':failure_detail(error)}}
        if cooldown is not None:
            result['cooldown'] = cooldown
        return result

    async def heartbeat():
        # The engine stops a worker whose progress file stays unchanged for minutes.
        while True:
            await asyncio.sleep(HEARTBEAT)
            publish()

    async def pause(seconds):
        nonlocal waiting_until
        waiting_until = time.time()+seconds
        publish()
        try:
            await asyncio.sleep(seconds)
        finally:
            waiting_until = None
        publish()

    publish()
    beat = asyncio.ensure_future(heartbeat())
    prepared = {}
    try:
        async with HTTPClient(retry_options=ExponentialRetry(attempts=1)) as client:
            recognizer = Shazam(http_client=client)
            signers = asyncio.Queue()
            for _ in range(PREPARE_AHEAD+1):
                signers.put_nowait(Shazam(http_client=client))
            decoded = [0, 0]

            async def prepare(at):
                try:
                    clip = await asyncio.to_thread(decode,source,at)
                except ValueError as error:
                    decoded[1] += 1
                    return 'decode', error
                decoded[0] += 1
                if not clip:
                    return 'silent', None
                signer = await signers.get()
                try:
                    return 'ok', await signer.core_recognizer.recognize_bytes(value=clip,options=None)
                except Exception as error:
                    return 'decode', error
                finally:
                    signers.put_nowait(signer)

            async def lookup(signature):
                nonlocal next_request
                # Start-to-start pacing with one request in flight.
                await asyncio.sleep(max(0,next_request-clock.time()))
                next_request = clock.time()+pacing['interval']
                response = await asyncio.wait_for(recognizer.send_recognize_request_v2(sig=signature),timeout=REQUEST_TIMEOUT)
                return recording(response)

            successes = 0
            bad_streak = 0
            retried = set()
            # Samples answered twice with an unreadable reply. They count as misses only once
            # another lookup succeeds; during an outage they stay unanswered for a later pass.
            doubtful = set()
            limited_here = False

            def settle_doubtful():
                for at in doubtful:
                    samples[at] = None
                doubtful.clear()

            while pending := next_samples(offset,end,{**samples,**dict.fromkeys(doubtful)},hints):
                for at in pending[:PREPARE_AHEAD+1]:
                    if at not in prepared:
                        prepared[at] = asyncio.ensure_future(prepare(at))
                for at in [at for at in prepared if at not in pending[:PREPARE_AHEAD+1]]:
                    prepared.pop(at).cancel()
                at = pending[0]
                kind, signature = await prepared.pop(at)
                if kind != 'ok':
                    if kind == 'decode' and not decoded[0] and decoded[1] >= 3:
                        raise ValueError('FFmpeg could not decode the mix audio.')
                    # Unreadable or silent audio is skipped, never retried.
                    samples[at] = None
                    publish()
                    continue
                try:
                    match = await lookup(signature)
                except Exception as error:
                    code = 'bad_reply' if isinstance(error,BadReply) else classify(error)
                    if code == 'bad_reply':
                        bad_streak += 1
                        if bad_streak < BAD_REPLY_LIMIT:
                            if at in retried:
                                doubtful.add(at)
                            else:
                                retried.add(at)
                                await pause(random.uniform(*BAD_REPLY_PAUSE))
                            publish()
                            continue
                    if code in ('rate_limited','bad_reply'):
                        # Repeated unreadable replies are treated as throttling, and their samples are asked again.
                        bad_streak = 0
                        successes = 0
                        retried.clear()
                        doubtful.clear()
                        pacing['episodes'] += 1
                        pacing['limited_at'] = time.time()
                        pacing['interval'] = min(MAX_INTERVAL, max(pacing['interval']*2, floor))
                        if not limited_here:
                            limited_here = True
                            await pause(LIMIT_PAUSE*random.uniform(.8,1.2))
                            continue
                        return failed('rate_limited', error, cooldown_for(pacing['episodes']))
                    return failed(code, error, {'unreachable':15,'internal':15}.get(code))
                bad_streak = 0
                # Shazam answers again, so a sample it could not answer twice is a miss of its own.
                settle_doubtful()
                samples[at] = match
                successes += 1
                if successes >= SPEEDUP_RUN:
                    successes = 0
                    pacing['interval'] = max(floor, round(pacing['interval']-SPEEDUP_STEP, 3))
                publish()
        # The pass ended before the unreadable streak reached the throttling limit.
        settle_doubtful()
    finally:
        beat.cancel()
        for task in prepared.values():
            task.cancel()

    result = {'hits':timeline(offset,end,samples),'plan':current_plan(finished=True),'pacing':dict(pacing)}
    if end < duration:
        result.update(scanned_until=end,cooldown=REQUEST_INTERVAL)
    return result
