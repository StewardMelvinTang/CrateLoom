import React, {useCallback, useEffect, useRef, useState} from 'react';
import {createRoot, type Root} from 'react-dom/client';
import {convertFileSrc} from '@tauri-apps/api/core';
import {open, save} from '@tauri-apps/plugin-dialog';
import {Activity, ArrowLeft, Check, Download, FolderOpen, ListMusic, LoaderCircle, Plus, Search, X} from 'lucide-react';
import './style.css';
import './workspace.css';
import {call, message} from './engine';
import {Sidebar} from './Sidebar';
import {Titlebar} from './Titlebar';
import {PanelPresence, transitionWorkspace} from './PanelMotion';
import {Disclosure} from './Disclosure';
import {Select} from './Select';
import {Menu} from './Menu';
import {ErrorBoundary} from './ErrorBoundary';
import {useTrackReorder} from './useTrackReorder';
import {MetadataEditor} from './MetadataEditor';
import {useMetadataBatch} from './MetadataBatch';
import {TagWriteReview} from './TagWriteReview';
import {DiscoveryPanel, type AcquisitionJob, type AcquisitionReview} from './DiscoveryPanel';
import {Downloads, activeDownloads} from './Downloads';
import {SetImportPanel} from './SetImportPanel';
import {LibraryView, type ExportSelector, type LibraryFilter} from './LibraryView';
import {CueEditor, type EditorActions, type Loop} from './CueEditor';
import {PlayerBar, type RepeatMode} from './PlayerBar';
import {PlaybackClock} from './waveform-envelope';
import {playhead} from './playhead';
import {AUDIO_EXTENSIONS, needsAnalysis, plural, typingTarget, type AnalysisData, type Cue, type GridState, type ImportItem, type ImportJob, type Job, type Playlist, type Section, type Track} from './types';

type Workspace = 'library' | 'find' | 'sets';
type Notice = {id: number; text: string; tone: 'info' | 'error'};
type ExportTask = {id: string; state: string; error: string | null; result: null | {xml: string; track_count: number; grid_count: number}};
type ExportStatus = {state: 'running' | 'done' | 'failed'; text: string; detail?: string};

const workspaceNames: Record<Workspace, string> = {library: 'library', find: 'Find Music', sets: 'Import DJ Set'};
const importActive = (job: ImportJob | null) => !!job && ['scanning', 'running'].includes(job.state);
const jobActive = (job: Job | null) => !!job && ['running', 'queued'].includes(job.state);
const wait = (ms: number) => new Promise(resolve => window.setTimeout(resolve, ms));
/** An export that has not finished after this long is reported instead of being polled forever. */
const EXPORT_TIMEOUT_MS = 30 * 60 * 1000;

