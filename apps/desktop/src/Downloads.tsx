import {useState, type ReactNode} from 'react';
import {AlertTriangle, Check, ChevronRight, Download, LoaderCircle, RotateCcw, X} from 'lucide-react';
import type {AcquisitionJob} from './DiscoveryPanel';
import {Disclosure} from './Disclosure';
import {useDialogFocus} from './useDialogFocus';
import {call, message} from './engine';
import './downloads.css';

/** The single definition of download states, shared by every panel that shows a download. */
export const activeDownloads = ['queued', 'downloading', 'verifying', 'importing', 'cancelling'];
export const retryableDownloads = ['failed', 'interrupted', 'cancelled'];
export const isActiveDownload = (state: string|null|undefined) => activeDownloads.includes(state || '');
export const isRetryableDownload = (state: string|null|undefined) => retryableDownloads.includes(state || '');

export const formatBytes = (value: number) => value >= 1024 * 1024
  ? `${(value / 1024 / 1024).toFixed(1)} MB` : `${Math.round(value / 1024)} KB`;

// Low-level failures arrive as raw system or HTTP text. Each pattern maps to one plain sentence with a next step.
const lowLevelErrors: [RegExp, string][] = [
  [/\[Errno (?:54|32|104)\]|connection reset|broken pipe|ECONNRESET|IncompleteRead|RemoteDisconnected/i, 'The connection dropped during the download. Retry to start it again.'],
  [/\[Errno (?:8|-2|-3)\]|nodename nor servname|name or service not known|getaddrinfo|temporary failure in name resolution/i, 'The server could not be reached. Check your internet connection, then retry.'],
  [/\[Errno (?:61|111)\]|connection refused/i, 'The server refused the connection. Retry later or choose another source.'],
  [/\[Errno (?:50|51|65)\]|network is (?:down|unreachable)|no route to host/i, 'There is no network connection. Reconnect, then retry.'],
  [/\[Errno 60\]|\[Errno 110\]|operation timed out|read timed out|timed out$/i, 'The server stopped responding. Retry, or choose another source.'],
  [/\[Errno 28\]|no space left/i, 'The disk is full. Free some space, then retry.'],
  [/\[Errno (?:13|1)\]|permission denied|operation not permitted/i, 'CrateLoom could not write to the downloads folder. Check its permissions, then retry.'],
  [/HTTP (?:Error )?(?:401|403)/i, 'The source refused the download. The link may have expired; find the source again.'],
  [/HTTP (?:Error )?(?:404|410)/i, 'The file is no longer at this address. Choose another source.'],
  [/HTTP (?:Error )?429|too many requests/i, 'The source is limiting requests. Wait a few minutes, then retry.'],
  [/HTTP (?:Error )?5\d\d/i, 'The source had a server problem. Retry later.'],
  [/SSL|certificate/i, 'A secure connection could not be made. Check your network, then retry.'],
  [/^Audio duration differs from the chosen recording/i, 'The downloaded audio was a different length than this track, so it was not added. Choose another version or add your own source.'],
  [/^Interrupted; retry to start again\.?$/i, 'CrateLoom closed before this download finished. Retry to start it again.'],
  [/\[Errno -?\d+\]|urlopen error|Traceback|Exception/i, 'The download failed because of a system error. Retry to start it again.'],
];

/** A plain sentence for a download failure, with the original text kept as `detail` when it was rewritten. */
export function describeDownloadError(error: string|null|undefined): {text: string; detail: string|null}|null {
  const raw = (error || '').trim();
  if (!raw) return null;
  const found = lowLevelErrors.find(([pattern]) => pattern.test(raw));
  return found ? {text: found[1], detail: raw} : {text: raw, detail: null};
}

/** What a download is doing, in words that do not depend on the engine's stage text. */
export function downloadStateLabel(job: {state: string; stage?: string|null; downloaded_bytes?: number|null; total_bytes?: number|null; duplicate?: boolean}) {
  const percent = job.total_bytes && job.total_bytes > 0 ? ` ${Math.round(Math.min(job.downloaded_bytes || 0, job.total_bytes) / job.total_bytes * 100)}%` : '';
  switch (job.state) {
    case 'queued': return 'Waiting to download';
    case 'downloading': return job.downloaded_bytes ? `Downloading${percent}` : job.stage || 'Connecting…';
    case 'verifying': return 'Checking the audio file';
    case 'importing': return 'Adding to library';
    case 'cancelling': return 'Cancelling…';
    case 'completed': return job.duplicate ? 'Already in library' : 'In library';
    case 'failed': return 'Download failed';
    case 'interrupted': return 'Download interrupted';
    case 'cancelled': return 'Download cancelled';
    default: return job.stage || job.state;
  }
}

/** In progress, did not finish, completed: the order a person needs them in. */
export function groupDownloads<T extends {state: string}>(jobs: T[]) {
  const active = jobs.filter(job => isActiveDownload(job.state));
  const completed = jobs.filter(job => job.state === 'completed');
  const unfinished = jobs.filter(job => !isActiveDownload(job.state) && job.state !== 'completed');
  return {active, unfinished, completed};
}

