import {useEffect, useRef, useState} from 'react';
import {invoke} from '@tauri-apps/api/core';
import {AlertTriangle, Check, LoaderCircle, X} from 'lucide-react';

type BatchItem={id:string;title:string;state:string;stage?:string;error?:string|null};
type Batch={id:string;state:string;total:number;processed:number;applied:number;needs_review:number;failed:number;skipped:number;cancelled:number;items:BatchItem[];error?:string};
const call=<T,>(method:string,params:object={})=>invoke<T>('engine_call',{method,params});
const running=(job:Batch|null)=>!!job && ['running','cancelling'].includes(job.state);

export function useMetadataBatch(onUpdated:()=>Promise<void>) {
  const [job,setJob]=useState<Batch|null>(null);
  const [starting,setStarting]=useState(false),[error,setError]=useState('');
  const update=useRef(onUpdated);update.current=onUpdated;
  const revision=useRef(''),startingRef=useRef(false),mounted=useRef(true);
  const requestGeneration=useRef(0);
  const receive=useRef((next:Batch|null)=>{});
  receive.current=(next:Batch|null)=>{
    if(!mounted.current)return;
    setJob(next);
    if(next){const key=`${next.id}:${next.processed}:${next.state}`;if(revision.current!==key){revision.current=key;if(next.processed)void update.current().catch(e=>setError(String(e)));}}
  };
  useEffect(()=>{
    mounted.current=true;
    const generation=requestGeneration.current;
    void call<Batch|null>('metadata.batch.latest').then(next=>{
      if(generation===requestGeneration.current && next && (running(next) || localStorage.getItem('metadataBatchDismissed')!==next.id))receive.current(next);
    }).catch(e=>{if(mounted.current)setError(String(e));});
    return()=>{mounted.current=false;};
  },[]);
  useEffect(()=>{
    if(!job || !running(job))return;
    let cancelled=false;
    const timer=window.setTimeout(()=>{
      void call<Batch>('metadata.batch.get',{id:job.id}).then(next=>{if(!cancelled){setError('');receive.current(next);}}).catch(e=>{
        if(!cancelled){setError(String(e));setJob(current=>current?{...current}:current);}
      });
    },900);
    return()=>{cancelled=true;window.clearTimeout(timer);};
  },[job]);
  const start=async(ids:string[])=>{
    if(startingRef.current || running(job) || !ids.length)return;
    startingRef.current=true;setStarting(true);setError('');
    requestGeneration.current++;
    try{receive.current(await call<Batch>('metadata.batch.start',{track_ids:ids}));}
    catch(e){if(mounted.current)setError(String(e));}
    finally{startingRef.current=false;if(mounted.current)setStarting(false);}
  };
  const cancel=async()=>{
    if(!job)return;
    try{receive.current(await call<Batch>('metadata.batch.cancel',{id:job.id}));}
    catch(e){setError(String(e));}
  };
  const dismiss=()=>{if(job)localStorage.setItem('metadataBatchDismissed',job.id);setJob(null);setError('');};
  return {job,starting,error,start,cancel,dismiss,running:running(job)};
}

export function MetadataBatchProgress({batch,onReview}:{batch:ReturnType<typeof useMetadataBatch>;onReview:(ids:string[])=>void}) {
  const {job,error}=batch;
  if(!job)return error?<p className="metadata-batch-error" role="alert">{error}</p>:null;
  const reviewIds=job.items.filter(item=>!['queued','running','cancelled'].includes(item.state)).map(item=>item.id);
  const attention=job.needs_review+job.failed+job.skipped;
  const current=job.items.filter(item=>item.state==='running');
  const label=job.state==='cancelling'?'Stopping metadata analysis':batch.running?'Analyzing metadata':job.state==='completed'?'Metadata analysis complete':job.state==='interrupted'?'Metadata analysis interrupted':job.state==='failed'?'Metadata analysis stopped':'Metadata analysis cancelled';
  return <section className="metadata-batch-progress" aria-label="Automatic metadata progress">
    <div className="metadata-batch-summary" role="status" aria-live="polite">
      <strong>{batch.running?<LoaderCircle size={15} className="spin" aria-hidden="true"/>:job.state==='completed'?<Check size={15} aria-hidden="true"/>:<AlertTriangle size={15} aria-hidden="true"/>}{label}</strong>
      <span className="time">{job.processed} / {job.total}</span>
      <span>{job.applied} applied with covers{attention>0?` · ${attention} need review`:''}</span>
    </div>
    {batch.running && <><progress aria-label="Metadata analysis progress" value={job.processed} max={job.total}/><p className="metadata-batch-current">{current.map(item=>`${item.title} · ${item.stage || 'Analyzing'}`).join(' / ') || 'Preparing tracks…'}</p></>}
    {!batch.running && job.cancelled>0 && <p>{job.cancelled} tracks left unchanged.</p>}
    {error || job.error ? <p className="metadata-batch-error" role="alert">{error || job.error}</p>:null}
    <div className="metadata-batch-actions">
      <button disabled={!reviewIds.length} onClick={()=>onReview(reviewIds)}>Review results</button>
      {batch.running?<button disabled={job.state==='cancelling'} onClick={()=>void batch.cancel()}>Cancel analysis</button>:<button className="icon" aria-label="Dismiss metadata results" onClick={batch.dismiss}><X size={15}/></button>}
    </div>
  </section>;
}
