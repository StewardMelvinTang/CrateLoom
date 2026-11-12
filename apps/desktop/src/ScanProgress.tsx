import type {ReactNode} from 'react';
import {LoaderCircle, Pause} from 'lucide-react';

export type ScanProgressData={stage:string;completed:number;total:number|null;unit:'steps'|'entries'|'bytes'|'seconds'|'samples';samples?:number;tracks_found?:number;position?:number;duration?:number};
const time=(seconds:number)=>{
  const minutes=Math.floor(seconds/60),tail=String(Math.floor(seconds%60)).padStart(2,'0');
  return minutes>=60?`${Math.floor(minutes/60)}:${String(minutes%60).padStart(2,'0')}:${tail}`:`${minutes}:${tail}`;
};
const bytes=(value:number)=>`${(value/1024/1024).toFixed(1)} MB`;

export function ScanProgress({label,progress,cancelling=false,active=true,onCancel,children}:{label:string;progress?:ScanProgressData;cancelling?:boolean;active?:boolean;onCancel?:()=>void;children?:ReactNode}) {
  const measured=!!progress?.total && progress.total>0;
  const completed=Math.max(0,progress?.completed || 0);
  const detail=progress?.unit==='bytes'?`${bytes(completed)}${measured?` of ${bytes(progress.total!)}`:''} downloaded`:
    progress?.unit==='seconds'?`${time(completed)}${measured?` of ${time(progress.total!)}`:''} checked`:
    progress?.unit==='samples'?`${completed} audio samples checked`:
    progress?.unit==='entries'?`${completed}${measured?` of ${progress.total}`:''} tracklist entries read`:'';
  return <div className="scan-progress" aria-label={label} aria-busy={active || cancelling}>
    {active || cancelling?<LoaderCircle size={34} className="spin" aria-hidden="true"/>:<Pause size={30} aria-hidden="true"/>}
    <div className="scan-progress-heading" role="status" aria-live="polite"><h2>{cancelling?'Stopping scan':label}</h2><p>{cancelling?'Keeping the results already found…':progress?.stage || 'Preparing scan…'}</p></div>
    <progress aria-label="Scan progress" {...(measured?{max:progress.total!,value:Math.min(completed,progress.total!)}:{})}/>
    {detail && <p className="scan-progress-detail time">{detail}</p>}
    {progress?.unit==='samples' && progress.position!=null && <p className="scan-progress-detail time">Audio position {time(progress.position)}{progress.duration?` / ${time(progress.duration)}`:''}{progress.tracks_found!=null?` · ${progress.tracks_found} recordings found`:''}</p>}
    {children}
    {onCancel && <button disabled={cancelling} onClick={onCancel}>{cancelling?'Stopping…':'Cancel scan'}</button>}
  </div>;
}
