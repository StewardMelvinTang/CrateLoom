import React, { useEffect, useRef, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { invoke, convertFileSrc } from '@tauri-apps/api/core';
import { open, save } from '@tauri-apps/plugin-dialog';
import { Music2, Plus, Search, Play, Pause, ArrowLeft, Upload, Activity, FolderOpen, SkipBack } from 'lucide-react';
import './style.css';

type Cue = { slot: number; position_us: number; label: string };
type Track = { id: string; path: string; title: string; artist: string; duration: number; codec: string; sample_rate: number | null; missing: boolean; cues: Cue[]; analysis: null | { peaks: number[]; tempo: null } };
const call = <T,>(method: string, params: object = {}) => invoke<T>('engine_call', { method, params });
const time = (n: number) => `${Math.floor(n / 60)}:${Math.floor(n % 60).toString().padStart(2, '0')}`;
const slotName = (n: number) => String.fromCharCode(65 + n);

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
  const [zoom, setZoom] = useState(1);
  const [dragPreview, setDragPreview] = useState<{slot: number; position: number} | null>(null);
  const audio = useRef<HTMLAudioElement>(null);
  const search = useRef<HTMLInputElement>(null);
  const activeId = useRef<string | null>(null);
  const editLock = useRef(false);
  const selection = useRef(0);
  const drag = useRef<number | null>(null);
  const waveElement = useRef<HTMLDivElement>(null);

  useEffect(() => { document.documentElement.dataset.theme = theme; localStorage.setItem('theme', theme); }, [theme]);
  const refresh = async () => setTracks(await call<Track[]>('library.list'));
  useEffect(() => { refresh().catch(e => setNotice(String(e))); }, []);
  const analyze = async (t: Track) => {
    if (t.missing) return;
    setBusy('Reading waveform…');
    try {
      const updated = await call<Track>('analysis.run', { id: t.id });
      if (activeId.current === t.id) setActive(updated);
      await refresh();
    } catch (e) { setNotice(String(e)); }
    finally { setBusy(''); }
  };
  const choose = async (t: Track, edit = false) => {
    if (editLock.current || busy) return;
    const request = ++selection.current;
    try {
      const loaded = await call<Track>('track.get', { id: t.id });
      if (request !== selection.current) return;
      const changed = activeId.current !== loaded.id;
      activeId.current = loaded.id;
      setActive(loaded); setEditor(edit); setHistory([]); setFuture([]); setSaved(''); setZoom(1);
      if (changed) setPosition(0);
      if (!loaded.analysis && !loaded.missing) void analyze(loaded);
    } catch (e) { setNotice(String(e)); }
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
      setActive(t); setPosition(0); setEditor(false); setHistory([]); setFuture([]); setSaved('');
      setBusy('');
      if (!t.analysis && !t.missing) void analyze(t);
    } catch (e) { setNotice(String(e)); setBusy(''); }
  };
  const persist = async (cues: Cue[], mode: 'edit' | 'undo' | 'redo' = 'edit') => {
    if (!active || editLock.current || busy) return;
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
    } catch (e) { setSaved('Save failed'); setNotice(String(e)); }
    finally { editLock.current = false; setSaving(false); }
  };
  const cueAt = (slot: number) => active?.cues.find(c => c.slot === slot);
  const updateCue = (slot: number, patch: Partial<Cue>) => {
    if (active) void persist(active.cues.map(c => c.slot === slot ? { ...c, ...patch } : c));
  };
  const setCue = (slot: number) => {
    if (!active || active.missing) return;
    const p = Math.min(position, Math.max(0, active.duration - .001));
    void persist([...active.cues.filter(c => c.slot !== slot), { slot, position_us: Math.round(p * 1e6), label: `Cue ${slotName(slot)}` }]);
  };
  const seek = (v: number, play = false) => {
    if (!audio.current || !active || active.missing) return;
    const p = Math.min(active.duration, Math.max(0, v));
    audio.current.currentTime = p; setPosition(p);
    if (play) void audio.current.play().catch(e => setNotice(String(e)));
  };
  const toggle = () => {
    if (!audio.current || !active || active.missing) return;
    if (audio.current.paused) void audio.current.play().catch(e => setNotice(String(e))); else audio.current.pause();
  };
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
      if (['INPUT', 'TEXTAREA', 'SELECT'].includes(el.tagName) || el.isContentEditable) return;
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
  const waveStart = active ? Math.max(0, Math.min(active.duration - active.duration / zoom, position - active.duration / zoom / 2)) : 0;
  const waveLength = active ? active.duration / zoom : 1;
  const wavePosition = (clientX: number, element: HTMLElement) => waveStart + Math.max(0, Math.min(1, (clientX - element.getBoundingClientRect().left) / element.clientWidth)) * waveLength;
  useEffect(() => {
    const finishDrag = (event: PointerEvent) => {
      if (drag.current === null || !waveElement.current || !active) return;
      const slot = drag.current;
      const seconds = Math.min(active.duration - .001, wavePosition(event.clientX, waveElement.current));
      drag.current = null; setDragPreview(null);
      updateCue(slot, { position_us: Math.round(seconds * 1e6) });
    };
    window.addEventListener('pointerup', finishDrag, true);
    return () => window.removeEventListener('pointerup', finishDrag, true);
  });

  return <div className="app"><aside className="sidebar"><div className="brand"><div className="brand-icon"><Music2 size={18}/></div><strong>CrateLoom</strong></div><div className="side-label">LIBRARY</div><button className="nav active" onClick={() => setEditor(false)}><FolderOpen size={17}/> All Tracks <span>{tracks.length}</span></button><div className="sidebar-bottom"><label htmlFor="appearance">Appearance</label><select id="appearance" value={theme} onChange={e => setTheme(e.target.value)}><option value="system">System</option><option value="dark">Dark</option><option value="light">Light</option></select></div></aside><main>
    <header><div className="heading">{editor && <button className="icon" aria-label="Back to library" title="Back to library" onClick={() => setEditor(false)}><ArrowLeft size={19}/></button>}<h1>{editor ? 'Cue Editor' : 'All Tracks'}</h1></div><div className="header-actions">{!editor && <label className="search"><Search size={16}/><input ref={search} aria-label="Search tracks" placeholder="Search tracks" value={query} onChange={e => setQuery(e.target.value)}/></label>}<button className="primary" disabled={locked} onClick={() => void importFile()}><Plus size={16}/> Add Music</button></div></header>
    {notice && <div className="notice" role="status"><span>{notice}</span><button onClick={() => setNotice('')}>Dismiss</button></div>}
    {editor && active ? <section className="editor"><div className="trackhead"><div className="cover large"><Music2 size={24}/></div><div className="track-details"><h2 title={active.title}>{active.title}</h2><p>{active.artist || 'Unknown artist'} · {time(active.duration)} · {active.codec.toUpperCase()}{active.sample_rate ? ` · ${active.sample_rate} Hz` : ''}</p></div><div className="push"/><button disabled={locked || active.missing} onClick={() => void analyze(active)}><Activity size={16}/>{active.analysis ? 'Rebuild waveform' : 'Analyze waveform'}</button><button disabled={locked || active.missing} onClick={() => void exportTrack()}><Upload size={16}/> Export XML</button></div>
      {active.missing && <div className="analysis-note" role="alert">File missing. Reconnect the drive or restore the original file to continue.</div>}
      <div className="wave-label"><span>WAVEFORM</span><span>{active.analysis ? 'Decoded audio envelope' : 'Not analyzed'}</span></div>
      <div ref={waveElement} className="wave" role="slider" tabIndex={0} aria-label="Seek track waveform" aria-valuemin={0} aria-valuemax={active.duration} aria-valuenow={position} onKeyDown={e => { if (e.key === 'ArrowRight') { e.preventDefault(); seek(position + 1); } if (e.key === 'ArrowLeft') { e.preventDefault(); seek(position - 1); } }} onClick={e => { if (drag.current === null) seek(wavePosition(e.clientX, e.currentTarget)); }}>
        {active.analysis ? <svg viewBox={`${waveStart / active.duration * 1000} 0 ${1000 / zoom} 100`} preserveAspectRatio="none" aria-hidden="true">{active.analysis.peaks.map((p, i) => <line key={i} x1={i * 1000 / Math.max(1, active.analysis!.peaks.length - 1)} x2={i * 1000 / Math.max(1, active.analysis!.peaks.length - 1)} y1={50 - p * 48} y2={50 + p * 48}/>)}</svg> : <div className="wave-empty">{busy || 'Analyze to see the waveform'}</div>}
        {position >= waveStart && position <= waveStart + waveLength && <div className="playhead" style={{left: `${(position - waveStart) / waveLength * 100}%`}}/>}
        {active.cues.filter(c => c.position_us / 1e6 >= waveStart && c.position_us / 1e6 <= waveStart + waveLength).map(c => <button key={c.slot} className="marker" aria-label={`Move cue ${slotName(c.slot)}; use Seconds for precise editing`} title={`${c.label} ${time(c.position_us / 1e6)} · drag to move`} style={{left: `${((dragPreview?.slot === c.slot ? dragPreview.position : c.position_us / 1e6) - waveStart) / waveLength * 100}%`}} disabled={locked || active.missing} onClick={e => e.stopPropagation()} onPointerDown={e => { e.stopPropagation(); drag.current = c.slot; audio.current?.pause(); setDragPreview({slot: c.slot, position: c.position_us / 1e6}); e.currentTarget.setPointerCapture(e.pointerId); }} onPointerMove={e => { const wave = e.currentTarget.parentElement; if (wave && drag.current === c.slot) setDragPreview({slot: c.slot, position: Math.min(active.duration - .001, wavePosition(e.clientX, wave))}); }} onPointerCancel={() => { drag.current = null; setDragPreview(null); }}>{slotName(c.slot)}</button>)}
      </div><div className="wave-times"><span>{time(waveStart)}</span><span>{time(waveStart + waveLength)}</span></div>
      <div className="editor-transport"><button onClick={toggle} disabled={active.missing}>{playing ? <Pause size={16}/> : <Play size={16}/>} {playing ? 'Pause' : 'Play'}</button><span className="time">{time(position)} / {time(active.duration)}</span><label className="zoom">Zoom <select aria-label="Waveform zoom" value={zoom} onChange={e => setZoom(Number(e.target.value))}><option value="1">Full track</option><option value="2">2×</option><option value="4">4×</option><option value="8">8×</option></select></label><div className="push"/><span className="muted" role="status">{saved}</span><button disabled={locked || !history.length} onClick={() => { const prev = history.at(-1); if (prev) void persist(prev, 'undo'); }}>Undo</button><button disabled={locked || !future.length} onClick={() => { const next = future.at(-1); if (next) void persist(next, 'redo'); }}>Redo</button></div>
      <div className="cue-title"><div><h2>Hot cues</h2><p>Set at the playhead. Select to audition; drag a marker to move it.</p></div><span>1–8 to play</span></div><div className="cues">{Array.from({length: 8}, (_, slot) => { const c = cueAt(slot); return <div className="cue" key={slot}><button className="cue-trigger" disabled={locked || active.missing} onClick={() => c ? seek(c.position_us / 1e6, true) : setCue(slot)}><span className="slot">{slotName(slot)}</span><span>{c ? c.label : 'Add cue'}<small>{c ? time(c.position_us / 1e6) : 'At playhead'}</small></span></button>{c && <div className="cue-edit"><label>Label<input aria-label={`Cue ${slotName(slot)} label`} defaultValue={c.label} key={c.label} maxLength={120} disabled={locked} onBlur={e => { const label = e.target.value.trim(); if (label && label !== c.label) updateCue(slot, {label}); else e.target.value = c.label; }}/></label><label>Seconds<input aria-label={`Cue ${slotName(slot)} seconds`} type="number" min="0" max={active.duration} step="0.001" defaultValue={c.position_us / 1e6} key={c.position_us} disabled={locked} onBlur={e => { const n = Number(e.target.value); if (e.target.value !== '' && Number.isFinite(n) && n >= 0 && n < active.duration) { const us = Math.round(n * 1e6); if (us !== c.position_us) updateCue(slot, {position_us: us}); } else { e.target.value = String(c.position_us / 1e6); setNotice('Cue must be within the track.'); } }}/></label><button disabled={locked} onClick={() => void persist(active.cues.filter(x => x.slot !== slot))}>Remove</button></div>}</div>; })}</div><div className="analysis-note">Tempo and song sections are not available yet. Cue positions are manual and need review.</div></section> :
    <section className="library"><div className="table-head"><span>Title</span><span>Artist</span><span>Duration</span><span>Status</span></div>{visible.length ? visible.map(t => <button key={t.id} disabled={locked} className={`track-row ${active?.id === t.id ? 'selected' : ''}`} onClick={() => void choose(t)} onDoubleClick={() => void choose(t, true)}><span className="track-name"><span className="cover"><Music2 size={16}/></span><strong title={t.title}>{t.title}</strong></span><span className="truncate" title={t.artist}>{t.artist || 'Unknown artist'}</span><span className="time">{time(t.duration)}</span><span><span className={`status ${t.missing ? 'missing' : ''}`}>{t.missing ? 'Missing file' : t.cues.length ? 'Needs review' : 'Imported'}</span></span></button>) : <div className="empty"><div className="empty-icon"><FolderOpen size={27}/></div><h2>{query ? 'No matching tracks' : 'Your library starts here'}</h2><p>{query ? 'Try a different search.' : 'Add a local audio file to prepare it for your next set.'}</p>{!query && <button disabled={locked} onClick={() => void importFile()}><Plus size={16}/> Add Music</button>}</div>}</section>}
    <audio ref={audio} src={active && !active.missing ? convertFileSrc(active.path) : undefined} onTimeUpdate={e => setPosition(e.currentTarget.currentTime)} onPlay={() => setPlaying(true)} onPause={() => setPlaying(false)} onEnded={() => setPlaying(false)} onError={() => active && !active.missing && setNotice('Playback failed. Check that the file is available and supported.')}/>
    {!editor && <footer><div className="now"><div className="cover"><Music2 size={16}/></div><div><strong>{active?.title || 'No track selected'}</strong><small>{active?.artist || 'Select a track to get started'}</small></div></div><div className="player"><div><button className="icon" aria-label="Return to start" onClick={() => seek(0)} disabled={!active}><SkipBack size={17}/></button><button className="play" aria-label={playing ? 'Pause' : 'Play'} onClick={toggle} disabled={!active || active.missing}>{playing ? <Pause size={18}/> : <Play size={18}/>}</button><span className="time">{time(position)} / {time(active?.duration || 0)}</span></div><input aria-label="Playback position" type="range" min="0" max={active?.duration || 1} step="0.01" value={position} onChange={e => seek(Number(e.target.value))}/></div><button disabled={!active} onClick={() => setEditor(true)}>Edit Cues</button></footer>}
    {busy && <div className="busy" role="status">{busy}</div>}
  </main></div>;
}
createRoot(document.getElementById('root')!).render(<App/>);
