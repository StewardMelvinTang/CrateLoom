import {PanelLeftClose, PanelLeftOpen} from 'lucide-react';
import mark from './assets/brand/crateloom-mark-small-black.svg';

export function Titlebar({expanded,onToggle}: {expanded:boolean;onToggle:()=>void}) {
  return <div className="titlebar" data-tauri-drag-region>
    <button className="titlebar-sidebar icon" title={expanded?'Collapse sidebar':'Expand sidebar'} aria-label={expanded?'Collapse sidebar':'Expand sidebar'} aria-controls="library-sidebar" aria-expanded={expanded} onClick={onToggle}>{expanded?<PanelLeftClose size={17}/>:<PanelLeftOpen size={17}/>}</button>
    <div className="titlebar-name" data-tauri-drag-region><img src={mark} alt="" data-tauri-drag-region/><span data-tauri-drag-region>CrateLoom</span></div>
  </div>;
}
