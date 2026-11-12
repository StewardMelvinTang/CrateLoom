import {useEffect,useRef} from 'react';
export function useDialogFocus(onClose:()=>void,blocked:boolean){
 const element=useRef<HTMLElement>(null),action=useRef({onClose,blocked});action.current={onClose,blocked};
 useEffect(()=>{const previous=document.activeElement as HTMLElement|null,host=element.current;if(!host)return;
  const controls=()=>Array.from(host.querySelectorAll<HTMLElement>('button:not(:disabled),input:not(:disabled),select:not(:disabled),textarea:not(:disabled),[tabindex="0"]')).filter(el=>el.getClientRects().length && !el.closest('[inert]'));
  (controls()[0] || host).focus();
  const key=(e:KeyboardEvent)=>{if(e.key==='Escape'){e.preventDefault();if(!action.current.blocked)action.current.onClose();}if(e.key==='Tab'){const all=controls(),first=all[0],last=all.at(-1);if(!first){e.preventDefault();host.focus();}else if(e.shiftKey && (document.activeElement===first || !host.contains(document.activeElement))){e.preventDefault();last?.focus();}else if(!e.shiftKey && (document.activeElement===last || !host.contains(document.activeElement))){e.preventDefault();first.focus();}}};
  document.addEventListener('keydown',key);return()=>{document.removeEventListener('keydown',key);previous?.focus();};
 },[]);return element;
}