/** Hides failed, cancelled and interrupted downloads from the list. A later retry shows that row again. */
export const clearFailedDownloads = () => call<{cleared: number}>('acquisitions.clear_failed');

export function DownloadProgress({title, received, total}: {title: string; received: number; total: number|null}) {
  const known = total != null && total > 0;
  const value = known ? Math.min(received, total) : undefined;
  return <div className="transfer-progress">
    <progress aria-label={`Downloading ${title}`} max={known ? total : undefined} value={value}/>
    {known ? <span>{Math.round((value ?? 0) / total * 100)}%</span> : received > 0 && <span>{formatBytes(received)}</span>}
  </div>;
}

/** One failure line: the plain sentence, with the original technical text one click away. */
export function DownloadError({error, announce = true}: {error: string|null|undefined; announce?: boolean}) {
  const problem = describeDownloadError(error);
  if (!problem) return null;
  // A failure saved earlier is shown quietly; only a new one interrupts a screen reader.
  return <div className="download-problem">
    <p role={announce ? 'alert' : undefined}>{problem.text}</p>
    {problem.detail && <Disclosure label="Technical detail"><code>{problem.detail}</code></Disclosure>}
  </div>;
}

export function Downloads({jobs, error, close, clear, cancel, retry, open}: {
  jobs: AcquisitionJob[]; error: string; close: () => void; clear: () => void;
  cancel: (id: string) => void; retry: (id: string) => void; open: (id: string) => void;
}) {
  const focus = useDialogFocus(close, false);
  // The list is refreshed by the app's own polling; rows cleared here are hidden at once until it catches up.
  const [cleared, setCleared] = useState<string[]>([]);
  const [clearing, setClearing] = useState(false), [clearError, setClearError] = useState('');
  const {active, unfinished, completed} = groupDownloads(jobs.filter(job => !(cleared.includes(job.id) && isRetryableDownload(job.state))));
  const clearFailed = async () => {
    if (clearing) return;
    const ids = unfinished.map(job => job.id);
    setClearing(true); setClearError('');
    try { await clearFailedDownloads(); setCleared(current => [...current, ...ids]); }
    catch (e) { setClearError(message(e)); }
    finally { setClearing(false); }
  };
  const row = (job: AcquisitionJob) => <li key={job.id}>
    <div className="download-summary">
      <div><strong title={job.title}>{job.title}</strong><small>{[job.artist, downloadStateLabel(job)].filter(Boolean).join(' · ')}</small></div>
      {isActiveDownload(job.state) && <button className="icon" disabled={job.state === 'cancelling'} title="Cancel download" aria-label={`Cancel ${job.title}`} onClick={() => cancel(job.id)}><X size={15}/></button>}
      {isRetryableDownload(job.state) && <button title="Start this download again" aria-label={`Retry ${job.title}`} onClick={() => retry(job.id)}><RotateCcw size={13}/> Retry</button>}
      {job.state === 'completed' && job.track_id && <button title="Show this track in the library" aria-label={`Open ${job.title}`} onClick={() => open(job.track_id!)}>Open track<ChevronRight size={13}/></button>}
    </div>
    {job.state === 'downloading' && <DownloadProgress title={job.title} received={job.downloaded_bytes} total={job.total_bytes}/>}
    <DownloadError error={job.error}/>
  </li>;
  const group = (label: string, icon: ReactNode, items: AcquisitionJob[]) => items.length > 0 && <section className="downloads-group" aria-label={label}>
    <h3>{icon}{label} · {items.length}</h3>
    <ul className="downloads-list">{items.map(row)}</ul>
  </section>;
  return <div className="download-overlay" onMouseDown={e => {if (e.target === e.currentTarget) close();}}>
    <section ref={focus} className="downloads-popover" role="dialog" aria-modal="true" aria-label="Downloads" tabIndex={-1}>
      <div className="downloads-heading">
        <h2><Download size={16}/> Downloads</h2>
        {unfinished.length > 0 && <button disabled={clearing} title="Remove failed, cancelled and interrupted downloads from this list." onClick={() => void clearFailed()}>Clear failed</button>}
        {completed.length > 0 && <button title="Remove completed downloads from this list. The tracks stay in your library." onClick={clear}>Clear completed</button>}
        <button className="icon" aria-label="Close downloads" title="Close downloads" onClick={close}><X size={17}/></button>
      </div>
      {(clearError || error) && <p role="alert" className="download-empty">{clearError || message(error)}</p>}
      {!(active.length + unfinished.length + completed.length) && <p className="download-empty">No downloads yet. Tracks you download from Find Music or a DJ set appear here.</p>}
      <div className="downloads-groups">
        {group('In progress', <LoaderCircle size={13} className="spin" aria-hidden="true"/>, active)}
        {group('Did not finish', <AlertTriangle size={13} aria-hidden="true"/>, unfinished)}
        {group('Completed', <Check size={13} aria-hidden="true"/>, completed)}
      </div>
    </section>
  </div>;
}
