import {useCallback, useState} from 'react';
import {pendingTags, trackBpm, type Track} from './types';

export type ColumnId = 'title' | 'artist' | 'bpm' | 'key' | 'time' | 'status';
export type TrackSort = {column: ColumnId; direction: 'asc' | 'desc'} | null;
type Resizable = Exclude<ColumnId, 'title'>;

/** Title always takes the remaining width; the others can be resized from their left edge. */
export const COLUMNS: {id: ColumnId; label: string; sortLabel: string; numeric?: boolean}[] = [
  {id: 'title', label: 'Title', sortLabel: 'title'},
  {id: 'artist', label: 'Artist', sortLabel: 'artist'},
  {id: 'bpm', label: 'BPM', sortLabel: 'tempo', numeric: true},
  {id: 'key', label: 'Key', sortLabel: 'key'},
  {id: 'time', label: 'Time', sortLabel: 'length', numeric: true},
  {id: 'status', label: 'Status', sortLabel: 'status'},
];
export const WIDTH_LIMITS: Record<Resizable, {min: number; max: number}> = {
  artist: {min: 70, max: 520}, bpm: {min: 40, max: 140}, key: {min: 38, max: 140}, time: {min: 40, max: 140}, status: {min: 70, max: 420},
};
/** Default track list widths. Artist shares the free space with Title until it is resized. */
const DEFAULT_WIDTHS: Record<Resizable, string> = {artist: 'minmax(100px,1fr)', bpm: '48px', key: '44px', time: '46px', status: 'minmax(118px,150px)'};

const SORT_KEY = 'trackSort', WIDTH_KEY = 'trackColumnWidths';
const read = <T,>(key: string, fallback: T): T => { try { const raw = localStorage.getItem(key); return raw ? JSON.parse(raw) as T : fallback; } catch { return fallback; } };
const write = (key: string, value: unknown) => { try { localStorage.setItem(key, JSON.stringify(value)); } catch { /* applies until the app closes */ } };

const valid = (value: unknown): value is TrackSort => value === null || (typeof value === 'object' && !!value
  && COLUMNS.some(column => column.id === (value as {column?: string}).column) && ['asc', 'desc'].includes((value as {direction?: string}).direction ?? ''));

export function useTrackSort() {
  const [sort, setSortState] = useState<TrackSort>(() => { const saved = read<unknown>(SORT_KEY, null); return valid(saved) ? saved : null; });
  const setSort = useCallback((next: TrackSort) => { setSortState(next); write(SORT_KEY, next); }, []);
  /** Ascending, then descending, then back to the list's own order. */
  const cycle = useCallback((column: ColumnId) => setSortState(current => {
    const next: TrackSort = current?.column !== column ? {column, direction: 'asc'} : current.direction === 'asc' ? {column, direction: 'desc'} : null;
    write(SORT_KEY, next);
    return next;
  }), []);
  return {sort, setSort, cycle};
}

export function useColumnWidths() {
  const [widths, setWidthsState] = useState<Partial<Record<Resizable, number>>>(() => {
    const saved = read<Record<string, unknown>>(WIDTH_KEY, {}), out: Partial<Record<Resizable, number>> = {};
    for (const id of Object.keys(WIDTH_LIMITS) as Resizable[]) {
      const value = saved?.[id];
      if (typeof value === 'number' && Number.isFinite(value)) out[id] = clampWidth(id, value);
    }
    return out;
  });
  const setWidth = useCallback((id: Resizable, value: number | null) => setWidthsState(current => {
    const next = {...current};
    if (value === null) delete next[id]; else next[id] = clampWidth(id, value);
    write(WIDTH_KEY, next);
    return next;
  }), []);
  const style = Object.fromEntries((Object.keys(DEFAULT_WIDTHS) as Resizable[]).map(id => [`--col-${id}`, widths[id] ? `${widths[id]}px` : DEFAULT_WIDTHS[id]]));
  return {widths, setWidth, style};
}

export const clampWidth = (id: Resizable, value: number) => Math.round(Math.min(WIDTH_LIMITS[id].max, Math.max(WIDTH_LIMITS[id].min, value)));

/** Camelot codes sort around the wheel: 1A, 1B, 2A … 12B. */
export const camelotRank = (code: string | null | undefined) => {
  const match = /^(1[0-2]|[1-9])([AB])$/.exec(code ?? '');
  return match ? Number(match[1]) * 2 + (match[2] === 'B' ? 1 : 0) : null;
};
export const trackKey = (track: Pick<Track, 'detail_analysis'>) => track.detail_analysis?.key ?? null;

const text = new Intl.Collator(undefined, {sensitivity: 'base', numeric: true});
/** Attention first: missing files, then unwritten tags, then tracks with the most cues. */
const statusRank = (track: Track) => (track.missing ? 0 : pendingTags(track) ? 1000 : 2000) - Math.min(track.cues.length, 999);

function value(track: Track, column: ColumnId): string | number | null {
  switch (column) {
    case 'title': return track.title.trim() || null;
    case 'artist': return track.artist.trim() || null;
    case 'bpm': return trackBpm(track);
    case 'key': return camelotRank(trackKey(track)?.camelot);
    case 'time': return track.duration > 0 ? track.duration : null;
    case 'status': return statusRank(track);
  }
}

/** A sorted copy; tracks with no value (not analysed, no artist) always come last. Ties keep the list's order. */
export function sortTracks<T extends Track>(tracks: T[], sort: TrackSort): T[] {
  if (!sort) return tracks;
  const sign = sort.direction === 'asc' ? 1 : -1;
  return tracks.map((track, index) => ({track, index, key: value(track, sort.column)})).sort((a, b) => {
    if (a.key === null || b.key === null) return a.key === b.key ? a.index - b.index : a.key === null ? 1 : -1;
    const order = typeof a.key === 'number' && typeof b.key === 'number' ? a.key - b.key : text.compare(String(a.key), String(b.key));
    return order ? order * sign : a.index - b.index;
  }).map(entry => entry.track);
}
