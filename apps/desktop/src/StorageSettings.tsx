import {useEffect, useId, useRef, useState, type ReactNode} from 'react';
import {invoke} from '@tauri-apps/api/core';
import {open} from '@tauri-apps/plugin-dialog';
import {FolderOpen, HardDrive, LoaderCircle, Monitor, X} from 'lucide-react';
import {call, message} from './engine';
import {ConfirmDialog} from './ConfirmDialog';
import {useDialogFocus} from './useDialogFocus';
import {plural} from './types';
import './storage-settings.css';

export type StorageStatus = {
  path: string; source: 'default' | 'chosen' | 'environment'; available: boolean; volume: string; external: boolean; default_path: string;
  usage: {bytes: number; files: number; groups: {label: string; bytes: number}[]} | null; free: number | null;
  previous: {path: string; volume: string; bytes: number} | null;
  moving: {state: 'running' | 'done' | 'failed' | 'cancelled'; stage: string; target: string; copied?: number; total?: number; files_done?: number; files?: number; current?: string | null; error?: string | null} | null;
  freed?: number;
};
type Plan = {target: string; volume: string; external: boolean; mode: 'move' | 'existing' | null; problem: string | null;
  needed?: number; files?: number; free?: number | null; library?: {tracks: number | null; changed_at: number | null};
  /** What was asked for, so the same choice can be confirmed. */ request?: {path?: string; default?: boolean}};

/** A notice the app shows once after it reloads on the new library. */
export const STORAGE_NOTICE = 'crateloom-storage-notice';

export const size = (value: number) => value >= 1e9 ? `${(value / 1e9).toFixed(value >= 1e10 ? 0 : 1)} GB`
  : value >= 1e6 ? `${Math.round(value / 1e6)} MB` : `${Math.max(1, Math.round(value / 1e3))} KB`;
const drive = (name: string, external: boolean) => external ? `“${name}”` : 'this Mac';
const changed = (seconds: number | null) => seconds ? new Date(seconds * 1000).toLocaleDateString(undefined, {day: 'numeric', month: 'short', year: 'numeric'}) : 'an unknown date';

/** Restart the engine on whatever location is now configured, then start the window afresh. */
async function restart(notice?: string) {
  await invoke('restart_engine');
  try { if (notice) sessionStorage.setItem(STORAGE_NOTICE, notice); } catch { /* the notice is optional */ }
  window.location.reload();
}

async function chooseFolder(): Promise<string | null> {
  const picked = await open({directory: true, multiple: false, defaultPath: '/Volumes', title: 'Choose where CrateLoom keeps its library'}).catch(() => null);
  return typeof picked === 'string' ? picked : null;
}

function PlanCard({plan, busy, onMove, onUse, onCancel}: {plan: Plan; busy: boolean; onMove: () => void; onUse: () => void; onCancel: () => void}) {
  return <div className="storage-plan" role="group" aria-label="New library location">
    <p className="storage-path" title={plan.target}>{plan.target}</p>
    {plan.mode === 'move' && <p>CrateLoom copies {plan.files ? plural(plan.files, 'file') : 'its files'} ({size(plan.needed ?? 0)}) to {drive(plan.volume, plan.external)}
      {plan.free != null && <>, which has {size(plan.free)} free</>}. It checks the copy before switching and keeps the current folder until you delete it.</p>}
    {plan.mode === 'existing' && <p>This folder already has a CrateLoom library{plan.library?.tracks != null && <> with {plural(plan.library.tracks, 'track')}</>}, last changed {changed(plan.library?.changed_at ?? null)}.
      CrateLoom will use that library as it is. Your current library stays where it is.</p>}
    {plan.problem && <p className="inline-error" role="alert">{plan.problem}</p>}
    <div className="storage-actions">
      <button type="button" disabled={busy} onClick={onCancel}>Cancel</button>
      {plan.mode === 'move' && <button type="button" className="primary" disabled={busy || !!plan.problem} onClick={onMove}>Move library</button>}
      {plan.mode === 'existing' && <button type="button" className="primary" disabled={busy || !!plan.problem} onClick={onUse}>Use that library</button>}
    </div>
  </div>;
}

