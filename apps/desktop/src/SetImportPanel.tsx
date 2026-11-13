import {useEffect, useRef, useState, type ReactNode} from 'react';
import {invoke, convertFileSrc} from '@tauri-apps/api/core';
import {open} from '@tauri-apps/plugin-dialog';
import {AlertTriangle, ArrowUpRight, AudioLines, Check, ChevronDown, ChevronUp, Download, FolderOpen, GripVertical, History, Info, ListMusic, LoaderCircle, Music2, Pause, Play, Plus, RotateCcw, Search, Trash2, X} from 'lucide-react';
import {call, message} from './engine';
import {DownloadError, DownloadProgress, downloadStateLabel, isActiveDownload, isRetryableDownload} from './Downloads';
import {Disclosure} from './Disclosure';
import {Select} from './Select';
import {useTrackReorder} from './useTrackReorder';
import {ServiceIcon} from './ServiceIcon';
import {ScanProgress, type ScanProgressData, type ScanState} from './ScanProgress';
import './set-import.css';

type SourceLink = {label: string; url: string};
type SourceOption = {title: string; url: string; duration?: number; reference_only?: boolean; compatible?: boolean; recommended?: boolean; recommendation_reason?: string};
type SetTrack = {
  entry_id: string; duration?: number|null; artwork_path?: string|null;
  position: number; at: number|null; end?: number; downloaded_bytes?: number|null; total_bytes?: number|null; title: string; artist: string; evidence: string[];
  links?: SourceLink[]; track_id: string|null; playback_track_id: string|null; download_id: string|null; download_state: string|null;
  source_options?: SourceOption[];
  needs_review?: boolean; unknown?: boolean; version_warning?: string; error: string|null;
  /** Where the background source search for this entry stands; null when none was started. */
  source_search?: SourceSearch|null;
  /** The tracklist listed a link for this entry: "Create playlist" uses it even without an artist. */
  listed_link?: boolean;
  recognized?: {at: number; played: number|null}; recognized_other?: {at: number; title: string; artist: string}[];
};
type SourceSearch = 'queued'|'searching'|'ready'|'choose'|'none'|'failed';
type ReportScan = {offset: number|null; duration: number|null; retry_at: number; automatic: boolean; running: boolean; stalled: boolean; fast: boolean; samples: number; cancelling?: boolean; progress?: ScanProgressData; pending_matches?: number};
type Recognition = {state: 'none'|'running'|'paused'|'complete'|'failed'; confirmed: number; added: number; warning: string|null};
type Heard = {at: number; end: number; title: string; artist: string; samples: number};
type Report = {id: string; name: string; watch: string|null; playlist_id: string|null; warning: string|null; tracks: SetTrack[]; scan?: ReportScan;
  /** The browser session saved with this set; its audio scan resumes with it. Null means public access. */
  youtube_browser?: string|null;
  discovery?: {pending: number; running: number}; recognition?: Recognition;
  /** Recordings the running audio scan has heard; they become entries when the scan finishes. */
  provisional?: Heard[]};
type Task = {id: string; state: 'running'|'cancelling'|'cancelled'|'completed'|'failed'; result: Report|null; error: string|null; error_code?: string; error_detail?: string|null; cancellable?: boolean; progress?: ScanProgressData};
type SavedSet = {id: string; name: string; playlist_id?: string|null; entries: number; imported: number; with_download: number;
  offset: number|null; duration: number|null; automatic: number; created_at: number|null};
type RowTask = {entry_id: string; report_id: string; task_id: string};
type View = 'new'|'history'|'report';
type Mode = 'link'|'file'|'paste';

const AUDIO_EXTENSIONS = ['mp3', 'm4a', 'wav', 'flac', 'aiff', 'aif', 'ogg'];
const timestamp = (seconds: number) => {
  const minutes = Math.floor(seconds / 60);
  return `${minutes >= 60 ? Math.floor(minutes / 60) + ':' : ''}${String(minutes % 60).padStart(2, '0')}:${String(Math.floor(seconds % 60)).padStart(2, '0')}`;
};
const clock = (epochSeconds: number) => new Date(epochSeconds * 1000).toLocaleTimeString([], {hour: '2-digit', minute: '2-digit'});
const plural = (count: number, one: string, many = one + 's') => `${count} ${count === 1 ? one : many}`;
const rowKey = (reportId: string, entryId: string) => `${reportId}:${entryId}`;

