import {useEffect, useLayoutEffect, useRef, useState, type ReactNode} from 'react';

export type ContextItem = {label: string; icon?: ReactNode; disabled?: boolean; hint?: string; onSelect: () => void} | 'separator';

/** A menu opened at the pointer by a right-click (or the keyboard menu key). Arrow keys move, Escape closes. */
export function ContextMenu({x, y, label, items, onClose}: {x: number; y: number; label: string; items: ContextItem[]; onClose: (restoreFocus: boolean) => void}) {
  const list = useRef<HTMLDivElement>(null);
  const [position, setPosition] = useState({left: x, top: y});
  const close = useRef(onClose); close.current = onClose;
  // Keep the whole menu inside the window, opening up or left when there is no room.
  useLayoutEffect(() => {
    const box = list.current?.getBoundingClientRect();
    if (!box) return;
    const margin = 8;
    setPosition({left: Math.max(margin, x + box.width > innerWidth - margin ? x - box.width : x), top: Math.max(margin, y + box.height > innerHeight - margin ? y - box.height : y)});
    list.current?.querySelector<HTMLButtonElement>('button:not(:disabled)')?.focus();
  }, [x, y]);
  useEffect(() => {
    const outside = (event: PointerEvent) => { if (event.target instanceof Node && !list.current?.contains(event.target)) close.current(false); };
    const dismiss = () => close.current(false);
    document.addEventListener('pointerdown', outside, true);
    window.addEventListener('blur', dismiss); window.addEventListener('resize', dismiss);
    document.addEventListener('scroll', dismiss, true);
    return () => {
      document.removeEventListener('pointerdown', outside, true);
      window.removeEventListener('blur', dismiss); window.removeEventListener('resize', dismiss);
      document.removeEventListener('scroll', dismiss, true);
    };
  }, []);
  const move = (step: number | 'first' | 'last') => {
    const options = Array.from(list.current?.querySelectorAll<HTMLButtonElement>('button:not(:disabled)') || []);
    if (!options.length) return;
    const at = options.indexOf(document.activeElement as HTMLButtonElement);
    options[step === 'first' ? 0 : step === 'last' ? options.length - 1 : (at + step + options.length) % options.length].focus();
  };
  return <div ref={list} className="context-menu menu-list" role="menu" aria-label={label} style={position}
    onContextMenu={event => event.preventDefault()}
    onKeyDown={event => {
      if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); close.current(true); }
      else if (event.key === 'ArrowDown') { event.preventDefault(); move(1); }
      else if (event.key === 'ArrowUp') { event.preventDefault(); move(-1); }
      else if (event.key === 'Home') { event.preventDefault(); move('first'); }
      else if (event.key === 'End') { event.preventDefault(); move('last'); }
      else if (event.key === 'Tab') { event.preventDefault(); close.current(true); }
    }}>
    {items.map((item, index) => item === 'separator'
      ? <div key={`separator-${index}`} role="separator" className="context-menu-separator"/>
      : <button key={item.label} type="button" role="menuitem" disabled={item.disabled} onClick={() => { close.current(true); item.onSelect(); }}>
          {item.icon}<span className="menu-item-text"><span>{item.label}</span></span>{item.hint && <kbd>{item.hint}</kbd>}
        </button>)}
  </div>;
}

/** Copy text to the clipboard, with a fallback for web views that refuse the async API. */
export async function copyText(value: string) {
  try { await navigator.clipboard.writeText(value); return; } catch { /* fall back below */ }
  const area = document.createElement('textarea');
  area.value = value; area.setAttribute('readonly', ''); area.style.position = 'fixed'; area.style.opacity = '0';
  document.body.appendChild(area); area.select();
  const copied = document.execCommand('copy');
  area.remove();
  if (!copied) throw new Error('The text could not be copied to the clipboard.');
}