/**
 * Where CrateLoom keeps its library: downloads, DJ set audio, artwork, analysis and caches.
 * Moving copies it, checks the copy, switches, and restarts the engine; the old folder stays until you delete it.
 */
export function StorageSettings({onClose}: {onClose: () => void}) {
  const [status, setStatus] = useState<StorageStatus | null>(null), [plan, setPlan] = useState<Plan | null>(null);
  const [busy, setBusy] = useState(false), [error, setError] = useState(''), [confirmDiscard, setConfirmDiscard] = useState(false);
  const [restarting, setRestarting] = useState(false), [finished, setFinished] = useState<StorageStatus['moving']>(null);
  const moving = status?.moving?.state === 'running';
  const dialog = useDialogFocus(onClose, moving || restarting || busy);
  const id = useId();
  const mounted = useRef(true);
  useEffect(() => () => { mounted.current = false; }, []);

  const load = async () => {
    try { const next = await call<StorageStatus>('storage.status'); if (mounted.current) setStatus(next); return next; }
    catch (failure) { if (mounted.current) setError(message(failure)); return null; }
  };
  useEffect(() => { void load(); }, []);

  // Follow a running move; when it ends, the engine restarts on whichever folder is now in use.
  useEffect(() => {
    if (!moving) return;
    const timer = window.setInterval(async () => {
      const next = await load();
      const state = next?.moving?.state;
      if (!next || !state || state === 'running') return;
      window.clearInterval(timer);
      if (state === 'done') {
        setRestarting(true);
        const left = next.previous ? ` The earlier copy is still on ${drive(next.previous.volume, next.previous.volume !== 'This Mac')}; delete it in Library location to free ${size(next.previous.bytes)}.` : '';
        await restart(`Library moved to ${next.moving!.target}.${left}`).catch(failure => { setRestarting(false); setError(message(failure)); });
      } else {
        setFinished(next.moving);
        setRestarting(true);
        // The engine paused its background work for the move; start it again on the unchanged library.
        await invoke('restart_engine').catch(failure => setError(message(failure)));
        if (mounted.current) { setRestarting(false); await load(); }
      }
    }, 400);
    return () => window.clearInterval(timer);
  }, [moving]); // eslint-disable-line react-hooks/exhaustive-deps

  const check = async (params: {path?: string; default?: boolean}) => {
    setBusy(true); setError(''); setFinished(null);
    try { setPlan({...await call<Plan>('storage.check', params), request: params}); }
    catch (failure) { setError(message(failure)); }
    finally { setBusy(false); }
  };
  const choose = async () => { const folder = await chooseFolder(); if (folder) await check({path: folder}); };
  const move = async () => {
    if (!plan) return;
    setBusy(true); setError('');
    try { setStatus(await call<StorageStatus>('storage.move', plan.request ?? {})); setPlan(null); }
    catch (failure) { setError(message(failure)); }
    finally { setBusy(false); }
  };
  const use = async () => {
    if (!plan) return;
    setBusy(true); setError('');
    try { await call('storage.use', plan.request ?? {}); setRestarting(true); await restart(`CrateLoom now uses the library in ${plan.target}.`); }
    catch (failure) { setError(message(failure)); setBusy(false); setRestarting(false); }
  };
  const discard = async () => {
    setBusy(true); setError('');
    try {
      const next = await call<StorageStatus>('storage.discard_previous');
      setStatus(next); setConfirmDiscard(false);
      setFinished({state: 'done', stage: `Deleted the earlier copy and freed ${size(next.freed ?? 0)}.`, target: next.path});
    } catch (failure) { setError(message(failure)); }
    finally { setBusy(false); }
  };

  const progress = status?.moving;
  const fraction = progress?.total ? Math.min(1, (progress.copied ?? 0) / progress.total) : 0;
  const locked = status?.source === 'environment';
  return <div className="modal-backdrop" onMouseDown={event => { if (event.target === event.currentTarget && !moving && !restarting && !busy) onClose(); }}>
    <section ref={dialog} tabIndex={-1} className="storage-window" role="dialog" aria-modal="true" aria-labelledby={`${id}-title`}>
      <div className="storage-head">
        <div><h2 id={`${id}-title`}>Library location</h2><p>Downloads, DJ set audio, artwork, analysis and caches.</p></div>
        <button type="button" className="quiet" aria-label="Close" disabled={moving || restarting} onClick={onClose}><X size={16}/></button>
      </div>
      {!status ? <p className="storage-loading" role="status"><LoaderCircle size={15} className="spin" aria-hidden="true"/> Measuring the library…</p> : <>
        <div className="storage-current">
          <span className="storage-drive">{status.external ? <HardDrive size={18} aria-hidden="true"/> : <Monitor size={18} aria-hidden="true"/>}</span>
          <div>
            <strong>{status.external ? status.volume : 'This Mac'}</strong>
            <p className="storage-path" title={status.path}>{status.path}</p>
            {status.usage && <p>{size(status.usage.bytes)} in {plural(status.usage.files, 'file')}{status.free != null && <> · {size(status.free)} free on this drive</>}</p>}
          </div>
        </div>
        {status.usage && status.usage.groups.length > 0 && <ul className="storage-groups" aria-label="What the library holds">
          {status.usage.groups.map(group => <li key={group.label}><span>{group.label}</span><span>{size(group.bytes)}</span></li>)}
        </ul>}
        <p className="storage-note">Music you imported from your own folders stays where it is. Only the files CrateLoom keeps itself move.</p>
        {moving && progress ? <div className="storage-progress" role="status" aria-live="polite">
          <p><strong>{restarting ? 'Restarting on the new location…' : progress.stage}</strong>{progress.total ? ` · ${size(progress.copied ?? 0)} of ${size(progress.total)}` : ''}</p>
          <div className="storage-bar" role="progressbar" aria-label="Moving the library" aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(fraction * 100)}><span style={{width: `${fraction * 100}%`}}/></div>
          {progress.current && <p className="storage-path" title={progress.current}>{progress.current}</p>}
          <div className="storage-actions"><button type="button" disabled={restarting} onClick={() => void call('storage.cancel').then(() => load())}>Stop moving</button></div>
        </div> : restarting ? <p className="storage-loading" role="status"><LoaderCircle size={15} className="spin" aria-hidden="true"/> Restarting CrateLoom’s engine…</p>
          : plan ? <PlanCard plan={plan} busy={busy} onMove={() => void move()} onUse={() => void use()} onCancel={() => setPlan(null)}/>
          : <div className="storage-actions storage-choices">
            {locked ? <p className="storage-note">This CrateLoom was started with CRATELOOM_DATA, so its library folder is set outside the app.</p> : <>
              {status.source !== 'default' && <button type="button" disabled={busy} onClick={() => void check({default: true})}><Monitor size={15}/> Move back to this Mac</button>}
              <button type="button" className="primary" disabled={busy} onClick={() => void choose()}><FolderOpen size={15}/> {busy ? 'Checking…' : 'Choose a folder…'}</button>
            </>}
          </div>}
        {finished && <p className={finished.state === 'done' ? 'storage-done' : 'inline-error'} role="status">
          {finished.state === 'cancelled' ? 'Stopped. Your library stays where it was, and the partial copy was removed.' : finished.state === 'failed' ? (finished.error || 'The move failed. Your library was not changed.') : finished.stage}</p>}
        {status.previous && !moving && !restarting && <div className="storage-previous">
          <p>The earlier copy is still on {drive(status.previous.volume, status.previous.volume !== 'This Mac')} ({size(status.previous.bytes)}).</p>
          <p className="storage-path" title={status.previous.path}>{status.previous.path}</p>
          <button type="button" disabled={busy} onClick={() => setConfirmDiscard(true)}>Delete earlier copy…</button>
        </div>}
      </>}
      {error && <p className="inline-error" role="alert">{error}</p>}
    </section>
    {confirmDiscard && status?.previous && <ConfirmDialog title="Delete the earlier copy?" confirmLabel="Delete" busy={busy} error={error}
      onConfirm={() => void discard()} onCancel={() => setConfirmDiscard(false)}>
      <p>This permanently deletes {status.previous.path} ({size(status.previous.bytes)}). It cannot be undone.</p>
      <p>Your library in {status.path} is not touched.</p>
    </ConfirmDialog>}
  </div>;
}

