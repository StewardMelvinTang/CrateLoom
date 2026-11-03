import base64
import atexit
import hashlib
import signal
import shutil
import json
import math
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import uuid
import xml.etree.ElementTree as ET

VERSION = '0.1.0'
ROOT = Path(os.environ.get('CRATELOOM_DATA', Path.home() / '.crateloom'))
ROOT.mkdir(parents=True, exist_ok=True)
DB = ROOT / 'library.sqlite3'
CACHE = ROOT / 'analysis-cache'
CACHE.mkdir(parents=True, exist_ok=True)
ANALYSIS_VERSION = 'librosa-0.11.0-rgb-5'
STRUCTURE_VERSION = 'allin1-infer-566f96c3-harmonix-all-v1'
WORKERS = {}

def connect():
    db = sqlite3.connect(DB)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA foreign_keys=ON')
    version = db.execute('PRAGMA user_version').fetchone()[0]
    if version > 8:
        db.close()
        raise ValueError('Library was created by a newer CrateLoom version')
    if 0 < version < 8:
        # SQLite's backup API includes committed WAL data; keep the old library
        # before changing its schema so a development upgrade is reversible.
        backup_path=ROOT / f'library-before-v8-{uuid.uuid4().hex}.sqlite3'
        backup_db=sqlite3.connect(backup_path)
        try:
            db.backup(backup_db)
        except Exception:
            backup_db.close(); backup_path.unlink(missing_ok=True); db.close(); raise
        backup_db.close()
    db.executescript('''CREATE TABLE IF NOT EXISTS tracks (id TEXT PRIMARY KEY,path TEXT UNIQUE NOT NULL,title TEXT NOT NULL,artist TEXT NOT NULL,duration REAL NOT NULL,codec TEXT NOT NULL, sample_rate INTEGER, file_hash TEXT NOT NULL, analysis TEXT); CREATE TABLE IF NOT EXISTS cues (track_id TEXT NOT NULL REFERENCES tracks(id),slot INTEGER NOT NULL,position_us INTEGER NOT NULL,label TEXT NOT NULL,PRIMARY KEY(track_id,slot));''')
    columns={row['name'] for row in db.execute('PRAGMA table_info(tracks)')}
    if 'audio_hash' not in columns:
        db.execute('ALTER TABLE tracks ADD COLUMN audio_hash TEXT')
    if 'detail_analysis' not in columns:
        db.execute('ALTER TABLE tracks ADD COLUMN detail_analysis TEXT')
    db.execute('CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, track_id TEXT NOT NULL, state TEXT NOT NULL, stage TEXT NOT NULL, status_path TEXT NOT NULL, output_path TEXT NOT NULL, cache_path TEXT NOT NULL, cache_key TEXT NOT NULL, error TEXT)')
    cue_columns={row['name'] for row in db.execute('PRAGMA table_info(cues)')}
    if 'color' not in cue_columns:
        db.execute('ALTER TABLE cues ADD COLUMN color TEXT')
    if 'grid_anchor_us' not in columns: db.execute('ALTER TABLE tracks ADD COLUMN grid_anchor_us INTEGER')
    if 'grid_cache_key' not in columns: db.execute('ALTER TABLE tracks ADD COLUMN grid_cache_key TEXT')
    db.execute('CREATE TABLE IF NOT EXISTS sections (id TEXT PRIMARY KEY, track_id TEXT NOT NULL REFERENCES tracks(id), start_us INTEGER NOT NULL, end_us INTEGER NOT NULL, label TEXT NOT NULL)')
    if 'grid_offset_us' not in columns: db.execute('ALTER TABLE tracks ADD COLUMN grid_offset_us INTEGER')
    if 'offset_cache_key' not in columns: db.execute('ALTER TABLE tracks ADD COLUMN offset_cache_key TEXT')
    if 'structure_analysis' not in columns: db.execute('ALTER TABLE tracks ADD COLUMN structure_analysis TEXT')
    job_columns={row['name'] for row in db.execute('PRAGMA table_info(jobs)')}
    if 'kind' not in job_columns: db.execute("ALTER TABLE jobs ADD COLUMN kind TEXT NOT NULL DEFAULT 'beat'")
    section_columns={row['name'] for row in db.execute('PRAGMA table_info(sections)')}
    if 'origin' not in section_columns: db.execute("ALTER TABLE sections ADD COLUMN origin TEXT NOT NULL DEFAULT 'manual'")
    if 'grid_bpm' not in columns: db.execute('ALTER TABLE tracks ADD COLUMN grid_bpm REAL')
    if 'grid_origin_us' not in columns: db.execute('ALTER TABLE tracks ADD COLUMN grid_origin_us INTEGER')
    if 'fixed_grid_key' not in columns: db.execute('ALTER TABLE tracks ADD COLUMN fixed_grid_key TEXT')
    if version < 8:
        db.execute('PRAGMA user_version=8')
    return db

