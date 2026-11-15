import React, {useEffect, useMemo, useRef, useState} from 'react';
import {convertFileSrc} from '@tauri-apps/api/core';
import {Activity, ArrowDown, ArrowUp, ChevronDown, ChevronUp, Copy, FolderOpen, FolderSearch, GripVertical, Link2, ListPlus, Music2, Pause, Play, Plus, SlidersHorizontal, Tags, Trash2, Upload, WandSparkles, X} from 'lucide-react';
import {Disclosure} from './Disclosure';
import {Select} from './Select';
import {Menu} from './Menu';
import {ConfirmDialog} from './ConfirmDialog';
import {PanelPresence} from './PanelMotion';
import {MetadataBatchProgress, type useMetadataBatch} from './MetadataBatch';
import type {useTrackReorder} from './useTrackReorder';
import {message} from './engine';
import {ContextMenu, copyText} from './ContextMenu';
import {COLUMNS, WIDTH_LIMITS, clampWidth, sortTracks, trackKey, useColumnWidths, type ColumnId, type TrackSort} from './trackColumns';
import {pendingTags, plural, time, trackBpm, type Playlist, type Track} from './types';

export type LibraryFilter = 'all' | 'missing' | 'unwritten';
export type ExportSelector = {id: string} | {playlist_id: string} | {track_ids: string[]};

export type LibraryActions = {
  /** Load the track into the player without starting it. */
  select: (track: Track) => void;
  /** Open the track in the Cue Editor. */
  edit: (track: Track) => void;
  togglePlay: (track: Track) => void;
  relink: (track: Track) => void;
  openMetadata: (ids: string[]) => void;
  writeTags: (ids: string[]) => void;
  /** Look up and stage metadata; with `writeTags` each applied track is also written to its file. */
  autoMetadata: (ids: string[], writeTags: boolean) => void;
  /** Queue beat analysis for tracks that have none from the current analyser. */
  analyze: (ids: string[]) => void;
  addToPlaylist: (playlist: Playlist, ids: string[]) => Promise<void>;
  newPlaylist: (ids: string[]) => Promise<void>;
  removeFromPlaylist: (ids: string[]) => Promise<void>;
  renamePlaylist: () => void;
  deletePlaylist: () => Promise<void>;
  removeFromLibrary: (ids: string[]) => Promise<void>;
  exportTracks: (selector: ExportSelector, name: string, copyAudio: boolean) => void;
  moveTrack: (id: string, direction: number) => void;
  importFiles: () => void;
  /** Show the track's audio file in Finder. */
  reveal: (track: Track) => Promise<void>;
};

type RowHandlers = Pick<LibraryActions, 'select' | 'edit' | 'togglePlay' | 'relink' | 'openMetadata' | 'moveTrack'> & {
  setSelected: (id: string, selected: boolean) => void;
  handle: ReturnType<typeof useTrackReorder>['handle'];
  openContext: (track: Track, x: number, y: number) => void;
};

/** Handlers whose identity never changes, so memoised rows are not re-rendered by unrelated state. */
function useStableHandlers<T extends Record<string, (...args: never[]) => unknown>>(handlers: T): T {
  const latest = useRef(handlers);
  latest.current = handlers;
  return useMemo(() => {
    const stable: Record<string, unknown> = {};
    for (const key of Object.keys(handlers)) stable[key] = (...args: never[]) => latest.current[key](...args);
    return stable as T;
  }, []);
}

