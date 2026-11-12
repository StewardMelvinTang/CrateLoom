import React, {useRef} from 'react';
import {Envelope, decodeSignal} from './waveform-envelope';
import type {AnalysisData} from './types';

export const WaveBars = React.memo(function WaveBars({data, fallback, start, length, duration, overview = false}: {data: AnalysisData | null; fallback: number[]; start: number; length: number; duration: number; overview?: boolean}) {
  const canvas = useRef<HTMLCanvasElement>(null), dimensions = useRef({width: 0, height: 0});
  const view = useRef({start, length}); view.current = {start, length};
  const prepared = React.useMemo(() => {
    let samples = decodeSignal(data?.waveform?.signal?.data);
    if (!samples) {
      const peaks = Array.isArray(data?.waveform?.peaks) ? data!.waveform.peaks : fallback;
      samples = new Int16Array(peaks.length * 2);
      peaks.forEach((p, i) => { samples![i * 2] = -Math.round(p * 32767); samples![i * 2 + 1] = Math.round(p * 32767); });
    }
    const rgb = Array.isArray(data?.waveform?.rgb) && data!.waveform.rgb.length ? data!.waveform.rgb : [[0, .7, .9]];
    const colors = rgb.map(value => {
      const weights = value.map(v => v * v * v), total = weights[0] + weights[1] + weights[2] + .00001;
      return `rgb(${Math.round(20 + 235 * weights[0] / total)},${Math.round(20 + 235 * weights[1] / total)},${Math.round(20 + 235 * weights[2] / total)})`;
    });
    return {envelope: new Envelope(samples), colors};
  }, [data, fallback]);
  const draw = React.useCallback(() => {
    const el = canvas.current; if (!el || !duration) return;
    const {width, height} = dimensions.current, ctx = el.getContext('2d'); if (!ctx || !width || !height) return;
    ctx.clearRect(0, 0, width, height);
    const {envelope, colors} = prepared, count = envelope.length, {start, length} = view.current; if (!count || !length) return;
    const centre = height / 2, gain = overview ? .66 : .72, bounds: [number, number] = [0, 0];
    for (let x = 0; x < width; x++) {
      const from = (start + x / width * length) / duration * count, to = (start + (x + 1) / width * length) / duration * count;
      if (to <= 0 || from >= count) continue;
      const [lo, hi] = envelope.range(from, Math.max(from + 1, to), bounds);
      const ci = Math.max(0, Math.min(colors.length - 1, Math.floor((from + to) / 2 / count * colors.length)));
      ctx.fillStyle = colors[ci]; ctx.fillRect(x, centre - hi * centre * gain, 1, Math.max(.8, (hi - lo) * centre * gain));
    }
  }, [prepared, duration, overview]);
  // Keep the resize observer for the canvas lifetime, not every playback frame.
  React.useLayoutEffect(() => {
    const el = canvas.current; if (!el) return;
    const resize = () => {
      const rect = el.getBoundingClientRect(), scale = window.devicePixelRatio || 1;
      const width = Math.max(1, Math.round(rect.width * scale)), height = Math.max(1, Math.round(rect.height * scale));
      if (el.width !== width) el.width = width; if (el.height !== height) el.height = height;
      dimensions.current = {width, height}; draw();
    };
    resize(); const observer = new ResizeObserver(resize); observer.observe(el); return () => observer.disconnect();
  }, [draw]);
  React.useLayoutEffect(draw, [start, length, draw]);
  return <canvas ref={canvas} aria-hidden="true"/>;
});
