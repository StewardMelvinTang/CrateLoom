export class Envelope {
  readonly levels:Int16Array[];
  constructor(readonly samples:Int16Array){
    this.levels=[samples];
    let prior=samples;
    while(prior.length>2){
      const next=new Int16Array(Math.ceil(prior.length/4)*2);
      for(let i=0;i<next.length/2;i++){
        const a=i*4,b=Math.min(prior.length-2,a+2);
        next[i*2]=Math.min(prior[a],prior[b]);next[i*2+1]=Math.max(prior[a+1],prior[b+1]);
      }
      this.levels.push(next);prior=next;
    }
  }
  get length(){return this.samples.length/2;}
  range(first:number,last:number,out:[number,number]=[0,0]):[number,number]{
    first=Math.max(0,Math.floor(first));last=Math.min(this.length,Math.ceil(last));
    let lo=32767,hi=-32768;
    if(last-first<=32){
      for(let i=first;i<last;i++){lo=Math.min(lo,this.samples[i*2]);hi=Math.max(hi,this.samples[i*2+1]);}
      out[0]=lo/32767;out[1]=hi/32767;return out;
    }
    while(first<last){
      const remaining=last-first, aligned=first===0 ? remaining : first & -first;
      const level=31-Math.clz32(Math.min(remaining,aligned)),size=1<<level,index=(first/size)*2;
      const values=this.levels[level];lo=Math.min(lo,values[index]);hi=Math.max(hi,values[index+1]);first+=size;
    }
    out[0]=lo/32767;out[1]=hi/32767;return out;
  }
}

// Interpolate a coarse media clock on display frames; rebase real seeks/stalls.
export class PlaybackClock {
  private visual=0; private media=0; private at:number|null=null;
  reset(media:number,at:number){this.visual=media;this.media=media;this.at=at;return media;}
  sample(media:number,at:number,rate=1,advancing=true){
    if(!advancing || this.at==null || media<this.media-.001)return this.reset(media,at);
    let next=this.visual+Math.max(0,at-this.at)/1000*rate;
    const error=media-next;
    if(Math.abs(error)>.12)next=media;
    else if(media!==this.media && Math.abs(error)>.025)next+=error*.15;
    this.visual=Math.max(media-.1,Math.min(media+.1,next));this.at=at;this.media=media;
    return this.visual;
  }
}
