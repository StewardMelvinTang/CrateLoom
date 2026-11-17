import {useEffect, useLayoutEffect, useRef, useState, type KeyboardEvent, type ReactNode} from 'react';
import {createPortal} from 'react-dom';
import {ChevronRight} from 'lucide-react';

export type ContextAction = {label: string; icon?: ReactNode; disabled?: boolean; hint?: string; danger?: boolean; onSelect: () => void};
export type ContextSubmenu = {label: string; icon?: ReactNode; disabled?: boolean; heading?: string; items: ContextItem[]};
export type ContextItem = ContextAction | ContextSubmenu | 'separator';

const isSubmenu = (item: ContextItem): item is ContextSubmenu => typeof item === 'object' && 'items' in item;
const margin = 8;

/** Keep a panel inside the window, flipping it to the other side of its anchor when there is no room. */
function place(box: DOMRect, x: number, y: number, flipX: number, flipY: number) {
  return {
    left: Math.max(margin, x + box.width > innerWidth - margin ? flipX - box.width : x),
    top: Math.max(margin, Math.min(y + box.height > innerHeight - margin ? flipY - box.height : y, innerHeight - margin - box.height)),
  };
}

function focusables(panel: HTMLElement | null) {
  return Array.from(panel?.querySelectorAll<HTMLButtonElement>(':scope > button:not(:disabled)') || []);
}

function moveFocus(panel: HTMLElement | null, step: number | 'first' | 'last') {
  const options = focusables(panel);
  if (!options.length) return;
  const at = options.indexOf(document.activeElement as HTMLButtonElement);
  options[step === 'first' ? 0 : step === 'last' ? options.length - 1 : (at + step + options.length) % options.length].focus();
}

function Entries({items, onAction, onSubmenu, openIndex, prefix}: {
  items: ContextItem[]; onAction: (item: ContextAction) => void; prefix: string;
  onSubmenu?: (index: number, anchor: HTMLButtonElement | null, focus: boolean) => void; openIndex?: number | null;
}) {
  return <>{items.map((item, index) => {
    if (item === 'separator') return <div key={`${prefix}separator-${index}`} role="separator" className="context-menu-separator"/>;
    if (isSubmenu(item)) return <button key={`${prefix}${item.label}`} type="button" role="menuitem" aria-haspopup="menu" aria-expanded={openIndex === index}
      disabled={item.disabled} data-open={openIndex === index || undefined}
      onMouseEnter={event => onSubmenu?.(index, event.currentTarget, false)}
      onClick={event => onSubmenu?.(index, event.currentTarget, true)}
      onKeyDown={event => { if (event.key === 'ArrowRight' || event.key === 'Enter' || event.key === ' ') { event.preventDefault(); event.stopPropagation(); onSubmenu?.(index, event.currentTarget, true); } }}>
      {item.icon}<span className="menu-item-text"><span>{item.label}</span></span><ChevronRight size={14} className="context-menu-arrow" aria-hidden="true"/>
    </button>;
    return <button key={`${prefix}${item.label}`} type="button" role="menuitem" disabled={item.disabled} data-danger={item.danger || undefined}
      onMouseEnter={() => onSubmenu?.(-1, null, false)} onClick={() => onAction(item)}>
      {item.icon}<span className="menu-item-text"><span>{item.label}</span></span>{item.hint && <kbd>{item.hint}</kbd>}
    </button>;
  })}</>;
}

/**
 * A menu opened at the pointer by a right-click, the keyboard menu key or a ⋯ button. It is drawn on top of
 * the whole window (above the player bar and dialogs' backdrops), and an item can open a submenu beside it.
 * Arrow keys move, Right opens a submenu, Left closes it, Escape closes.
 */
