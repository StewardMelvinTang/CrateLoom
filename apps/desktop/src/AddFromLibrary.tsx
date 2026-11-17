import {useId, useMemo, useState} from 'react';
import {convertFileSrc} from '@tauri-apps/api/core';
import {Check, Music2, Search, X} from 'lucide-react';
import {message} from './engine';
import {useDialogFocus} from './useDialogFocus';
import {plural, time, type Playlist, type Track} from './types';
import './add-from-library.css';

/** Pick tracks already in the library and add them to a playlist. */
export function AddFromLibrary({tracks, playlist, onAdd, onClose}: {
  tracks: Track[]; playlist: Playlist; onAdd: (ids: string[]) => Promise<void>; onClose: () => void;
}) {
  const [query, setQuery] = useState(''), [chosen, setChosen] = useState<string[]>([]), [busy, setBusy] = useState(false), [error, setError] = useState('');
  const dialog = useDialogFocus(onClose, busy);
  const id = useId();
  const inPlaylist = useMemo(() => new Set(playlist.track_ids), [playlist.track_ids]);
  const shown = useMemo(() => {
    const needle = query.trim().toLowerCase();
    return tracks.filter(track => !needle || `${track.title} ${track.artist}`.toLowerCase().includes(needle));
  }, [tracks, query]);
  const available = shown.filter(track => !inPlaylist.has(track.id));
  const chosenSet = new Set(chosen);
  const allChosen = available.length > 0 && available.every(track => chosenSet.has(track.id));
  const toggle = (trackId: string, on: boolean) => setChosen(ids => on ? [...ids.filter(item => item !== trackId), trackId] : ids.filter(item => item !== trackId));
  const add = async () => {
    setBusy(true); setError('');
    // Keep library order, whatever order the boxes were ticked in.
    const ids = tracks.filter(track => chosenSet.has(track.id)).map(track => track.id);
    try { await onAdd(ids); onClose(); } catch (failure) { setError(message(failure)); setBusy(false); }
  };
  return <div className="modal-backdrop" onMouseDown={event => { if (event.target === event.currentTarget && !busy) onClose(); }}>
    <section ref={dialog} tabIndex={-1} className="add-library-window" role="dialog" aria-modal="true" aria-labelledby={`${id}-title`}>
      <div className="add-library-head">
        <div><h2 id={`${id}-title`}>Add to “{playlist.name}”</h2><p>Choose tracks from your library.</p></div>
        <button type="button" className="quiet" aria-label="Close" disabled={busy} onClick={onClose}><X size={16}/></button>
      </div>
      <label className="add-library-search"><Search size={15} aria-hidden="true"/>
        <input type="search" aria-label="Search your library" placeholder="Search by title or artist" value={query} onChange={event => setQuery(event.target.value)} autoFocus/></label>
      <div className="add-library-bar">
        <label><input type="checkbox" checked={allChosen} disabled={!available.length}
          onChange={event => { const ids = new Set(available.map(track => track.id)); setChosen(current => event.target.checked ? [...new Set([...current, ...ids])] : current.filter(item => !ids.has(item))); }}/>
          {query.trim() ? 'Select all shown' : 'Select all'}</label>
        <span className="muted">{plural(shown.length, 'track')}{shown.length !== available.length ? ` · ${shown.length - available.length} already in the playlist` : ''}</span>
      </div>
      <ul className="add-library-list" aria-label="Library tracks">
        {shown.map(track => {
          const added = inPlaylist.has(track.id);
          return <li key={track.id} data-added={added || undefined}>
            <label>
              <input type="checkbox" checked={added || chosenSet.has(track.id)} disabled={added} aria-label={`Add ${track.title}`} onChange={event => toggle(track.id, event.target.checked)}/>
              <span className="cover">{track.metadata?.artwork_path ? <img src={convertFileSrc(track.metadata.artwork_path)} alt="" loading="lazy"/> : <Music2 size={14}/>}</span>
              <span className="add-library-text"><strong>{track.title}</strong><small>{track.artist || 'Unknown artist'}</small></span>
              {added ? <span className="add-library-added"><Check size={13} aria-hidden="true"/> Added</span> : <span className="time">{time(track.duration)}</span>}
            </label>
          </li>;
        })}
        {!shown.length && <li className="add-library-empty">{tracks.length ? 'No tracks match your search.' : 'Your library is empty. Add music first.'}</li>}
      </ul>
      {error && <p className="inline-error" role="alert">{error}</p>}
      <div className="add-library-foot">
        <button type="button" disabled={busy} onClick={onClose}>Cancel</button>
        <button type="button" className="primary" disabled={busy || !chosen.length} onClick={() => void add()}>
          {busy ? 'Adding…' : chosen.length ? `Add ${plural(chosen.length, 'track')}` : 'Add tracks'}</button>
      </div>
    </section>
  </div>;
}