const TrackRow = React.memo(function TrackRow({track, order, selected, active, playing, first, last, ordering, sorted, dropTarget, handlers}: {
  track: Track; order: number | null; selected: boolean; active: boolean; playing: boolean; first: boolean; last: boolean;
  ordering: boolean; sorted: boolean; dropTarget: boolean; handlers: RowHandlers;
}) {
  const bpm = trackBpm(track), key = trackKey(track), unwritten = pendingTags(track), isPlaying = active && playing;
  // While the list is sorted, rows are not in playlist order, so they cannot be moved.
  const locked = ordering || sorted, lockedTitle = sorted ? 'Clear the sort to reorder' : undefined;
  return <div className={`track-row ${active ? 'selected' : ''} ${dropTarget ? 'drop-target' : ''}`} data-reorder-group="playlist" data-reorder-id={track.id}
    onContextMenu={event => {
      event.preventDefault();
      // The keyboard menu key reports no pointer position; open beside the row instead.
      const box = event.currentTarget.getBoundingClientRect();
      handlers.openContext(track, event.clientX || box.left + 48, event.clientY || box.top + box.height / 2);
    }}>
    <div className="track-title-cell">
      {order !== null && <>
        <button className="reorder-handle" {...handlers.handle(track.id)} disabled={locked} title={lockedTitle ?? 'Drag to reorder, or press Up / Down'} aria-label={`Reorder ${track.title}. Use up or down arrow keys.`}
          onKeyDown={event => { if (event.key === 'ArrowUp' || event.key === 'ArrowDown') { event.preventDefault(); handlers.moveTrack(track.id, event.key === 'ArrowUp' ? -1 : 1); } }}><GripVertical size={15}/></button>
        <span className="track-order time">{String(order).padStart(2, '0')}</span>
      </>}
      <input type="checkbox" aria-label={`Select ${track.title}`} checked={selected} onChange={event => handlers.setSelected(track.id, event.target.checked)}/>
      <button className="cover cover-play" data-playing={isPlaying} disabled={track.missing} title={track.missing ? 'File missing' : isPlaying ? 'Pause' : 'Play'}
        aria-label={`${isPlaying ? 'Pause' : 'Play'} ${track.title}`} onClick={() => handlers.togglePlay(track)}>
        {track.metadata?.artwork_path ? <img src={convertFileSrc(track.metadata.artwork_path)} alt="" loading="lazy"/> : <Music2 size={16}/>}
        {!track.missing && <span className="cover-play-icon" aria-hidden="true">{isPlaying ? <Pause size={15}/> : <Play size={15}/>}</span>}
      </button>
      <button className="track-select track-name" title={`${track.title} — double-click to edit cues`} onClick={() => handlers.select(track)} onDoubleClick={() => handlers.edit(track)}>
        <strong>{track.title}</strong><small className="track-artist-inline">{track.artist || 'Unknown artist'}</small>
      </button>
    </div>
    <span className="truncate track-artist" title={track.artist}>{track.artist || 'Unknown artist'}</span>
    <span className="time track-bpm" title={bpm ? (track.grid_bpm ? 'Tempo you set in the Cue Editor' : 'Detected tempo') : 'Not analysed yet'}>{bpm ? bpm.toFixed(1) : '—'}</span>
    <span className="time track-key" title={key ? `${key.name} (${key.camelot}) — detected key` : 'Not analysed yet'}>{key ? key.camelot : '—'}</span>
    <span className="time track-time">{time(track.duration)}</span>
    <span className="track-status">
      {track.missing ? <><strong>File missing</strong><button className="relink" title="Choose where this audio file is now. Cues and analysis are kept." onClick={() => handlers.relink(track)}><Link2 size={13}/> Relink</button></> : <>
        {track.cues.length > 0 && <span>{plural(track.cues.length, 'cue')}</span>}
        {unwritten && <span title="Metadata changes are saved in CrateLoom but not yet written to the audio file. Select the track and choose Write tags.">Tags not written</span>}
      </>}
    </span>
    <span className="track-row-actions">
      <button aria-label={`Edit cues for ${track.title}`} title="Edit cues" onClick={() => handlers.edit(track)}><SlidersHorizontal size={14}/></button>
      <button aria-label={`Edit metadata for ${track.title}`} title="Edit metadata" onClick={() => handlers.openMetadata([track.id])}><Tags size={14}/></button>
      {order !== null && <>
        <button aria-label={`Move ${track.title} up`} title={lockedTitle ?? 'Move up'} disabled={locked || first} onClick={() => handlers.moveTrack(track.id, -1)}><ArrowUp size={13}/></button>
        <button aria-label={`Move ${track.title} down`} title={lockedTitle ?? 'Move down'} disabled={locked || last} onClick={() => handlers.moveTrack(track.id, 1)}><ArrowDown size={13}/></button>
      </>}
    </span>
  </div>;
});