// --- YouTube access ---------------------------------------------------------
// The engine reads a browser's YouTube session for mixes that need sign-in or age verification.
const YOUTUBE_ACCESS: {value: string; name: string; label: string; guide: string}[] = [
  {value: '', name: '', label: 'Public (no sign-in)', guide: 'Works for most mixes. If YouTube asks for sign-in or age verification, choose the browser where you are signed in to YouTube.'},
  {value: 'safari', name: 'Safari', label: 'Signed in with Safari', guide: 'Safari needs one permission first: allow CrateLoom under System Settings > Privacy & Security > Full Disk Access, then reopen CrateLoom.'},
  {value: 'chrome', name: 'Chrome', label: 'Signed in with Chrome', guide: 'macOS may show a keychain prompt for Chrome. Choose Allow.'},
  {value: 'firefox', name: 'Firefox', label: 'Signed in with Firefox', guide: 'Firefox needs no macOS prompt.'},
  {value: 'edge', name: 'Edge', label: 'Signed in with Edge', guide: 'macOS may show a keychain prompt for Edge. Choose Allow.'},
];
const ACCESS_STORAGE_KEY = 'setImport.youtubeAccess';
const savedYoutubeAccess = () => {
  try { const value = localStorage.getItem(ACCESS_STORAGE_KEY) || ''; return YOUTUBE_ACCESS.some(item => item.value === value) ? value : ''; }
  catch { return ''; }
};
const isYoutubeUrl = (text: string) => /^(?:https?:\/\/)?(?:[\w-]+\.)?(?:youtube\.com|youtu\.be)\//i.test(text.trim());
/** One YouTube video, as opposed to a playlist: the only kind of set whose audio can be recognized after the fact. */
const isYoutubeVideo = (text: string) => isYoutubeUrl(text) && !/[?&]list=|\/playlist\b/i.test(text);
// The same share and tracking parameters the engine drops; v and list are kept.
const TRACKING_PARAMS = /^(?:si|pp|feature|ref|fbclid|igsh|igshid|t|utm_.*)$/i;
/** A pasted link without its share and tracking parameters. Anything that is not a URL is returned as typed. */
const stripTracking = (text: string) => {
  const trimmed = text.trim();
  if (!/^(?:https?:\/\/)?[\w-]+(?:\.[\w-]+)+\/\S*\?/i.test(trimmed)) return text;
  const scheme = /^https?:\/\//i.test(trimmed);
  try {
    const parsed = new URL(scheme ? trimmed : `https://${trimmed}`);
    const names: string[] = [];
    parsed.searchParams.forEach((_, name) => { if (TRACKING_PARAMS.test(name) && !names.includes(name)) names.push(name); });
    if (!names.length) return text;
    names.forEach(name => parsed.searchParams.delete(name));
    const clean = parsed.toString();
    return scheme ? clean : clean.replace(/^https:\/\//, '');
  } catch { return text; }
};
// Every access failure the engine reports names YouTube access, a session, or age verification.
// Keep this list explicit so a reworded sentence elsewhere cannot be mistaken for an access problem.
const ACCESS_ERROR_PHRASES = ['youtube access', 'youtube session', 'age verif', 'signed-in session',
  ...YOUTUBE_ACCESS.filter(item => item.name).map(item => `${item.name.toLowerCase()} session`)];
const isYoutubeAccessError = (text: string|null|undefined) => {
  const lower = (text || '').toLowerCase();
  return ACCESS_ERROR_PHRASES.some(phrase => lower.includes(phrase));
};

// --- Set notes ----------------------------------------------------------------
const APPROXIMATE_NOTE = /Shazam timestamps are approximate\. Check track versions and transitions\.?/g;
const APPROXIMATE_MARK = 'Shazam timestamps are approximate.';
const SPOTIFY_PUBLIC_NOTE = 'Spotify supplied its public track list. Use Spotify authorization for guaranteed pagination.';
/** The part of a saved scan warning that still tells the user something the rest of the report does not. */
const cleanNote = (warning: string|null) => (warning || '').replace(APPROXIMATE_NOTE, '').replace(/^Partial scan\.\s*/, '').trim();
const scanNote = (warning: string|null) => {
  const text = cleanNote(warning);
  return text === SPOTIFY_PUBLIC_NOTE
    ? 'Spotify’s public page was read, which can cut a long playlist short. If entries are missing, connect Spotify and scan the link again.'
    : text;
};

// --- Plain errors ---------------------------------------------------------------
// Engine and network text the user cannot act on is replaced by a sentence; the original stays under Details.
const RESTART_SENTENCE = 'CrateLoom was updated while it was open. Restart CrateLoom to continue.';
const PLAIN_ERRORS: [RegExp, string][] = [
  [/decompressing data|incorrect header check|^ModuleNotFoundError|^ImportError/, RESTART_SENTENCE],
  [/valid HTTPS download URL|public HTTPS URL on port 443|private or unsupported address/, 'Paste the mix’s https:// link.'],
  [/single YouTube video or song link|public track link from the service['’]s Share menu/, 'Paste a single mix link from YouTube, or a playlist link.'],
  [/urlopen error|Name or service not known|nodename nor servname/, 'Couldn’t open this link. Check your connection.'],
  [/^The published tracklist is unavailable/, 'The tracklist couldn’t be read.'],
  [/^Install the ShazamIO scanner/, 'Audio recognition isn’t installed.'],
  [/^\[Errno 2\]/, 'The audio file can’t be found.'],
  [/ShazamIO stopped unexpectedly|^\w+(\.\w+)*(Error|Exception): /, 'Audio recognition stopped unexpectedly. Try again.'],
  [/A Shazam scan is already running/, 'Another audio scan is running; this one will start after it.'],
  [/database is locked/, 'CrateLoom was busy. Try again.'],
];
// Sentences the engine already words for the user; they are shown as they are.
const KEPT_ERRORS = /Spotify|SoundCloud|playlist|Live mixes|request queue is full|No published entries|more than 150|Scan stopped when CrateLoom closed/;
type PlainError = {text: string; detail: string|null};
function plainError(raw: string, detail?: string|null): PlainError {
  const text = raw.trim();
  const extra = detail && detail.trim() !== text ? detail.trim() : null;
  if (!text) return {text: '', detail: extra};
  if (!isYoutubeAccessError(text)) {
    const mapped = PLAIN_ERRORS.find(([pattern]) => pattern.test(text));
    if (mapped) return {text: mapped[1], detail: extra || (mapped[1] === text ? null : text)};
    if (!KEPT_ERRORS.test(text) && /Traceback|Errno|Error -\d/.test(text)) return {text: 'The scan stopped because of an internal error. Try again.', detail: extra || text};
  }
  return {text, detail: extra};
}
/** A plain error sentence, with the engine's own wording available on demand. */
function ErrorText({raw, detail}: {raw: string; detail?: string|null}) {
  const error = plainError(raw, detail);
  return <>
    <span className="set-error-text">{error.text}</span>
    {error.detail && <Disclosure className="set-error-details" label="Details"><code>{error.detail}</code></Disclosure>}
  </>;
}
const NOT_SCANNED_LEAD = 'The mix audio was not scanned.';
const sourceName = (watch: string) => {
  try {
    const host = new URL(watch).hostname;
    return host.includes('youtu') ? 'YouTube' : host.includes('spotify') ? 'Spotify' : host.includes('soundcloud') ? 'SoundCloud' : host.replace(/^www\./, '');
  } catch { return 'the source'; }
};
const linkAtTime = (watch: string, seconds: number) => {
  try { const target = new URL(watch); target.searchParams.set('t', String(Math.floor(seconds))); return target.toString(); }
  catch { return watch; }
};

/** Repeats an async poll, waiting for each run to finish before scheduling the next, so slow engine calls never overlap. */
function usePolling(active: boolean, delay: number, poll: (alive: () => boolean) => Promise<void>, key: string = '') {
  const latest = useRef(poll); latest.current = poll;
  useEffect(() => {
    if (!active) return;
    let stopped = false, timer = 0;
    const tick = async () => {
      try { await latest.current(() => !stopped); } catch { /* each poll reports its own failure */ }
      if (!stopped) timer = window.setTimeout(tick, delay);
    };
    timer = window.setTimeout(tick, delay);
    return () => { stopped = true; window.clearTimeout(timer); };
  }, [active, delay, key]);
}

function Notice({children, tone = 'error', onDismiss, action}: {children: ReactNode; tone?: 'error'|'status'; onDismiss?: () => void; action?: ReactNode}) {
  return <div className="set-notice" data-tone={tone} role={tone === 'error' ? 'alert' : 'status'}>
    {tone === 'error' ? <AlertTriangle size={15} aria-hidden="true"/> : <Info size={15} aria-hidden="true"/>}
    <div className="set-notice-body">{children}</div>
    {action}
    {onDismiss && <button type="button" className="set-notice-dismiss" aria-label="Dismiss message" title="Dismiss" onClick={onDismiss}><X size={14}/></button>}
  </div>;
}

function Artwork({path}: {path?: string|null}) {
  const [failed, setFailed] = useState<string|null>(null);
  return <div className="set-artwork cover">{path && failed !== path
    ? <img src={convertFileSrc(path)} alt="" loading="lazy" onError={() => setFailed(path)}/>
    : <Music2 size={22} aria-hidden="true"/>}</div>;
}

type RowStatus = {kind: 'done'|'busy'|'problem'|'idle'; label: string; detail?: string};
// Sources are looked for in the background as soon as a report is saved; this is where each entry stands before a playlist exists.
const SOURCE_SEARCH: Record<SourceSearch, RowStatus> = {
  queued: {kind: 'idle', label: 'Waiting to search'},
  searching: {kind: 'busy', label: 'Finding sources…'},
  ready: {kind: 'done', label: 'Source ready'},
  choose: {kind: 'idle', label: 'Choose a version'},
  none: {kind: 'idle', label: 'No source found yet'},
  failed: {kind: 'problem', label: 'Search failed'},
};
function rowStatus(row: SetTrack, hasPlaylist: boolean, checking: boolean): RowStatus {
  if (row.playback_track_id) return {kind: 'done', label: 'In library'};
  if (row.track_id) return {kind: 'problem', label: 'File missing', detail: 'The audio file for this track was moved or deleted. Relink it from the library.'};
  if (checking) return {kind: 'busy', label: 'Checking source'};
  if (row.download_state && (isActiveDownload(row.download_state) || isRetryableDownload(row.download_state))) return {
    kind: isActiveDownload(row.download_state) ? 'busy' : 'problem',
    label: downloadStateLabel({state: row.download_state, downloaded_bytes: row.downloaded_bytes, total_bytes: row.total_bytes}),
  };
  if (row.download_state === 'completed') return {kind: 'busy', label: 'Adding to library'};
  if (row.unknown) return row.evidence?.length
    ? {kind: 'idle', label: 'Unknown track', detail: `The tracklist lists this entry without a name.${hasPlaylist ? ' Add a file if you know the track.' : ''}`}
    : {kind: 'idle', label: 'Unidentified audio', detail: `Recognition found no match for this part of the mix.${hasPlaylist ? ' Add a file if you know the track.' : ''}`};
  if (!hasPlaylist) return (row.source_search && SOURCE_SEARCH[row.source_search]) || {kind: 'idle', label: 'Not in library'};
  if (row.source_options?.some(option => !option.reference_only)) return {kind: 'idle', label: 'Needs a source'};
  return row.error ? {kind: 'problem', label: 'No source found'} : {kind: 'idle', label: 'Needs a source'};
}
/** Entries a source search can act on: identified, not in the library, and with no download yet. */
const searchable = (row: SetTrack) => !row.track_id && !row.playback_track_id && !row.download_id && !row.unknown && (!!row.artist || row.at === null || !!row.listed_link);
/** Entries with a title of two or more words, no artist and no listed link: searched by title only, never chosen automatically. */
const titleOnly = (row: SetTrack) => !row.track_id && !row.playback_track_id && !row.download_id && !row.unknown && !row.artist && !row.listed_link && row.at !== null
  && (row.title || '').trim().split(/\s+/).filter(Boolean).length >= 2;

export function SetImportPanel({hidden, activeTrackId, playing, onPlay, onUpdated, onPlaylist, onRetry}: {
  hidden: boolean; onUpdated: () => Promise<void>; onPlaylist: (id: string) => void; onRetry: (id: string) => Promise<void>;
  activeTrackId: string|null; playing: boolean; onPlay: (id: string, order: string[]) => void;
}) {
  const [view, setView] = useState<View>('new');
  const viewRef = useRef(view); viewRef.current = view;
  const workspace = useRef<HTMLElement>(null);
  const mounted = useRef(true);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
  const refresh = useRef(onUpdated); refresh.current = onUpdated;

  // New scan form. Each way in keeps its own input, so switching between them loses nothing.
  const [mode, setMode] = useState<Mode>('link');
  const [url, setUrl] = useState('');
  const [file, setFile] = useState('');
  const [tracklist, setTracklist] = useState('');
  const [youtubeAccess, setYoutubeAccess] = useState(savedYoutubeAccess);
  // The access block opens from the link row, or by itself when YouTube refused access.
  const [accessOpen, setAccessOpen] = useState(false);
  const [configured, setConfigured] = useState(false);
  const [spotifyToken, setSpotifyToken] = useState('');
  const [spotifyConnected, setSpotifyConnected] = useState(false);
  const [soundcloudToken, setSoundcloudToken] = useState('');
  const [soundcloudConnected, setSoundcloudConnected] = useState(false);
  const [formError, setFormError] = useState('');
  // The engine's own wording of a failure, shown under Details while that failure is the one on screen.
  const [formDetail, setFormDetail] = useState<{text: string; detail: string}|null>(null);
  const [accessError, setAccessError] = useState('');
  // Each token field shows only its own service's error.
  const [tokenError, setTokenErrorState] = useState<{service: 'spotify'|'soundcloud'; text: string}|null>(null);
  // A token field opened from a report note ("Connect Spotify and scan again").
  const [reportToken, setReportToken] = useState(false);

  // The one scan this panel can run at a time, and what became of the last one.
  const [scanTask, setScanTask] = useState<Task|null>(null);
  const [startingScan, setStartingScan] = useState(false);
  const [scanNotice, setScanNotice] = useState('');
  const [finishedScan, setFinishedScan] = useState<Report|null>(null);
  const [finishedPartial, setFinishedPartial] = useState(false);
  const audioPhase = useRef(false);

  // History and the open report.
  const [saved, setSaved] = useState<SavedSet[]>([]);
  const [deleteId, setDeleteId] = useState<string|null>(null);
  const [historyError, setHistoryError] = useState('');
  const [report, setReport] = useState<Report|null>(null);
  const reportRef = useRef(report); reportRef.current = report;
  const [name, setName] = useState('');
  const [reportError, setReportError] = useState('');
  const [reportDetail, setReportDetail] = useState<{text: string; detail: string}|null>(null);
  const [reportNotice, setReportNotice] = useState('');
  // The access a paused scan will resume with. It starts as the one saved with the set and is stored when Resume is pressed.
  const [resumeAccess, setResumeAccess] = useState('');
  // A source search or local import started from a report. It belongs to that report only.
  const [job, setJob] = useState<{task: Task; reportId: string; kind: 'search'|'local'}|null>(null);
  const [rowTasks, setRowTasks] = useState<RowTask[]>([]);
  const rowTasksRef = useRef(rowTasks); rowTasksRef.current = rowTasks;
  const [queuedRows, setQueuedRows] = useState<string[]>([]);
  const queuedRef = useRef(queuedRows); queuedRef.current = queuedRows;
  const [rowErrors, setRowErrors] = useState<Record<string, string>>({});
  const [sourceRow, setSourceRow] = useState<string|null>(null);
  const [sourceUrl, setSourceUrl] = useState('');
  const [pending, setPending] = useState(false);
  const pendingRef = useRef(false);
  const reorderLock = useRef(false);
  const orderRevision = useRef(0);
  const [reordering, setReordering] = useState(false);
  const artworkRequested = useRef(new Set<string>());
  const soleSourceAttempted = useRef(new Set<string>());

  const scanActive = startingScan || !!scanTask;
  const jobHere = !!job && job.reportId === report?.id;
  const reportBusy = pending || jobHere;
  const youtubeLink = isYoutubeUrl(url);
  const access = YOUTUBE_ACCESS.find(item => item.value === youtubeAccess) || YOUTUBE_ACCESS[0];

  const refreshSets = async () => { const next = await call<SavedSet[]>('sets.list'); if (mounted.current) setSaved(next); };
  const historyScanning = saved.some(item => item.automatic && item.offset !== null);
  const failScan = (text: string, detail?: string|null) => {
    if (isYoutubeAccessError(text)) { setAccessError(text); setAccessOpen(true); setFormError(''); } else setFormError(text);
    setFormDetail(detail ? {text, detail} : null);
  };
  const failReport = (text: string, detail?: string|null) => { setReportError(text); setReportDetail(detail ? {text, detail} : null); };

  /** One user action at a time; a second click while the first is still running is ignored. */
  const run = async (action: () => Promise<void>, fail: (text: string) => void) => {
    if (pendingRef.current) return;
    pendingRef.current = true; setPending(true);
    try { await action(); }
    catch (e) { if (mounted.current) fail(message(e)); }
    finally { pendingRef.current = false; if (mounted.current) setPending(false); }
  };
  const showReport = (next: Report) => { setReport(next); setName(next.name); setSourceRow(null); setSourceUrl(''); setReportError(''); setReportNotice(''); setReportToken(false); };
  const reloadReport = async (id: string) => {
    const revision = orderRevision.current;
    const next = await call<Report>('sets.get', {id});
    // A reorder made while this request was in flight is newer than its answer.
    if (mounted.current && reportRef.current?.id === id && revision === orderRevision.current && !reorderLock.current) setReport(next);
  };

  useEffect(() => { workspace.current?.scrollTo(0, 0); }, [view]);
  useEffect(() => { if (view === 'report' && !report) setView('history'); }, [view, report]);
  useEffect(() => { setResumeAccess(YOUTUBE_ACCESS.some(item => item.value === report?.youtube_browser) ? report!.youtube_browser! : ''); }, [report?.id, report?.youtube_browser]);
  useEffect(() => {
    if (hidden) return;
    void call<{fingerprinting: boolean; spotify_connected?: boolean; soundcloud_connected?: boolean}>('sets.capabilities').then(c => {
      if (!mounted.current) return;
      setConfigured(c.fingerprinting);
      // Tokens can also come from the environment, so the engine is the one that knows.
      if (typeof c.spotify_connected === 'boolean') setSpotifyConnected(c.spotify_connected);
      if (typeof c.soundcloud_connected === 'boolean') setSoundcloudConnected(c.soundcloud_connected);
    }).catch(e => { if (mounted.current) setFormError(message(e)); });
    void refreshSets().catch(e => { if (mounted.current) setHistoryError(message(e)); });
    // Downloads and library edits may have happened while this panel was hidden.
    const open = reportRef.current?.id;
    if (open) void reloadReport(open).catch(() => {});
  }, [hidden]);
  usePolling(!hidden && view === 'history' && historyScanning, 4000, () => refreshSets());

  // --- Scanning ---------------------------------------------------------------
  /** Sends one scan request. Links are read for their tracklist; only an audio file is recognized from the start. */
  const sendScan = async (scanMode: Mode, typed: string) => {
    const link = scanMode === 'file' ? '' : stripTracking(typed).trim();
    if (scanMode !== 'file' && link !== typed) setUrl(link);
    setFormError(''); setFormDetail(null); setAccessError(''); setScanNotice('');
    audioPhase.current = scanMode === 'file';
    setStartingScan(true);
    try {
      setScanTask(await call<Task>('sets.scan', {
        url: link, path: scanMode === 'file' ? file : null, tracklist: scanMode === 'paste' ? tracklist : '',
        fingerprint: scanMode === 'file',
        youtube_browser: isYoutubeUrl(link) ? youtubeAccess || null : null,
      }));
    } finally { if (mounted.current) setStartingScan(false); }
  };
  const startScan = () => run(() => sendScan(mode, url), failScan);
  const cancelScan = async () => {
    if (!scanTask || scanTask.state === 'cancelling') return;
    try { const next = await call<Task>('sets.cancel_task', {task_id: scanTask.id}); if (mounted.current) setScanTask(current => current?.id === next.id ? next : current); }
    catch (e) { if (mounted.current) setFormError(message(e)); }
  };
  const finishScan = async (done: Task) => {
    if (done.state === 'cancelled') setScanNotice(done.result ? 'Scan cancelled. The entries and audio samples found so far were saved.' : 'Scan cancelled. Nothing had been found yet, so no set was saved.');
    else if (done.state === 'failed' || !done.result) failScan(done.error || 'The scan did not finish. Check the link or file, then try again.', done.error_detail);
    if (!done.result) return;
    let result = done.result;
    try { result = await call<Report>('sets.get', {id: result.id}); } catch { /* the task's own copy is still valid */ }
    if (!mounted.current) return;
    if (viewRef.current === 'new') {
      showReport(result); setView('report');
      if (done.state === 'completed') { setUrl(''); setFile(''); setTracklist(''); }
    } else if (reportRef.current?.id === result.id) {
      if (!reorderLock.current) setReport(result);
    } else { setFinishedScan(result); setFinishedPartial(done.state !== 'completed'); }
    await refreshSets().catch(() => {});
    if (result.playlist_id) await refresh.current().catch(() => {});
  };
  usePolling(!!scanTask, 800, async alive => {
    if (!scanTask) return;
    let next: Task;
    try { next = await call<Task>('metadata.job', {id: scanTask.id}); }
    catch (e) { if (alive()) { setScanTask(null); failScan(message(e)); } return; }
    if (!alive()) return;
    if (next.progress && ['bytes', 'seconds', 'samples'].includes(next.progress.unit)) audioPhase.current = true;
    if (next.state === 'running' || next.state === 'cancelling') { setScanTask(next); return; }
    setScanTask(null);
    await finishScan(next);
  }, scanTask?.id);

  // --- Report jobs ------------------------------------------------------------
  const startJob = (kind: 'search'|'local', method: string, params: {id: string; [field: string]: unknown}) => run(async () => {
    setReportError('');
    setJob({task: await call<Task>(method, params), reportId: params.id, kind});
  }, setReportError);
  usePolling(!!job, 800, async alive => {
    if (!job) return;
    let next: Task;
    try { next = await call<Task>('metadata.job', {id: job.task.id}); }
    catch (e) { if (alive()) { setJob(null); if (reportRef.current?.id === job.reportId) setReportError(message(e)); } return; }
    if (!alive() || next.state === 'running' || next.state === 'cancelling') return;
    setJob(null);
    if (reportRef.current?.id === job.reportId) {
      if (next.state === 'failed' || next.error) failReport(next.error || (job.kind === 'local' ? 'The file could not be added.' : 'The source search did not finish. Try again.'), next.error_detail);
      const source = next.result?.tracks.find(row => row.entry_id === sourceRow);
      if (source?.track_id || (source?.download_id && !source.error)) setSourceRow(null);
      await reloadReport(job.reportId).catch(() => {});
    }
    await refreshSets().catch(() => {});
    await refresh.current().catch(() => {});
  }, job?.task.id);

  // Row work (a chosen source, or artwork for the whole report) runs in the background per report.
  useEffect(() => {
    if (!report || hidden || artworkRequested.current.has(report.id)) return;
    const reportId = report.id;
    artworkRequested.current.add(reportId);
    void call<Task>('sets.artwork', {id: reportId})
      .then(next => { if (mounted.current) setRowTasks(tasks => [...tasks, {entry_id: 'artwork', report_id: reportId, task_id: next.id}]); })
      .catch(() => artworkRequested.current.delete(reportId));
  }, [report?.id, hidden]);
  usePolling(rowTasks.length > 0, 900, async () => {
    const tasks = rowTasksRef.current;
    const results = await Promise.allSettled(tasks.map(item => call<Task>('metadata.job', {id: item.task_id})));
    if (!mounted.current) return;
    const finished = tasks.filter((_, index) => { const result = results[index]; return result.status === 'rejected' || !['running', 'cancelling'].includes(result.value.state); });
    if (!finished.length) return;
    results.forEach((result, index) => {
      const item = tasks[index];
      const text = result.status === 'rejected' ? message(result.reason) : result.value.error;
      if (text && item.entry_id !== 'artwork') setRowErrors(errors => ({...errors, [rowKey(item.report_id, item.entry_id)]: text}));
    });
    setRowTasks(current => current.filter(item => !finished.some(done => done.task_id === item.task_id)));
    const open = reportRef.current?.id;
    try {
      if (open && finished.some(item => item.report_id === open)) await reloadReport(open);
      if (finished.some(item => item.entry_id !== 'artwork')) await refresh.current();
    } catch (e) { if (mounted.current) setReportError(message(e)); }
  });

  const chooseSource = async (row: SetTrack, link: string) => {
    const reportId = reportRef.current?.id;
    if (!reportId) return;
    const key = rowKey(reportId, row.entry_id);
    if (queuedRef.current.includes(key)) return;
    queuedRef.current = [...queuedRef.current, key];
    setQueuedRows(rows => [...rows, key]);
    setRowErrors(errors => ({...errors, [key]: ''}));
    try {
      const next = await call<Task>('sets.add_source', {id: reportId, entry_id: row.entry_id, url: link});
      if (mounted.current) setRowTasks(tasks => [...tasks, {entry_id: row.entry_id, report_id: reportId, task_id: next.id}]);
    } catch (e) { if (mounted.current) setRowErrors(errors => ({...errors, [key]: message(e)})); }
    finally { if (mounted.current) setQueuedRows(rows => rows.filter(item => item !== key)); }
  };
  // When a search leaves exactly one matching version, there is nothing to choose: start it.
  // An entry whose download already failed or was cancelled is left for the user to retry.
  useEffect(() => {
    if (!report?.playlist_id || hidden || reportBusy) return;
    for (const row of report.tracks) {
      const options = (row.source_options || []).filter(option => !option.reference_only && (option.compatible ?? option.recommended));
      const key = rowKey(report.id, row.entry_id);
      if (row.track_id || row.download_id || row.unknown || options.length !== 1 || soleSourceAttempted.current.has(key) || queuedRows.includes(key) || rowTasks.some(task => task.report_id === report.id && task.entry_id === row.entry_id)) continue;
      soleSourceAttempted.current.add(key);
      void chooseSource(row, options[0].url);
    }
  }, [report, hidden, reportBusy]);

  const reorder = async (entryId: string, targetId: string) => {
    const current = reportRef.current;
    if (!current || entryId === targetId || reorderLock.current) return;
    const ids = current.tracks.map(row => row.entry_id);
    const from = ids.indexOf(entryId), to = ids.indexOf(targetId);
    if (from < 0 || to < 0) return;
    ids.splice(to, 0, ids.splice(from, 1)[0]);
    reorderLock.current = true; orderRevision.current++; setReordering(true);
    setReport({...current, tracks: ids.map((id, position) => ({...current.tracks.find(row => row.entry_id === id)!, position}))});
    try {
      const next = await call<Report>('sets.reorder', {id: current.id, entry_ids: ids});
      if (mounted.current && reportRef.current?.id === current.id) setReport(next);
      await refresh.current().catch(() => {});
    } catch (e) {
      // Only undo the move in the report it was made in; the user may have opened another one since.
      if (mounted.current && reportRef.current?.id === current.id) { setReport(current); setReportError(`The new order could not be saved, so it was restored. ${message(e)}`); }
    } finally { reorderLock.current = false; if (mounted.current) setReordering(false); }
  };
  const trackReorder = useTrackReorder('set', (id, target) => void reorder(id, target), reordering);

  const downloading = report?.tracks.some(row => isActiveDownload(row.download_state)) || false;
  const scan = report?.scan && report.scan.offset !== null ? report.scan : null;
  const scanMoving = !!scan && (scan.automatic || scan.running || !!scan.cancelling);
  const rowWorkHere = !!report && (rowTasks.some(item => item.report_id === report.id) || queuedRows.some(key => key.startsWith(report.id + ':')));
  const sourceWorkHere = !!report && (rowTasks.some(item => item.report_id === report.id && item.entry_id !== 'artwork') || queuedRows.some(key => key.startsWith(report.id + ':')));
  // Sources are found in the background after a scan; the report keeps reading them in while that runs.
  const discovering = !!report?.discovery && report.discovery.pending + report.discovery.running > 0;
  const recognition = report?.recognition || null;
  // "Recognize audio too" runs as this report's audio scan; its results are merged into the published entries.
  const recognizing = !!scan && !!recognition && recognition.state !== 'none' && recognition.state !== 'complete';
  usePolling(!!report && !hidden && (scanMoving || downloading || jobHere || rowWorkHere || discovering || recognition?.state === 'running'), 1800, async alive => {
    const id = reportRef.current?.id;
    if (!id) return;
    try { await reloadReport(id); }
    catch (e) { if (alive() && reportRef.current?.id === id) setReportError(message(e)); }
  }, report?.id);

  const load = (id: string) => run(async () => {
    setHistoryError('');
    const next = await call<Report>('sets.get', {id});
    if (!mounted.current) return;
    setRowErrors(errors => Object.fromEntries(Object.entries(errors).filter(([key]) => !key.startsWith(id + ':'))));
    showReport(next); setView('report'); setScanNotice('');
    setFinishedScan(current => current?.id === id ? null : current);
  }, setHistoryError);
  const remove = (id: string) => run(async () => {
    await call('sets.delete', {id});
    if (!mounted.current) return;
    if (reportRef.current?.id === id) setReport(null);
    setFinishedScan(current => current?.id === id ? null : current);
    setDeleteId(null);
    await refreshSets();
  }, setHistoryError);
  const openLink = (link: string, fail: (text: string) => void = setReportError) => invoke<void>('open_reference', {url: link}).catch(e => fail(message(e)));
  const rescanWithAccess = (current: Report) => {
    setMode('link'); setUrl(current.watch || '');
    setAccessError(scanNote(current.warning)); setAccessOpen(true);
    setView('new');
  };
  /** Starts recognizing the audio of a set that was read from its published tracklist. */
  const recognizeAudio = (current: Report, browser: string|null) => run(async () => {
    setReportNotice(''); setReportError('');
    const next = await call<Report>('sets.recognize', {id: current.id, youtube_browser: browser});
    if (mounted.current && reportRef.current?.id === next.id) setReport(next);
  }, failReport);
  /** Scans a set's link again from New scan, for example after connecting Spotify. Runs inside the caller's action. */
  const rescan = async (link: string) => {
    setMode('link'); setUrl(link); setView('new');
    try { await sendScan('link', link); }
    catch (e) { if (mounted.current) failScan(message(e)); }
  };
  const connect = (service: 'spotify'|'soundcloud', token: string, then?: () => Promise<void>) => run(async () => {
    setTokenErrorState(null);
    const result = await call<{connected: boolean}>(service === 'spotify' ? 'sets.configure_spotify' : 'sets.configure_soundcloud', {token});
    if (!mounted.current) return;
    if (service === 'spotify') { setSpotifyConnected(result.connected); setSpotifyToken(''); }
    else { setSoundcloudConnected(result.connected); setSoundcloudToken(''); }
    if (result.connected && then) await then();
  }, text => setTokenErrorState(text ? {service, text} : null));
  // A token is asked for only where it is needed: beside a Spotify error, or on a report that would gain from it.
  const tokenField = (service: 'spotify'|'soundcloud', then?: {label: string; action: () => Promise<void>}) => {
    const spotify = service === 'spotify';
    const title = spotify ? 'Spotify' : 'SoundCloud', token = spotify ? spotifyToken : soundcloudToken, connected = spotify ? spotifyConnected : soundcloudConnected;
    const setToken = spotify ? setSpotifyToken : setSoundcloudToken;
    const submit = () => { if (token.trim() && !pendingRef.current) void connect(service, token.trim(), then?.action); };
    return <div className="set-token">
      <label htmlFor={`set-token-${service}`}>{title} access token <span className="set-token-state">{connected ? <><Check size={12} aria-hidden="true"/> Connected</> : 'Not connected'}</span></label>
      <div className="set-form-line">
        {/* Inside the scan form, Enter connects instead of starting a scan. */}
        <input id={`set-token-${service}`} type="password" autoComplete="off" placeholder={`Paste a ${title} access token`} value={token} onChange={e => setToken(e.target.value)}
          onKeyDown={e => { if (e.key === 'Enter') { e.preventDefault(); submit(); } }}/>
        <button type="button" disabled={pending || !token.trim()} onClick={submit}>{then ? then.label : connected ? 'Replace token' : 'Connect'}</button>
        {connected && <button type="button" disabled={pending} onClick={() => void connect(service, '')}>Disconnect</button>}
      </div>
      <p className="set-hint">{spotify
        ? 'Reads every entry of a long or private Spotify playlist. The token is kept until CrateLoom closes.'
        : 'Downloads the original file when a creator has enabled downloads on SoundCloud. Find sources again after connecting. The token is kept until CrateLoom closes.'}</p>
      {tokenError?.service === service && <Notice onDismiss={() => setTokenErrorState(null)}><ErrorText raw={tokenError.text}/></Notice>}
    </div>;
  };

  const scanLabel = audioPhase.current || (scanTask?.progress && ['bytes', 'seconds', 'samples'].includes(scanTask.progress.unit)) ? 'Scanning audio' : 'Reading tracklist';
  const canScan = mode === 'link' ? !!url.trim() : mode === 'file' ? !!file : !!tracklist.trim();
  // A pasted or filled-in link loses its share and tracking parameters at once; typing is left alone until the field is left.
  const typeLink = (value: string) => {
    // One character typed, or a run of characters deleted, counts as typing.
    let at = 0;
    while (at < value.length && value[at] === url[at]) at++;
    const typed = value.length === url.length + 1 ? url.slice(at) === value.slice(at + 1) : value.length < url.length && url.endsWith(value.slice(at));
    setUrl(typed ? value : stripTracking(value));
  };
  const accessShown = (mode === 'link' && youtubeLink && accessOpen) || !!accessError;
  const accessPanel = accessShown && <div id="set-access" className="set-access" data-error={!!accessError}>
    <label className="set-youtube-access">YouTube access
      <Select aria-label="YouTube access" aria-describedby="set-access-guide" value={youtubeAccess} disabled={scanActive} onChange={e => {
        setYoutubeAccess(e.target.value); setAccessError('');
        try { localStorage.setItem(ACCESS_STORAGE_KEY, e.target.value); } catch { /* the choice still applies to this session */ }
      }}>
        {YOUTUBE_ACCESS.map(item => <option key={item.value} value={item.value}>{item.label}</option>)}
      </Select>
    </label>
    {accessError && <Notice onDismiss={() => setAccessError('')}><ErrorText raw={accessError} detail={formDetail?.text === accessError ? formDetail.detail : null}/></Notice>}
    <ul id="set-access-guide" className="set-access-guide">
      <li>{access.guide}</li>
      {access.name && <li>Sign in to YouTube in {access.name} first and check that the mix plays there. The most recently used {access.name} profile is read.</li>}
      {access.name && <li>The session is read on this Mac, only while a mix is scanned, and is not stored. This choice is remembered for later scans.</li>}
    </ul>
  </div>;
  const inLibrary = report?.tracks.filter(row => row.playback_track_id).length || 0;
  const toSearch = report?.tracks.filter(searchable).length || 0;
  const toReview = report?.tracks.filter(titleOnly).length || 0;
  // After a playlist exists, entries that already offer versions to choose from need a choice, not another search.
  const unresolved = report?.tracks.filter(row => searchable(row) && !row.source_options?.some(option => !option.reference_only)) || [];
  const searchedBefore = unresolved.length > 0 && unresolved.every(row => !!row.error);
  const toDownload = report?.tracks.filter(row => !row.track_id && !isActiveDownload(row.download_state) && row.source_options?.some(option => option.recommended)).length || 0;
  const note = report ? scanNote(report.warning) : '';
  const approximate = !!report && (!!report.scan?.fast || (report.warning || '').includes(APPROXIMATE_MARK) || report.tracks.some(row => row.evidence?.includes('Fingerprint')));
  const playOrder = report ? [...new Set(report.tracks.flatMap(item => item.playback_track_id ? [item.playback_track_id] : []))] : [];
  // A set read from a YouTube mix's tracklist can have its audio recognized too, once, to confirm entries and add missed ones.
  const canRecognize = !!report && configured && !!report.watch && isYoutubeVideo(report.watch) && !scan
    && (!recognition || recognition.state === 'none' || recognition.state === 'failed')
    && report.tracks.length > 0 && !report.tracks.every(row => row.evidence?.includes('Fingerprint'));
  const notScanned = note.includes(NOT_SCANNED_LEAD);
  const spotifyNote = !!report && cleanNote(report.warning) === SPOTIFY_PUBLIC_NOTE;
  const sourcesReady = !report || report.playlist_id ? 0 : report.tracks.filter(row => row.source_search === 'ready' && searchable(row)).length;
  const heard = scan ? report?.provisional || [] : [];
  const spotifyFormError = /Spotify (authorization failed|could not share this playlist)/.test(formError);
  const soundcloudNeeded = !!report && report.tracks.some(row => !row.track_id && (row.source_options?.some(option => option.reference_only) || /^Track page found/.test(row.error || '')));

  // What the unfinished audio scan of the open report is doing, and what the user can do about it.
  const scanStatus = (current: ReportScan): {state: ScanState; label: string; stage: string} => {
    const now = Date.now() / 1000;
    const position = `Checked up to ${timestamp(current.offset || 0)}${current.duration ? ` of ${timestamp(current.duration)}` : ''}.`;
    if (current.running) return {state: 'running', label: recognizing ? `Recognizing audio · ${recognition!.confirmed} confirmed, ${recognition!.added} added` : 'Scanning audio', stage: current.progress?.stage || 'Recognizing the next section…'};
    if (current.automatic && current.retry_at > now + 15) return {state: 'waiting', label: 'Audio scan waiting', stage: /rate limit|limiting requests/i.test((recognizing ? recognition!.warning : report?.warning) || '')
      ? `Shazam is rate limiting; continues at ${clock(current.retry_at)}. ${position}`
      : `Waiting before the next attempt; continues at ${clock(current.retry_at)}. ${position}`};
    if (current.automatic) return {state: 'running', label: recognizing ? `Recognizing audio · ${recognition!.confirmed} confirmed, ${recognition!.added} added` : 'Scanning audio', stage: `Starting the next section… ${position}`};
    if (current.stalled) return {state: 'stalled', label: 'Audio scan stopped', stage: `Several attempts in a row made no progress. ${position} Resume to try again.`};
    return {state: 'paused', label: 'Audio scan paused', stage: `${position} Matches and audio samples are saved. Resume to continue from there.`};
  };

  return <section ref={workspace} className="set-workspace" aria-label="Import DJ Set" hidden={hidden}>
    <nav className="set-navigation" aria-label="DJ sets">
      <button type="button" aria-pressed={view === 'new'} onClick={() => setView('new')}>{scanActive ? <LoaderCircle size={16} className="spin" aria-hidden="true"/> : <Plus size={16} aria-hidden="true"/>}New scan</button>
      <button type="button" aria-pressed={view === 'history'} onClick={() => { setView('history'); setDeleteId(null); void refreshSets().catch(e => setHistoryError(message(e))); }}><History size={16} aria-hidden="true"/> History</button>
      {report && <button type="button" className="set-navigation-open" aria-pressed={view === 'report'} title={`Open set: ${report.name}`} onClick={() => setView('report')}><ListMusic size={16} aria-hidden="true"/><span>{report.name}</span></button>}
    </nav>

    {/* One strip answers "is a scan running, and where are its results" from every view. */}
    {view !== 'new' && scanActive && <div className="set-background-status"><LoaderCircle size={16} className="spin" aria-hidden="true"/><span role="status">Scan running · {scanTask?.progress?.stage || scanLabel}</span><button type="button" onClick={() => setView('new')}>View scan</button>{scanTask?.cancellable && <button type="button" disabled={scanTask.state === 'cancelling'} onClick={() => void cancelScan()}>{scanTask.state === 'cancelling' ? 'Stopping…' : 'Cancel scan'}</button>}</div>}
    {view !== 'new' && !scanActive && (formError || accessError) && <div className="set-background-status"><AlertTriangle size={16} aria-hidden="true"/><span role="status">The last scan could not finish.</span><button type="button" onClick={() => setView('new')}>See why</button></div>}
    {finishedScan && !scanActive && <div className="set-background-status" role="status"><Check size={16} aria-hidden="true"/><span>{finishedPartial ? 'Partial results saved' : 'Scan finished'} · {finishedScan.name}</span><button type="button" onClick={() => { showReport(finishedScan); setFinishedScan(null); setView('report'); }}>Open results</button><button type="button" className="set-notice-dismiss" aria-label="Dismiss scan result" title="Dismiss. The set stays in History." onClick={() => setFinishedScan(null)}><X size={14}/></button></div>}
    {scanNotice && !scanActive && <Notice tone="status" onDismiss={() => setScanNotice('')}>{scanNotice}</Notice>}

    {view === 'new' && <div className="set-new">
      <form className="set-form" data-scanning={scanActive} onSubmit={e => { e.preventDefault(); if (!scanActive && canScan) void startScan(); }}>
        <fieldset className="set-modes" disabled={scanActive}>
          <legend>Scan from</legend>
          <div className="set-mode-options">{([['link', 'A link'], ['file', 'An audio file'], ['paste', 'A pasted tracklist']] as [Mode, string][]).map(([value, label]) =>
            <label key={value} data-checked={mode === value}><input type="radio" name="set-mode" value={value} checked={mode === value} onChange={() => setMode(value)}/>{label}</label>)}</div>
        </fieldset>

        {mode === 'link' && <>
          <label htmlFor="set-url">Mix or playlist link</label>
          <div className="set-form-line">
            <input id="set-url" type="url" placeholder="YouTube mix, or a YouTube, Spotify or SoundCloud playlist" value={url} disabled={scanActive}
              onChange={e => typeLink(e.target.value)} onBlur={() => setUrl(value => stripTracking(value))}/>
            {youtubeLink && <button type="button" className="set-access-toggle" aria-label={`Access: ${access.name || 'Public'}, change YouTube access`} title={`YouTube access: ${access.label}`} aria-expanded={accessShown} aria-controls={accessShown ? 'set-access' : undefined} disabled={scanActive}
              onClick={() => { if (accessShown) { setAccessOpen(false); setAccessError(''); } else setAccessOpen(true); }}>Access: {access.name || 'Public'}<ChevronDown size={14} aria-hidden="true"/></button>}
            <button className="primary" disabled={scanActive || pending || !canScan}><Search size={15} aria-hidden="true"/> Scan</button>
          </div>
          {accessPanel}
          <p className="set-hint">{configured
            ? 'Reads the published tracklist; if there is none, recognizes the audio with Shazam.'
            : 'Reads the published tracklist. Audio recognition isn’t installed, so mixes without a tracklist can’t be scanned.'}</p>
        </>}

        {mode === 'file' && <>
          <span className="set-label">Mix audio file</span>
          <div className="set-form-line">
            <button type="button" disabled={scanActive || !configured} onClick={() => void (async () => {
              try { const path = await open({filters: [{name: 'Audio', extensions: AUDIO_EXTENSIONS}]}); if (typeof path === 'string') setFile(path); }
              catch (e) { setFormError(message(e)); }
            })()}><FolderOpen size={15} aria-hidden="true"/>{file ? 'Choose another file' : 'Choose audio file'}</button>
            {file && <span className="set-file-name" title={file}>{file.split(/[\\/]/).at(-1)}</span>}
            <button className="primary" disabled={scanActive || pending || !canScan || !configured}><Search size={15} aria-hidden="true"/> Scan</button>
          </div>
          <p className="set-hint">{configured
            ? 'The mix (up to 3 hours) is recognized with Shazam: short samples are sent online and positions are approximate. Your file is not changed.'
            : 'Audio recognition isn’t installed. Run scripts/setup-shazam-macos.sh, then restart CrateLoom.'}</p>
        </>}

        {mode === 'paste' && <>
          <label htmlFor="set-tracklist">Tracklist</label>
          <textarea id="set-tracklist" placeholder={'00:00 Artist – Track\n03:45 Artist – Track'} rows={5} maxLength={100000} value={tracklist} disabled={scanActive} onChange={e => setTracklist(e.target.value)}/>
          <p className="set-hint">One line per entry with its time, for example 00:00 Artist – Track; lines without a time are skipped. Entries are used as written.{configured ? ' Add the YouTube mix link to recognize its audio from the report too.' : ''}</p>
          <label htmlFor="set-paste-url">Mix link (optional)</label>
          <div className="set-form-line">
            <input id="set-paste-url" type="url" placeholder="Lets you jump to each entry in the mix" value={url} disabled={scanActive}
              onChange={e => typeLink(e.target.value)} onBlur={() => setUrl(value => stripTracking(value))}/>
            <button className="primary" disabled={scanActive || pending || !canScan}><Search size={15} aria-hidden="true"/> Scan</button>
          </div>
        </>}

        {mode !== 'link' && accessError && accessPanel}
        {formError && <Notice onDismiss={() => { setFormError(''); setFormDetail(null); }}><ErrorText raw={formError} detail={formDetail?.text === formError ? formDetail.detail : null}/></Notice>}
        {spotifyFormError && !scanActive && tokenField('spotify', {label: spotifyConnected ? 'Replace and scan again' : 'Connect and scan again', action: () => rescan(url)})}
      </form>

      {scanActive && <div className="set-progress-stage"><ScanProgress label={scanLabel} progress={scanTask?.progress} cancelling={scanTask?.state === 'cancelling'} onCancel={scanTask?.cancellable ? () => void cancelScan() : undefined}>
        <p className="scan-progress-detail">You can keep working. Results open here when the scan finishes, and are saved in History.</p>
      </ScanProgress></div>}
    </div>}

    {view === 'history' && <section className="set-history-panel" aria-label="Scan history">
      <h2>Scan history</h2>
      {historyError && <Notice onDismiss={() => setHistoryError('')}><ErrorText raw={historyError}/></Notice>}
      {saved.length ? <ul className="set-history-list">{saved.map(item => {
        const state = item.offset !== null ? item.automatic ? 'Audio scan in progress' : 'Audio scan paused'
          : item.playlist_id ? `Playlist created · ${item.imported} of ${item.entries} in library${item.with_download ? ` · ${plural(item.with_download, 'download')} not finished` : ''}`
          : 'No playlist yet';
        return <li key={item.id}>
          <button className="set-history-open" disabled={pending} title="Open this set" onClick={() => void load(item.id)}>
            <strong>{item.name}</strong>
            <small><span className="set-history-state">{item.offset !== null && item.automatic ? <LoaderCircle size={12} className="spin" aria-hidden="true"/> : item.offset !== null ? <Pause size={12} aria-hidden="true"/> : item.playlist_id ? <Check size={12} aria-hidden="true"/> : null}{state}</span>
              {` · ${plural(item.entries, 'entry', 'entries')}`}
              {item.created_at ? ` · ${new Date(item.created_at * 1000).toLocaleDateString(undefined, {day: 'numeric', month: 'short', year: 'numeric'})}` : ''}</small>
          </button>
          {deleteId === item.id ? <div className="set-history-confirm" role="group" aria-label={`Delete ${item.name}?`}>
            <small>Delete this scan report? Its playlist and tracks stay in your library.</small>
            <button type="button" disabled={pending} onClick={() => void remove(item.id)}>Delete report</button>
            <button type="button" autoFocus onClick={() => setDeleteId(null)}>Keep</button>
          </div> : <button className="set-history-delete" aria-label={`Delete ${item.name}`} title="Delete this scan report" disabled={pending}
            onClick={() => setDeleteId(item.id)}><Trash2 size={16}/></button>}
        </li>;
      })}</ul> : <p>No scans yet. Start one under New scan with a mix link, an audio file or a pasted tracklist.</p>}
    </section>}

    {view === 'report' && report && <div className="set-report">
      <div className="set-report-head">
        <div><h2>{report.name}</h2><small>
          {plural(report.tracks.length, 'entry', 'entries')} · {inLibrary} in library · {report.playlist_id ? 'Playlist created' : 'No playlist yet'}
          {approximate && <span title="Positions come from audio recognition and can be off. Check versions and transitions."> · Approximate positions</span>}
          {report.scan?.offset === null && report.scan.duration !== null && !recognition?.confirmed && !recognition?.added && ` · Audio scan complete (${timestamp(report.scan.duration)})`}
          {recognition?.state === 'complete' && ` · Audio recognized: ${recognition.confirmed} confirmed, ${recognition.added} added`}
        </small>
          <div className="set-report-tools">
            {report.watch && <button type="button" className="set-source-link" onClick={() => void openLink(report.watch!)}>Open on {sourceName(report.watch)}<ArrowUpRight size={12} aria-hidden="true"/></button>}
            {canRecognize && !notScanned && <button type="button" className="set-recognize" disabled={reportBusy} title="Recognizes the mix audio with Shazam to confirm the entries and add any the tracklist missed. Runs in the background."
              onClick={() => void recognizeAudio(report, report.youtube_browser ?? null)}><AudioLines size={15} aria-hidden="true"/> Recognize audio too</button>}
          </div>
        </div>
        {report.playlist_id ? <div className="set-report-actions">
          <button disabled={reportBusy} onClick={() => onPlaylist(report.playlist_id!)}><ListMusic size={15} aria-hidden="true"/> Open playlist</button>
          {unresolved.length > 0 && <button disabled={reportBusy} title="Searches for a download for each entry that is not in your library and has no source yet, and starts the downloads it is sure about."
            onClick={() => void startJob('search', 'sets.prepare', {id: report.id})}><Search size={15} aria-hidden="true"/> {searchedBefore ? 'Search again' : 'Find sources'} for {plural(unresolved.length, 'entry', 'entries')}</button>}
          {toDownload > 0 && <button className="primary" disabled={reportBusy || sourceWorkHere} title="Downloads the recommended source of every entry that has one."
            onClick={() => void run(async () => {
              const reportId = report.id, wanted = toDownload;
              setReportNotice('');
              const tasks = await call<(Task & {entry_id: string})[]>('sets.apply_recommended', {id: reportId});
              if (!mounted.current) return;
              setRowTasks(current => [...current, ...tasks.map(item => ({entry_id: item.entry_id, report_id: reportId, task_id: item.id}))]);
              // The engine starts fewer than asked when its queue is full; the rest keep their recommendation.
              if (tasks.length < wanted && reportRef.current?.id === reportId) setReportNotice(tasks.length
                ? `Started ${tasks.length} of ${wanted} downloads because the queue is full. The other entries keep their recommended source; download them when these finish.`
                : 'No download was started because the queue is full. Try again when the current downloads finish.');
            }, setReportError)}><Download size={15} aria-hidden="true"/> Download {toDownload} recommended</button>}
        </div> : <form className="set-playlist-create" onSubmit={e => { e.preventDefault(); if (!reportBusy && name.trim()) void startJob('search', 'sets.prepare', {id: report.id, name}); }}>
          <label htmlFor="set-playlist-name">Playlist name</label>
          <div className="set-form-line">
            <input id="set-playlist-name" aria-label="Set playlist name" maxLength={100} value={name} disabled={reportBusy} onChange={e => setName(e.target.value)}/>
            <button className="primary" disabled={reportBusy || !name.trim()}>{toSearch > 0 ? 'Create playlist and find tracks' : 'Create playlist'}</button>
            {(discovering || (toSearch > 0 && sourcesReady > 0)) && <span className="set-sources-ready" role="status">{discovering && <LoaderCircle size={13} className="spin" aria-hidden="true"/>}{plural(sourcesReady, 'source')} ready{discovering ? ' · still searching' : ''}</span>}
          </div>
          <small>{inLibrary > 0 ? `Adds the ${plural(inLibrary, 'entry', 'entries')} already in your library to a new playlist` : 'Creates a new playlist'}{toSearch > 0 ? `, then searches for and downloads the ${inLibrary > 0 ? 'other ' : ''}${plural(toSearch, 'identified entry', 'identified entries')}. Entries with several possible versions wait for your choice.` : '.'}{toReview > 0 ? ` ${plural(toReview, 'entry', 'entries')} without an artist ${toReview === 1 ? 'is' : 'are'} searched by title only; choose a version for ${toReview === 1 ? 'it' : 'each'} after the playlist is created.` : ''}</small>
        </form>}
      </div>
      {jobHere && <Notice tone="status">{job!.kind === 'local' ? 'Adding the file to your library…' : 'Searching for sources and starting downloads… Entries update as results arrive.'}</Notice>}
      {reportError && <Notice onDismiss={() => { setReportError(''); setReportDetail(null); }}><ErrorText raw={reportError} detail={reportDetail?.text === reportError ? reportDetail.detail : null}/></Notice>}
      {reportNotice && <Notice tone="status" onDismiss={() => setReportNotice('')}>{reportNotice}</Notice>}
      {note && !scan && <Notice tone="status" action={notScanned && canRecognize
        ? <button type="button" disabled={reportBusy} onClick={() => void recognizeAudio(report, resumeAccess || null)}><AudioLines size={14} aria-hidden="true"/> Recognize audio too</button>
        : spotifyNote && report.watch
          ? spotifyConnected
            ? <button type="button" disabled={pending} onClick={() => void run(() => rescan(report.watch!), failScan)}>Scan again</button>
            : <button type="button" aria-expanded={reportToken} onClick={() => setReportToken(open => !open)}>Connect Spotify and scan again</button>
          : isYoutubeAccessError(note) && report.watch ? <button type="button" onClick={() => rescanWithAccess(report)}>Change YouTube access and rescan</button> : undefined}>
        {notScanned
          ? <>{note} The entries below come from the tracklist.
            {canRecognize && <label className="set-notice-access">YouTube access <Select aria-label="YouTube access for this set" value={resumeAccess} disabled={reportBusy} onChange={e => setResumeAccess(e.target.value)}>
              {YOUTUBE_ACCESS.map(item => <option key={item.value} value={item.value}>{item.label}</option>)}
            </Select></label>}</>
          : <><strong>Note from scanning this {report.watch && /playlist/.test(report.watch) ? 'playlist' : 'mix'}: </strong><ErrorText raw={note}/></>}</Notice>}
      {spotifyNote && reportToken && !spotifyConnected && report.watch && !scan && tokenField('spotify', {label: 'Connect and scan again', action: () => rescan(report.watch!)})}
      {recognition?.state === 'failed' && !scan && <Notice tone="status">Audio recognition stopped before the end of the mix.{recognition.warning && <> <ErrorText raw={recognition.warning}/></>}{canRecognize ? ' Recognize audio too starts it again.' : ''}</Notice>}
      {soundcloudNeeded && <Disclosure className="set-connect" label={soundcloudConnected ? 'SoundCloud connected' : 'Connect SoundCloud'}>{tokenField('soundcloud')}</Disclosure>}
      {scan && (() => {
        const status = scanStatus(scan);
        const moving = scan.automatic || scan.running;
        const youtubeSet = !!report.watch && isYoutubeUrl(report.watch);
        const saved = YOUTUBE_ACCESS.find(item => item.value === (report.youtube_browser || '')) || YOUTUBE_ACCESS[0];
        const chosen = YOUTUBE_ACCESS.find(item => item.value === resumeAccess) || YOUTUBE_ACCESS[0];
        // A recognition run keeps its own warning; the set's note is about the scan that published the entries.
        const scanText = recognizing ? recognition!.warning || '' : note;
        return <div className="set-scan-status"><ScanProgress label={status.label} state={status.state} cancelling={scan.cancelling}
          progress={scan.progress && (scan.running || scan.progress.resume_at) ? scan.progress : {stage: status.stage, completed: scan.offset || 0, total: scan.duration, unit: 'steps'}}
          cancelLabel="Pause scan" onCancel={moving ? () => void run(async () => { const next = await call<Report>('sets.cancel_scan', {id: report.id}); if (mounted.current && reportRef.current?.id === next.id) setReport(next); }, setReportError) : undefined}>
          {/* Engine text (an OSError, a tool's output) becomes one sentence; the raw text sits under Details. */}
          {scanText && !(status.state === 'waiting' && /rate limit|limiting requests/i.test(scanText)) && <div className="scan-progress-detail">{isYoutubeAccessError(scanText) && !moving ? <ErrorText raw={scanText}/> : <>Last attempt: <ErrorText raw={scanText}/></>}</div>}
          {recognizing && status.state !== 'running' && <p className="scan-progress-detail">{recognition!.confirmed} confirmed, {recognition!.added} added so far</p>}
          {scan.samples > 0 && !scan.running && <p className="scan-progress-detail">{plural(scan.samples, 'audio sample')} saved{scan.pending_matches ? ` · ${plural(scan.pending_matches, 'match', 'matches')} waiting to be placed` : ''}</p>}
          {moving && <p className="scan-progress-detail">Continues in the background; {recognizing ? 'confirmed and new entries appear below.' : 'entries appear below as they are found.'}{youtubeSet && ` Uses ${saved.name ? `the ${saved.name} session` : 'public YouTube access'} (pause to change).`}</p>}
          {/* Resume comes first so it stays in view in the compact strip; the access choice beneath it applies to it. */}
          {!moving && <button type="button" className="scan-progress-action" disabled={reportBusy || !configured} onClick={() => void run(async () => {
            setScanNotice('');
            const next = await call<Report>('sets.auto_scan', {id: report.id, enabled: true, ...(youtubeSet ? {youtube_browser: resumeAccess || null} : {})});
            if (mounted.current && reportRef.current?.id === next.id) setReport(next);
          }, setReportError)}>Resume audio scan</button>}
          {!moving && youtubeSet && <div className="set-resume-access">
            <label>Resume with <Select aria-label="YouTube access for this set" value={resumeAccess} disabled={reportBusy} onChange={e => setResumeAccess(e.target.value)}>
              {YOUTUBE_ACCESS.map(item => <option key={item.value} value={item.value}>{item.label}{item.value === saved.value ? ' (saved with this set)' : ''}</option>)}
            </Select></label>
            <p className="scan-progress-detail">{chosen.guide}{chosen.value !== saved.value && ' The new choice is saved with this set when you resume.'}</p>
          </div>}
          {!moving && !configured && <p className="scan-progress-detail">Audio recognition is not installed, so this scan cannot continue.</p>}
        </ScanProgress></div>;
      })()}
      {heard.length > 0 && <section className="set-provisional" aria-label="Heard so far">
        <h3>Heard so far <span>(placed when the scan finishes)</span></h3>
        <ol>{heard.map(item => <li key={`${item.at}:${item.artist}:${item.title}`}>
          <span className="time">{timestamp(item.at)}</span>
          <span>{item.artist ? `${item.artist} – ${item.title}` : item.title}</span>
          {item.samples <= 1 && <small>Heard once</small>}
        </li>)}</ol>
      </section>}

      <ol className="set-tracklist" aria-label="Set tracklist">
        {report.tracks.map((row, index) => {
          const key = rowKey(report.id, row.entry_id);
          const checking = queuedRows.includes(key) || rowTasks.some(item => item.report_id === report.id && item.entry_id === row.entry_id);
          const trackId = row.playback_track_id;
          const current = !!trackId && trackId === activeTrackId;
          const isPlaying = current && playing;
          const retryable = !!row.download_id && isRetryableDownload(row.download_state);
          const canChooseSource = !!report.playlist_id && !row.track_id && !trackId && (!row.download_id || retryable);
          const status = rowStatus(row, !!report.playlist_id, checking);
          const options = canChooseSource ? row.source_options || [] : [];
          const recommended = options.find(option => option.recommended && !option.reference_only);
          const others = options.filter(option => option !== recommended);
          const query = encodeURIComponent([row.artist, row.title].filter(Boolean).join(' '));
          const pages = [...new Map((row.links || []).map(link => [link.label, link])).values()];
          const searches = [
            {label: 'SoundCloud', url: `https://soundcloud.com/search/sounds?q=${query}`},
            {label: 'YouTube Music', url: `https://music.youtube.com/search?q=${query}`},
            {label: 'Spotify', url: `https://open.spotify.com/search/${query}`},
          ].filter(search => !pages.some(link => link.label === search.label));
          const watch = report.watch;
          const at = row.at;
          const order = String(index + 1).padStart(2, '0');
          const problem = trackId ? null : row.error === 'Review the suggested versions below.' && options.length ? 'Several versions were found. Choose the one that matches this entry.' : row.error;
          const sourceChoice = (option: SourceOption) => <div className={`set-source-choice ${option.recommended ? 'is-recommended' : ''}`}>
            <button type="button" className="set-source-title" title={`Open ${option.title} in your browser`} onClick={() => void openLink(option.url)}><ServiceIcon name={option.url.includes('soundcloud.com') ? 'SoundCloud' : 'YouTube'}/><span>{option.title}
              {option.recommended && <small><Check size={11} aria-hidden="true"/> Recommended{option.recommendation_reason ? ` · ${option.recommendation_reason}` : ''}</small>}
              {!option.recommended && option.compatible === false && !option.reference_only && <small>Different version or length</small>}</span><ArrowUpRight size={12} aria-hidden="true"/></button>
            <span className="time source-duration">{option.duration ? timestamp(option.duration) : ''}</span>
            {!option.reference_only ? <button type="button" className="use-source" disabled={checking || reportBusy} onClick={() => void chooseSource(row, option.url)}>{checking ? <><LoaderCircle size={13} className="spin" aria-hidden="true"/> Checking</> : 'Use source'}</button>
              : <small title="This page can be opened, but CrateLoom cannot download from it.">Reference only</small>}
          </div>;
          return <li key={row.entry_id} className={`${current ? 'set-track-current' : ''} ${trackReorder.target === row.entry_id ? 'drop-target' : ''}`} data-reorder-group="set" data-reorder-id={row.entry_id}>
            <div className="set-row-order">
              <button type="button" className="set-move" title="Move up" aria-label={`Move ${row.title} up`} disabled={reordering || index === 0} onClick={() => void reorder(row.entry_id, report.tracks[index - 1].entry_id)}><ChevronUp size={13}/></button>
              <button className="reorder-handle" type="button" {...trackReorder.handle(row.entry_id)} disabled={reordering} aria-label={`Reorder ${row.title}. Use up or down arrow keys.`} title="Drag to reorder, or press the Up and Down keys" onKeyDown={e => {
                if (e.key !== 'ArrowUp' && e.key !== 'ArrowDown') return;
                e.preventDefault();
                const next = report.tracks[index + (e.key === 'ArrowUp' ? -1 : 1)];
                if (next) void reorder(row.entry_id, next.entry_id);
              }}><GripVertical size={16}/></button>
              <button type="button" className="set-move" title="Move down" aria-label={`Move ${row.title} down`} disabled={reordering || index === report.tracks.length - 1} onClick={() => void reorder(row.entry_id, report.tracks[index + 1].entry_id)}><ChevronDown size={13}/></button>
            </div>
            {watch && at != null ? <button type="button" className="set-time" title={`Open the mix at ${timestamp(at)}`} aria-label={`Entry ${index + 1}. Open the mix at ${timestamp(at)}`} onClick={() => void openLink(linkAtTime(watch, at))}>{order}<small>{timestamp(at)}</small></button> :
              <span className="set-time">{order}{at != null && <small>{timestamp(at)}</small>}</span>}
            <Artwork path={row.artwork_path}/>
            <div className="set-track-info">
              <strong>{row.title}</strong>
              {row.artist && <small>{row.artist}</small>}
              {row.version_warning && <small>{row.version_warning}</small>}
              {!!row.recognized_other?.length && <small>Also heard during this entry: {row.recognized_other.map(other => [other.artist, other.title].filter(Boolean).join(' – ')).join('; ')}</small>}
              {row.needs_review && !row.unknown && !trackId && <small>Uncertain match. Check the title and artist before downloading.</small>}
              {status.detail && <small>{status.detail}</small>}
              {problem && <DownloadError error={problem} announce={false}/>}
              {rowErrors[key] && <Notice onDismiss={() => setRowErrors(errors => ({...errors, [key]: ''}))}><ErrorText raw={rowErrors[key]}/></Notice>}
              {row.download_state === 'downloading' && <DownloadProgress title={row.title} received={row.downloaded_bytes || 0} total={row.total_bytes ?? null}/>}
              {recommended && sourceChoice(recommended)}
              {others.length > 0 && <Disclosure label={recommended ? `Other sources · ${others.length}` : `Review sources · ${others.length}`}>
                <ul className="set-source-options">{others.map(option => <li key={option.url}>{sourceChoice(option)}</li>)}</ul>
              </Disclosure>}
              {!trackId && !row.track_id && !row.unknown && <Disclosure label="Look up this track" className="set-row-links"><div className="set-links">
                {pages.map(link => link.label === 'Creator download'
                  ? <button type="button" key={link.label} title={link.url} onClick={() => void navigator.clipboard.writeText(link.url).catch(e => setRowErrors(errors => ({...errors, [key]: message(e)})))}>Copy creator download link</button>
                  : <button type="button" key={link.label} title={`Open this track on ${link.label}`} onClick={() => void openLink(link.url)}><ServiceIcon name={link.label}/>{link.label}<ArrowUpRight size={12} aria-hidden="true"/></button>)}
                {searches.map(link => <button type="button" key={link.label} title={`Search for this track on ${link.label}`} onClick={() => void openLink(link.url)}><ServiceIcon name={link.label}/>Search {link.label}<ArrowUpRight size={12} aria-hidden="true"/></button>)}
              </div></Disclosure>}
              {sourceRow === row.entry_id && canChooseSource && <form className="set-add-source" onSubmit={e => { e.preventDefault(); if (sourceUrl.trim()) void chooseSource(row, sourceUrl.trim()); }}>
                {!row.unknown && <>
                  <label htmlFor={`set-source-${row.entry_id}`}>Track link</label>
                  <div className="set-form-line">
                    <input id={`set-source-${row.entry_id}`} type="url" placeholder="YouTube, SoundCloud or a direct audio file link" value={sourceUrl} disabled={reportBusy} onChange={e => setSourceUrl(e.target.value)}/>
                    <button disabled={reportBusy || checking || !sourceUrl.trim()}><Download size={14} aria-hidden="true"/> Download</button>
                  </div>
                </>}
                <div className="set-form-line">
                  <button type="button" disabled={reportBusy} onClick={() => void (async () => {
                    try {
                      const path = await open({filters: [{name: 'Audio', extensions: AUDIO_EXTENSIONS}]});
                      if (typeof path === 'string') await startJob('local', 'sets.add_local', {id: report.id, entry_id: row.entry_id, path});
                    } catch (e) { setRowErrors(errors => ({...errors, [key]: message(e)})); }
                  })()}><FolderOpen size={14} aria-hidden="true"/> Use a file from this Mac</button>
                </div>
              </form>}
            </div>
            <div className="set-track-state">
              <div className="set-state-line"><span className="set-status" data-kind={status.kind} role={checking ? 'status' : undefined}>
                {status.kind === 'done' ? <Check size={13} aria-hidden="true"/> : status.kind === 'busy' ? <LoaderCircle size={13} className="spin" aria-hidden="true"/> : status.kind === 'problem' ? <AlertTriangle size={13} aria-hidden="true"/> : null}{status.label}</span>
              {row.duration ? <span className="time set-duration">{timestamp(row.duration)}</span> : null}</div>
              <div className="set-state-actions">{trackId && <button type="button" className="set-track-play" aria-label={`${isPlaying ? 'Pause' : 'Play'} ${row.title}`} title={isPlaying ? 'Pause' : 'Play this track'}
                onClick={() => onPlay(trackId, playOrder)}>{isPlaying ? <Pause size={14} aria-hidden="true"/> : <Play size={14} aria-hidden="true"/>}{isPlaying ? 'Pause' : 'Play'}</button>}
              {row.download_id && retryable && <button type="button" disabled={reportBusy} title="Start this download again" onClick={() => void run(async () => { await onRetry(row.download_id!); await reloadReport(report.id); }, text => setRowErrors(errors => ({...errors, [key]: text})))}><RotateCcw size={13} aria-hidden="true"/> Retry</button>}
              {canChooseSource && <button type="button" disabled={reportBusy || checking} aria-expanded={sourceRow === row.entry_id} onClick={() => { setSourceRow(sourceRow === row.entry_id ? null : row.entry_id); setSourceUrl(''); }}>{sourceRow === row.entry_id ? 'Close' : row.unknown ? 'Add file' : 'Add source'}</button>}</div>
            </div>
          </li>;
        })}
      </ol>
    </div>}
  </section>;
}
