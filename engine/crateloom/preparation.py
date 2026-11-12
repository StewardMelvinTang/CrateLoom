"""Playlists and staged metadata; original files change only on explicit write."""
import hashlib,json,os,re,shutil,subprocess,tempfile,threading,time,urllib.parse,urllib.request,uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from .metadata_matching import catalog_identity

FIELDS=('title','artist','album','genre','date')
JOBS={}
JOB_LOCK=threading.Lock()
PROVIDER_POOL=ThreadPoolExecutor(max_workers=4,thread_name_prefix='provider')

def values(api,t):
    stored=t.get('metadata') or {}; stored=stored if isinstance(stored,dict) else json.loads(stored)
    if 'values' in stored:
        stored['artwork_supported']=t.get('codec') not in ('vorbis','opus')
        return stored
    tags={}
    if not t['missing']:
        _,_,tags=api.probe(t['path'])
    return {'values':{'title':t['title'],'artist':t['artist'],'album':tags.get('album',''),'genre':tags.get('genre',''),'date':tags.get('date',tags.get('year',''))},'pending':{},'artwork_path':None,'artwork_pending':False,'source':None,'artwork_supported':t.get('codec') not in ('vorbis','opus')}

def revision(state,file_hash):
    return hashlib.sha256(json.dumps({'fields':{k:state.get(k) for k in ('pending','artwork_path','artwork_pending')},'file_hash':file_hash},sort_keys=True).encode()).hexdigest()

def validate_image(api,source):
    source=Path(source).resolve(strict=True)
    if not source.is_file() or source.stat().st_size>5*1024*1024:raise ValueError('Choose a JPEG or PNG smaller than 5 MB')
    data=source.read_bytes()
    suffix='.jpg' if data.startswith(b'\xff\xd8\xff') else '.png' if data.startswith(b'\x89PNG\r\n\x1a\n') else None
    if not suffix:raise ValueError('Choose a JPEG or PNG image')
    result=subprocess.run(['ffprobe','-v','error','-show_streams','-of','json',str(source)],capture_output=True,text=True,check=True,timeout=10)
    streams=json.loads(result.stdout)['streams']
    if not streams or not 0<streams[0].get('width',0)<=4096 or not 0<streams[0].get('height',0)<=4096:raise ValueError('Artwork dimensions must be at most 4096 × 4096')
    dest=api.ROOT/'artwork'/ (hashlib.sha256(data).hexdigest()+suffix);api.atomic(dest,data);return str(dest)

def request(url,limit=2*1024*1024,timeout=12):
    req=urllib.request.Request(url,headers={'User-Agent':'CrateLoom/0.1.0 (https://github.com/StewardMelvinTang/CrateLoom)','Accept':'application/json,image/jpeg,image/png'})
    with urllib.request.urlopen(req,timeout=timeout) as response:
        if not response.url.startswith('https://'):raise ValueError('Insecure provider redirect rejected')
        data=response.read(limit+1)
        if len(data)>limit:raise ValueError('Provider response is too large')
        return data

def launch(fn,incremental=False,control=None):
    with JOB_LOCK:
        if sum(j['state'] in ('running','cancelling') for j in JOBS.values())>=128:raise ValueError('The request queue is full. Try again after a few requests finish.')
        for old in list(JOBS):
            if len(JOBS)>150 and JOBS[old]['state'] not in ('running','cancelling'):JOBS.pop(old)
        jid=str(uuid.uuid4());JOBS[jid]={'id':jid,'state':'running','stage':'Queued','result':None,'error':None}
        if control:control.attach(jid,lambda fields:JOBS[jid].update(fields))
    def work():
        JOBS[jid]['stage']='Working'
        def progress(result):JOBS[jid].update(result=result,stage='Checking remaining catalog')
        try:
            result=control.run(fn) if control else fn(progress) if incremental else fn()
            if control:
                with control.lock:
                    control.result=result
                    JOBS[jid].update(result=result,state='cancelled' if control.stop.is_set() else 'completed')
            else:JOBS[jid].update(result=result,state='completed')
        except Exception as error:JOBS[jid].update(error=None if control and control.stop.is_set() else str(error),result=control.result if control else None,state='cancelled' if control and control.stop.is_set() else 'failed')
        finally:
            if control:control.finish()
    # Return the same pending contract even when a cached/local result finishes
    # before Thread.start returns. Clients then consume it via the job endpoint.
    pending=dict(JOBS[jid])
    PROVIDER_POOL.submit(work);return pending

