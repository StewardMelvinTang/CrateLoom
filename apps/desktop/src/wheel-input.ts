export type WheelMode = 'auto' | 'mouse' | 'trackpad';
type WheelSample = {deltaX:number;deltaY:number;deltaMode:number;ctrlKey?:boolean;altKey?:boolean;timeStamp:number};
// WheelEvent has no device identifier. Retain trackpad evidence throughout a
// gesture so accelerated/inertial events don't suddenly become mouse zooms.
export class WheelInput {
  private last = -Infinity;
  private trackpad = false;
  action(e:WheelSample, mode:WheelMode):'zoom'|'scrub' {
    if(e.ctrlKey || e.altKey)return 'zoom';
    if(mode==='mouse')return 'zoom';
    if(mode==='trackpad')return 'scrub';
    if(e.deltaMode!==0)return 'zoom';
    if(e.timeStamp-this.last>250)this.trackpad=false;
    this.last=e.timeStamp;
    if(e.deltaX!==0 || !Number.isInteger(e.deltaY) || Math.abs(e.deltaY)<40)this.trackpad=true;
    return this.trackpad ? 'scrub' : 'zoom';
  }
}
