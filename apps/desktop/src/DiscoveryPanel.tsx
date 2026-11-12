import {useEffect, useState, type ReactNode} from 'react';
import {invoke, convertFileSrc} from '@tauri-apps/api/core';
import {AlertTriangle, Check, ChevronRight, Download, ExternalLink, FolderOpen, Info, Link2, LoaderCircle, Music2, RotateCcw, Search, X} from 'lucide-react';
import {call, message as errorText} from './engine';
import {Disclosure} from './Disclosure';
import {clearFailedDownloads, describeDownloadError, downloadStateLabel, formatBytes, groupDownloads, isActiveDownload, isRetryableDownload} from './Downloads';
import './discovery.css';

export type AcquisitionJob={id:string;state:string;stage:string;title:string;artist:string;version:string;downloaded_bytes:number;total_bytes:number|null;track_id:string|null;path:string|null;error:string|null;duplicate:boolean;attempt:number;source_url:string;quality:null|{codec:string;container:string;sample_rate:number;channels:number;bit_rate:number;duration:number;bytes:number;sha256:string;resolved_url:string;converted:boolean;reference_url:string|null}};
type Candidate={id:string;adapter?:'youtube';kind:'direct'|'reference';provider:string;values:Record<string,string>;version:string;source_url?:string;display_url?:string;url?:string;filename?:string;declared_bytes?:number|null;duration?:number|null;release_id?:string;release_kind?:string;release_country?:string;creator?:string;guidance?:string};
type Provider={id:string;name:string;state:string;url_resolution:boolean;search:boolean;metadata:boolean;acquisition:boolean;authentication:string;formats:string[]};
type Capabilities={download_directory:string;max_bytes:number;providers:Provider[]};
type Result={kind?:string;query?:{title:string;artist:string;version:string};candidates?:Candidate[];message?:string|null;path?:string;reference_id?:string};
type Task={id:string;state:string;error:string|null;result:Result|null};
export type AcquisitionReview={source_id:string;reference_id?:string;values:Record<string,string>;version:string;artwork_path:string|null};
const duration=(value:number)=>`${Math.floor(value/60)}:${Math.floor(value%60).toString().padStart(2,'0')}`;

// Find Music unmounts whenever another workspace is opened. The search lives here so a typed
// query, a running lookup and its results are still there when the user comes back.
type Memory={query:string;sourceUrl:string;task:Task|null;intent:'search'|'source'|'cover';references:Candidate[];reference:Candidate|null;source:Candidate|null;values:Record<string,string>;version:string;artwork:string|null;note:string;error:string;generation:number};
const memory:Memory={query:'',sourceUrl:'',task:null,intent:'search',references:[],reference:null,source:null,values:{title:'',artist:''},version:'',artwork:null,note:'',error:'',generation:0};
function useRemembered<K extends keyof Memory>(key:K):[Memory[K],(value:Memory[K])=>void] {
  const [value,setValue]=useState(memory[key]);
  // Writing to the store first means a result that arrives while the panel is closed is not lost.
  return [value,next=>{memory[key]=next;setValue(next);}];
}

function Notice({children,tone='error',onDismiss,action}:{children:ReactNode;tone?:'error'|'status';onDismiss?:()=>void;action?:ReactNode}) {
  return <div className="discovery-notice" data-tone={tone} role={tone==='error'?'alert':'status'}>
    {tone==='error'?<AlertTriangle size={15} aria-hidden="true"/>:<Info size={15} aria-hidden="true"/>}
    <span>{children}</span>
    {action}
    {onDismiss && <button type="button" className="discovery-notice-dismiss" aria-label="Dismiss message" title="Dismiss" onClick={onDismiss}><X size={14}/></button>}
  </div>;
}

