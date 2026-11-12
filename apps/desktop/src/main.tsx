import React, { useCallback, useEffect, useRef, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { invoke, convertFileSrc } from '@tauri-apps/api/core';
import { open, save } from '@tauri-apps/plugin-dialog';
import { Music2, Plus, Search, Play, Pause, ArrowLeft, Upload, Activity, FolderOpen, SkipBack, ZoomIn, ZoomOut, ChevronLeft, ChevronRight, Crosshair, RotateCcw, SlidersHorizontal, X, Undo2, Redo2, Info, RefreshCw, FolderPlus, Link2, ListMusic, ArrowUp, ArrowDown, Tags, Download, SkipForward, Shuffle, Repeat, Repeat1, Volume2, VolumeX, Pencil, GripVertical, WandSparkles } from 'lucide-react';
import './style.css';
import './workspace.css';
import {Sidebar} from './Sidebar';
import {Titlebar} from './Titlebar';
import {PanelPresence,transitionWorkspace} from './PanelMotion';
import {Disclosure} from './Disclosure';
import {Select} from './Select';
import {useTrackReorder} from './useTrackReorder';
import {MetadataEditor,type MetadataState} from './MetadataEditor';
import {MetadataBatchProgress,useMetadataBatch} from './MetadataBatch';
import {TagWriteReview} from './TagWriteReview';
import {DiscoveryPanel,type AcquisitionJob,type AcquisitionReview} from './DiscoveryPanel';
import {Downloads, activeDownloads} from './Downloads';
import {SetImportPanel} from './SetImportPanel';
import {WheelInput, type WheelMode} from './wheel-input';
import {completeBeats,lowerBound,beatCoordinate,jumpBeats} from './beat-grid';
import {Envelope,PlaybackClock} from './waveform-envelope';

type Cue = { slot: number; position_us: number; label: string; color?: string };
type Section = {id: string; start_us: number; end_us: number; label: string; origin?: 'manual' | 'model-reviewed' | 'model'};
type StructureAnalysis = {suggestions: {start_us:number;end_us:number;label:string}[]; model:string; weight_license:string; revision:string};
type AnalysisData = { version: string; tempo: number | null; beats: number[]; downbeats: null; sections: null; waveform: { peaks: number[]; rgb: number[][]; signal?: {encoding:string;hop:number;rate:number;data:string} }; warnings: string[] };
type ImportItem={sequence:number;path:string;state:string;track_id:string|null;error:string|null};
type ImportJob={id:string;state:string;total:number;processed:number;imported:number;duplicates:number;failed:number;current:string;error:string|null;cursor:number;items:ImportItem[]};
type GridState = {bpm:number|null;origin_us:number|null;anchor_us:number|null;offset_us:number};
type Job = {id: string; track_id: string; state: string; stage: string; error: string | null; kind: 'beat' | 'structure'};
type Playlist={id:string;name:string;track_ids:string[]};
type Track = { metadata:MetadataState|null; id: string; path: string; title: string; artist: string; duration: number; codec: string; sample_rate: number | null; missing: boolean; cues: Cue[]; sections: Section[]; structure_analysis: StructureAnalysis | null; grid_anchor_us: number | null; grid_cache_key: string | null; grid_offset_us: number; grid_bpm: number | null; grid_origin_us: number | null; fixed_grid_key: string | null; offset_cache_key: string | null; detail_analysis: null | {cache_path: string; cache_key: string; tempo: number | null; revision?: string; version?:string}; analysis: null | { peaks: number[]; tempo: null } };
const call = <T,>(method: string, params: object = {}) => invoke<T>('engine_call', { method, params });
const time = (n: number) => `${Math.floor(n / 60)}:${Math.floor(n % 60).toString().padStart(2, '0')}`;
const slotName = (n: number) => String.fromCharCode(65 + n);
const cueColors = ['#ef5c86', '#57b7ee', '#9ccc51', '#ae7cf5', '#edac52', '#5acbb7', '#d885c3', '#b3bec8'];
const emptyPeaks:number[] = [];
const sectionColors: Record<string,string> = {Intro:'#356997',Verse:'#536d8e',Chorus:'#25835e',Bridge:'#67548b',Instrumental:'#4b7180',Solo:'#965c60',Break:'#876828',Up:'#7849aa',Down:'#92602b',Outro:'#496a99',Other:'#5c6f6b'};
const WaveBars = React.memo(function WaveBars({data, fallback, start, length, duration, overview=false}: {data: AnalysisData | null; fallback: number[]; start: number; length: number; duration: number; overview?:boolean}) {
  const canvas=useRef<HTMLCanvasElement>(null), dimensions=useRef({width:0,height:0});
  const view=useRef({start,length});view.current={start,length};
  const prepared=React.useMemo(()=>{
    const encoded=data?.waveform.signal?.data;
    let samples:Int16Array;
    if(encoded){const bytes=Uint8Array.from(atob(encoded),c=>c.charCodeAt(0));samples=new Int16Array(bytes.buffer);}
    else {const peaks=data?.waveform.peaks || fallback;samples=new Int16Array(peaks.length*2);peaks.forEach((p,i)=>{samples[i*2]=-Math.round(p*32767);samples[i*2+1]=Math.round(p*32767);});}
    const colors=(data?.waveform.rgb || [[0,.7,.9]]).map(rgb=>{
      const weights=rgb.map(v=>v*v*v),total=weights[0]+weights[1]+weights[2]+.00001;
      return `rgb(${Math.round(20+235*weights[0]/total)},${Math.round(20+235*weights[1]/total)},${Math.round(20+235*weights[2]/total)})`;
    });
    return {envelope:new Envelope(samples),colors};
  },[data,fallback]);
  const draw=React.useCallback(()=>{
    const el=canvas.current;if(!el || !duration)return;
    const {width,height}=dimensions.current,ctx=el.getContext('2d');if(!ctx || !width || !height)return;
    ctx.clearRect(0,0,width,height);
    const {envelope,colors}=prepared,count=envelope.length,{start,length}=view.current;if(!count || !length)return;
    const centre=height/2,gain=overview ? .66 : .72,bounds:[number,number]=[0,0];
    for(let x=0;x<width;x++){
      const from=(start+x/width*length)/duration*count,to=(start+(x+1)/width*length)/duration*count;
      if(to<=0 || from>=count)continue;
      const [lo,hi]=envelope.range(from,Math.max(from+1,to),bounds);
      const ci=Math.max(0,Math.min(colors.length-1,Math.floor((from+to)/2/count*colors.length)));
      ctx.fillStyle=colors[ci];ctx.fillRect(x,centre-hi*centre*gain,1,Math.max(.8,(hi-lo)*centre*gain));
    }
  },[prepared,duration,overview]);
  // Keep the resize observer for the canvas lifetime, not every playback frame.
  React.useLayoutEffect(()=>{
    const el=canvas.current;if(!el)return;
    const resize=()=>{const rect=el.getBoundingClientRect(),scale=window.devicePixelRatio || 1;
      const width=Math.max(1,Math.round(rect.width*scale)),height=Math.max(1,Math.round(rect.height*scale));
      if(el.width!==width)el.width=width;if(el.height!==height)el.height=height;
      dimensions.current={width,height};draw();};
    resize();const observer=new ResizeObserver(resize);observer.observe(el);return()=>observer.disconnect();
  },[draw]);
  React.useLayoutEffect(draw,[start,length,draw]);
  return <canvas ref={canvas} aria-hidden="true"/>;
});

function App() {
  const [sidebarVisible,setSidebarVisible]=useState(()=>localStorage.getItem('sidebarVisible')!=='false');
  const [sidebarCollapsed,setSidebarCollapsed]=useState(()=>localStorage.getItem('sidebarCollapsed')==='true');
  useEffect(()=>localStorage.setItem('sidebarVisible',String(sidebarVisible)),[sidebarVisible]);
  const [ordering,setOrdering]=useState(false);
  const orderingLock=useRef(false);
  const orderRevision=useRef(0);
  const [tracks, setTracks] = useState<Track[]>([]);
  const [active, setActive] = useState<Track | null>(null);
  const [editor, setEditor] = useState(false);
  const [workspace,setWorkspace]=useState<'library'|'find'|'sets'>('library');
  const discoverOpen=workspace==='find', setOpen=workspace==='sets';
  const setDiscoverOpen=(open:boolean)=>setWorkspace(open?'find':'library');
  const [acquisitions,setAcquisitions]=useState<AcquisitionJob[]>([]);
  const [acquisitionError,setAcquisitionError]=useState('');
  const [downloadsOpen, setDownloadsOpen]=useState(false);
  const [shuffle, setShuffle]=useState(false);
  const [repeat, setRepeat]=useState<'off'|'all'|'one'>('off');
  const [volume, setVolume]=useState(()=>{
    const stored=localStorage.getItem('playerVolume');
    return stored !== null && Number.isFinite(Number(stored)) ? Math.min(1,Math.max(0,Number(stored))) : 1;
  });
  const [muted, setMuted]=useState(false);
  const [setPlaybackOrder, setSetPlaybackOrder]=useState<string[]>([]);
  const shuffleAnchor=useRef<string|null>(null);
  const handledAcquisitions=useRef(new Set<string>());
  const handledSetAnalysis=useRef(new Set<string>());
  const [query, setQuery] = useState('');
  const [notice, setNotice] = useState('');
  const [busy, setBusy] = useState('');
  const [saving, setSaving] = useState(false);
  const [playing, setPlaying] = useState(false);
  const [position, renderPosition] = useState(0);
  const positionRef=useRef(0);
  const setPosition=(value:number)=>{positionRef.current=value;renderPosition(value);};
  const playbackClock=useRef(new PlaybackClock());
  const [theme, setTheme] = useState(localStorage.getItem('theme') || 'system');
  const [history, setHistory] = useState<Cue[][]>([]);
  const [future, setFuture] = useState<Cue[][]>([]);
  const [saved, setSaved] = useState('');
  const [zoom, setZoom] = useState(16);
  const [beatJump,setBeatJump]=useState(()=>{const n=Number(localStorage.getItem('beatJump') || 16);return [.25,.5,1,2,4,8,16,32,64].includes(n)?n:16;});
  const [wheelMode,setWheelMode]=useState<WheelMode>((localStorage.getItem('waveWheelMode') as WheelMode) || 'auto');
  const wheelModeRef=useRef(wheelMode);wheelModeRef.current=wheelMode;
  const [job, setJob] = useState<Job | null>(null);
  const [detail, setDetail] = useState<AnalysisData | null>(null);
  const [snap, setSnap] = useState('off');
  const [preRoll, setPreRoll] = useState(4);
  const [loop, setLoop] = useState<{start: number; end: number} | null>(null);
  const [loopSeconds, setLoopSeconds] = useState(4);
  const [editingSection, setEditingSection] = useState<string | null>(null);
  const [sectionsOpen, setSectionsOpen] = useState(false);
  const [gridHistory,setGridHistory] = useState<GridState[]>([]);
  const [gridFuture,setGridFuture] = useState<GridState[]>([]);
  const taps = useRef<number[]>([]);
  const [autoQueue, setAutoQueue] = useState<string[]>([]);
  const [structureQueue, setStructureQueue] = useState<string[]>([]);
  const [structureLimit, setStructureLimit] = useState(0);
  const [detailedSectionsAvailable,setDetailedSectionsAvailable] = useState(false);
  const [batch,setBatch]=useState<ImportJob|null>(null);
  const [importItems,setImportItems]=useState<ImportItem[]>([]);
  const autoImport=useRef(false);
  const [libraryFilter,setLibraryFilter]=useState('all');
  const [playlists,setPlaylists]=useState<Playlist[]>([]);
  const [playlistId,setPlaylistId]=useState<string|null>(null);
  const [playlistName,setPlaylistName]=useState('');
  const [selectedIds,setSelectedIds]=useState<string[]>([]);
  const [metadataIds,setMetadataIds]=useState<string[]|null>(null);
  const [tagWriteIds,setTagWriteIds]=useState<string[]|null>(null);
  const currentPlaylist=playlists.find(p=>p.id===playlistId);
  const batchRunning=!!batch && ['scanning','running'].includes(batch.state);
  const queueStarting = useRef(false);
  const currentJob = useRef(job);
  currentJob.current = job;
  const tempCue = useRef(0);
  const [tempCuePosition,setTempCuePosition]=useState(0);
  const cuePreview = useRef(false);
  const cueKeepPlaying = useRef(false);
  const zoomRef = useRef(zoom);
  zoomRef.current = zoom;
  const [sectionLabel, setSectionLabel] = useState('Intro');
  const [sectionStart, setSectionStart] = useState('0');
  const [sectionEnd, setSectionEnd] = useState('');
  const loopRef = useRef<{start: number; end: number} | null>(null);
  const [dragPreview, setDragPreview] = useState<{slot: number; position: number} | null>(null);
  const audio = useRef<HTMLAudioElement>(null);
  const search = useRef<HTMLInputElement>(null);
  const activeId = useRef<string | null>(null);
  const editLock = useRef(false);
  const selection = useRef(0);
  const pendingPlayback = useRef<string | null>(null);
  const wantsPlayback = useRef(false);
  const audioGeneration = useRef(0);
  const playbackPreparing = useRef(false);
  const playbackFallback = useRef(false);
  const pendingSeek = useRef<number|null>(null);
  const [playerLoading,setPlayerLoading] = useState(false);
  const drag = useRef<number | null>(null);
  const waveElement = useRef<HTMLDivElement>(null);
  const [waveWidth,setWaveWidth]=useState(1000);
  const scrub = useRef<{pointer: number; x: number; position: number; wasPlaying: boolean; moved: boolean} | null>(null);
  const scrubFrame=useRef(0),scrubPending=useRef<number|null>(null),scratchAt=useRef(0),wheelScrubbing=useRef(false);
  const scratch = useRef<{context: AudioContext; node:AudioWorkletNode; path: string} | null>(null);
  const scratchLoading = useRef<{path:string; promise:Promise<void>} | null>(null);
  const scratchGeneration = useRef(0);
  const loadScratch = (path: string) => {
    if (scratch.current?.path === path || scratchLoading.current?.path === path) return;
    const generation = ++scratchGeneration.current;
    const context = new AudioContext();
    const promise = fetch(convertFileSrc(path)).then(r => { if (!r.ok) throw new Error('Audio unavailable'); return r.arrayBuffer(); }).then(b => context.decodeAudioData(b)).then(async buffer => {
      await context.audioWorklet.addModule(new URL('./scrub-processor.js',import.meta.url).href);
      if (generation !== scratchGeneration.current) { void context.close(); return; }
      if (scratch.current) void scratch.current.context.close();
      const node=new AudioWorkletNode(context,'crateloom-scrub',{outputChannelCount:[buffer.numberOfChannels]});
      const channels=Array.from({length:buffer.numberOfChannels},(_,c)=>buffer.getChannelData(c).slice());
      node.port.postMessage({type:'load',channels},channels.map(c=>c.buffer));node.connect(context.destination);
      scratch.current = {context, node, path};
    }).catch(() => { void context.close(); if (generation === scratchGeneration.current) setNotice('Scratch preview could not load for this file; dragging still seeks.'); }).finally(() => { if (generation === scratchGeneration.current) scratchLoading.current = null; });
    scratchLoading.current = {path, promise};
  };
  const scratchSound = (from: number, to: number, elapsed=.016) => {
    const item=scratch.current;if(!item || item.path!==active?.path)return;
    if(item.context.state==='suspended')void item.context.resume();
    item.node.port.postMessage({type:'move',from,speed:(to-from)/Math.max(.005,elapsed)});
  };
  const stopScratch=()=>scratch.current?.node.port.postMessage({type:'stop'});
  const previewScrub=(value:number)=>{
    scrubPending.current=value;
    if(scrubFrame.current)return;
    const id=activeId.current;
    scrubFrame.current=requestAnimationFrame(now=>{
      scrubFrame.current=0;const next=scrubPending.current;scrubPending.current=null;
      if(next==null || activeId.current!==id)return;
      scratchSound(positionRef.current,next,scratchAt.current?(now-scratchAt.current)/1000:1/60);
      scratchAt.current=now;setPosition(next);
    });
  };
  const commitScrub=()=>{
    if(scrubFrame.current)cancelAnimationFrame(scrubFrame.current);
    scrubFrame.current=0;const next=scrubPending.current ?? positionRef.current;scrubPending.current=null;scratchAt.current=0;
    stopScratch();seek(next);return next;
  };

  useEffect(() => { return () => { scratchGeneration.current++; scratchLoading.current = null;cancelAnimationFrame(scrubFrame.current);scrubFrame.current=0;scrubPending.current=null;scratchAt.current=0;wheelScrubbing.current=false;scrub.current=null; if (scratch.current) { void scratch.current.context.close(); scratch.current = null; } }; }, [active?.path]);
  useEffect(() => {
    if (!playing) return;
    let frame = 0;
    const tick = (now:number) => { const a = audio.current; if (a && !a.paused) {
      if (loopRef.current && a.currentTime >= loopRef.current.end) {a.currentTime=loopRef.current.start;playbackClock.current.reset(a.currentTime,now);}
      const value=playbackClock.current.sample(a.currentTime,now,a.playbackRate,!a.seeking && a.readyState>=3);
      setPosition(Math.max(0,Math.min(a.duration || Infinity,value)));
    } frame=requestAnimationFrame(tick); };
    frame=requestAnimationFrame(tick); return () => cancelAnimationFrame(frame);
  }, [playing]);
  useEffect(() => {
    const el=waveElement.current; if (!el || !editor) return;
    const resize=()=>setWaveWidth(el.clientWidth);resize();const observer=new ResizeObserver(resize);observer.observe(el);
    let base=zoomRef.current, gesturing=false, wheelTimer=0, wheelActive=false, resume=false;
    const finish=()=>{if(!wheelActive)return;clearTimeout(wheelTimer);commitScrub();wheelActive=false;wheelScrubbing.current=false;if(resume)void audio.current?.play().catch(e=>setNotice(String(e)));};
    const wheelInput=new WheelInput();
    const wheel=(e:WheelEvent) => {
      e.preventDefault();if(gesturing)return;
      if(wheelInput.action(e,wheelModeRef.current)==='zoom'){finish();setZoom(z=>Math.max(1,Math.min(128,z*Math.exp(-e.deltaY*.003))));return;}
      if(!audio.current || !active || active.missing)return;
      if(!wheelActive){wheelActive=true;wheelScrubbing.current=true;resume=!audio.current.paused;pauseAudio();loadScratch(active.path);}
      const delta=Math.abs(e.deltaX)>Math.abs(e.deltaY)?e.deltaX:e.deltaY;
      const from=scrubPending.current ?? positionRef.current, to=Math.max(0,Math.min(active.duration,from+delta/el.clientWidth*active.duration/zoomRef.current));
      previewScrub(to);
      clearTimeout(wheelTimer);wheelTimer=window.setTimeout(finish,120);
    };
    const begin=(e:Event) => { e.preventDefault();finish(); gesturing=true; base=zoomRef.current; };
    const change=(e:Event) => { e.preventDefault(); const scale=(e as Event & {scale:number}).scale; if(Number.isFinite(scale)) setZoom(Math.max(1,Math.min(128,base*scale))); };
    const end=(e:Event) => { e.preventDefault(); gesturing=false; };
    el.addEventListener('gestureend',end,{passive:false}); el.addEventListener('wheel',wheel,{passive:false}); el.addEventListener('gesturestart',begin,{passive:false}); el.addEventListener('gesturechange',change,{passive:false});
    return () => {observer.disconnect();clearTimeout(wheelTimer);if(wheelActive){cancelAnimationFrame(scrubFrame.current);scrubFrame.current=0;scrubPending.current=null;wheelScrubbing.current=false;}stopScratch();el.removeEventListener('wheel',wheel);el.removeEventListener('gesturestart',begin);el.removeEventListener('gesturechange',change);el.removeEventListener('gestureend',end);};
  }, [editor, active?.id]);
  const offsetSeconds = (active?.grid_offset_us || 0) / 1e6;
  const beats = React.useMemo(() => {
    if (!detail || !active) return [];
    return completeBeats(detail.beats,active.duration,active.grid_bpm || detail.tempo,active.grid_origin_us!=null?active.grid_origin_us/1e6:null,offsetSeconds);
  }, [detail,offsetSeconds,active?.duration,active?.grid_bpm,active?.grid_origin_us]);
  const anchorIndex = React.useMemo(() => {
    if(!beats.length)return -1;
    if(active?.grid_anchor_us==null)return 0;
    const anchor=(active.grid_anchor_us+(active.grid_offset_us || 0))/1e6,index=lowerBound(beats,anchor-.001);
    return index<beats.length && Math.abs(beats[index]-anchor)<=.001 ? index : -1;
  },[beats,active?.grid_anchor_us,active?.grid_offset_us]);

  useEffect(() => { loopRef.current = loop; }, [loop]);
  useEffect(() => { setGridHistory([]);setGridFuture([]); }, [active?.id,active?.detail_analysis?.cache_key]);
  useEffect(() => { if ((snap === 'beat' && !beats.length) || (snap === 'bar' && anchorIndex < 0)) setSnap('off'); }, [snap, beats, anchorIndex]);
  useEffect(() => { document.documentElement.dataset.theme = theme; localStorage.setItem('theme', theme); }, [theme]);
  const refresh = async () => {const revision=orderRevision.current;const loaded=await call<Track[]>('library.list');setTracks(loaded);const next=await call<Playlist[]>('playlists.list');if(revision===orderRevision.current && !orderingLock.current)setPlaylists(next);setActive(current=>{const match=loaded.find(t=>t.id===current?.id);return current && match ? {...current,missing:match.missing,title:match.title,artist:match.artist,metadata:match.metadata} : current;});};
  const metadataBatch=useMetadataBatch(refresh);
  useEffect(() => { refresh().catch(e => setNotice(String(e))); }, []);
  useEffect(() => {
    void call<{semantic_analysis:boolean;fast_sections:boolean;structure_max_duration:number}>('handshake').then(info => {
      if (info.fast_sections || info.semantic_analysis) setStructureLimit(info.structure_max_duration);
      setDetailedSectionsAvailable(info.semantic_analysis);
    }).catch(e => setNotice(String(e)));
  }, []);
  const queueStructure = useCallback((id:string) => {
    if (!structureLimit) return;
    const running = currentJob.current;
    if (running?.kind === 'structure' && running.track_id === id && ['queued','running'].includes(running.state)) return;
    setStructureQueue(queue => queue.includes(id) ? queue : [...queue,id]);
  }, [structureLimit]);
  useEffect(() => {
    if (active && !active.missing && !active.structure_analysis && active.duration <= structureLimit) queueStructure(active.id);
  }, [active?.id,active?.missing,active?.duration,active?.structure_analysis,editor,structureLimit,queueStructure]);
  const refreshAcquisitions=async()=>{
    try{
      const [downloads,pending]=await Promise.all([call<AcquisitionJob[]>('acquisitions.list'),call<string[]>('sets.pending_analysis')]);
      const next=pending.filter(id=>!handledSetAnalysis.current.has(id));
      next.forEach(id=>handledSetAnalysis.current.add(id));
      if(next.length)setAutoQueue(q=>[...new Set([...q,...next])]);
      setAcquisitions(downloads);setAcquisitionError('');
    }catch(e){setAcquisitionError(String(e));throw e;}
  };
  useEffect(()=>{void refreshAcquisitions().catch(()=>{});},[]);
  const hasActiveDownloads = acquisitions.some(j=>activeDownloads.includes(j.state));
  useEffect(()=>{
    const timer=window.setInterval(()=>void refreshAcquisitions().catch(()=>{}),acquisitionError?5000:hasActiveDownloads?800:2500);
    return()=>clearInterval(timer);
  },[hasActiveDownloads,acquisitionError]);
  useEffect(()=>{
    const completed=acquisitions.filter(j=>j.state==='completed' && j.track_id && !handledAcquisitions.current.has(j.id));
    if(!completed.length)return;
    completed.forEach(j=>handledAcquisitions.current.add(j.id));
    // Also recover an imported download whose analysis handoff was missed on exit.
    void (async()=>{
      await refresh();
      const results=await Promise.allSettled(completed.map(j=>call<Track>('track.get',{id:j.track_id})));
      const ids:string[]=[];
      results.forEach((result,i)=>{if(result.status==='fulfilled'){const t=result.value;if(!handledSetAnalysis.current.has(t.id) && !t.missing && t.duration<=900 && t.detail_analysis?.version!=='librosa-0.11.0-rgb-5')ids.push(t.id);}else{handledAcquisitions.current.delete(completed[i].id);setAcquisitionError(String(result.reason));}});
      setAutoQueue(q=>[...new Set([...q,...ids])]);
    })().catch(e=>{completed.forEach(j=>handledAcquisitions.current.delete(j.id));setAcquisitionError(String(e));});
  },[acquisitions]);
  const queueAcquisition=async(review:AcquisitionReview)=>{await call('acquisitions.enqueue',review);await refreshAcquisitions();};
  const cancelAcquisition=async(id:string)=>{await call('acquisitions.cancel',{id});await refreshAcquisitions();};
  const retryAcquisition=async(id:string)=>{await call('acquisitions.retry',{id});await refreshAcquisitions();};

  useEffect(()=>{call<ImportJob|null>('imports.latest').then(j=>{if(j){autoImport.current=['scanning','running'].includes(j.state);setBatch({...j,cursor:0});setImportItems([]);}}).catch(e=>setNotice(String(e)));},[]);
  useEffect(()=>{
    if(!batch || (!['scanning','running'].includes(batch.state) && batch.cursor>=batch.processed))return;
    let stopped=false;
    const timer=window.setTimeout(async()=>{
      try{
        const next=await call<ImportJob>('imports.get',{id:batch.id,cursor:batch.cursor});if(stopped)return;
        setImportItems(items=>[...items,...next.items]);setBatch(next);
        if(next.items.length){await refresh();if(autoImport.current){const ids=next.items.filter(i=>i.state==='imported' && i.track_id).map(i=>i.track_id!);setAutoQueue(q=>[...new Set([...q,...ids])]);}}
      }catch(e){setNotice(String(e));}
    },500);
    return()=>{stopped=true;clearTimeout(timer);};
  },[batch]);
  const startBatch=async(params:object)=>{try{const next=await call<ImportJob>('imports.start',params);autoImport.current=true;setImportItems(next.items);setBatch(next);setEditor(false);setDiscoverOpen(false);if(next.items.length){await refresh();setAutoQueue(q=>[...new Set([...q,...next.items.filter(i=>i.state==='imported' && i.track_id).map(i=>i.track_id!)])]);}}catch(e){setNotice(String(e));}};
  const importFolder=async()=>{try{const folder=await open({directory:true,multiple:false});if(folder)await startBatch({folder});}catch(e){setNotice(String(e));}};
  const relink=async(t:Track)=>{
    if(editLock.current)return;
    try{
      const path=await open({multiple:false,filters:[{name:'Audio',extensions:['mp3','wav','aiff','aif','m4a','flac','ogg']}]});if(!path)return;
      editLock.current=true;setSaving(true);
      const updated=await call<Track>('track.relink',{id:t.id,path});if(activeId.current===updated.id){pauseAudio();setPosition(0);setLoop(null);setActive(updated);}
      await refresh();setNotice('File relinked. Cues and analysis preserved.');
    }catch(e){setNotice(String(e));}finally{editLock.current=false;setSaving(false);}
  };

  const savePlaylist=async(name:string,ids:string[],id?:string)=>{
    try{const saved=await call<Playlist>('playlists.save',{id,name,track_ids:ids});await refresh();setPlaylistId(saved.id);setPlaylistName('');setEditor(false);setDiscoverOpen(false);}catch(e){setNotice(String(e));}
  };
  const movePlaylistTrack=(id:string,direction:number)=>{if(!currentPlaylist)return;const ids=currentPlaylist.track_ids,at=ids.indexOf(id),target=at+direction;if(at<0||target<0||target>=ids.length)return;void reorderPlaylist(id,ids[target]);};
  const reorderPlaylist=async(id:string,target:string)=>{
    if(!currentPlaylist || orderingLock.current || id===target)return;
    const playlist=currentPlaylist,ids=[...playlist.track_ids],from=ids.indexOf(id),to=ids.indexOf(target);
    if(from<0||to<0)return;
    ids.splice(to,0,ids.splice(from,1)[0]);orderingLock.current=true;orderRevision.current++;setOrdering(true);
    setPlaylists(items=>items.map(item=>item.id===playlist.id?{...item,track_ids:ids}:item));
    try{const saved=await call<Playlist>('playlists.save',{id:playlist.id,name:playlist.name,track_ids:ids});setPlaylists(items=>items.map(item=>item.id===saved.id?saved:item));}
    catch(e){setNotice(String(e));setPlaylists(items=>items.map(item=>item.id===playlist.id?playlist:item));}
    finally{orderingLock.current=false;setOrdering(false);}
  };
  const playlistReorder=useTrackReorder('playlist',(id,target)=>void reorderPlaylist(id,target),ordering);
  const choose = async (t: Pick<Track, 'id'>, edit = false, preview = false) => {
    if (editLock.current || busy) return;
    const request = ++selection.current;
    pendingPlayback.current = null;
    try {
      const loaded = await call<Track>(edit ? 'track.get' : 'track.playback', { id: t.id });
      if (request !== selection.current) return;
      if (preview && loaded.missing) {
        setNotice('Audio file is missing. Relink it in the library.');
        return;
      }
      const changed = activeId.current !== loaded.id;
      activeId.current = loaded.id;
      if (preview) pendingPlayback.current = loaded.id;
      else { setEditor(edit); setDiscoverOpen(false); }
      setActive(loaded); setHistory([]); setFuture([]); setSaved(''); setZoom(16);
      if (changed) { setGridHistory([]);setGridFuture([]);setSectionsOpen(false);tempCue.current=0;setTempCuePosition(0); setPosition(0); setLoop(null); setEditingSection(null);  }
      if (!loaded.missing && loaded.duration<=900 && loaded.detail_analysis?.version !== 'librosa-0.11.0-rgb-5') setAutoQueue(q=>q.includes(loaded.id)?q:[...q,loaded.id]);
    } catch (e) { setNotice(String(e)); }
  };
  React.useLayoutEffect(() => {
    const media=audio.current;
    const generation=++audioGeneration.current;
    playbackPreparing.current=false;playbackFallback.current=false;pendingSeek.current=null;
    wantsPlayback.current=!!active && pendingPlayback.current===active.id;
    setPlayerLoading(false);setPlaying(false);
    if(!media)return;
    media.pause();media.removeAttribute('src');media.load();
    if(!active || active.missing)return;
    if(!['aac','mp3','pcm_s16le'].includes(active.codec)) {void preparePlayback();return;}
    media.src=convertFileSrc(active.path);media.load();
    return()=>{if(audioGeneration.current===generation)audioGeneration.current++;};
  },[active?.id,active?.path,active?.missing]);
  useEffect(() => {
    if (!active || pendingPlayback.current !== active.id) return;
    pendingPlayback.current = null;
    playAudio();
  }, [active]);
  useEffect(() => {
    let cancelled = false;
    setDetail(null);
    if (active?.detail_analysis?.cache_path) {
      fetch(convertFileSrc(active.detail_analysis.cache_path), {cache:'no-store'}).then(r => { if (!r.ok) throw new Error('Analysis cache could not be read'); return r.json() as Promise<AnalysisData>; }).then(data => { if (!cancelled) setDetail(data); }).catch(e => { if (!cancelled) setNotice(String(e)); });
    }
    return () => { cancelled = true; };
  }, [active?.detail_analysis?.cache_path, active?.detail_analysis?.revision]);
  const startAnalysis = async () => {
    if (!active || active.missing || queueStarting.current || job?.state === 'running') return;
    queueStarting.current=true;
    try { setJob(await call<Job>('analysis.enqueue', {id: active.id, force: !!detail})); }
    catch (e) { setNotice(String(e)); }
    finally { queueStarting.current=false; }
  };
  const startStructure = async (mode:'fast'|'model'='fast') => {
    if (!active || active.missing || queueStarting.current || job?.state === 'running') return;
    queueStarting.current=true;
    try {  setJob(await call<Job>('structure.enqueue', {id:active.id,force:!!active.structure_analysis,mode})); }
    catch (e) { setNotice(String(e)); }
    finally { queueStarting.current=false; }
  };
  useEffect(() => {
    if (queueStarting.current || (job && ['running','queued'].includes(job.state))) return;
    const structure = autoQueue.length ? undefined : structureQueue[0];
    const id = structure ?? autoQueue[0];
    if (!id) return;
    queueStarting.current=true;
    const request = structure ? call<Job|null>('structure.enqueue',{id,automatic:true,mode:'fast'}) : call<Job>('analysis.enqueue',{id});
    void request.then(next=>{if(next)setJob(next);}).catch(e=>setNotice(`Automatic analysis failed: ${String(e)}`)).finally(()=>{
      queueStarting.current=false;
      (structure ? setStructureQueue : setAutoQueue)(q=>q.filter(item=>item!==id));
    });
  }, [structureQueue, autoQueue, job?.id, job?.state]);
  useEffect(() => {
    if (!job || !['running', 'queued'].includes(job.state)) return;
    let stopped = false;
    const timer = window.setInterval(async () => {
      try {
        const next = await call<Job>('jobs.get', {id: job.id});
        if (stopped) return;
        setJob(next);
      } catch (e) { if (!stopped) { setNotice(String(e)); setJob(null); } }
    }, 600);
    return () => { stopped = true; window.clearInterval(timer); };
  }, [job?.id, job?.state]);
  useEffect(() => {
    if (job?.state !== 'succeeded') return;
    if (job.kind === 'beat') void refresh().catch(e=>setNotice(String(e)));
    if (activeId.current !== job.track_id) return;
    void call<Track>('track.get', {id:job.track_id}).then(t => setActive(current => {
      if (current?.id !== t.id) return current;
      if (job.kind === 'structure') return {...current,structure_analysis:t.structure_analysis,sections:t.sections};
      return {...current,detail_analysis:t.detail_analysis,cues:current.cues.length?current.cues:t.cues,grid_bpm:t.grid_bpm,grid_origin_us:t.grid_origin_us,fixed_grid_key:t.fixed_grid_key,grid_cache_key:t.grid_cache_key,offset_cache_key:t.offset_cache_key,grid_anchor_us:current.grid_cache_key === t.detail_analysis?.cache_key ? current.grid_anchor_us : t.grid_anchor_us,grid_offset_us:current.offset_cache_key === t.detail_analysis?.cache_key ? current.grid_offset_us : t.grid_offset_us};
    })).catch(e=>setNotice(String(e)));
  }, [job?.id, job?.state]);
  useEffect(() => { if(job?.state === 'failed') setNotice(`${job.kind === 'beat' ? 'BPM/RGB' : 'Section'} analysis failed: ${job.error || 'Unknown error'}`); }, [job?.id, job?.state]);
  const cancelAnalysis = async () => {
    if (!job) return;
    try { setJob(await call<Job>('jobs.cancel', {id: job.id})); } catch (e) { setNotice(String(e)); }
  };
  const importFile = async () => {
    if (editLock.current || busy || batchRunning) return;
    try {
      const path = await open({ multiple: true, filters: [{ name: 'Audio', extensions: ['mp3', 'wav', 'aiff', 'aif', 'm4a', 'flac', 'ogg'] }] });
      if (!path) return;
      const paths=Array.isArray(path)?path:[path];if(paths.length>1){await startBatch({paths});return;}
      setBusy('Importing…');
      const t = await call<Track & {duplicate?:boolean}>('library.import', { path:paths[0] });
      if(t.duplicate)setNotice(t.missing?'This audio is already in your library with a missing path. Use Relink to locate it.':'This audio is already in your library.');
      await refresh();
      // Select directly: the import already returned the track and granted asset access.
      ++selection.current; activeId.current = t.id;
      setGridHistory([]);setGridFuture([]);setSectionsOpen(false);tempCue.current=0;setTempCuePosition(0); setActive(t); setDiscoverOpen(false); setPosition(0); setLoop(null); setEditingSection(null);  setEditor(false); setHistory([]); setFuture([]); setSaved('');
      setBusy('');
      if (t.detail_analysis?.version !== 'librosa-0.11.0-rgb-5' && !t.missing && t.duration<=900) setAutoQueue(q=>q.includes(t.id)?q:[...q,t.id]);
    } catch (e) { setNotice(String(e)); setBusy(''); }
  };
  const persist = async (cues: Cue[], mode: 'edit' | 'undo' | 'redo' = 'edit') => {
    if (!active || editLock.current || busy) return false;
    editLock.current = true; setSaving(true); setSaved('Saving…');
    const previous = active.cues;
    try {
      const updated = await call<Track>('cues.save', { id: active.id, cues });
      if (activeId.current === active.id) setActive(updated);
      if (mode === 'edit') { setHistory(h => [...h, previous]); setFuture([]); }
      if (mode === 'undo') { setHistory(h => h.slice(0, -1)); setFuture(f => [...f, previous]); }
      if (mode === 'redo') { setFuture(f => f.slice(0, -1)); setHistory(h => [...h, previous]); }
      setSaved('Saved');
      try { await refresh(); } catch (e) { setNotice(`Cue saved, but the library could not refresh: ${String(e)}`); }
      return true;
    } catch (e) { setSaved('Save failed'); setNotice(String(e)); return false; }
    finally { editLock.current = false; setSaving(false); }
  };
  const generateCues = async () => {
    if(!active || editLock.current || busy)return;
    const previous=active.cues, id=active.id;
    editLock.current=true;setSaving(true);setSaved('Finding cues…');
    try {
      const result=await call<{track:Track;added:number;has_sections:boolean;free_slots:number}>('cues.auto',{id});
      if(activeId.current===id){setActive(result.track);if(result.added){setHistory(h=>[...h,previous]);setFuture([]);}}
      setSaved(result.added ? `${result.added} cues added` : 'No new cues found');
      if(!result.free_slots)setNotice('All hot cue slots are occupied. Remove a cue to make room.');
      else if(!result.has_sections)setNotice(result.added ? 'Added a cue at the first beat. Analyze sections to find more cue points.' : 'No new cue positions found. Analyze sections to find more.');
      await refresh();
    } catch(e){setSaved('Cue analysis failed');setNotice(String(e));}
    finally {editLock.current=false;setSaving(false);}
  };
  const saveTrackField = async (method: string, params: object) => {
    if (!active || editLock.current || busy) return false;
    editLock.current = true; setSaving(true); setSaved('Saving…');
    try {
      const updated = await call<Track>(method, {id:active.id, ...params});
      if (activeId.current === updated.id) setActive(updated);
      setSaved('Saved'); return true;
    } catch (e) { setSaved('Save failed'); setNotice(String(e)); return false; }
    finally { editLock.current = false; setSaving(false); }
  };
  const snapTime = (seconds: number) => {
    if (snap === 'off' || !beats.length) return seconds;
    let choices = beats;
    if (snap === 'bar') {
      if (anchorIndex < 0) return seconds;
      choices = choices.filter((_,i)=>(i-anchorIndex)%4===0);
    }
    if (!choices.length) return seconds;
    const nearest = choices.reduce((best,b)=>Math.abs(b-seconds)<Math.abs(best-seconds)?b:best);
    return Math.abs(nearest-seconds)<=.1 ? nearest : seconds;
  };
  const cueAt = (slot: number) => active?.cues.find(c => c.slot === slot);
  const updateCue = (slot: number, patch: Partial<Cue>) => {
    if (active) void persist(active.cues.map(c => c.slot === slot ? { ...c, ...patch } : c));
  };
  const setCue = (slot: number) => {
    if (!active || active.missing) return;
    const p = Math.min(snapTime(position), Math.max(0, active.duration - .001));
    void persist([...active.cues.filter(c => c.slot !== slot), { slot, position_us: Math.round(p * 1e6), label: `Cue ${slotName(slot)}`, color: cueColors[slot] }]);
  };
  const seek = (v: number, play = false) => {
    if (!audio.current || !active || active.missing) return;
    const p = Math.min(active.duration, Math.max(0, v));
    cancelAnimationFrame(scrubFrame.current);scrubFrame.current=0;scrubPending.current=null;
    if(audio.current.readyState>=1)audio.current.currentTime = p;else pendingSeek.current=p;
    playbackClock.current.reset(p,performance.now());setPosition(p);
    if (play) playAudio();
  };
  const jump=(direction:number)=>{
    if(!active || active.missing || beats.length<2)return;
    const from=positionRef.current,target=jumpBeats(beats,from,direction*beatJump,active.duration);
    const current=loopRef.current;
    if(current){
      const offset=target-from,width=current.end-current.start;
      const start=Math.max(0,Math.min(active.duration-width,current.start+offset));
      loopRef.current={start,end:start+width};setLoop(loopRef.current);
      seek(Math.max(start,Math.min(start+width,from+start-current.start)));
    }else seek(target);
  };
  const preparePlayback = async () => {
    if(!active || playbackPreparing.current || playbackFallback.current)return;
    const generation=audioGeneration.current,id=active.id;
    playbackPreparing.current=true;playbackFallback.current=true;setPlayerLoading(true);
    pendingSeek.current=positionRef.current;
    try {
      let task=await call<{id:string;state:string;error:string|null;result:{path:string}|null}>('playback.prepare',{id});
      while(task.state==='running' && generation===audioGeneration.current){
        await new Promise(resolve=>window.setTimeout(resolve,200));
        if(generation!==audioGeneration.current)return;
        task=await call('metadata.job',{id:task.id});
      }
      if(generation!==audioGeneration.current)return;
      if(!task.result)throw new Error(task.error || 'Audio could not be prepared');
      const media=audio.current;
      if(media){media.src=convertFileSrc(task.result.path);media.load();}
    } catch(error) {if(generation===audioGeneration.current){wantsPlayback.current=false;setNotice(`Playback could not load this file: ${String(error)}`);}}
    finally {if(generation===audioGeneration.current){playbackPreparing.current=false;setPlayerLoading(false);}}
  };
  const playAudio = () => {
    wantsPlayback.current=true;
    const media=audio.current,generation=audioGeneration.current;
    if(!media || playbackPreparing.current || !media.getAttribute('src'))return;
    if(media.ended){media.currentTime=0;setPosition(0);}
    void media.play().catch(error => {
      if(generation!==audioGeneration.current || !wantsPlayback.current)return;
      if(error.name==='NotSupportedError'){void preparePlayback();return;}
      if(error.name!=='AbortError'){wantsPlayback.current=false;setNotice('Playback could not start. Select the track again or relink its file.');}
    });
  };
  const pauseAudio = () => {wantsPlayback.current=false;audio.current?.pause();};
  const toggle = () => {
    if (!audio.current) return;
    if (!active || active.missing) { if (!editor) advancePlayback(1); return; }
    if (cuePreview.current) { cueKeepPlaying.current=true; cuePreview.current=false; return; }
    if (audio.current.paused && !wantsPlayback.current) playAudio(); else {wantsPlayback.current=false;pendingPlayback.current=null;audio.current.pause();}
  };
  const beginScrub=(e:React.PointerEvent<HTMLDivElement>)=>{
    if(e.button!==0 || !active || active.missing)return;
    e.preventDefault();e.currentTarget.setPointerCapture(e.pointerId);
    scrub.current={pointer:e.pointerId,x:e.clientX,position:positionRef.current,wasPlaying:!!audio.current && !audio.current.paused,moved:false};
    pauseAudio();loadScratch(active.path);
  };
  const moveScrub=(e:React.PointerEvent<HTMLDivElement>)=>{
    const d=scrub.current;if(!d || d.pointer!==e.pointerId || !active)return;
    const delta=e.clientX-d.x;if(Math.abs(delta)>2)d.moved=true;if(!d.moved)return;
    previewScrub(Math.max(0,Math.min(active.duration,d.position-delta/e.currentTarget.clientWidth*active.duration/zoomRef.current)));
  };
  const endScrub=(e:React.PointerEvent<HTMLDivElement>,cancelled=false)=>{
    const d=scrub.current;if(!d || d.pointer!==e.pointerId)return;
    if(d.moved)commitScrub();else if(!cancelled)seek(wavePosition(e.clientX,e.currentTarget));
    scrub.current=null;stopScratch();
    if(d.wasPlaying)void audio.current?.play().catch(err=>setNotice(String(err)));
  };
  const gridSnapshot = ():GridState => ({bpm:active?.grid_bpm ?? null,origin_us:active?.grid_origin_us ?? null,anchor_us:active?.grid_anchor_us ?? null,offset_us:active?.grid_offset_us || 0});
  const saveGrid = async (method:string,params:object) => {const before=gridSnapshot();if(await saveTrackField(method,params)){setGridHistory(h=>[...h,before]);setGridFuture([]);}};
  const restoreGrid = async (direction:'undo'|'redo') => {
    const stack=direction==='undo'?gridHistory:gridFuture, state=stack.at(-1);if(!state)return;const before=gridSnapshot();
    if(await saveTrackField('grid.restore',{state})){if(direction==='undo'){setGridHistory(h=>h.slice(0,-1));setGridFuture(f=>[...f,before]);}else{setGridFuture(f=>f.slice(0,-1));setGridHistory(h=>[...h,before]);}}
  };
  const setBpm = (bpm:number) => {
    if(!active || !detail || !Number.isFinite(bpm) || bpm<20 || bpm>400){setNotice('BPM must be between 20 and 400.');return;}
    const origin=active.grid_anchor_us != null ? active.grid_anchor_us : active.grid_origin_us != null ? active.grid_origin_us : beats.length ? Math.round((beats.reduce((a,b)=>Math.abs(b-position)<Math.abs(a-position)?b:a)-offsetSeconds)*1e6) : Math.round((position-offsetSeconds)*1e6);
    void saveGrid('grid.fixed',{bpm,origin_us:origin,bar_one:active.grid_anchor_us != null});
  };
  const cueDown = () => {
    const a=audio.current; if(!a || !active || active.missing)return;
    cueKeepPlaying.current=false;
    if(!a.paused){pauseAudio();seek(tempCue.current);cuePreview.current=false;return;}
    if(Math.abs(a.currentTime-tempCue.current)>.05){tempCue.current=a.currentTime;setTempCuePosition(a.currentTime);cuePreview.current=false;return;}
    cuePreview.current=true; seek(tempCue.current,true);
  };
  const cueUp = () => { if(cuePreview.current && !cueKeepPlaying.current){pauseAudio();seek(tempCue.current);} cuePreview.current=false; };
  const [copyExportAudio,setCopyExportAudio]=useState(false);
  const exportTracks = async (selector:{id:string}|{playlist_id:string}|{track_ids:string[]},name:string,copyAudio=false) => {
    if (editLock.current || busy) return;
    editLock.current=true;setBusy('Exporting…');
    try {
      const base=name.replace(/[<>:"/\\|?*\x00-\x1f]/g,'_').slice(0,100) || 'CrateLoom';
      const path=await save({defaultPath:base+'.xml',filters:[{name:'Rekordbox XML',extensions:['xml']}]});
      if(!path)return;
      type ExportTask={id:string;state:string;error:string|null;result:null|{xml:string;track_count:number;grid_count:number}};
      let next=await call<ExportTask>('export.enqueue',{...selector,path,copy_audio:copyAudio});
      while(next.state==='running'){
        await new Promise(resolve=>setTimeout(resolve,450));
        next=await call<ExportTask>('metadata.job',{id:next.id});
      }
      if(!next.result)throw Error(next.error || 'Export did not complete');
      setNotice(`Exported ${next.result.track_count} track${next.result.track_count===1?'':'s'} · ${next.result.grid_count} reviewed beatgrid${next.result.grid_count===1?'':'s'}.
${next.result.xml}`);
    }catch(e){setNotice(String(e));}finally{editLock.current=false;setBusy('');}
  };
  const exportTrack=()=>active?exportTracks({id:active.id},active.title):Promise.resolve();
  const keyboardHandler=useRef<(e:KeyboardEvent)=>void>(()=>{});
  keyboardHandler.current=(e:KeyboardEvent)=>{
      if(e.defaultPrevented || downloadsOpen)return;
      const el = e.target as HTMLElement;
      if(editor && e.shiftKey && ['ArrowLeft','ArrowRight'].includes(e.key) && !['INPUT','TEXTAREA','SELECT'].includes(el.tagName) && !el.isContentEditable && !el.closest('[role=dialog]')){e.preventDefault();jump(e.key==='ArrowRight'?1:-1);return;}
      if (['INPUT', 'TEXTAREA', 'SELECT', 'BUTTON'].includes(el.tagName) || el.isContentEditable || el.closest('[role=dialog]')) return;
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'f') { e.preventDefault(); search.current?.focus(); }
      else if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'o') { e.preventDefault(); void importFile(); }
      else if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'z' && editor && active && !saving && !busy) {
        e.preventDefault(); const next = e.shiftKey ? future.at(-1) : history.at(-1);
        if (next) void persist(next, e.shiftKey ? 'redo' : 'undo');
      } else if (e.code === 'Space') { e.preventDefault(); toggle(); }
      else if (editor && /^[1-8]$/.test(e.key)) { const cue = cueAt(Number(e.key) - 1); if (cue) seek(cue.position_us / 1e6, true); }
  };
  useEffect(()=>{const handler=(e:KeyboardEvent)=>keyboardHandler.current(e);window.addEventListener('keydown',handler);return()=>window.removeEventListener('keydown',handler);},[]);
  const libraryTracks=React.useMemo(()=>{if(!currentPlaylist)return tracks;const byId=new Map(tracks.map(t=>[t.id,t]));return currentPlaylist.track_ids.map(id=>byId.get(id)).filter((t):t is Track=>!!t);},[currentPlaylist,tracks]);
  const visible = React.useMemo(()=>libraryTracks.filter(t => (libraryFilter!=='missing' || t.missing) && `${t.title} ${t.artist}`.toLowerCase().includes(query.toLowerCase())),[libraryTracks,libraryFilter,query]);
  const metadataTargets=selectedIds.length?selectedIds:visible.map(track=>track.id);
  const metadataReviewTargets=selectedIds.length?selectedIds:metadataBatch.job?.items.filter(item=>!['queued','running','cancelled'].includes(item.state)).map(item=>item.id) || [];
  const playbackIds = JSON.stringify(setOpen && setPlaybackOrder.length ? setPlaybackOrder :
    libraryTracks.filter(track=>!track.missing).map(track=>track.id));
  const playbackOrder = React.useMemo(()=>{
    const ids=JSON.parse(playbackIds) as string[];
    if (!shuffle) return ids;
    const anchor=shuffleAnchor.current;
    const shuffled=ids.filter(id=>id!==anchor);
    for (let i=shuffled.length-1;i>0;i--) {
      const j=Math.floor(Math.random()*(i+1));
      [shuffled[i],shuffled[j]]=[shuffled[j],shuffled[i]];
    }
    return anchor && ids.includes(anchor) ? [anchor,...shuffled] : shuffled;
  },[playbackIds,shuffle]);
  const playbackIndex=active && !active.missing ? playbackOrder.indexOf(active.id) : -1;
  const adjacentTrack=(direction:number)=>{
    if (!playbackOrder.length) return undefined;
    if (playbackIndex < 0) return playbackOrder[direction > 0 ? 0 : playbackOrder.length-1];
    const next=playbackIndex+direction;
    if (repeat==='all') return playbackOrder[(next+playbackOrder.length)%playbackOrder.length];
    return playbackOrder[next];
  };
  const advancePlayback=(direction:number, ended=false)=>{
    if (ended && repeat==='one' && active && !active.missing) {seek(0,true);return;}
    if (direction<0 && active && positionRef.current>3) {seek(0);return;}
    const id=adjacentTrack(direction);
    if (id===active?.id) seek(0,true);
    else if (id) void choose({id},false,true);
  };
  useEffect(()=>{
    const media=audio.current;
    if(media){media.volume=volume;media.muted=muted;}
    localStorage.setItem('playerVolume',String(volume));
  },[volume,muted]);
  const runningDownloads=acquisitions.filter(job=>activeDownloads.includes(job.state)).length;
  const analysisWaiting=autoQueue.length+structureQueue.length;
  const locked = !!busy || saving;
  const waveStart = active ? position - active.duration / zoom / 2 : 0;
  const waveLength = active ? active.duration / zoom : 1;
  const firstVisibleBeat=lowerBound(beats,waveStart-1e-7),lastVisibleBeat=lowerBound(beats,waveStart+waveLength+1e-7);
  const currentBeat=beats.length ? Math.floor(beatCoordinate(beats,position)+1e-6) : 0;
  const beatPixels=waveWidth*(60/(active?.grid_bpm || detail?.tempo || 120))/waveLength;
  const gridStride=beatPixels>=5?1:beatPixels*4>=5?4:16;
  const wavePosition = (clientX: number, element: HTMLElement) => (active?positionRef.current-active.duration/zoomRef.current/2:0) + Math.max(0, Math.min(1, (clientX - element.getBoundingClientRect().left) / element.clientWidth)) * waveLength;
  useEffect(() => {
    const finishDrag = (event: PointerEvent) => {
      if (drag.current === null || !waveElement.current || !active) return;
      const slot = drag.current;
      const seconds = Math.min(active.duration - .001, wavePosition(event.clientX, waveElement.current));
      drag.current = null; setDragPreview(null);
      updateCue(slot, { position_us: Math.round(snapTime(seconds) * 1e6) });
    };
    window.addEventListener('pointerup', finishDrag, true);
    return () => window.removeEventListener('pointerup', finishDrag, true);
  });

  return <div className="app"><Titlebar expanded={sidebarVisible && !sidebarCollapsed} onToggle={()=>{if(!sidebarVisible){setSidebarVisible(true);setSidebarCollapsed(false);}else setSidebarCollapsed(value=>!value);}}/><Sidebar visible={sidebarVisible} collapsed={sidebarCollapsed} onCollapsedChange={setSidebarCollapsed}><div className="side-label">LIBRARY</div><button title="All Tracks" aria-label="All Tracks" className={`nav ${!playlistId && workspace==='library'?'active':''}`} onClick={() => {setPlaylistId(null);setEditor(false);setDiscoverOpen(false);}}><FolderOpen size={17}/> All Tracks <span>{tracks.length}</span></button><button title="Find Music" aria-label="Find Music" className={`nav ${discoverOpen?'active':''}`} onClick={()=>{setDiscoverOpen(true);setEditor(false);}}><Search size={17}/> Find Music</button><button title="Import DJ Set" aria-label="Import DJ Set" className={`nav ${setOpen?'active':''}`} onClick={()=>{setWorkspace('sets');setEditor(false);}}><ListMusic size={17}/> Import DJ Set</button><div className="playlist-nav"><div className="side-label">PLAYLISTS</div>{playlists.map(p=><button key={p.id} title={p.name} aria-label={p.name} className={`nav ${playlistId===p.id && workspace==='library'?'active':''}`} onClick={()=>{setPlaylistId(p.id);setEditor(false);setDiscoverOpen(false);}}><ListMusic size={15}/><span className="playlist-name">{p.name}</span><span>{p.track_ids.length}</span></button>)}<form onSubmit={e=>{e.preventDefault();void savePlaylist(playlistName,selectedIds);}}><input aria-label="New playlist name" placeholder="New playlist" maxLength={100} value={playlistName} onChange={e=>setPlaylistName(e.target.value)}/><button aria-label="Create playlist" disabled={!playlistName.trim()}><Plus size={14}/></button></form></div><div className="sidebar-bottom"><label htmlFor="appearance">Appearance</label><Select id="appearance" value={theme} onChange={e => setTheme(e.target.value)}><option value="system">System</option><option value="dark">Dark</option><option value="light">Light</option></Select></div></Sidebar><main>
    <header><div className="heading">{editor && <button className="icon" aria-label="Back to library" title="Back to library" onClick={() => transitionWorkspace(()=>setEditor(false))}><ArrowLeft size={19}/></button>}<h1>{editor ? 'Cue Editor' : discoverOpen ? 'Find Music' : setOpen ? 'Import DJ Set' : currentPlaylist?.name || 'All Tracks'}</h1></div><div className="header-actions"><button className="toolbar-download" title="Downloads" aria-label="Downloads"
      aria-haspopup="dialog" aria-expanded={downloadsOpen} onClick={()=>{setDownloadsOpen(true);void refreshAcquisitions().catch(()=>{});}}>
      <Download size={17}/>{runningDownloads>0 && <span>{runningDownloads}</span>}</button>{!editor && workspace==='library' && <label className="search"><Search size={16}/><input ref={search} aria-label="Search tracks" placeholder="Search tracks" value={query} onChange={e => setQuery(e.target.value)}/></label>}<button title="Import folder" aria-label="Import folder" disabled={locked || batchRunning} onClick={()=>void importFolder()}><FolderPlus size={16}/></button><button className="primary" title="Add music" aria-label="Add music" disabled={locked || batchRunning} onClick={() => void importFile()}><Plus size={17}/></button></div></header>
    {notice && <div className="notice" role="status"><span>{notice}</span><button onClick={() => setNotice('')}>Dismiss</button></div>}
    {batch && <div className="import-progress" role="status"><div><strong>{batch.state==='scanning'?'Scanning folder':batchRunning?'Importing':batch.state==='completed'?'Import complete':`Import ${batch.state}`}</strong><span>{batch.processed} / {batch.total} · {batch.imported} added · {batch.duplicates} duplicates · {batch.failed} failed</span><progress max={Math.max(1,batch.total)} value={batch.processed}/>{batch.current && <small>{batch.current}</small>}{batch.error && <small>{batch.error}</small>}</div>{batchRunning?<button onClick={()=>{void call('imports.cancel',{id:batch.id}).catch(e=>setNotice(String(e)));}}>Cancel</button>:<button aria-label="Dismiss import report" onClick={()=>setBatch(null)}><X size={16}/></button>}<Disclosure label={<> File results </>}>{importItems.map(item=><div key={item.sequence} className="import-result"><span title={item.path}>{item.path.split(/[\\/]/).at(-1)}</span><span>{item.state}{item.error?` · ${item.error}`:''}</span></div>)}</Disclosure></div>}
    {!editor && (analysisWaiting>0 || job?.state==='running') && <div className="analysis-queue-status" role="status"><Activity size={14}/><span>{job?.state==='running'?`${job.stage} · ${tracks.find(t=>t.id===job.track_id)?.title || 'Track'}`:'Analysis queued'} · {analysisWaiting} waiting</span>{job?.state==='running' && <button onClick={()=>void cancelAnalysis()}>Cancel current</button>}{analysisWaiting>0 && <button onClick={()=>{setAutoQueue([]);setStructureQueue([]);}}>Clear waiting</button>}</div>}
    <SetImportPanel hidden={!setOpen || editor} activeTrackId={active?.id ?? null} playing={playing}
      onPlay={(id,order)=>{
        setSetPlaybackOrder(order);
        if (active?.id === id && !active.missing) {
          selection.current++;
          pendingPlayback.current = null;
          toggle();
        } else void choose({id}, false, true);
      }}
      onUpdated={async()=>{await refresh();await refreshAcquisitions();}} onPlaylist={id=>{setPlaylistId(id);setWorkspace('library');}} onRetry={retryAcquisition}/>
    {editor && active ? <section className="editor"><div className="trackhead"><div className="cover large player-artwork">{active.metadata?.artwork_path?<img src={convertFileSrc(active.metadata.artwork_path)} alt="Track artwork"/>:<Music2 size={24}/>}</div><div className="track-details"><h2 className="player-title" title={active.title}>{active.title}</h2><p><span className="player-artist">{active.artist || 'Unknown artist'}</span> · {time(active.duration)} · {active.codec.toUpperCase()}{active.sample_rate ? ` · ${active.sample_rate} Hz` : ''}</p></div><div className="push"/><button disabled={locked} onClick={()=>setMetadataIds([active.id])}><Tags size={16}/> Metadata</button><button disabled={locked || active.missing || job?.state === 'running'} onClick={() => void startAnalysis()}><Activity size={16}/>{detail ? 'Analyze again' : 'Analyze beats & RGB'}</button><button disabled={locked || active.missing || job?.state === 'running'} onClick={() => void startStructure()} title="Estimate sections from local energy and texture changes">Fast sections</button>{detailedSectionsAvailable&&<button disabled={locked || active.missing || job?.state==='running'} onClick={()=>void startStructure('model')} title="Detailed vocal and song-structure model; slower">Detailed sections</button>}<button disabled={locked || active.missing} onClick={() => void exportTrack()}><Upload size={16}/> Export XML</button></div>
      {active.missing && <div className="analysis-note" role="alert">File missing. <button disabled={locked} onClick={()=>void relink(active)}><Link2 size={14}/> Relink file</button></div>}
      <Disclosure className="analysis-info" label={<> <Info size={14}/> Analysis info </>}><label className="wheel-setting">Scroll input <Select aria-label="Waveform scroll input" value={wheelMode} onChange={e=>{const mode=e.target.value as WheelMode;setWheelMode(mode);localStorage.setItem('waveWheelMode',mode);}}><option value="auto">Auto detect</option><option value="mouse">Mouse wheel</option><option value="trackpad">Trackpad</option></Select></label><p>Fast sections estimate energy and texture changes locally. Review the labels and boundaries. The optional detailed model uses stem separation and takes longer.</p></Disclosure>
      {job && (job.track_id===active.id || job.state==='running') && <div className="jobline" role="status"><span>{job.stage}{job.track_id!==active.id && ` · ${tracks.find(t=>t.id===job.track_id)?.title || 'Another track'}`}{job.error ? `: ${job.error}` : ''}</span>{job.state === 'running' && <button onClick={() => void cancelAnalysis()}>Cancel analysis</button>}</div>}
      <div className="wave-label"><span>FULL TRACK</span><span>{detail ? <label className="bpm-field"><input aria-label="BPM" type="number" min="20" max="400" step="0.01" key={`${active.id}-${active.grid_bpm}-${detail.tempo}`} defaultValue={(active.grid_bpm || detail.tempo)?.toFixed(2) || ''} placeholder="Set" onBlur={e=>{const n=Number(e.target.value);if(!Number.isFinite(n)||n<20||n>400){e.target.value=(active.grid_bpm || detail.tempo)?.toFixed(2) || '';setNotice('BPM must be between 20 and 400.');}else if(n!==(active.grid_bpm || detail.tempo))setBpm(n);}} onKeyDown={e=>{if(e.key==='Enter')e.currentTarget.blur();}}/> BPM</label> : 'Beat analysis not available'}</span></div>
      <div className="overview" role="slider" tabIndex={0} aria-label="Full track overview" aria-valuemin={0} aria-valuemax={active.duration} aria-valuenow={position} onClick={e => seek((e.clientX-e.currentTarget.getBoundingClientRect().left)/e.currentTarget.clientWidth*active.duration)} onKeyDown={e => {if(e.key==='ArrowRight') seek(position+5); if(e.key==='ArrowLeft') seek(position-5);}}>
        <WaveBars data={detail} fallback={active.analysis?.peaks || emptyPeaks} start={0} length={active.duration} duration={active.duration} overview/>
        {active.sections.map(section => <div className="section-band" key={section.id} title={`${section.label} (${section.origin === 'model-reviewed' ? 'reviewed suggestion' : 'manual'})`} style={{left:`${section.start_us / 1e6 / active.duration * 100}%`,width:`${(section.end_us-section.start_us) / 1e6 / active.duration * 100}%`,background:sectionColors[section.label] || sectionColors.Other}}>{section.label}</div>)}
        {active.cues.map(c => <span className="overview-cue" key={c.slot} style={{left:`${c.position_us / 1e6 / active.duration*100}%`, background:c.color || cueColors[c.slot]}}>{slotName(c.slot)}</span>)}
        <span className="temp-cue-arrow" role="img" aria-label={`Temporary cue at ${time(tempCuePosition)}`} title="Temporary cue" style={{left:`${tempCuePosition/active.duration*100}%`}}/>
        <div className="playhead" style={{left:`${position/active.duration*100}%`}}/>
      </div>
      <div className="wave-label detail-label"><span>DETAIL</span><span>{gridStride>1?'Zoom in for individual beats':'Two fingers to scrub · Pinch to zoom'}</span></div>
      <div className="wave-detail-layout"><div className="deck-transport"><button className="cue-transport" aria-label="Temporary cue" title="Set while paused; hold to preview; return while playing" disabled={active.missing} onPointerDown={e=>{e.currentTarget.setPointerCapture(e.pointerId);cueDown();}} onClick={e=>{if(e.detail===0){cueDown();cueUp();}}} onPointerUp={cueUp} onPointerCancel={cueUp} onKeyDown={e=>{if((e.key===' ' || e.key==='Enter') && !e.repeat){e.preventDefault();cueDown();}}} onKeyUp={e=>{if(e.key===' ' || e.key==='Enter'){e.preventDefault();cueUp();}}}>CUE</button><button className="play-transport" onClick={toggle} aria-label={playing ? "Pause" : "Play"} disabled={active.missing}>{playing ? <Pause size={21}/> : <Play size={21}/>}</button></div><div className="wave-detail-body">
      <div ref={waveElement} className="wave" role="slider" tabIndex={0} aria-label="Seek track waveform" aria-valuemin={0} aria-valuemax={active.duration} aria-valuenow={position}
        onKeyDown={e=>{if(e.key==='ArrowRight' || e.key==='ArrowLeft'){e.preventDefault();const direction=e.key==='ArrowRight'?1:-1;if(e.shiftKey)jump(direction);else seek(positionRef.current+direction);}}}
        onPointerDown={beginScrub} onPointerEnter={()=>{if(!active.missing)loadScratch(active.path);}} onPointerMove={moveScrub}
        onPointerUp={e=>endScrub(e)} onPointerCancel={e=>endScrub(e,true)} onLostPointerCapture={e=>endScrub(e,true)}>
        {active.analysis || detail ? <WaveBars data={detail} fallback={active.analysis?.peaks || emptyPeaks} start={waveStart} length={waveLength} duration={active.duration}/> : <div className="wave-empty">{busy || 'Analyze to see the waveform'}</div>}
        {beats.slice(firstVisibleBeat,lastVisibleBeat).map((beat,i)=>{
          const index=firstVisibleBeat+i,isBar=anchorIndex>=0 && (index-anchorIndex)%4===0;
          if(gridStride>1 && (index-anchorIndex)%gridStride!==0)return null;
          return <span key={index} className={`beat-line ${isBar?'bar-line':''}`} data-beat-index={index} style={{left:`${(beat-waveStart)/waveLength*100}%`}}>{isBar && beatPixels*4>=32?<span className="bar-number">{Math.floor((index-anchorIndex)/4)+1}</span>:null}</span>;
        })}
        {active.sections.filter(section=>section.end_us/1e6>waveStart && section.start_us/1e6<waveStart+waveLength).map(section=>{const left=Math.max(0,(section.start_us/1e6-waveStart)/waveLength*100), right=Math.min(100,(section.end_us/1e6-waveStart)/waveLength*100);return <div key={section.id} className="section-band detail-section" style={{left:`${left}%`,width:`${right-left}%`,background:sectionColors[section.label] || sectionColors.Other}}>{section.label}</div>;})}
        {tempCuePosition>=waveStart && tempCuePosition<=waveStart+waveLength && <span className="temp-cue-arrow" role="img" aria-label={`Temporary cue at ${time(tempCuePosition)}`} title="Temporary cue" style={{left:`${(tempCuePosition-waveStart)/waveLength*100}%`}}/>}
        {position >= waveStart && position <= waveStart + waveLength && <div className="playhead" style={{left: `${(position - waveStart) / waveLength * 100}%`}}/>}
        {active.cues.filter(c => c.position_us / 1e6 >= waveStart && c.position_us / 1e6 <= waveStart + waveLength).map(c => <button key={c.slot} className="marker" aria-label={`Move cue ${slotName(c.slot)}; use Seconds for precise editing`} title={`${c.label} ${time(c.position_us / 1e6)} · drag to move`} style={{left: `${((dragPreview?.slot === c.slot ? dragPreview.position : c.position_us / 1e6) - waveStart) / waveLength * 100}%`, background: c.color || cueColors[c.slot]}} disabled={locked || active.missing} onClick={e => e.stopPropagation()} onPointerDown={e => { e.stopPropagation(); drag.current = c.slot; audio.current?.pause(); setDragPreview({slot: c.slot, position: c.position_us / 1e6}); e.currentTarget.setPointerCapture(e.pointerId); }} onPointerMove={e => { const wave = e.currentTarget.parentElement; if (wave && drag.current === c.slot) setDragPreview({slot: c.slot, position: Math.min(active.duration - .001, wavePosition(e.clientX, wave))}); }} onPointerCancel={() => { drag.current = null; setDragPreview(null); }}>{slotName(c.slot)}</button>)}
      </div><div className="wave-times"><span>{time(Math.max(0,waveStart))}</span><span title={active.grid_anchor_us==null?"Estimated 4/4 bars. Set bar 1 to confirm the phrasing.":"Bar and beat in the confirmed 4/4 grid"}>{anchorIndex>=0?`Bar ${active.grid_anchor_us==null?'~':''}${Math.floor((currentBeat-anchorIndex)/4)+1}.${((currentBeat-anchorIndex)%4+4)%4+1}`:'Bar —'}</span><span>{time(Math.min(active.duration,waveStart + waveLength))}</span></div></div></div>
      <div className="editor-transport"><span className="time">{time(position)} / {time(active.duration)}</span><label className="zoom">Snap <Select aria-label="Cue snapping" value={snap} onChange={e=>setSnap(e.target.value)}><option value="off">Off</option><option value="beat" disabled={!beats.length}>Beat</option><option value="bar" disabled={anchorIndex < 0}>Bar</option></Select></label><div className="zoom"><button aria-label="Zoom out" onClick={()=>setZoom(z=>Math.max(1,z/1.5))}><ZoomOut size={16}/></button><span>{zoom.toFixed(1)}×</span><button aria-label="Zoom in" onClick={()=>setZoom(z=>Math.min(128,z*1.5))}><ZoomIn size={16}/></button></div><div className="beat-jump" role="group" aria-label="Beat jump"><button aria-label={`Jump backward ${beatJump} beats`} title={`Jump back ${beatJump} beats · Shift+Left`} disabled={active.missing || beats.length<2} onClick={()=>jump(-1)}><ChevronLeft size={16}/></button><Select aria-label="Beat jump length" value={beatJump} onChange={e=>{const value=Number(e.target.value);setBeatJump(value);localStorage.setItem('beatJump',String(value));}}>{[.25,.5,1,2,4,8,16,32,64].map(n=><option key={n} value={n}>{n===.25?'¼':n===.5?'½':n} {n<=1?'beat':'beats'}</option>)}</Select><button aria-label={`Jump forward ${beatJump} beats`} title={`Jump forward ${beatJump} beats · Shift+Right`} disabled={active.missing || beats.length<2} onClick={()=>jump(1)}><ChevronRight size={16}/></button></div><div className="push"/><span className="muted" role="status">{saved}</span><button disabled={locked || !history.length} onClick={() => { const prev = history.at(-1); if (prev) void persist(prev, 'undo'); }} aria-label="Undo cues" title="Undo cues"><Undo2 size={16}/></button><button disabled={locked || !future.length} onClick={() => { const next = future.at(-1); if (next) void persist(next, 'redo'); }} aria-label="Redo cues" title="Redo cues"><Redo2 size={16}/></button></div>
      <div className="review-tools">
        <label>Check before cue <Select value={preRoll} onChange={e=>setPreRoll(Number(e.target.value))}><option value="2">2 beats</option><option value="4">4 beats</option><option value="8">8 beats</option></Select></label>
        <label>Loop length <Select value={loopSeconds} onChange={e=>setLoopSeconds(Number(e.target.value))}><option value="2">2 seconds</option><option value="4">4 seconds</option><option value="8">8 seconds</option></Select></label>
        {loop ? <button onClick={()=>{loopRef.current=null;setLoop(null);}}>Exit loop</button> : <button disabled={active.missing || position+loopSeconds>active.duration} onClick={()=>{loopRef.current={start:position,end:position+loopSeconds};setLoop(loopRef.current); void audio.current?.play().catch(e=>setNotice(String(e)));}}>Loop from playhead</button>}
      </div>
      <div className="grid-tools" role="group" aria-label="Beat grid controls"><strong>GRID</strong>
        <button aria-label="Set grid beat at playhead" title="Move nearest grid beat to playhead" disabled={!beats.length || locked} onClick={()=>{const nearest=beats.reduce((a,b)=>Math.abs(b-position)<Math.abs(a-position)?b:a);const offset=Math.round((offsetSeconds+position-nearest)*1e6);void saveGrid('grid.offset',{offset_us:offset});}}><Crosshair size={17}/></button>
        <button aria-label="Set bar one at playhead" title="Confirm a fixed 4/4 bar 1 at the playhead for XML export" disabled={!(active.grid_bpm || detail?.tempo) || locked} onClick={()=>{const bpm=active.grid_bpm || detail?.tempo;if(bpm)void saveGrid('grid.fixed',{bpm,origin_us:Math.round((position-offsetSeconds)*1e6),bar_one:true});}}>1.1</button>
        <button aria-label="Shift grid left one millisecond" title="Shift grid left 1 ms" disabled={!detail || locked} onClick={()=>void saveGrid('grid.offset',{offset_us:active.grid_offset_us-1000})}><ChevronLeft size={17}/></button>
        <button aria-label="Shift grid right one millisecond" title="Shift grid right 1 ms" disabled={!detail || locked} onClick={()=>void saveGrid('grid.offset',{offset_us:active.grid_offset_us+1000})}><ChevronRight size={17}/></button>
        <button aria-label="Halve BPM" title="Halve BPM" disabled={!detail?.tempo || locked} onClick={()=>setBpm((active.grid_bpm || detail!.tempo!)/2)}>½</button><button aria-label="Double BPM" title="Double BPM" disabled={!detail?.tempo || locked} onClick={()=>setBpm((active.grid_bpm || detail!.tempo!)*2)}>×2</button>
        <button aria-label="Reset grid shift" title="Reset grid shift" disabled={!detail || locked} onClick={()=>void saveGrid('grid.offset',{offset_us:0})}><RotateCcw size={16}/></button>
        <button aria-label="Decrease BPM" title="Decrease BPM 0.1" disabled={!detail?.tempo || locked} onClick={()=>setBpm((active.grid_bpm || detail!.tempo!)-.1)}>−</button><button aria-label="Increase BPM" title="Increase BPM 0.1" disabled={!detail?.tempo || locked} onClick={()=>setBpm((active.grid_bpm || detail!.tempo!)+.1)}>+</button>
        <button title="Tap to set BPM" aria-label="Tap BPM" disabled={!detail?.tempo || locked} onClick={()=>{const now=performance.now();taps.current=[...taps.current.filter(t=>now-t<3000),now].slice(-5);if(taps.current.length>=3){const samples=taps.current;const bpm=60000*(samples.length-1)/(samples.at(-1)!-samples[0]);if(bpm>=20 && bpm<=400)setBpm(Math.round(bpm*100)/100);}}}>TAP</button>
        <button aria-label="Undo grid edit" title="Undo grid edit" disabled={locked || !gridHistory.length} onClick={()=>void restoreGrid('undo')}><Undo2 size={16}/></button><button aria-label="Redo grid edit" title="Redo grid edit" disabled={locked || !gridFuture.length} onClick={()=>void restoreGrid('redo')}><Redo2 size={16}/></button>
        <button aria-label="Reset grid to detected beats" title="Reset grid to detected beats" disabled={!detail || locked} onClick={()=>void saveGrid('grid.restore',{state:{bpm:null,origin_us:null,anchor_us:null,offset_us:0}})}><RotateCcw size={16}/></button>
        <span className="muted">{active.grid_anchor_us == null ? 'Set bar 1 to confirm bars' : `Bar 1 · ${time((active.grid_anchor_us+active.grid_offset_us)/1e6)}`}</span>
      </div>
      <div className="review-tools"><button disabled={locked || active.missing || (!detail && !active.sections.length)} onClick={()=>void generateCues()}>Auto cues</button><span className="muted"></span></div>
      <button onClick={()=>setSectionsOpen(true)}><SlidersHorizontal size={16}/> Edit sections · {active.sections.length}</button>
      <PanelPresence open={sectionsOpen}>{sectionsOpen && <div className="modal-backdrop" onClick={()=>setSectionsOpen(false)}><section className="section-window" role="dialog" aria-modal="true" aria-label="Edit sections" onClick={e=>e.stopPropagation()}><div className="section-window-head"><h2>Edit sections</h2><button aria-label="Close section editor" onClick={()=>setSectionsOpen(false)}><X size={16}/></button></div><p className="muted">Automatic labels can be edited or removed.</p><div className="review-tools"><label>Label <Select value={sectionLabel} onChange={e=>setSectionLabel(e.target.value)}>{['Intro','Verse','Chorus','Bridge','Break','Instrumental','Solo','Up','Down','Outro','Other'].map(x=><option key={x}>{x}</option>)}</Select></label><label>Start (seconds) <input type="number" min="0" step="0.01" value={sectionStart} onChange={e=>setSectionStart(e.target.value)}/></label><label>End (seconds) <input type="number" min="0" step="0.01" value={sectionEnd} onChange={e=>setSectionEnd(e.target.value)}/></label><button disabled={locked || active.missing} onClick={()=>{const start=Number(sectionStart),end=Number(sectionEnd);if(!sectionEnd || start<0 || end<=start || end>active.duration){setNotice('Enter a valid start and end within the track.');return;}const next=[...active.sections.filter(section=>section.id!==editingSection),{id:editingSection || crypto.randomUUID(),start_us:Math.round(start*1e6),end_us:Math.round(end*1e6),label:sectionLabel}];void saveTrackField('sections.save',{sections:next}).then(ok=>{if(ok){setEditingSection(null);setSectionStart('0');setSectionEnd('');}});}}>{editingSection?'Save section':'Add section'}</button>{editingSection && <button onClick={()=>{setEditingSection(null);setSectionStart('0');setSectionEnd('');}}>Cancel edit</button>}</div>{active.sections.map(section=><div className="section-row" key={section.id}><span>{section.label} · {time(section.start_us/1e6)}–{time(section.end_us/1e6)}{section.origin==='model'?' · automatic':section.origin==='model-reviewed'?' · reviewed suggestion':''}</span><div><button disabled={locked || active.missing} onClick={()=>{setEditingSection(section.id);setSectionLabel(section.label);setSectionStart(String(section.start_us/1e6));setSectionEnd(String(section.end_us/1e6));}}>Edit</button> <button disabled={locked || active.missing} onClick={()=>void saveTrackField('sections.save',{sections:active.sections.filter(x=>x.id!==section.id)})}>Remove</button></div></div>)}</section></div>}</PanelPresence>

      <div className="cue-title"><div><h2>Hot cues</h2><p>Click an empty slot to set a cue.</p></div><span>1–8 to play</span></div><div className="cues">{Array.from({length: 8}, (_, slot) => { const c = cueAt(slot); return <div className="cue" key={slot}><button className="cue-trigger" disabled={locked || active.missing} onClick={() => c ? seek(c.position_us / 1e6, true) : setCue(slot)}><span className="slot" style={{background: c?.color || cueColors[slot], color:'#16201d'}}>{slotName(slot)}</span><span>{c ? c.label : 'Add cue'}<small>{c ? time(c.position_us / 1e6) : 'At playhead'}</small></span></button>{c && <div className="cue-edit"><label>Color<input aria-label={`Cue ${slotName(slot)} color`} type="color" value={c.color || cueColors[slot]} disabled={locked} onChange={e => updateCue(slot, {color:e.target.value})}/></label><button disabled={active.missing} onClick={() => { const at = c.position_us/1e6; const before = beats.filter(b => b < at-.001); const from = before.length ? before[Math.max(0, before.length-preRoll)] : Math.max(0,at-5); seek(from,true); }}>Check</button><label>Label<input aria-label={`Cue ${slotName(slot)} label`} defaultValue={c.label} key={c.label} maxLength={120} disabled={locked} onBlur={e => { const label = e.target.value.trim(); if (label && label !== c.label) updateCue(slot, {label}); else e.target.value = c.label; }}/></label><label>Seconds<input aria-label={`Cue ${slotName(slot)} seconds`} type="number" min="0" max={active.duration} step="0.001" defaultValue={c.position_us / 1e6} key={c.position_us} disabled={locked} onBlur={e => { const n = Number(e.target.value); if (e.target.value !== '' && Number.isFinite(n) && n >= 0 && n < active.duration) { const us = Math.round(n * 1e6); if (us !== c.position_us) updateCue(slot, {position_us: us}); } else { e.target.value = String(c.position_us / 1e6); setNotice('Cue must be within the track.'); } }}/></label><button disabled={locked} onClick={() => void persist(active.cues.filter(x => x.slot !== slot))}>Remove</button></div>}</div>; })}</div></section> :
    discoverOpen ? <DiscoveryPanel onImport={importFile} jobs={acquisitions} error={acquisitionError} onQueue={queueAcquisition} onCancel={cancelAcquisition} onRetry={retryAcquisition} onRefresh={refreshAcquisitions} onOpen={async id=>{await choose(await call<Track>('track.get',{id}),true);}}/> :
    setOpen ? null : <section className="library"><MetadataBatchProgress batch={metadataBatch} onReview={setMetadataIds}/><div className="library-context">
      <div className="library-context-title">{currentPlaylist && <label>Playlist <input className="playlist-name-input" aria-label="Playlist name" key={currentPlaylist.id+currentPlaylist.name} defaultValue={currentPlaylist.name} onBlur={e=>{if(e.target.value.trim() && e.target.value!==currentPlaylist.name)void savePlaylist(e.target.value,currentPlaylist.track_ids,currentPlaylist.id);}}/></label>}<span className="muted">{visible.length} tracks</span></div>
      <div className="library-view-controls"><Select aria-label="Library filter" value={libraryFilter} onChange={e=>setLibraryFilter(e.target.value)}><option value="all">All tracks</option><option value="missing">Missing files</option></Select><button aria-label="Refresh library" title="Refresh library" onClick={()=>void refresh().catch(e=>setNotice(String(e)))}><RefreshCw size={15}/></button></div>
    </div><div className="library-tools">
      <div className="metadata-tools"><button className="primary" disabled={locked || metadataBatch.starting || metadataBatch.running || !metadataTargets.length} title={`Find and apply the best metadata and album covers for ${metadataTargets.length} ${selectedIds.length?'selected tracks':'tracks in this view'}`} onClick={()=>{const ids=[...metadataTargets];setSelectedIds(ids);void metadataBatch.start(ids);}}><WandSparkles size={15}/> {metadataBatch.starting?'Starting…':'Auto metadata'} · {metadataTargets.length}</button><button disabled={!metadataReviewTargets.length || locked} onClick={()=>setMetadataIds(metadataReviewTargets)}><Tags size={15}/> Review {metadataReviewTargets.length || ''}</button></div>
      {selectedIds.length>0 && <Disclosure className="selection-tools toolbar-menu" label={`${selectedIds.length} selected`} popover><button disabled={locked} onClick={()=>setTagWriteIds(selectedIds)}>Write tags</button>{currentPlaylist && <>
        {selectedIds.some(id=>!currentPlaylist.track_ids.includes(id)) && <button onClick={()=>void savePlaylist(currentPlaylist.name,[...new Set([...currentPlaylist.track_ids,...selectedIds])],currentPlaylist.id)}>Add selected</button>}
        {selectedIds.some(id=>currentPlaylist.track_ids.includes(id)) && <button onClick={()=>void savePlaylist(currentPlaylist.name,currentPlaylist.track_ids.filter(id=>!selectedIds.includes(id)),currentPlaylist.id)}>Remove selected</button>}
      </>}<button aria-label="Clear track selection" onClick={()=>setSelectedIds([])}><X size={14}/> Clear selection</button></Disclosure>}
      {(currentPlaylist || selectedIds.length>0) && <div className="library-export"><button disabled={locked || (currentPlaylist?!currentPlaylist.track_ids.length:!selectedIds.length)} onClick={()=>void exportTracks(currentPlaylist?{playlist_id:currentPlaylist.id}:{track_ids:tracks.filter(t=>selectedIds.includes(t.id)).map(t=>t.id)},currentPlaylist?.name || 'CrateLoom selection',copyExportAudio)}><Upload size={15}/> {currentPlaylist?'Export playlist':'Export selected'}</button><Disclosure className="toolbar-menu export-settings" label="Export options" popover><label className="export-audio-option"><input type="checkbox" checked={copyExportAudio} disabled={locked} onChange={e=>setCopyExportAudio(e.target.checked)}/> Include audio files</label><small>Copy verified audio beside the rekordbox XML.</small></Disclosure></div>}
    </div><div className="table-head"><label className="select-visible"><input aria-label="Select all visible tracks" type="checkbox" checked={visible.length>0 && visible.every(t=>selectedIds.includes(t.id))} onChange={e=>setSelectedIds(e.target.checked?[...new Set([...selectedIds,...visible.map(t=>t.id)])]:selectedIds.filter(id=>!visible.some(t=>t.id===id)))}/>Title</label><span>Artist</span><span>Duration</span><span>Status</span></div>{visible.length ? visible.map(t => <div key={t.id} className={`track-row ${active?.id === t.id ? 'selected' : ''} ${playlistReorder.target===t.id?'drop-target':''}`} data-reorder-group="playlist" data-reorder-id={t.id}><div className="track-title-cell">{currentPlaylist&&<><button className="reorder-handle" {...playlistReorder.handle(t.id)} disabled={ordering} title="Drag to reorder · Up / Down keys" aria-label={`Reorder ${t.title}. Use up or down arrow keys.`} onKeyDown={e=>{if(e.key==='ArrowUp'||e.key==='ArrowDown'){e.preventDefault();movePlaylistTrack(t.id,e.key==='ArrowUp'?-1:1);}}}><GripVertical size={15}/></button><span className="track-order time">{String(currentPlaylist.track_ids.indexOf(t.id)+1).padStart(2,'0')}</span></>}<input type="checkbox" aria-label={`Select ${t.title}`} checked={selectedIds.includes(t.id)} onChange={e=>setSelectedIds(ids=>e.target.checked?[...ids,t.id]:ids.filter(id=>id!==t.id))}/><button disabled={locked} className="track-select track-name" onClick={() => void choose(t)} onDoubleClick={() => void choose(t, true)}><span className="cover">{t.metadata?.artwork_path?<img src={convertFileSrc(t.metadata.artwork_path)} alt="" loading="lazy"/>:<Music2 size={16}/>}</span><strong title={t.title}>{t.title}</strong></button></div><span className="truncate" title={t.artist}>{t.artist || 'Unknown artist'}</span><span className="time">{time(t.duration)}</span><span>{t.missing?<button className="relink" disabled={locked} onClick={()=>void relink(t)}><Link2 size={14}/> Relink</button>:<span className="track-row-actions"><button className="row-play" title={playing && active?.id===t.id?'Pause':'Play'} aria-label={`${playing && active?.id===t.id?'Pause':'Play'} ${t.title}`} disabled={locked} onClick={()=>{
      if(active?.id===t.id) toggle(); else void choose(t,false,true);
    }}>{playing && active?.id===t.id?<Pause size={14}/>:<Play size={14}/>}</button><button aria-label={`Edit metadata for ${t.title}`} title="Metadata" onClick={()=>setMetadataIds([t.id])}><Tags size={14}/></button>{currentPlaylist?<><button aria-label={`Move ${t.title} up`} disabled={ordering || currentPlaylist.track_ids[0]===t.id} onClick={()=>movePlaylistTrack(t.id,-1)}><ArrowUp size={13}/></button><button aria-label={`Move ${t.title} down`} disabled={ordering || currentPlaylist.track_ids.at(-1)===t.id} onClick={()=>movePlaylistTrack(t.id,1)}><ArrowDown size={13}/></button></>:<span className="status">{t.metadata && Object.keys(t.metadata.pending).length?'Tags pending':t.cues.length?'Prepared':'Imported'}</span>}</span>}</span></div>) : <div className="empty"><div className="empty-icon"><FolderOpen size={27}/></div><h2>{query ? 'No matching tracks' : currentPlaylist ? 'This playlist is empty' : libraryFilter==='missing'?'No missing files':'Your library starts here'}</h2><p>{query ? 'Try a different search.' : currentPlaylist ? 'Select tracks in All Tracks, then add them here.' : libraryFilter==='missing'?'All library paths are available.':'Add a local audio file to prepare it for your next set.'}</p>{!query && !currentPlaylist && libraryFilter!=='missing' && <button disabled={locked} onClick={() => void importFile()}><Plus size={16}/> Add Music</button>}</div>}</section>}
    <PanelPresence open={!!tagWriteIds}>{tagWriteIds && <TagWriteReview ids={tagWriteIds} onClose={()=>setTagWriteIds(null)} onSaved={refresh}/>}</PanelPresence>
    <PanelPresence open={!!metadataIds}>{metadataIds && <MetadataEditor key={metadataIds.join(',')} ids={metadataIds} onClose={()=>setMetadataIds(null)} onSaved={async()=>{await refresh();if(activeId.current){const updated=await call<Track>('track.get',{id:activeId.current});setActive(current=>current?.id===updated.id?updated:current);}}}/>}</PanelPresence>
    <audio ref={audio} preload="metadata" onLoadedMetadata={e=>{if(pendingSeek.current!==null){e.currentTarget.currentTime=Math.min(pendingSeek.current,e.currentTarget.duration || pendingSeek.current);pendingSeek.current=null;}}} onCanPlay={()=>{setPlayerLoading(false);if(wantsPlayback.current && audio.current?.paused)playAudio();}} onWaiting={()=>setPlayerLoading(true)} onTimeUpdate={e=>{if(e.currentTarget.paused && !e.currentTarget.seeking && !scrub.current && !wheelScrubbing.current)setPosition(e.currentTarget.currentTime);}} onPlay={e=>{wantsPlayback.current=true;setPlayerLoading(false);playbackClock.current.reset(e.currentTarget.currentTime,performance.now());setPlaying(true);}} onPause={()=>setPlaying(false)} onEnded={() => {wantsPlayback.current=false;setPlaying(false);if(!editor)advancePlayback(1,true);}} onError={()=>{setPlaying(false);if(active && !active.missing){if(!playbackFallback.current)void preparePlayback();else if(!playbackPreparing.current){setPlayerLoading(false);wantsPlayback.current=false;setNotice('Playback failed. Select the track again or relink its file.');}}}}/>
    {!editor && <footer className="transport">
      <button className="now" aria-label={active?`Edit cues for ${active.title}`:'Edit track cues'} disabled={!active} onClick={()=>transitionWorkspace(()=>{setEditor(true);setDiscoverOpen(false);})}><span className="cover player-artwork">{active?.metadata?.artwork_path ? <img src={convertFileSrc(active.metadata.artwork_path)} alt=""/> : <Music2 size={16}/>}</span>
        <span className="player-track-text"><strong className="player-title" title={active?.title}>{active?.title || 'No track selected'}</strong>{(playerLoading || active?.artist) && <small className="player-artist" role={playerLoading?'status':undefined}>{playerLoading?'Loading audio…':active?.artist}</small>}</span>
      </button>
      <div className="player">
        <div className="transport-controls">
          <button className="icon" aria-label="Shuffle" title="Shuffle" aria-pressed={shuffle} onClick={()=>{shuffleAnchor.current=active?.id ?? null;setShuffle(!shuffle);}}><Shuffle size={16}/></button>
          <button className="icon" aria-label="Previous track" title="Previous track" onClick={()=>advancePlayback(-1)} disabled={!adjacentTrack(-1) && (!active || position<=3)}><SkipBack size={18}/></button>
          <button className="play" aria-label={playerLoading && wantsPlayback.current ? 'Cancel playback' : playing ? 'Pause' : 'Play'} title={playerLoading ? 'Loading audio' : playing ? 'Pause' : 'Play'} onClick={toggle} disabled={(!active || active.missing) && !playbackOrder.length}>{playing ? <Pause size={18}/> : <Play size={18}/>}</button>
          <button className="icon" aria-label="Next track" title="Next track" onClick={()=>advancePlayback(1)} disabled={!adjacentTrack(1)}><SkipForward size={18}/></button>
          <button className="icon" aria-label={`Repeat ${repeat}`} title={`Repeat ${repeat}`} aria-pressed={repeat!=='off'} onClick={()=>setRepeat(repeat==='off'?'all':repeat==='all'?'one':'off')}>{repeat==='one'?<Repeat1 size={16}/>:<Repeat size={16}/>}</button>
        </div>
        <div className="transport-timeline"><span className="time">{time(position)}</span><input aria-label="Playback position" type="range" min="0" max={active?.duration || 1} step="0.01" value={position} disabled={!active || active.missing} onChange={e=>seek(Number(e.target.value))}/><span className="time">{time(active?.duration || 0)}</span></div>
      </div>
      <div className="transport-side">
        <button className="icon" aria-label={muted?'Unmute':'Mute'} title={muted?'Unmute':'Mute'} onClick={()=>setMuted(!muted)}>{muted || volume===0?<VolumeX size={17}/>:<Volume2 size={17}/>}</button>
        <input aria-label="Volume" type="range" min="0" max="1" step="0.01" value={muted?0:volume} onChange={e=>{setMuted(false);setVolume(Number(e.target.value));}}/>
        <button className="icon" aria-label="Edit cues" title="Edit cues" disabled={!active} onClick={()=>transitionWorkspace(()=>{setEditor(true);setDiscoverOpen(false);})}><Pencil size={15}/> Edit</button>
      </div>
    </footer>}
    <PanelPresence open={downloadsOpen}>{downloadsOpen && <Downloads jobs={acquisitions} error={acquisitionError} close={()=>setDownloadsOpen(false)}
      clear={()=>{void call('acquisitions.clear_completed').then(refreshAcquisitions).catch(e=>setAcquisitionError(String(e)));}}
      cancel={id=>{void cancelAcquisition(id).catch(e=>setAcquisitionError(String(e)));}}
      retry={id=>{void retryAcquisition(id).catch(e=>setAcquisitionError(String(e)));}}
      open={id=>{setDownloadsOpen(false);void choose({id},true);}}/>}</PanelPresence>
    {busy && <div className="busy" role="status">{busy}</div>}
  </main></div>;
}
createRoot(document.getElementById('root')!).render(<App/>);
