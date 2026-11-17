import React, {useEffect, useMemo, useRef, useState, type MutableRefObject, type RefObject} from 'react';
import {convertFileSrc} from '@tauri-apps/api/core';
import {Activity, ChevronLeft, ChevronRight, Crosshair, Link2, LoaderCircle, Music2, Pause, Play, Redo2, Repeat, RotateCcw, SlidersHorizontal, Tags, Trash2, Undo2, Upload, WandSparkles, X, ZoomIn, ZoomOut} from 'lucide-react';
import {Select} from './Select';
import {Menu} from './Menu';
import {PanelPresence} from './PanelMotion';
import {WaveBars} from './WaveBars';
import {WheelInput, type WheelMode} from './wheel-input';
import {beatCoordinate, completeBeats, jumpBeats, lowerBound} from './beat-grid';
import {playhead, usePlayhead, usePlayheadValue} from './playhead';
import {useDialogFocus} from './useDialogFocus';
import {SCRATCH_MAX_SECONDS, useScratch} from './useScratch';
import {ANALYSIS_MAX_SECONDS, cueColors, plural, preciseTime, sectionColors, sectionLabels, slotName, time, typingTarget, type AnalysisData, type Cue, type Job, type Section, type Track} from './types';

export type Loop = {start: number; end: number};
export type EditorActions = {
  seek: (seconds: number, play?: boolean) => void;
  toggle: () => void; play: () => void; pause: () => void;
  setLoop: (loop: Loop | null) => void;
  persistCues: (cues: Cue[]) => Promise<boolean>;
  undoCues: () => void; redoCues: () => void; autoCues: () => void;
  fixGrid: (grid: {bpm: number; origin_us: number; bar_one: boolean}) => void;
  offsetGrid: (offset_us: number) => void;
  resetGrid: () => void; undoGrid: () => void; redoGrid: () => void;
  saveSections: (sections: Section[]) => Promise<boolean>;
  analyze: () => void; findSections: (mode: 'fast' | 'model') => void; cancelAnalysis: () => void;
  exportTrack: () => void; openMetadata: () => void; relink: () => void;
  notify: (text: string) => void;
};

const emptyPeaks: number[] = [];
const clamp = (value: number, low: number, high: number) => Math.max(low, Math.min(high, value));
const jumpSizes = [.25, .5, 1, 2, 4, 8, 16, 32, 64];
const beatCount = (n: number) => `${n === .25 ? '¼' : n === .5 ? '½' : n} ${n <= 1 ? 'beat' : 'beats'}`;

function useStored<T extends string | number>(key: string, initial: T, allowed: readonly T[]): [T, (value: T) => void] {
  const [value, setValue] = useState<T>(() => {
    const raw = localStorage.getItem(key);
    const parsed = (typeof initial === 'number' ? Number(raw) : raw) as T;
    return raw !== null && allowed.includes(parsed) ? parsed : initial;
  });
  return [value, next => { setValue(next); localStorage.setItem(key, String(next)); }];
}

/** Elapsed and total time; re-renders once a second instead of on every frame. */
function Clock({duration}: {duration: number}) {
  const second = usePlayheadValue(position => Math.floor(position));
  return <span className="time editor-clock" aria-label="Position">{time(second)} / {time(duration)}</span>;
}

/** The whole track: waveform, sections, cues and the playhead. Clicking or arrow keys seek. Also shown under the playing row in the track list. */
export function Overview({track, detail, loop = null, tempCue, onSeek, label = 'Full track overview'}: {track: Track; detail: AnalysisData | null; loop?: Loop | null; tempCue?: number; onSeek: (seconds: number) => void; label?: string}) {
  const position = usePlayhead();
  const percent = (seconds: number) => `${seconds / track.duration * 100}%`;
  return <div className="overview" role="slider" tabIndex={0} aria-label={label} aria-valuemin={0} aria-valuemax={Math.round(track.duration)} aria-valuenow={Math.round(position)} aria-valuetext={`${time(position)} of ${time(track.duration)}`}
    onClick={event => onSeek((event.clientX - event.currentTarget.getBoundingClientRect().left) / event.currentTarget.clientWidth * track.duration)}
    onKeyDown={event => { if (event.key === 'ArrowRight' || event.key === 'ArrowLeft') { event.preventDefault(); onSeek(playhead.get() + (event.key === 'ArrowRight' ? 5 : -5)); } }}>
    <WaveBars data={detail} fallback={track.analysis?.peaks || emptyPeaks} start={0} length={track.duration} duration={track.duration} overview/>
    {track.sections.map(section => <div className="section-band" key={section.id} title={`${section.label} · ${time(section.start_us / 1e6)}–${time(section.end_us / 1e6)}`}
      style={{left: percent(section.start_us / 1e6), width: percent((section.end_us - section.start_us) / 1e6), background: sectionColors[section.label] || sectionColors.Other}}>{section.label}</div>)}
    {loop && <div className="loop-band" style={{left: percent(loop.start), width: percent(loop.end - loop.start)}}/>}
    {track.cues.map(cue => <span className="overview-cue" key={cue.slot} style={{left: percent(cue.position_us / 1e6), background: cue.color || cueColors[cue.slot]}}>{slotName(cue.slot)}</span>)}
    {tempCue !== undefined && <span className="temp-cue-arrow" aria-hidden="true" style={{left: percent(tempCue)}}/>}
    <div className="playhead" style={{left: percent(position)}}/>
  </div>;
}

type WaveProps = {
  track: Track; detail: AnalysisData | null; beats: number[]; anchorIndex: number; zoom: number; setZoom: (update: (zoom: number) => number) => void;
  wheelMode: WheelMode; snapTime: (seconds: number) => number; locked: boolean; loop: Loop | null; tempCue: number; selectedSlot: number | null;
  audio: RefObject<HTMLAudioElement>; scrubbing: MutableRefObject<boolean>; cancelScrub: MutableRefObject<() => void>; volume: number;
  onSelectCue: (slot: number) => void; onMoveCue: (slot: number, seconds: number) => void; onJump: (direction: number) => void; actions: EditorActions;
};

