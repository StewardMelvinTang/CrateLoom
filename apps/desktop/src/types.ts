import type {MetadataState} from './MetadataEditor';

export type Cue = {slot: number; position_us: number; label: string; color?: string};
export type Section = {id: string; start_us: number; end_us: number; label: string; origin?: 'manual' | 'model-reviewed' | 'model'};
export type StructureAnalysis = {suggestions: {start_us: number; end_us: number; label: string}[]; model: string; weight_license: string; revision: string};
export type AnalysisData = {
  version: string; tempo: number | null; beats: number[]; downbeats: null; sections: null;
  /** Constant-tempo fit. When stable, the engine stores it as the track's straight grid. */
  grid?: {bpm: number; origin: number; agreement: number; stable: boolean} | null;
  waveform: {peaks: number[]; rgb: number[][]; signal?: {encoding: string; hop: number; rate: number; data: string}};
  warnings: string[];
};
export type ImportItem = {sequence: number; path: string; state: string; track_id: string | null; error: string | null};
export type ImportJob = {id: string; state: string; total: number; processed: number; imported: number; duplicates: number; failed: number; current: string; error: string | null; cursor: number; items: ImportItem[]};
export type GridState = {bpm: number | null; origin_us: number | null; anchor_us: number | null; offset_us: number};
export type Job = {id: string; track_id: string; state: string; stage: string; error: string | null; kind: 'beat' | 'structure'};
export type Playlist = {id: string; name: string; track_ids: string[]};
export type Track = {
  metadata: MetadataState | null; id: string; path: string; title: string; artist: string; duration: number; codec: string;
  sample_rate: number | null; missing: boolean; cues: Cue[]; sections: Section[]; structure_analysis: StructureAnalysis | null;
  grid_anchor_us: number | null; grid_cache_key: string | null; grid_offset_us: number; grid_bpm: number | null;
  grid_origin_us: number | null; fixed_grid_key: string | null; offset_cache_key: string | null;
  detail_analysis: null | {cache_path: string; cache_key: string; tempo: number | null; revision?: string; version?: string};
  analysis: null | {peaks: number[]; tempo: null};
};

/** Tracks longer than this are not analysed automatically (seconds). */
export const ANALYSIS_MAX_SECONDS = 900;
/**
 * Whether a track should be analysed automatically. `version` is the analysis version the engine
 * reports in its handshake; until that is known nothing is queued.
 */
export const needsAnalysis = (track: Pick<Track, 'missing' | 'duration' | 'detail_analysis'>, version: string | null) =>
  !!version && !track.missing && track.duration <= ANALYSIS_MAX_SECONDS && track.detail_analysis?.version !== version;

export const AUDIO_EXTENSIONS = ['mp3', 'wav', 'aiff', 'aif', 'm4a', 'flac', 'ogg', 'opus'];

export const time = (seconds: number) => `${Math.floor(seconds / 60)}:${Math.floor(seconds % 60).toString().padStart(2, '0')}`;
/** Minutes, seconds and hundredths, for positions the user edits. */
export const preciseTime = (seconds: number) => `${Math.floor(seconds / 60)}:${(seconds % 60).toFixed(2).padStart(5, '0')}`;
export const plural = (count: number, word: string) => `${count} ${word}${count === 1 ? '' : 's'}`;
export const slotName = (slot: number) => String.fromCharCode(65 + slot);
export const cueColors = ['#ef5c86', '#57b7ee', '#9ccc51', '#ae7cf5', '#edac52', '#5acbb7', '#d885c3', '#b3bec8'];
export const sectionLabels = ['Intro', 'Verse', 'Chorus', 'Bridge', 'Break', 'Instrumental', 'Solo', 'Up', 'Down', 'Outro', 'Other'];
export const sectionColors: Record<string, string> = {Intro: '#356997', Verse: '#536d8e', Chorus: '#25835e', Bridge: '#67548b', Instrumental: '#4b7180', Solo: '#965c60', Break: '#876828', Up: '#7849aa', Down: '#92602b', Outro: '#496a99', Other: '#5c6f6b'};

export const trackBpm = (track: Pick<Track, 'grid_bpm' | 'detail_analysis'>) => track.grid_bpm || track.detail_analysis?.tempo || null;
export const pendingTags = (track: Pick<Track, 'metadata'>) => !!track.metadata && (Object.keys(track.metadata.pending).length > 0 || track.metadata.artwork_pending);

/** True while the user is typing or a dialog owns the keyboard, so global shortcuts must not fire. */
export const typingTarget = (event: KeyboardEvent) => {
  const el = event.target as HTMLElement | null;
  return !el || ['INPUT', 'TEXTAREA', 'SELECT'].includes(el.tagName) || el.isContentEditable || !!el.closest('[role=dialog],[role=alertdialog],[role=menu]')
    || !!document.querySelector('.modal-backdrop,.download-overlay');
};
