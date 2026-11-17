import {useState} from 'react';
import {Copy, FolderSearch, ListMinus, ListPlus, Pause, Play, Plus, Replace, SlidersHorizontal, Tags, Trash2, X} from 'lucide-react';
import {ContextMenu, copyText, type ContextItem} from './ContextMenu';
import {ConfirmDialog} from './ConfirmDialog';
import {PanelPresence} from './PanelMotion';
import {message} from './engine';
import type {Playlist, Track} from './types';

/** Where a track's menu was opened, and what should get focus back when it closes. */
export type TrackMenuRequest = {track: Track; x: number; y: number; opener: Element | null};

export type TrackMenuActions = {
  togglePlay: (track: Track) => void;
  edit: (track: Track) => void;
  openMetadata: (ids: string[]) => void;
  addToPlaylist: (playlist: Playlist, ids: string[]) => Promise<void>;
  newPlaylist: (ids: string[]) => Promise<void>;
  removeFromPlaylist: (ids: string[]) => Promise<void>;
  removeFromLibrary: (ids: string[]) => Promise<void>;
  /** Move the audio files to the Trash and remove the tracks. */
  trash: (ids: string[]) => Promise<void>;
  changeSource: (track: Track) => void;
  reveal: (track: Track) => Promise<void>;
  fail: (error: unknown) => void;
};

/** Open a track's menu at a pointer event, or beside the element for the keyboard menu key. */
export const menuAt = (event: {clientX: number; clientY: number; currentTarget: Element}, track: Track): TrackMenuRequest => {
  const box = event.currentTarget.getBoundingClientRect();
  return {track, x: event.clientX || box.left + 48, y: event.clientY || box.top + box.height / 2, opener: document.activeElement};
};

/**
 * The one menu for a track, wherever it is opened: a row in All Tracks or a playlist, or the player bar.
 * Removing from the library and deleting the file are confirmed first.
 */
export function TrackMenu({request, playlists, playlist, activeId, playing, actions, onClose}: {
  request: TrackMenuRequest | null; playlists: Playlist[]; playlist: Playlist | null; activeId: string | null; playing: boolean;
  actions: TrackMenuActions; onClose: () => void;
}) {
  const [confirm, setConfirm] = useState<{kind: 'library' | 'trash'; track: Track} | null>(null);
  const [busy, setBusy] = useState(false), [error, setError] = useState('');
  const close = (restoreFocus: boolean) => {
    const opener = request?.opener;
    onClose();
    if (restoreFocus && opener instanceof HTMLElement && opener.isConnected) opener.focus();
  };
  const run = (task: Promise<void>) => { void task.catch(actions.fail); };
  const copy = (value: string) => run(copyText(value));
  const confirmed = async () => {
    if (!confirm) return;
    setBusy(true); setError('');
    try {
      await (confirm.kind === 'trash' ? actions.trash : actions.removeFromLibrary)([confirm.track.id]);
      setConfirm(null);
    } catch (failure) { setError(message(failure)); }
    finally { setBusy(false); }
  };

  let items: ContextItem[] = [];
  const track = request?.track;
  if (track) {
    const isPlaying = activeId === track.id && playing;
    const inPlaylist = !!playlist?.track_ids.includes(track.id);
    items = [
      {label: isPlaying ? 'Pause' : 'Play', icon: isPlaying ? <Pause size={14}/> : <Play size={14}/>, disabled: track.missing, onSelect: () => actions.togglePlay(track)},
      {label: 'Edit cues', icon: <SlidersHorizontal size={14}/>, onSelect: () => actions.edit(track)},
      {label: 'Edit metadata…', icon: <Tags size={14}/>, onSelect: () => actions.openMetadata([track.id])},
      'separator',
      {label: 'Add to playlist', icon: <ListPlus size={14}/>, heading: `Add “${track.title}” to`, items: [
        ...playlists.map(item => item.track_ids.includes(track.id)
          ? {label: item.name, hint: 'Added', disabled: true, onSelect: () => {}}
          : {label: item.name, hint: String(item.track_ids.length), onSelect: () => run(actions.addToPlaylist(item, [track.id]))}),
        ...(playlists.length ? ['separator' as const] : []),
        {label: 'New playlist', icon: <Plus size={14}/>, onSelect: () => run(actions.newPlaylist([track.id]))},
      ]},
      ...(inPlaylist && playlist ? [{label: `Remove from “${playlist.name}”`, icon: <ListMinus size={14}/>, onSelect: () => run(actions.removeFromPlaylist([track.id]))}] : []),
      {label: 'Copy', icon: <Copy size={14}/>, items: [
        {label: 'Title', disabled: !track.title.trim(), onSelect: () => copy(track.title)},
        {label: 'Artist', disabled: !track.artist.trim(), onSelect: () => copy(track.artist)},
        {label: 'Artist and title', disabled: !track.artist.trim() || !track.title.trim(), onSelect: () => copy(`${track.artist} - ${track.title}`)},
      ]},
      'separator',
      {label: 'Change source…', icon: <Replace size={14}/>, onSelect: () => actions.changeSource(track)},
      {label: 'Reveal in Finder', icon: <FolderSearch size={14}/>, disabled: track.missing, onSelect: () => run(actions.reveal(track))},
      'separator',
      {label: 'Remove from library…', icon: <X size={14}/>, onSelect: () => { setError(''); setConfirm({kind: 'library', track}); }},
      {label: 'Delete…', icon: <Trash2 size={14}/>, danger: true, onSelect: () => { setError(''); setConfirm({kind: 'trash', track}); }},
    ];
  }

  return <>
    {request && track && <ContextMenu x={request.x} y={request.y} label={`Actions for ${track.title}`} items={items} onClose={close}/>}
    <PanelPresence open={!!confirm}>{confirm && (confirm.kind === 'trash'
      ? <ConfirmDialog title={`Delete “${confirm.track.title}”?`} confirmLabel="Move to Trash" busy={busy} error={error} onConfirm={() => void confirmed()} onCancel={() => setConfirm(null)}>
          <p>{confirm.track.missing ? 'The audio file is already missing, so only the track is removed from CrateLoom.' : 'The audio file is moved to the Trash, and the track is removed from CrateLoom.'} Its cues, sections and playlist places are removed too.</p>
          {!confirm.track.missing && <p>You can put the file back from the Trash until you empty it; the CrateLoom details are not restored.</p>}
        </ConfirmDialog>
      : <ConfirmDialog title={`Remove “${confirm.track.title}” from the library?`} confirmLabel="Remove from library" busy={busy} error={error} onConfirm={() => void confirmed()} onCancel={() => setConfirm(null)}>
          <p>The track is removed from CrateLoom, including its cues, sections and playlist places. The audio file stays on disk and is not deleted.</p>
        </ConfirmDialog>)}</PanelPresence>
  </>;
}
