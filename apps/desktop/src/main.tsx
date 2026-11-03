import React, { useEffect, useRef, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { invoke, convertFileSrc } from '@tauri-apps/api/core';
import { open, save } from '@tauri-apps/plugin-dialog';
import { Music2, Plus, Search, Play, Pause, ArrowLeft, Upload, Activity, FolderOpen, SkipBack, ZoomIn, ZoomOut, ChevronLeft, ChevronRight, Crosshair, RotateCcw, SlidersHorizontal, X, Undo2, Redo2, Info } from 'lucide-react';
import './style.css';
import {WheelInput, type WheelMode} from './wheel-input';

type Cue = { slot: number; position_us: number; label: string; color?: string };
type Section = {id: string; start_us: number; end_us: number; label: string; origin?: 'manual' | 'model-reviewed' | 'model'};
type StructureAnalysis = {suggestions: {start_us:number;end_us:number;label:string}[]; model:string; weight_license:string; revision:string};
type AnalysisData = { version: string; tempo: number | null; beats: number[]; downbeats: null; sections: null; waveform: { peaks: number[]; rgb: number[][]; signal?: {encoding:string;hop:number;rate:number;data:string} }; warnings: string[] };
type GridState = {bpm:number|null;origin_us:number|null;anchor_us:number|null;offset_us:number};
type Job = {id: string; track_id: string; state: string; stage: string; error: string | null; kind: 'beat' | 'structure'};
type Track = { id: string; path: string; title: string; artist: string; duration: number; codec: string; sample_rate: number | null; missing: boolean; cues: Cue[]; sections: Section[]; structure_analysis: StructureAnalysis | null; grid_anchor_us: number | null; grid_cache_key: string | null; grid_offset_us: number; grid_bpm: number | null; grid_origin_us: number | null; fixed_grid_key: string | null; offset_cache_key: string | null; detail_analysis: null | {cache_path: string; cache_key: string; tempo: number | null; revision?: string; version?:string}; analysis: null | { peaks: number[]; tempo: null } };
const call = <T,>(method: string, params: object = {}) => invoke<T>('engine_call', { method, params });
const time = (n: number) => `${Math.floor(n / 60)}:${Math.floor(n % 60).toString().padStart(2, '0')}`;
const slotName = (n: number) => String.fromCharCode(65 + n);
const cueColors = ['#ef5c86', '#57b7ee', '#9ccc51', '#ae7cf5', '#edac52', '#5acbb7', '#d885c3', '#b3bec8'];
const emptyPeaks:number[] = [];
const sectionColors: Record<string,string> = {Intro:'#356997',Verse:'#536d8e',Chorus:'#25835e',Bridge:'#67548b',Instrumental:'#4b7180',Solo:'#965c60',Break:'#876828',Up:'#7849aa',Down:'#92602b',Outro:'#496a99',Other:'#5c6f6b'};
const WaveBars = React.memo(function WaveBars({data, fallback, start, length, duration, overview=false}: {data: AnalysisData | null; fallback: number[]; start: number; length: number; duration: number; overview?:boolean}) {
  const canvas = useRef<HTMLCanvasElement>(null);
  const signal = React.useMemo(() => {
    const encoded=data?.waveform.signal?.data;if(!encoded)return null;
    const bytes=Uint8Array.from(atob(encoded),c=>c.charCodeAt(0));
    return new Int16Array(bytes.buffer);
  },[data]);
  useEffect(() => {
    const el = canvas.current; if (!el || !duration || !length) return;
    const draw = () => {
      const rect = el.getBoundingClientRect(), scale = window.devicePixelRatio || 1;
      const width = Math.max(1, Math.round(rect.width * scale)), height = Math.max(1, Math.round(rect.height * scale));
      if (el.width !== width || el.height !== height) { el.width = width; el.height = height; }
      const ctx = el.getContext('2d'); if (!ctx) return;
      ctx.clearRect(0, 0, width, height);
      const peaks = data?.waveform.peaks || fallback, colors = data?.waveform.rgb;
      if (!peaks.length) return;
      // Each column spans actual signed samples, preserving fine oscillations.
      const count=signal ? signal.length/2 : peaks.length;
      const step=1;
      for(let x=0;x<width;x+=step){
        const from=(start+x/width*length)/duration*count, to=(start+(x+step)/width*length)/duration*count;
        if(to<=0 || from>=count)continue;
        const first=Math.max(0,Math.floor(from)), last=Math.min(count,Math.max(first+1,Math.ceil(to)));
        let lo=1,hi=-1;
        if(signal){for(let i=first;i<last;i++){lo=Math.min(lo,signal[i*2]/32767);hi=Math.max(hi,signal[i*2+1]/32767);}}
        else {let peak=0;for(let i=first;i<last;i++)peak=Math.max(peak,peaks[i]);lo=-peak;hi=peak;}
        const ci=colors ? Math.max(0,Math.min(colors.length-1,Math.floor((from+to)/2/count*colors.length))) : 0;
        const rgb=colors?.[ci] || [0,.7,.9];
        const weights=rgb.map(v=>v*v*v);
        const total=weights[0]+weights[1]+weights[2]+.00001;
        // A saturated frequency palette: bass red, mids green, highs blue.
        const bass=weights[0]/total, mid=weights[1]/total, high=weights[2]/total;
        const r=Math.round(20+235*bass);
        const g=Math.round(20+235*mid);
        const blue=Math.round(20+235*high);
        ctx.fillStyle=`rgb(${r},${g},${blue})`;
        const gain=overview ? .66 : .72, centre=height/2;
        const top=centre-hi*centre*gain, bottom=centre-lo*centre*gain;
        ctx.fillRect(x,top,1,Math.max(.8,bottom-top));
      }
    };
    draw(); const observer = new ResizeObserver(draw); observer.observe(el); return () => observer.disconnect();
  }, [data, fallback, start, length, duration, overview, signal]);
  return <canvas ref={canvas} aria-hidden="true"/>;
});

function App() {
  const [tracks, setTracks] = useState<Track[]>([]);
  const [active, setActive] = useState<Track | null>(null);
  const [editor, setEditor] = useState(false);
  const [query, setQuery] = useState('');
  const [notice, setNotice] = useState('');
  const [busy, setBusy] = useState('');
  const [saving, setSaving] = useState(false);
  const [playing, setPlaying] = useState(false);
  const [position, setPosition] = useState(0);
  const [theme, setTheme] = useState(localStorage.getItem('theme') || 'system');
  const [history, setHistory] = useState<Cue[][]>([]);
  const [future, setFuture] = useState<Cue[][]>([]);
  const [saved, setSaved] = useState('');
  const [zoom, setZoom] = useState(16);
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
  const queueStarting = useRef(false);
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
  const drag = useRef<number | null>(null);
  const waveElement = useRef<HTMLDivElement>(null);
  const scrub = useRef<{pointer: number; x: number; position: number; wasPlaying: boolean; moved: boolean; previous: number; at: number} | null>(null);
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
  useEffect(() => { return () => { scratchGeneration.current++; scratchLoading.current = null; if (scratch.current) { void scratch.current.context.close(); scratch.current = null; } }; }, [active?.path]);
  useEffect(() => {
    if (!playing) return;
    let frame = 0;
    const tick = () => { const a = audio.current; if (a && !a.paused) { if (loopRef.current && a.currentTime >= loopRef.current.end) a.currentTime=loopRef.current.start; setPosition(a.currentTime); frame=requestAnimationFrame(tick); } };
    frame=requestAnimationFrame(tick); return () => cancelAnimationFrame(frame);
  }, [playing]);
  useEffect(() => {
    const el=waveElement.current; if (!el || !editor) return;
    let base=zoomRef.current, gesturing=false, wheelTimer=0, wheelActive=false, resume=false, previousAt=0;
    const finish=()=>{wheelActive=false;stopScratch();if(resume)void audio.current?.play().catch(e=>setNotice(String(e)));};
    const wheelInput=new WheelInput();
    const wheel=(e:WheelEvent) => {
      e.preventDefault();if(gesturing)return;
      if(wheelInput.action(e,wheelModeRef.current)==='zoom'){setZoom(z=>Math.max(1,Math.min(128,z*Math.exp(-e.deltaY*.003))));return;}
      if(!audio.current || !active || active.missing)return;
      if(!wheelActive){wheelActive=true;resume=!audio.current.paused;audio.current.pause();loadScratch(active.path);previousAt=performance.now()-16;}
      const delta=Math.abs(e.deltaX)>Math.abs(e.deltaY)?e.deltaX:e.deltaY;
      const from=audio.current.currentTime, to=Math.max(0,Math.min(active.duration,from+delta/el.clientWidth*active.duration/zoomRef.current));
      const now=performance.now();seek(to);scratchSound(from,to,(now-previousAt)/1000);previousAt=now;
      clearTimeout(wheelTimer);wheelTimer=window.setTimeout(finish,120);
    };
    const begin=(e:Event) => { e.preventDefault(); gesturing=true; base=zoomRef.current; };
    const change=(e:Event) => { e.preventDefault(); const scale=(e as Event & {scale:number}).scale; if(Number.isFinite(scale)) setZoom(Math.max(1,Math.min(128,base*scale))); };
    const end=(e:Event) => { e.preventDefault(); gesturing=false; };
    el.addEventListener('gestureend',end,{passive:false}); el.addEventListener('wheel',wheel,{passive:false}); el.addEventListener('gesturestart',begin,{passive:false}); el.addEventListener('gesturechange',change,{passive:false});
    return () => {clearTimeout(wheelTimer);stopScratch();el.removeEventListener('wheel',wheel);el.removeEventListener('gesturestart',begin);el.removeEventListener('gesturechange',change);el.removeEventListener('gestureend',end);};
  }, [editor, active?.id]);
  const offsetSeconds = (active?.grid_offset_us || 0) / 1e6;
  const beats = React.useMemo(() => {
    if (!detail || !active) return [];
    if (active.grid_bpm && active.grid_origin_us != null) {
      const interval=60/active.grid_bpm, origin=active.grid_origin_us/1e6+offsetSeconds;
      const start=Math.ceil(-origin/interval), end=Math.ceil((active.duration-origin)/interval);
      return Array.from({length:Math.max(0,end-start)},(_,i)=>origin+(start+i)*interval).filter(b=>b>=0 && b<active.duration);
    }
    return detail.beats.map(b=>b+offsetSeconds).filter(b=>b>=0 && b<active.duration);
  }, [detail, offsetSeconds, active?.duration, active?.grid_bpm, active?.grid_origin_us]);
  const anchorIndex = React.useMemo(() => active?.grid_anchor_us == null ? -1 : beats.findIndex(b => Math.abs(b*1e6-active.grid_anchor_us!-(active.grid_offset_us || 0)) <= 1000), [beats, active?.grid_anchor_us, active?.grid_offset_us]);

  useEffect(() => { loopRef.current = loop; }, [loop]);
  useEffect(() => { setGridHistory([]);setGridFuture([]); }, [active?.id,active?.detail_analysis?.cache_key]);
  useEffect(() => { if ((snap === 'beat' && !beats.length) || (snap === 'bar' && anchorIndex < 0)) setSnap('off'); }, [snap, beats, anchorIndex]);
  useEffect(() => { document.documentElement.dataset.theme = theme; localStorage.setItem('theme', theme); }, [theme]);
  const refresh = async () => setTracks(await call<Track[]>('library.list'));
  useEffect(() => { refresh().catch(e => setNotice(String(e))); }, []);
  const choose = async (t: Track, edit = false) => {
    if (editLock.current || busy) return;
    const request = ++selection.current;
    try {
      const loaded = await call<Track>('track.get', { id: t.id });
      if (request !== selection.current) return;
      const changed = activeId.current !== loaded.id;
      activeId.current = loaded.id;
      setActive(loaded); setEditor(edit); setHistory([]); setFuture([]); setSaved(''); setZoom(16);
      if (changed) { setGridHistory([]);setGridFuture([]);setSectionsOpen(false);tempCue.current=0;setTempCuePosition(0); setPosition(0); setLoop(null); setEditingSection(null);  }
      if (!loaded.missing && loaded.detail_analysis?.version !== 'librosa-0.11.0-rgb-5') setAutoQueue(q=>q.includes(loaded.id)?q:[...q,loaded.id]);
    } catch (e) { setNotice(String(e)); }
  };
  useEffect(() => {
    let cancelled = false;
    setDetail(null);
    if (active?.detail_analysis?.cache_path) {
      fetch(convertFileSrc(active.detail_analysis.cache_path), {cache:'no-store'}).then(r => { if (!r.ok) throw new Error('Analysis cache could not be read'); return r.json() as Promise<AnalysisData>; }).then(data => { if (!cancelled) setDetail(data); }).catch(e => { if (!cancelled) setNotice(String(e)); });
    }
    return () => { cancelled = true; };
  }, [active?.detail_analysis?.cache_path, active?.detail_analysis?.revision]);
  const startAnalysis = async () => {
    if (!active || active.missing || job?.state === 'running') return;
    try { setJob(await call<Job>('analysis.enqueue', {id: active.id, force: !!detail})); }
    catch (e) { setNotice(String(e)); }
  };
  const startStructure = async () => {
    if (!active || active.missing || job?.state === 'running') return;
    try {  setJob(await call<Job>('structure.enqueue', {id:active.id,force:!!active.structure_analysis})); }
    catch (e) { setNotice(String(e)); }
  };
  useEffect(() => {
    if (!autoQueue.length || queueStarting.current || (job && ['running','queued'].includes(job.state))) return;
    const id=autoQueue[0]; queueStarting.current=true;
    call<Job>('analysis.enqueue',{id}).then(setJob).catch(e=>setNotice(`Automatic analysis failed: ${String(e)}`)).finally(()=>{queueStarting.current=false;setAutoQueue(q=>q.slice(1));});
  }, [autoQueue, job?.id, job?.state]);
  useEffect(() => {
    if (!job || !['running', 'queued'].includes(job.state)) return;
    let stopped = false;
    const timer = window.setInterval(async () => {
      try {
        const next = await call<Job>('jobs.get', {id: job.id});
        if (stopped) return;
        setJob(next);
        if (next.state === 'succeeded' && activeId.current === next.track_id) {
          const loaded = await call<Track>('track.get', {id: next.track_id});
          if (!stopped) setActive(current => current?.id === loaded.id ? {...current, detail_analysis:loaded.detail_analysis, structure_analysis:loaded.structure_analysis, sections:loaded.sections, grid_bpm:loaded.grid_bpm, grid_origin_us:loaded.grid_origin_us, fixed_grid_key:loaded.fixed_grid_key, grid_cache_key:loaded.grid_cache_key, offset_cache_key:loaded.offset_cache_key, grid_anchor_us:current.grid_cache_key === loaded.detail_analysis?.cache_key ? current.grid_anchor_us : loaded.grid_anchor_us, grid_offset_us:current.offset_cache_key === loaded.detail_analysis?.cache_key ? current.grid_offset_us : loaded.grid_offset_us} : current);
          await refresh();
        }
      } catch (e) { if (!stopped) { setNotice(String(e)); setJob(null); } }
    }, 600);
    return () => { stopped = true; window.clearInterval(timer); };
  }, [job?.id, job?.state]);
  useEffect(() => {
    if (job?.state === 'succeeded' && activeId.current === job.track_id) {
      call<Track>('track.get', {id: job.track_id}).then(t => { setActive(current => current?.id === t.id ? {...current, detail_analysis:t.detail_analysis, structure_analysis:t.structure_analysis, sections:t.sections, grid_bpm:t.grid_bpm, grid_origin_us:t.grid_origin_us, fixed_grid_key:t.fixed_grid_key, grid_cache_key:t.grid_cache_key, offset_cache_key:t.offset_cache_key, grid_anchor_us:current.grid_cache_key === t.detail_analysis?.cache_key ? current.grid_anchor_us : t.grid_anchor_us, grid_offset_us:current.offset_cache_key === t.detail_analysis?.cache_key ? current.grid_offset_us : t.grid_offset_us} : current); }).catch(e => setNotice(String(e)));
    }
  }, [job?.id, job?.state]);
  useEffect(() => { if(job?.state === 'failed') setNotice(`${job.kind === 'beat' ? 'BPM/RGB' : 'Section'} analysis failed: ${job.error || 'Unknown error'}`); }, [job?.id, job?.state]);
  const cancelAnalysis = async () => {
    if (!job) return;
    try { setJob(await call<Job>('jobs.cancel', {id: job.id})); } catch (e) { setNotice(String(e)); }
  };
  const importFile = async () => {
    if (editLock.current || busy) return;
    try {
      const path = await open({ multiple: false, filters: [{ name: 'Audio', extensions: ['mp3', 'wav', 'aiff', 'aif', 'm4a', 'flac', 'ogg'] }] });
      if (!path) return;
      setBusy('Importing…');
      const t = await call<Track>('library.import', { path });
      await refresh();
      // Select directly: the import already returned the track and granted asset access.
      ++selection.current; activeId.current = t.id;
      setGridHistory([]);setGridFuture([]);setSectionsOpen(false);tempCue.current=0;setTempCuePosition(0); setActive(t); setPosition(0); setLoop(null); setEditingSection(null);  setEditor(false); setHistory([]); setFuture([]); setSaved('');
      setBusy('');
      if (t.detail_analysis?.version !== 'librosa-0.11.0-rgb-5' && !t.missing) setAutoQueue(q=>q.includes(t.id)?q:[...q,t.id]);
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
      choices = choices.filter((_,i)=>i>=anchorIndex && (i-anchorIndex)%4===0);
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
    audio.current.currentTime = p; setPosition(p);
    if (play) void audio.current.play().catch(e => setNotice(String(e)));
  };
  const toggle = () => {
    if (!audio.current || !active || active.missing) return;
    if (cuePreview.current) { cueKeepPlaying.current=true; cuePreview.current=false; return; }
    if (audio.current.paused) void audio.current.play().catch(e => setNotice(String(e))); else audio.current.pause();
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
    if(!a.paused){a.pause();seek(tempCue.current);cuePreview.current=false;return;}
    if(Math.abs(a.currentTime-tempCue.current)>.05){tempCue.current=a.currentTime;setTempCuePosition(a.currentTime);cuePreview.current=false;return;}
    cuePreview.current=true; seek(tempCue.current,true);
  };
  const cueUp = () => { if(cuePreview.current && !cueKeepPlaying.current){audio.current?.pause();seek(tempCue.current);} cuePreview.current=false; };
  const exportTrack = async () => {
    if (!active || editLock.current || busy) return;
    try {
      const path = await save({ defaultPath: 'CrateLoom.xml', filters: [{ name: 'Rekordbox XML', extensions: ['xml'] }] });
      if (!path) return;
      const result = await call<{xml: string; backup: string}>('export.rekordbox', { id: active.id, path });
      setNotice(`Exported XML and cue backup. Import the XML playlist in rekordbox and check cue alignment.\n${result.xml}\n${result.backup}`);
    } catch (e) { setNotice(String(e)); }
  };
  useEffect(() => {
    const key = (e: KeyboardEvent) => {
      const el = e.target as HTMLElement;
      if (['INPUT', 'TEXTAREA', 'SELECT', 'BUTTON'].includes(el.tagName) || el.isContentEditable || el.closest('[role=dialog]')) return;
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'f') { e.preventDefault(); search.current?.focus(); }
      else if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'o') { e.preventDefault(); void importFile(); }
      else if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'z' && editor && active && !saving && !busy) {
        e.preventDefault(); const next = e.shiftKey ? future.at(-1) : history.at(-1);
        if (next) void persist(next, e.shiftKey ? 'redo' : 'undo');
      } else if (e.code === 'Space') { e.preventDefault(); toggle(); }
      else if (editor && /^[1-8]$/.test(e.key)) { const cue = cueAt(Number(e.key) - 1); if (cue) seek(cue.position_us / 1e6, true); }
    };
    window.addEventListener('keydown', key); return () => window.removeEventListener('keydown', key);
  });
  const visible = tracks.filter(t => `${t.title} ${t.artist}`.toLowerCase().includes(query.toLowerCase()));
  const locked = !!busy || saving;
  const waveStart = active ? position - active.duration / zoom / 2 : 0;
  const waveLength = active ? active.duration / zoom : 1;
  const wavePosition = (clientX: number, element: HTMLElement) => waveStart + Math.max(0, Math.min(1, (clientX - element.getBoundingClientRect().left) / element.clientWidth)) * waveLength;
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

  return <div className="app"><aside className="sidebar"><div className="brand"><div className="brand-icon"><Music2 size={18}/></div><strong>CrateLoom</strong></div><div className="side-label">LIBRARY</div><button className="nav active" onClick={() => setEditor(false)}><FolderOpen size={17}/> All Tracks <span>{tracks.length}</span></button><div className="sidebar-bottom"><label htmlFor="appearance">Appearance</label><select id="appearance" value={theme} onChange={e => setTheme(e.target.value)}><option value="system">System</option><option value="dark">Dark</option><option value="light">Light</option></select></div></aside><main>
    <header><div className="heading">{editor && <button className="icon" aria-label="Back to library" title="Back to library" onClick={() => setEditor(false)}><ArrowLeft size={19}/></button>}<h1>{editor ? 'Cue Editor' : 'All Tracks'}</h1></div><div className="header-actions">{!editor && <label className="search"><Search size={16}/><input ref={search} aria-label="Search tracks" placeholder="Search tracks" value={query} onChange={e => setQuery(e.target.value)}/></label>}<button className="primary" disabled={locked} onClick={() => void importFile()}><Plus size={16}/> Add Music</button></div></header>
    {notice && <div className="notice" role="status"><span>{notice}</span><button onClick={() => setNotice('')}>Dismiss</button></div>}
    {editor && active ? <section className="editor"><div className="trackhead"><div className="cover large"><Music2 size={24}/></div><div className="track-details"><h2 title={active.title}>{active.title}</h2><p>{active.artist || 'Unknown artist'} · {time(active.duration)} · {active.codec.toUpperCase()}{active.sample_rate ? ` · ${active.sample_rate} Hz` : ''}</p></div><div className="push"/><button disabled={locked || active.missing || job?.state === 'running'} onClick={() => void startAnalysis()}><Activity size={16}/>{detail ? 'Analyze again' : 'Analyze beats & RGB'}</button><button disabled={locked || active.missing || job?.state === 'running'} onClick={() => void startStructure()} title="Detect song sections">Analyze sections</button><button disabled={locked || active.missing} onClick={() => void exportTrack()}><Upload size={16}/> Export XML</button></div>
      {active.missing && <div className="analysis-note" role="alert">File missing. Reconnect the drive or restore the original file to continue.</div>}
      <details className="analysis-info"><summary aria-label="About analysis"><Info size={14}/> Analysis info</summary><label className="wheel-setting">Scroll input <select aria-label="Waveform scroll input" value={wheelMode} onChange={e=>{const mode=e.target.value as WheelMode;setWheelMode(mode);localStorage.setItem('waveWheelMode',mode);}}><option value="auto">Auto detect</option><option value="mouse">Mouse wheel</option><option value="trackpad">Trackpad</option></select></label><p>Sections are model predictions. The optional model downloads about 95 MB on first use. Its weight-license terms need clarification; see the project verification notes.</p></details>
      {job && <div className="jobline" role="status"><span>{job.stage}{job.error ? `: ${job.error}` : ''}</span>{job.state === 'running' && <button onClick={() => void cancelAnalysis()}>Cancel analysis</button>}</div>}
      <div className="wave-label"><span>FULL TRACK</span><span>{detail ? <label className="bpm-field"><input aria-label="BPM" type="number" min="20" max="400" step="0.01" key={`${active.id}-${active.grid_bpm}-${detail.tempo}`} defaultValue={(active.grid_bpm || detail.tempo)?.toFixed(2) || ''} placeholder="Set" onBlur={e=>{const n=Number(e.target.value);if(!Number.isFinite(n)||n<20||n>400){e.target.value=(active.grid_bpm || detail.tempo)?.toFixed(2) || '';setNotice('BPM must be between 20 and 400.');}else if(n!==(active.grid_bpm || detail.tempo))setBpm(n);}} onKeyDown={e=>{if(e.key==='Enter')e.currentTarget.blur();}}/> BPM</label> : 'Beat analysis not available'}</span></div>
      <div className="overview" role="slider" tabIndex={0} aria-label="Full track overview" aria-valuemin={0} aria-valuemax={active.duration} aria-valuenow={position} onClick={e => seek((e.clientX-e.currentTarget.getBoundingClientRect().left)/e.currentTarget.clientWidth*active.duration)} onKeyDown={e => {if(e.key==='ArrowRight') seek(position+5); if(e.key==='ArrowLeft') seek(position-5);}}>
        <WaveBars data={detail} fallback={active.analysis?.peaks || emptyPeaks} start={0} length={active.duration} duration={active.duration} overview/>
        {active.sections.map(section => <div className="section-band" key={section.id} title={`${section.label} (${section.origin === 'model-reviewed' ? 'reviewed suggestion' : 'manual'})`} style={{left:`${section.start_us / 1e6 / active.duration * 100}%`,width:`${(section.end_us-section.start_us) / 1e6 / active.duration * 100}%`,background:sectionColors[section.label] || sectionColors.Other}}>{section.label}</div>)}
        {active.cues.map(c => <span className="overview-cue" key={c.slot} style={{left:`${c.position_us / 1e6 / active.duration*100}%`, background:c.color || cueColors[c.slot]}}>{slotName(c.slot)}</span>)}
        <span className="temp-cue-arrow" role="img" aria-label={`Temporary cue at ${time(tempCuePosition)}`} title="Temporary cue" style={{left:`${tempCuePosition/active.duration*100}%`}}/>
        <div className="playhead" style={{left:`${position/active.duration*100}%`}}/>
      </div>
      <div className="wave-label detail-label"><span>DETAIL</span><span>{detail ? 'Two fingers to scrub · Pinch to zoom' : 'Two fingers to scrub · Pinch to zoom'}</span></div>
      <div className="wave-detail-layout"><div className="deck-transport"><button className="cue-transport" aria-label="Temporary cue" title="Set while paused; hold to preview; return while playing" disabled={active.missing} onPointerDown={e=>{e.currentTarget.setPointerCapture(e.pointerId);cueDown();}} onClick={e=>{if(e.detail===0){cueDown();cueUp();}}} onPointerUp={cueUp} onPointerCancel={cueUp} onKeyDown={e=>{if((e.key===' ' || e.key==='Enter') && !e.repeat){e.preventDefault();cueDown();}}} onKeyUp={e=>{if(e.key===' ' || e.key==='Enter'){e.preventDefault();cueUp();}}}>CUE</button><button className="play-transport" onClick={toggle} aria-label={playing ? "Pause" : "Play"} disabled={active.missing}>{playing ? <Pause size={21}/> : <Play size={21}/>}</button></div><div className="wave-detail-body">
      <div ref={waveElement} className="wave" role="slider" tabIndex={0} aria-label="Seek track waveform" aria-valuemin={0} aria-valuemax={active.duration} aria-valuenow={position} onKeyDown={e => { if (e.key === 'ArrowRight') { e.preventDefault(); seek(position + 1); } if (e.key === 'ArrowLeft') { e.preventDefault(); seek(position - 1); } }} onPointerDown={e => { if (e.button !== 0 || active.missing) return; e.currentTarget.setPointerCapture(e.pointerId); const wasPlaying=!!audio.current && !audio.current.paused; audio.current?.pause(); scrub.current={pointer:e.pointerId,x:e.clientX,position,wasPlaying,moved:false,previous:position,at:0}; loadScratch(active.path); }} onPointerEnter={()=>{if(!active.missing)loadScratch(active.path);}} onPointerMove={e => { const d=scrub.current; if(!d || d.pointer!==e.pointerId) return; const delta=e.clientX-d.x; if(Math.abs(delta)>2) d.moved=true; if(!d.moved) return; const next=Math.max(0,Math.min(active.duration,d.position-delta/e.currentTarget.clientWidth*waveLength)); seek(next); const now=performance.now();scratchSound(d.previous,next,d.at ? (now-d.at)/1000 : .016);d.previous=next;d.at=now; }} onPointerUp={e => { const d=scrub.current; if(!d || d.pointer!==e.pointerId) return; scrub.current=null;stopScratch(); if(!d.moved) seek(wavePosition(e.clientX,e.currentTarget)); if(d.wasPlaying) void audio.current?.play().catch(err=>setNotice(String(err))); }} onPointerCancel={() => { const d=scrub.current; scrub.current=null;stopScratch(); if(d?.wasPlaying) void audio.current?.play().catch(err=>setNotice(String(err))); }}>
        {active.analysis || detail ? <WaveBars data={detail} fallback={active.analysis?.peaks || emptyPeaks} start={waveStart} length={waveLength} duration={active.duration}/> : <div className="wave-empty">{busy || 'Analyze to see the waveform'}</div>}
        {detail && beats.map((beat, index) => {
          if (beat < waveStart || beat > waveStart + waveLength) return null;
          const isBar = anchorIndex >= 0 && index >= anchorIndex && (index-anchorIndex)%4===0;
          return <span key={index} className={`beat-line ${isBar ? 'bar-line' : ''}`} style={{left:`${(beat-waveStart)/waveLength*100}%`}}>{isBar ? Math.floor((index-anchorIndex)/4)+1 : ''}</span>;
        })}
        {active.sections.filter(section=>section.end_us/1e6>waveStart && section.start_us/1e6<waveStart+waveLength).map(section=>{const left=Math.max(0,(section.start_us/1e6-waveStart)/waveLength*100), right=Math.min(100,(section.end_us/1e6-waveStart)/waveLength*100);return <div key={section.id} className="section-band detail-section" style={{left:`${left}%`,width:`${right-left}%`,background:sectionColors[section.label] || sectionColors.Other}}>{section.label}</div>;})}
        {tempCuePosition>=waveStart && tempCuePosition<=waveStart+waveLength && <span className="temp-cue-arrow" role="img" aria-label={`Temporary cue at ${time(tempCuePosition)}`} title="Temporary cue" style={{left:`${(tempCuePosition-waveStart)/waveLength*100}%`}}/>}
        {position >= waveStart && position <= waveStart + waveLength && <div className="playhead" style={{left: `${(position - waveStart) / waveLength * 100}%`}}/>}
        {active.cues.filter(c => c.position_us / 1e6 >= waveStart && c.position_us / 1e6 <= waveStart + waveLength).map(c => <button key={c.slot} className="marker" aria-label={`Move cue ${slotName(c.slot)}; use Seconds for precise editing`} title={`${c.label} ${time(c.position_us / 1e6)} · drag to move`} style={{left: `${((dragPreview?.slot === c.slot ? dragPreview.position : c.position_us / 1e6) - waveStart) / waveLength * 100}%`, background: c.color || cueColors[c.slot]}} disabled={locked || active.missing} onClick={e => e.stopPropagation()} onPointerDown={e => { e.stopPropagation(); drag.current = c.slot; audio.current?.pause(); setDragPreview({slot: c.slot, position: c.position_us / 1e6}); e.currentTarget.setPointerCapture(e.pointerId); }} onPointerMove={e => { const wave = e.currentTarget.parentElement; if (wave && drag.current === c.slot) setDragPreview({slot: c.slot, position: Math.min(active.duration - .001, wavePosition(e.clientX, wave))}); }} onPointerCancel={() => { drag.current = null; setDragPreview(null); }}>{slotName(c.slot)}</button>)}
      </div><div className="wave-times"><span>{time(Math.max(0,waveStart))}</span><span>{anchorIndex >= 0 && beats[anchorIndex] <= position + .000001 ? `Bar ${Math.floor((beats.filter(b=>b<=position+.000001).length-1-anchorIndex)/4)+1}` : anchorIndex >= 0 ? 'Before bar 1' : 'Bar —'}</span><span>{time(Math.min(active.duration,waveStart + waveLength))}</span></div></div></div>
      <div className="editor-transport"><span className="time">{time(position)} / {time(active.duration)}</span><label className="zoom">Snap <select aria-label="Cue snapping" value={snap} onChange={e=>setSnap(e.target.value)}><option value="off">Off</option><option value="beat" disabled={!beats.length}>Beat</option><option value="bar" disabled={anchorIndex < 0}>Bar</option></select></label><div className="zoom"><button aria-label="Zoom out" onClick={()=>setZoom(z=>Math.max(1,z/1.5))}><ZoomOut size={16}/></button><span>{zoom.toFixed(1)}×</span><button aria-label="Zoom in" onClick={()=>setZoom(z=>Math.min(128,z*1.5))}><ZoomIn size={16}/></button></div><div className="push"/><span className="muted" role="status">{saved}</span><button disabled={locked || !history.length} onClick={() => { const prev = history.at(-1); if (prev) void persist(prev, 'undo'); }} aria-label="Undo cues" title="Undo cues"><Undo2 size={16}/></button><button disabled={locked || !future.length} onClick={() => { const next = future.at(-1); if (next) void persist(next, 'redo'); }} aria-label="Redo cues" title="Redo cues"><Redo2 size={16}/></button></div>
      <div className="review-tools">
        <label>Check before cue <select value={preRoll} onChange={e=>setPreRoll(Number(e.target.value))}><option value="2">2 beats</option><option value="4">4 beats</option><option value="8">8 beats</option></select></label>
        <label>Loop length <select value={loopSeconds} onChange={e=>setLoopSeconds(Number(e.target.value))}><option value="2">2 seconds</option><option value="4">4 seconds</option><option value="8">8 seconds</option></select></label>
        {loop ? <button onClick={()=>{loopRef.current=null;setLoop(null);}}>Exit loop</button> : <button disabled={active.missing || position+loopSeconds>active.duration} onClick={()=>{loopRef.current={start:position,end:position+loopSeconds};setLoop(loopRef.current); void audio.current?.play().catch(e=>setNotice(String(e)));}}>Loop from playhead</button>}
      </div>
      <div className="grid-tools" role="group" aria-label="Beat grid controls"><strong>GRID</strong>
        <button aria-label="Set grid beat at playhead" title="Move nearest grid beat to playhead" disabled={!beats.length || locked} onClick={()=>{const nearest=beats.reduce((a,b)=>Math.abs(b-position)<Math.abs(a-position)?b:a);const offset=Math.round((offsetSeconds+position-nearest)*1e6);void saveGrid('grid.offset',{offset_us:offset});}}><Crosshair size={17}/></button>
        <button aria-label="Set bar one at playhead" title="Set bar 1 at the playhead" disabled={!detail?.tempo || locked} onClick={()=>{const bpm=active.grid_bpm || detail?.tempo;if(bpm)void saveGrid('grid.fixed',{bpm,origin_us:Math.round((position-offsetSeconds)*1e6),bar_one:true});}}>1.1</button>
        <button aria-label="Shift grid left one millisecond" title="Shift grid left 1 ms" disabled={!detail || locked} onClick={()=>void saveGrid('grid.offset',{offset_us:active.grid_offset_us-1000})}><ChevronLeft size={17}/></button>
        <button aria-label="Shift grid right one millisecond" title="Shift grid right 1 ms" disabled={!detail || locked} onClick={()=>void saveGrid('grid.offset',{offset_us:active.grid_offset_us+1000})}><ChevronRight size={17}/></button>
        <button aria-label="Halve BPM" title="Halve BPM" disabled={!detail?.tempo || locked} onClick={()=>setBpm((active.grid_bpm || detail!.tempo!)/2)}>½</button><button aria-label="Double BPM" title="Double BPM" disabled={!detail?.tempo || locked} onClick={()=>setBpm((active.grid_bpm || detail!.tempo!)*2)}>×2</button>
        <button aria-label="Reset grid shift" title="Reset grid shift" disabled={!detail || locked} onClick={()=>void saveGrid('grid.offset',{offset_us:0})}><RotateCcw size={16}/></button>
        <button aria-label="Decrease BPM" title="Decrease BPM 0.1" disabled={!detail?.tempo || locked} onClick={()=>setBpm((active.grid_bpm || detail!.tempo!)-.1)}>−</button><button aria-label="Increase BPM" title="Increase BPM 0.1" disabled={!detail?.tempo || locked} onClick={()=>setBpm((active.grid_bpm || detail!.tempo!)+.1)}>+</button>
        <button title="Tap to set BPM" aria-label="Tap BPM" disabled={!detail?.tempo || locked} onClick={()=>{const now=performance.now();taps.current=[...taps.current.filter(t=>now-t<3000),now].slice(-5);if(taps.current.length>=3){const samples=taps.current;const bpm=60000*(samples.length-1)/(samples.at(-1)!-samples[0]);if(bpm>=20 && bpm<=400)setBpm(Math.round(bpm*100)/100);}}}>TAP</button>
        <button aria-label="Undo grid edit" title="Undo grid edit" disabled={locked || !gridHistory.length} onClick={()=>void restoreGrid('undo')}><Undo2 size={16}/></button><button aria-label="Redo grid edit" title="Redo grid edit" disabled={locked || !gridFuture.length} onClick={()=>void restoreGrid('redo')}><Redo2 size={16}/></button>
        <button aria-label="Reset grid to detected beats" title="Reset grid to detected beats" disabled={!detail || locked} onClick={()=>void saveGrid('grid.restore',{state:{bpm:null,origin_us:null,anchor_us:null,offset_us:0}})}><RotateCcw size={16}/></button>
        <span className="muted">{active.grid_anchor_us == null ? 'Set bar 1 for bar numbers' : `Bar 1 · ${time((active.grid_anchor_us+active.grid_offset_us)/1e6)}`}</span>
      </div>
      <div className="review-tools"><button disabled={locked || active.missing || (!detail && !active.sections.length)} onClick={()=>void generateCues()}>Auto cues</button><span className="muted"></span></div>
      <button onClick={()=>setSectionsOpen(true)}><SlidersHorizontal size={16}/> Edit sections · {active.sections.length}</button>
      {sectionsOpen && <div className="modal-backdrop" onClick={()=>setSectionsOpen(false)}><section className="section-window" role="dialog" aria-modal="true" aria-label="Edit sections" onClick={e=>e.stopPropagation()}><div className="section-window-head"><h2>Edit sections</h2><button aria-label="Close section editor" onClick={()=>setSectionsOpen(false)}><X size={16}/></button></div><p className="muted">Automatic labels can be edited or removed.</p><div className="review-tools"><label>Label <select value={sectionLabel} onChange={e=>setSectionLabel(e.target.value)}>{['Intro','Verse','Chorus','Bridge','Break','Instrumental','Solo','Up','Down','Outro','Other'].map(x=><option key={x}>{x}</option>)}</select></label><label>Start (seconds) <input type="number" min="0" step="0.01" value={sectionStart} onChange={e=>setSectionStart(e.target.value)}/></label><label>End (seconds) <input type="number" min="0" step="0.01" value={sectionEnd} onChange={e=>setSectionEnd(e.target.value)}/></label><button disabled={locked || active.missing} onClick={()=>{const start=Number(sectionStart),end=Number(sectionEnd);if(!sectionEnd || start<0 || end<=start || end>active.duration){setNotice('Enter a valid start and end within the track.');return;}const next=[...active.sections.filter(section=>section.id!==editingSection),{id:editingSection || crypto.randomUUID(),start_us:Math.round(start*1e6),end_us:Math.round(end*1e6),label:sectionLabel}];void saveTrackField('sections.save',{sections:next}).then(ok=>{if(ok){setEditingSection(null);setSectionStart('0');setSectionEnd('');}});}}>{editingSection?'Save section':'Add section'}</button>{editingSection && <button onClick={()=>{setEditingSection(null);setSectionStart('0');setSectionEnd('');}}>Cancel edit</button>}</div>{active.sections.map(section=><div className="section-row" key={section.id}><span>{section.label} · {time(section.start_us/1e6)}–{time(section.end_us/1e6)}{section.origin==='model'?' · automatic':section.origin==='model-reviewed'?' · reviewed suggestion':''}</span><div><button disabled={locked || active.missing} onClick={()=>{setEditingSection(section.id);setSectionLabel(section.label);setSectionStart(String(section.start_us/1e6));setSectionEnd(String(section.end_us/1e6));}}>Edit</button> <button disabled={locked || active.missing} onClick={()=>void saveTrackField('sections.save',{sections:active.sections.filter(x=>x.id!==section.id)})}>Remove</button></div></div>)}</section></div>}

      <div className="cue-title"><div><h2>Hot cues</h2><p>Click an empty slot to set a cue.</p></div><span>1–8 to play</span></div><div className="cues">{Array.from({length: 8}, (_, slot) => { const c = cueAt(slot); return <div className="cue" key={slot}><button className="cue-trigger" disabled={locked || active.missing} onClick={() => c ? seek(c.position_us / 1e6, true) : setCue(slot)}><span className="slot" style={{background: c?.color || cueColors[slot], color:'#16201d'}}>{slotName(slot)}</span><span>{c ? c.label : 'Add cue'}<small>{c ? time(c.position_us / 1e6) : 'At playhead'}</small></span></button>{c && <div className="cue-edit"><label>Color<input aria-label={`Cue ${slotName(slot)} color`} type="color" value={c.color || cueColors[slot]} disabled={locked} onChange={e => updateCue(slot, {color:e.target.value})}/></label><button disabled={active.missing} onClick={() => { const at = c.position_us/1e6; const before = beats.filter(b => b < at-.001); const from = before.length ? before[Math.max(0, before.length-preRoll)] : Math.max(0,at-5); seek(from,true); }}>Check</button><label>Label<input aria-label={`Cue ${slotName(slot)} label`} defaultValue={c.label} key={c.label} maxLength={120} disabled={locked} onBlur={e => { const label = e.target.value.trim(); if (label && label !== c.label) updateCue(slot, {label}); else e.target.value = c.label; }}/></label><label>Seconds<input aria-label={`Cue ${slotName(slot)} seconds`} type="number" min="0" max={active.duration} step="0.001" defaultValue={c.position_us / 1e6} key={c.position_us} disabled={locked} onBlur={e => { const n = Number(e.target.value); if (e.target.value !== '' && Number.isFinite(n) && n >= 0 && n < active.duration) { const us = Math.round(n * 1e6); if (us !== c.position_us) updateCue(slot, {position_us: us}); } else { e.target.value = String(c.position_us / 1e6); setNotice('Cue must be within the track.'); } }}/></label><button disabled={locked} onClick={() => void persist(active.cues.filter(x => x.slot !== slot))}>Remove</button></div>}</div>; })}</div></section> :
    <section className="library"><div className="table-head"><span>Title</span><span>Artist</span><span>Duration</span><span>Status</span></div>{visible.length ? visible.map(t => <button key={t.id} disabled={locked} className={`track-row ${active?.id === t.id ? 'selected' : ''}`} onClick={() => void choose(t)} onDoubleClick={() => void choose(t, true)}><span className="track-name"><span className="cover"><Music2 size={16}/></span><strong title={t.title}>{t.title}</strong></span><span className="truncate" title={t.artist}>{t.artist || 'Unknown artist'}</span><span className="time">{time(t.duration)}</span><span><span className={`status ${t.missing ? 'missing' : ''}`}>{t.missing ? 'Missing file' : t.cues.length ? 'Needs review' : 'Imported'}</span></span></button>) : <div className="empty"><div className="empty-icon"><FolderOpen size={27}/></div><h2>{query ? 'No matching tracks' : 'Your library starts here'}</h2><p>{query ? 'Try a different search.' : 'Add a local audio file to prepare it for your next set.'}</p>{!query && <button disabled={locked} onClick={() => void importFile()}><Plus size={16}/> Add Music</button>}</div>}</section>}
    <audio ref={audio} src={active && !active.missing ? convertFileSrc(active.path) : undefined} onTimeUpdate={e => {if(e.currentTarget.paused) setPosition(e.currentTarget.currentTime);}} onPlay={() => setPlaying(true)} onPause={() => setPlaying(false)} onEnded={() => setPlaying(false)} onError={() => active && !active.missing && setNotice('Playback failed. Check that the file is available and supported.')}/>
    {!editor && <footer><div className="now"><div className="cover"><Music2 size={16}/></div><div><strong>{active?.title || 'No track selected'}</strong><small>{job && ['running','queued'].includes(job.state) && active?.id === job.track_id ? job.stage : active?.artist || 'Select a track to get started'}</small></div></div><div className="player"><div><button className="icon" aria-label="Return to start" onClick={() => seek(0)} disabled={!active}><SkipBack size={17}/></button><button className="play" aria-label={playing ? 'Pause' : 'Play'} onClick={toggle} disabled={!active || active.missing}>{playing ? <Pause size={18}/> : <Play size={18}/>}</button><span className="time">{time(position)} / {time(active?.duration || 0)}</span></div><input aria-label="Playback position" type="range" min="0" max={active?.duration || 1} step="0.01" value={position} onChange={e => seek(Number(e.target.value))}/></div><button disabled={!active} onClick={() => setEditor(true)}>Edit Cues</button></footer>}
    {busy && <div className="busy" role="status">{busy}</div>}
  </main></div>;
}
createRoot(document.getElementById('root')!).render(<App/>);
