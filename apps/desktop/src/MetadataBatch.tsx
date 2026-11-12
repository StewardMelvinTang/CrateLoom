import {useEffect, useRef, useState} from 'react';
import {AlertTriangle, Check, LoaderCircle, X} from 'lucide-react';
import {call, message} from './engine';
import {Disclosure} from './Disclosure';
import './metadata.css';

type BatchItem={id:string;title:string;state:string;stage?:string;error?:string|null};
type Batch={id:string;state:string;total:number;processed:number;applied:number;needs_review:number;failed:number;skipped:number;cancelled:number;items:BatchItem[];error?:string};
const running=(job:Batch|null)=>!!job && ['running','cancelling'].includes(job.state);
const DISMISSED_KEY='metadataBatchDismissed';
const wasDismissed=(id:string)=>{try{return localStorage.getItem(DISMISSED_KEY)===id;}catch{return false;}};
const rememberDismissed=(id:string|null)=>{try{if(id)localStorage.setItem(DISMISSED_KEY,id);else localStorage.removeItem(DISMISSED_KEY);}catch{/* the banner still hides for this session */}};

export function useMetadataBatch(onUpdated:()=>Promise<void>) {
  const [job,setJob]=useState<Batch|null>(null);
  const [starting,setStarting]=useState(false),[error,setError]=useState('');
  // A dismissed result is collapsed to one line, not thrown away, so it can be reopened.
  const [dismissed,setDismissed]=useState(false);
  const update=useRef(onUpdated);update.current=onUpdated;
  const revision=useRef(''),startingRef=useRef(false),mounted=useRef(true);
  const requestGeneration=useRef(0);
  const receive=useRef((next:Batch|null)=>{});
  receive.current=(next:Batch|null)=>{
    if(!mounted.current)return;
    setJob(next);
    if(next){const key=`${next.id}:${next.processed}:${next.state}`;if(revision.current!==key){revision.current=key;if(next.processed)void update.current().catch(e=>{if(mounted.current)setError(message(e));});}}
  };
  useEffect(()=>{
    mounted.current=true;
    const generation=requestGeneration.current;
    void call<Batch|null>('metadata.batch.latest').then(next=>{
      if(generation!==requestGeneration.current || !next || !mounted.current)return;
      setDismissed(!running(next) && wasDismissed(next.id));
      receive.current(next);
    }).catch(e=>{if(mounted.current)setError(message(e));});
    return()=>{mounted.current=false;};
  },[]);
  useEffect(()=>{
    if(!job || !running(job))return;
    let cancelled=false;
    const timer=window.setTimeout(()=>{
      void call<Batch>('metadata.batch.get',{id:job.id}).then(next=>{if(!cancelled){setError('');receive.current(next);}}).catch(e=>{
        if(!cancelled){setError(message(e));setJob(current=>current?{...current}:current);}
      });
    },900);
    return()=>{cancelled=true;window.clearTimeout(timer);};
  },[job]);
  const start=async(ids:string[])=>{
    if(startingRef.current || running(job) || !ids.length)return;
    startingRef.current=true;setStarting(true);setError('');
    requestGeneration.current++;
    try{const next=await call<Batch>('metadata.batch.start',{track_ids:ids});if(mounted.current)setDismissed(false);receive.current(next);}
    catch(e){if(mounted.current)setError(message(e));}
    finally{startingRef.current=false;if(mounted.current)setStarting(false);}
  };
  const cancel=async()=>{
    if(!job)return;
    try{receive.current(await call<Batch>('metadata.batch.cancel',{id:job.id}));}
    catch(e){if(mounted.current)setError(message(e));}
  };
  const dismiss=()=>{if(job)rememberDismissed(job.id);setDismissed(true);setError('');};
  const reopen=()=>{rememberDismissed(null);setDismissed(false);};
  return {job,starting,error,start,cancel,dismiss,reopen,dismissed,clearError:()=>setError(''),running:running(job)};
}

