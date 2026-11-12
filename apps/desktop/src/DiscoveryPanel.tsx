import {Disclosure} from './Disclosure';
import React,{useEffect,useRef,useState} from 'react';
import {invoke,convertFileSrc} from '@tauri-apps/api/core';
import {Search,Link2,Download,RotateCcw,X,Music2,ChevronRight,ExternalLink,FolderOpen} from 'lucide-react';

export type AcquisitionJob={id:string;state:string;stage:string;title:string;artist:string;version:string;downloaded_bytes:number;total_bytes:number|null;track_id:string|null;path:string|null;error:string|null;duplicate:boolean;attempt:number;source_url:string;quality:null|{codec:string;container:string;sample_rate:number;channels:number;bit_rate:number;duration:number;bytes:number;sha256:string;resolved_url:string;converted:boolean;reference_url:string|null}};
type Candidate={id:string;adapter?:'youtube';kind:'direct'|'reference';provider:string;values:Record<string,string>;version:string;source_url?:string;display_url?:string;url?:string;filename?:string;declared_bytes?:number|null;duration?:number|null;release_id?:string;release_kind?:string;release_country?:string;creator?:string;guidance?:string};
type Provider={id:string;name:string;state:string;url_resolution:boolean;search:boolean;metadata:boolean;acquisition:boolean;authentication:string;formats:string[]};
type Capabilities={download_directory:string;max_bytes:number;providers:Provider[]};
type Result={kind?:string;query?:{title:string;artist:string;version:string};candidates?:Candidate[];message?:string|null;path?:string;reference_id?:string};
type Task={id:string;state:string;error:string|null;result:Result|null};
export type AcquisitionReview={source_id:string;reference_id?:string;values:Record<string,string>;version:string;artwork_path:string|null};
const call=<T,>(method:string,params:object={})=>invoke<T>('engine_call',{method,params});
const activeStates=['queued','downloading','verifying','importing','cancelling'];
const bytes=(value:number)=>value>=1024*1024?`${(value/1024/1024).toFixed(1)} MB`:`${Math.round(value/1024)} KB`;
const duration=(value:number)=>`${Math.floor(value/60)}:${Math.floor(value%60).toString().padStart(2,'0')}`;

