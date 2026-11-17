import {useEffect, useId, useRef, useState} from 'react';
import {convertFileSrc, invoke} from '@tauri-apps/api/core';
import {open} from '@tauri-apps/plugin-dialog';
import {ArrowUpRight, FolderOpen, Link2, LoaderCircle, Pause, Play, Search, X} from 'lucide-react';
import {call, message} from './engine';
import {useDialogFocus} from './useDialogFocus';
import {ServiceIcon} from './ServiceIcon';
import {AUDIO_EXTENSIONS, time, type Track} from './types';
import './change-source.css';

export type SourceOption = {title: string; url: string; duration: number; channel: string; verified: boolean; topic?: boolean;
  matched: boolean; compatible: boolean; current: boolean; music_video: boolean; difference: number | null; recommended?: boolean; recommendation_reason?: string};
type Found = {query: string; current: string | null; options: SourceOption[]};
type EngineJob<T> = {id: string; state: string; error: string | null; result: T | null};

/** Polls a background engine request until it finishes. */
async function settle<T>(pending: EngineJob<T>, alive: () => boolean): Promise<T | null> {
  let job = pending;
  while (job.state === 'running' && alive()) {
    await new Promise(resolve => window.setTimeout(resolve, 350));
    job = await call<EngineJob<T>>('library.job', {id: job.id});
  }
  if (job.state === 'failed') throw new Error(job.error || 'The request failed.');
  return job.result;
}

const lengthNote = (difference: number | null) => difference === null ? '' : Math.abs(difference) < 2 ? 'same length as now'
  : `${time(Math.abs(difference))} ${difference > 0 ? 'longer' : 'shorter'} than now`;

/**
 * Change the audio behind a library track: another version online, a pasted link, or a file on this Mac.
 * The track keeps its details, cover, cues and playlist places; its tempo, key and beat grid are measured again.
 */
