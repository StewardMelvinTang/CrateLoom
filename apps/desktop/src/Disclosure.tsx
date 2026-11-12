import {useEffect, useId, useRef, useState, type ReactNode, type KeyboardEventHandler} from 'react';
import {ChevronDown,ChevronRight} from 'lucide-react';

export function Disclosure({label, children, className = '', initialOpen = false, onKeyDown, popover=false}: {label: ReactNode; children: ReactNode; className?: string; initialOpen?: boolean; onKeyDown?:KeyboardEventHandler<HTMLDivElement>; popover?:boolean}) {
  const [expanded, setExpanded] = useState(initialOpen);
  const host=useRef<HTMLDivElement>(null);
  const id = useId();
  useEffect(()=>{
    if(!popover || !expanded)return;
    const outside=(event:PointerEvent)=>{if(event.target instanceof Node && !host.current?.contains(event.target))setExpanded(false);};
    document.addEventListener('pointerdown',outside);
    return()=>document.removeEventListener('pointerdown',outside);
  },[popover,expanded]);
  return <div ref={host} className={`disclosure ${className}`} data-expanded={expanded} onKeyDown={event=>{
    if(popover && expanded && event.key==='Escape'){event.preventDefault();event.stopPropagation();setExpanded(false);host.current?.querySelector<HTMLButtonElement>('.disclosure-toggle')?.focus();}
    onKeyDown?.(event);
  }}>
    <button type="button" className="disclosure-toggle" aria-expanded={expanded} aria-controls={id} onClick={() => setExpanded(value => !value)}>
      {popover?<ChevronDown size={14}/>:<ChevronRight size={14}/>} {label}
    </button>
    <div id={id} className="disclosure-content" aria-hidden={!expanded} ref={node => {if (node) node.toggleAttribute('inert', !expanded);}} onClick={event=>{if(popover && event.target instanceof Element && event.target.closest('button'))setExpanded(false);}}>
      <div>{children}</div>
    </div>
  </div>;
}
