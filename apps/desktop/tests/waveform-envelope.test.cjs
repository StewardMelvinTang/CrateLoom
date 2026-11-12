const {test}=require('node:test'),assert=require('node:assert/strict'),ts=require('typescript'),fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
const out={};vm.runInNewContext(ts.transpileModule(fs.readFileSync(path.join(__dirname,'../src/waveform-envelope.ts'),'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText,{exports:out,atob});
const {Envelope,PlaybackClock,decodeSignal}=out;
test('pyramid ranges preserve exact signed transients at arbitrary zooms and boundaries',()=>{
 const samples=new Int16Array(8198);
 for(let i=0;i<samples.length/2;i++){samples[i*2]=Math.round(Math.sin(i*1.731)*16000-16000);samples[i*2+1]=Math.round(Math.cos(i*.731)*16000+16000);}
 const pyramid=new Envelope(samples);
 for(let n=0;n<300;n++){
  const first=(n*131)%4099,last=Math.min(4099,first+1+(n*67)%1500);let lo=32767,hi=-32768;
  for(let i=first;i<last;i++){lo=Math.min(lo,samples[i*2]);hi=Math.max(hi,samples[i*2+1]);}
  assert.deepEqual(Array.from(pyramid.range(first,last)),[lo/32767,hi/32767]);
 }
 assert.equal(new Envelope(new Int16Array()).length,0);
});
test('media interpolation advances on 120 Hz frames despite coarse 50 ms media samples',()=>{
 const clock=new PlaybackClock();let prior=-1;
 for(let frame=0;frame<120;frame++){
  const seconds=frame/120,actual=Math.floor(seconds/.05+1e-9)*.05;
  const visual=clock.sample(actual,frame*1000/120);
  assert.ok(visual>prior);assert.ok(Math.abs(visual-seconds)<.06);prior=visual;
 }
 assert.equal(clock.sample(12,1100),12);assert.equal(clock.sample(3,1200),3);
 assert.equal(clock.sample(3,1300,1,false),3);
});
test('clock handles playback-rate changes and stalls without indefinite animation drift',()=>{
 const clock=new PlaybackClock();clock.reset(5,0);
 assert.ok(Math.abs(clock.sample(5.02,10,2)-5.02)<1e-9);
 for(let n=1;n<100;n++)clock.sample(5.02,10+n*16,1);
 assert.ok(clock.sample(5.02,1700,1)<=5.12);
});
test('damaged waveform signal data is trimmed or rejected instead of throwing',()=>{
 const encode=bytes=>Buffer.from(bytes).toString('base64');
 assert.deepEqual(Array.from(decodeSignal(encode([1,0,2,0,3,0,4,0]))),[1,2,3,4]);
 assert.deepEqual(Array.from(decodeSignal(encode([1,0,2,0,3,0,4,0,9]))),[1,2,3,4]);
 assert.deepEqual(Array.from(decodeSignal(encode([1,0,2,0,3,0]))),[1,2]);
 for(const bad of [encode([7]),'','not base64 !!',null,undefined,42])assert.equal(decodeSignal(bad),null);
});
