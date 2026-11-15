import {useEffect, useRef, useState, type ReactNode} from 'react';
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

let currentTransition:{skipTransition:()=>void}|undefined;
export function transitionWorkspace(update:()=>void) {
  const documentWithMotion = document as Document & {startViewTransition?:(update:()=>void)=>{skipTransition:()=>void;finished:Promise<void>;ready?:Promise<void>;updateCallbackDone?:Promise<void>}};
  // A hidden window cannot animate; apply the change directly instead of starting a transition that aborts.
  if (!documentWithMotion.startViewTransition || reducedMotion() || document.hidden) { update(); return; }
  currentTransition?.skipTransition();
  const transition = documentWithMotion.startViewTransition(() => flushSync(update));
  currentTransition = transition;
  // A skipped or aborted transition still applies the update; its rejections are expected.
  void transition.ready?.catch(()=>{});
  void transition.updateCallbackDone?.catch(error=>{if(!['AbortError','InvalidStateError'].includes(error?.name))console.error(error);});
  void transition.finished.catch(()=>{}).finally(()=>{if(currentTransition===transition)currentTransition=undefined;});
}