/** Shows the app only when its library folder can be opened; otherwise explains which drive to connect. */
export function StorageGate({children}: {children: ReactNode}) {
  const [status, setStatus] = useState<StorageStatus | null>(null), [error, setError] = useState(''), [busy, setBusy] = useState(false);
  const [plan, setPlan] = useState<Plan | null>(null);
  const load = () => call<StorageStatus>('storage.status').then(setStatus).catch(failure => { setError(message(failure)); setStatus(null); });
  useEffect(() => {
    // The app applies the chosen appearance once it opens; this screen shows before it.
    try { document.documentElement.dataset.theme = localStorage.getItem('theme') || 'system'; } catch { /* system appearance */ }
    void load();
  }, []);
  if (status?.available || (!status && error)) return <>{children}</>;
  if (!status) return null;
  const retry = async () => { setBusy(true); setError(''); try { await restart(); } catch (failure) { setError(message(failure)); setBusy(false); } };
  const elsewhere = async () => {
    const folder = await chooseFolder(); if (!folder) return;
    setBusy(true); setError('');
    try {
      const found = await call<Plan>('storage.check', {path: folder});
      if (found.mode !== 'existing') throw new Error(found.problem || 'There is no CrateLoom library in that folder.');
      await call('storage.use', {path: folder}); await restart(`CrateLoom now uses the library in ${found.target}.`);
    } catch (failure) { setError(message(failure)); setBusy(false); }
  };
  const local = async () => {
    setBusy(true); setError('');
    try { setPlan(await call<Plan>('storage.check', {default: true})); } catch (failure) { setError(message(failure)); } finally { setBusy(false); }
  };
  const useLocal = async () => {
    setBusy(true); setError('');
    try { await call('storage.use', {default: true}); await restart(`CrateLoom now uses the library in ${status.default_path}.`); }
    catch (failure) { setError(message(failure)); setBusy(false); }
  };
  return <div className="storage-missing" role="main">
    <section aria-labelledby="storage-missing-title">
      <span className="storage-drive"><HardDrive size={22} aria-hidden="true"/></span>
      <h1 id="storage-missing-title">Connect “{status.volume}”</h1>
      <p>CrateLoom keeps its library on this drive, in <span className="storage-path" title={status.path}>{status.path}</span>. Connect it, then choose Try again.</p>
      {plan ? <div className="storage-plan">
        <p>{plan.mode === 'existing' ? <>This Mac has a CrateLoom library from {changed(plan.library?.changed_at ?? null)}{plan.library?.tracks != null && <> with {plural(plan.library.tracks, 'track')}</>}. CrateLoom will use it as it is; the library on “{status.volume}” is not changed.</>
          : <>CrateLoom will start a new, empty library on this Mac. The library on “{status.volume}” is not changed, and you can switch back once the drive is connected.</>}</p>
        {plan.problem && <p className="inline-error" role="alert">{plan.problem}</p>}
        <div className="storage-actions"><button type="button" disabled={busy} onClick={() => setPlan(null)}>Cancel</button>
          <button type="button" className="primary" disabled={busy || !!plan.problem} onClick={() => void useLocal()}>{plan.mode === 'existing' ? 'Use library on this Mac' : 'Start a new library'}</button></div>
      </div> : <div className="storage-actions">
        <button type="button" disabled={busy} onClick={() => void local()}>Use this Mac instead…</button>
        <button type="button" disabled={busy} onClick={() => void elsewhere()}>Choose another library…</button>
        <button type="button" className="primary" disabled={busy} onClick={() => void retry()}>{busy ? 'Checking…' : 'Try again'}</button>
      </div>}
      {error && <p className="inline-error" role="alert">{error}</p>}
    </section>
  </div>;
}
