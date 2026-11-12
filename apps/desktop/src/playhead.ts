import {useEffect, useRef, useState, useSyncExternalStore} from 'react';

// The playback position changes on every animation frame. Keeping it outside React state means only
// the few components that draw the playhead re-render, not the whole application.
class Playhead {
  private value = 0;
  private listeners = new Set<() => void>();
  get = () => this.value;
  set = (value: number) => {
    if (value === this.value) return;
    this.value = value;
    this.listeners.forEach(listener => listener());
  };
  subscribe = (listener: () => void) => {
    this.listeners.add(listener);
    return () => { this.listeners.delete(listener); };
  };
}

export const playhead = new Playhead();

/** Current playback position in seconds; re-renders the calling component on every change. */
export const usePlayhead = () => useSyncExternalStore(playhead.subscribe, playhead.get);

/** A value derived from the position; re-renders only when the derived value changes. */
export function usePlayheadValue<T>(select: (position: number) => T) {
  const selector = useRef(select);
  selector.current = select;
  const [value, setValue] = useState(() => select(playhead.get()));
  useEffect(() => {
    const update = () => setValue(selector.current(playhead.get()));
    update();
    return playhead.subscribe(update);
  }, []);
  return value;
}