export function MetadataBatchProgress({batch,onReview}:{batch:ReturnType<typeof useMetadataBatch>;onReview:(ids:string[])=>void}) {
  const {job,error}=batch;
  const problem=error || job?.error;
  const alert=problem?<p className="metadata-batch-error" role="alert"><AlertTriangle size={14} aria-hidden="true"/><span>{problem}</span>{error && <button className="icon" aria-label="Dismiss message" title="Dismiss" onClick={batch.clearError}><X size={14}/></button>}</p>:null;
  if(!job)return alert;
  const reviewIds=job.items.filter(item=>!['queued','running','cancelled'].includes(item.state)).map(item=>item.id);
  const unresolved=job.items.filter(item=>['needs_review','failed','skipped'].includes(item.state));
  const attention=job.needs_review+job.failed+job.skipped;
  const current=job.items.filter(item=>item.state==='running');
  const label=job.state==='cancelling'?'Stopping metadata analysis':batch.running?'Analyzing metadata':job.state==='completed'?'Metadata analysis complete':job.state==='interrupted'?'Metadata analysis interrupted':job.state==='failed'?'Metadata analysis stopped':'Metadata analysis cancelled';
  const counts=`${job.applied} applied with covers${attention>0?` · ${attention} need review`:''}`;
  if(batch.dismissed && !batch.running)return <section className="metadata-batch-progress metadata-batch-collapsed" aria-label="Automatic metadata results">
    <span>Last Auto metadata run: {counts}</span>
    <button onClick={batch.reopen}>Show results</button>
  </section>;
  return <section className="metadata-batch-progress" aria-label="Automatic metadata progress">
    <div className="metadata-batch-summary" role="status" aria-live="polite">
      <strong>{batch.running?<LoaderCircle size={15} className="spin" aria-hidden="true"/>:job.state==='completed'?<Check size={15} aria-hidden="true"/>:<AlertTriangle size={15} aria-hidden="true"/>}{label}</strong>
      <span className="time">{job.processed} / {job.total}</span>
      <span>{counts}</span>
    </div>
    {batch.running && <><progress aria-label="Metadata analysis progress" value={job.processed} max={job.total}/><p className="metadata-batch-current">{current.map(item=>`${item.title} · ${item.stage || 'Analyzing'}`).join(' / ') || 'Preparing tracks…'}</p></>}
    {!batch.running && job.cancelled>0 && <p>{job.cancelled} tracks left unchanged.</p>}
    {!batch.running && job.processed>0 && <p className="metadata-batch-explain">{job.applied>0?'Applied tracks have new details and a cover saved in the library. Their files are unchanged until you write tags. ':''}{attention>0?'Tracks that need review were left as they were.':''}</p>}
    {alert}
    {!batch.running && unresolved.length>0 && <Disclosure className="metadata-batch-reasons" label={`Why ${unresolved.length} need review`}><ul>{unresolved.map(item=><li key={item.id}><strong>{item.title}</strong><span>{item.error || (item.state==='failed'?'The lookup failed.':'No confident match was found.')}</span></li>)}</ul></Disclosure>}
    <div className="metadata-batch-actions">
      <button disabled={!reviewIds.length} title="Open each analyzed track to check its details, cover and matches" onClick={()=>onReview(reviewIds)}>Review results</button>
      {!batch.running && unresolved.length>0 && unresolved.length<reviewIds.length && <button title="Open only the tracks Auto metadata could not finish" onClick={()=>onReview(unresolved.map(item=>item.id))}>Review the {unresolved.length} that need it</button>}
      {batch.running?<button disabled={job.state==='cancelling'} onClick={()=>void batch.cancel()}>{job.state==='cancelling'?'Stopping…':'Cancel analysis'}</button>:<button className="icon" aria-label="Dismiss metadata results" title="Hide these results. You can show them again from this spot." onClick={batch.dismiss}><X size={15}/></button>}
    </div>
  </section>;
}
