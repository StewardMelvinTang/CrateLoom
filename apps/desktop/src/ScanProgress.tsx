import {useEffect, useState, type ReactNode} from 'react';
import {AlertTriangle, Clock, LoaderCircle, Pause} from 'lucide-react';
import './scan-progress.css';

/** resume_at (epoch seconds) is set while the scan waits to continue; stage then says what it waits for. */
export type ScanProgressData={stage:string;completed:number;total:number|null;unit:'steps'|'entries'|'bytes'|'seconds'|'samples';samples?:number;tracks_found?:number;position?:number;duration?:number;resume_at?:number|null};
/** running: work is happening now. waiting: it continues by itself later. paused and stalled: it needs the user. */
export type ScanState='running'|'waiting'|'paused'|'stalled';
const time=(seconds:number)=>{
  const minutes=Math.floor(seconds/60),tail=String(Math.floor(seconds%60)).padStart(2,'0');
  return minutes>=60?`${Math.floor(minutes/60)}:${String(minutes%60).padStart(2,'0')}:${tail}`:`${minutes}:${tail}`;
};
const bytes=(value:number)=>`${(value/1024/1024).toFixed(1)} MB`;

/** Seconds left until an epoch time, counted down once a second while there is one. */
function useCountdown(until:number|null|undefined) {
  // The state is only a once-a-second re-render; the time is read on every render, so a new
  // resume_at is never measured against the moment the component mounted.
  const [,setTick]=useState(0);
  useEffect(()=>{
    if(!until)return;
    const timer=window.setInterval(()=>setTick(tick=>tick+1),1000);
    return()=>window.clearInterval(timer);
  },[until]);
  return until?Math.max(0,Math.ceil(until-Date.now()/1000)):0;
}

export function ScanProgress({label,progress,state='running',cancelling=false,onCancel,cancelLabel='Cancel scan',children}:{label:string;progress?:ScanProgressData;state?:ScanState;cancelling?:boolean;onCancel?:()=>void;cancelLabel?:string;children?:ReactNode}) {
  // A waiting scan is not working until resume_at. The engine names the wait: Shazam's cooldown, or a pause before another attempt.
  const left=useCountdown(progress?.resume_at);
  const shown:ScanState=left>0 && state==='running'?'waiting':state;
  const busy=shown==='running' || cancelling;
  const measured=!!progress?.total && progress.total>0;
  const completed=Math.max(0,progress?.completed || 0);
  const detail=progress?.unit==='bytes'?`${bytes(completed)}${measured?` of ${bytes(progress.total!)}`:''} downloaded`:
    progress?.unit==='seconds'?`${time(completed)}${measured?` of ${time(progress.total!)}`:''} checked`:
    progress?.unit==='samples'?`${completed} audio samples checked`:
    progress?.unit==='entries'?`${completed}${measured?` of ${progress.total}`:''} tracklist entries read`:'';
  const stage=progress?.stage || (left>0?'Waiting to continue':'Preparing scan…');
  return <div className="scan-progress" data-state={cancelling?'cancelling':shown} aria-label={label} aria-busy={busy}>
    {busy?<LoaderCircle size={34} className="spin" aria-hidden="true"/>:shown==='waiting'?<Clock size={30} aria-hidden="true"/>:shown==='stalled'?<AlertTriangle size={30} aria-hidden="true"/>:<Pause size={30} aria-hidden="true"/>}
    <div className="scan-progress-heading" role="status" aria-live="polite"><h2>{cancelling?'Stopping scan':label}</h2><p>{cancelling?'Keeping the results already found…':stage}</p></div>
    {/* The countdown sits outside the live region so it is not announced every second. */}
    {left>0 && !cancelling && <p className="scan-progress-detail scan-progress-countdown time">Continues automatically in {time(left)}</p>}
    {/* A bar without a value animates, so it is only shown while work is really happening or a position is known. */}
    {(busy || measured) && <progress aria-label="Scan progress" {...(measured?{max:progress.total!,value:Math.min(completed,progress.total!)}:{})}/>}
    {detail && <p className="scan-progress-detail time">{detail}</p>}
    {progress?.unit==='samples' && progress.position!=null && <p className="scan-progress-detail time">Audio position {time(progress.position)}{progress.duration?` / ${time(progress.duration)}`:''}{progress.tracks_found!=null?` · ${progress.tracks_found} recordings found`:''}</p>}
    {children}
    {onCancel && <button type="button" className="scan-progress-action" disabled={cancelling} onClick={onCancel}>{cancelling?'Stopping…':cancelLabel}</button>}
  </div>;
}
