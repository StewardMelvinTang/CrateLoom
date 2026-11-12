import {useEffect, useRef, useState, type ReactNode} from 'react';
import {convertFileSrc} from '@tauri-apps/api/core';
import {open} from '@tauri-apps/plugin-dialog';
import {call, message as errorText} from './engine';
import {useDialogFocus} from './useDialogFocus';
import {usePanelMorph} from './PanelMotion';
import {X, Search, RefreshCw, ImagePlus, ChevronLeft, ChevronRight, Check, AlertTriangle, Info} from 'lucide-react';
import './metadata.css';

export type MetadataState={revision?:string;artwork_supported?:boolean;values:Record<string,string>;pending:Record<string,string>;artwork_path:string|null;artwork_pending:boolean;source:{provider:string;recording_id:string;release_id?:string}|null;automatic?:{rank:number;match_status:string;reviewed:boolean};automatic_review?:{state:string;reason?:string|null;query?:{title:string;artist:string;version:string};candidates:Candidate[];warnings:string[]}|null};
type Candidate={provider?:string;recording_id:string;release_id:string|null;values:Record<string,string>;version:string;duration:number|null;duration_difference:number|null;match_status:'strong'|'review'|'rejected';reasons:string[];release_kind?:string;release_country?:string;release_format?:string};
type Task={id:string;state:string;error:string|null;result:{candidates?:Candidate[];path?:string;cached?:boolean;partial?:boolean;warnings?:string[];providers?:{name:string;state:string}[];query?:{title:string;artist:string;version:string}}|null};
const FIELDS=['title','artist','album','genre','date'];
const FIELD_LABELS:Record<string,string>={title:'Title',artist:'Artist',album:'Album',genre:'Genre',date:'Release date'};
const DATE_FORMAT=/^\d{4}(?:-\d{2}(?:-\d{2})?)?$/;
const DATE_HINT='Use YYYY, YYYY-MM or YYYY-MM-DD.';
const time=(seconds:number)=>`${Math.floor(seconds/60)}:${Math.floor(seconds%60).toString().padStart(2,'0')}`;
const snapshot=(state:MetadataState)=>JSON.stringify([FIELDS.map(key=>state.values[key] || ''),state.artwork_path,state.source]);
// Plain words for the engine's match grades.
const matchLabel=(status:Candidate['match_status'])=>status==='strong'?'Close match':status==='review'?'Possible match, check the version':'Not a match';

/** A save error that is already shown beside the field it concerns. */
class FieldError extends Error {}

function Notice({children,tone='error',onDismiss,action}:{children:ReactNode;tone?:'error'|'status';onDismiss?:()=>void;action?:ReactNode}) {
  return <div className="metadata-notice" data-tone={tone} role={tone==='error'?'alert':'status'}>
    {tone==='error'?<AlertTriangle size={15} aria-hidden="true"/>:<Info size={15} aria-hidden="true"/>}
    <span>{children}</span>
    {action}
    {onDismiss && <button type="button" className="metadata-notice-dismiss" aria-label="Dismiss message" title="Dismiss" onClick={onDismiss}><X size={14}/></button>}
  </div>;
}

