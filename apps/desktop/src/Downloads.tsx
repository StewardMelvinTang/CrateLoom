import {Download, FolderOpen, RotateCcw, X} from 'lucide-react';
import type {AcquisitionJob} from './DiscoveryPanel';
import {useDialogFocus} from './useDialogFocus';

export const activeDownloads = ['queued', 'downloading', 'verifying', 'importing', 'cancelling'];
export const retryableDownloads = ['failed', 'interrupted', 'cancelled'];

const bytes = (value: number) => value >= 1024 * 1024
  ? `${(value / 1024 / 1024).toFixed(1)} MB` : `${Math.round(value / 1024)} KB`;

export function DownloadProgress({title, received, total}: {title: string; received: number; total: number|null}) {
  const known = total != null && total > 0;
  const value = known ? Math.min(received, total) : undefined;
  return <div className="transfer-progress">
    <progress aria-label={`Downloading ${title}`} max={known ? total : undefined} value={value}/>
    {known ? <span>{Math.round((value ?? 0) / total * 100)}%</span> : received > 0 && <span>{bytes(received)}</span>}
  </div>;
}

export function Downloads({jobs, error, close, clear, cancel, retry, open}: {
  jobs: AcquisitionJob[]; error: string; close: () => void; clear: () => void;
  cancel: (id: string) => void; retry: (id: string) => void; open: (id: string) => void;
}) {
  const focus = useDialogFocus(close, false);
  return <div className="download-overlay" onMouseDown={e => {if (e.target === e.currentTarget) close();}}>
    <section ref={focus} className="downloads-popover" role="dialog" aria-modal="true" aria-label="Downloads" tabIndex={-1}>
      <div className="downloads-heading">
        <h2><Download size={16}/> Downloads</h2>
        <button title="Clear completed downloads from history" disabled={!jobs.some(job => job.state === 'completed')} onClick={clear}>Clear</button>
        <button className="icon" aria-label="Close downloads" title="Close downloads" onClick={close}><X size={17}/></button>
      </div>
      {error && <p role="alert" className="download-empty">{error}</p>}
      {!jobs.length && <p className="download-empty">No downloads</p>}
      <ul className="downloads-list">
        {jobs.map(job => <li key={job.id}>
          <div className="download-summary">
            <div><strong title={job.title}>{job.title}</strong><small>{[job.artist,job.stage].filter(Boolean).join(' · ')}</small></div>
            {activeDownloads.includes(job.state) && <button className="icon" title="Cancel download" aria-label={`Cancel ${job.title}`} onClick={() => cancel(job.id)}><X size={15}/></button>}
            {retryableDownloads.includes(job.state) && <button className="icon" title="Retry download" aria-label={`Retry ${job.title}`} onClick={() => retry(job.id)}><RotateCcw size={15}/></button>}
            {job.track_id && <button className="icon" title="Open track" aria-label={`Open ${job.title}`} onClick={() => open(job.track_id!)}><FolderOpen size={16}/></button>}
          </div>
          {job.state === 'downloading' && <DownloadProgress title={job.title} received={job.downloaded_bytes} total={job.total_bytes}/>}
          {job.error && <small className="download-error">{job.error}</small>}
        </li>)}
      </ul>
    </section>
  </div>;
}