function App() {
  const [sidebarCollapsed, setSidebarCollapsed] = useState(() => localStorage.getItem('sidebarCollapsed') === 'true');
  const [theme, setTheme] = useState(localStorage.getItem('theme') || 'system');
  useEffect(() => { document.documentElement.dataset.theme = theme; localStorage.setItem('theme', theme); }, [theme]);

  // Messages. Errors stay until dismissed; confirmations clear themselves.
  const [notices, setNotices] = useState<Notice[]>([]);
  const noticeId = useRef(0);
  const dismissNotice = useCallback((id: number) => setNotices(items => items.filter(item => item.id !== id)), []);
  const notify = useCallback((text: string, tone: Notice['tone'] = 'error') => {
    const id = ++noticeId.current;
    setNotices(items => [...items.filter(item => item.text !== text), {id, text, tone}].slice(-3));
    if (tone === 'info') window.setTimeout(() => dismissNotice(id), 8000);
  }, [dismissNotice]);
  const fail = useCallback((error: unknown, prefix = '') => notify(prefix + message(error)), [notify]);

  // Library
  const [tracks, setTracks] = useState<Track[]>([]);
  const [libraryLoaded, setLibraryLoaded] = useState(false);
  const [playlists, setPlaylists] = useState<Playlist[]>([]);
  const [playlistId, setPlaylistId] = useState<string | null>(null);
  const [newPlaylistName, setNewPlaylistName] = useState('');
  const [renaming, setRenaming] = useState(false);
  const [selectedIds, setSelectedIds] = useState<string[]>([]);
  const [libraryFilter, setLibraryFilter] = useState<LibraryFilter>('all');
  const [query, setQuery] = useState('');
  const [metadataIds, setMetadataIds] = useState<string[] | null>(null);
  const [tagWriteIds, setTagWriteIds] = useState<string[] | null>(null);
  const [ordering, setOrdering] = useState(false);
  const orderingLock = useRef(false), orderRevision = useRef(0);
  const currentPlaylist = playlists.find(playlist => playlist.id === playlistId) || null;
  const search = useRef<HTMLInputElement>(null);

  // Navigation. The Cue Editor opens over the current workspace and returns to it.
  const [workspace, setWorkspace] = useState<Workspace>('library');
  const [editor, setEditor] = useState(false);
  const editorRef = useRef(editor); editorRef.current = editor;
  const [downloadsOpen, setDownloadsOpen] = useState(false);

  // Player
  const [active, setActive] = useState<Track | null>(null);
  const activeRef = useRef(active); activeRef.current = active;
  const activeId = useRef<string | null>(null);
  const audio = useRef<HTMLAudioElement>(null);
  const [playing, setPlaying] = useState(false);
  const [playerLoading, setPlayerLoading] = useState(false);
  const [shuffle, setShuffle] = useState(false);
  const [repeat, setRepeat] = useState<RepeatMode>('off');
  const [volume, setVolume] = useState(() => {
    const stored = localStorage.getItem('playerVolume');
    return stored !== null && Number.isFinite(Number(stored)) ? Math.min(1, Math.max(0, Number(stored))) : 1;
  });
  const [muted, setMuted] = useState(false);
  const [setPlaybackOrder, setSetPlaybackOrder] = useState<string[]>([]);
  const [loop, setLoopState] = useState<Loop | null>(null);
  const loopRef = useRef<Loop | null>(null);
  const setLoop = useCallback((next: Loop | null) => { loopRef.current = next; setLoopState(next); }, []);
  const shuffleAnchor = useRef<string | null>(null);
  const playbackClock = useRef(new PlaybackClock());
  const selection = useRef(0);
  const pendingPlayback = useRef<string | null>(null);
  const wantsPlayback = useRef(false);
  const audioGeneration = useRef(0);
  const playbackPreparing = useRef(false), playbackFallback = useRef(false);
  const pendingSeek = useRef<number | null>(null);
  const scrubbing = useRef(false);

  // Editing
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState('');
  const editLock = useRef(false);
  const [history, setHistory] = useState<Cue[][]>([]);
  const [future, setFuture] = useState<Cue[][]>([]);
  const [gridHistory, setGridHistory] = useState<GridState[]>([]);
  const [gridFuture, setGridFuture] = useState<GridState[]>([]);
  const [detail, setDetail] = useState<AnalysisData | null>(null);

  // Background work
  const [job, setJob] = useState<Job | null>(null);
  const currentJob = useRef(job); currentJob.current = job;
  const queueStarting = useRef(false);
  const [autoQueue, setAutoQueue] = useState<string[]>([]);
  const [structureQueue, setStructureQueue] = useState<string[]>([]);
  const [structureLimit, setStructureLimit] = useState(0);
  const [detailedSectionsAvailable, setDetailedSectionsAvailable] = useState(false);
  // Reported by the engine; a track whose saved analysis has another version is analysed again.
  const [analysisVersion, setAnalysisVersion] = useState<string | null>(null);
  const analysisVersionRef = useRef(analysisVersion); analysisVersionRef.current = analysisVersion;
  const autoAnalysed = useRef(new Set<string>());
  const [batch, setBatch] = useState<ImportJob | null>(null);
  const batchRef = useRef(batch); batchRef.current = batch;
  const [importItems, setImportItems] = useState<ImportItem[]>([]);
  const [importError, setImportError] = useState('');
  const [importing, setImporting] = useState(false);
  const autoImport = useRef(false);
  const [exportStatus, setExportStatus] = useState<ExportStatus | null>(null);
  const exporting = exportStatus?.state === 'running';
  const exportRunning = useRef(false);
  const [acquisitions, setAcquisitions] = useState<AcquisitionJob[]>([]);
  const [acquisitionError, setAcquisitionError] = useState('');
  const handledAcquisitions = useRef(new Set<string>()), handledSetAnalysis = useRef(new Set<string>());
  const batchRunning = importActive(batch);
  const locked = saving || importing;

  const queueAnalysis = useCallback((ids: string[]) => { if (ids.length) setAutoQueue(queue => [...new Set([...queue, ...ids])]); }, []);
  // Tracks whose saved analysis is missing or from an older analyser, minus those already waiting.
  const unanalysed = React.useMemo(() => new Set(tracks.filter(track => needsAnalysis(track, analysisVersion) && !autoQueue.includes(track.id) && track.id !== job?.track_id).map(track => track.id)), [tracks, analysisVersion, autoQueue, job?.track_id]);

  const refresh = useCallback(async () => {
    const revision = orderRevision.current;
    const loaded = await call<Track[]>('library.list');
    setTracks(loaded);
    const next = await call<Playlist[]>('playlists.list');
    if (revision === orderRevision.current && !orderingLock.current) setPlaylists(next);
    setActive(current => {
      const match = loaded.find(track => track.id === current?.id);
      return current && match ? {...current, missing: match.missing, title: match.title, artist: match.artist, metadata: match.metadata} : current;
    });
  }, []);
  const metadataBatch = useMetadataBatch(refresh);
  useEffect(() => { refresh().catch(error => fail(error, 'The library could not be loaded: ')).finally(() => setLibraryLoaded(true)); }, []);
  // Files can be moved or restored outside the app; look again when the window regains focus.
  useEffect(() => {
    let last = Date.now();
    const again = () => { if (document.hidden || Date.now() - last < 5000) return; last = Date.now(); void refresh().catch(() => {}); };
    window.addEventListener('focus', again); document.addEventListener('visibilitychange', again);
    return () => { window.removeEventListener('focus', again); document.removeEventListener('visibilitychange', again); };
  }, [refresh]);
  useEffect(() => {
    void call<{semantic_analysis: boolean; fast_sections: boolean; structure_max_duration: number; analysis_version?: string}>('handshake').then(info => {
      if (info.fast_sections || info.semantic_analysis) setStructureLimit(info.structure_max_duration);
      setDetailedSectionsAvailable(info.semantic_analysis);
      if (typeof info.analysis_version === 'string' && info.analysis_version) setAnalysisVersion(info.analysis_version);
    }).catch(error => fail(error, 'The audio engine did not start: '));
  }, []);

  // ---- Playback ----
  const seek = (value: number, play = false) => {
    const media = audio.current, track = activeRef.current;
    if (!media || !track || track.missing) return;
    const position = Math.min(track.duration, Math.max(0, value));
    if (media.readyState >= 1) media.currentTime = position; else pendingSeek.current = position;
    playbackClock.current.reset(position, performance.now()); playhead.set(position);
    if (play) playAudio();
  };
  const preparePlayback = async () => {
    const track = activeRef.current;
    if (!track || playbackPreparing.current || playbackFallback.current) return;
    const generation = audioGeneration.current, id = track.id;
    playbackPreparing.current = true; playbackFallback.current = true; setPlayerLoading(true);
    pendingSeek.current = playhead.get();
    try {
      let task = await call<{id: string; state: string; error: string | null; result: {path: string} | null}>('playback.prepare', {id});
      while (task.state === 'running' && generation === audioGeneration.current) {
        await wait(200);
        if (generation !== audioGeneration.current) return;
        task = await call('metadata.job', {id: task.id});
      }
      if (generation !== audioGeneration.current) return;
      if (!task.result) throw new Error(task.error || 'Audio could not be prepared');
      const media = audio.current;
      if (media) { media.src = convertFileSrc(task.result.path); media.load(); }
    } catch (error) { if (generation === audioGeneration.current) { wantsPlayback.current = false; fail(error, 'Playback could not load this file: '); } }
    finally { if (generation === audioGeneration.current) { playbackPreparing.current = false; setPlayerLoading(false); } }
  };
  const playAudio = () => {
    wantsPlayback.current = true;
    const media = audio.current, generation = audioGeneration.current;
    if (!media || playbackPreparing.current || !media.getAttribute('src')) return;
    if (media.ended) { media.currentTime = 0; playhead.set(0); }
    void media.play().catch(error => {
      if (generation !== audioGeneration.current || !wantsPlayback.current) return;
      if (error.name === 'NotSupportedError') { void preparePlayback(); return; }
      if (error.name !== 'AbortError') { wantsPlayback.current = false; notify('Playback could not start. Select the track again, or relink its file.'); }
    });
  };
  const pauseAudio = () => { wantsPlayback.current = false; pendingPlayback.current = null; audio.current?.pause(); };
  const toggle = () => {
    const media = audio.current, track = activeRef.current;
    if (!media) return;
    if (!track || track.missing) { if (!editorRef.current) advancePlayback(1); return; }
    if (media.paused && !wantsPlayback.current) playAudio(); else pauseAudio();
  };
  const resetEditing = () => { setHistory([]); setFuture([]); setSaved(''); setGridHistory([]); setGridFuture([]); };
  const choose = async (track: Pick<Track, 'id'>, edit = false, preview = false) => {
    if (editLock.current) return;
    const request = ++selection.current;
    pendingPlayback.current = null;
    try {
      const loaded = await call<Track>(edit ? 'track.get' : 'track.playback', {id: track.id});
      if (request !== selection.current) return;
      if (preview && loaded.missing) { notify('This audio file is missing. Use Relink in the track list to locate it.'); return; }
      const changed = activeId.current !== loaded.id;
      activeId.current = loaded.id;
      if (preview) pendingPlayback.current = loaded.id;
      const apply = () => {
        if (!preview) setEditor(edit);
        setActive(loaded); setHistory([]); setFuture([]); setSaved('');
        if (changed) { setGridHistory([]); setGridFuture([]); playhead.set(0); setLoop(null); }
      };
      // Every way into or out of the editor uses the same content-area transition.
      if (!preview && edit !== editorRef.current) transitionWorkspace(apply); else apply();
    } catch (error) { fail(error); }
  };
  React.useLayoutEffect(() => {
    const media = audio.current;
    const generation = ++audioGeneration.current;
    playbackPreparing.current = false; playbackFallback.current = false; pendingSeek.current = null;
    wantsPlayback.current = !!active && pendingPlayback.current === active.id;
    setPlayerLoading(false); setPlaying(false);
    if (!media) return;
    media.pause(); media.removeAttribute('src'); media.load();
    if (!active || active.missing) return;
    if (!['aac', 'mp3', 'pcm_s16le'].includes(active.codec)) { void preparePlayback(); return; }
    media.src = convertFileSrc(active.path); media.load();
    return () => { if (audioGeneration.current === generation) audioGeneration.current++; };
  }, [active?.id, active?.path, active?.missing]);
  useEffect(() => {
    if (!active || pendingPlayback.current !== active.id) return;
    pendingPlayback.current = null;
    playAudio();
  }, [active]);
  useEffect(() => {
    if (!playing) return;
    let frame = 0;
    const tick = (now: number) => {
      const media = audio.current;
      if (media && !media.paused) {
        const range = loopRef.current;
        if (range && media.currentTime >= range.end) { media.currentTime = range.start; playbackClock.current.reset(media.currentTime, now); }
        const value = playbackClock.current.sample(media.currentTime, now, media.playbackRate, !media.seeking && media.readyState >= 3);
        playhead.set(Math.max(0, Math.min(media.duration || Infinity, value)));
      }
      frame = requestAnimationFrame(tick);
    };
    frame = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(frame);
  }, [playing]);
  useEffect(() => {
    const media = audio.current;
    if (media) { media.volume = volume; media.muted = muted; }
    localStorage.setItem('playerVolume', String(volume));
  }, [volume, muted]);

  const playlistTracks = React.useMemo(() => {
    if (!currentPlaylist) return tracks;
    const byId = new Map(tracks.map(track => [track.id, track]));
    return currentPlaylist.track_ids.map(id => byId.get(id)).filter((track): track is Track => !!track);
  }, [currentPlaylist, tracks]);
  // Order for Previous/Next and auto-advance: the DJ set being played, otherwise the playlist or library on screen.
  const playbackIds = JSON.stringify(workspace === 'sets' && setPlaybackOrder.length ? setPlaybackOrder : playlistTracks.filter(track => !track.missing).map(track => track.id));
  const playbackOrder = React.useMemo(() => {
    const ids = JSON.parse(playbackIds) as string[];
    if (!shuffle) return ids;
    const anchor = shuffleAnchor.current, shuffled = ids.filter(id => id !== anchor);
    for (let i = shuffled.length - 1; i > 0; i--) { const j = Math.floor(Math.random() * (i + 1)); [shuffled[i], shuffled[j]] = [shuffled[j], shuffled[i]]; }
    return anchor && ids.includes(anchor) ? [anchor, ...shuffled] : shuffled;
  }, [playbackIds, shuffle]);
  const playbackIndex = active && !active.missing ? playbackOrder.indexOf(active.id) : -1;
  const adjacentTrack = (direction: number) => {
    if (!playbackOrder.length) return undefined;
    if (playbackIndex < 0) return playbackOrder[direction > 0 ? 0 : playbackOrder.length - 1];
    const next = playbackIndex + direction;
    return repeat === 'all' ? playbackOrder[(next + playbackOrder.length) % playbackOrder.length] : playbackOrder[next];
  };
  const advancePlayback = (direction: number, ended = false) => {
    const track = activeRef.current, playable = !!track && !track.missing;
    if (ended && repeat === 'one' && playable) { seek(0, true); return; }
    // Read the media clock itself: the drawn playhead only advances while the window is visible.
    if (direction < 0 && playable && (audio.current?.currentTime ?? playhead.get()) > 3) { seek(0); return; }
    const id = adjacentTrack(direction);
    if (id && id !== track?.id) void choose({id}, false, true);
    else if (playable && (id || direction < 0)) seek(0, !!id || playing);
  };

  // ---- Analysis ----
  useEffect(() => {
    let cancelled = false;
    setDetail(null);
    if (active?.detail_analysis?.cache_path) {
      fetch(convertFileSrc(active.detail_analysis.cache_path), {cache: 'no-store'}).then(response => { if (!response.ok) throw new Error('The saved analysis could not be read. Run Analysis again for this track.'); return response.json() as Promise<AnalysisData>; })
        .then(data => { if (!cancelled) setDetail(data && typeof data === 'object' ? data : null); }).catch(error => { if (!cancelled) fail(error); });
    }
    return () => { cancelled = true; };
  }, [active?.detail_analysis?.cache_path, active?.detail_analysis?.revision]);
  // The track in the player is analysed once per session when its saved analysis is missing or outdated.
  useEffect(() => {
    if (!active || autoAnalysed.current.has(active.id) || !needsAnalysis(active, analysisVersion)) return;
    autoAnalysed.current.add(active.id); queueAnalysis([active.id]);
  }, [active?.id, active?.missing, active?.detail_analysis?.version, analysisVersion, queueAnalysis]);
  const queueStructure = useCallback((id: string) => {
    if (!structureLimit) return;
    const running = currentJob.current;
    if (running?.kind === 'structure' && running.track_id === id && jobActive(running)) return;
    setStructureQueue(queue => queue.includes(id) ? queue : [...queue, id]);
  }, [structureLimit]);
  useEffect(() => {
    if (active && !active.missing && !active.structure_analysis && active.duration <= structureLimit) queueStructure(active.id);
  }, [active?.id, active?.missing, active?.duration, active?.structure_analysis, editor, structureLimit, queueStructure]);
  const startJob = async (method: 'analysis.enqueue' | 'structure.enqueue', params: object) => {
    const track = activeRef.current;
    if (!track || track.missing || queueStarting.current || jobActive(job)) return;
    queueStarting.current = true;
    try { setJob(await call<Job>(method, {id: track.id, ...params})); }
    catch (error) { fail(error, 'Analysis could not start: '); }
    finally { queueStarting.current = false; }
  };
  useEffect(() => {
    if (queueStarting.current || jobActive(job)) return;
    const structure = autoQueue.length ? undefined : structureQueue[0];
    const id = structure ?? autoQueue[0];
    if (!id) return;
    queueStarting.current = true;
    const request = structure ? call<Job | null>('structure.enqueue', {id, automatic: true, mode: 'fast'}) : call<Job>('analysis.enqueue', {id});
    void request.then(next => { if (next) setJob(next); }).catch(error => fail(error, 'Automatic analysis could not start: ')).finally(() => {
      queueStarting.current = false;
      (structure ? setStructureQueue : setAutoQueue)(queue => queue.filter(item => item !== id));
    });
  }, [structureQueue, autoQueue, job?.id, job?.state]);
  // One request at a time: the next poll is scheduled only after the previous answer arrives.
  useEffect(() => {
    if (!job || !jobActive(job)) return;
    let stopped = false, timer = 0, failures = 0;
    const poll = async () => {
      try {
        const next = await call<Job>('jobs.get', {id: job.id});
        if (stopped) return;
        failures = 0;
        if (next.state !== job.state || next.stage !== job.stage) { setJob(next); if (!jobActive(next)) return; }
      } catch (error) {
        if (stopped) return;
        if (++failures >= 5) { fail(error, 'Lost track of the running analysis: '); setJob(null); return; }
      }
      timer = window.setTimeout(poll, failures ? Math.min(5000, 600 * 2 ** failures) : 600);
    };
    timer = window.setTimeout(poll, 600);
    return () => { stopped = true; window.clearTimeout(timer); };
  }, [job?.id, job?.state, job?.stage]);
  useEffect(() => {
    if (job?.state !== 'succeeded') return;
    if (job.kind === 'beat') void refresh().catch(error => fail(error));
    if (activeId.current !== job.track_id) return;
    void call<Track>('track.get', {id: job.track_id}).then(track => setActive(current => {
      if (current?.id !== track.id) return current;
      if (job.kind === 'structure') return {...current, structure_analysis: track.structure_analysis, sections: track.sections};
      return {...current, detail_analysis: track.detail_analysis, cues: current.cues.length ? current.cues : track.cues, grid_bpm: track.grid_bpm, grid_origin_us: track.grid_origin_us, fixed_grid_key: track.fixed_grid_key, grid_cache_key: track.grid_cache_key, offset_cache_key: track.offset_cache_key,
        grid_anchor_us: current.grid_cache_key === track.detail_analysis?.cache_key ? current.grid_anchor_us : track.grid_anchor_us,
        grid_offset_us: current.offset_cache_key === track.detail_analysis?.cache_key ? current.grid_offset_us : track.grid_offset_us};
    })).catch(error => fail(error));
  }, [job?.id, job?.state]);
  useEffect(() => {
    // The Cue Editor shows the outcome for its own track; everywhere else it is reported here.
    if (job?.state !== 'failed' || (editorRef.current && activeId.current === job.track_id)) return;
    const title = tracks.find(track => track.id === job.track_id)?.title || 'a track';
    notify(`${job.kind === 'beat' ? 'Beat analysis' : 'Section detection'} failed for “${title}”: ${job.error || 'unknown error'}. Open the track and run it again from the Analysis menu.`);
  }, [job?.id, job?.state]);
  const cancelAnalysis = async () => {
    if (!job) return;
    try { setJob(await call<Job>('jobs.cancel', {id: job.id})); } catch (error) { fail(error, 'The analysis could not be cancelled: '); }
  };

  // ---- Downloads ----
  const refreshAcquisitions = useCallback(async () => {
    try {
      const [downloads, pending] = await Promise.all([call<AcquisitionJob[]>('acquisitions.list'), call<string[]>('sets.pending_analysis')]);
      const next = pending.filter(id => !handledSetAnalysis.current.has(id));
      next.forEach(id => handledSetAnalysis.current.add(id));
      queueAnalysis(next);
      setAcquisitions(downloads); setAcquisitionError('');
    } catch (error) { setAcquisitionError(message(error)); throw error; }
  }, [queueAnalysis]);
  const hasActiveDownloads = acquisitions.some(item => activeDownloads.includes(item.state));
  const pollDelay = useRef(2500);
  pollDelay.current = acquisitionError ? 5000 : hasActiveDownloads ? 800 : 2500;
  useEffect(() => {
    let stopped = false, timer = 0;
    const poll = async () => {
      await refreshAcquisitions().catch(() => {});
      if (!stopped) timer = window.setTimeout(poll, pollDelay.current);
    };
    void poll();
    return () => { stopped = true; window.clearTimeout(timer); };
  }, [refreshAcquisitions]);
  useEffect(() => {
    const completed = acquisitions.filter(item => item.state === 'completed' && item.track_id && !handledAcquisitions.current.has(item.id));
    if (!completed.length) return;
    completed.forEach(item => handledAcquisitions.current.add(item.id));
    // Also recover an imported download whose analysis handoff was missed on exit.
    void (async () => {
      await refresh();
      const results = await Promise.allSettled(completed.map(item => call<Track>('track.get', {id: item.track_id})));
      const ids: string[] = [];
      results.forEach((result, i) => {
        if (result.status === 'fulfilled') { if (!handledSetAnalysis.current.has(result.value.id) && needsAnalysis(result.value, analysisVersionRef.current)) ids.push(result.value.id); }
        else { handledAcquisitions.current.delete(completed[i].id); setAcquisitionError(message(result.reason)); }
      });
      queueAnalysis(ids);
    })().catch(error => { completed.forEach(item => handledAcquisitions.current.delete(item.id)); setAcquisitionError(message(error)); });
  }, [acquisitions]);
  const queueAcquisition = async (review: AcquisitionReview) => { await call('acquisitions.enqueue', review); await refreshAcquisitions(); };
  const cancelAcquisition = async (id: string) => { await call('acquisitions.cancel', {id}); await refreshAcquisitions(); };
  const retryAcquisition = async (id: string) => { await call('acquisitions.retry', {id}); await refreshAcquisitions(); };

  // ---- Importing ----
  const leaveEditor = (update: () => void) => { if (editorRef.current) transitionWorkspace(() => { setEditor(false); update(); }); else update(); };
  // In a narrow window the sidebar lies over the workspace, so choosing a destination puts it away.
  const navigate = (update: () => void) => { leaveEditor(update); if (window.matchMedia('(max-width:760px)').matches) setSidebarCollapsed(true); };
  const showLibrary = (playlist: string | null = null) => leaveEditor(() => { setWorkspace('library'); setPlaylistId(playlist); setRenaming(false); });
  const queueImported = (items: ImportItem[]) => queueAnalysis(items.filter(item => item.state === 'imported' && item.track_id).map(item => item.track_id!));
  useEffect(() => {
    call<ImportJob | null>('imports.latest').then(latest => {
      if (!latest || (!importActive(latest) && localStorage.getItem('importDismissed') === latest.id)) return;
      autoImport.current = importActive(latest); setImportItems([]); setBatch({...latest, cursor: 0});
    }).catch(error => fail(error, 'The last import could not be checked: '));
  }, []);
  const batchId = batch?.id;
  useEffect(() => {
    const start = batchRef.current;
    if (!batchId || !start || (!importActive(start) && start.cursor >= start.processed)) return;
    let stopped = false, timer = 0, failures = 0, cursor = start.cursor;
    // A failed request is retried with a growing delay, so the banner keeps moving once the engine answers again.
    const poll = async () => {
      let done = false;
      try {
        const next = await call<ImportJob>('imports.get', {id: batchId, cursor});
        if (stopped) return;
        failures = 0; cursor = next.cursor; setImportError('');
        if (next.items.length) setImportItems(items => [...items, ...next.items]);
        setBatch(next);
        if (next.items.length) { await refresh().catch(() => {}); if (autoImport.current) queueImported(next.items); }
        done = !importActive(next) && next.cursor >= next.processed;
      } catch (error) { if (stopped) return; failures++; setImportError(message(error)); }
      if (!stopped && !done) timer = window.setTimeout(poll, failures ? Math.min(8000, 500 * 2 ** failures) : 500);
    };
    timer = window.setTimeout(poll, 500);
    return () => { stopped = true; window.clearTimeout(timer); };
  }, [batchId]);
  const dismissImport = () => { if (batch) localStorage.setItem('importDismissed', batch.id); setBatch(null); setImportItems([]); setImportError(''); };
  const startBatch = async (params: object) => {
    const next = await call<ImportJob>('imports.start', params);
    autoImport.current = true; setImportError(''); setImportItems(next.items); setBatch(next);
    showLibrary();
    if (next.items.length) { await refresh(); queueImported(next.items); }
  };
  const importFolder = async () => {
    if (importing || batchRunning) return;
    try { const folder = await open({directory: true, multiple: false}); if (folder) await startBatch({folder}); }
    catch (error) { fail(error, 'The folder could not be imported: '); }
  };
  const importFiles = async () => {
    if (editLock.current || importing || batchRunning) return;
    try {
      const chosen = await open({multiple: true, filters: [{name: 'Audio', extensions: AUDIO_EXTENSIONS}]});
      if (!chosen) return;
      const paths = Array.isArray(chosen) ? chosen : [chosen];
      if (paths.length > 1) { await startBatch({paths}); return; }
      setImporting(true);
      const track = await call<Track & {duplicate?: boolean}>('library.import', {path: paths[0]});
      if (track.duplicate) notify(track.missing ? 'This audio is already in your library, but its file is missing. Use Relink to locate it.' : 'This audio is already in your library.', 'info');
      await refresh();
      // Select directly: the import already returned the track and granted asset access.
      ++selection.current; activeId.current = track.id;
      leaveEditor(() => { setWorkspace('library'); setPlaylistId(null); setActive(track); resetEditing(); playhead.set(0); setLoop(null); });
    } catch (error) { fail(error, 'The file could not be added: '); }
    finally { setImporting(false); }
  };
  const relink = async (track: Track) => {
    if (editLock.current) return;
    try {
      const path = await open({multiple: false, filters: [{name: 'Audio', extensions: AUDIO_EXTENSIONS}]}); if (!path) return;
      editLock.current = true; setSaving(true);
      const updated = await call<Track>('track.relink', {id: track.id, path});
      if (activeId.current === updated.id) { pauseAudio(); playhead.set(0); setLoop(null); setActive(updated); }
      await refresh(); notify('File relinked. Cues and analysis were kept.', 'info');
    } catch (error) { fail(error, 'The file could not be relinked: '); }
    finally { editLock.current = false; setSaving(false); }
  };

  // ---- Playlists and library contents ----
  const savePlaylist = async (name: string, ids: string[], id?: string) => {
    const result = await call<Playlist>('playlists.save', {id, name, track_ids: ids});
    await refresh();
    return result;
  };
  const createPlaylist = async (name: string, ids: string[], rename = false) => {
    const created = await savePlaylist(name, ids);
    setNewPlaylistName(''); showLibrary(created.id); setRenaming(rename);
    if (ids.length) setSelectedIds(selected => selected.filter(id => !ids.includes(id)));
  };
  const newPlaylistFromSelection = async (ids: string[]) => {
    const names = new Set(playlists.map(playlist => playlist.name));
    let name = 'New playlist';
    for (let n = 2; names.has(name); n++) name = `New playlist ${n}`;
    await createPlaylist(name, ids, true);
  };
  const addToPlaylist = async (playlist: Playlist, ids: string[]) => {
    const added = ids.filter(id => !playlist.track_ids.includes(id));
    if (added.length) await savePlaylist(playlist.name, [...playlist.track_ids, ...added], playlist.id);
    notify(added.length ? `Added ${plural(added.length, 'track')} to “${playlist.name}”.` : `Already in “${playlist.name}”.`, 'info');
  };
  const removeFromPlaylist = async (ids: string[]) => {
    if (!currentPlaylist) return;
    await savePlaylist(currentPlaylist.name, currentPlaylist.track_ids.filter(id => !ids.includes(id)), currentPlaylist.id);
    setSelectedIds(selected => selected.filter(id => !ids.includes(id)));
  };
  const renamePlaylist = async (name: string) => {
    setRenaming(false);
    const playlist = currentPlaylist, next = name.trim();
    if (!playlist || !next || next === playlist.name) return;
    try { await savePlaylist(next, playlist.track_ids, playlist.id); } catch (error) { fail(error, 'The playlist could not be renamed: '); }
  };
  const deletePlaylist = async () => {
    if (!currentPlaylist) return;
    const {id, name} = currentPlaylist;
    await call<{deleted: boolean}>('playlists.delete', {id});
    setPlaylistId(null); setRenaming(false);
    await refresh();
    notify(`Deleted the playlist “${name}”. Its tracks are still in your library.`, 'info');
  };
  const removeFromLibrary = async (ids: string[]) => {
    const result = await call<{removed: number}>('library.remove', {ids});
    if (activeId.current && ids.includes(activeId.current)) {
      ++selection.current; pendingPlayback.current = null; pauseAudio(); activeId.current = null;
      setActive(null); setLoop(null); playhead.set(0); resetEditing();
    }
    setSelectedIds(selected => selected.filter(id => !ids.includes(id)));
    setAutoQueue(queue => queue.filter(id => !ids.includes(id))); setStructureQueue(queue => queue.filter(id => !ids.includes(id)));
    await refresh();
    notify(`Removed ${plural(result.removed, 'track')} from the library. ${result.removed === 1 ? 'The audio file was' : 'The audio files were'} not deleted.`, 'info');
  };
  const movePlaylistTrack = (id: string, direction: number) => {
    if (!currentPlaylist) return;
    const ids = currentPlaylist.track_ids, at = ids.indexOf(id), target = at + direction;
    if (at < 0 || target < 0 || target >= ids.length) return;
    void reorderPlaylist(id, ids[target]);
  };
  const reorderPlaylist = async (id: string, target: string) => {
    if (!currentPlaylist || orderingLock.current || id === target) return;
    const playlist = currentPlaylist, ids = [...playlist.track_ids], from = ids.indexOf(id), to = ids.indexOf(target);
    if (from < 0 || to < 0) return;
    ids.splice(to, 0, ids.splice(from, 1)[0]); orderingLock.current = true; orderRevision.current++; setOrdering(true);
    setPlaylists(items => items.map(item => item.id === playlist.id ? {...item, track_ids: ids} : item));
    try { const result = await call<Playlist>('playlists.save', {id: playlist.id, name: playlist.name, track_ids: ids}); setPlaylists(items => items.map(item => item.id === result.id ? result : item)); }
    catch (error) { fail(error, 'The new order could not be saved: '); setPlaylists(items => items.map(item => item.id === playlist.id ? playlist : item)); }
    finally { orderingLock.current = false; setOrdering(false); }
  };
  const playlistReorder = useTrackReorder('playlist', (id, target) => void reorderPlaylist(id, target), ordering);

  // ---- Cues, grid and sections ----
  const persist = async (cues: Cue[], mode: 'edit' | 'undo' | 'redo' = 'edit') => {
    const track = activeRef.current;
    if (!track || editLock.current) return false;
    editLock.current = true; setSaving(true); setSaved('Saving…');
    const previous = track.cues;
    try {
      const updated = await call<Track>('cues.save', {id: track.id, cues});
      if (activeId.current === track.id) setActive(updated);
      if (mode === 'edit') { setHistory(items => [...items, previous]); setFuture([]); }
      if (mode === 'undo') { setHistory(items => items.slice(0, -1)); setFuture(items => [...items, previous]); }
      if (mode === 'redo') { setFuture(items => items.slice(0, -1)); setHistory(items => [...items, previous]); }
      setSaved('Saved');
      try { await refresh(); } catch (error) { fail(error, 'The cue was saved, but the library list could not refresh: '); }
      return true;
    } catch (error) { setSaved(`Not saved: ${message(error)}`); return false; }
    finally { editLock.current = false; setSaving(false); }
  };
  const generateCues = async () => {
    const track = activeRef.current;
    if (!track || editLock.current) return;
    const previous = track.cues, id = track.id;
    editLock.current = true; setSaving(true); setSaved('Finding cues…');
    try {
      const result = await call<{track: Track; added: number; has_sections: boolean; free_slots: number}>('cues.auto', {id});
      if (activeId.current === id) { setActive(result.track); if (result.added) { setHistory(items => [...items, previous]); setFuture([]); } }
      setSaved(result.added ? `${plural(result.added, 'cue')} added` : !result.free_slots ? 'All eight pads are in use. Remove a cue to make room.'
        : result.has_sections ? 'No new cue positions found' : 'No new cue positions found. Run Analysis ▸ Find sections for more.');
      if (result.added && !result.has_sections) notify('Added a cue at the first beat. Run Analysis ▸ Find sections to get cues at section starts too.', 'info');
      await refresh();
    } catch (error) { setSaved(`Auto cues failed: ${message(error)}`); }
    finally { editLock.current = false; setSaving(false); }
  };
  const saveTrackField = async (method: string, params: object) => {
    const track = activeRef.current;
    if (!track || editLock.current) return false;
    editLock.current = true; setSaving(true);
    try {
      const updated = await call<Track>(method, {id: track.id, ...params});
      if (activeId.current === updated.id) setActive(updated);
      return true;
    } catch (error) { fail(error, 'The change was not saved: '); return false; }
    finally { editLock.current = false; setSaving(false); }
  };
  const gridSnapshot = (): GridState => ({bpm: active?.grid_bpm ?? null, origin_us: active?.grid_origin_us ?? null, anchor_us: active?.grid_anchor_us ?? null, offset_us: active?.grid_offset_us || 0});
  const saveGrid = async (method: 'grid.fixed' | 'grid.offset' | 'grid.restore', params: object) => {
    const before = gridSnapshot();
    if (await saveTrackField(method, params)) { setGridHistory(items => [...items, before]); setGridFuture([]); void refresh().catch(() => {}); }
  };
  const restoreGrid = async (direction: 'undo' | 'redo') => {
    const state = (direction === 'undo' ? gridHistory : gridFuture).at(-1); if (!state) return;
    const before = gridSnapshot();
    if (!await saveTrackField('grid.restore', {state})) return;
    if (direction === 'undo') { setGridHistory(items => items.slice(0, -1)); setGridFuture(items => [...items, before]); }
    else { setGridFuture(items => items.slice(0, -1)); setGridHistory(items => [...items, before]); }
    void refresh().catch(() => {});
  };

  // ---- Export ----
  const exportTracks = async (selector: ExportSelector, name: string, copyAudio = false) => {
    if (exportRunning.current) return;
    exportRunning.current = true;
    try {
      const base = name.replace(/[<>:"/\\|?*\x00-\x1f]/g, '_').slice(0, 100) || 'CrateLoom';
      const path = await save({defaultPath: base + '.xml', filters: [{name: 'Rekordbox XML', extensions: ['xml']}]});
      if (!path) return;
      // Progress is shown in a strip, so the library, player and editor stay usable while this runs.
      setExportStatus({state: 'running', text: `Exporting “${name}”…`});
      let task = await call<ExportTask>('export.enqueue', {...selector, path, copy_audio: copyAudio});
      const started = Date.now();
      let failures = 0;
      while (task.state === 'running') {
        await wait(450);
        if (Date.now() - started > EXPORT_TIMEOUT_MS) throw new Error('it is taking much longer than expected. Check the destination folder before exporting again.');
        try { task = await call<ExportTask>('metadata.job', {id: task.id}); failures = 0; }
        catch (error) { if (++failures >= 5) throw error; }
      }
      if (!task.result) throw new Error(task.error || 'the export stopped without a result');
      const {track_count, grid_count, xml} = task.result;
      setExportStatus({state: 'done', text: `Saved rekordbox XML with ${plural(track_count, 'track')} · ${grid_count} with a confirmed beat grid.`, detail: xml});
    } catch (error) { setExportStatus({state: 'failed', text: `“${name}” was not exported: ${message(error)}`}); }
    finally { exportRunning.current = false; }
  };

  // ---- Keyboard ----
  const keys = useRef<(event: KeyboardEvent) => void>(() => {});
  keys.current = event => {
    if (event.defaultPrevented || typingTarget(event)) return;
    const command = event.metaKey || event.ctrlKey, key = event.key.toLowerCase();
    if (command && key === 'f' && search.current) { event.preventDefault(); search.current.focus(); search.current.select(); }
    else if (command && key === 'o') { event.preventDefault(); void importFiles(); }
    // Space is always play/pause, as in other music apps, even after clicking a button such as a cue pad
    // (Enter still presses the focused button). Text fields, menus and dialogs keep their own Space.
    else if (event.code === 'Space' && !command && !event.altKey) { event.preventDefault(); if (!event.repeat) toggle(); }
  };
  useEffect(() => {
    const handler = (event: KeyboardEvent) => keys.current(event);
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, []);

  const openEditor = () => { if (active && !editor) transitionWorkspace(() => setEditor(true)); };
  const editorActions: EditorActions = {
    seek, toggle, play: playAudio, pause: pauseAudio, setLoop,
    persistCues: cues => persist(cues),
    undoCues: () => { const previous = history.at(-1); if (previous) void persist(previous, 'undo'); },
    redoCues: () => { const next = future.at(-1); if (next) void persist(next, 'redo'); },
    autoCues: () => void generateCues(),
    fixGrid: grid => void saveGrid('grid.fixed', grid),
    offsetGrid: offset_us => void saveGrid('grid.offset', {offset_us}),
    resetGrid: () => void saveGrid('grid.restore', {state: {bpm: null, origin_us: null, anchor_us: null, offset_us: 0}}),
    undoGrid: () => void restoreGrid('undo'), redoGrid: () => void restoreGrid('redo'),
    saveSections: (sections: Section[]) => saveTrackField('sections.save', {sections}),
    analyze: () => void startJob('analysis.enqueue', {force: !!detail}),
    findSections: mode => void startJob('structure.enqueue', {force: !!active?.structure_analysis, mode}),
    cancelAnalysis: () => void cancelAnalysis(),
    exportTrack: () => { if (active) void exportTracks({id: active.id}, active.title); },
    openMetadata: () => { if (active) setMetadataIds([active.id]); },
    relink: () => { if (active) void relink(active); },
    notify: text => notify(text),
  };
  const runningDownloads = acquisitions.filter(item => activeDownloads.includes(item.state)).length;
  const analysisWaiting = autoQueue.length + structureQueue.length;
  const jobTitle = job ? tracks.find(track => track.id === job.track_id)?.title || 'another track' : '';
  const title = editor ? 'Cue Editor' : workspace === 'find' ? 'Find Music' : workspace === 'sets' ? 'Import DJ Set' : currentPlaylist?.name || 'All Tracks';
  const importTitle = !batch ? '' : batch.state === 'scanning' ? 'Scanning folder' : batchRunning ? 'Importing' : batch.state === 'completed' ? 'Import complete' : batch.state === 'cancelled' ? 'Import cancelled' : batch.state === 'failed' ? 'Import failed' : `Import ${batch.state}`;

  return <div className="app">
    <Titlebar expanded={!sidebarCollapsed} onToggle={() => setSidebarCollapsed(value => !value)}/>
    <Sidebar collapsed={sidebarCollapsed} onCollapsedChange={setSidebarCollapsed}>
      <div className="side-label">LIBRARY</div>
      <button title="All Tracks" aria-label="All Tracks" aria-current={!editor && workspace === 'library' && !playlistId ? 'page' : undefined} className={`nav ${!playlistId && workspace === 'library' ? 'active' : ''}`} onClick={() => navigate(() => { setWorkspace('library'); setPlaylistId(null); setRenaming(false); })}><FolderOpen size={17}/> All Tracks <span>{tracks.length}</span></button>
      <button title="Find Music" aria-label="Find Music" aria-current={!editor && workspace === 'find' ? 'page' : undefined} className={`nav ${workspace === 'find' ? 'active' : ''}`} onClick={() => navigate(() => setWorkspace('find'))}><Search size={17}/> Find Music</button>
      <button title="Import DJ Set" aria-label="Import DJ Set" aria-current={!editor && workspace === 'sets' ? 'page' : undefined} className={`nav ${workspace === 'sets' ? 'active' : ''}`} onClick={() => navigate(() => setWorkspace('sets'))}><ListMusic size={17}/> Import DJ Set</button>
      <div className="playlist-nav">
        <div className="side-label">PLAYLISTS</div>
        {playlists.map(playlist => <button key={playlist.id} title={playlist.name} aria-label={playlist.name} aria-current={!editor && workspace === 'library' && playlistId === playlist.id ? 'page' : undefined}
          className={`nav ${playlistId === playlist.id && workspace === 'library' ? 'active' : ''}`} onClick={() => navigate(() => { setWorkspace('library'); setPlaylistId(playlist.id); setRenaming(false); })}><ListMusic size={15}/><span className="playlist-name">{playlist.name}</span><span>{playlist.track_ids.length}</span></button>)}
        <form onSubmit={event => { event.preventDefault(); if (newPlaylistName.trim()) void createPlaylist(newPlaylistName, []).catch(error => fail(error, 'The playlist could not be created: ')); }}>
          <input aria-label="New playlist name" placeholder="New playlist" maxLength={100} value={newPlaylistName} onChange={event => setNewPlaylistName(event.target.value)}/>
          <button aria-label="Create playlist" title="Create an empty playlist with this name" disabled={!newPlaylistName.trim()}><Plus size={14}/></button>
        </form>
      </div>
      <div className="sidebar-bottom"><label htmlFor="appearance">Appearance</label><Select id="appearance" value={theme} onChange={event => setTheme(event.target.value)}><option value="system">System</option><option value="dark">Dark</option><option value="light">Light</option></Select></div>
    </Sidebar>
    <main>
      <header>
        <div className="heading">
          {editor && <button className="icon" aria-label={`Back to ${workspaceNames[workspace]}`} title={`Back to ${workspaceNames[workspace]}`} onClick={() => transitionWorkspace(() => setEditor(false))}><ArrowLeft size={19}/></button>}
          {renaming && currentPlaylist && !editor && workspace === 'library'
            ? <input className="title-input" aria-label="Playlist name" autoFocus maxLength={100} defaultValue={currentPlaylist.name} onFocus={event => event.currentTarget.select()}
                onBlur={event => void renamePlaylist(event.currentTarget.value)} onKeyDown={event => { if (event.key === 'Enter') event.currentTarget.blur(); if (event.key === 'Escape') { event.currentTarget.value = currentPlaylist.name; event.currentTarget.blur(); } }}/>
            : <h1 title={title}>{title}</h1>}
        </div>
        <div className="header-actions">
          {!editor && workspace === 'library' && <label className="search" title="Search by title or artist (⌘F)"><Search size={16}/><input ref={search} type="search" aria-label="Search tracks" placeholder="Search tracks" value={query} onChange={event => setQuery(event.target.value)}/></label>}
          <button className="toolbar-download" title={runningDownloads ? `Downloads: ${runningDownloads} in progress` : 'Downloads'} aria-label={runningDownloads ? `Downloads, ${runningDownloads} in progress` : 'Downloads'}
            aria-haspopup="dialog" aria-expanded={downloadsOpen} onClick={() => { setDownloadsOpen(true); void refreshAcquisitions().catch(() => {}); }}>
            <Download size={16}/><span className="toolbar-label">Downloads</span>{runningDownloads > 0 && <span className="count-badge">{runningDownloads}</span>}</button>
          <Menu label={importing ? 'Adding…' : 'Add music'} ariaLabel="Add music" icon={<Plus size={16}/>} buttonClassName="primary" align="end" title="Add audio files or a folder to the library" disabled={importing || batchRunning} items={[
            {label: 'Files…', description: 'Choose one or more audio files.', hint: '⌘O', onSelect: () => void importFiles()},
            {label: 'Folder…', description: 'Add every audio file in a folder.', onSelect: () => void importFolder()},
          ]}/>
        </div>
      </header>
      <div className="status-stack">
        {notices.map(item => <div key={item.id} className="notice" data-tone={item.tone} role={item.tone === 'error' ? 'alert' : 'status'}><span>{item.text}</span><button onClick={() => dismissNotice(item.id)}>Dismiss</button></div>)}
        {exportStatus && <div className="notice export-status" data-tone={exportStatus.state === 'failed' ? 'error' : 'info'} role={exportStatus.state === 'failed' ? 'alert' : 'status'}>
          {exportStatus.state === 'running' ? <LoaderCircle size={15} className="spin" aria-hidden="true"/> : exportStatus.state === 'done' ? <Check size={15} aria-hidden="true"/> : null}
          <span>{exportStatus.text}{exportStatus.detail && <small>{exportStatus.detail}</small>}</span>
          {exportStatus.state !== 'running' && <button onClick={() => setExportStatus(null)}>Dismiss</button>}
        </div>}
        {batch && <div className="import-progress" role="status">
          <div>
            <strong>{importTitle}</strong>
            <span>{batch.processed} / {batch.total} · {batch.imported} added · {plural(batch.duplicates, 'duplicate')} · {batch.failed} failed</span>
            {batchRunning && <progress aria-label="Import progress" max={Math.max(1, batch.total)} value={batch.processed}/>}
            {batchRunning && batch.current && <small>{batch.current}</small>}
            {batch.error && <small>{batch.error}</small>}
            {importError && <small role="alert">Progress could not be read ({importError}). Trying again…</small>}
          </div>
          {batchRunning && <button onClick={() => { void call('imports.cancel', {id: batch.id}).catch(error => fail(error, 'The import could not be cancelled: ')); }}>Cancel import</button>}
          {(!batchRunning || importError) && <button className="icon" aria-label="Dismiss import report" title="Dismiss" onClick={dismissImport}><X size={16}/></button>}
          {importItems.length > 0 && <Disclosure label={<> File results · {importItems.length} </>}>{importItems.map(item => <div key={item.sequence} className="import-result"><span title={item.path}>{item.path.split(/[\\/]/).at(-1)}</span><span>{item.state}{item.error ? ` · ${item.error}` : ''}</span></div>)}</Disclosure>}
        </div>}
        {!editor && (analysisWaiting > 0 || job?.state === 'running') && <div className="analysis-queue-status" role="status">
          <Activity size={14}/><span>{job?.state === 'running' ? `${job.kind === 'structure' ? 'Finding sections' : 'Analysing beats'} · ${jobTitle}` : 'Analysis queued'}{analysisWaiting > 0 ? ` · ${analysisWaiting} waiting` : ''}</span>
          {job?.state === 'running' && <button onClick={() => void cancelAnalysis()}>Cancel this track</button>}
          {analysisWaiting > 0 && <button title="Remove the waiting tracks from the analysis queue" onClick={() => { setAutoQueue([]); setStructureQueue([]); }}>Clear queue</button>}
        </div>}
      </div>
      <SetImportPanel hidden={workspace !== 'sets' || editor} activeTrackId={active?.id ?? null} playing={playing}
        onPlay={(id, order) => {
          setSetPlaybackOrder(order);
          if (active?.id === id && !active.missing) { selection.current++; pendingPlayback.current = null; toggle(); }
          else void choose({id}, false, true);
        }}
        onUpdated={async () => { await refresh(); await refreshAcquisitions(); }} onPlaylist={id => { setPlaylistId(id); setWorkspace('library'); }} onRetry={retryAcquisition}/>
      {editor && active && <CueEditor key={active.id} track={active} detail={detail} playing={playing} audio={audio} scrubbing={scrubbing} loop={loop} locked={locked} saved={saved}
        canUndo={history.length > 0} canRedo={future.length > 0} canUndoGrid={gridHistory.length > 0} canRedoGrid={gridFuture.length > 0}
        job={job} jobTitle={jobTitle} queued={autoQueue.includes(active.id) || structureQueue.includes(active.id)} sectionsAvailable={structureLimit > 0} detailedSections={detailedSectionsAvailable}
        exporting={exporting} volume={muted ? 0 : volume} actions={editorActions}/>}
      {/* Find Music stays mounted behind the editor so its search is still there on the way back. */}
      {workspace === 'find' && <div className="workspace-keep" hidden={editor}>
        <DiscoveryPanel onImport={importFiles} jobs={acquisitions} error={acquisitionError} onQueue={queueAcquisition} onCancel={cancelAcquisition} onRetry={retryAcquisition} onRefresh={refreshAcquisitions}
          onOpen={async id => { await choose({id}, true); }}/>
      </div>}
      {!editor && workspace === 'library' && <LibraryView tracks={tracks} playlists={playlists} playlist={currentPlaylist} query={query} filter={libraryFilter} onFilter={setLibraryFilter}
        loading={!libraryLoaded} selectedIds={selectedIds} onSelectedIds={setSelectedIds} activeId={active?.id ?? null} playing={playing} ordering={ordering} reorder={playlistReorder} exporting={exporting} importing={importing || batchRunning}
        metadataBatch={metadataBatch} unanalysed={unanalysed} actions={{
          analyze: queueAnalysis,
          select: track => void choose(track),
          edit: track => void choose(track, true),
          togglePlay: track => { if (active?.id === track.id) toggle(); else void choose(track, false, true); },
          relink: track => void relink(track),
          openMetadata: setMetadataIds, writeTags: setTagWriteIds,
          autoMetadata: (ids, writeTags) => void metadataBatch.start(ids, writeTags),
          addToPlaylist, newPlaylist: newPlaylistFromSelection, removeFromPlaylist,
          renamePlaylist: () => setRenaming(true), deletePlaylist, removeFromLibrary,
          exportTracks: (selector, name, copyAudio) => void exportTracks(selector, name, copyAudio),
          moveTrack: movePlaylistTrack, importFiles: () => void importFiles(),
        }}/>}
      <PanelPresence open={!!tagWriteIds}>{tagWriteIds && <TagWriteReview ids={tagWriteIds} onClose={() => setTagWriteIds(null)} onSaved={refresh}/>}</PanelPresence>
      <PanelPresence open={!!metadataIds}>{metadataIds && <MetadataEditor key={metadataIds.join(',')} ids={metadataIds} onClose={() => setMetadataIds(null)}
        onSaved={async () => { await refresh(); if (activeId.current) { const updated = await call<Track>('track.get', {id: activeId.current}); setActive(current => current?.id === updated.id ? updated : current); } }}/>}</PanelPresence>
      <audio ref={audio} preload="metadata"
        onLoadedMetadata={event => { if (pendingSeek.current !== null) { event.currentTarget.currentTime = Math.min(pendingSeek.current, event.currentTarget.duration || pendingSeek.current); pendingSeek.current = null; } }}
        onCanPlay={() => { setPlayerLoading(false); if (wantsPlayback.current && audio.current?.paused) playAudio(); }}
        onWaiting={() => setPlayerLoading(true)}
        onTimeUpdate={event => { if (event.currentTarget.paused && !event.currentTarget.seeking && !scrubbing.current) playhead.set(event.currentTarget.currentTime); }}
        onPlay={event => { wantsPlayback.current = true; setPlayerLoading(false); playbackClock.current.reset(event.currentTarget.currentTime, performance.now()); setPlaying(true); }}
        onPause={() => setPlaying(false)}
        onEnded={() => { wantsPlayback.current = false; setPlaying(false); if (!editorRef.current) advancePlayback(1, true); }}
        onError={() => { setPlaying(false); if (active && !active.missing) { if (!playbackFallback.current) void preparePlayback(); else if (!playbackPreparing.current) { setPlayerLoading(false); wantsPlayback.current = false; notify('Playback failed. Select the track again, or relink its file.'); } } }}/>
      {!editor && <PlayerBar track={active} playing={playing} loading={playerLoading} wantsPlayback={wantsPlayback.current}
        canPlay={(!!active && !active.missing) || playbackOrder.length > 0} hasPrevious={(!!active && !active.missing) || !!adjacentTrack(-1)} hasNext={!!adjacentTrack(1)}
        shuffle={shuffle} repeat={repeat} volume={volume} muted={muted} onEdit={openEditor} onToggle={toggle} onPrevious={() => advancePlayback(-1)} onNext={() => advancePlayback(1)}
        onShuffle={() => { shuffleAnchor.current = active?.id ?? null; setShuffle(!shuffle); }} onRepeat={() => setRepeat(repeat === 'off' ? 'all' : repeat === 'all' ? 'one' : 'off')}
        onVolume={setVolume} onMuted={setMuted} onSeek={seek}/>}
      <PanelPresence open={downloadsOpen}>{downloadsOpen && <Downloads jobs={acquisitions} error={acquisitionError} close={() => setDownloadsOpen(false)}
        clear={() => { void call('acquisitions.clear_completed').then(refreshAcquisitions).catch(error => setAcquisitionError(message(error))); }}
        cancel={id => { void cancelAcquisition(id).catch(error => setAcquisitionError(message(error))); }}
        retry={id => { void retryAcquisition(id).catch(error => setAcquisitionError(message(error))); }}
        open={id => { setDownloadsOpen(false); void choose({id}, true); }}/>}</PanelPresence>
    </main>
  </div>;
}
// Reuse the root when the development server re-runs this module, so there is never a second App.
const container = document.getElementById('root') as HTMLElement & {crateloomRoot?: Root};
(container.crateloomRoot ??= createRoot(container)).render(<ErrorBoundary><App/></ErrorBoundary>);