/** The zoomed waveform: drag or scroll to scrub, drag a cue marker to move it. */
function DetailWave({track, detail, beats, anchorIndex, zoom, setZoom, wheelMode, snapTime, locked, loop, tempCue, selectedSlot, audio, scrubbing, cancelScrub, volume, onSelectCue, onMoveCue, onJump, actions}: WaveProps) {
  const position = usePlayhead();
  const wave = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(1000);
  const [dragPreview, setDragPreview] = useState<{slot: number; position: number} | null>(null);
  const scrub = useRef<{pointer: number; x: number; position: number; wasPlaying: boolean; moved: boolean} | null>(null);
  const markerDrag = useRef<{slot: number; pointer: number; x: number; moved: boolean} | null>(null);
  const frame = useRef(0), pending = useRef<number | null>(null), lastFrame = useRef(0);
  const scratchEnabled = !track.missing && track.duration <= SCRATCH_MAX_SECONDS;
  const scratch = useScratch(track.path, scratchEnabled, volume, () => actions.notify('Scrub audio could not load for this file. Dragging the waveform still moves the playhead.'));

  const waveLength = track.duration / zoom, waveStart = position - waveLength / 2;
  const positionAt = (clientX: number) => {
    const el = wave.current; if (!el) return playhead.get();
    const bounds = el.getBoundingClientRect(), length = track.duration / zoom;
    return playhead.get() - length / 2 + clamp((clientX - bounds.left) / bounds.width, 0, 1) * length;
  };
  const cancel = () => { cancelAnimationFrame(frame.current); frame.current = 0; pending.current = null; };
  cancelScrub.current = cancel;
  const preview = (value: number) => {
    pending.current = value;
    if (frame.current) return;
    frame.current = requestAnimationFrame(now => {
      frame.current = 0; const next = pending.current; pending.current = null;
      if (next == null) return;
      scratch.sound(playhead.get(), next, lastFrame.current ? (now - lastFrame.current) / 1000 : 1 / 60);
      lastFrame.current = now; playhead.set(next);
    });
  };
  const commit = () => {
    const next = pending.current ?? playhead.get();
    cancel(); lastFrame.current = 0; scratch.stop(); actions.seek(next);
  };

  // The wheel and pinch listeners are attached once; they read the current track and callbacks from here.
  const latest = useRef({track, zoom, wheelMode, scratch, preview, commit, actions, setZoom, audio});
  latest.current = {track, zoom, wheelMode, scratch, preview, commit, actions, setZoom, audio};
  useEffect(() => {
    const el = wave.current; if (!el) return;
    const resize = () => setWidth(el.clientWidth); resize();
    const observer = new ResizeObserver(resize); observer.observe(el);
    let base = latest.current.zoom, gesturing = false, wheelTimer = 0, wheelActive = false, resume = false;
    const finish = () => {
      if (!wheelActive) return;
      clearTimeout(wheelTimer); latest.current.commit(); wheelActive = false; scrubbing.current = !!scrub.current;
      if (resume) latest.current.actions.play();
    };
    const wheelInput = new WheelInput();
    const wheel = (event: WheelEvent) => {
      event.preventDefault(); if (gesturing) return;
      const now = latest.current;
      if (wheelInput.action(event, now.wheelMode) === 'zoom') { finish(); now.setZoom(z => clamp(z * Math.exp(-event.deltaY * .003), 1, 128)); return; }
      const media = now.audio.current;
      if (!media || now.track.missing) return;
      if (!wheelActive) { wheelActive = true; scrubbing.current = true; resume = !media.paused; now.actions.pause(); now.scratch.load(); }
      const delta = Math.abs(event.deltaX) > Math.abs(event.deltaY) ? event.deltaX : event.deltaY;
      const from = pending.current ?? playhead.get();
      now.preview(clamp(from + delta / el.clientWidth * now.track.duration / now.zoom, 0, now.track.duration));
      clearTimeout(wheelTimer); wheelTimer = window.setTimeout(finish, 120);
    };
    const begin = (event: Event) => { event.preventDefault(); finish(); gesturing = true; base = latest.current.zoom; };
    const change = (event: Event) => { event.preventDefault(); const scale = (event as Event & {scale: number}).scale; if (Number.isFinite(scale)) latest.current.setZoom(() => clamp(base * scale, 1, 128)); };
    const end = (event: Event) => { event.preventDefault(); gesturing = false; };
    el.addEventListener('wheel', wheel, {passive: false}); el.addEventListener('gesturestart', begin, {passive: false});
    el.addEventListener('gesturechange', change, {passive: false}); el.addEventListener('gestureend', end, {passive: false});
    return () => {
      observer.disconnect(); clearTimeout(wheelTimer);
      cancelAnimationFrame(frame.current); frame.current = 0; pending.current = null; scrubbing.current = false; latest.current.scratch.stop();
      el.removeEventListener('wheel', wheel); el.removeEventListener('gesturestart', begin); el.removeEventListener('gesturechange', change); el.removeEventListener('gestureend', end);
    };
  }, []);

  const beginScrub = (event: React.PointerEvent<HTMLDivElement>) => {
    if (event.button !== 0 || track.missing) return;
    event.preventDefault(); event.currentTarget.setPointerCapture(event.pointerId); event.currentTarget.focus({preventScroll: true});
    scrub.current = {pointer: event.pointerId, x: event.clientX, position: playhead.get(), wasPlaying: !!audio.current && !audio.current.paused, moved: false};
    scrubbing.current = true; actions.pause(); scratch.load();
  };
  const moveScrub = (event: React.PointerEvent<HTMLDivElement>) => {
    const drag = scrub.current; if (!drag || drag.pointer !== event.pointerId) return;
    const delta = event.clientX - drag.x; if (Math.abs(delta) > 2) drag.moved = true; if (!drag.moved) return;
    preview(clamp(drag.position - delta / event.currentTarget.clientWidth * track.duration / zoom, 0, track.duration));
  };
  const endScrub = (event: React.PointerEvent<HTMLDivElement>, cancelled = false) => {
    const drag = scrub.current; if (!drag || drag.pointer !== event.pointerId) return;
    if (drag.moved) commit(); else if (!cancelled) { cancel(); actions.seek(positionAt(event.clientX)); }
    scrub.current = null; scrubbing.current = false; scratch.stop();
    if (drag.wasPlaying) actions.play();
  };

  const firstBeat = lowerBound(beats, waveStart - 1e-7), lastBeat = lowerBound(beats, waveStart + waveLength + 1e-7);
  const currentBeat = beats.length ? Math.floor(beatCoordinate(beats, position) + 1e-6) : 0;
  const beatPixels = width * (60 / (track.grid_bpm || detail?.tempo || 120)) / waveLength;
  const stride = beatPixels >= 5 ? 1 : beatPixels * 4 >= 5 ? 4 : 16;
  const left = (seconds: number) => `${(seconds - waveStart) / waveLength * 100}%`;
  const inView = (seconds: number) => seconds >= waveStart && seconds <= waveStart + waveLength;
  const estimated = track.grid_anchor_us == null;

  return <div className="wave-detail-body">
    <div ref={wave} className="wave" role="group" aria-label="Detail waveform" onPointerEnter={() => scratch.load()}>
      <div className="wave-surface" role="slider" tabIndex={0} aria-label="Seek in the waveform" aria-valuemin={0} aria-valuemax={Math.round(track.duration)} aria-valuenow={Math.round(position)} aria-valuetext={`${time(position)} of ${time(track.duration)}`}
        onKeyDown={event => { if (event.key === 'ArrowRight' || event.key === 'ArrowLeft') { event.preventDefault(); const direction = event.key === 'ArrowRight' ? 1 : -1; if (event.shiftKey) onJump(direction); else { cancel(); actions.seek(playhead.get() + direction); } } }}
        onPointerDown={beginScrub} onPointerMove={moveScrub} onPointerUp={event => endScrub(event)} onPointerCancel={event => endScrub(event, true)} onLostPointerCapture={event => endScrub(event, true)}>
        {track.analysis || detail ? <WaveBars data={detail} fallback={track.analysis?.peaks || emptyPeaks} start={waveStart} length={waveLength} duration={track.duration}/> : <div className="wave-empty">{track.detail_analysis ? 'Loading waveform…' : 'The waveform appears after analysis'}</div>}
        {beats.slice(firstBeat, lastBeat).map((beat, i) => {
          const index = firstBeat + i, isBar = anchorIndex >= 0 && (index - anchorIndex) % 4 === 0;
          if (stride > 1 && (index - anchorIndex) % stride !== 0) return null;
          return <span key={index} className={`beat-line ${isBar ? 'bar-line' : ''}`} data-beat-index={index} style={{left: left(beat)}}>{isBar && beatPixels * 4 >= 32 ? <span className="bar-number">{Math.floor((index - anchorIndex) / 4) + 1}</span> : null}</span>;
        })}
        {track.sections.filter(section => section.end_us / 1e6 > waveStart && section.start_us / 1e6 < waveStart + waveLength).map(section => {
          const from = Math.max(0, (section.start_us / 1e6 - waveStart) / waveLength * 100), to = Math.min(100, (section.end_us / 1e6 - waveStart) / waveLength * 100);
          return <div key={section.id} className="section-band detail-section" style={{left: `${from}%`, width: `${to - from}%`, background: sectionColors[section.label] || sectionColors.Other}}>{section.label}</div>;
        })}
        {loop && loop.end > waveStart && loop.start < waveStart + waveLength && <div className="loop-band" style={{left: left(Math.max(waveStart, loop.start)), width: `${(Math.min(waveStart + waveLength, loop.end) - Math.max(waveStart, loop.start)) / waveLength * 100}%`}}/>}
        {inView(tempCue) && <span className="temp-cue-arrow" aria-hidden="true" style={{left: left(tempCue)}}/>}
        {inView(position) && <div className="playhead" style={{left: left(position)}}/>}
      </div>
      {track.cues.map(cue => {
        const at = dragPreview?.slot === cue.slot ? dragPreview.position : cue.position_us / 1e6;
        if (!inView(at)) return null;
        const choose = () => { onSelectCue(cue.slot); cancel(); actions.seek(cue.position_us / 1e6); };
        return <button key={cue.slot} className="marker" data-selected={selectedSlot === cue.slot} disabled={locked || track.missing}
          aria-label={`Cue ${slotName(cue.slot)}, ${cue.label}, at ${preciseTime(cue.position_us / 1e6)}. Drag to move it.`} title={`${cue.label} · ${preciseTime(cue.position_us / 1e6)} · drag to move`}
          style={{left: left(at), background: cue.color || cueColors[cue.slot]}}
          onClick={event => { if (event.detail === 0) choose(); }}
          onPointerDown={event => { if (event.button !== 0) return; event.stopPropagation(); event.currentTarget.setPointerCapture(event.pointerId); markerDrag.current = {slot: cue.slot, pointer: event.pointerId, x: event.clientX, moved: false}; }}
          onPointerMove={event => {
            const drag = markerDrag.current; if (!drag || drag.pointer !== event.pointerId) return;
            // A plain click must not move the cue: only a real drag does.
            if (!drag.moved) { if (Math.abs(event.clientX - drag.x) < 4) return; drag.moved = true; actions.pause(); }
            setDragPreview({slot: cue.slot, position: clamp(positionAt(event.clientX), 0, track.duration - .001)});
          }}
          onPointerUp={event => {
            const drag = markerDrag.current; if (!drag || drag.pointer !== event.pointerId) return;
            markerDrag.current = null; setDragPreview(null);
            if (drag.moved) onMoveCue(cue.slot, snapTime(clamp(positionAt(event.clientX), 0, track.duration - .001))); else choose();
          }}
          onPointerCancel={() => { markerDrag.current = null; setDragPreview(null); }}
          onLostPointerCapture={() => { if (markerDrag.current?.slot === cue.slot) { markerDrag.current = null; setDragPreview(null); } }}>{slotName(cue.slot)}</button>;
      })}
    </div>
    <div className="wave-times">
      <span>{time(Math.max(0, waveStart))}</span>
      <span title={estimated ? 'Estimated bar and beat. Set bar 1 in the beat grid to confirm the bars.' : 'Bar and beat in the confirmed grid'}>
        {anchorIndex >= 0 ? `Bar ${estimated ? '~' : ''}${Math.floor((currentBeat - anchorIndex) / 4) + 1}.${((currentBeat - anchorIndex) % 4 + 4) % 4 + 1}` : beats.length ? 'Bar —' : ''}</span>
      <span>{time(Math.min(track.duration, waveStart + waveLength))}</span>
    </div>
  </div>;
}

