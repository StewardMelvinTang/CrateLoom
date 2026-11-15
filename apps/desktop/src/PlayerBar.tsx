import {convertFileSrc} from '@tauri-apps/api/core';
import {Music2, Pause, Play, Repeat, Repeat1, Shuffle, SkipBack, SkipForward, SlidersHorizontal, Volume2, VolumeX} from 'lucide-react';
import {usePlayhead} from './playhead';
import {time, type Track} from './types';

export type RepeatMode = 'off' | 'all' | 'one';

/** Only this part follows the playback position, so the rest of the player does not re-render while playing. */
function Timeline({track, onSeek}: {track: Track | null; onSeek: (seconds: number) => void}) {
  const position = usePlayhead();
  return <div className="transport-timeline">
    <span className="time">{time(position)}</span>
    <input aria-label="Playback position" aria-valuetext={`${time(position)} of ${time(track?.duration || 0)}`} type="range" min="0" max={track?.duration || 1} step="0.01" value={Math.min(position, track?.duration || 1)}
      disabled={!track || track.missing} onChange={event => onSeek(Number(event.target.value))}/>
    <span className="time">{time(track?.duration || 0)}</span>
  </div>;
}

export function PlayerBar({track, playing, loading, wantsPlayback, canPlay, hasPrevious, hasNext, shuffle, repeat, volume, muted, onEdit, onToggle, onPrevious, onNext, onShuffle, onRepeat, onVolume, onMuted, onSeek}: {
  track: Track | null; playing: boolean; loading: boolean; wantsPlayback: boolean; canPlay: boolean; hasPrevious: boolean; hasNext: boolean;
  shuffle: boolean; repeat: RepeatMode; volume: number; muted: boolean;
  onEdit: () => void; onToggle: () => void; onPrevious: () => void; onNext: () => void; onShuffle: () => void; onRepeat: () => void;
  onVolume: (value: number) => void; onMuted: (value: boolean) => void; onSeek: (seconds: number) => void;
}) {
  const repeatLabel = repeat === 'off' ? 'Repeat is off' : repeat === 'all' ? 'Repeating the whole list' : 'Repeating this track';
  const silent = muted || volume === 0;
  return <footer className="transport">
    <button className="now" aria-label={track ? `Edit cues for ${track.title}` : 'No track selected'} title={track ? 'Open in the Cue Editor' : undefined} disabled={!track} onClick={onEdit}>
      <span className="cover player-artwork">{track?.metadata?.artwork_path ? <img src={convertFileSrc(track.metadata.artwork_path)} alt=""/> : <Music2 size={16}/>}</span>
      <span className="player-track-text">
        <strong className="player-title" title={track?.title}>{track?.title || 'No track selected'}</strong>
        {(loading || track) && <small className="player-artist" role={loading ? 'status' : undefined}>{loading ? 'Loading audio…' : track?.missing ? 'File missing' : track?.artist || 'Unknown artist'}</small>}
      </span>
    </button>
    <div className="player">
      <div className="transport-controls">
        <button className="icon" aria-label="Shuffle" title={shuffle ? 'Shuffle is on' : 'Shuffle is off'} aria-pressed={shuffle} onClick={onShuffle}><Shuffle size={16}/></button>
        <button className="icon" aria-label="Previous track" title="Previous track, or restart this one" onClick={onPrevious} disabled={!hasPrevious}><SkipBack size={18}/></button>
        <button className="play" aria-label={loading && wantsPlayback ? 'Cancel playback' : playing ? 'Pause' : 'Play'} title={loading ? 'Loading audio' : playing ? 'Pause (Space)' : 'Play (Space)'} onClick={onToggle} disabled={!canPlay}>{playing ? <Pause size={18}/> : <Play size={18}/>}</button>
        <button className="icon" aria-label="Next track" title="Next track" onClick={onNext} disabled={!hasNext}><SkipForward size={18}/></button>
        <button className="icon" aria-label={`Repeat: ${repeat}`} title={`${repeatLabel}. Click to change.`} aria-pressed={repeat !== 'off'} onClick={onRepeat}>{repeat === 'one' ? <Repeat1 size={16}/> : <Repeat size={16}/>}</button>
      </div>
      <Timeline track={track} onSeek={onSeek}/>
    </div>
    <div className="transport-side">
      <button className="icon" aria-label={muted ? 'Unmute' : 'Mute'} title={muted ? 'Unmute' : 'Mute'} aria-pressed={muted} onClick={() => onMuted(!muted)}>{silent ? <VolumeX size={17}/> : <Volume2 size={17}/>}</button>
      <input aria-label="Volume" type="range" min="0" max="1" step="0.01" value={muted ? 0 : volume} onChange={event => { onMuted(false); onVolume(Number(event.target.value)); }}/>
      <button className="edit-cues" aria-label="Edit cues" title={track ? 'Open this track in the Cue Editor' : 'Select a track first'} disabled={!track} onClick={onEdit}><SlidersHorizontal size={15}/> <span>Edit cues</span></button>
    </div>
  </footer>;
}
