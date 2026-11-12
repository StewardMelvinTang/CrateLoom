import {useEffect, useRef, useState} from 'react';
import {invoke, convertFileSrc} from '@tauri-apps/api/core';
import {open} from '@tauri-apps/plugin-dialog';
import {DownloadProgress} from './Downloads';
import {ArrowUp, ArrowDown, ArrowUpRight, FolderOpen, History, ListMusic, Pause, Play, Plus, Search, Trash2, GripVertical, Music2, Download, LoaderCircle, Check} from 'lucide-react';
import {Disclosure} from './Disclosure';
import {Select} from './Select';
import {useTrackReorder} from './useTrackReorder';
import {ServiceIcon} from './ServiceIcon';
import {ScanProgress,type ScanProgressData} from './ScanProgress';

type SourceLink = {label: string; url: string};
type SetTrack = {
  entry_id: string; duration?: number|null; artwork_path?: string|null;
  position: number; at: number|null; end?: number; downloaded_bytes?: number|null; total_bytes?: number|null; title: string; artist: string; evidence: string[];
  links?: SourceLink[]; track_id: string|null; playback_track_id: string|null; download_id: string|null; download_state: string|null;
  source_options?: {title: string; url: string; duration?: number; reference_only?: boolean; compatible?: boolean; recommended?: boolean; recommendation_reason?: string}[];
  needs_review?: boolean; unknown?: boolean; version_warning?: string; error: string|null;
};
type Report = {id: string; name: string; watch: string|null; playlist_id: string|null; warning: string|null; tracks: SetTrack[];
  scan?: {offset: number|null; duration: number|null; retry_at: number; automatic: boolean; running: boolean; stalled: boolean; fast: boolean; samples: number;cancelling?:boolean;progress?:ScanProgressData;pending_matches?:number}};
type Task = {id: string; state: 'running'|'cancelling'|'cancelled'|'completed'|'failed'; result: Report|null; error: string|null;cancellable?:boolean;progress?:ScanProgressData};
type SavedSet = {id: string; name: string; entries: number; imported: number; with_download: number;
  offset: number|null; duration: number|null; automatic: number; created_at: number|null};
const call = <T,>(method: string, params: object = {}) => invoke<T>('engine_call', {method, params});
const isDownloading = (row: SetTrack) => ['queued', 'downloading', 'verifying', 'importing', 'cancelling'].includes(row.download_state || '');
const timestamp = (seconds: number) => {
  const minutes = Math.floor(seconds / 60);
  return `${minutes >= 60 ? Math.floor(minutes / 60) + ':' : ''}${String(minutes % 60).padStart(2, '0')}:${String(Math.floor(seconds % 60)).padStart(2, '0')}`;
};

