import {useEffect, useRef, useState, type PointerEvent as ReactPointerEvent} from 'react';

// Pointer capture works in WKWebView without starting a native file drag.
export function useTrackReorder(group:string, move:(id:string,target:string)=>void, disabled:boolean) {
  const [target,setTarget]=useState<string|null>(null);
  const drag=useRef<{id:string;pointer:number;x:number;y:number;startX:number;startY:number;active:boolean;target:string|null;scroll:HTMLElement|null}|null>(null);
  const callback=useRef(move); callback.current=move;
  const frame=useRef(0);
  const cancel=()=>{cancelAnimationFrame(frame.current);drag.current=null;setTarget(null);};
  useEffect(()=>()=>{cancelAnimationFrame(frame.current);drag.current=null;},[]);
  const tick=()=>{
      const current=drag.current;
      if(!current)return;
      if(current?.active){
        const row=document.elementFromPoint(current.x,current.y)?.closest<HTMLElement>('[data-reorder-id]');
        const next=row?.dataset.reorderGroup===group?row.dataset.reorderId || null:null;
        if(current.target!==next){current.target=next;setTarget(next===current.id?null:next);}
        const scroller=current.scroll;
        if(scroller){const bounds=scroller.getBoundingClientRect();const delta=current.y<bounds.top+36?-12:current.y>bounds.bottom-36?12:0;if(delta)scroller.scrollTop+=delta;}
      }
      frame.current=requestAnimationFrame(tick);
    };
  const handle=(id:string)=>({
    onPointerDown:(event:ReactPointerEvent<HTMLButtonElement>)=>{
      if(disabled || event.button!==0)return;
      event.preventDefault();event.currentTarget.focus();event.currentTarget.setPointerCapture(event.pointerId);
      let scroll:HTMLElement|null=event.currentTarget.parentElement;
      while(scroll && !(scroll.scrollHeight>scroll.clientHeight && /auto|scroll/.test(getComputedStyle(scroll).overflowY)))scroll=scroll.parentElement;
      drag.current={id,pointer:event.pointerId,x:event.clientX,y:event.clientY,startX:event.clientX,startY:event.clientY,active:false,target:null,scroll};
      frame.current=requestAnimationFrame(tick);
    },
    onPointerMove:(event:ReactPointerEvent<HTMLButtonElement>)=>{
      const current=drag.current;if(!current || current.pointer!==event.pointerId)return;
      current.x=event.clientX;current.y=event.clientY;
      if(Math.hypot(current.x-current.startX,current.y-current.startY)>4)current.active=true;
    },
    onPointerUp:(event:ReactPointerEvent<HTMLButtonElement>)=>{
      const current=drag.current;if(!current || current.pointer!==event.pointerId)return;
      const row=document.elementFromPoint(event.clientX,event.clientY)?.closest<HTMLElement>('[data-reorder-id]');
      const next=row?.dataset.reorderGroup===group?row.dataset.reorderId:null;
      cancel();if(current.active && next && next!==id)callback.current(id,next);
    },
    onPointerCancel:cancel,
    onLostPointerCapture:cancel,
  });
  return {target,handle};
}