export function DiscoveryPanel({jobs,error:queueError,onQueue,onCancel,onRetry,onOpen,onRefresh,onImport}:{jobs:AcquisitionJob[];error:string;onQueue:(review:AcquisitionReview)=>Promise<void>;onCancel:(id:string)=>Promise<void>;onRetry:(id:string)=>Promise<void>;onOpen:(id:string)=>Promise<void>;onRefresh:()=>Promise<void>;onImport:()=>Promise<void>}){
 const [query,setQuery]=useRemembered('query'),[sourceUrl,setSourceUrl]=useRemembered('sourceUrl');
 const [task,setTask]=useRemembered('task'),[intent,setIntent]=useRemembered('intent');
 const [references,setReferences]=useRemembered('references'),[reference,setReference]=useRemembered('reference'),[source,setSource]=useRemembered('source');
 const [values,setValues]=useRemembered('values'),[version,setVersion]=useRemembered('version'),[artwork,setArtwork]=useRemembered('artwork');
 const [note,setNote]=useRemembered('note'),[error,setError]=useRemembered('error');
 const [capabilities,setCapabilities]=useState<Capabilities|null>(null);
 const [clearing,setClearing]=useState(false),[listError,setListError]=useState('');
 const [busy,setBusy]=useState(false),[rowBusy,setRowBusy]=useState<string[]>([]),[rowErrors,setRowErrors]=useState<Record<string,string>>({});
 useEffect(()=>{let stopped=false;call<Capabilities>('providers.capabilities').then(c=>{if(!stopped)setCapabilities(c);}).catch(()=>{/* only the optional "what can be downloaded" list is missing */});return()=>{stopped=true;};},[]);
 const looking=task?.state==='running';
 const working=busy || looking;
 const action=async(fn:()=>Promise<void>)=>{if(busy)return;setBusy(true);setError('');try{await fn();}catch(e){setError(errorText(e));}finally{setBusy(false);}};
 const rowAction=async(id:string,fn:()=>Promise<void>)=>{
  if(rowBusy.includes(id))return;
  setRowBusy(ids=>[...ids,id]);setRowErrors(errors=>({...errors,[id]:''}));
  try{await fn();}catch(e){setRowErrors(errors=>({...errors,[id]:errorText(e)}));}finally{setRowBusy(ids=>ids.filter(item=>item!==id));}
 };
 const selectReference=(candidate:Candidate)=>{setReference(candidate);setValues(candidate.values);setVersion(candidate.version || '');setArtwork(null);setSource(null);setSourceUrl('');setError('');setNote('');};
 const startOver=()=>{memory.generation++;setTask(null);setReferences([]);setReference(null);setSource(null);setArtwork(null);setValues({title:'',artist:''});setVersion('');setSourceUrl('');setNote('');setError('');};
 const resolve=(text:string,kind:'search'|'source')=>action(async()=>{
  setNote('');setSource(null);
  if(kind==='search'){setReferences([]);setReference(null);setArtwork(null);setValues({title:'',artist:''});setVersion('');setSourceUrl('');}
  setIntent(kind);const current=++memory.generation;
  const next=await call<Task>('providers.resolve',{query:text.trim()});if(current===memory.generation)setTask(next);
 });
 useEffect(()=>{
  if(!task || task.state!=='running')return;let stopped=false;
  const started=memory.generation;
  const timer=window.setTimeout(async()=>{
   try{
    const next=await call<Task>('providers.job',{id:task.id});
    // Compare with the store, not this render: the panel may have been closed and reopened meanwhile.
    if(started!==memory.generation || memory.task?.id!==task.id)return;
    if(stopped && next.state==='running')return;
    setTask(next);
    if(next.error){setError(next.error);return;}
    const result=next.result;if(!result)return;
    if(result.path && result.reference_id===memory.reference?.id)setArtwork(result.path);
    if(result.kind==='references'){
     setReferences(result.candidates || []);
     if(!result.candidates?.length)setNote(result.message || 'No recording was found for that search. Check the spelling, or paste a link to the track instead.');
    }
    if(result.kind==='service_reference'){
     const found=result.candidates?.[0];if(!found)return;
     if(memory.intent==='source'){setError(`That is a ${found.provider} page, not an audio file. Paste a YouTube link or a direct link to an audio file.`);return;}
     selectReference(found);
    }
    if(result.kind==='direct'){
     const found=result.candidates?.[0];if(!found)return;
     setSource(found);setSourceUrl(found.url || '');
     if(!memory.reference){setValues(found.values);setVersion(found.version || '');}
    }
   }catch(e){if(started===memory.generation){setTask(null);setError(errorText(e));}}
  },400);
  return()=>{stopped=true;clearTimeout(timer);};
 },[task]);
 const {active,unfinished,completed}=groupDownloads(jobs);
 const limit=capabilities?`${Math.round(capabilities.max_bytes/1024/1024)} MB`:null;
 const downloadRow=(job:AcquisitionJob)=>{
  const quality=job.quality,pending=rowBusy.includes(job.id),problem=describeDownloadError(job.error);
  return <article className="download-row" key={job.id}>
   <div className="download-row-head"><div><strong title={job.title}>{job.title}</strong>{(job.artist || job.version) && <span>{[job.artist,job.version].filter(Boolean).join(' · ')}</span>}</div>
    <span className="download-state">{downloadStateLabel(job)}{job.attempt>1?` · Attempt ${job.attempt}`:''}</span>
    {isActiveDownload(job.state) && <button disabled={pending || job.state==='cancelling'} aria-label={`Cancel download ${job.title}`} title="Cancel this download" onClick={()=>void rowAction(job.id,()=>onCancel(job.id))}><X size={14} aria-hidden="true"/> Cancel</button>}
    {isRetryableDownload(job.state) && <button disabled={pending} aria-label={`Retry download ${job.title}`} title="Start this download again" onClick={()=>void rowAction(job.id,()=>onRetry(job.id))}><RotateCcw size={14} aria-hidden="true"/> Retry</button>}
    {job.state==='completed' && job.track_id && <button disabled={pending} aria-label={`Open ${job.title} in the library`} title="Show this track in the library" onClick={()=>void rowAction(job.id,()=>onOpen(job.track_id!))}>Open track<ChevronRight size={14} aria-hidden="true"/></button>}
   </div>
   {isActiveDownload(job.state) && <div className="download-progress"><progress aria-label={`Download progress ${job.title}`} {...(job.total_bytes && job.total_bytes>0?{max:job.total_bytes,value:Math.min(job.downloaded_bytes,job.total_bytes)}:{})}/><small>{formatBytes(job.downloaded_bytes)}{job.total_bytes!=null?` / ${formatBytes(job.total_bytes)}`:''}</small></div>}
   {problem && <p className="download-problem-text">{problem.text}</p>}
   {rowErrors[job.id] && <Notice onDismiss={()=>setRowErrors(errors=>({...errors,[job.id]:''}))}>{rowErrors[job.id]}</Notice>}
   {quality && <p className="download-quality">{quality.container.toUpperCase()}{quality.codec!==quality.container?` · ${quality.codec.toUpperCase()}`:''}{quality.bit_rate?` · ${Math.round(quality.bit_rate/1000)} kbps`:''}{quality.sample_rate?` · ${quality.sample_rate/1000} kHz`:''}{quality.channels?` · ${quality.channels===2?'Stereo':quality.channels===1?'Mono':`${quality.channels} channels`}`:''} · {duration(quality.duration)} · {formatBytes(quality.bytes)}{quality.converted?'':' · Not converted'}{job.duplicate?' · Already in your library, so the existing track was kept':''}</p>}
   <Disclosure className="download-provenance" label="Source and file details">{problem?.detail && <small>Error reported: <code>{problem.detail}</code></small>}<label>Source link<input readOnly value={job.source_url} onFocus={e=>e.currentTarget.select()}/></label>{quality?.resolved_url!==job.source_url && quality?.resolved_url && <label>Downloaded from<input readOnly value={quality.resolved_url} onFocus={e=>e.currentTarget.select()}/></label>}{quality?.reference_url && <label>Recording reference<input readOnly value={quality.reference_url} onFocus={e=>e.currentTarget.select()}/></label>}{job.path && <label>Saved file<input readOnly value={job.path} onFocus={e=>e.currentTarget.select()}/></label>}{quality && <small>File checksum (SHA-256) <code>{quality.sha256}</code></small>}</Disclosure>
  </article>;
 };
 const group=(label:string,icon:ReactNode,items:AcquisitionJob[],tool?:ReactNode)=>items.length>0 && <section className="download-group" aria-label={label}><h3>{icon}<span>{label} · {items.length}</span>{tool}</h3>{items.map(downloadRow)}</section>;
 const clearFailed=async()=>{if(clearing)return;setClearing(true);setListError('');try{await clearFailedDownloads();await onRefresh();}catch(e){setListError(errorText(e));}finally{setClearing(false);}};
 return <section className="discovery-workspace">
  <form className="discovery-search" onSubmit={e=>{e.preventDefault();if(!working && query.trim())void resolve(query,'search');}}>
   <label htmlFor="discovery-query">Song name or link</label>
   <div><Search size={18} aria-hidden="true"/><input id="discovery-query" placeholder="Artist and title, or https://…" value={query} maxLength={4096} disabled={working} onChange={e=>setQuery(e.target.value)}/><button className="primary" disabled={working || !query.trim()} type="submit">Find</button></div>
   <p className="discovery-help">A name search finds the recording and its details. A YouTube link or a direct link to an audio file can be downloaded. Spotify and SoundCloud links are used as a reference only.</p>
  </form>
  {looking && <p className="discovery-message" role="status"><LoaderCircle size={14} className="spin" aria-hidden="true"/> {intent==='cover'?'Looking for cover art…':intent==='source'?'Checking the link…':'Searching…'}</p>}
  {error && <Notice onDismiss={()=>setError('')}>{error}</Notice>}
  {note && <Notice tone="status" onDismiss={()=>setNote('')}>{note}</Notice>}
  {references.length>0 && <section className="discovery-results" aria-label="Recordings found">
   <div className="discovery-results-head"><h2>Recordings found · {references.length}</h2><button type="button" disabled={working} onClick={startOver}>Clear results</button></div>
   <p className="discovery-help">These identify a recording so its title, artist and release are right. They are references, not downloads: choose one, then add a link to its audio.</p>
   <div className="discovery-references" role="group" aria-label="Recording references">{references.map(candidate=><button key={candidate.id} disabled={working} aria-pressed={reference?.id===candidate.id} onClick={()=>selectReference(candidate)}>
    <strong>{candidate.values.title}{candidate.version?` (${candidate.version})`:''}</strong><span>{candidate.values.artist || 'Artist unavailable'}</span><small>{[candidate.values.album,candidate.values.date,candidate.release_country,candidate.duration!=null?duration(candidate.duration):'',`${candidate.provider} reference`].filter(Boolean).join(' · ')}</small>
   </button>)}</div>
  </section>}
  {(reference || source) && <div className="discovery-review">
   <div className="discovery-review-heading"><h2>{source?'Review before downloading':'Add the audio for this recording'}</h2>{references.length===0 && <button type="button" disabled={working} onClick={startOver}>Start over</button>}</div>
   {reference && <div className="discovery-reference-source"><span><strong>{[reference.values.artist,reference.values.title].filter(Boolean).join(' – ')}</strong> · {reference.provider} reference, not a download</span>{(reference.creator || reference.values.album || reference.version) && <small>{[reference.creator && 'Uploaded by '+reference.creator,reference.values.album,reference.version].filter(Boolean).join(' · ')}</small>}
    {reference.guidance && <small>{reference.guidance}</small>}
    {reference.guidance && reference.source_url && <div className="service-reference-actions"><button disabled={working} onClick={()=>void action(()=>invoke<void>('open_reference',{url:reference.source_url}))}><ExternalLink size={14} aria-hidden="true"/> Open on {reference.provider}</button><button disabled={working} title="Add an audio file you already have on this Mac to the library" onClick={()=>void action(onImport)}><FolderOpen size={14} aria-hidden="true"/> Add a file from this Mac</button></div>}</div>}
   {reference && <form className="direct-url-form" onSubmit={e=>{e.preventDefault();if(!working && sourceUrl.trim())void resolve(sourceUrl,'source');}}><label htmlFor="direct-audio-url">Audio link</label><div><Link2 size={16} aria-hidden="true"/><input id="direct-audio-url" type="url" placeholder="A YouTube link, or a direct link to an audio file" maxLength={4096} disabled={working} value={sourceUrl} onChange={e=>{setSourceUrl(e.target.value);setSource(null);}}/><button disabled={working || !sourceUrl.trim()}>Check link</button></div></form>}
   {source && <p className="discovery-source-url" title={source.display_url}><Check size={13} aria-hidden="true"/> Audio found · {source.adapter==='youtube'?`${source.provider}, M4A as published`:'original file'}{source.declared_bytes!=null?` · ${formatBytes(source.declared_bytes)}`:''}{source.duration!=null?` · ${duration(source.duration)}`:''} · {source.display_url}</p>}
   {source && <p className="discovery-help">Check that this is the version you want. CrateLoom has not confirmed that it is a particular remix or edit{reference?.duration!=null && source.duration!=null && Math.abs(reference.duration-source.duration)>Math.max(5,reference.duration*.04)?`; its length (${duration(source.duration)}) differs from the chosen recording (${duration(reference.duration)})`:''}.</p>}
   <div className="discovery-review-body">
    {reference?.release_id && <div className="discovery-cover">{artwork?<img src={convertFileSrc(artwork)} alt="Selected release cover"/>:<div><Music2 size={26} aria-hidden="true"/></div>}<button disabled={working} onClick={()=>void action(async()=>{setIntent('cover');memory.generation++;setTask(await call<Task>('providers.cover',{reference_id:reference.id}));})}>{artwork?'Find cover again':'Find cover art'}</button></div>}
    <div className="discovery-fields"><label>Title<input aria-label="Download title" maxLength={500} disabled={working} value={values.title || ''} onChange={e=>setValues({...values,title:e.target.value})}/></label><label>Artist<input aria-label="Download artist" maxLength={500} disabled={working} value={values.artist || ''} onChange={e=>setValues({...values,artist:e.target.value})}/></label><label>Version<input aria-label="Download version" maxLength={200} disabled={working} placeholder="For example Extended Mix. Leave empty if unsure." value={version} onChange={e=>setVersion(e.target.value)}/></label></div>
   </div>
   <div className="discovery-review-actions"><small>{source?'The file is checked after download and added to your library with these details. The audio is not converted and its tags are not changed.':'Add an audio link above to download this recording.'}</small><button className="primary" disabled={working || !source || !values.title?.trim()} onClick={()=>void action(async()=>{if(!source)return;await onQueue({source_id:source.id,...(reference?{reference_id:reference.id}:{}),values,version,artwork_path:artwork});startOver();setQuery('');setNote('Added to Downloads below. The track is analyzed after it is added to your library.');})}><Download size={16} aria-hidden="true"/> Download and add to library</button></div>
  </div>}
  <div className="download-queue-head"><h2>Downloads</h2></div>
  {queueError && <Notice action={<button type="button" onClick={()=>void onRefresh().catch(()=>{})}>Try again</button>}>The download list could not be updated. {errorText(queueError)}</Notice>}
  {listError && <Notice onDismiss={()=>setListError('')}>{listError}</Notice>}
  {jobs.length===0?<p className="discovery-help">Nothing downloaded yet. Downloads appear here and in your library.</p>:<div className="download-queue">
   {group('In progress',<LoaderCircle size={13} className="spin" aria-hidden="true"/>,active)}
   {group('Did not finish',<AlertTriangle size={13} aria-hidden="true"/>,unfinished,<button type="button" disabled={clearing} title="Remove failed, cancelled and interrupted downloads from this list. Retrying one later shows it again." onClick={()=>void clearFailed()}>Clear failed</button>)}
   {group('Completed',<Check size={13} aria-hidden="true"/>,completed)}
  </div>}
  {capabilities && <Disclosure className="provider-capabilities" label="What can be downloaded"><dl>{capabilities.providers.map(provider=><div key={provider.id}><dt>{provider.name}</dt><dd>{provider.acquisition?`Audio can be downloaded${provider.formats.length?` (${provider.formats.join(', ')})`:''}`:provider.metadata || provider.search?'Reference only: identifies a track, no audio is downloaded':'Not available'}</dd></div>)}</dl><p>Downloads are saved in {capabilities.download_directory}</p><p>Files up to {limit} can be downloaded. Audio is kept as published; nothing is converted.</p></Disclosure>}
 </section>;
}
