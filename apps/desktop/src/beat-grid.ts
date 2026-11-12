// Complete a preview grid without changing detected beats or stored analysis.
export function completeBeats(detected: number[], duration: number, bpm: number | null, origin: number | null, offset=0): number[] {
  if (!(duration>0)) return [];
  if (bpm && bpm>=20 && bpm<=400 && origin!=null) {
    const interval=60/bpm, start=origin+offset;
    const first=Math.ceil((-start-1e-7)/interval), last=Math.ceil((duration-start)/interval);
    return Array.from({length:Math.max(0,last-first)},(_,i)=>Math.round((start+(first+i)*interval)*1e6)/1e6).filter(b=>b>=0 && b<duration);
  }
  const raw=detected.filter((b,i)=>Number.isFinite(b) && (i===0 || b>detected[i-1]));
  const deltas=raw.slice(1).map((b,i)=>b-raw[i]).filter(d=>d>=.15 && d<=3).sort((a,b)=>a-b);
  const period=deltas.length ? deltas[Math.floor(deltas.length/2)] : bpm && bpm>=20 && bpm<=400 ? 60/bpm : 0;
  if (!raw.length || !period) return raw.map(b=>b+offset).filter(b=>b>=0 && b<duration);
  const result:number[]=[];
  const add=(b:number)=>{const value=Math.round((b+offset)*1e6)/1e6;if(value>=0 && value<duration)result.push(value);};
  const first=raw[0];
  for(let i=Math.ceil((-first-offset-1e-7)/period);i<0;i++)add(first+i*period);
  add(first);
  for(let i=1;i<raw.length;i++){
    const gap=raw[i]-raw[i-1], steps=gap>period*1.75 ? Math.max(1,Math.round(gap/period)) : 1;
    for(let j=1;j<=steps;j++)add(raw[i-1]+gap*j/steps);
  }
  const last=raw[raw.length-1];
  for(let i=1;last+i*period+offset<duration;i++)add(last+i*period);
  return result;
}

export function lowerBound(beats:number[], value:number):number {
  let lo=0,hi=beats.length;
  while(lo<hi){const mid=(lo+hi)>>>1;if(beats[mid]<value)lo=mid+1;else hi=mid;}
  return lo;
}

export function beatCoordinate(beats:number[], position:number):number {
  if(beats.length<2)return 0;
  const i=Math.max(0,Math.min(beats.length-2,lowerBound(beats,position)-1));
  return i+(position-beats[i])/(beats[i+1]-beats[i]);
}

export function timeAtBeat(beats:number[], coordinate:number):number {
  const i=Math.max(0,Math.min(beats.length-2,Math.floor(coordinate)));
  return beats[i]+(coordinate-i)*(beats[i+1]-beats[i]);
}

// Preserve the fractional beat position; don't snap off-beat playback to a line.
export function jumpBeats(beats:number[], position:number, count:number, duration:number):number {
  if(beats.length<2 || !Number.isFinite(count))return position;
  return Math.max(0,Math.min(duration,timeAtBeat(beats,beatCoordinate(beats,position)+count)));
}
