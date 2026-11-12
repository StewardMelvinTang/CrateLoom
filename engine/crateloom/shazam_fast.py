"""Coarse-to-fine mix recognition with parallel local preparation and paced lookups."""
import asyncio
import subprocess

from .shazam_worker import RATE, WINDOW, MIN_TAIL, REQUEST_INTERVAL, RATE_LIMIT_COOLDOWN, wav_bytes, report_progress

COARSE_STEP = 60
REFINE_STEP = 20
FAST_BATCH_SECONDS = 300


def next_samples(start, end, samples):
    anchors = list(range(int(start), int(end-MIN_TAIL)+1, COARSE_STEP))
    tail = max(int(start), int(end-WINDOW))
    if end-start >= MIN_TAIL and (not anchors or tail-anchors[-1] >= REFINE_STEP):
        anchors.append(tail)
    missing = [at for at in anchors if at not in samples]
    if missing:
        return missing
    ordered = sorted(samples)
    for left, right in zip(ordered, ordered[1:]):
        if right-left <= REFINE_STEP:
            continue
        a, b = samples[left], samples[right]
        if a and b and a['key']==b['key']:
            continue
        # Probe an entirely unidentified coarse interval once. Refine further
        # where there is positive evidence for at least one recording.
        if a or b or (left in anchors and right in anchors):
            missing.append((left+right)//2)
    return missing


def timeline(start, end, samples):
    hits = []
    ordered = sorted(samples)
    for index, at in enumerate(ordered):
        match = samples[at]
        if match is None:
            continue
        left = (ordered[index-1]+at)/2 if index else start
        right = (at+ordered[index+1])/2 if index+1<len(ordered) else end
        if hits and hits[-1]['key']==match['key'] and hits[-1]['end']==left:
            hits[-1]['end'] = right
            hits[-1]['samples'] += 1
        else:
            hits.append(dict(match,start=left,end=right,samples=1))
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
    if not isinstance(response,dict):
        raise ValueError('Shazam returned an invalid response.')
    track = response.get('track')
    if not isinstance(track,dict):
        return None
    match = {'key':track.get('key'),'title':track.get('title'),'artist':track.get('subtitle')}
    if all(isinstance(value,str) and value.strip() for value in match.values()):
        return {field:value[:300] for field,value in match.items()}


async def scan(source, duration, offset, plan):
    from aiohttp_retry import ExponentialRetry
    from shazamio import Shazam
    from shazamio.client import HTTPClient
    from shazamio.exceptions import RateLimited

    end = min(duration,offset+FAST_BATCH_SECONDS)
    samples = {int(at):match for at,match in plan.get('samples',{}).items()}
    serial = bool(plan.get('serial'))
    clock = asyncio.get_running_loop()
    gate = asyncio.Lock()
    next_request = 0
    failure = None
    def publish():
        anchors = range(int(offset),int(end-MIN_TAIL)+1,COARSE_STEP)
        refining = all(at in samples for at in anchors)
        report_progress({'stage':'Refining transitions' if refining else 'Identifying tracks','completed':len(samples),
                         'total':None,'unit':'samples','position':max(samples,default=offset),'duration':duration,
                         'tracks_found':len({match['key'] for match in samples.values() if match})},
                        {'hits':[],'scanned_until':offset,'duration':duration,
                         'plan':{'samples':samples,'serial':serial}})
    publish()

    async with HTTPClient(retry_options=ExponentialRetry(attempts=1)) as client:
        recognizers = [Shazam(http_client=client) for _ in range(2)]

        async def prepare(at, recognizer):
            clip = await asyncio.to_thread(decode,source,at)
            return await recognizer.core_recognizer.recognize_bytes(value=clip,options=None) if clip else None

        async def lookup(at, signature, recognizer):
            nonlocal next_request, failure
            if signature is None:
                samples[at] = None
                publish()
                return
            async with gate:
                await asyncio.sleep(max(0,next_request-clock.time()))
                if failure is not None:
                    return
                next_request = clock.time()+REQUEST_INTERVAL
            try:
                response = await asyncio.wait_for(recognizer.send_recognize_request_v2(sig=signature),timeout=18)
                samples[at] = recording(response)
                publish()
            except Exception as error:
                if failure is None or isinstance(error,RateLimited):
                    failure = error

        while pending := next_samples(offset,end,samples):
            # Prepare two clips locally in parallel. If Shazam is healthy, a
            # slow request can overlap the next start, still two seconds apart.
            pair = list(zip(pending[:2],recognizers))
            signatures = await asyncio.gather(*(prepare(at, recognizer) for at,recognizer in pair))
            requests = [(at,signature,recognizer) for (at,recognizer),signature in zip(pair,signatures)]
            if serial:
                for request in requests:
                    await lookup(*request)
                    if failure is not None:
                        break
            else:
                await asyncio.gather(*(lookup(*request) for request in requests))
            if failure is not None:
                limited = isinstance(failure,RateLimited)
                return {'hits':[],'scanned_until':offset,
                        'plan':{'samples':samples,'serial':serial or limited},
                        'warning':'Shazam is rate limiting requests.' if limited else 'Shazam could not be reached.',
                        'cooldown':RATE_LIMIT_COOLDOWN if limited else 0}

    result = {'hits':timeline(offset,end,samples),'plan':{'samples':{},'serial':serial}}
    if end < duration:
        result.update(scanned_until=end,cooldown=REQUEST_INTERVAL)
    return result
