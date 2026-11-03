/* Bidirectional, variable-rate audition of the decoded track. */
class CrateLoomScrub extends AudioWorkletProcessor {
  constructor() {
    super(); this.channels=[];this.cursor=0;this.speed=0;this.targetSpeed=0;this.gain=0;this.targetGain=0;this.idle=0;
    this.port.onmessage=({data:m})=>{
      if(m.type==='load')this.channels=m.channels;
      if(m.type==='move'){
        if(this.gain<.001 || Math.abs(this.cursor/sampleRate-m.from)>.1)this.cursor=m.from*sampleRate;
        this.targetSpeed=Math.max(-8,Math.min(8,m.speed));this.targetGain=Math.abs(this.targetSpeed)>.001 ? 1 : 0;this.idle=0;
      }
      if(m.type==='stop'){this.targetGain=0;this.targetSpeed=0;}
    };
  }
  process(inputs,outputs){
    const out=outputs[0];
    for(let i=0;i<out[0].length;i++){
      if(++this.idle>sampleRate*.08){this.targetGain=0;this.targetSpeed=0;}
      this.speed+=(this.targetSpeed-this.speed)*.008;this.gain+=(this.targetGain-this.gain)*.008;
      const index=Math.floor(this.cursor),fraction=this.cursor-index;
      for(let c=0;c<out.length;c++){
        const ch=this.channels[Math.min(c,this.channels.length-1)];
        out[c][i]=ch && index>=0 && index+1<ch.length ? (ch[index]*(1-fraction)+ch[index+1]*fraction)*this.gain : 0;
      }
      this.cursor+=this.speed;
    }
    return true;
  }
}
registerProcessor('crateloom-scrub',CrateLoomScrub);