def file_hash(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as source:
        for block in iter(lambda: source.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()

def audio_hash(path):
    proc=subprocess.run(['ffmpeg','-v','error','-i',str(path),'-map','0:a:0','-c','copy','-f','hash','-hash','sha256','-'],capture_output=True,text=True,check=True,timeout=600)
    line=proc.stdout.strip()
    if not line.startswith('SHA256=') or len(line) != 71:
        raise ValueError('Unable to verify the audio stream')
    return line[7:]

def verify_asset(db, t):
    path = Path(t['path'])
    if not path.is_file():
        raise ValueError('Audio file is missing; restore it before continuing')
    current = file_hash(path)
    if current != t['file_hash']:
        # A legacy track has no prior audio-stream signature, so changed bytes
        # cannot safely be classified as tags-only.
        if not t['audio_hash']:
            raise ValueError('The file changed since import. Restore the original before editing or exporting cues.')
        duration, stream, _ = probe(path)
        if (audio_hash(path) != t['audio_hash'] or stream.get('codec_name') != t['codec']
                or int(stream.get('sample_rate') or 0) != int(t['sample_rate'] or 0)
                or abs(duration - t['duration']) > .001):
            raise ValueError('The audio changed since import. Restore the original before editing or exporting cues.')
        db.execute('UPDATE tracks SET file_hash=? WHERE id=?', (current, t['id']))
    elif not t['audio_hash']:
        # Record a baseline for unchanged tracks created by schema version 1.
        db.execute('UPDATE tracks SET audio_hash=? WHERE id=?', (audio_hash(path), t['id']))
    return path

def track(db, tid):
    row = db.execute('SELECT * FROM tracks WHERE id=?', (tid,)).fetchone()
    if row is None: raise ValueError('Track not found')
    t = dict(row); t['missing'] = not Path(t['path']).is_file()
    t['analysis'] = json.loads(t['analysis']) if t['analysis'] else None
    t['detail_analysis'] = json.loads(t['detail_analysis']) if t['detail_analysis'] else None
    t['structure_analysis'] = json.loads(t['structure_analysis']) if t['structure_analysis'] else None
    if t['detail_analysis'] and not Path(t['detail_analysis']['cache_path']).is_file(): t['detail_analysis'] = None
    t['cues'] = [{k:v for k,v in dict(c).items() if v is not None} for c in db.execute('SELECT slot,position_us,label,color FROM cues WHERE track_id=? ORDER BY slot', (tid,))]
    t['sections'] = [{k:v for k,v in dict(row).items() if not (k=='origin' and v=='manual')} for row in db.execute('SELECT id,start_us,end_us,label,origin FROM sections WHERE track_id=? ORDER BY start_us', (tid,))]
    if not t['detail_analysis'] or t['fixed_grid_key'] != t['detail_analysis']['cache_key']: t['grid_bpm'] = None; t['grid_origin_us'] = None
    if not t['detail_analysis'] or t['grid_cache_key'] != t['detail_analysis']['cache_key']: t['grid_anchor_us'] = None
    if not t['detail_analysis'] or t['offset_cache_key'] != t['detail_analysis']['cache_key']: t['grid_offset_us'] = 0
    else: t['grid_offset_us'] = t['grid_offset_us'] or 0
    return t

def probe(path):
    p = subprocess.run(['ffprobe','-v','error','-show_format','-show_streams','-of','json',str(path)],capture_output=True,text=True,check=True,timeout=30)
    data=json.loads(p.stdout); stream=next((s for s in data['streams'] if s.get('codec_type')=='audio'),None)
    if not stream: raise ValueError('No audio stream found')
    duration=float(data['format'].get('duration',0))
    if not 0 < duration < 86400: raise ValueError('Audio duration is invalid or exceeds 24 hours')
    tags={k.lower():v for k,v in data['format'].get('tags',{}).items()}
    return duration,stream,tags

def atomic(path, data):
    path=Path(path); path.parent.mkdir(parents=True,exist_ok=True)
    fd, name=tempfile.mkstemp(prefix='.crateloom-',dir=path.parent)
    try:
        with os.fdopen(fd,'wb') as f: f.write(data); f.flush(); os.fsync(f.fileno())
        os.replace(name,path)
    finally:
        if os.path.exists(name): os.unlink(name)

def stop_worker(proc):
    if proc.poll() is not None: return
    try:
        if os.name == 'posix': os.killpg(proc.pid, signal.SIGTERM)
        else: proc.terminate()
        proc.wait(timeout=3)
    except (ProcessLookupError, subprocess.TimeoutExpired):
        if proc.poll() is None:
            if os.name == 'posix': os.killpg(proc.pid, signal.SIGKILL)
            else: proc.kill()
            proc.wait(timeout=3)

@atexit.register
def stop_workers():
    for proc in list(WORKERS.values()):
        try: stop_worker(proc)
        except Exception: pass

def validate_analysis(data, expected_duration):
    if not isinstance(data,dict) or data.get('version') != ANALYSIS_VERSION: raise ValueError('Unexpected analysis version')
    duration=data.get('duration'); tempo=data.get('tempo'); beats=data.get('beats'); waveform=data.get('waveform')
    if not isinstance(duration,(int,float)) or not math.isfinite(duration) or duration<=0 or abs(duration-expected_duration)>1: raise ValueError('Invalid analysis duration')
    if tempo is not None and (not isinstance(tempo,(int,float)) or not math.isfinite(tempo) or tempo<=0): raise ValueError('Invalid tempo')
    if not isinstance(beats,list) or len(beats)>10000 or any(not isinstance(x,(int,float)) or not math.isfinite(x) or x<0 or x>=min(duration,expected_duration) for x in beats) or any(a>=b for a,b in zip(beats,beats[1:])): raise ValueError('Invalid beat positions')
    if not isinstance(waveform,dict): raise ValueError('Invalid waveform')
    peaks=waveform.get('peaks'); colors=waveform.get('rgb')
    valid=lambda x:isinstance(x,(int,float)) and math.isfinite(x) and 0<=x<=1
    if not isinstance(peaks,list) or not 0<len(peaks)<=1250000 or not isinstance(colors,list) or not 0<len(colors)<=50000 or any(not valid(x) for x in peaks) or any(not isinstance(row,list) or len(row)!=3 or any(not valid(x) for x in row) for row in colors): raise ValueError('Invalid waveform data')
    signal=waveform.get('signal')
    if not isinstance(signal,dict) or signal.get('encoding')!='i16le-minmax' or signal.get('hop')!=8 or signal.get('rate')!=22050 or not isinstance(signal.get('data'),str) or len(signal['data'])>14000000: raise ValueError('Invalid signal detail')
    raw=base64.b64decode(signal['data'],validate=True)
    expected=math.ceil(round(duration*22050)/8)*4
    if len(raw)!=expected: raise ValueError('Signal detail length mismatch')
    if not isinstance(data.get('warnings'),list): raise ValueError('Invalid analysis warnings')
    return data

def validate_structure(data, expected_duration):
    if not isinstance(data,dict) or data.get('version')!=STRUCTURE_VERSION: raise ValueError('Unexpected structure analysis version')
    if data.get('weight_license')!='CC-BY-NC-SA-4.0' or not isinstance(data.get('suggestions'),list) or len(data['suggestions'])>200: raise ValueError('Invalid structure output')
    for section in data['suggestions']:
        if not isinstance(section,dict) or type(section.get('start_us')) is not int or type(section.get('end_us')) is not int or not 0<=section['start_us']<section['end_us']<=round(expected_duration*1000000) or not isinstance(section.get('label'),str) or not 0<len(section['label'])<=80: raise ValueError('Invalid suggested section')
    return data

def grid_beats(t, data):
    offset=t['grid_offset_us']/1000000
    if t['grid_bpm'] and t['grid_origin_us'] is not None:
        interval=60/t['grid_bpm']; origin=t['grid_origin_us']/1000000 + offset
        first=math.ceil(-origin/interval); last=math.ceil((t['duration']-origin)/interval)
        return [round(origin+i*interval,6) for i in range(first,last) if 0<=origin+i*interval<t['duration']]
    return [round(beat+offset,6) for beat in data['beats'] if 0<=beat+offset<t['duration']]

def store_analysis(db, asset, job_id, key, path, data, kind):
    db.execute('SAVEPOINT store_analysis')
    try:
        _store_analysis(db, asset, job_id, key, path, data, kind)
    except Exception:
        db.execute('ROLLBACK TO store_analysis'); db.execute('RELEASE store_analysis')
        raise
    db.execute('RELEASE store_analysis')

def _store_analysis(db, asset, job_id, key, path, data, kind):
    if kind=='structure':
        detail={'cache_key':key,'version':data['version'],'suggestions':data['suggestions'],'model':data['model'],'weight_license':data['weight_license'],'warnings':data['warnings'],'revision':job_id}
        db.execute('UPDATE tracks SET structure_analysis=? WHERE id=?',(json.dumps(detail),asset['id']))
        # Keep manual/reviewed edits, replace only untouched model-generated sections.
        protected=[dict(x) for x in db.execute("SELECT start_us,end_us FROM sections WHERE track_id=? AND origin!='model' ORDER BY start_us",(asset['id'],))]
        db.execute("DELETE FROM sections WHERE track_id=? AND origin='model'",(asset['id'],))
        for suggestion in data['suggestions']:
            fragments=[(suggestion['start_us'],suggestion['end_us'])]
            for saved in protected:
                fragments=[part for a,b in fragments for part in ((a,min(b,saved['start_us'])),(max(a,saved['end_us']),b)) if part[1]>part[0]]
            for start,end in fragments:
                db.execute("INSERT INTO sections (id,track_id,start_us,end_us,label,origin) VALUES (?,?,?,?,?,'model')",(str(uuid.uuid4()),asset['id'],start,end,suggestion['label']))
                protected.append({'start_us':start,'end_us':end})
    else:
        detail={'cache_path':str(path),'cache_key':key,'version':data['version'],'tempo':data['tempo'],'warnings':data['warnings'],'revision':job_id}
        old_key=asset['detail_analysis']['cache_key'] if asset['detail_analysis'] else None
        # Preserve the user's grid for the same verified audio stream. Detected
        # anchors are retained only if they still match a newly detected beat.
        if old_key and old_key != key:
            if asset['fixed_grid_key']==old_key and asset['grid_bpm'] is not None:
                db.execute('UPDATE tracks SET fixed_grid_key=? WHERE id=?',(key,asset['id']))
            if asset['offset_cache_key']==old_key:
                db.execute('UPDATE tracks SET offset_cache_key=? WHERE id=?',(key,asset['id']))
            if asset['grid_cache_key']==old_key and asset['grid_anchor_us'] is not None:
                fixed=asset['fixed_grid_key']==old_key and asset['grid_bpm'] is not None
                if fixed or any(abs(round(b*1000000)-asset['grid_anchor_us'])<=1000 for b in data['beats']):
                    db.execute('UPDATE tracks SET grid_cache_key=? WHERE id=?',(key,asset['id']))
        db.execute('UPDATE tracks SET detail_analysis=? WHERE id=?',(json.dumps(detail),asset['id']))

def job_status(db, job_id):
    row = db.execute('SELECT * FROM jobs WHERE id=?', (job_id,)).fetchone()
    if not row: raise ValueError('Analysis job not found')
    job = dict(row)
    if job['state'] in ('queued', 'running'):
        proc = WORKERS.get(job_id)
        status_file = Path(job['status_path'])
        status = json.loads(status_file.read_text()) if status_file.is_file() else {'state': 'running', 'stage': 'Starting'}
        if status.get('state') == 'succeeded' and proc and proc.poll() is None:
            status = {'state': 'running', 'stage': 'Finishing'}
        if status.get('state') == 'succeeded' and proc and proc.poll() is not None:
            if proc.returncode != 0: status = {'state': 'failed', 'stage': 'Failed', 'error': 'Analysis worker exited unexpectedly'}
            else:
                try:
                    asset = track(db, job['track_id']); verify_asset(db, asset)
                    current = track(db, job['track_id'])
                    key = cache_key(current, job['kind'])
                    if key != job['cache_key']: raise ValueError('Audio changed while analysis was running')
                    data = json.loads(Path(job['output_path']).read_text())
                    data = validate_structure(data,current['duration']) if job['kind']=='structure' else validate_analysis(data,current['duration'])
                    os.replace(job['output_path'], job['cache_path'])
                    store_analysis(db,current,job_id,key,job['cache_path'],data,job['kind'])
                except Exception as e: status = {'state': 'failed', 'stage': 'Failed', 'error': str(e)}
        elif proc is None:
            status = {'state': 'failed', 'stage': 'Interrupted', 'error': 'Analysis was interrupted; start it again.'}
        elif proc.poll() is not None and status.get('state') not in ('succeeded', 'failed'):
            status = {'state': 'failed', 'stage': 'Failed', 'error': 'Analysis worker exited unexpectedly'}
        state = status.get('state', 'running'); stage = status.get('stage', 'Working'); error = status.get('error')
        db.execute('UPDATE jobs SET state=?,stage=?,error=? WHERE id=?', (state,stage,error,job_id))
        job.update(state=state,stage=stage,error=error)
        if state in ('succeeded', 'failed'):
            finished=WORKERS.pop(job_id, None)
            if finished and finished.poll() is None:
                try: finished.wait(timeout=1)
                except subprocess.TimeoutExpired: stop_worker(finished)
            if state == 'failed': Path(job['output_path']).unlink(missing_ok=True)
            shutil.rmtree(job['output_path']+'.work',ignore_errors=True)
    return {key: job[key] for key in ('id','track_id','state','stage','error','kind')}

def cache_key(t, kind='beat'):
    ffmpeg_version=subprocess.run(['ffmpeg','-version'],capture_output=True,text=True,check=True,timeout=10).stdout.splitlines()[0]
    version=STRUCTURE_VERSION if kind=='structure' else ANALYSIS_VERSION
    text = f"{t['audio_hash']}:{t['codec']}:{t['sample_rate']}:{t['duration']}:{version}:{ffmpeg_version}"
    return hashlib.sha256(text.encode()).hexdigest()

def dispatch(method, p):
    with connect() as db:
        if method == 'system.shutdown': return {'stopping':True}
        if method == 'handshake': return {'version':VERSION,'capabilities':['import','waveform','manual_cues','rekordbox_xml','analysis_jobs','beat_tracking','rgb_waveform','manual_sections','optional_structure_suggestions'],'semantic_analysis':bool(os.environ.get('CRATELOOM_STRUCTURE_PYTHON')),'analysis_python_configured':bool(os.environ.get('CRATELOOM_ANALYSIS_PYTHON'))}
        if method == 'library.list': return [track(db,r['id']) for r in db.execute('SELECT id FROM tracks ORDER BY rowid DESC').fetchall()]
        if method == 'library.import':
            path=Path(p['path']).expanduser().resolve(strict=True)
            if not path.is_file(): raise ValueError('Choose an audio file')
            old=db.execute('SELECT id FROM tracks WHERE path=?',(str(path),)).fetchone()
            if old:
                existing=track(db,old['id']); verify_asset(db,existing); return track(db,old['id'])
            duration,stream,tags=probe(path)
            digest=file_hash(path)
            signature=audio_hash(path)
            tid=str(uuid.uuid4()); db.execute('INSERT INTO tracks (id,path,title,artist,duration,codec,sample_rate,file_hash,analysis,audio_hash) VALUES (?,?,?,?,?,?,?,?,NULL,?)',(tid,str(path),tags.get('title') or path.stem,tags.get('artist',''),duration,stream.get('codec_name','unknown'),stream.get('sample_rate'),digest,signature))
            return track(db,tid)
        if method in ('analysis.enqueue','structure.enqueue'):
            kind='structure' if method=='structure.enqueue' else 'beat'
            asset=track(db,p['id']); verify_asset(db,asset); asset=track(db,p['id'])
            if kind=='beat' and asset['duration']>900: raise ValueError('Beat analysis currently supports tracks up to 15 minutes.')
            if kind=='structure' and asset['duration']>300: raise ValueError('Structure analysis currently supports tracks up to 5 minutes.')
            for existing in db.execute("SELECT id FROM jobs WHERE state IN ('queued','running')").fetchall():
                current=job_status(db,existing['id'])
                if current['state'] in ('queued','running'): raise ValueError('Another analysis is running. Wait or cancel it first.')
            key=cache_key(asset,kind); cache=CACHE / (key + '.json'); job_id=str(uuid.uuid4())
            status=CACHE / (job_id + '.status.json'); output=CACHE / (job_id + '.partial.json')
            force=p.get('force',False)
            if type(force) is not bool: raise ValueError('Invalid force setting')
            if cache.is_file() and not force:
                try:
                    raw=json.loads(cache.read_text())
                    data=validate_structure(raw,asset['duration']) if kind=='structure' else validate_analysis(raw,asset['duration'])
                    store_analysis(db,asset,job_id,key,cache,data,kind)
                    db.execute('INSERT INTO jobs (id,track_id,state,stage,status_path,output_path,cache_path,cache_key,error,kind) VALUES (?,?,?,?,?,?,?,?,NULL,?)',(job_id,asset['id'],'succeeded','Using cached analysis',str(status),str(output),str(cache),key,kind))
                    return job_status(db,job_id)
                except (OSError,ValueError,KeyError,json.JSONDecodeError): pass
            python=os.environ.get('CRATELOOM_STRUCTURE_PYTHON' if kind=='structure' else 'CRATELOOM_ANALYSIS_PYTHON')
            if not python or not Path(python).is_file():
                raise ValueError('Structure model unavailable. Run scripts/setup-structure-macos.sh and restart the app.' if kind=='structure' else 'Beat analysis is unavailable. Run scripts/setup-analysis-macos.sh and restart the app.')
            env=os.environ.copy(); env['PYTHONPATH']=str(Path(__file__).resolve().parent.parent)
            module='crateloom.structure_worker' if kind=='structure' else 'crateloom.analysis_worker'
            proc=subprocess.Popen([python,'-m',module,asset['path'],str(output),str(status),str(asset['duration'])],env=env,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,start_new_session=(os.name=='posix'))
            WORKERS[job_id]=proc
            try:
                db.execute('INSERT INTO jobs (id,track_id,state,stage,status_path,output_path,cache_path,cache_key,error,kind) VALUES (?,?,?,?,?,?,?,?,NULL,?)',(job_id,asset['id'],'running','Starting',str(status),str(output),str(cache),key,kind))
            except Exception:
                WORKERS.pop(job_id,None); stop_worker(proc); raise
            return job_status(db,job_id)
        if method == 'jobs.get': return job_status(db,p['id'])
        if method == 'jobs.cancel':
            job=job_status(db,p['id'])
            if job['state'] in ('queued','running'):
                proc=WORKERS.pop(job['id'],None)
                if proc: stop_worker(proc)
                row=db.execute('SELECT output_path,status_path FROM jobs WHERE id=?',(job['id'],)).fetchone()
                Path(row['output_path']).unlink(missing_ok=True); Path(row['status_path']).unlink(missing_ok=True)
                shutil.rmtree(row['output_path']+'.work',ignore_errors=True)
                db.execute("UPDATE jobs SET state='cancelled',stage='Cancelled' WHERE id=?",(job['id'],))
            return job_status(db,job['id'])
        if method == 'track.get':
            result=track(db,p['id'])
            if not result['missing']: verify_asset(db,result)
            result=track(db,p['id'])
            return result
        if method == 'cues.auto':
            from .cue_rules import auto_cues
            t=track(db,p['id']); verify_asset(db,t); t=track(db,p['id'])
            if not t['detail_analysis'] and not t['sections']: raise ValueError('Analyze the track first')
            data=json.loads(Path(t['detail_analysis']['cache_path']).read_text()) if t['detail_analysis'] else None
            beats=grid_beats(t,data) if data else []
            additions=auto_cues(beats,t['sections'],t['cues'],t['duration'])
            colors=['#ef5c86','#57b7ee','#9ccc51','#ae7cf5','#edac52','#5acbb7','#d885c3','#b3bec8']
            db.executemany('INSERT INTO cues (track_id,slot,position_us,label,color) VALUES (?,?,?,?,?)',[(t['id'],c['slot'],c['position_us'],c['label'],colors[c['slot']]) for c in additions])
            return {'track':track(db,t['id']),'added':len(additions),'has_sections':bool(t['sections']),'free_slots':8-len(t['cues'])}
        if method == 'cues.propose':
            from .cue_rules import propose
            t=track(db,p['id']); verify_asset(db,t)
            if not t['detail_analysis']: raise ValueError('Analyze beats before generating suggestions')
            data=json.loads(Path(t['detail_analysis']['cache_path']).read_text())
            beats=grid_beats(t,data)
            anchor=t['grid_anchor_us']+t['grid_offset_us'] if t['grid_anchor_us'] is not None else None
            return {'preset':'conservative-four-v1','cues':propose(beats,t['sections'],t['cues'],anchor,t['duration'])}
        if method == 'grid.restore':
            t=track(db,p['id']); verify_asset(db,t); detail=t['detail_analysis']; state=p.get('state')
            if not detail or not isinstance(state,dict): raise ValueError('Grid is unavailable')
            bpm=state.get('bpm'); origin=state.get('origin_us'); anchor=state.get('anchor_us'); offset=state.get('offset_us')
            if type(offset) is not int or not -2000000<=offset<=2000000: raise ValueError('Invalid grid shift')
            if (bpm is None) != (origin is None): raise ValueError('Incomplete fixed grid')
            if bpm is not None and (not isinstance(bpm,(int,float)) or isinstance(bpm,bool) or not math.isfinite(bpm) or not 20<=bpm<=400 or type(origin) is not int or not 0<=origin+offset<round(t['duration']*1000000)): raise ValueError('Invalid fixed grid')
            if anchor is not None:
                if type(anchor) is not int or not 0<=anchor+offset<round(t['duration']*1000000): raise ValueError('Invalid bar 1')
                data=json.loads(Path(detail['cache_path']).read_text()); candidate=dict(t,grid_bpm=bpm,grid_origin_us=origin,grid_offset_us=offset)
                if not any(abs(round(b*1000000)-offset-anchor)<=1000 for b in grid_beats(candidate,data)): raise ValueError('Bar 1 must match the grid')
            db.execute('UPDATE tracks SET grid_bpm=?,grid_origin_us=?,fixed_grid_key=?,grid_anchor_us=?,grid_cache_key=?,grid_offset_us=?,offset_cache_key=? WHERE id=?',(bpm,origin,detail['cache_key'] if bpm is not None else None,anchor,detail['cache_key'] if anchor is not None else None,offset,detail['cache_key'],t['id']))
            return track(db,t['id'])
        if method == 'grid.fixed':
            t=track(db,p['id']); verify_asset(db,t); detail=t['detail_analysis']
            bpm=p.get('bpm'); origin=p.get('origin_us')
            if not detail or not isinstance(bpm,(int,float)) or isinstance(bpm,bool) or not math.isfinite(bpm) or not 20<=bpm<=400: raise ValueError('BPM must be between 20 and 400')
            if type(origin) is not int or not 0<=origin+t['grid_offset_us']<round(t['duration']*1000000): raise ValueError('Grid origin must be within the track')
            # The origin is itself a grid beat. A pre-existing bar anchor must coincide with it.
            anchor=origin if p.get('bar_one') else None
            db.execute('UPDATE tracks SET grid_bpm=?,grid_origin_us=?,fixed_grid_key=?,grid_anchor_us=?,grid_cache_key=? WHERE id=?',(bpm,origin,detail['cache_key'],anchor,detail['cache_key'] if anchor is not None else None,t['id']))
            return track(db,t['id'])
        if method == 'grid.offset':
            t=track(db,p['id']); verify_asset(db,t); detail=t['detail_analysis']; offset=p.get('offset_us')
            if not detail or type(offset) is not int or not -2000000<=offset<=2000000: raise ValueError('Grid shift must be between -2000 and 2000 ms')
            if t['grid_anchor_us'] is not None and not 0<=t['grid_anchor_us']+offset<round(t['duration']*1000000): raise ValueError('Grid shift would move bar 1 outside the track')
            db.execute('UPDATE tracks SET grid_offset_us=?,offset_cache_key=? WHERE id=?',(offset,detail['cache_key'],t['id']))
            return track(db,t['id'])
        if method == 'grid.set':
            t=track(db,p['id']); verify_asset(db,t)
            detail=t['detail_analysis']; anchor=p.get('anchor_us')
            if anchor is None:
                db.execute('UPDATE tracks SET grid_anchor_us=NULL,grid_cache_key=NULL WHERE id=?',(t['id'],))
            else:
                if type(anchor) is not int or not 0<=anchor+t['grid_offset_us']<round(t['duration']*1000000) or not detail: raise ValueError('Analyze beats before setting bar 1')
                data=json.loads(Path(detail['cache_path']).read_text())
                if not any(abs(round(beat*1000000)-t['grid_offset_us']-anchor)<=1000 for beat in grid_beats(t,data)): raise ValueError('Bar 1 must be on a detected beat')
                db.execute('UPDATE tracks SET grid_anchor_us=?,grid_cache_key=? WHERE id=?',(anchor,detail['cache_key'],t['id']))
            return track(db,t['id'])
        if method == 'sections.save':
            t=track(db,p['id']); verify_asset(db,t); sections=p['sections']
            if not isinstance(sections,list) or len(sections)>200: raise ValueError('Expected at most 200 sections')
            prior_end=0; checked=[]; ids=set()
            for section in sorted(sections,key=lambda x:x['start_us']):
                sid=section['id']; start=section['start_us']; end=section['end_us']; label=section['label']; origin=section.get('origin','manual')
                if not isinstance(sid,str) or not sid or len(sid)>80 or sid in ids: raise ValueError('Invalid section ID')
                if type(start) is not int or type(end) is not int or start<prior_end or end<=start or end>round(t['duration']*1000000): raise ValueError('Sections must be ordered, non-overlapping and within the track')
                if not isinstance(label,str) or not label.strip() or len(label)>80: raise ValueError('Section needs a label of up to 80 characters')
                if origin not in ('manual','model-reviewed','model'): raise ValueError('Invalid section origin')
                checked.append((sid,t['id'],start,end,label.strip(),origin)); prior_end=end; ids.add(sid)
            db.execute('DELETE FROM sections WHERE track_id=?',(t['id'],))
            db.executemany('INSERT INTO sections (id,track_id,start_us,end_us,label,origin) VALUES (?,?,?,?,?,?)',checked)
            return track(db,t['id'])
        if method == 'analysis.run':
            t=track(db,p['id']); path=verify_asset(db,t)
            # Decoded mono envelope; no semantic or tempo claims are inferred from peaks.
            proc=subprocess.run(['ffmpeg','-v','error','-i',str(path),'-vn','-ac','1','-ar','1000','-f','s16le','-'],capture_output=True,check=True,timeout=600)
            import array
            samples=array.array('h'); samples.frombytes(proc.stdout)
            if sys.byteorder != 'little': samples.byteswap()
            width=max(1,(len(samples)+999)//1000)
            peaks=[round(max(abs(x) for x in samples[i:i+width])/32768,4) for i in range(0,len(samples),width)]
            result={'peaks':peaks,'provider':'ffmpeg envelope 1','sample_count':len(samples),'decoded_rate':1000,'tempo':None,'sections':None}
            db.execute('UPDATE tracks SET analysis=? WHERE id=?',(json.dumps(result),t['id']))
            return track(db,t['id'])
        if method == 'cues.save':
            t=track(db,p['id']); verify_asset(db,t); cues=p['cues']; seen=set()
            if not isinstance(cues,list) or len(cues)>8: raise ValueError('Expected at most eight cues')
            for c in cues:
                slot=c['slot']; pos=c['position_us']; label=c['label']
                if type(slot) is not int or not 0<=slot<8 or slot in seen: raise ValueError('Cue slots must be unique from A to H')
                if type(pos) is not int or not 0<=pos<int(t['duration']*1_000_000): raise ValueError('Cue position is outside the track')
                if not isinstance(label,str) or len(label)>120: raise ValueError('Invalid cue label')
                color=c.get('color')
                if color is not None and (not isinstance(color,str) or len(color)!=7 or color[0]!='#' or any(ch not in '0123456789abcdefABCDEF' for ch in color[1:])): raise ValueError('Invalid cue color')
                seen.add(slot)
            db.execute('DELETE FROM cues WHERE track_id=?',(t['id'],))
            db.executemany('INSERT INTO cues (track_id,slot,position_us,label,color) VALUES (?,?,?,?,?)',[(t['id'],c['slot'],c['position_us'],c['label'],c.get('color')) for c in cues])
            return track(db,t['id'])
        if method == 'export.rekordbox':
            t=track(db,p['id'])
            source=verify_asset(db,t).resolve()
            dest=Path(p['path']).expanduser().resolve()
            backup=dest.with_suffix('.crateloom.json')
            if backup.exists(): raise ValueError('Cue backup already exists; choose a new export name to avoid overwriting it')
            protected={Path(r['path']).resolve() for r in db.execute('SELECT path FROM tracks')}
            if dest.suffix.lower() != '.xml' or dest in protected or backup in protected: raise ValueError('Choose an XML destination separate from all library audio')
            root=ET.Element('DJ_PLAYLISTS',Version='1.0.0'); ET.SubElement(root,'PRODUCT',Name='CrateLoom',Version=VERSION,Company='CrateLoom')
            collection=ET.SubElement(root,'COLLECTION',Entries='1')
            loc=source.as_uri().replace('file:///','file://localhost/',1)
            item=ET.SubElement(collection,'TRACK',TrackID='1',Name=t['title'],Artist=t['artist'],TotalTime=str(int(t['duration'])),Location=loc)
            for c in t['cues']:
                attrs={'Name':c['label'],'Type':'0','Start':f"{c['position_us']/1_000_000:.6f}",'Num':str(c['slot'])}
                if c.get('color'):
                    color=c['color']; attrs.update(Red=str(int(color[1:3],16)),Green=str(int(color[3:5],16)),Blue=str(int(color[5:7],16)))
                ET.SubElement(item,'POSITION_MARK',**attrs)
            lists=ET.SubElement(root,'PLAYLISTS'); node=ET.SubElement(lists,'NODE',Type='0',Name='ROOT',Count='1'); playlist=ET.SubElement(node,'NODE',Type='1',Name='CrateLoom',KeyType='0',Entries='1'); ET.SubElement(playlist,'TRACK',Key='1')
            xml=ET.tostring(root,encoding='utf-8',xml_declaration=True)
            atomic(backup,json.dumps({'format_version':1,'track':t},ensure_ascii=False,indent=2).encode()); atomic(dest,xml)
            return {'xml':str(dest),'backup':str(backup)}
        raise ValueError('Unknown method')

def main():
    for line in sys.stdin.buffer:
        req={}
        try:
            if len(line)>1024*1024: raise ValueError('Request exceeds 1 MB')
            parsed=json.loads(line)
            if not isinstance(parsed,dict): raise ValueError('Request must be an object')
            req=parsed
            if req.get('protocol_version')!=1 or not isinstance(req.get('params'),dict) or not isinstance(req.get('request_id'),str): raise ValueError('Invalid protocol request')
            out={'protocol_version':1,'request_id':req['request_id'],'result':dispatch(req['method'],req['params'])}
        except Exception as e:
            out={'protocol_version':1,'request_id':req.get('request_id'),'error':{'code':'engine_error','message':str(e)}}
        print(json.dumps(out,ensure_ascii=False),flush=True)
        if req.get('method') == 'system.shutdown' and 'error' not in out: break
if __name__ == '__main__': main()
