"""Background import orchestration. All SQLite writes stay in the engine host."""
import os
from pathlib import Path
import threading
import uuid

# Raw ADTS .aac is left out on purpose: ffprobe only estimates its duration
# from the bitrate, which would put cue and analysis bounds seconds off.
EXTENSIONS={'.mp3','.wav','.aiff','.aif','.m4a','.flac','.ogg','.opus'}
RUNNING={}
THREADS={}
MAX_FILES=5000

class ImportCancelled(Exception): pass

def start(api, paths, folder):
    with api.connect() as db:
        for row in db.execute("SELECT id FROM library_imports WHERE state IN ('scanning','running')"):
            if row['id'] in RUNNING: raise ValueError('An import is already running')
        jid=str(uuid.uuid4())
        db.execute("INSERT INTO library_imports(id,state,total,current) VALUES (?,'scanning',0,'Scanning…')",(jid,))
    stop=threading.Event(); RUNNING[jid]=stop
    def work():
        api.IMPORT_CONTEXT.cancel=stop
        try:
            files=[]; seen=set()
            def push(path,problem=None):
                if len(files)>=MAX_FILES: raise ValueError('Choose a smaller folder: import limit is 5,000 audio files')
                files.append((str(path),problem))
            def add(p,chosen=False):
                if stop.is_set(): raise ImportCancelled()
                try: resolved=Path(p).resolve(strict=True)
                except OSError as error:
                    # One missing or unreadable path is reported as its own failed
                    # item; the rest of the batch still imports.
                    if chosen or Path(p).suffix.lower() in EXTENSIONS:
                        push(p,'File not found' if isinstance(error,FileNotFoundError) else 'File could not be read: '+(error.strerror or str(error)))
                    return
                if resolved.is_file() and resolved.suffix.lower() in EXTENSIONS and str(resolved) not in seen:
                    push(resolved);seen.add(str(resolved))
            if folder:
                try: root=Path(folder).expanduser().resolve(strict=True)
                except OSError as error: raise ValueError('The folder could not be opened. Choose it again.') from error
                if not root.is_dir(): raise ValueError('Choose a folder')
                def scan_error(error):
                    # Skip an unreadable subfolder with a recorded reason instead of
                    # failing every other file in the tree.
                    push(error.filename or root,'Folder could not be read: '+(error.strerror or str(error)))
                for parent, dirs, names in os.walk(root,followlinks=False,onerror=scan_error):
                    if stop.is_set(): raise ImportCancelled()
                    dirs[:]=sorted(d for d in dirs if not d.startswith('.'))
                    for name in sorted(names):
                        if not name.startswith('.') and not Path(parent,name).is_symlink(): add(Path(parent,name))
            else:
                for p in paths: add(p,True)
            with api.connect() as db: db.execute("UPDATE library_imports SET state='running',total=? WHERE id=?",(len(files),jid))
            for sequence,(path,problem) in enumerate(files):
                if stop.is_set(): raise ImportCancelled()
                with api.connect() as db: db.execute('UPDATE library_imports SET current=? WHERE id=?',(Path(path).name,jid))
                try:
                    if problem: raise ValueError(problem)
                    with api.connect() as db:
                        result,duplicate=api.import_track(db,path)
                        db.execute('INSERT INTO library_import_items VALUES (?,?,?,?,?,?)',(jid,sequence,path,'duplicate' if duplicate else 'imported',result['id'],None))
                except ImportCancelled: raise
                except Exception as error:
                    with api.connect() as db: db.execute('INSERT INTO library_import_items VALUES (?,?,?,?,?,?)',(jid,sequence,path,'failed',None,str(error)))
            state='completed';error=None
        except ImportCancelled: state='cancelled';error=None
        except Exception as exc: state='failed';error=str(exc)
        finally:
            with api.connect() as db: db.execute('UPDATE library_imports SET state=?,error=?,current=? WHERE id=?',(state,error,'',jid))
            RUNNING.pop(jid,None); THREADS.pop(jid,None)
    thread=threading.Thread(target=work,name='library-import',daemon=True);THREADS[jid]=thread;thread.start()
    return status(api,jid,0)

def status(api,jid,cursor=0):
    if type(cursor) is not int or cursor<0: raise ValueError('Invalid import cursor')
    with api.connect() as db:
        row=db.execute('SELECT * FROM library_imports WHERE id=?',(jid,)).fetchone()
        if row is None: raise ValueError('Import not found')
        job=dict(row)
        if job['state'] in ('scanning','running') and jid not in RUNNING:
            db.execute("UPDATE library_imports SET state='interrupted',error='Import interrupted; choose the files or folder again.' WHERE id=?",(jid,))
            job.update(state='interrupted',error='Import interrupted; choose the files or folder again.')
        counts={r['state']:r['n'] for r in db.execute('SELECT state,count(*) AS n FROM library_import_items WHERE job_id=? GROUP BY state',(jid,))}
        job.update(imported=counts.get('imported',0),duplicates=counts.get('duplicate',0),failed=counts.get('failed',0))
        job['processed']=sum(counts.values())
        job['items']=[dict(r) for r in db.execute('SELECT sequence,path,state,track_id,error FROM library_import_items WHERE job_id=? AND sequence>=? ORDER BY sequence LIMIT 100',(jid,cursor))]
        job['cursor']=job['items'][-1]['sequence']+1 if job['items'] else cursor
        return job

def cancel(api,jid):
    if jid in RUNNING: RUNNING[jid].set()
    return status(api,jid)

def shutdown():
    for event in list(RUNNING.values()):event.set()
    for thread in list(THREADS.values()):thread.join(timeout=3)