def fetch_cover(api,source,artwork_url=None):
    if source.get('provider')=='Apple Music':
        recording_id=source.get('recording_id')
        if not isinstance(recording_id,str) or not re.fullmatch(r'[0-9]{1,20}',recording_id):raise ValueError('Choose an Apple Music candidate first')
        target=api.ROOT/'artwork'/('apple-'+recording_id+'.image')
        if not target.exists():
            if artwork_url is None:
                from .metadata_lookup import cached_request
                payload,_,_=cached_request(api,request,'Apple Music','https://itunes.apple.com/lookup?'+urllib.parse.urlencode({'id':recording_id,'entity':'song','country':'US'}))
                row=next((r for r in payload['results'] if str(r.get('trackId'))==recording_id and r.get('kind')=='song'),None)
                artwork_url=row.get('artworkUrl100','') if row else ''
            host=urllib.parse.urlsplit(artwork_url or '').hostname or ''
            if not (artwork_url or '').startswith('https://') or not host.endswith('.mzstatic.com'):raise ValueError('No usable cover was found for this Apple Music release.')
            api.atomic(target,request(artwork_url.replace('100x100bb','600x600bb'),5*1024*1024))
        return validate_image(api,target)
    release=source.get('release_id')
    if not isinstance(release,str) or not re.fullmatch(r'[0-9a-fA-F-]{36}',release):raise ValueError('Choose a release candidate first')
    target=api.ROOT/'artwork'/('release-'+release+'-1200.image')
    try:
        if not target.exists():api.atomic(target,request('https://coverartarchive.org/release/'+release+'/front-1200',5*1024*1024))
        return validate_image(api,target)
    except Exception as error:raise ValueError('No usable front cover was found for this release. Choose a local image instead.') from error