export function ContextMenu({x, y, label, items, onClose}: {x: number; y: number; label: string; items: ContextItem[]; onClose: (restoreFocus: boolean) => void}) {
  const list = useRef<HTMLDivElement>(null), sub = useRef<HTMLDivElement>(null);
  const [position, setPosition] = useState({left: x, top: y});
  const [open, setOpen] = useState<{index: number; anchor: HTMLButtonElement; focus: boolean} | null>(null);
  const opened = useRef(open); opened.current = open;
  const [subPosition, setSubPosition] = useState<{left: number; top: number} | null>(null);
  const close = useRef(onClose); close.current = onClose;
  useLayoutEffect(() => {
    const box = list.current?.getBoundingClientRect();
    if (!box) return;
    setPosition(place(box, x, y, x, y));
    focusables(list.current)[0]?.focus();
  }, [x, y]);
  useLayoutEffect(() => {
    if (!open) { setSubPosition(null); return; }
    const box = sub.current?.getBoundingClientRect(), anchor = open.anchor.getBoundingClientRect();
    if (!box) return;
    const next = place(box, anchor.right + 2, anchor.top - 7, anchor.left - 2, anchor.bottom + 7);
    setSubPosition(current => current && current.left === next.left && current.top === next.top ? current : next);
    if (open.focus) focusables(sub.current)[0]?.focus();
  }, [open]);
  useEffect(() => {
    const outside = (event: PointerEvent) => {
      if (event.target instanceof Node && !list.current?.contains(event.target) && !sub.current?.contains(event.target)) close.current(false);
    };
    const dismiss = () => close.current(false);
    // The right-click that opened the menu can focus the row and nudge its list's scroll position; that
    // scroll event arrives a frame later and must not close the menu it just opened.
    const opened = performance.now();
    const scrolled = (event: Event) => {
      if (performance.now() - opened < 250) return;
      if (!(event.target instanceof Node) || (!list.current?.contains(event.target) && !sub.current?.contains(event.target))) dismiss();
    };
    document.addEventListener('pointerdown', outside, true);
    window.addEventListener('blur', dismiss); window.addEventListener('resize', dismiss);
    document.addEventListener('scroll', scrolled, true);
    return () => {
      document.removeEventListener('pointerdown', outside, true);
      window.removeEventListener('blur', dismiss); window.removeEventListener('resize', dismiss);
      document.removeEventListener('scroll', scrolled, true);
    };
  }, []);
  const act = (item: ContextAction) => { close.current(true); item.onSelect(); };
  const openSubmenu = (index: number, anchor: HTMLButtonElement | null, focus: boolean) => {
    if (index < 0 || !anchor) { if (opened.current) setOpen(null); return; }
    // Hovering an item whose submenu is already open changes nothing; a click or Right moves focus into it.
    if (opened.current?.index === index && (!focus || opened.current.focus)) return;
    setOpen({index, anchor, focus});
  };
  const keys = (panel: () => HTMLDivElement | null, nested: boolean) => (event: KeyboardEvent) => {
    if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); if (nested) { open?.anchor.focus(); setOpen(null); } else close.current(true); }
    else if (event.key === 'ArrowLeft' && nested) { event.preventDefault(); event.stopPropagation(); open?.anchor.focus(); setOpen(null); }
    else if (event.key === 'ArrowDown') { event.preventDefault(); event.stopPropagation(); moveFocus(panel(), 1); }
    else if (event.key === 'ArrowUp') { event.preventDefault(); event.stopPropagation(); moveFocus(panel(), -1); }
    else if (event.key === 'Home') { event.preventDefault(); event.stopPropagation(); moveFocus(panel(), 'first'); }
    else if (event.key === 'End') { event.preventDefault(); event.stopPropagation(); moveFocus(panel(), 'last'); }
    else if (event.key === 'Tab') { event.preventDefault(); event.stopPropagation(); close.current(true); }
  };
  const submenu = open ? items[open.index] : null;
  return createPortal(<>
    <div ref={list} className="context-menu menu-list" role="menu" aria-label={label} style={position}
      onContextMenu={event => event.preventDefault()} onKeyDown={keys(() => list.current, false)}>
      <Entries items={items} onAction={act} onSubmenu={openSubmenu} openIndex={open?.index ?? null} prefix=""/>
    </div>
    {submenu && isSubmenu(submenu) && <div ref={sub} className="context-menu context-submenu menu-list" role="menu" aria-label={submenu.label}
      style={subPosition ?? {left: -9999, top: 0}} onContextMenu={event => event.preventDefault()} onKeyDown={keys(() => sub.current, true)}>
      {submenu.heading && <div className="context-menu-heading" aria-hidden="true">{submenu.heading}</div>}
      <Entries items={submenu.items} onAction={act} prefix="sub-"/>
    </div>}
  </>, document.body);
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