function GridPanel({track, detail, beats, locked, canUndo, canRedo, actions}: {track: Track; detail: AnalysisData | null; beats: number[]; locked: boolean; canUndo: boolean; canRedo: boolean; actions: EditorActions}) {
  const [error, setError] = useState('');
  const taps = useRef<number[]>([]);
  const bpm = track.grid_bpm || detail?.tempo || null;
  const offsetSeconds = (track.grid_offset_us || 0) / 1e6;
  const confirmed = track.grid_anchor_us != null, straight = track.grid_bpm != null;
  const off = !detail || locked;
  const nearestBeat = (position: number) => beats.reduce((best, beat) => Math.abs(beat - position) < Math.abs(best - position) ? beat : best);
  const setBpm = (value: number) => {
    if (!detail || !Number.isFinite(value) || value < 20 || value > 400) { setError('Tempo must be between 20 and 400 BPM.'); return; }
    setError('');
    const position = playhead.get();
    const origin = track.grid_anchor_us != null ? track.grid_anchor_us : track.grid_origin_us != null ? track.grid_origin_us
      : Math.round(((beats.length ? nearestBeat(position) : position) - offsetSeconds) * 1e6);
    actions.fixGrid({bpm: value, origin_us: origin, bar_one: confirmed});
  };
  const tap = () => {
    const now = performance.now();
    taps.current = [...taps.current.filter(at => now - at < 3000), now].slice(-5);
    if (taps.current.length < 3) return;
    const samples = taps.current, value = 60000 * (samples.length - 1) / (samples.at(-1)! - samples[0]);
    if (value >= 20 && value <= 400) setBpm(Math.round(value * 100) / 100);
  };
  const shift = (event: React.MouseEvent, direction: number) => actions.offsetGrid(track.grid_offset_us + direction * (event.shiftKey ? 10000 : 1000));
  return <section className="grid-panel" aria-label="Beat grid">
    <div className="grid-panel-head">
      <h2>Beat grid</h2>
      {detail && <strong className="grid-kind" title={straight ? 'Evenly spaced beats at one tempo' : detail.grid?.stable === false
        ? 'The tempo of this track drifts, so the grid follows each detected beat. Setting a tempo or bar 1 replaces it with a straight grid.'
        : 'The grid follows each detected beat. Setting a tempo or bar 1 replaces it with a straight grid.'}>{straight ? 'Straight grid (steady tempo)' : 'Detected beats (tempo varies)'}</strong>}
      <p>{!detail ? (track.detail_analysis ? 'Loading the beat grid…' : 'Analyse the track to get a beat grid.') : confirmed
        ? `Bar 1 is confirmed at ${preciseTime((track.grid_anchor_us! + track.grid_offset_us) / 1e6)}, so this grid is included when you export rekordbox XML.`
        : 'Bar 1 is not confirmed yet, so this grid is not exported. Put the playhead on the first downbeat and choose Set bar 1 at playhead to include the grid in the rekordbox XML export.'}</p>
    </div>
    <div className="grid-groups">
      <div className="grid-group" role="group" aria-label="Tempo">
        <span className="grid-caption">Tempo</span>
        <label className="bpm-field"><input aria-label="Tempo in BPM" title="Type a tempo and press Enter" type="number" min="20" max="400" step="0.01" disabled={off}
          key={`${track.id}-${track.grid_bpm}-${detail?.tempo}`} defaultValue={bpm?.toFixed(2) || ''} placeholder="—" onFocus={() => setError('')}
          onBlur={event => { const value = Number(event.target.value); if (!Number.isFinite(value) || value < 20 || value > 400) { event.target.value = bpm?.toFixed(2) || ''; setError('Tempo must be between 20 and 400 BPM.'); } else if (value !== bpm) setBpm(value); }}
          onKeyDown={event => { if (event.key === 'Enter') event.currentTarget.blur(); }}/> BPM</label>
        <button aria-label="Halve tempo" title="Halve the tempo" disabled={off || !bpm} onClick={() => setBpm(bpm! / 2)}>½</button>
        <button aria-label="Double tempo" title="Double the tempo" disabled={off || !bpm} onClick={() => setBpm(bpm! * 2)}>×2</button>
        <button title="Tap along with the beat at least three times to set the tempo" disabled={off} onClick={tap}>Tap</button>
      </div>
      <div className="grid-group" role="group" aria-label="Grid position">
        <span className="grid-caption">Position</span>
        <button aria-label="Shift grid earlier" title="Shift the grid 1 ms earlier (hold Shift for 10 ms)" disabled={off} onClick={event => shift(event, -1)}><ChevronLeft size={15}/> Earlier</button>
        <button aria-label="Shift grid later" title="Shift the grid 1 ms later (hold Shift for 10 ms)" disabled={off} onClick={event => shift(event, 1)}>Later <ChevronRight size={15}/></button>
        <button title="Move the nearest beat line onto the playhead" disabled={off || !beats.length} onClick={() => { const position = playhead.get(); actions.offsetGrid(Math.round((offsetSeconds + position - nearestBeat(position)) * 1e6)); }}><Crosshair size={15}/> Align to playhead</button>
      </div>
      <div className="grid-group" role="group" aria-label="Bar 1">
        <span className="grid-caption">Bar 1</span>
        <button title="Mark the playhead as beat 1 of bar 1. This confirms a fixed grid for the rekordbox XML export." disabled={off || !bpm}
          onClick={() => { if (bpm) actions.fixGrid({bpm, origin_us: Math.round((playhead.get() - offsetSeconds) * 1e6), bar_one: true}); }}>Set bar 1 at playhead</button>
      </div>
      <div className="grid-group" role="group" aria-label="Grid changes">
        <button aria-label="Undo grid change" title="Undo grid change" disabled={locked || !canUndo} onClick={actions.undoGrid}><Undo2 size={15}/></button>
        <button aria-label="Redo grid change" title="Redo grid change" disabled={locked || !canRedo} onClick={actions.redoGrid}><Redo2 size={15}/></button>
        <button title="Discard your tempo, position and bar 1 changes and return to the detected beats" disabled={off} onClick={actions.resetGrid}><RotateCcw size={15}/> Reset</button>
      </div>
    </div>
    {error && <p className="inline-error" role="alert">{error}</p>}
  </section>;
}