export function SetImportPanel({hidden, activeTrackId, playing, onPlay, onUpdated, onPlaylist, onRetry}: {
  hidden: boolean; onUpdated: () => Promise<void>; onPlaylist: (id: string) => void; onRetry: (id: string) => Promise<void>;
  activeTrackId: string|null; playing: boolean; onPlay: (id: string, order: string[]) => void;
}) {
  const [view,setView] = useState<'new'|'history'|'report'>('new');
  const viewRef=useRef(view);viewRef.current=view;
  const [completedScan,setCompletedScan]=useState<Report|null>(null);
  const workspace = useRef<HTMLElement>(null);
  const [deleteId,setDeleteId] = useState<string|null>(null);
  const [url, setUrl] = useState('');
  const [file, setFile] = useState('');
  const [tracklist, setTracklist] = useState('');
  const [configured, setConfigured] = useState(false);
  const [fingerprint, setFingerprint] = useState(false);
  const [youtubeBrowser,setYoutubeBrowser]=useState('');
  const [spotifyToken, setSpotifyToken] = useState('');
  const [spotifyConnected, setSpotifyConnected] = useState(false);
  const [soundcloudToken, setSoundcloudToken] = useState('');
  const [soundcloudConnected, setSoundcloudConnected] = useState(false);
  const [report, setReport] = useState<Report|null>(null);
  const [saved, setSaved] = useState<SavedSet[]>([]);
  const [name, setName] = useState('');
  const [task, setTask] = useState<Task|null>(null);
  const [intent, setIntent] = useState('');
  const [error, setError] = useState('');
  const [pending, setPending] = useState(false);
  const [startingScan,setStartingScan]=useState(false);
  const [scanCancelled,setScanCancelled]=useState(false);
  const [sourceRow, setSourceRow] = useState<string|null>(null);
  const [rowTasks, setRowTasks] = useState<{entry_id:string;report_id:string;task_id:string}[]>([]);
  const [queuedRows, setQueuedRows] = useState<string[]>([]);
  const [rowErrors, setRowErrors] = useState<Record<string,string>>({});
  const reorderLock=useRef(false);
  const orderRevision=useRef(0);
  const [reordering, setReordering] = useState(false);
  const reportRef = useRef(report); reportRef.current = report;
  const artworkRequested = useRef(new Set<string>());
  const soleSourceAttempted = useRef(new Set<string>());
  const [sourceUrl, setSourceUrl] = useState('');
  const busy = pending || !!task;
  const activeScan=startingScan || !!task?.cancellable;
  const audioScanStatus=/audio|recognition|identifying|transitions/i.test(task?.progress?.stage || intent) || !!task?.progress && ['bytes','seconds','samples'].includes(task.progress.unit);
  const refreshSets = async () => setSaved(await call<SavedSet[]>('sets.list'));
  const historyScanning = saved.some(item=>item.automatic && item.offset!==null);

  useEffect(() => { workspace.current?.scrollTo(0,0); },[view]);
  useEffect(() => {
    if (hidden || view!=='history' || !historyScanning) return;
    const timer = window.setInterval(()=>void refreshSets().catch(()=>{}),4000);
    return ()=>window.clearInterval(timer);
  },[hidden,view,historyScanning]);

  useEffect(() => {
    if (hidden) return;
    void call<{fingerprinting: boolean}>('sets.capabilities').then(c => {
      setConfigured(c.fingerprinting);
    }).catch(e => setError(String(e)));
    void refreshSets().catch(e => setError(String(e)));
  }, [hidden]);

  const run = async (action: () => Promise<void>) => {
    setError('');
    setPending(true);
    try { await action(); }
    catch (e) { setError(String(e)); }
    finally { setPending(false); }
  };
  const start = (method: string, params: object, label: string) => run(async () => {
    setIntent(label);
    setScanCancelled(false);
    setStartingScan(method==='sets.scan' || method==='sets.resume_scan');
    try{setTask(await call<Task>(method,params));}finally{setStartingScan(false);}
  });
  const cancelTask=async()=>{
    if(!task || task.state==='cancelling')return;
    try{setTask(await call<Task>('sets.cancel_task',{task_id:task.id}));}catch(e){setError(String(e));}
  };
  const load = async (id: string) => {
    const next = await call<Report>('sets.get', {id});
    setReport(next);
    setName(next.name);
    setSourceRow(null);
    setView('report');
  };
  const remove = (id:string) => run(async () => {
    await call('sets.delete',{id});
    if (report?.id===id) setReport(null);
    setDeleteId(null);
    await refreshSets();
  });
  useEffect(() => {
    if (!task) return;
    let cancelled = false;
    const timer = window.setTimeout(async () => {
      try {
        const next = await call<Task>('metadata.job', {id: task.id});
        if (cancelled) return;
        if (next.state === 'running' || next.state==='cancelling') { setTask(next); return; }
        setTask(null);
        if(next.state==='cancelled')setScanCancelled(true);
        if (!next.result) { if(next.state!=='cancelled')setError(next.error || 'Set import did not finish.'); return; }
        const updated=reportRef.current?.id===next.result.id ? await call<Report>('sets.get',{id:next.result.id}) : next.result;
        if(viewRef.current==='new' || reportRef.current?.id===updated.id){
          setReport(updated);setName(updated.name);
          if(viewRef.current==='new')setView('report');
        }else setCompletedScan(updated);
        const source = next.result.tracks.find(row => row.entry_id === sourceRow);
        if (source?.track_id || (source?.download_id && !source.error)) setSourceRow(null);
        await refreshSets();
        if (next.result.playlist_id) await onUpdated();
      } catch (e) {
        if (!cancelled) { setTask(null); setError(String(e)); }
      }
    }, 800);
    return () => { cancelled = true; window.clearTimeout(timer); };
  }, [task]);

  useEffect(() => {
    if (!report || hidden || artworkRequested.current.has(report.id)) return;
    artworkRequested.current.add(report.id);
    const sid = report.id;
    void call<Task>('sets.artwork', {id:sid}).then(next => setRowTasks(tasks => [...tasks, {entry_id:'artwork', report_id:sid, task_id:next.id}])).catch(() => artworkRequested.current.delete(sid));
  }, [report?.id, hidden]);

  useEffect(() => {
    if (!rowTasks.length) return;
    let cancelled = false;
    const timer = window.setTimeout(async () => {
      const results = await Promise.allSettled(rowTasks.map(item => call<Task>('metadata.job', {id:item.task_id})));
      if (cancelled) return;
      const finished = rowTasks.filter((_, index) => results[index].status === 'rejected' || (results[index] as PromiseFulfilledResult<Task>).value.state !== 'running');
      results.forEach((result, index) => {
        const item = rowTasks[index];
        const message = result.status === 'rejected' ? String(result.reason) : result.value.error;
        if (message && item.entry_id !== 'artwork') setRowErrors(errors => ({...errors, [item.entry_id]:message}));
      });
      try {
        const sid = reportRef.current?.id;
        if (sid && rowTasks.some(item => item.report_id === sid)) {
          const revision=orderRevision.current;
          const next = await call<Report>('sets.get', {id:sid});
          if (!cancelled && reportRef.current?.id === sid && revision===orderRevision.current && !reorderLock.current) setReport(next);
        }
        if (finished.some(item => item.entry_id !== 'artwork')) await onUpdated();
      } catch (e) { if (!cancelled) setError(String(e)); }
      if (!cancelled) setRowTasks(tasks => tasks.filter(item => !finished.some(done => done.task_id === item.task_id)));
    }, 900);
    return () => {cancelled = true; window.clearTimeout(timer);};
  }, [rowTasks]);

  const chooseSource = async (row:SetTrack, url:string) => {
    const sid = reportRef.current?.id;
    if (!sid) return;
    setQueuedRows(rows => [...rows,row.entry_id]);
    setRowErrors(errors => ({...errors,[row.entry_id]:''}));
    try {
      const next = await call<Task>('sets.add_source', {id:sid,entry_id:row.entry_id,url});
      setRowTasks(tasks => [...tasks,{entry_id:row.entry_id,report_id:sid,task_id:next.id}]);
    } catch (e) { setRowErrors(errors => ({...errors,[row.entry_id]:String(e)})); }
    finally {setQueuedRows(rows => rows.filter(id => id !== row.entry_id));}
  };
  useEffect(() => {
    if (!report?.playlist_id || hidden || busy) return;
    for (const row of report.tracks) {
      const options = (row.source_options || []).filter(option => !option.reference_only && (option.compatible ?? option.recommended));
      const key = `${report.id}:${row.entry_id}`;
      if (row.track_id || row.unknown || isDownloading(row) || options.length !== 1 || soleSourceAttempted.current.has(key) || queuedRows.includes(row.entry_id) || rowTasks.some(task => task.report_id === report.id && task.entry_id === row.entry_id)) continue;
      soleSourceAttempted.current.add(key);
      void chooseSource(row,options[0].url);
    }
  }, [report, hidden, busy]);
  const reorder = async (entryId:string, targetId:string) => {
    const current = reportRef.current;
    if (!current || entryId === targetId || reorderLock.current) return;
    const ids = current.tracks.map(row => row.entry_id);
    const from = ids.indexOf(entryId), to = ids.indexOf(targetId);
    if (from < 0 || to < 0) return;
    ids.splice(to,0,ids.splice(from,1)[0]);
    reorderLock.current=true;orderRevision.current++;setReordering(true);
    setReport({...current,tracks:ids.map((id,position) => ({...current.tracks.find(row => row.entry_id === id)!,position}))});
    try {
      const next = await call<Report>('sets.reorder', {id:current.id,entry_ids:ids});
      if (reportRef.current?.id === current.id) setReport(next);
      await onUpdated();
    } catch (e) {setReport(current);setError(String(e));}
    finally {reorderLock.current=false;setReordering(false);}
  };

  const trackReorder=useTrackReorder('set',(id,target)=>void reorder(id,target),reordering);

  const downloading = report?.tracks.some(isDownloading) || false;
  const scanning = report?.scan && (report.scan.automatic || report.scan.running || report.scan.cancelling) && report.scan.offset !== null;
  useEffect(() => {
    if (!report || hidden || (!scanning && !downloading && (intent !== 'Finding files…' || !task))) return;
    let cancelled = false;
    const timer = window.setInterval(async () => {
      try {
        const revision=orderRevision.current;
        const next = await call<Report>('sets.get', {id: report.id});
        if (!cancelled && revision===orderRevision.current && !reorderLock.current) setReport(next);
      } catch (e) { if (!cancelled) setError(String(e)); }
    }, 1800);
    return () => { cancelled = true; window.clearInterval(timer); };
  }, [report?.id, hidden, scanning, downloading, intent, !!task]);

  const openLink = (link: string) => invoke<void>('open_reference', {url: link}).catch(e => setError(String(e)));
  return <section ref={workspace} className="set-workspace" aria-label="Import DJ Set" hidden={hidden}>
    <nav className="set-navigation" aria-label="DJ sets">
      <button type="button" aria-pressed={view==='new'} onClick={()=>setView('new')}><Plus size={16}/>{activeScan?'Active scan':'New scan'}</button>
      <button type="button" aria-pressed={view==='history'} onClick={()=>{setView('history');setDeleteId(null);void refreshSets().catch(e=>setError(String(e)));}}><History size={16}/> History</button>
      {report && <button type="button" aria-pressed={view==='report'} onClick={()=>setView('report')}>Current set</button>}
    </nav>
    {completedScan && !task && <div className="set-background-status" role="status"><Check size={16}/><span>Scan ready · {completedScan.name}</span><button onClick={()=>{setReport(completedScan);setName(completedScan.name);setCompletedScan(null);setView('report');}}>Open scanned set</button></div>}
    {view!=='new' && (task || startingScan) && <div className="set-background-status"><LoaderCircle size={16} className="spin"/><span role="status">{task?.progress?.stage || intent || 'Scan in progress'}</span><button onClick={()=>setView('new')}>View scan</button>{task?.cancellable && <button disabled={task.state==='cancelling'} onClick={()=>void cancelTask()}>{task.state==='cancelling'?'Stopping…':'Cancel scan'}</button>}</div>}
    {view==='new' && <form className="set-form" onSubmit={e => {
      e.preventDefault();
      if(busy)return;
      void start('sets.scan', {url: url.trim(), path: file || null, tracklist, fingerprint: fingerprint && !!(url.trim() || file),youtube_browser:youtubeBrowser || null}, file || fingerprint ? 'Scanning mix audio…' : 'Reading set…');
    }}>
      <label htmlFor="set-url">Mix or playlist link</label>
      <div className="set-form-line">
        <input id="set-url" type="url" placeholder="YouTube, Spotify or SoundCloud" value={url} disabled={busy || !!file} onChange={e => setUrl(e.target.value)} />
        {configured && <button type="button" disabled={busy} onClick={() => void run(async () => {
          if (file) { setFile(''); return; }
          const path = await open({filters: [{name: 'Audio', extensions: ['mp3', 'm4a', 'wav', 'flac', 'aiff', 'aif', 'ogg']}]});
          if (typeof path === 'string') { setUrl(''); setFile(path); setFingerprint(true); }
        })}><FolderOpen size={15}/>{file ? 'Clear file' : 'Choose audio'}</button>}
        <button className="primary" disabled={busy || (!url.trim() && !file && !tracklist.trim())}><Search size={15}/> Scan</button>
      </div>
      {file && <span>{file.split(/[\\/]/).at(-1)}</span>}
      <Disclosure key={activeScan?'scanning-options':'scan-options'} className="set-connections" label="Options" initialOpen={/YouTube.*(?:age-verified|signed-in|browser)/.test(error)} onKeyDown={e => {if(e.key === 'Enter' && e.target instanceof HTMLInputElement) e.preventDefault();}}>
      {configured ? <label className="set-check"><input type="checkbox" checked={fingerprint} disabled={busy || !!file} onChange={e => setFingerprint(e.target.checked)}/>
        Scan audio even if the video has a tracklist
      </label> : <small>Install the ShazamIO scanner to identify tracks from audio.</small>}
      <label className="set-youtube-access">YouTube access <Select aria-label="YouTube access" value={youtubeBrowser} disabled={busy} onChange={e=>setYoutubeBrowser(e.target.value)}>
        <option value="">Public access</option><option value="safari">Signed in with Safari</option><option value="chrome">Signed in with Chrome</option><option value="firefox">Signed in with Firefox</option><option value="edge">Signed in with Edge</option>
      </Select></label>
      <small className="set-access-note">For mixes requiring sign-in, choose a browser where you can play the video. Its YouTube session is used locally for this scan. macOS may request access.</small>
      <label className="set-manual-list">Paste a tracklist
        <textarea placeholder="00:00 Artist – Track" rows={3} maxLength={100000} value={tracklist} disabled={busy} onChange={e=>setTracklist(e.target.value)}/>
      </label>
      <Disclosure className="set-connections" label="Spotify and SoundCloud connections">
        <div className="set-form-line">
          <input aria-label="Spotify access token" placeholder="Spotify access token" type="password" value={spotifyToken} onChange={e => setSpotifyToken(e.target.value)}/>
          <button type="button" disabled={busy || !spotifyToken.trim()} onClick={() => void run(async () => {
            await call('sets.configure_spotify',{token:spotifyToken.trim()});
            setSpotifyConnected(true);setSpotifyToken('');
          })}>{spotifyConnected ? 'Update Spotify' : 'Connect Spotify'}</button>
          {spotifyConnected && <button type="button" onClick={() => void run(async () => {
            await call('sets.configure_spotify',{token:''});setSpotifyConnected(false);
          })}>Disconnect</button>}
        </div>
        <div className="set-form-line">
          <input aria-label="SoundCloud access token" placeholder="SoundCloud access token" type="password" value={soundcloudToken} onChange={e => setSoundcloudToken(e.target.value)}/>
          <button type="button" disabled={busy || !soundcloudToken.trim()} onClick={() => void run(async () => {
            const result = await call<{connected: boolean}>('sets.configure_soundcloud',{token:soundcloudToken.trim()});
            setSoundcloudConnected(result.connected);setSoundcloudToken('');
          })}>{soundcloudConnected ? 'Update SoundCloud' : 'Connect SoundCloud'}</button>
          {soundcloudConnected && <button type="button" onClick={() => void run(async () => {
            const result = await call<{connected: boolean}>('sets.configure_soundcloud',{token:''});setSoundcloudConnected(result.connected);
          })}>Disconnect</button>}
        </div>
      </Disclosure>
      </Disclosure>
    </form>}
    {view==='history' && <section className="set-history-panel" aria-label="Scan history">
      <h2>Scan history</h2>
      {saved.length ? <ul className="set-history-list">{saved.map(item=><li key={item.id}>
        <button className="set-history-open" disabled={pending} onClick={()=>void run(()=>load(item.id))}>
          <strong>{item.name}</strong>
          <small>{item.entries} {item.entries===1?'entry':'entries'}{item.imported ? ` · ${item.imported} in playlist` : item.with_download ? ` · ${item.with_download} with sources` : ''}
            {item.offset !== null ? item.automatic ? ' · In progress' : ' · Paused' : ''}
            {item.created_at && ` · ${new Date(item.created_at*1000).toLocaleDateString()}`}</small>
        </button>
        {deleteId===item.id ? <div className="set-history-confirm">
          <small>Delete this report? Your music and playlist stay.</small>
          <button type="button" disabled={busy} onClick={()=>void remove(item.id)}>Delete report</button>
          <button type="button" onClick={()=>setDeleteId(null)}>Cancel</button>
        </div> : <button className="set-history-delete" aria-label={`Delete ${item.name}`} title="Delete report" disabled={busy}
          onClick={()=>setDeleteId(item.id)}><Trash2 size={16}/></button>}
      </li>)}</ul> : <p>No scans yet. Start with a mix link or audio file.</p>}
    </section>}
    {view==='new' && (task || startingScan) && <div className={activeScan?'set-progress-stage':'set-progress-compact'}><ScanProgress label={activeScan?audioScanStatus?'Scanning audio':'Reading tracklist':intent || 'Scanning DJ set'} progress={task?.progress} cancelling={task?.state==='cancelling'} onCancel={task?.cancellable?()=>void cancelTask():undefined}/></div>}
    {scanCancelled && !task && <p className="set-cancelled-status" role="status">Scan cancelled. Any completed entries and audio samples are kept.</p>}
    {error && <p role="alert">{error}</p>}
    {view==='report' && report && <div className="set-report">
      <div className="set-report-head">
        <div><h2>{report.name}</h2><small>{report.tracks.length} entries
          {report.playlist_id && ` · ${report.tracks.filter(row => row.track_id).length} in playlist`}
          {report.scan?.fast && ' · Approximate times'}</small></div>
        {report.playlist_id ? <button disabled={busy} onClick={() => onPlaylist(report.playlist_id!)}><ListMusic size={15}/> Open playlist</button> :
          <form className="set-playlist-create" onSubmit={e => {e.preventDefault();void start('sets.prepare', {id: report.id, name}, 'Finding files…');}}>
            <Disclosure label="Playlist name">
              <input aria-label="Set playlist name" maxLength={100} value={name} disabled={busy} onChange={e => setName(e.target.value)} />
            </Disclosure>
            <button className="primary" disabled={busy || !name.trim()}>Create playlist</button>
          </form>}
        {report.playlist_id && report.tracks.some(row => !row.track_id && !row.download_id && !row.unknown && (row.artist || row.at === null)) &&
          <button disabled={busy} onClick={() => void start('sets.prepare', {id: report.id}, 'Finding files…')}>Find missing files</button>}
        {report.playlist_id && report.tracks.some(row => !row.track_id && !isDownloading(row) && row.source_options?.some(option => option.recommended)) &&
          <button className="primary" disabled={busy || rowTasks.some(item => item.report_id === report.id && item.entry_id !== 'artwork') || queuedRows.length > 0} onClick={() => void run(async () => {
            const sid = report.id;
            const tasks = await call<(Task & {entry_id:string})[]>('sets.apply_recommended', {id:sid});
            setRowTasks(current => [...current,...tasks.map(item => ({entry_id:item.entry_id,report_id:sid,task_id:item.id}))]);
          })}><Download size={15}/> Apply recommended sources</button>}
      </div>
      {report.warning && <p className="set-warning">{report.warning.replace(/Shazam timestamps are approximate\. Check track versions and transitions\.?/g,'').trim() || 'Approximate track positions.'}</p>}
      {report.scan && report.scan.offset !== null && <div className="set-scan-status"><ScanProgress
        label={report.scan.cancelling?'Stopping audio scan':report.scan.running?'Scanning audio':report.scan.automatic?'Audio scan queued':'Audio scan paused'}
        active={report.scan.running || report.scan.automatic && report.scan.retry_at<=Date.now()/1000}
        cancelling={report.scan.cancelling}
        progress={report.scan.progress || {stage:report.scan.automatic?(report.scan.retry_at>Date.now()/1000?'Waiting for the recognition service':'Preparing the next section'):report.scan.stalled?'Paused after repeated retries':'Your saved matches and samples are ready to resume',completed:report.scan.offset,total:report.scan.duration,unit:'seconds'}}
        onCancel={report.scan.automatic || report.scan.running?()=>void run(async()=>{setReport(await call<Report>('sets.cancel_scan',{id:report.id}));}):undefined}>
        {report.scan.progress && <p className="scan-progress-detail time">Saved through {timestamp(report.scan.offset)}{report.scan.duration?` of ${timestamp(report.scan.duration)}`:''}</p>}
        {report.scan.samples>0 && !report.scan.running && <p className="scan-progress-detail">{report.scan.samples} saved audio samples{report.scan.pending_matches?` · ${report.scan.pending_matches} matches kept`:''}</p>}
        {!report.scan.automatic && !report.scan.running && <button type="button" disabled={busy || !configured} onClick={()=>void run(async()=>{setScanCancelled(false);setReport(await call<Report>('sets.auto_scan',{id:report.id,enabled:true}));})}>Resume audio scan</button>}
      </ScanProgress></div>}
      {report.scan?.offset === null && report.scan.duration !== null && <p className="set-cancelled-status" role="status">Scan complete · {timestamp(report.scan.duration)}</p>}
      <ol className="set-tracklist" aria-label="Set tracklist">
        {report.tracks.map(row => {
          const sourceBusy = queuedRows.includes(row.entry_id) || rowTasks.some(item => item.report_id === report.id && item.entry_id === row.entry_id);
          const trackId = row.playback_track_id;
          const current = !!trackId && trackId === activeTrackId;
          const isPlaying = current && playing;
          const retryable = !!row.download_id && ['failed', 'interrupted', 'cancelled'].includes(row.download_state || '');
          const canChooseSource = !!report.playlist_id && !row.track_id && (!row.download_id || retryable);
          const status = row.track_id ? trackId ? 'In playlist' : 'File missing' : trackId ? 'In library' : isDownloading(row) ? row.download_state :
            row.download_state ? 'Download ' + row.download_state : row.unknown ? 'Unidentified' : row.needs_review || !row.artist || row.source_options?.length ?
            'Review match' : row.error ? 'Source needed' : 'Identified';
          const query = encodeURIComponent([row.artist,row.title].filter(Boolean).join(' '));
          const candidates = [
            {label: 'SoundCloud', url: `https://soundcloud.com/search/sounds?q=${query}`},
            {label: 'YouTube Music', url: `https://music.youtube.com/search?q=${query}`},
            {label: 'Spotify', url: `https://open.spotify.com/search/${query}`},
          ];
          const links = [...new Map((row.links || []).map(link => [link.label,link])).values()];
          const watch = report.watch;
          const at = row.at;
          const timeLabel = String(row.position + 1).padStart(2, '0');
          return <li key={row.entry_id} className={`${current ? 'set-track-current' : ''} ${trackReorder.target === row.entry_id ? 'drop-target' : ''}`} data-reorder-group="set" data-reorder-id={row.entry_id}>
            <button className="reorder-handle" type="button" {...trackReorder.handle(row.entry_id)} disabled={reordering} aria-label={`Reorder ${row.title}. Use up or down arrow keys.`} title="Drag to reorder · Up / Down keys" onKeyDown={e => {if(e.key==='ArrowUp'||e.key==='ArrowDown'){e.preventDefault();const next=report.tracks[row.position+(e.key==='ArrowUp'?-1:1)];if(next)void reorder(row.entry_id,next.entry_id);}}}><GripVertical size={16}/></button>
            {watch && at != null ? <button type="button" className="set-time" aria-label={`Entry ${row.position+1}. Open set at ${timestamp(at)}`} onClick={() => void openLink(watch + '&t=' + Math.floor(at))}>{timeLabel}<small>{timestamp(at)}</small></button> :
              <span className="set-time">{timeLabel}</span>}
            <div className="set-artwork cover">{row.artwork_path ? <img src={convertFileSrc(row.artwork_path)} alt="" loading="lazy" onError={e => {e.currentTarget.style.display='none';}}/> : <Music2 size={22}/>}</div>
            <div className="set-track-info">
              <strong>{row.title}</strong>
              <small>{row.artist}</small>
              {row.version_warning && <small>{row.version_warning}</small>}
              {row.error && <small>{row.error}</small>}
              {rowErrors[row.entry_id] && <small role="alert">{rowErrors[row.entry_id]}</small>}
              {row.download_state === 'downloading' && <DownloadProgress title={row.title} received={row.downloaded_bytes || 0} total={row.total_bytes ?? null}/>}
              {canChooseSource && !!row.source_options?.length && <Disclosure label={`Review sources · ${row.source_options.length}`}>
                <ul className="set-source-options">{row.source_options.map(option => <li key={option.url}>
                  <div className={`set-source-choice ${option.recommended ? 'is-recommended' : ''}`}>
                    <button className="set-source-title" title={option.title} onClick={() => void openLink(option.url)}><ServiceIcon name={option.url.includes('soundcloud.com')?'SoundCloud':'YouTube'}/><span>{option.title}{option.recommended && <small><Check size={11}/> Recommended · {option.recommendation_reason}</small>}</span><ArrowUpRight size={12}/></button>
                    <span className="time source-duration">{option.duration ? timestamp(option.duration) : '—'}</span>
                    {!option.reference_only ? <button className="use-source" disabled={sourceBusy || busy} onClick={() => void chooseSource(row,option.url)}>{sourceBusy ? <><LoaderCircle size={13} className="spin"/> Checking</> : 'Use source'}</button> : <small>Reference</small>}
                  </div>
                </li>)}</ul>
              </Disclosure>}
              {!row.track_id && !trackId && !row.unknown && <Disclosure label="Links" className="set-row-links"><div className="set-links">
                {[...links, ...candidates.filter(candidate => !links.some(link => link.label === candidate.label))].map(link =>
                  link.label === 'Creator download' ?
                    <button key={link.label} title={link.url} onClick={() => void run(() => navigator.clipboard.writeText(link.url))}>Copy creator link</button> :
                    <button key={link.label} onClick={() => void openLink(link.url)}><ServiceIcon name={link.label}/>{link.label}<ArrowUpRight size={12}/></button>)}
              </div></Disclosure>}
              {sourceRow === row.entry_id && <form className="set-form-line" onSubmit={e => {
                e.preventDefault(); void chooseSource(row,sourceUrl);
              }}>
                {!row.unknown && <><input aria-label="Audio source URL" type="url" placeholder="Direct audio download URL" value={sourceUrl} disabled={busy} onChange={e => setSourceUrl(e.target.value)} />
                <button disabled={busy || sourceBusy || !sourceUrl.trim()}>Download</button></>}
                <button type="button" disabled={busy} onClick={() => void run(async () => {
                  const path = await open({filters:[{name:'Audio', extensions:['mp3','m4a','wav','flac','aiff','aif','ogg']}]});
                  if (typeof path === 'string') {
                    setIntent('Importing file…');
                    setTask(await call<Task>('sets.add_local', {id: report.id, entry_id: row.entry_id, path}));
                  }
                })}><FolderOpen size={14}/> Local file</button>
              </form>}
            </div>
            <div className="set-track-state"><span className="time set-duration">{row.duration ? timestamp(row.duration) : '—'}</span>{sourceBusy ? <span role="status"><LoaderCircle size={13} className="spin"/> Checking source</span> : status!=='Identified' && <span>{status}</span>}
              <span className="set-order-actions"><button title="Move up" aria-label={`Move ${row.title} up`} disabled={reordering || row.position===0} onClick={()=>void reorder(row.entry_id,report.tracks[row.position-1].entry_id)}><ArrowUp size={13}/></button><button title="Move down" aria-label={`Move ${row.title} down`} disabled={reordering || row.position===report.tracks.length-1} onClick={()=>void reorder(row.entry_id,report.tracks[row.position+1].entry_id)}><ArrowDown size={13}/></button></span>
              {trackId && <button className="set-track-play" aria-label={`${isPlaying ? 'Pause' : 'Play'} ${row.title}`}
                onClick={() => onPlay(trackId, [...new Set(report.tracks.flatMap(item => item.playback_track_id ? [item.playback_track_id] : []))])}>{isPlaying ? <Pause size={15}/> : <Play size={15}/>}</button>}
              {canChooseSource &&
                <button disabled={busy || sourceBusy} onClick={() => {setSourceRow(sourceRow === row.entry_id ? null : row.entry_id);setSourceUrl('');}}>{sourceRow === row.entry_id ? 'Close' : 'Add source'}</button>}
              {row.download_id && retryable &&
                <button disabled={busy} onClick={() => void run(async () => {await onRetry(row.download_id!);setReport(await call<Report>('sets.get', {id: report.id}));})}>Retry</button>}
            </div>
          </li>;
        })}
      </ol>
    </div>}
  </section>;
}