export function ChangeSource({track, onClose, onReplaced, onQueued, onPreviewStart}: {
  track: Track; onClose: () => void; onReplaced: (track: Track, previousDeleted: boolean) => Promise<void> | void; onQueued: () => void;
  /** Called when a preview starts playing, so the main player can pause. */
  onPreviewStart?: () => void;
}) {
  const [query, setQuery] = useState(''), [found, setFound] = useState<Found | null>(null), [searching, setSearching] = useState(false);
  const [link, setLink] = useState(''), [busy, setBusy] = useState<string | null>(null), [error, setError] = useState(''), [queued, setQueued] = useState(false);
  const mounted = useRef(true);
  const dialog = useDialogFocus(onClose, false);
  const id = useId();
  useEffect(() => () => { mounted.current = false; }, []);

  // ---- Preview: listen to the current audio or another version before choosing ----
  const media = useRef<HTMLAudioElement>(null), paths = useRef(new Map<string, string>()), request = useRef(0), converted = useRef(false);
  const [loaded, setLoaded] = useState<{key: string; title: string} | null>(null), [loading, setLoading] = useState<string | null>(null);
  const [playing, setPlaying] = useState(false), [position, setPosition] = useState(0), [length, setLength] = useState(track.duration);
  const preview = async (key: string, title: string, url?: string, convert = false) => {
    const audio = media.current;
    if (!audio) return;
    if (loaded?.key === key && !convert) {
      if (audio.paused) { onPreviewStart?.(); void audio.play().catch(() => {}); } else audio.pause();
      return;
    }
    const mine = ++request.current;
    audio.pause(); setLoading(key); setError('');
    try {
      let path = convert ? undefined : paths.current.get(key);
      if (!path) {
        const result = await settle(await call<EngineJob<{preview_path: string}>>('library.preview_source', {id: track.id, ...(url ? {url} : {}), ...(convert ? {convert: true} : {})}),
          () => mounted.current && mine === request.current);
        if (!result || !mounted.current || mine !== request.current) return;
        path = result.preview_path; paths.current.set(key, path);
      }
      if (!mounted.current || mine !== request.current) return;
      if (key === 'current' && !convert) converted.current = false;
      audio.src = convertFileSrc(path); setLoaded({key, title}); setPosition(0);
      onPreviewStart?.();
      await audio.play().catch(() => {});
    } catch (failure) { if (mounted.current && mine === request.current) setError(message(failure)); }
    finally { if (mounted.current && mine === request.current) setLoading(null); }
  };
  const current = () => void preview('current', 'Current audio');

  const search = async (text?: string) => {
    setSearching(true); setError('');
    try {
      const result = await settle(await call<EngineJob<Found>>('library.source_options', {id: track.id, ...(text ? {query: text} : {})}), () => mounted.current);
      if (mounted.current && result) { setFound(result); if (!text) setQuery(result.query); }
    } catch (failure) { if (mounted.current) setError(message(failure)); }
    finally { if (mounted.current) setSearching(false); }
  };
  useEffect(() => { void search(); }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const use = async (url: string) => {
    setBusy(url); setError(''); media.current?.pause();
    try {
      await settle(await call<EngineJob<unknown>>('library.change_source', {id: track.id, url}), () => true);
      if (!mounted.current) return;
      setQueued(true); onQueued();
    } catch (failure) { if (mounted.current) setError(message(failure)); }
    finally { if (mounted.current) setBusy(null); }
  };
  const chooseFile = async () => {
    setError('');
    const path = await open({multiple: false, filters: [{name: 'Audio', extensions: AUDIO_EXTENSIONS}]}).catch(() => null);
    if (typeof path !== 'string') return;
    setBusy('file');
    try {
      const updated = await call<Track & {previous_deleted: boolean}>('library.replace_file', {id: track.id, path});
      await onReplaced(updated, updated.previous_deleted);
      if (mounted.current) onClose();
    } catch (failure) { if (mounted.current) setError(message(failure)); }
    finally { if (mounted.current) setBusy(null); }
  };
  const openPage = (url: string) => invoke<void>('open_reference', {url}).catch(failure => setError(message(failure)));

  const options = found?.options ?? [];
  const versions = options.filter(option => option.matched), others = options.filter(option => !option.matched);
  const working = busy !== null;
  const row = (option: SourceOption) => {
    const badges = [option.recommended && option.recommendation_reason, option.current && 'Current source', option.topic && !option.recommended && 'Audio-only upload',
      option.music_video && 'Music video', option.matched && !option.compatible && 'Different version'].filter(Boolean) as string[];
    const key = option.current ? 'current' : option.url, active = loaded?.key === key, isPlaying = active && playing;
    return <li key={option.url} className="change-source-option" data-recommended={option.recommended || undefined} data-current={option.current || undefined} data-previewing={active || undefined}>
      <button type="button" className="change-source-play" aria-label={`${isPlaying ? 'Pause' : 'Play'} ${option.title}`} title={isPlaying ? 'Pause' : 'Listen before choosing'}
        onClick={() => void preview(key, option.current ? 'Current audio' : option.title, option.current ? undefined : option.url)}>
        {loading === key ? <LoaderCircle size={14} className="spin" aria-hidden="true"/> : isPlaying ? <Pause size={14} aria-hidden="true"/> : <Play size={14} aria-hidden="true"/>}</button>
      <div className="change-source-option-text">
        <button type="button" className="change-source-title" title={`Open ${option.title} in your browser`} onClick={() => void openPage(option.url)}>
          <ServiceIcon name="YouTube"/><span>{option.title}</span><ArrowUpRight size={12} aria-hidden="true"/></button>
        <small>{[option.channel, time(option.duration), lengthNote(option.difference)].filter(Boolean).join(' · ')}</small>
        {badges.length > 0 && <span className="change-source-badges">{badges.map(badge => <span key={badge} className="change-source-badge" data-kind={badge === 'Music video' || badge === 'Different version' ? 'warning' : undefined}>{badge}</span>)}</span>}
      </div>
      <button type="button" className={option.recommended ? 'primary' : ''} disabled={working || queued || option.current} aria-label={`Use ${option.title}`}
        onClick={() => void use(option.url)}>{busy === option.url ? <><LoaderCircle size={14} className="spin" aria-hidden="true"/> Preparing…</> : option.current ? 'In use' : 'Use this'}</button>
    </li>;
  };

  return <div className="modal-backdrop" onMouseDown={event => { if (event.target === event.currentTarget && !working) onClose(); }}>
    <section ref={dialog} tabIndex={-1} className="change-source-window" role="dialog" aria-modal="true" aria-labelledby={`${id}-title`}>
      <div className="change-source-head">
        <div><h2 id={`${id}-title`}>Change source</h2>
          <dl className="change-source-track">
            <div><dt>Title</dt><dd title={track.title}>{track.title || 'Untitled'}</dd></div>
            <div><dt>Artist</dt><dd title={track.artist}>{track.artist || 'Unknown artist'}</dd></div>
            <div><dt>Length</dt><dd>{time(track.duration)}</dd></div>
          </dl></div>
        <button type="button" className="quiet" aria-label="Close" onClick={onClose}><X size={16}/></button>
      </div>
      <div className="change-source-player" role="group" aria-label="Preview">
        <button type="button" className="change-source-play" disabled={track.missing && !loaded} aria-label={playing ? 'Pause preview' : 'Play preview'}
          onClick={() => loaded ? void preview(loaded.key, loaded.title) : current()}>
          {loading && (!loaded || loading === loaded.key || loading === 'current') ? <LoaderCircle size={15} className="spin" aria-hidden="true"/> : playing ? <Pause size={15} aria-hidden="true"/> : <Play size={15} aria-hidden="true"/>}</button>
        <div className="change-source-player-text">
          <strong title={loaded?.title}>{loading && loading !== loaded?.key ? 'Loading preview…' : loaded?.title ?? 'Current audio'}</strong>
          <span className="time">{time(position)} / {time(length)}</span>
        </div>
        <input type="range" aria-label="Preview position" aria-valuetext={`${time(position)} of ${time(length)}`} min={0} max={Math.max(1, length)} step={0.1} value={Math.min(position, length)} disabled={!loaded}
          onChange={event => { const value = Number(event.target.value); setPosition(value); if (media.current) media.current.currentTime = value; }}/>
        <audio ref={media} preload="auto" onTimeUpdate={event => setPosition(event.currentTarget.currentTime)}
          onLoadedMetadata={event => { if (Number.isFinite(event.currentTarget.duration)) setLength(event.currentTarget.duration); }}
          onPlay={() => setPlaying(true)} onPause={() => setPlaying(false)} onEnded={() => setPlaying(false)}
          onError={() => {
            // The web view cannot play some formats (Ogg, Opus): play a converted copy of the track instead.
            if (loaded?.key === 'current' && !converted.current) { converted.current = true; void preview('current', 'Current audio', undefined, true); }
            else if (loaded) setError('This preview could not be played.');
          }}/>
      </div>
      {queued ? <div className="change-source-done" role="status">
        <p><strong>Downloading the new version.</strong> The track switches to it when the download finishes; follow it in Downloads.</p>
        <button type="button" className="primary" onClick={onClose}>Done</button>
      </div> : <>
        <form className="change-source-search" onSubmit={event => { event.preventDefault(); if (query.trim()) void search(query.trim()); }}>
          <label className="visually-hidden" htmlFor={`${id}-query`}>Search for versions</label>
          <Search size={15} aria-hidden="true"/><input id={`${id}-query`} value={query} onChange={event => setQuery(event.target.value)} placeholder="Artist and title" maxLength={300}/>
          <button type="submit" disabled={searching || !query.trim()}>Search</button>
        </form>
        <div className="change-source-results" aria-busy={searching}>
          {searching && !options.length && <p className="change-source-empty" role="status"><LoaderCircle size={15} className="spin" aria-hidden="true"/> Looking for versions…</p>}
          {!searching && found && !options.length && <p className="change-source-empty">No versions found. Try different words, paste a link, or choose a file.</p>}
          {versions.length > 0 && <ul aria-label="Versions of this track">{versions.map(row)}</ul>}
          {others.length > 0 && <><h3>{versions.length ? 'Other results' : 'Results'}</h3><ul aria-label="Other results">{others.map(row)}</ul></>}
        </div>
        <form className="change-source-link" onSubmit={event => { event.preventDefault(); if (link.trim()) void use(link.trim()); }}>
          <label htmlFor={`${id}-link`}><Link2 size={14} aria-hidden="true"/> Or paste a link</label>
          <input id={`${id}-link`} value={link} onChange={event => setLink(event.target.value)} placeholder="YouTube, SoundCloud or direct audio link" maxLength={4096}/>
          <button type="submit" disabled={working || !link.trim()}>{busy === link.trim() ? 'Preparing…' : 'Use link'}</button>
        </form>
      </>}
      {error && <p className="inline-error" role="alert">{error}</p>}
      {!queued && <div className="change-source-foot">
        <button type="button" disabled={working} onClick={() => void chooseFile()}><FolderOpen size={15}/> {busy === 'file' ? 'Replacing…' : 'Choose a file on this Mac…'}</button>
        <button type="button" onClick={onClose}>Cancel</button>
      </div>}
    </section>
  </div>;
}
