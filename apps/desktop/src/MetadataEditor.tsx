import React,{useEffect,useRef,useState} from 'react';
import {invoke,convertFileSrc} from '@tauri-apps/api/core';
import {open} from '@tauri-apps/plugin-dialog';
import {useDialogFocus} from './useDialogFocus';
import {usePanelMorph} from './PanelMotion';
import {X,Search,RefreshCw,ImagePlus,ChevronLeft,ChevronRight,Check,AlertTriangle} from 'lucide-react';
export type MetadataState={revision?:string;artwork_supported?:boolean;values:Record<string,string>;pending:Record<string,string>;artwork_path:string|null;artwork_pending:boolean;source:{provider:string;recording_id:string;release_id?:string}|null;automatic?:{rank:number;match_status:string;reviewed:boolean};automatic_review?:{state:string;reason?:string|null;query?:{title:string;artist:string;version:string};candidates:Candidate[];warnings:string[]}|null};
type Candidate={provider?:string;recording_id:string;release_id:string|null;values:Record<string,string>;version:string;duration:number|null;duration_difference:number|null;match_status:'strong'|'review'|'rejected';reasons:string[];release_kind?:string;release_country?:string;release_format?:string};
type Task={id:string;state:string;error:string|null;result:{candidates?:Candidate[];path?:string;cached?:boolean;partial?:boolean;warnings?:string[];providers?:{name:string;state:string}[];query?:{title:string;artist:string;version:string}}|null};
const call=<T,>(method:string,params:object={})=>invoke<T>('engine_call',{method,params});
const time=(seconds:number)=>`${Math.floor(seconds/60)}:${Math.floor(seconds%60).toString().padStart(2,'0')}`;
export function MetadataEditor({ids,onTrackOpen,onClose,onSaved}:{ids:string[];onTrackOpen?:(id:string)=>void;onClose:()=>void;onSaved:()=>Promise<void>}){
 const [index,setIndex]=useState(0),id=ids[index],generation=useRef(0);
 const [state,setState]=useState<MetadataState|null>(null),[busy,setBusy]=useState(false),[error,setError]=useState(''),[message,setMessage]=useState('');
 const [matches,setMatches]=useState<Candidate[]>([]),[task,setTask]=useState<Task|null>(null),[lookupQuery,setLookupQuery]=useState<{title:string;artist:string;version:string}|null>(null),[showExcluded,setShowExcluded]=useState(false);
 const [taskKind,setTaskKind]=useState<'search'|'cover'>('search');
 const actionLock=useRef(false);
 useEffect(()=>{if(id)onTrackOpen?.(id);},[id,onTrackOpen]);
 useEffect(()=>{const request=++generation.current;setState(null);setMatches([]);setTask(null);setError('');setMessage('');setShowExcluded(false);setLookupQuery(null);call<MetadataState>('metadata.get',{id}).then(s=>{if(request===generation.current){setState(s);setMatches(s.automatic_review?.candidates || []);setLookupQuery(s.automatic_review?.query || null);}}).catch(e=>{if(request===generation.current)setError(String(e));});return()=>{generation.current++;};},[id]);
 useEffect(()=>{
  if(!task || task.state!=='running')return;let stopped=false;
  const timer=window.setTimeout(()=>{call<Task>('metadata.job',{id:task.id}).then(next=>{if(stopped)return;setTask(next);if(next.error)setError(next.error);if(next.result?.candidates)setMatches(next.result.candidates);if(next.result?.query)setLookupQuery(next.result.query);if(next.result?.path)setState(s=>s?{...s,artwork_path:next.result!.path!,artwork_pending:true}:s);}).catch(e=>{if(!stopped){setTask(null);setError(String(e));}});},400);
  return()=>{stopped=true;clearTimeout(timer);};
 },[task,id]);
 const save=async()=>{if(!state)return;const result=await call<{metadata:MetadataState}>('metadata.save',{id,values:state.values,artwork_path:state.artwork_path,source:state.source});setState(result.metadata);await onSaved();};
 const action=async(fn:()=>Promise<void>)=>{if(actionLock.current)return;actionLock.current=true;setBusy(true);setError('');setMessage('');try{await fn();}catch(e){setError(String(e));}finally{actionLock.current=false;setBusy(false);}};
 const search=(refresh=false)=>action(async()=>{if(!state)return;setMatches([]);setLookupQuery(null);setShowExcluded(false);setTaskKind('search');const request=generation.current;const job=await call<Task>('metadata.search',{id,title:state.values.title,artist:state.values.artist,refresh});if(request===generation.current)setTask(job);});
 const selectImage=()=>action(async()=>{const path=await open({multiple:false,filters:[{name:'Artwork',extensions:['jpg','jpeg','png']}]});if(!path)return;const result=await call<{path:string}>('metadata.artwork',{path});setState(s=>s?{...s,artwork_path:result.path,artwork_pending:true}:s);});
 const searching=taskKind==='search' && task?.state==='running';
 const working=busy || taskKind==='cover' && task?.state==='running';
 const dialog=useDialogFocus(onClose,working);
 usePanelMorph(dialog);
 const navigate=(direction:number)=>{if(!state || working || index+direction<0 || index+direction>=ids.length)return;void action(async()=>{await save();setIndex(i=>i+direction);});};
 const excluded=matches.filter(c=>c.match_status==='rejected'),usable=matches.filter(c=>c.match_status!=='rejected');
 const choose=(c:Candidate)=>{
  if(!state || c.match_status==='rejected')return;
  const differentRelease=state.source && (state.source.provider!==(c.provider || 'MusicBrainz') || state.source.release_id!==c.release_id);
  setState({...state,values:{...state.values,...Object.fromEntries(Object.entries(c.values).filter(([key,v])=>v || state.source && key!=='genre'))},
   ...(differentRelease?{artwork_path:null,artwork_pending:true}:{}),
   source:{provider:c.provider || 'MusicBrainz',recording_id:c.recording_id,...(c.release_id?{release_id:c.release_id}:{})}});
  setMessage('Match selected. Review the fields before saving.');
 };
 return <div className="modal-backdrop">
  <section ref={dialog} tabIndex={-1} className="metadata-window" role="dialog" aria-modal="true" aria-label="Review metadata">
   <div className="panel-surface" aria-hidden="true"/>
   <div className="section-window-head metadata-review-head"><h2>Metadata</h2><div className="metadata-navigation" aria-label="Review navigation">
    <button disabled={working || !state || index===0} aria-label="Save and previous track" title="Save and previous track" onClick={()=>navigate(-1)}><ChevronLeft size={18}/></button>
    <span aria-live="polite">{index+1} / {ids.length}</span>
    <button disabled={working || !state || index+1===ids.length} aria-label="Save and next track" title="Save and next track" onClick={()=>navigate(1)}><ChevronRight size={18}/></button>
   </div><button disabled={working} aria-label="Close metadata editor" onClick={onClose}><X size={17}/></button></div>
   {error && <p className="metadata-error" role="alert">{error}</p>}
   {message && <p role="status">{message}</p>}
   {!state?<div className="metadata-loading" role="status">Loading metadata…</div>:<div className="metadata-track-content" key={id}>
    {state.automatic && <p className="metadata-auto-note" role="status">{state.automatic.reviewed?'Reviewed automatic match':'Applied automatically'} · Match score {state.automatic.rank.toFixed(2)} / 100 · Review the fields and album cover.</p>}
    {state.automatic_review?.reason && <p className="metadata-auto-note" role="status">{state.automatic_review.reason}</p>}
    <div className="metadata-body">
     <div className="metadata-artwork">
      {state.artwork_path?<img src={convertFileSrc(state.artwork_path)} alt="Selected artwork"/>:<div className="artwork-empty"><ImagePlus size={32}/></div>}
      <button disabled={working || state.artwork_supported===false} onClick={()=>void selectImage()}>Choose image</button>
      {state.artwork_path && <button disabled={working || state.artwork_supported===false} onClick={()=>setState({...state,artwork_path:null,artwork_pending:true})}>Remove image</button>}
      {state.artwork_supported===false && <small>Embedded cover writing is unavailable for OGG.</small>}
      {state.source?.release_id && <button disabled={working || state.artwork_supported===false} onClick={()=>void action(async()=>{setTaskKind('cover');setTask(await call<Task>('metadata.cover',{id,...state.source}));})}>Find release cover</button>}
     </div>
     <div className="metadata-fields">{['title','artist','album','genre','date'].map(key=><label key={key}>
      {key==='date'?'Release date':key[0].toUpperCase()+key.slice(1)}
      <input aria-label={`Metadata ${key}`} maxLength={500} value={state.values[key] || ''} disabled={working} onChange={e=>{
       setState({...state,values:{...state.values,[key]:e.target.value},...(['title','artist'].includes(key)?{source:null}:{})});
       if(['title','artist'].includes(key)){setMatches([]);setLookupQuery(null);setTask(null);setMessage('');}
      }}/>
     </label>)}</div>
    </div>
    <div className="metadata-lookup">
     <button disabled={working || searching} onClick={()=>void search()}><Search size={15}/> Find matches</button>
     <button disabled={working || searching} aria-label="Refresh metadata matches" title="Refresh provider results" onClick={()=>void search(true)}><RefreshCw size={15}/></button>
     <small>Title, artist, version &amp; duration · Apple Music + MusicBrainz</small>
    </div>
    {task?.state==='running' && <p role="status">{taskKind==='cover'?'Finding release cover…':matches.length?'Matches ready to review. Checking remaining catalog…':'Searching Apple Music and MusicBrainz…'}</p>}
    {lookupQuery && <p className="metadata-query">{[lookupQuery.artist,lookupQuery.title,lookupQuery.version].filter(Boolean).join(' · ')}</p>}
    {task?.result?.warnings?.map(warning=><p key={warning} className="metadata-source" role="status">{warning}</p>)}
    {task?.state==='completed' && task.result?.candidates && usable.length===0 && <p className="metadata-empty" role="status">No reliable match. Check the title, artist and remix name, or edit the fields manually.</p>}
    {usable.length>0 && <p className="metadata-source">Text matches · Check the version before applying</p>}
    {state.source && <p className="metadata-source">Selected source: {state.source.provider}</p>}
    <div className="metadata-matches" aria-label="Metadata matches">
     {(showExcluded?matches:usable).map((c,i)=>{
      const rejected=c.match_status==='rejected',selected=state.source?.provider===(c.provider || 'MusicBrainz') && state.source?.recording_id===c.recording_id && state.source?.release_id===(c.release_id || undefined);
      return <button key={`${c.provider}-${c.recording_id}-${c.release_id}-${i}`} className="metadata-match" disabled={working || rejected} aria-pressed={selected} onClick={()=>choose(c)}>
       <span className="metadata-match-heading"><strong>{c.values.title}</strong><span className={`metadata-match-status ${rejected?'is-rejected':''}`}>
        {rejected?<AlertTriangle size={12}/>:selected?<Check size={12}/>:null}{rejected?'Excluded':selected?'Selected':c.match_status==='strong'?'Strong text match':'Check version'}
       </span></span>
       <span>{c.values.artist || 'Artist unavailable'}</span>
       <span>{[c.values.album || 'Release unavailable',c.values.date,c.release_kind,c.release_country,c.release_format,c.provider || 'MusicBrainz'].filter(Boolean).join(' · ')}</span>
       <span>{c.duration!=null?time(c.duration):'Duration unavailable'}{c.duration_difference!=null?` · ${c.duration_difference.toFixed(2)}s difference`:''}{rejected?` · ${c.reasons.join(' · ')}`:''}</span>
      </button>;
     })}
    </div>
    {excluded.length>0 && <button className="metadata-excluded-toggle disclosure-toggle" aria-expanded={showExcluded} onClick={()=>setShowExcluded(!showExcluded)}><ChevronRight size={14}/>{showExcluded?'Hide':`Show ${excluded.length}`} excluded results</button>}
    <div className="metadata-actions">
     <button disabled={working} title="Discard library metadata changes and read the file tags" onClick={()=>void action(async()=>{setState(await call<MetadataState>('metadata.reload',{id}));setMatches([]);setLookupQuery(null);setTask(null);await onSaved();setMessage('Reloaded file tags.');})}>Reload file tags</button>
     <button disabled={working} onClick={()=>void action(async()=>{await save();setMessage('Saved to library.');})}>Save to library</button>
     <div className="push"/>
     <button className="primary" disabled={working} onClick={()=>void action(async()=>{await save();const result=await call<{written:boolean;backup:string|null}>('metadata.write',{id});setState(await call<MetadataState>('metadata.get',{id}));await onSaved();setMessage(result.written?'Tags written. A verified backup was saved.':'No pending tag changes.');})}>Write tags to file</button>
    </div>
    <p className="metadata-write-note">Writing updates the original file after audio verification and keeps a backup.</p>
   </div>}
  </section>
 </div>;
}
