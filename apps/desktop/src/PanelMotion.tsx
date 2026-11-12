import {useEffect, useLayoutEffect, useRef, useState, type ReactNode, type RefObject} from 'react';
import {flushSync} from 'react-dom';

const reducedMotion = () => window.matchMedia('(prefers-reduced-motion: reduce)').matches;

export function PanelPresence({open, children}: {open:boolean; children:ReactNode}) {
  const [present,setPresent] = useState(open);
  const last = useRef(children);
  if (open) last.current = children;
  useEffect(() => {
    if (open) { setPresent(true); return; }
    const timer = window.setTimeout(() => setPresent(false),reducedMotion()?80:170);
    return () => clearTimeout(timer);
  },[open]);
  return open || present ? <div className="panel-presence" data-state={open?'open':'closing'}>{open?children:last.current}</div> : null;
}

// Resize the surface independently so form text stays sharp as the panel morphs.
export function usePanelMorph(ref:RefObject<HTMLElement>) {
  useLayoutEffect(() => {
    const panel = ref.current;
    if (!panel) return;
    let previous:DOMRect|null = null;
    let movement:Animation|undefined, surfaceMotion:Animation|undefined;
    const observer = new ResizeObserver(() => {
      movement?.cancel(); surfaceMotion?.cancel();
      const next = panel.getBoundingClientRect();
      const surface = panel.querySelector<HTMLElement>('.panel-surface');
      if (previous && surface && !reducedMotion() && (Math.abs(previous.height-next.height)>1 || Math.abs(previous.width-next.width)>1)) {
        const options = {duration:280,easing:'cubic-bezier(.16,1,.3,1)'};
        movement = panel.animate([{transform:`translate(${previous.x-next.x}px,${previous.y-next.y}px)`},{transform:'translate(0,0)'}],options);
        surfaceMotion = surface.animate([{transform:`scale(${previous.width/next.width},${previous.height/next.height})`},{transform:'scale(1,1)'}],options);
      }
      previous = next;
    });
    observer.observe(panel);
    return () => { observer.disconnect(); movement?.cancel(); surfaceMotion?.cancel(); };
  },[ref]);
}

let currentTransition:{skipTransition:()=>void}|undefined;
export function transitionWorkspace(update:()=>void) {
  const documentWithMotion = document as Document & {startViewTransition?:(update:()=>void)=>{skipTransition:()=>void;finished:Promise<void>}};
  if (!documentWithMotion.startViewTransition || reducedMotion()) { update(); return; }
  currentTransition?.skipTransition();
  const transition = documentWithMotion.startViewTransition(() => flushSync(update));
  currentTransition = transition;
  void transition.finished.catch(()=>{}).finally(()=>{if(currentTransition===transition)currentTransition=undefined;});
}
