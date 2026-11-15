import {useId, type ReactNode} from 'react';
import {useDialogFocus} from './useDialogFocus';

/** Asks before something is removed. Cancel is first, so it receives focus and Escape also cancels. */
export function ConfirmDialog({title, children, confirmLabel, busy = false, error = '', onConfirm, onCancel}: {
  title: string; children: ReactNode; confirmLabel: string; busy?: boolean; error?: string; onConfirm: () => void; onCancel: () => void;
}) {
  const dialog = useDialogFocus(onCancel, busy);
  const id = useId();
  return <div className="modal-backdrop" onMouseDown={event => { if (event.target === event.currentTarget && !busy) onCancel(); }}>
    <section ref={dialog} tabIndex={-1} className="confirm-window" role="alertdialog" aria-modal="true" aria-labelledby={`${id}-title`} aria-describedby={`${id}-body`}>
      <h2 id={`${id}-title`}>{title}</h2>
      <div id={`${id}-body`} className="confirm-body">{children}</div>
      {error && <p className="inline-error" role="alert">{error}</p>}
      <div className="confirm-actions">
        <button type="button" disabled={busy} onClick={onCancel}>Cancel</button>
        <button type="button" className="primary" disabled={busy} onClick={onConfirm}>{busy ? 'Working…' : confirmLabel}</button>
      </div>
    </section>
  </div>;
}