function SectionEditor({track, locked, onSave, onClose}: {track: Track; locked: boolean; onSave: (sections: Section[]) => Promise<boolean>; onClose: () => void}) {
  const dialog = useDialogFocus(onClose, false);
  const [editing, setEditing] = useState<string | null>(null);
  const [label, setLabel] = useState('Intro'), [start, setStart] = useState('0'), [end, setEnd] = useState(''), [error, setError] = useState('');
  const reset = () => { setEditing(null); setStart('0'); setEnd(''); setError(''); };
  const submit = () => {
    const from = Number(start), to = Number(end);
    if (!end || !Number.isFinite(from) || !Number.isFinite(to) || from < 0 || to <= from || to > track.duration) { setError(`Enter a start and an end in seconds, between 0 and ${track.duration.toFixed(2)}, with the end after the start.`); return; }
    setError('');
    const next = [...track.sections.filter(section => section.id !== editing), {id: editing || crypto.randomUUID(), start_us: Math.round(from * 1e6), end_us: Math.round(to * 1e6), label}];
    void onSave(next).then(ok => { if (ok) reset(); });
  };
  const disabled = locked || track.missing;
  return <div className="modal-backdrop" onMouseDown={event => { if (event.target === event.currentTarget) onClose(); }}>
    <section ref={dialog} tabIndex={-1} className="section-window" role="dialog" aria-modal="true" aria-label="Edit sections">
      <div className="section-window-head"><h2>Edit sections</h2><button className="icon" aria-label="Close section editor" title="Close" onClick={onClose}><X size={16}/></button></div>
      <p className="muted">Sections mark the parts of the track on the waveform and guide Auto cues. Automatic labels are estimates: change or remove any that are wrong.</p>
      <form className="review-tools" onSubmit={event => { event.preventDefault(); submit(); }}>
        <label>Label <Select value={label} onChange={event => setLabel(event.target.value)}>{sectionLabels.map(name => <option key={name}>{name}</option>)}</Select></label>
        <label>Start (seconds) <input type="number" min="0" max={track.duration} step="0.01" value={start} onChange={event => setStart(event.target.value)}/></label>
        <label>End (seconds) <input type="number" min="0" max={track.duration} step="0.01" value={end} onChange={event => setEnd(event.target.value)}/></label>
        <button type="submit" disabled={disabled}>{editing ? 'Save section' : 'Add section'}</button>
        {editing && <button type="button" onClick={reset}>Cancel edit</button>}
      </form>
      {error && <p className="inline-error" role="alert">{error}</p>}
      {!track.sections.length && <p className="muted">No sections yet. Add one above, or run Analysis ▸ Find sections.</p>}
      {track.sections.map(section => <div className="section-row" key={section.id} data-editing={editing === section.id}>
        <span>{section.label} · {time(section.start_us / 1e6)}–{time(section.end_us / 1e6)}{section.origin === 'model' ? ' · automatic' : section.origin === 'model-reviewed' ? ' · automatic, reviewed' : ''}</span>
        <div>
          <button disabled={disabled} aria-label={`Edit ${section.label} at ${time(section.start_us / 1e6)}`} onClick={() => { setEditing(section.id); setLabel(section.label); setStart(String(section.start_us / 1e6)); setEnd(String(section.end_us / 1e6)); setError(''); }}>Edit</button>{' '}
          <button disabled={disabled} aria-label={`Remove ${section.label} at ${time(section.start_us / 1e6)}`} onClick={() => { if (editing === section.id) reset(); void onSave(track.sections.filter(item => item.id !== section.id)); }}>Remove</button>
        </div>
      </div>)}
    </section>
  </div>;
}

