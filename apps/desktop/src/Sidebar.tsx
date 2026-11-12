import {useEffect, useRef, useState, type ReactNode} from 'react';

export function Sidebar({visible, collapsed, onCollapsedChange:setCollapsed, children}: {visible:boolean; collapsed:boolean; onCollapsedChange:(value:boolean)=>void; children:ReactNode}) {
  const [width,setWidth] = useState(() => Math.min(340,Math.max(180,Number(localStorage.getItem('sidebarWidth')) || 216)));
  const [resizing,setResizing] = useState(false);
  const drag = useRef<{pointer:number;x:number;width:number}|null>(null);
  useEffect(() => {localStorage.setItem('sidebarWidth',String(width));localStorage.setItem('sidebarCollapsed',String(collapsed));},[width,collapsed]);
  return <aside id="library-sidebar" hidden={!visible} className={`sidebar ${collapsed?'sidebar-collapsed':''} ${resizing?'sidebar-resizing':''}`} style={{width:collapsed?64:width}} aria-label="Library navigation">
    <div className="sidebar-content">{children}</div>
    <div className="sidebar-resizer" role="separator" tabIndex={0} aria-label="Resize sidebar" aria-orientation="vertical" aria-valuemin={64} aria-valuemax={340} aria-valuenow={collapsed?64:width}
      onDoubleClick={() => {setCollapsed(false);setWidth(216);}}
      onPointerDown={event => {if(event.button!==0)return;event.preventDefault();event.currentTarget.setPointerCapture(event.pointerId);drag.current={pointer:event.pointerId,x:event.clientX,width:collapsed?64:width};setResizing(true);}}
      onPointerMove={event => {const start=drag.current;if(!start||start.pointer!==event.pointerId)return;const next=start.width+event.clientX-start.x;setCollapsed(next<128);if(next>=128)setWidth(Math.min(340,Math.max(180,next)));}}
      onPointerUp={() => {drag.current=null;setResizing(false);}} onPointerCancel={() => {drag.current=null;setResizing(false);}}
      onKeyDown={event => {if(event.key==='ArrowLeft'||event.key==='ArrowRight'){event.preventDefault();if(collapsed&&event.key==='ArrowRight')setCollapsed(false);else if(width<=180&&event.key==='ArrowLeft')setCollapsed(true);else setWidth(value=>Math.min(340,Math.max(180,value+(event.key==='ArrowRight'?16:-16))));}if(event.key==='Home'){event.preventDefault();setCollapsed(true);}if(event.key==='End'){event.preventDefault();setCollapsed(false);setWidth(340);}}}/>
  </aside>;
}
