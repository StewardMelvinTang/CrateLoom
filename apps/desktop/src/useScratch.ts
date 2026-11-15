import {useEffect, useRef} from 'react';
import {convertFileSrc} from '@tauri-apps/api/core';

/** Scrub audio decodes the whole file into memory, so it is limited to ordinary track lengths (seconds). */
export const SCRATCH_MAX_SECONDS = 900;

type Scratch = {context: AudioContext; node: AudioWorkletNode; gain: GainNode; path: string};

/** Variable-speed audio while the waveform is dragged. Follows the player volume and loads at most once per file. */
export function useScratch(path: string, enabled: boolean, volume: number, onUnavailable: () => void) {
  const current = useRef<Scratch | null>(null);
  const loading = useRef<string | null>(null), failed = useRef<string | null>(null), generation = useRef(0);
  const level = useRef(volume); level.current = volume;
  const unavailable = useRef(onUnavailable); unavailable.current = onUnavailable;
  useEffect(() => { if (current.current) current.current.gain.gain.value = volume; }, [volume]);
  useEffect(() => () => {
    generation.current++; loading.current = null;
    if (current.current) { void current.current.context.close(); current.current = null; }
  }, [path]);
  const load = () => {
    if (!enabled || current.current?.path === path || loading.current === path || failed.current === path) return;
    const run = ++generation.current;
    const context = new AudioContext();
    loading.current = path;
    fetch(convertFileSrc(path)).then(response => { if (!response.ok) throw new Error('Audio unavailable'); return response.arrayBuffer(); })
      .then(bytes => context.decodeAudioData(bytes)).then(async buffer => {
        await context.audioWorklet.addModule(new URL('./scrub-processor.js', import.meta.url).href);
        if (run !== generation.current) { void context.close(); return; }
        const node = new AudioWorkletNode(context, 'crateloom-scrub', {outputChannelCount: [buffer.numberOfChannels]});
        const channels = Array.from({length: buffer.numberOfChannels}, (_, channel) => buffer.getChannelData(channel).slice());
        node.port.postMessage({type: 'load', channels}, channels.map(channel => channel.buffer));
        const gain = context.createGain(); gain.gain.value = level.current;
        node.connect(gain); gain.connect(context.destination);
        current.current = {context, node, gain, path};
      }).catch(() => {
        void context.close();
        // Remember the failure so hovering the waveform does not download and decode the file again.
        if (run === generation.current) { failed.current = path; unavailable.current(); }
      }).finally(() => { if (run === generation.current) loading.current = null; });
  };
  const sound = (from: number, to: number, elapsed = .016) => {
    const item = current.current; if (!item || item.path !== path) return;
    if (item.context.state === 'suspended') void item.context.resume();
    item.node.port.postMessage({type: 'move', from, speed: (to - from) / Math.max(.005, elapsed)});
  };
  const stop = () => current.current?.node.port.postMessage({type: 'stop'});
  return {load, sound, stop};
}