export function CueEditor({track, detail, playing, audio, scrubbing, loop, locked, saved, canUndo, canRedo, canUndoGrid, canRedoGrid, job, jobTitle, queued, sectionsAvailable, detailedSections, exporting, volume, actions}: {
  track: Track; detail: AnalysisData | null; playing: boolean; audio: RefObject<HTMLAudioElement>; scrubbing: MutableRefObject<boolean>; loop: Loop | null;
  locked: boolean; saved: string; canUndo: boolean; canRedo: boolean; canUndoGrid: boolean; canRedoGrid: boolean;
  job: Job | null; jobTitle: string; queued: boolean; sectionsAvailable: boolean; detailedSections: boolean; exporting: boolean; volume: number; actions: EditorActions;
}) {
  const [zoom, setZoom] = useState(16);
  const [beatJump, setBeatJump] = useStored<number>('beatJump', 16, jumpSizes);
  const [wheelMode, setWheelMode] = useStored<WheelMode>('waveWheelMode', 'auto', ['auto', 'mouse', 'trackpad']);
  const [snapChoice, setSnapChoice] = useStored<'off' | 'beat' | 'bar'>('cueSnap', 'off', ['off', 'beat', 'bar']);
  const [leadIn, setLeadIn] = useStored<number>('cueLeadIn', 4, [2, 4, 8]);
  const [loopLength, setLoopLength] = useStored<number>('loopLength', 4, [1, 2, 4, 8, 16]);
  const [selectedSlot, setSelectedSlot] = useState<number | null>(null);
  const [sectionsOpen, setSectionsOpen] = useState(false);
  const [tempCue, setTempCue] = useState(0);
  const tempCueAt = useRef(0), cuePreview = useRef(false), cueKeepPlaying = useRef(false);
  const cancelScrub = useRef<() => void>(() => {});

  const offsetSeconds = (track.grid_offset_us || 0) / 1e6;
  const beats = useMemo(() => {
    if (!detail) return [];
    return completeBeats(Array.isArray(detail.beats) ? detail.beats : [], track.duration, track.grid_bpm || detail.tempo, track.grid_origin_us != null ? track.grid_origin_us / 1e6 : null, offsetSeconds);
  }, [detail, offsetSeconds, track.duration, track.grid_bpm, track.grid_origin_us]);
  const anchorIndex = useMemo(() => {
    if (!beats.length) return -1;
    if (track.grid_anchor_us == null) return 0;
    const anchor = (track.grid_anchor_us + (track.grid_offset_us || 0)) / 1e6, index = lowerBound(beats, anchor - .001);
    return index < beats.length && Math.abs(beats[index] - anchor) <= .001 ? index : -1;
  }, [beats, track.grid_anchor_us, track.grid_offset_us]);
  // The stored preference is kept even when this track cannot honour it yet.
  const snap = (snapChoice === 'beat' && !beats.length) || (snapChoice === 'bar' && anchorIndex < 0) ? 'off' : snapChoice;
  const snapTime = (seconds: number) => {
    if (snap === 'off' || !beats.length) return seconds;
    const choices = snap === 'bar' ? beats.filter((_, i) => (i - anchorIndex) % 4 === 0) : beats;
    if (!choices.length) return seconds;
    const nearest = choices.reduce((best, beat) => Math.abs(beat - seconds) < Math.abs(best - seconds) ? beat : best);
    return Math.abs(nearest - seconds) <= .1 ? nearest : seconds;
  };

  const seek = (seconds: number, play = false) => { cancelScrub.current(); actions.seek(seconds, play); };
  const cueAt = (slot: number) => track.cues.find(cue => cue.slot === slot);
  const selectedCue = selectedSlot === null ? undefined : cueAt(selectedSlot);
  const updateCue = (slot: number, patch: Partial<Cue>) => void actions.persistCues(track.cues.map(cue => cue.slot === slot ? {...cue, ...patch} : cue));
  const setCue = (slot: number) => {
    if (track.missing) return;
    const at = Math.min(snapTime(playhead.get()), Math.max(0, track.duration - .001));
    void actions.persistCues([...track.cues.filter(cue => cue.slot !== slot), {slot, position_us: Math.round(at * 1e6), label: `Cue ${slotName(slot)}`, color: cueColors[slot]}]).then(ok => { if (ok) setSelectedSlot(slot); });
  };
  const playCue = (slot: number) => { const cue = cueAt(slot); if (!cue) return; setSelectedSlot(slot); seek(cue.position_us / 1e6, true); };
  const audition = (cue: Cue) => {
    const at = cue.position_us / 1e6, before = lowerBound(beats, at - .001);
    seek(before ? beats[Math.max(0, before - leadIn)] : Math.max(0, at - 5), true);
  };
  const jump = (direction: number) => {
    if (track.missing || beats.length < 2) return;
    const from = playhead.get(), target = jumpBeats(beats, from, direction * beatJump, track.duration);
    if (loop) {
      const length = loop.end - loop.start, start = clamp(loop.start + target - from, 0, track.duration - length);
      actions.setLoop({start, end: start + length});
      seek(clamp(from + start - loop.start, start, start + length));
    } else seek(target);
  };
  const loopInBeats = beats.length >= 2;
  const toggleLoop = () => {
    if (loop) { actions.setLoop(null); return; }
    const start = playhead.get(), end = loopInBeats ? jumpBeats(beats, start, loopLength, track.duration) : start + loopLength;
    if (end > track.duration || end - start < .05) { actions.notify('There is not enough of the track left to loop from here.'); return; }
    actions.setLoop({start, end}); actions.play();
  };

  // CUE works like a deck: while paused it marks the position, or plays from the mark while held; while playing it returns to the mark.
  const cueDown = () => {
    const media = audio.current; if (!media || track.missing) return;
    cueKeepPlaying.current = false;
    if (!media.paused) { actions.pause(); seek(tempCueAt.current); cuePreview.current = false; return; }
    if (Math.abs(media.currentTime - tempCueAt.current) > .05) { tempCueAt.current = media.currentTime; setTempCue(media.currentTime); cuePreview.current = false; return; }
    cuePreview.current = true; seek(tempCueAt.current, true);
  };
  const cueUp = () => { if (cuePreview.current && !cueKeepPlaying.current) { actions.pause(); seek(tempCueAt.current); } cuePreview.current = false; };
  const togglePlay = () => { if (cuePreview.current) { cueKeepPlaying.current = true; cuePreview.current = false; return; } actions.toggle(); };

  const keys = useRef<(event: KeyboardEvent) => void>(() => {});
  keys.current = event => {
    if (event.defaultPrevented || typingTarget(event)) return;
    const command = event.metaKey || event.ctrlKey;
    if (event.shiftKey && !command && (event.key === 'ArrowLeft' || event.key === 'ArrowRight')) { event.preventDefault(); jump(event.key === 'ArrowRight' ? 1 : -1); }
    else if (command && event.key.toLowerCase() === 'z') { event.preventDefault(); if (!locked) { if (event.shiftKey) actions.redoCues(); else actions.undoCues(); } }
    else if (!command && !event.altKey && /^[1-8]$/.test(event.key)) playCue(Number(event.key) - 1);
  };
  useEffect(() => {
    const handler = (event: KeyboardEvent) => keys.current(event);
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, []);

  const running = !!job && ['running', 'queued'].includes(job.state);
  const mine = job?.track_id === track.id;
  const bpm = track.grid_bpm || detail?.tempo || null;
  const analysisOff = locked || track.missing || running;
  const automatic = track.sections.filter(section => section.origin === 'model').length;
  const sectionSummary = !track.sections.length ? 'no sections yet'
    : `${plural(track.sections.length, 'section')}${automatic ? ` (${track.structure_analysis?.model.startsWith('Local') === false ? 'detailed model' : 'fast estimate'}${automatic < track.sections.length ? ', some edited' : ''})` : ' (edited by you)'}`;
  const jobName = job?.kind === 'structure' ? 'Section detection' : 'Beat analysis';

  return <section className="editor workspace-view">
    <div className="trackhead">
      <div className="cover large player-artwork">{track.metadata?.artwork_path ? <img src={convertFileSrc(track.metadata.artwork_path)} alt=""/> : <Music2 size={24}/>}</div>
      <div className="track-details">
        <h2 className="player-title" title={track.title}>{track.title}</h2>
        <p><span className="player-artist">{track.artist || 'Unknown artist'}</span> · {time(track.duration)} · {track.codec.toUpperCase()}{track.sample_rate ? ` · ${track.sample_rate} Hz` : ''}</p>
      </div>
      <div className="push"/>
      <div className="trackhead-actions">
        <button title="Edit title, artist, album and cover" onClick={actions.openMetadata}><Tags size={16}/> Metadata</button>
        <Menu label="Analysis" icon={<Activity size={16}/>} align="end" title="Run beat analysis or section detection again" disabled={analysisOff} items={[
          {label: detail ? 'Analyze again' : 'Analyze beats, key and waveform', description: 'Detects the tempo, key, beat positions and waveform colours.', onSelect: actions.analyze},
          sectionsAvailable && {label: 'Find sections (fast)', description: 'Estimates intro, breaks and outro from changes in energy and texture. Takes a few seconds.', onSelect: () => actions.findSections('fast')},
          detailedSections && {label: 'Find sections (detailed)', description: 'Uses a song-structure model that also labels verses and choruses. Slower.', onSelect: () => actions.findSections('model')},
        ]}/>
        <button disabled={track.missing || exporting} title="Save this track with its cues as a rekordbox XML file" onClick={actions.exportTrack}><Upload size={16}/> {exporting ? 'Exporting…' : 'Export XML'}</button>
      </div>
    </div>
    {track.missing && <div className="analysis-note" role="alert"><strong>The audio file is missing.</strong> Cues and analysis are kept. <button disabled={locked} onClick={actions.relink}><Link2 size={14}/> Relink file</button></div>}
    <div className="analysis-status" role="status">
      {running ? <>
        <LoaderCircle size={14} className="spin" aria-hidden="true"/>
        <span>{mine ? `${jobName}: ${job!.stage}…` : `Analysing “${jobTitle}” first${queued ? '; this track is waiting' : ''}.`}</span>
        <button onClick={actions.cancelAnalysis}>Cancel</button>
      </> : mine && job!.state === 'failed' ? <span className="inline-error">{jobName} failed: {job!.error || 'unknown error'}. Check that the file plays, then run it again from the Analysis menu.</span>
        : mine && job!.state === 'cancelled' ? <span>{jobName} was cancelled. Run it again from the Analysis menu.</span>
        : detail ? <span>{bpm ? `${bpm.toFixed(2)} BPM` : 'No tempo detected'}{track.detail_analysis?.key ? <> · <span title={`Detected key: ${track.detail_analysis.key.name}`}>{track.detail_analysis.key.camelot} ({track.detail_analysis.key.short})</span></> : ''} · {plural(beats.length, 'beat')} · {sectionSummary}</span>
        : track.detail_analysis ? <span>Loading analysis…</span>
        : <span>{track.missing ? 'Not analysed.' : queued ? 'Waiting for beat analysis…' : track.duration > ANALYSIS_MAX_SECONDS ? `Not analysed: tracks over ${ANALYSIS_MAX_SECONDS / 60} minutes are not analysed automatically. Use the Analysis menu to run it.` : 'Not analysed yet. Use the Analysis menu to detect beats and draw the waveform.'}</span>}
    </div>

    <div className="wave-label"><span>Full track</span>
      <button className="link-button" title="Add, rename or remove the sections shown on the waveform" onClick={() => setSectionsOpen(true)}><SlidersHorizontal size={13}/> Edit sections · {track.sections.length}</button></div>
    <Overview track={track} detail={detail} loop={loop} tempCue={tempCue} onSeek={seek}/>
    <div className="wave-label detail-label"><span>Detail</span>
      <span>Drag or two-finger scroll to scrub · Pinch or mouse wheel to zoom{track.duration > SCRATCH_MAX_SECONDS ? ' · No scrub audio for tracks this long' : ''}</span></div>
    <div className="wave-detail-layout">
      <div className="deck-transport">
        <button className="cue-transport" aria-label="Cue" title="Paused: marks this position, or plays from the mark while held. Playing: returns to the mark and stops." disabled={track.missing}
          onPointerDown={event => { event.currentTarget.setPointerCapture(event.pointerId); cueDown(); }} onClick={event => { if (event.detail === 0) { cueDown(); cueUp(); } }} onPointerUp={cueUp} onPointerCancel={cueUp}
          onKeyDown={event => { if ((event.key === ' ' || event.key === 'Enter') && !event.repeat) { event.preventDefault(); cueDown(); } }} onKeyUp={event => { if (event.key === ' ' || event.key === 'Enter') { event.preventDefault(); cueUp(); } }}>CUE</button>
        <button className="play-transport" onClick={togglePlay} aria-label={playing ? 'Pause' : 'Play'} title={playing ? 'Pause (Space)' : 'Play (Space)'} disabled={track.missing}>{playing ? <Pause size={21}/> : <Play size={21}/>}</button>
      </div>
      <DetailWave track={track} detail={detail} beats={beats} anchorIndex={anchorIndex} zoom={zoom} setZoom={setZoom} wheelMode={wheelMode} snapTime={snapTime} locked={locked} loop={loop} tempCue={tempCue}
        selectedSlot={selectedSlot} audio={audio} scrubbing={scrubbing} cancelScrub={cancelScrub} volume={volume} onSelectCue={setSelectedSlot}
        onMoveCue={(slot, seconds) => { setSelectedSlot(slot); updateCue(slot, {position_us: Math.round(seconds * 1e6)}); }} onJump={jump} actions={actions}/>
    </div>
    <div className="editor-transport">
      <Clock duration={track.duration}/>
      <div className="control-group" role="group" aria-label="Jump">
        <span className="control-caption">Jump</span>
        <div className="beat-jump">
          <button aria-label={`Jump back ${beatCount(beatJump)}`} title={`Jump back ${beatCount(beatJump)} (Shift+←)`} disabled={track.missing || beats.length < 2} onClick={() => jump(-1)}><ChevronLeft size={16}/></button>
          <Select aria-label="Jump length" value={beatJump} onChange={event => setBeatJump(Number(event.target.value))}>{jumpSizes.map(n => <option key={n} value={n}>{beatCount(n)}</option>)}</Select>
          <button aria-label={`Jump forward ${beatCount(beatJump)}`} title={`Jump forward ${beatCount(beatJump)} (Shift+→)`} disabled={track.missing || beats.length < 2} onClick={() => jump(1)}><ChevronRight size={16}/></button>
        </div>
      </div>
      <div className="control-group" role="group" aria-label="Loop">
        <button className="loop-toggle" aria-pressed={!!loop} disabled={track.missing} title={loop ? 'Stop looping and play on' : `Repeat the next ${loopLength} ${loopInBeats ? 'beats' : 'seconds'} from the playhead. Jump moves the loop.`} onClick={toggleLoop}><Repeat size={15}/> {loop ? 'Stop loop' : 'Loop'}</button>
        <Select aria-label="Loop length" value={loopLength} disabled={!!loop} onChange={event => setLoopLength(Number(event.target.value))}>{[1, 2, 4, 8, 16].map(n => <option key={n} value={n}>{n} {loopInBeats ? (n === 1 ? 'beat' : 'beats') : (n === 1 ? 'second' : 'seconds')}</option>)}</Select>
      </div>
      <div className="push"/>
      <div className="control-group" role="group" aria-label="Zoom">
        <span className="control-caption">Zoom</span>
        <div className="zoom"><button aria-label="Zoom out" title="Zoom out" disabled={zoom <= 1} onClick={() => setZoom(z => Math.max(1, z / 1.5))}><ZoomOut size={16}/></button><span className="time">{zoom.toFixed(zoom < 10 ? 1 : 0)}×</span><button aria-label="Zoom in" title="Zoom in" disabled={zoom >= 128} onClick={() => setZoom(z => Math.min(128, z * 1.5))}><ZoomIn size={16}/></button></div>
      </div>
      <label className="control-group" title="What scrolling over the waveform does. Auto tells a mouse wheel (zoom) from a trackpad (scrub).">
        <span className="control-caption">Scrolling</span>
        <Select aria-label="Scrolling over the waveform" value={wheelMode} onChange={event => setWheelMode(event.target.value as WheelMode)}><option value="auto">Auto</option><option value="mouse">Always zoom</option><option value="trackpad">Always scrub</option></Select>
      </label>
    </div>

    <GridPanel track={track} detail={detail} beats={beats} locked={locked} canUndo={canUndoGrid} canRedo={canRedoGrid} actions={actions}/>

    <div className="cue-title">
      <div><h2>Hot cues</h2><p>Click a pad or press 1–8 to play from that cue. An empty pad sets a cue at the playhead.</p></div>
      <div className="cue-tools">
        <span className="muted cue-save-status" role="status">{saved}</span>
        <label className="control-group" title="New and dragged cues land on the nearest beat or bar line when one is within 0.1 seconds."><span className="control-caption">Snap cues to</span>
          <Select aria-label="Snap cues to" value={snap} onChange={event => setSnapChoice(event.target.value as 'off' | 'beat' | 'bar')}><option value="off">Nothing</option><option value="beat" disabled={!beats.length}>Beats</option><option value="bar" disabled={anchorIndex < 0}>Bars</option></Select></label>
        <button disabled={locked || track.missing || (!detail && !track.sections.length)} title="Add cues at the first beat and at section starts, using free pads only" onClick={actions.autoCues}><WandSparkles size={15}/> Auto cues</button>
        <button disabled={locked || !canUndo} onClick={actions.undoCues} aria-label="Undo cue change" title="Undo cue change (⌘Z)"><Undo2 size={16}/></button>
        <button disabled={locked || !canRedo} onClick={actions.redoCues} aria-label="Redo cue change" title="Redo cue change (⇧⌘Z)"><Redo2 size={16}/></button>
      </div>
    </div>
    <div className="cues">{Array.from({length: 8}, (_, slot) => {
      const cue = cueAt(slot);
      return cue ? <button key={slot} className="cue-pad" aria-pressed={selectedSlot === slot} title={`Play from ${cue.label} (${slot + 1})`} onClick={() => playCue(slot)}>
        <span className="slot" style={{background: cue.color || cueColors[slot]}}>{slotName(slot)}</span>
        <span className="cue-pad-text"><span>{cue.label}</span><small>{preciseTime(cue.position_us / 1e6)}</small></span><kbd aria-hidden="true">{slot + 1}</kbd>
      </button> : <button key={slot} className="cue-pad cue-pad-empty" disabled={locked || track.missing} title={`Set cue ${slotName(slot)} at the playhead`} onClick={() => setCue(slot)}>
        <span className="slot">{slotName(slot)}</span><span className="cue-pad-text"><span>Set cue</span><small>at the playhead</small></span>
      </button>;
    })}</div>
    {selectedCue ? <div className="cue-editor" role="group" aria-label={`Cue ${slotName(selectedCue.slot)}`} key={selectedCue.slot}>
      <span className="slot" style={{background: selectedCue.color || cueColors[selectedCue.slot]}}>{slotName(selectedCue.slot)}</span>
      <label>Label<input aria-label={`Cue ${slotName(selectedCue.slot)} label`} defaultValue={selectedCue.label} key={selectedCue.label} maxLength={120} disabled={locked}
        onKeyDown={event => { if (event.key === 'Enter') event.currentTarget.blur(); }}
        onBlur={event => { const label = event.target.value.trim(); if (label && label !== selectedCue.label) updateCue(selectedCue.slot, {label}); else event.target.value = selectedCue.label; }}/></label>
      <label title="Position from the start of the track, in seconds">Position (s)<input aria-label={`Cue ${slotName(selectedCue.slot)} position in seconds`} type="number" min="0" max={track.duration} step="0.001" defaultValue={selectedCue.position_us / 1e6} key={selectedCue.position_us} disabled={locked}
        onKeyDown={event => { if (event.key === 'Enter') event.currentTarget.blur(); }}
        onBlur={event => {
          const value = Number(event.target.value);
          if (event.target.value !== '' && Number.isFinite(value) && value >= 0 && value < track.duration) { const us = Math.round(value * 1e6); if (us !== selectedCue.position_us) updateCue(selectedCue.slot, {position_us: us}); }
          else { event.target.value = String(selectedCue.position_us / 1e6); actions.notify(`A cue must be between 0 and ${track.duration.toFixed(2)} seconds.`); }
        }}/></label>
      <label className="cue-colour">Colour<input aria-label={`Cue ${slotName(selectedCue.slot)} colour`} type="color" value={selectedCue.color || cueColors[selectedCue.slot]} disabled={locked} onChange={event => updateCue(selectedCue.slot, {color: event.target.value})}/></label>
      <div className="cue-editor-actions">
        <button disabled={locked || track.missing} title="Move this cue to where the playhead is now" onClick={() => updateCue(selectedCue.slot, {position_us: Math.round(Math.min(snapTime(playhead.get()), Math.max(0, track.duration - .001)) * 1e6)})}>Move to playhead</button>
        <span className="audition"><button disabled={track.missing} title="Play the lead-in before this cue, to hear how it comes in" onClick={() => audition(selectedCue)}><Play size={14}/> Audition</button>
          <Select aria-label="Audition lead-in" title="How much plays before the cue" value={leadIn} onChange={event => setLeadIn(Number(event.target.value))}>{[2, 4, 8].map(n => <option key={n} value={n}>from {n} beats before</option>)}</Select></span>
        <button disabled={locked} title="Remove this cue" onClick={() => { const slot = selectedCue.slot; setSelectedSlot(null); void actions.persistCues(track.cues.filter(cue => cue.slot !== slot)); }}><Trash2 size={14}/> Remove</button>
      </div>
    </div> : track.cues.length > 0 && <p className="muted cue-editor-hint">Select a cue to rename, recolour, move or remove it.</p>}
    <PanelPresence open={sectionsOpen}>{sectionsOpen && <SectionEditor track={track} locked={locked} onSave={actions.saveSections} onClose={() => setSectionsOpen(false)}/>}</PanelPresence>
  </section>;
}