const AUTO_WRITE_KEY = 'autoMetadataWriteTags';
const AUTO_WRITE_TRUTH = 'Writes only tracks where a match and a cover are found. Each file is backed up first.';

export function LibraryView({tracks, playlists, playlist, query, filter, onFilter, sort, onSort, loading, selectedIds, onSelectedIds, activeId, playing, ordering, reorder, exporting, importing, metadataBatch, unanalysed, actions}: {
  tracks: Track[]; playlists: Playlist[]; playlist: Playlist | null; unanalysed: ReadonlySet<string>; query: string; filter: LibraryFilter; onFilter: (value: LibraryFilter) => void; loading: boolean;
  sort: TrackSort; onSort: (column: ColumnId) => void;
  selectedIds: string[]; onSelectedIds: (update: (ids: string[]) => string[]) => void; activeId: string | null; playing: boolean; ordering: boolean;
  reorder: ReturnType<typeof useTrackReorder>; exporting: boolean; importing: boolean; metadataBatch: ReturnType<typeof useMetadataBatch>; actions: LibraryActions;
}) {
  const [copyExportAudio, setCopyExportAudio] = useState(false);
  const [explainAuto, setExplainAuto] = useState(false);
  const [autoWrite, setAutoWrite] = useState(() => { try { return localStorage.getItem(AUTO_WRITE_KEY) === '1'; } catch { return false; } });
  const chooseAutoWrite = (on: boolean) => { setAutoWrite(on); try { localStorage.setItem(AUTO_WRITE_KEY, on ? '1' : '0'); } catch { /* still applies until the app closes */ } };
  const [confirm, setConfirm] = useState<null | {kind: 'playlist'} | {kind: 'library'; ids: string[]}>(null);
  const [confirmBusy, setConfirmBusy] = useState(false), [confirmError, setConfirmError] = useState('');
  const [toolError, setToolError] = useState('');
  const selectAll = useRef<HTMLInputElement>(null);
  const columns = useColumnWidths();
  const [context, setContext] = useState<{track: Track; x: number; y: number; opener: Element | null} | null>(null);

  const scoped = useMemo(() => {
    if (!playlist) return tracks;
    const byId = new Map(tracks.map(track => [track.id, track]));
    return playlist.track_ids.map(id => byId.get(id)).filter((track): track is Track => !!track);
  }, [playlist, tracks]);
  const visible = useMemo(() => {
    const needle = query.trim().toLowerCase();
    return sortTracks(scoped, sort).filter(track => (filter !== 'missing' || track.missing) && (filter !== 'unwritten' || pendingTags(track))
      && (!needle || `${track.title} ${track.artist}`.toLowerCase().includes(needle)));
  }, [scoped, filter, query, sort]);
  // Actions only ever apply to selected rows the user can see.
  const selectedSet = useMemo(() => new Set(selectedIds), [selectedIds]);
  const selected = useMemo(() => visible.filter(track => selectedSet.has(track.id)).map(track => track.id), [visible, selectedSet]);
  const narrowed = !!query.trim() || filter !== 'all';
  const autoTargets = selected.length ? selected : visible.map(track => track.id);
  const autoScope = selected.length ? `${selected.length} selected` : narrowed ? `${visible.length} shown` : `all ${plural(visible.length, 'track')}`;
  const autoScopeSentence = selected.length ? `the ${plural(selected.length, 'selected track')}` : narrowed ? `the ${plural(visible.length, 'track')} shown` : `all ${plural(visible.length, 'track')} in ${playlist ? 'this playlist' : 'your library'}`;
  const autoBusy = metadataBatch.starting || metadataBatch.running;
  const analysisTargets = useMemo(() => autoTargets.filter(id => unanalysed.has(id)), [autoTargets, unanalysed]);

  useEffect(() => { if (selectAll.current) selectAll.current.indeterminate = selected.length > 0 && selected.length < visible.length; }, [selected.length, visible.length]);
  useEffect(() => { setExplainAuto(false); setToolError(''); }, [playlist?.id]);

  const handlers = useStableHandlers<RowHandlers>({
    select: actions.select, edit: actions.edit, togglePlay: actions.togglePlay, relink: actions.relink, openMetadata: actions.openMetadata, moveTrack: actions.moveTrack,
    setSelected: (id, on) => onSelectedIds(ids => on ? [...ids.filter(item => item !== id), id] : ids.filter(item => item !== id)),
    handle: reorder.handle,
    openContext: (track, x, y) => setContext({track, x, y, opener: document.activeElement}),
  });
  const closeContext = (restoreFocus: boolean) => {
    const opener = context?.opener;
    setContext(null);
    if (restoreFocus && opener instanceof HTMLElement && opener.isConnected) opener.focus();
  };
  const copy = (value: string) => { setToolError(''); void copyText(value).catch(error => setToolError(message(error))); };
  const run = (task: Promise<void>) => { setToolError(''); void task.catch(error => setToolError(message(error))); };
  const startAuto = () => {
    if (!autoTargets.length || autoBusy) return;
    if (localStorage.getItem('autoMetadataExplained') !== '1') { setExplainAuto(true); return; }
    actions.autoMetadata([...autoTargets], autoWrite);
  };
  const confirmAuto = () => { localStorage.setItem('autoMetadataExplained', '1'); setExplainAuto(false); actions.autoMetadata([...autoTargets], autoWrite); };
  const runConfirm = async () => {
    if (!confirm) return;
    setConfirmBusy(true); setConfirmError('');
    try {
      if (confirm.kind === 'playlist') await actions.deletePlaylist(); else await actions.removeFromLibrary(confirm.ids);
      setConfirm(null);
    } catch (error) { setConfirmError(message(error)); }
    finally { setConfirmBusy(false); }
  };
  const closeConfirm = () => { setConfirm(null); setConfirmError(''); };
  const otherPlaylists = playlists.filter(item => item.id !== playlist?.id);
  const count = visible.length === scoped.length ? plural(scoped.length, 'track') : `${visible.length} of ${plural(scoped.length, 'track')}`;
  const exportDisabled = exporting || (playlist ? !playlist.track_ids.length : !selected.length);
  const none = selected.length === 0;

  return <section className="library workspace-view" data-playlist={playlist ? 'true' : undefined} style={columns.style as React.CSSProperties}>
    <MetadataBatchProgress batch={metadataBatch} onReview={actions.openMetadata} onWriteTags={actions.writeTags}/>
    <div className="library-context">
      <span className="muted library-count" role="status">{loading && !tracks.length ? 'Loading…' : count}</span>
      <Select aria-label="Show" title="Choose which tracks are listed" value={filter} onChange={event => onFilter(event.target.value as LibraryFilter)}>
        <option value="all">All tracks</option><option value="missing">Missing files</option><option value="unwritten">Tags not written</option>
      </Select>
      <div className="push"/>
      <div className="library-export">
        <button disabled={exportDisabled} title={playlist ? 'Save this playlist as a rekordbox XML file' : selected.length ? 'Save the selected tracks as a rekordbox XML file' : 'Tick the tracks to export first'}
          onClick={() => actions.exportTracks(playlist ? {playlist_id: playlist.id} : {track_ids: selected}, playlist?.name || 'CrateLoom selection', copyExportAudio)}>
          <Upload size={15}/> {exporting ? 'Exporting…' : playlist ? 'Export playlist' : 'Export selected'}</button>
        <Disclosure className="toolbar-menu export-settings" label="Export options" title="Export options" popover>
          <label className="export-audio-option"><input type="checkbox" checked={copyExportAudio} disabled={exporting} onChange={event => setCopyExportAudio(event.target.checked)}/> Include audio files</label>
          <small>Copy verified audio beside the rekordbox XML.</small>
        </Disclosure>
      </div>
      {playlist && <Menu label="Playlist" align="end" title="Rename or delete this playlist" items={[
        {label: 'Rename…', onSelect: actions.renamePlaylist},
        {label: 'Delete playlist…', description: 'Tracks stay in your library.', icon: <Trash2 size={14}/>, onSelect: () => setConfirm({kind: 'playlist'})},
      ]}/>}
    </div>
    <div className="library-tools">
      <button className="auto-metadata" disabled={autoBusy || !autoTargets.length} aria-expanded={explainAuto || undefined}
        title={`Look up title, artist, album and cover online for ${autoScopeSentence}. ${autoWrite ? `Results are saved in CrateLoom, and applied matches are written into the audio files after a backup.` : 'Results are saved in CrateLoom for review; audio files are not changed until you write tags.'}`}
        onClick={startAuto}><WandSparkles size={15}/> {metadataBatch.starting ? 'Starting…' : 'Auto metadata'}{autoTargets.length ? ` · ${autoScope}` : ''}</button>
      {/* Belongs to Auto metadata and applies to its next run, so it is fixed while a run is going. */}
      <label className="auto-write-option" title={autoBusy ? 'Applies to the next Auto metadata run.' : `After Auto metadata applies a match and cover, also write them into the audio file. ${AUTO_WRITE_TRUTH}`}>
        <input type="checkbox" checked={autoWrite} disabled={autoBusy} aria-describedby={autoWrite ? 'auto-write-note' : undefined} onChange={event => chooseAutoWrite(event.target.checked)}/> Write tags to the files automatically</label>
      {autoWrite && <small id="auto-write-note" className="auto-write-note">{AUTO_WRITE_TRUTH}</small>}
      {analysisTargets.length > 0 && <button className="analyze-beats"
        title={`Detect tempo, key, beat grid and waveform colours for ${plural(analysisTargets.length, 'track')} without a current analysis. Runs in the background, one track at a time; your cues and grid edits are kept.`}
        onClick={() => actions.analyze(analysisTargets)}><Activity size={15}/> Analyze tempo &amp; key · {analysisTargets.length}</button>}
      {/* Always shown, so ticking a row never moves the list; the buttons explain what a selection is for. */}
      <div className="selection-tools" role="group" aria-label="Actions for selected tracks">
        <span className="selection-count" role="status">{selected.length ? `${selected.length} selected:` : 'Tick tracks to:'}</span>
        <button disabled={none} title="Review and edit the metadata of the selected tracks" onClick={() => actions.openMetadata(selected)}><Tags size={15}/> Edit metadata</button>
        <button disabled={none} title="Write the metadata changes saved in CrateLoom into the selected audio files. You review every change first." onClick={() => actions.writeTags(selected)}>Write tags…</button>
        <Menu label="Add to playlist" icon={<ListPlus size={15}/>} disabled={none} heading={`Add ${plural(selected.length, 'track')} to`} items={[
          ...otherPlaylists.map(item => ({label: item.name, hint: String(item.track_ids.length), onSelect: () => run(actions.addToPlaylist(item, selected))})),
          {label: 'New playlist', icon: <Plus size={14}/>, onSelect: () => run(actions.newPlaylist(selected))},
        ]}/>
        {playlist ? <Menu label="Remove" icon={<Trash2 size={15}/>} disabled={none} items={[
          {label: 'Remove from this playlist', description: 'Tracks stay in your library.', onSelect: () => run(actions.removeFromPlaylist(selected))},
          {label: 'Remove from library…', description: 'Audio files are not deleted.', onSelect: () => setConfirm({kind: 'library', ids: selected})},
        ]}/> : <button disabled={none} title="Remove the selected tracks from the CrateLoom library. Audio files are not deleted." onClick={() => setConfirm({kind: 'library', ids: selected})}><Trash2 size={15}/> Remove…</button>}
        {!none && <button className="quiet" aria-label="Clear track selection" title="Clear selection" onClick={() => onSelectedIds(ids => ids.filter(id => !selected.includes(id)))}><X size={14}/> Clear</button>}
      </div>
    </div>
    {explainAuto && <div className="inline-explainer" role="group" aria-label="About Auto metadata">
      <p><strong>Auto metadata</strong> looks up title, artist, album and cover online for {autoScopeSentence} and saves the best match in CrateLoom for you to review. {autoWrite
        ? 'Where a match and a cover are found, it also writes them into the audio file, after backing the file up.' : 'Your audio files are not changed until you choose Write tags.'}</p>
      <button className="primary" onClick={confirmAuto}>Start</button><button onClick={() => setExplainAuto(false)}>Cancel</button>
    </div>}
    {toolError && <div className="inline-error dismissible" role="alert"><span>{toolError}</span><button className="quiet" onClick={() => setToolError('')}>Dismiss</button></div>}
    <div className="table-head" role="group" aria-label="Track list columns">
      {COLUMNS.map(column => {
        const active = sort?.column === column.id, direction = active ? sort!.direction : null;
        const next = !active ? 'ascending' : direction === 'asc' ? 'descending' : playlist ? 'playlist order' : 'library order';
        const button = <button type="button" className="column-sort" data-active={active || undefined} onClick={() => onSort(column.id)}
          aria-label={`${column.label}${active ? `, sorted ${direction === 'asc' ? 'ascending' : 'descending'}` : ''}. Sort by ${column.sortLabel}: ${next}`}
          title={`Sort by ${column.sortLabel} (${next})`}>
          <span>{column.label}</span>{active && (direction === 'asc' ? <ChevronUp size={13} aria-hidden="true"/> : <ChevronDown size={13} aria-hidden="true"/>)}
        </button>;
        if (column.id === 'title') return <span key="title" className="select-visible column-cell">
          <input ref={selectAll} aria-label="Select all visible tracks" title="Select all tracks shown" type="checkbox" checked={visible.length > 0 && selected.length === visible.length}
            onChange={event => { const shown = new Set(visible.map(track => track.id)); onSelectedIds(ids => event.target.checked ? [...new Set([...ids, ...shown])] : ids.filter(id => !shown.has(id))); }}/>
          {button}</span>;
        return <span key={column.id} className={`column-cell column-${column.id} ${column.id === 'artist' ? 'track-artist' : ''}`} data-numeric={column.numeric || undefined}>
          <ColumnResizer id={column.id} label={column.label} width={columns.widths[column.id]} onWidth={columns.setWidth}/>{button}</span>;
      })}
      <span className="visually-hidden">Actions</span>
    </div>
    {visible.length ? visible.map(track => <TrackRow key={track.id} track={track} order={playlist ? playlist.track_ids.indexOf(track.id) + 1 : null}
      selected={selectedSet.has(track.id)} active={activeId === track.id} playing={playing} first={playlist?.track_ids[0] === track.id} last={playlist?.track_ids.at(-1) === track.id}
      ordering={ordering} sorted={!!sort} dropTarget={reorder.target === track.id} handlers={handlers}/>) : loading ? <div className="empty" role="status"><p>Loading your library…</p></div> : <div className="empty">
      <div className="empty-icon"><FolderOpen size={27}/></div>
      <h2>{query.trim() ? 'No matching tracks' : filter === 'missing' ? 'No missing files' : filter === 'unwritten' ? 'No unwritten tag changes' : playlist ? 'This playlist is empty' : 'Your library starts here'}</h2>
      <p>{query.trim() ? 'Try a different search.' : filter === 'missing' ? 'Every audio file is where CrateLoom expects it.' : filter === 'unwritten' ? 'All saved metadata has been written to the audio files.'
        : playlist ? 'In All Tracks, tick the tracks you want and choose Add to playlist.' : 'Add audio files or a folder to prepare them for your next set.'}</p>
      {!query.trim() && !playlist && filter === 'all' && <button disabled={importing} onClick={actions.importFiles}><Plus size={16}/> Add music</button>}
    </div>}
    {context && <ContextMenu x={context.x} y={context.y} label={`Actions for ${context.track.title}`} onClose={closeContext} items={[
      {label: 'Copy title', icon: <Copy size={14}/>, disabled: !context.track.title.trim(), onSelect: () => copy(context.track.title)},
      {label: 'Copy artist', icon: <Copy size={14}/>, disabled: !context.track.artist.trim(), onSelect: () => copy(context.track.artist)},
      {label: 'Copy artist and title', icon: <Copy size={14}/>, disabled: !context.track.artist.trim() || !context.track.title.trim(),
        onSelect: () => copy(`${context.track.artist} - ${context.track.title}`)},
      'separator',
      {label: 'Reveal in Finder', icon: <FolderSearch size={14}/>, disabled: context.track.missing, onSelect: () => run(actions.reveal(context.track))},
    ]}/>}
    <PanelPresence open={!!confirm}>{confirm && (confirm.kind === 'playlist'
      ? <ConfirmDialog title={`Delete playlist “${playlist?.name || ''}”?`} confirmLabel="Delete playlist" busy={confirmBusy} error={confirmError} onConfirm={() => void runConfirm()} onCancel={closeConfirm}>
          <p>Only the playlist is deleted. {playlist?.track_ids.length ? `Its ${plural(playlist.track_ids.length, 'track')} ${playlist.track_ids.length === 1 ? 'stays' : 'stay'} in your library, and no audio files are deleted.` : 'No tracks or audio files are deleted.'}</p>
        </ConfirmDialog>
      : <ConfirmDialog title={`Remove ${plural(confirm.ids.length, 'track')} from the library?`} confirmLabel="Remove from library" busy={confirmBusy} error={confirmError} onConfirm={() => void runConfirm()} onCancel={closeConfirm}>
          <p>The {confirm.ids.length === 1 ? 'track is' : 'tracks are'} removed from CrateLoom, including {confirm.ids.length === 1 ? 'its' : 'their'} cues, sections and playlist entries. The audio {confirm.ids.length === 1 ? 'file stays' : 'files stay'} on disk and {confirm.ids.length === 1 ? 'is' : 'are'} never deleted.</p>
        </ConfirmDialog>)}</PanelPresence>
  </section>;
}

