"""Optional non-commercial All-In-One inference. Results are review suggestions."""
import json
import math
from importlib.metadata import distribution
from pathlib import Path
import shutil
import subprocess
import sys
from .analysis_worker import write_json

VERSION = 'allin1-infer-566f96c3-harmonix-all-v1'
MAX_DURATION = 30 * 60
MAX_SECTIONS = 1024
WINDOW_SECONDS = 180
CONTEXT_SECONDS = 12


def analyze_sections(session, source, work, status, duration):
    count = math.ceil(duration / WINDOW_SECONDS) if duration > 300 else 1
    window = math.ceil(duration / count)
    allowed = {'intro','verse','chorus','bridge','outro','break','instrumental','solo','other'}
    segments = []
    for i in range(count):
        start = i * window
        end = min(duration, start + window) if count > 1 else duration
        offset = max(0, start - CONTEXT_SECONDS)
        clip = source
        stage = 'Separating and analyzing sections'
        if count > 1:
            stage = f'Analyzing sections · {i + 1}/{count}'
            clip = work / f'window-{i}.wav'
        write_json(status, {'state':'running', 'stage':stage})
        if count > 1:
            clip_end = min(duration, end + CONTEXT_SECONDS)
            subprocess.run(['ffmpeg', '-nostdin', '-v', 'error', '-ss', str(offset), '-i', str(source),
                            '-t', str(clip_end - offset), '-map', '0:a:0', '-ac', '2', '-ar', '44100',
                            str(clip)], check=True, capture_output=True, timeout=120)
        result = session.infer(clip, demix_dir=str(work/'demix'), spec_dir=str(work/'spec'),
                               keep_byproducts=False, multiprocess=False)
        if count > 1:
            clip.unlink()
        for seg in sorted(result.segments, key=lambda item: item.start):
            label = str(seg.label).lower()
            if label == 'inst':
                label = 'instrumental'
            if label not in allowed or not math.isfinite(seg.start) or not math.isfinite(seg.end):
                continue
            left = round(max(start, offset + seg.start) * 1e6)
            right = round(min(end, offset + seg.end) * 1e6)
            if segments:
                left = max(left, segments[-1]['end_us'])
            if right <= left:
                continue
            if i and segments and left == round(start * 1e6) == segments[-1]['end_us'] and label.title() == segments[-1]['label']:
                segments[-1]['end_us'] = right
            else:
                segments.append({'start_us':left,'end_us':right,'label':label.title()})
    if len(segments) > MAX_SECTIONS:
        raise ValueError('Model returned too many sections')
    return segments

def run(source, output, status, expected_duration):
    write_json(status, {'state':'running', 'stage':'Loading structure model'})
    expected = {
        'all-in-one-infer': ('3.1.0', '566f96c3f007c783ab5eb6db0123bf3335af8f31'),
        'demucs-infer': ('4.4.0', 'ffe0080ef96336a34e3f0ee8ba22ceaa0623a36c'),
        'madmom-infer': ('0.3.0', 'a9ed04ec942dd6ec5309df7ce7f3783d1bb42575'),
    }
    for package, (version, commit) in expected.items():
        dist=distribution(package)
        provenance=json.loads(dist.read_text('direct_url.json') or '{}')
        if dist.version != version or provenance.get('vcs_info',{}).get('commit_id') != commit:
            raise RuntimeError('Structure environment does not match the tested lock. Run scripts/setup-structure-macos.sh')
    if distribution('torch').version != '2.15.0':
        raise RuntimeError('Structure environment does not match the tested lock. Run scripts/setup-structure-macos.sh')
    from allin1_infer import AllInOneSession
    work=Path(output+'.work')
    work.mkdir(parents=True,exist_ok=True)
    try:
        duration=float(expected_duration)
        if not 0 < duration <= MAX_DURATION:
            raise ValueError('Structure analysis supports tracks up to 30 minutes.')
        with AllInOneSession(device='cpu') as session:
            segments=analyze_sections(session, source, work, status, duration)
        warnings=['Machine-generated labels and boundaries require review.']
        if duration > 300:
            warnings.append('Sections on long tracks are estimated in overlapping windows.')
        write_json(output,{'version':VERSION,'duration':duration,'suggestions':segments,
                           'model':'All-In-One harmonix-all via all-in-one-infer',
                           'weight_license':'CC-BY-NC-SA-4.0',
                           'warnings':warnings})
        write_json(status,{'state':'succeeded','stage':'Complete'})
    finally:
        shutil.rmtree(work,ignore_errors=True)

if __name__=='__main__':
    try:
        run(*sys.argv[1:5])
    except Exception as exc:
        message=str(exc)
        if isinstance(exc,(ImportError,ModuleNotFoundError)):
            message += '. Run scripts/setup-structure-macos.sh and restart the app.'
        write_json(sys.argv[3],{'state':'failed','stage':'Failed','error':message})
        print(str(exc),file=sys.stderr)
        raise SystemExit(1)