/** `onTrackOpen` is optional and currently not passed by the app shell; it reports which track is on screen. */
export function MetadataEditor({ids,onTrackOpen,onClose,onSaved}:{ids:string[];onTrackOpen?:(id:string)=>void;onClose:()=>void;onSaved:()=>Promise<void>}){
 const [index,setIndex]=useState(0),id=ids[index],generation=useRef(0);
 const [state,setState]=useState<MetadataState|null>(null),[busy,setBusy]=useState(false),[error,setError]=useState(''),[message,setMessage]=useState('');
 const [loadFailed,setLoadFailed]=useState(false),[reloadKey,setReloadKey]=useState(0);
 const [matches,setMatches]=useState<Candidate[]>([]),[task,setTask]=useState<Task|null>(null),[lookupQuery,setLookupQuery]=useState<{title:string;artist:string;version:string}|null>(null),[showExcluded,setShowExcluded]=useState(false);
 const [taskKind,setTaskKind]=useState<'search'|'cover'>('search');
 const [dateError,setDateError]=useState(''),[confirm,setConfirm]=useState<null|'close'|'reload'>(null);
 // What the library held when this track was loaded or last saved; anything else is an unsaved edit.
 const baseline=useRef(''),savedDate=useRef('');
 const actionLock=useRef(false),mounted=useRef(true);
 useEffect(()=>{mounted.current=true;return()=>{mounted.current=false;};},[]);
 useEffect(()=>{if(id)onTrackOpen?.(id);},[id,onTrackOpen]);
 const accept=(next:MetadataState)=>{baseline.current=snapshot(next);savedDate.current=next.values.date || '';setState(next);};
 useEffect(()=>{
  const request=++generation.current;
  setState(null);setLoadFailed(false);setMatches([]);setTask(null);setError('');setMessage('');setShowExcluded(false);setLookupQuery(null);setDateError('');setConfirm(null);
  call<MetadataState>('metadata.get',{id}).then(next=>{if(request===generation.current){accept(next);setMatches(next.automatic_review?.candidates || []);setLookupQuery(next.automatic_review?.query || null);}})
   .catch(e=>{if(request===generation.current){setLoadFailed(true);setError(errorText(e));}});
  return()=>{generation.current++;};
 },[id,reloadKey]);
 // Lookup and cover jobs are polled one request at a time and dropped when the track changes or the editor closes.
 useEffect(()=>{
  if(!task || task.state!=='running')return;let stopped=false;
  const timer=window.setTimeout(()=>{call<Task>('metadata.job',{id:task.id}).then(next=>{if(stopped)return;setTask(next);if(next.error)setError(next.error);if(next.result?.candidates)setMatches(next.result.candidates);if(next.result?.query)setLookupQuery(next.result.query);if(next.result?.path)setState(s=>s?{...s,artwork_path:next.result!.path!,artwork_pending:true}:s);}).catch(e=>{if(!stopped){setTask(null);setError(errorText(e));}});},400);
  return()=>{stopped=true;clearTimeout(timer);};
 },[task,id]);
 // The fields are disabled while a save runs, so move to the date field once they are usable again.
 useEffect(()=>{if(dateError && !busy)document.getElementById('metadata-date')?.focus();},[dateError,busy]);
 const dirty=!!state && snapshot(state)!==baseline.current;
 const save=async()=>{
  if(!state)return;
  try{const result=await call<{metadata:MetadataState}>('metadata.save',{id,values:state.values,artwork_path:state.artwork_path,source:state.source});accept(result.metadata);setDateError('');}
  catch(e){
   // The engine only refuses a date it cannot write; show that beside the field it is about.
   const text=errorText(e);if(/\bdate\b/i.test(text)){setDateError(text);throw new FieldError(text);}
   throw e;
  }
  await onSaved();
 };
 const action=async(fn:()=>Promise<void>)=>{if(actionLock.current)return;actionLock.current=true;setBusy(true);setError('');setMessage('');setConfirm(null);try{await fn();}catch(e){if(mounted.current && !(e instanceof FieldError))setError(errorText(e));}finally{actionLock.current=false;if(mounted.current)setBusy(false);}};
 const search=(refresh=false)=>action(async()=>{if(!state)return;setMatches([]);setLookupQuery(null);setShowExcluded(false);setTaskKind('search');const request=generation.current;const job=await call<Task>('metadata.search',{id,title:state.values.title,artist:state.values.artist,refresh});if(request===generation.current)setTask(job);});
 const selectImage=()=>action(async()=>{const path=await open({multiple:false,filters:[{name:'Artwork',extensions:['jpg','jpeg','png']}]});if(!path)return;const result=await call<{path:string}>('metadata.artwork',{path});setState(s=>s?{...s,artwork_path:result.path,artwork_pending:true}:s);});
 const searching=taskKind==='search' && task?.state==='running';
 const working=busy || taskKind==='cover' && task?.state==='running';
 const requestClose=()=>{if(dirty)setConfirm('close');else onClose();};
 const dialog=useDialogFocus(requestClose,working);
 usePanelMorph(dialog);
 // Previous and next save first. A failed save keeps this track and its edits on screen.
 const navigate=(direction:number)=>{
  if(working || index+direction<0 || index+direction>=ids.length)return;
  if(!state){if(loadFailed)setIndex(i=>i+direction);return;}
  // Saving an unedited track is deliberate: it marks an automatic match as reviewed.
  void action(async()=>{await save();setIndex(i=>i+direction);});
 };
 const excluded=matches.filter(c=>c.match_status==='rejected'),usable=matches.filter(c=>c.match_status!=='rejected');
 const choose=(c:Candidate)=>{
  if(!state || c.match_status==='rejected')return;
  const differentRelease=state.source && (state.source.provider!==(c.provider || 'MusicBrainz') || state.source.release_id!==c.release_id);
  setState({...state,values:{...state.values,...Object.fromEntries(Object.entries(c.values).filter(([key,v])=>v || state.source && key!=='genre'))},
   ...(differentRelease?{artwork_path:null,artwork_pending:true}:{}),
   source:{provider:c.provider || 'MusicBrainz',recording_id:c.recording_id,...(c.release_id?{release_id:c.release_id}:{})}});
  setMessage('Match applied to the fields below. Check them, then save.');
 };
 const staged=state?[...Object.keys(state.pending).map(key=>(FIELD_LABELS[key] || key).toLowerCase()),...(state.artwork_pending?['cover']:[])]:[];
 const date=state?.values.date || '';
 const dateInvalid=!!date.trim() && date!==savedDate.current && !DATE_FORMAT.test(date.trim());
 return <div className="modal-backdrop">
  <section ref={dialog} tabIndex={-1} className="metadata-window" role="dialog" aria-modal="true" aria-label="Review metadata">
   <div className="panel-surface" aria-hidden="true"/>
   <div className="section-window-head metadata-review-head"><h2>Metadata</h2><div className="metadata-navigation" role="group" aria-label="Review navigation">
    <button disabled={working || (!state && !loadFailed) || index===0} aria-label="Save and previous track" title="Save and go to the previous track" onClick={()=>navigate(-1)}><ChevronLeft size={18}/></button>
    <span aria-live="polite">{index+1} / {ids.length}</span>
    <button disabled={working || (!state && !loadFailed) || index+1===ids.length} aria-label="Save and next track" title="Save and go to the next track" onClick={()=>navigate(1)}><ChevronRight size={18}/></button>
   </div><button disabled={working} aria-label="Close metadata editor" title={dirty?'Close. You will be asked about unsaved edits.':'Close'} onClick={requestClose}><X size={17}/></button></div>
   {confirm==='close' && <Notice tone="status" action={<><button type="button" disabled={working} onClick={()=>void action(async()=>{await save();onClose();})}>Save and close</button><button type="button" disabled={working} onClick={onClose}>Discard edits</button></>} onDismiss={()=>setConfirm(null)}>This track has unsaved edits.</Notice>}
   {error && <Notice onDismiss={()=>setError('')} action={loadFailed?<button type="button" onClick={()=>setReloadKey(key=>key+1)}>Try again</button>:undefined}>{error}</Notice>}
   {message && <Notice tone="status" onDismiss={()=>setMessage('')}>{message}</Notice>}
   {!state?<div className="metadata-loading" role="status">{loadFailed?'This track’s metadata could not be loaded.':'Loading metadata…'}</div>:<div className="metadata-track-content" key={id}>
    {state.automatic && <p className="metadata-auto-note" role="status">{state.automatic.reviewed?'Automatic match, reviewed':'Applied automatically'} · match score {Math.round(state.automatic.rank)} of 100{state.automatic.reviewed?'':'. Check the fields and the cover.'}</p>}
    {state.automatic_review?.reason && <p className="metadata-auto-note" role="status"><strong>Auto metadata left this track unchanged. </strong><span>{state.automatic_review.reason}</span></p>}
    <div className="metadata-body">
     <div className="metadata-artwork">
      {state.artwork_path?<img src={convertFileSrc(state.artwork_path)} alt="Selected artwork"/>:<div className="artwork-empty"><ImagePlus size={32} aria-hidden="true"/></div>}
      <button disabled={working} onClick={()=>void selectImage()}>Choose image</button>
      {state.artwork_path && <button disabled={working} onClick={()=>setState({...state,artwork_path:null,artwork_pending:true})}>Remove cover</button>}
      {state.source?.release_id && <button disabled={working} title="Look for the cover of the selected release" onClick={()=>void action(async()=>{setTaskKind('cover');const request=generation.current;const job=await call<Task>('metadata.cover',{id,...state.source});if(request===generation.current)setTask(job);})}>Find release cover</button>}
      {state.artwork_supported===false && <small>This file format cannot hold a cover. The cover is kept in the CrateLoom library only.</small>}
     </div>
     <div className="metadata-fields">{FIELDS.map(key=><label key={key}>
      {FIELD_LABELS[key]}
      <input id={`metadata-${key}`} aria-label={`Metadata ${key}`} maxLength={500} value={state.values[key] || ''} disabled={working}
       {...(key==='date'?{placeholder:'YYYY, YYYY-MM or YYYY-MM-DD','aria-invalid':dateInvalid || !!dateError,'aria-describedby':'metadata-date-help'}:{})} onChange={e=>{
       setState({...state,values:{...state.values,[key]:e.target.value},...(['title','artist'].includes(key)?{source:null}:{})});
       if(key==='date')setDateError('');
       if(['title','artist'].includes(key)){setMatches([]);setLookupQuery(null);setTask(null);setMessage('');}
      }}/>
      {key==='date' && (dateError || dateInvalid) && <small id="metadata-date-help" className="metadata-field-error" role={dateError?'alert':undefined}><AlertTriangle size={12} aria-hidden="true"/> {dateError?`${dateError.replace(/\.$/,'')}.`:DATE_HINT}</small>}
     </label>)}</div>
    </div>
    <div className="metadata-lookup">
     <button disabled={working || searching} onClick={()=>void search()}><Search size={15} aria-hidden="true"/> Find matches</button>
     {(matches.length>0 || task?.state==='completed') && <button disabled={working || searching} aria-label="Search again, ignoring saved results" title="Search again, ignoring saved results" onClick={()=>void search(true)}><RefreshCw size={15}/></button>}
     <small>Searches Apple Music and MusicBrainz by title and artist, then compares version and length.</small>
    </div>
    {task?.state==='running' && <p role="status">{taskKind==='cover'?'Finding release cover…':matches.length?'First matches are ready. Still checking the remaining catalog…':'Searching Apple Music and MusicBrainz…'}</p>}
    {lookupQuery && <p className="metadata-query">Searched for: {[lookupQuery.artist,lookupQuery.title,lookupQuery.version].filter(Boolean).join(' · ')}</p>}
    {task?.result?.warnings?.map(warning=><p key={warning} className="metadata-source" role="status">{warning}</p>)}
    {task?.state==='completed' && task.result?.candidates && usable.length===0 && <p className="metadata-empty" role="status">No match found. Check the title, artist and remix name, or fill in the fields yourself.</p>}
    {usable.length>0 && <p className="metadata-source">Matched by title and artist. Check the version and length before choosing one.</p>}
    {state.source && <p className="metadata-source">Details taken from: {state.source.provider}</p>}
    <div className="metadata-matches" role="group" aria-label="Metadata matches">
     {(showExcluded?matches:usable).map((c,i)=>{
      const rejected=c.match_status==='rejected',selected=state.source?.provider===(c.provider || 'MusicBrainz') && state.source?.recording_id===c.recording_id && state.source?.release_id===(c.release_id || undefined);
      return <button key={`${c.provider}-${c.recording_id}-${c.release_id}-${i}`} className="metadata-match" disabled={working || rejected} aria-pressed={selected} onClick={()=>choose(c)}>
       <span className="metadata-match-heading"><strong>{c.values.title}</strong><span className={`metadata-match-status ${rejected?'is-rejected':''}`}>
        {rejected?<AlertTriangle size={12} aria-hidden="true"/>:selected?<Check size={12} aria-hidden="true"/>:null}{selected?'Selected':matchLabel(c.match_status)}
       </span></span>
       <span>{c.values.artist || 'Artist unavailable'}</span>
       <span>{[c.values.album || 'Release unavailable',c.values.date,c.release_kind,c.release_country,c.release_format,c.provider || 'MusicBrainz'].filter(Boolean).join(' · ')}</span>
       <span>{c.duration!=null?time(c.duration):'Length unavailable'}{c.duration_difference!=null?c.duration_difference<1?' · same length as your file':` · ${c.duration_difference.toFixed(1)} s different from your file`:''}{rejected && c.reasons.length?` · ${c.reasons.join(' · ')}`:''}</span>
      </button>;
     })}
    </div>
    {excluded.length>0 && <button className="metadata-excluded-toggle disclosure-toggle" aria-expanded={showExcluded} onClick={()=>setShowExcluded(!showExcluded)}><ChevronRight size={14} aria-hidden="true"/>{showExcluded?'Hide':'Show'} {excluded.length} {excluded.length===1?'result that does':'results that do'} not match</button>}
    <p className="metadata-staged" role="status">{dirty?'Unsaved edits on this track.':staged.length?`Saved in the library, not yet written to the file: ${staged.join(', ')}.`:'The library and the file’s tags match.'}</p>
    <p className="metadata-write-note">Save to library keeps your changes in CrateLoom only. Write tags to file also puts them into the audio file: the audio is verified unchanged and a backup is kept.</p>
    {confirm==='reload' && <Notice tone="status" action={<><button type="button" disabled={working} onClick={()=>void action(async()=>{accept(await call<MetadataState>('metadata.reload',{id}));setMatches([]);setLookupQuery(null);setTask(null);setDateError('');await onSaved();setMessage('Reloaded from the file. Library changes for this track were discarded.');})}>Discard and reload</button><button type="button" onClick={()=>setConfirm(null)}>Keep changes</button></>}>Reloading replaces this track’s library details and cover with the tags in the file.</Notice>}
    <div className="metadata-actions">
     <button disabled={working} title="Discard this track’s library changes and read the tags from the file again" onClick={()=>setConfirm('reload')}>Reload file tags</button>
     <button disabled={working} title="Keep these details in CrateLoom. The audio file is not changed." onClick={()=>void action(async()=>{await save();setMessage('Saved to the library. The file is unchanged until you write tags.');})}>Save to library</button>
     <div className="push"/>
     <button className="primary" disabled={working} title="Save, then write these details into the audio file’s tags" onClick={()=>void action(async()=>{await save();const result=await call<{written:boolean;backup:string|null;artwork_embedded?:boolean;note?:string}>('metadata.write',{id});accept(await call<MetadataState>('metadata.get',{id}));await onSaved();setMessage([result.written?'Tags written to the file. A backup of the original was kept.':'Nothing to write: the file already has these tags.',result.note].filter(Boolean).join(' '));})}>Write tags to file</button>
    </div>
   </div>}
  </section>
 </div>;
}
