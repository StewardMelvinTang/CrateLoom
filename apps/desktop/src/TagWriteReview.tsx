import {useEffect, useRef, useState} from 'react';
import {AlertTriangle, Check, X} from 'lucide-react';
import {call, message} from './engine';
import {Disclosure} from './Disclosure';
import {useDialogFocus} from './useDialogFocus';
import type {MetadataState} from './MetadataEditor';
import './metadata.css';

type Row={id:string;title:string;metadata:MetadataState};
type Outcome={id:string;title:string;ok:boolean;text:string;note?:string;backup?:string|null};
const FIELD_LABELS:Record<string,string>={title:'Title',artist:'Artist',album:'Album',genre:'Genre',date:'Release date'};
const files=(count:number)=>`${count} ${count===1?'file':'files'}`;

/** Lists the changes saved in the library for the chosen tracks, then writes them into the audio files' tags. */
export function TagWriteReview({ids,onClose,onSaved}:{ids:string[];onClose:()=>void;onSaved:()=>Promise<void>}){
 const [rows,setRows]=useState<Row[]>([]),[loaded,setLoaded]=useState(0),[loading,setLoading]=useState(true),[writing,setWriting]=useState(false),[error,setError]=useState(''),[results,setResults]=useState<Outcome[]>([]);
 const stop=useRef(false),mounted=useRef(true);
 const dialog=useDialogFocus(onClose,writing);
 // Leaving the dialog stops the batch after the file being written; a write in progress is never interrupted.
 useEffect(()=>{mounted.current=true;return()=>{mounted.current=false;stop.current=true;};},[]);
 useEffect(()=>{
  let closed=false;
  setLoading(true);setLoaded(0);setError('');
  (async()=>{
   try{
    const found:Row[]=[];
    for(const id of ids){
     const track=await call<{title:string}>('track.get',{id});const metadata=await call<MetadataState>('metadata.get',{id});
     if(closed)return;
     if(Object.keys(metadata.pending).length || metadata.artwork_pending)found.push({id,title:track.title,metadata});
     setLoaded(count=>count+1);
    }
    if(!closed)setRows(found);
   }catch(e){if(!closed)setError(message(e));}
   finally{if(!closed)setLoading(false);}
  })();
  return()=>{closed=true;};
 },[ids]);
 const write=async()=>{
  if(writing)return;
  setWriting(true);setError('');stop.current=false;
  const attempted=new Set<string>();
  try{
   for(const row of rows){
    if(stop.current)break;
    attempted.add(row.id);
    let outcome:Outcome;
    try{
     const result=await call<{written?:boolean;backup:string|null;artwork_embedded?:boolean;note?:string}>('metadata.write',{id:row.id,revision:row.metadata.revision});
     outcome={id:row.id,title:row.title,ok:true,text:result.written===false?'Nothing needed writing to the file':'Tags written to the file',note:result.note,backup:result.backup};
    }catch(e){outcome={id:row.id,title:row.title,ok:false,text:message(e)};}
    if(mounted.current)setResults(items=>[...items.filter(item=>item.id!==row.id),outcome]);
   }
   await onSaved();
  }catch(e){if(mounted.current)setError(message(e));}
  finally{if(mounted.current){setWriting(false);setRows(current=>current.filter(row=>!attempted.has(row.id)));}}
 };
 const failed=results.filter(result=>!result.ok).length;
 return <div className="modal-backdrop"><section ref={dialog} tabIndex={-1} className="metadata-window tag-write-window" role="dialog" aria-modal="true" aria-label="Write tags to files">
  <div className="section-window-head"><h2>Write tags to files</h2><button disabled={writing} aria-label="Close tag write review" title={writing?'Wait for the current file, or stop first':'Close'} onClick={onClose}><X size={17}/></button></div>
  <p className="metadata-write-note">These changes are saved in the CrateLoom library. Writing puts them into the audio files’ tags. The audio itself is not changed: each file is verified and a backup of the original is kept.</p>
  {loading && <p role="status">Checking saved changes… {loaded} / {ids.length}</p>}
  {error && <p className="metadata-error" role="alert">{error}</p>}
  {!loading && !rows.length && !results.length && !error && <p>None of the selected tracks has changes waiting to be written.</p>}
  {rows.length>0 && <h3 className="tag-review-heading">{results.length?'Still to write':'To write'} · {files(rows.length)}</h3>}
  {rows.map(row=><div className="tag-review-row" key={row.id}><strong>{row.title}</strong>
   {Object.entries(row.metadata.pending).map(([key,value])=><span key={key}>{FIELD_LABELS[key] || key}: {value?`“${value}”`:'remove this tag'}</span>)}
   {row.metadata.artwork_pending && <span>Cover: {row.metadata.artwork_supported===false?'kept in the library only; this file format cannot hold a cover':row.metadata.artwork_path?'replace the cover in the file':'remove the cover from the file'}</span>}
  </div>)}
  {results.length>0 && <h3 className="tag-review-heading">Results · {results.length-failed} done{failed?` · ${failed} failed`:''}</h3>}
  <div className="tag-write-results" role="status">{results.map(result=><div className="tag-review-row" key={result.id}><strong>{result.title}</strong>
   <span className="tag-write-outcome">{result.ok?<Check size={13} aria-hidden="true"/>:<AlertTriangle size={13} aria-hidden="true"/>}{result.ok?result.text:`Not written: ${result.text}`}</span>
   {result.note && <span>{result.note}</span>}
   {result.backup && <Disclosure label="Backup location"><code>{result.backup}</code></Disclosure>}
  </div>)}</div>
  <div className="metadata-actions">
   {writing?<button onClick={()=>{stop.current=true;}}>Stop after the current file</button>
    :results.length && !rows.length?<button className="primary" onClick={onClose}>Done</button>
    :<><button onClick={onClose}>{results.length?'Close':'Cancel'}</button><button className="primary" disabled={loading || !rows.length} onClick={()=>void write()}>Write tags to {files(rows.length)}</button></>}
   {writing && <span role="status">Writing {Math.min(results.length+1,results.length+rows.length)} of {results.length+rows.length}…</span>}
  </div>
 </section></div>;
}