/**
 * The drag handle on a column's left edge. Title takes the remaining width, so dragging the
 * edge left widens this column and the edge follows the pointer. Double-click resets it;
 * arrow keys resize in 8px steps.
 */
function ColumnResizer({id, label, width, onWidth}: {id: Exclude<ColumnId, 'title'>; label: string; width: number | undefined; onWidth: (id: Exclude<ColumnId, 'title'>, value: number | null) => void}) {
  const handle = useRef<HTMLSpanElement>(null);
  const measured = () => width ?? handle.current?.parentElement?.getBoundingClientRect().width ?? WIDTH_LIMITS[id].min;
  return <span ref={handle} className="column-resizer" role="separator" aria-orientation="vertical" tabIndex={0}
    aria-label={`Resize ${label} column`} aria-valuemin={WIDTH_LIMITS[id].min} aria-valuemax={WIDTH_LIMITS[id].max} aria-valuenow={Math.round(measured())}
    title="Drag to resize. Double-click to reset."
    onPointerDown={event => {
      if (event.button !== 0) return;
      event.preventDefault(); event.stopPropagation();
      const start = event.clientX, from = measured(), target = event.currentTarget;
      target.setPointerCapture(event.pointerId);
      document.body.dataset.resizingColumn = 'true';
      const moved = (move: PointerEvent) => onWidth(id, clampWidth(id, from + start - move.clientX));
      const done = () => { target.removeEventListener('pointermove', moved); delete document.body.dataset.resizingColumn; };
      target.addEventListener('pointermove', moved);
      target.addEventListener('pointerup', done, {once: true});
      target.addEventListener('pointercancel', done, {once: true});
    }}
    onDoubleClick={() => onWidth(id, null)}
    onKeyDown={event => {
      if (event.key === 'ArrowLeft' || event.key === 'ArrowRight') { event.preventDefault(); onWidth(id, measured() + (event.key === 'ArrowLeft' ? 8 : -8)); }
      else if (event.key === 'Home' || event.key === 'Delete' || event.key === 'Backspace') { event.preventDefault(); onWidth(id, null); }
    }}/>;
}
