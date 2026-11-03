import hashlib
import json
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

def connect():
    db = sqlite3.connect(DB)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA foreign_keys=ON')
    db.executescript('''CREATE TABLE IF NOT EXISTS tracks (id TEXT PRIMARY KEY,path TEXT UNIQUE NOT NULL,title TEXT NOT NULL,artist TEXT NOT NULL,duration REAL NOT NULL,codec TEXT NOT NULL, sample_rate INTEGER, file_hash TEXT NOT NULL, analysis TEXT); CREATE TABLE IF NOT EXISTS cues (track_id TEXT NOT NULL REFERENCES tracks(id),slot INTEGER NOT NULL,position_us INTEGER NOT NULL,label TEXT NOT NULL,PRIMARY KEY(track_id,slot)); PRAGMA user_version=1;''')
    return db

def track(db, tid):
    row = db.execute('SELECT * FROM tracks WHERE id=?', (tid,)).fetchone()
    if row is None: raise ValueError('Track not found')
    t = dict(row); t['missing'] = not Path(t['path']).is_file()
    t['analysis'] = json.loads(t['analysis']) if t['analysis'] else None
    t['cues'] = [dict(c) for c in db.execute('SELECT slot,position_us,label FROM cues WHERE track_id=? ORDER BY slot', (tid,))]
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

def dispatch(method, p):
    with connect() as db:
        if method == 'handshake': return {'version':VERSION,'capabilities':['import','waveform','manual_cues','rekordbox_xml'],'semantic_analysis':False}
        if method == 'library.list': return [track(db,r['id']) for r in db.execute('SELECT id FROM tracks ORDER BY rowid DESC').fetchall()]
        if method == 'library.import':
            path=Path(p['path']).expanduser().resolve(strict=True)
            if not path.is_file(): raise ValueError('Choose an audio file')
            old=db.execute('SELECT id FROM tracks WHERE path=?',(str(path),)).fetchone()
            if old: return track(db,old['id'])
            duration,stream,tags=probe(path)
            h=hashlib.sha256()
            with path.open('rb') as f:
                for block in iter(lambda:f.read(1024*1024),b''): h.update(block)
            tid=str(uuid.uuid4()); db.execute('INSERT INTO tracks VALUES (?,?,?,?,?,?,?,?,NULL)',(tid,str(path),tags.get('title') or path.stem,tags.get('artist',''),duration,stream.get('codec_name','unknown'),stream.get('sample_rate'),h.hexdigest()))
            return track(db,tid)
        if method == 'track.get': return track(db,p['id'])
        if method == 'analysis.run':
            t=track(db,p['id']); path=Path(t['path'])
            if not path.is_file(): raise ValueError('Audio file is missing; restore it before analysis')
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
            t=track(db,p['id']); cues=p['cues']; seen=set()
            if not isinstance(cues,list) or len(cues)>8: raise ValueError('Expected at most eight cues')
            for c in cues:
                slot=c['slot']; pos=c['position_us']; label=c['label']
                if type(slot) is not int or not 0<=slot<8 or slot in seen: raise ValueError('Cue slots must be unique from A to H')
                if type(pos) is not int or not 0<=pos<int(t['duration']*1_000_000): raise ValueError('Cue position is outside the track')
                if not isinstance(label,str) or len(label)>120: raise ValueError('Invalid cue label')
                seen.add(slot)
            db.execute('DELETE FROM cues WHERE track_id=?',(t['id'],))
            db.executemany('INSERT INTO cues VALUES (?,?,?,?)',[(t['id'],c['slot'],c['position_us'],c['label']) for c in cues])
            return track(db,t['id'])
        if method == 'export.rekordbox':
            t=track(db,p['id'])
            if t['missing']: raise ValueError('Audio file is missing; restore it before export')
            dest=Path(p['path']).expanduser().resolve(); source=Path(t['path']).resolve()
            backup=dest.with_suffix('.crateloom.json')
            if backup.exists(): raise ValueError('Cue backup already exists; choose a new export name to avoid overwriting it')
            if dest.suffix.lower() != '.xml' or dest == source or backup == source: raise ValueError('Choose an XML destination separate from your audio')
            root=ET.Element('DJ_PLAYLISTS',Version='1.0.0'); ET.SubElement(root,'PRODUCT',Name='CrateLoom',Version=VERSION,Company='CrateLoom')
            collection=ET.SubElement(root,'COLLECTION',Entries='1')
            loc=source.as_uri().replace('file:///','file://localhost/',1)
            item=ET.SubElement(collection,'TRACK',TrackID='1',Name=t['title'],Artist=t['artist'],TotalTime=str(int(t['duration'])),Location=loc)
            for c in t['cues']: ET.SubElement(item,'POSITION_MARK',Name=c['label'],Type='0',Start=f"{c['position_us']/1_000_000:.6f}",Num=str(c['slot']))
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
            req=json.loads(line)
            if req.get('protocol_version')!=1 or not isinstance(req.get('params'),dict) or not isinstance(req.get('request_id'),str): raise ValueError('Invalid protocol request')
            out={'protocol_version':1,'request_id':req['request_id'],'result':dispatch(req['method'],req['params'])}
        except Exception as e:
            out={'protocol_version':1,'request_id':req.get('request_id'),'error':{'code':'engine_error','message':str(e)}}
        print(json.dumps(out,ensure_ascii=False),flush=True)
if __name__ == '__main__': main()
