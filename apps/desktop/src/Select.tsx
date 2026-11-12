import {forwardRef,useState,type SelectHTMLAttributes} from 'react';
import {ChevronDown} from 'lucide-react';

export const Select = forwardRef<HTMLSelectElement,SelectHTMLAttributes<HTMLSelectElement>>(function Select({children,onChange,onBlur,onKeyDown,onPointerDown,...props},ref) {
  const [expanded,setExpanded] = useState(false);
  return <span className="select-field" data-expanded={expanded} data-disabled={props.disabled || false}>
    <select {...props} ref={ref} onChange={event=>{setExpanded(false);onChange?.(event);}} onBlur={event=>{setExpanded(false);onBlur?.(event);}}
      onPointerDown={event=>{if(!props.disabled)setExpanded(value=>!value);onPointerDown?.(event);}}
      onKeyDown={event=>{if([' ','ArrowDown','ArrowUp'].includes(event.key))setExpanded(true);if(['Enter','Escape','Tab'].includes(event.key))setExpanded(false);onKeyDown?.(event);}}>{children}</select>
    <ChevronDown size={14} aria-hidden="true"/>
  </span>;
});