export function DiscoveryPanel({jobs,error:queueError,onQueue,onCancel,onRetry,onOpen,onRefresh,onImport}:{jobs:AcquisitionJob[];error:string;onQueue:(review:AcquisitionReview)=>Promise<void>;onCancel:(id:string)=>Promise<void>;onRetry:(id:string)=>Promise<void>;onOpen:(id:string)=>Promise<void>;onRefresh:()=>Promise<void>;onImport:()=>Promise<void>}){
 const [query,setQuery]=useState(''),[sourceUrl,setSourceUrl]=useState(''),[capabilities,setCapabilities]=useState<Capabilities|null>(null);
 const [task,setTask]=useState<Task|null>(null),[busy,setBusy]=useState(false),[error,setError]=useState(''),[message,setMessage]=useState('');
 const [references,setReferences]=useState<Candidate[]>([]),[reference,setReference]=useState<Candidate|null>(null),[source,setSource]=useState<Candidate|null>(null);
 const [values,setValues]=useState<Record<string,string>>({title:'',artist:''}),[version,setVersion]=useState(''),[artwork,setArtwork]=useState<string|null>(null);
 const generation=useRef(0),intent=useRef<'search'|'source'|'cover'>('search');
 useEffect(()=>{let stopped=false;call<Capabilities>('providers.capabilities').then(c=>{if(!stopped)setCapabilities(c);}).catch(e=>{if(!stopped)setError(String(e));});return()=>{stopped=true;generation.current++;};},[]);
 const working=busy || task?.state==='running';
 const action=async(fn:()=>Promise<void>)=>{setBusy(true);setError('');try{await fn();}catch(e){setError(String(e));}finally{setBusy(false);}};
 const resolve=(text:string,kind:'search'|'source')=>action(async()=>{
  setMessage('');setSource(null);
  if(kind==='search'){setReferences([]);setReference(null);setArtwork(null);setValues({title:'',artist:''});setVersion('');setSourceUrl('');}
  intent.current=kind;const current=++generation.current;
  const next=await call<Task>('providers.resolve',{query:text});if(current===generation.current)setTask(next);
 });
 useEffect(()=>{
  if(!task || task.state!=='running')return;let stopped=false;
  const timer=window.setTimeout(async()=>{
   try{
    const next=await call<Task>('providers.job',{id:task.id});if(stopped)return;setTask(next);
    if(next.error){setError(next.error);return;}
    const result=next.result;if(!result)return;
    if(result.path && result.reference_id===reference?.id)setArtwork(result.path);
    if(result.kind==='references'){setReferences(result.candidates || []);setMessage(result.message || 'Choose the recording, then add its direct audio download link.');}
    if(result.kind==='service_reference'){
     const found=result.candidates?.[0];if(!found)return;
     if(intent.current==='source'){setError('This is a reference page. Paste a direct audio file link.');return;}
     selectReference(found);
    }
    if(result.kind==='direct'){
     const found=result.candidates?.[0];if(!found)return;
     setSource(found);setSourceUrl(found.url || '');
     if(!reference){setValues(found.values);setVersion(found.version || '');}
     setMessage(found.adapter==='youtube'?'Audio found. Review the track, then download and import.':'Review the title and version before adding this source.');
    }
   }catch(e){if(!stopped){setTask(null);setError(String(e));}}
  },400);
  return()=>{stopped=true;clearTimeout(timer);};
 },[task,reference?.id]);
 const selectReference=(candidate:Candidate)=>{setReference(candidate);setValues(candidate.values);setVersion(candidate.version || '');setArtwork(null);setSource(null);setSourceUrl('');setError('');setMessage(candidate.guidance || 'Add the direct download link for this version.');};
 return <section className="discovery-workspace">
  <form className="discovery-search" onSubmit={e=>{e.preventDefault();void resolve(query,'search');}}>
   <label htmlFor="discovery-query">Song name or audio URL</label>
   <div><Search size={18}/><input id="discovery-query" placeholder="Artist — title or https://…" value={query} maxLength={4096} disabled={working} onChange={e=>setQuery(e.target.value)}/><button className="primary" disabled={working || !query.trim()} type="submit">Find source</button></div>
  </form>
  {!source && !reference && references.length===0 && !working && !message && <p className="discovery-help">Search by name, paste a service link, or use a direct audio file URL.</p>}
  {working && <p className="discovery-message" role="status">{intent.current==='cover'?'Finding release artwork…':'Resolving source…'}</p>}
  {message && <p className="discovery-message" role="status">{message}</p>}
  {(error || queueError) && <p className="metadata-error" role="alert">{error || queueError}</p>}
  {references.length>0 && <div className="discovery-references" role="group" aria-label="Recording references">{references.map(candidate=><button key={candidate.id} disabled={working} aria-pressed={reference?.id===candidate.id} onClick={()=>selectReference(candidate)}>
   <strong>{candidate.values.title}</strong><span>{candidate.values.artist || 'Artist unavailable'}</span><small>{[candidate.values.album,candidate.values.date,candidate.release_country,candidate.duration!=null?duration(candidate.duration):''].filter(Boolean).join(' · ')}</small>
  </button>)}</div>}
  {(reference || source) && <div className="discovery-review">
   <div className="discovery-review-heading"><h2>Review source</h2>{source && <span>{source.adapter==='youtube'?`${source.provider} · M4A · No conversion`:'Original file · No conversion'}</span>}</div>
   {reference && <div className="discovery-reference-source"><span>Recording reference · {reference.provider}</span>{(reference.creator || reference.values.album || reference.version) && <small>{[reference.creator && 'Uploaded by '+reference.creator,reference.values.album,reference.version].filter(Boolean).join(' · ')}</small>}{reference.guidance && reference.source_url && <div className="service-reference-actions"><button disabled={working} onClick={()=>void action(()=>invoke<void>('open_reference',{url:reference.source_url}))}><ExternalLink size={14}/> Open on {reference.provider}</button><button disabled={working} onClick={()=>void action(onImport)}><FolderOpen size={14}/> Add local file</button></div>}</div>}
   {reference && <form className="direct-url-form" onSubmit={e=>{e.preventDefault();void resolve(sourceUrl,'source');}}><label htmlFor="direct-audio-url">Direct audio download URL</label><div><Link2 size={16}/><input id="direct-audio-url" type="url" placeholder="https://artist.example/track.flac" maxLength={4096} disabled={working} value={sourceUrl} onChange={e=>{setSourceUrl(e.target.value);setSource(null);}}/><button disabled={working || !sourceUrl.trim()}>Resolve link</button></div></form>}
   {source && <p className="discovery-source-url" title={source.display_url}>{source.display_url}{source.declared_bytes!=null?` · ${bytes(source.declared_bytes)}`:''}</p>}
   <div className="discovery-review-body">
    {reference?.release_id && <div className="discovery-cover">{artwork?<img src={convertFileSrc(artwork)} alt="Selected release cover"/>:<div><Music2 size={26}/></div>}<button disabled={working} onClick={()=>void action(async()=>{intent.current='cover';setTask(await call<Task>('providers.cover',{reference_id:reference.id}));})}>Load cover</button></div>}
    <div className="discovery-fields"><label>Title<input aria-label="Download title" maxLength={500} disabled={working} value={values.title || ''} onChange={e=>setValues({...values,title:e.target.value})}/></label><label>Artist<input aria-label="Download artist" maxLength={500} disabled={working} value={values.artist || ''} onChange={e=>setValues({...values,artist:e.target.value})}/></label><label>Version<input aria-label="Download version" maxLength={200} disabled={working} placeholder="Original, remix or edit, if known" value={version} onChange={e=>setVersion(e.target.value)}/></label></div>
   </div>
   <div className="discovery-review-actions"><small>File integrity and audio format are checked after download. Metadata is saved to the library; file tags stay unchanged.</small><button className="primary" disabled={working || !source || !values.title?.trim()} onClick={()=>void action(async()=>{if(!source)return;await onQueue({source_id:source.id,...(reference?{reference_id:reference.id}:{}),values,version,artwork_path:artwork});setMessage('Added to downloads. Analysis starts after import.');})}><Download size={16}/> Download &amp; import</button></div>
  </div>}
  <div className="download-queue-head"><h2>Downloads</h2><button className="icon" aria-label="Refresh downloads" title="Refresh downloads" onClick={()=>void action(onRefresh)}><RotateCcw size={15}/></button></div>
  {jobs.length===0?<p className="discovery-help">Downloaded tracks will appear here and in your library.</p>:<div className="download-queue">{jobs.map(job=>{
   const active=activeStates.includes(job.state),quality=job.quality;
   return <article className="download-row" key={job.id}>
    <div className="download-row-head"><div><strong>{job.title}</strong>{job.artist && <span>{job.artist}{job.version?` · ${job.version}`:''}</span>}</div><span className="download-state">{job.stage}{job.attempt>1?` · Attempt ${job.attempt}`:''}</span>
     {active && <button disabled={job.state==='cancelling'} aria-label={`Cancel download ${job.title}`} title="Cancel download" onClick={()=>void action(()=>onCancel(job.id))}><X size={15}/></button>}
     {['failed','cancelled','interrupted'].includes(job.state) && <button disabled={busy} onClick={()=>void action(()=>onRetry(job.id))}><RotateCcw size={14}/> Retry</button>}
     {job.state==='completed' && job.track_id && <button onClick={()=>void action(()=>onOpen(job.track_id!))}>Open track<ChevronRight size={14}/></button>}
    </div>
    {active && <div className="download-progress"><progress aria-label={`Download progress ${job.title}`} {...(job.total_bytes && job.total_bytes>0?{max:job.total_bytes,value:Math.min(job.downloaded_bytes,job.total_bytes)}:{})}/><small>{bytes(job.downloaded_bytes)}{job.total_bytes!=null?` / ${bytes(job.total_bytes)}`:''}</small></div>}
    {job.error && <p className="metadata-error" role="alert">{job.error}</p>}
    {quality && <p className="download-quality">{quality.container.toUpperCase()}{quality.codec!==quality.container?` · ${quality.codec.toUpperCase()}`:''}{quality.bit_rate?` · ${Math.round(quality.bit_rate/1000)} kbps`:''}{quality.sample_rate?` · ${quality.sample_rate/1000} kHz`:''}{quality.channels?` · ${quality.channels===2?'Stereo':quality.channels===1?'Mono':`${quality.channels} channels`}`:''} · {duration(quality.duration)} · {bytes(quality.bytes)} · Original source{job.duplicate?' · Existing track kept':''}</p>}
    <Disclosure className="download-provenance" label={<> Source &amp; file </>}><label>Source<input readOnly value={job.source_url} onClick={e=>e.currentTarget.select()}/></label>{quality?.resolved_url!==job.source_url && quality?.resolved_url && <label>Resolved URL<input readOnly value={quality.resolved_url} onClick={e=>e.currentTarget.select()}/></label>}{job.path && <label>Saved file<input readOnly value={job.path} onClick={e=>e.currentTarget.select()}/></label>}{quality && <small>Downloaded SHA-256 <code>{quality.sha256}</code></small>}{quality?.reference_url && <label>Chosen reference<input readOnly value={quality.reference_url} onClick={e=>e.currentTarget.select()}/></label>}</Disclosure>
   </article>;
  })}</div>}
  {capabilities && <Disclosure className="provider-capabilities" label={<> Supported sources </>}><dl>{capabilities.providers.map(provider=><div key={provider.id}><dt>{provider.name}</dt><dd>{provider.acquisition?'Audio download available':provider.metadata?'References only':'Unavailable'}<small>{provider.authentication}</small></dd></div>)}</dl><p>Downloads folder: {capabilities.download_directory}</p><p>512 MB per file. Spotify and SoundCloud links are references.</p></Disclosure>}
 </section>;
}
