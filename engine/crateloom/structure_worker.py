"""Optional non-commercial All-In-One inference. Results are review suggestions."""
import json
import math
from importlib.metadata import distribution
from pathlib import Path
import shutil
import sys
from .analysis_worker import write_json

VERSION = 'allin1-infer-566f96c3-harmonix-all-v1'

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
    import allin1_infer
    work=Path(output+'.work')
    work.mkdir(parents=True,exist_ok=True)
    try:
        write_json(status, {'state':'running', 'stage':'Separating and analyzing sections'})
        result=allin1_infer.analyze(source,device='cpu',demix_dir=str(work/'demix'),spec_dir=str(work/'spec'),keep_byproducts=False)
        duration=float(expected_duration)
        allowed={'intro','verse','chorus','bridge','outro','break','instrumental','solo','other'}
        segments=[]
        for seg in result.segments:
            label=str(seg.label).lower(); start=float(seg.start); end=float(seg.end)
            if label not in allowed or not math.isfinite(start) or not math.isfinite(end): continue
            start=max(0.0,start); end=min(duration,end)
            if end <= start: continue
            segments.append({'start_us':round(start*1e6),'end_us':round(end*1e6),'label':label.title()})
        segments.sort(key=lambda s:s['start_us'])
        if len(segments)>200: raise ValueError('Model returned too many sections')
        write_json(output,{'version':VERSION,'duration':duration,'suggestions':segments,
                           'model':'All-In-One harmonix-all via all-in-one-infer',
                           'weight_license':'CC-BY-NC-SA-4.0',
                           'warnings':['Machine-generated labels and boundaries require review.']})
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
