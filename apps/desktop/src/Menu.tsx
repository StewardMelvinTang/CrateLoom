import {useEffect, useId, useRef, useState, type ReactNode} from 'react';
import {ChevronDown} from 'lucide-react';

export type MenuItem = {
  label: string;
  /** One short line under the label saying what the action does. */
  description?: string;
  /** Right-aligned note, such as a keyboard shortcut. */
  hint?: string;
  icon?: ReactNode;
  disabled?: boolean;
  onSelect: () => void;
};

/** A labelled button that opens a short list of actions. Arrow keys move, Escape closes. */
export function Menu({label, icon, items, title, ariaLabel, align = 'start', className = '', buttonClassName = '', disabled = false, heading}: {
  label: ReactNode; icon?: ReactNode; items: (MenuItem | null | false | undefined)[]; title?: string; ariaLabel?: string;
  align?: 'start' | 'end'; className?: string; buttonClassName?: string; disabled?: boolean; heading?: string;
}) {
  const [open, setOpen] = useState(false);
  const host = useRef<HTMLDivElement>(null), button = useRef<HTMLButtonElement>(null), list = useRef<HTMLDivElement>(null);
  const id = useId();
  const entries = items.filter((item): item is MenuItem => !!item);
  useEffect(() => {
    if (!open) return;
    const outside = (event: PointerEvent) => { if (event.target instanceof Node && !host.current?.contains(event.target)) setOpen(false); };
    document.addEventListener('pointerdown', outside);
    list.current?.querySelector<HTMLButtonElement>('button:not(:disabled)')?.focus();
    return () => document.removeEventListener('pointerdown', outside);
  }, [open]);
  useEffect(() => { if (disabled) setOpen(false); }, [disabled]);
  const move = (step: number | 'first' | 'last') => {
    const options = Array.from(list.current?.querySelectorAll<HTMLButtonElement>('button:not(:disabled)') || []);
    if (!options.length) return;
    const at = options.indexOf(document.activeElement as HTMLButtonElement);
    const next = step === 'first' ? 0 : step === 'last' ? options.length - 1 : (at + step + options.length) % options.length;
    options[next].focus();
  };
  return <div ref={host} className={`menu ${className}`} data-open={open} data-align={align}
    onKeyDown={event => {
      if (!open) return;
      if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); setOpen(false); button.current?.focus(); }
      else if (event.key === 'ArrowDown') { event.preventDefault(); move(1); }
      else if (event.key === 'ArrowUp') { event.preventDefault(); move(-1); }
      else if (event.key === 'Home') { event.preventDefault(); move('first'); }
      else if (event.key === 'End') { event.preventDefault(); move('last'); }
      else if (event.key === 'Tab') setOpen(false);
    }}>
    <button ref={button} type="button" className={`menu-button ${buttonClassName}`} title={title} aria-label={ariaLabel} aria-haspopup="menu" aria-expanded={open} aria-controls={open ? id : undefined}
      disabled={disabled} onClick={() => setOpen(value => !value)}>
      {icon}{label}<ChevronDown className="menu-chevron" size={14} aria-hidden="true"/>
    </button>
    {open && <div ref={list} id={id} className="menu-list" role="menu" aria-label={ariaLabel || (typeof label === 'string' ? label : undefined)}>
      {heading && <p className="menu-heading">{heading}</p>}
      {entries.map(item => <button key={item.label} type="button" role="menuitem" disabled={item.disabled}
        onClick={() => { setOpen(false); button.current?.focus(); item.onSelect(); }}>
        {item.icon}
        <span className="menu-item-text"><span>{item.label}</span>{item.description && <small>{item.description}</small>}</span>
        {item.hint && <kbd>{item.hint}</kbd>}
      </button>)}
    </div>}
  </div>;
}