def dispatch(api,db,method,p):
    if method.startswith('metadata.batch.'):
        from .metadata_batch import manager
        return manager(api).dispatch(db,method,p)
    if method=='playlists.list':
        return [dict(row,track_ids=[r['track_id'] for r in db.execute('SELECT track_id FROM playlist_tracks WHERE playlist_id=? ORDER BY position',(row['id'],))]) for row in db.execute('SELECT id,name FROM playlists ORDER BY rowid')]
    if method=='playlists.save':
        name=p.get('name');ids=p.get('track_ids',[]);pid=p.get('id') or str(uuid.uuid4())
        if not isinstance(name,str) or not name.strip() or len(name)>100:raise ValueError('Playlist name needs 1–100 characters')
        if not isinstance(pid,str) or not isinstance(ids,list) or len(ids)>5000 or any(not isinstance(i,str) for i in ids) or len(set(ids))!=len(ids):raise ValueError('Invalid playlist tracks')
        for tid in ids:
            if not db.execute('SELECT 1 FROM tracks WHERE id=?',(tid,)).fetchone():raise ValueError('A playlist track is no longer in the library')
        db.execute('INSERT INTO playlists(id,name) VALUES (?,?) ON CONFLICT(id) DO UPDATE SET name=excluded.name',(pid,name.strip()))
        db.execute('DELETE FROM playlist_tracks WHERE playlist_id=?',(pid,));db.executemany('INSERT INTO playlist_tracks VALUES (?,?,?)',[(pid,tid,i) for i,tid in enumerate(ids)])
        return {'id':pid,'name':name.strip(),'track_ids':ids}
    if method=='metadata.job':
        if p.get('id') not in JOBS:raise ValueError('Metadata request expired; try again')
        return dict(JOBS[p['id']])
    if method=='metadata.artwork':return {'path':validate_image(api,p['path'])}
    t=api.track(db,p['id'])
    if method=='metadata.reload':
        api.verify_asset(db,t);_,_,tags=api.probe(t['path'])
        db.execute('UPDATE tracks SET title=?,artist=?,metadata=NULL WHERE id=?',(tags.get('title') or Path(t['path']).stem,tags.get('artist',''),t['id']))
        return dispatch(api,db,'metadata.get',{'id':t['id']})
    state=values(api,t)
    if method=='metadata.get':
        if not t.get('metadata') and not t['missing']:
            try:
                probe=json.loads(subprocess.run(['ffprobe','-v','error','-show_streams','-of','json',t['path']],capture_output=True,text=True,check=True,timeout=10).stdout)
                if any(s.get('disposition',{}).get('attached_pic') for s in probe['streams']):
                    target=api.ROOT/'artwork'/('embedded-'+t['file_hash']+'.png');target.parent.mkdir(parents=True,exist_ok=True)
                    if not target.exists():subprocess.run(['ffmpeg','-v','error','-i',t['path'],'-map','0:v:0','-frames:v','1','-vf','scale=512:512:force_original_aspect_ratio=decrease','-y',str(target)],capture_output=True,check=True,timeout=20)
                    state['artwork_path']=validate_image(api,target)
            except (OSError,subprocess.SubprocessError,ValueError):pass
            db.execute('UPDATE tracks SET metadata=? WHERE id=?',(json.dumps(state),t['id']))
        from .metadata_batch import review
        return dict(state,revision=revision(state,t['file_hash']),automatic_review=review(api,t))
    if method=='metadata.save':
        fields=p.get('values')
        if not isinstance(fields,dict) or any(k not in FIELDS for k in fields):raise ValueError('Invalid metadata fields')
        if any(not isinstance(v,str) or len(v)>500 or '\x00' in v for v in fields.values()):raise ValueError('Metadata fields must be text up to 500 characters')
        if 'title' in fields and not fields['title'].strip():raise ValueError('Title cannot be empty')
        for key,value in fields.items():
            if value!=state['values'].get(key,''):state['pending'][key]=value
            state['values'][key]=value
        artwork=p.get('artwork_path',state.get('artwork_path'))
        if artwork!=state.get('artwork_path'):
            if artwork is not None:
                path=Path(artwork).resolve(strict=True)
                if path.parent!=(api.ROOT/'artwork').resolve():raise ValueError('Choose artwork before saving')
            state.update(artwork_path=artwork,artwork_pending=True)
        if 'source' in p:
            source=p['source']
            if source is not None and (not isinstance(source,dict) or any(k not in ('provider','recording_id','release_id') or not isinstance(v,str) or len(v)>100 for k,v in source.items())):raise ValueError('Invalid metadata source')
            state['source']=source
        if state.get('automatic'):state['automatic']['reviewed']=True
        db.execute('UPDATE tracks SET title=?,artist=?,metadata=? WHERE id=?',(state['values']['title'],state['values']['artist'],json.dumps(state),t['id']))
        return api.track(db,t['id'])
    if method=='metadata.search':
        title=p.get('title',state['values']['title']);artist=p.get('artist',state['values']['artist'])
        if any(not isinstance(s,str) or len(s)>300 for s in (title,artist)):raise ValueError('Search terms must be at most 300 characters')
        identity=catalog_identity(title,artist)
        if not identity['title']:raise ValueError('Enter a song title to search')
        from . import metadata_lookup
        return launch(lambda progress:metadata_lookup.search(api,request,identity,t['duration'],bool(p.get('refresh')),progress),incremental=True)
    if method=='metadata.cover':
        return launch(lambda:{'path':fetch_cover(api,p)})
    if method=='metadata.write':
        if p.get('revision') is not None and p['revision']!=revision(state,t['file_hash']):raise ValueError('Metadata changed after review. Open the review again before writing.')
        if not state['pending'] and not state.get('artwork_pending'):return {'track':t,'backup':None,'written':False}
        api.verify_asset(db,t);t=api.track(db,t['id'])
        if p.get('revision') is not None and p['revision']!=revision(state,t['file_hash']):raise ValueError('File tags changed after review. Reload and review before writing.')
        python=os.environ.get('CRATELOOM_METADATA_PYTHON')
        if not python or not Path(python).is_file():raise ValueError('Tag writer unavailable. Run scripts/setup-metadata-macos.sh and restart.')
        recipe=api.ROOT/'tag-receipts'/(uuid.uuid4().hex+'.json');output=recipe.with_suffix('.result.json')
        api.atomic(recipe,json.dumps({'path':t['path'],'expected_hash':t['file_hash'],'fields':state['pending'],'artwork_path':state.get('artwork_path') if state.get('artwork_pending') else None,'write_artwork':bool(state.get('artwork_pending')),'backup_root':str(api.ROOT/'tag-backups')}).encode())
        env={**os.environ,'PYTHONPATH':str(Path(__file__).resolve().parent.parent)}
        result=subprocess.run([python,'-m','crateloom.tag_writer',str(recipe),str(output)],env=env,capture_output=True,text=True,timeout=900)
        receipt=json.loads(output.read_text()) if output.exists() else {'error':'Tag writer stopped before verification; original replacement was not confirmed.'}
        if result.returncode or receipt.get('error'):raise ValueError(receipt.get('error','Tag write failed'))
        state.update(pending={},artwork_pending=False)
        db.execute('UPDATE tracks SET file_hash=?,metadata=? WHERE id=?',(receipt['file_hash'],json.dumps(state),t['id']))
        return {'track':api.track(db,t['id']),'backup':receipt['backup'],'written':True}
    raise ValueError('Unknown preparation method')
