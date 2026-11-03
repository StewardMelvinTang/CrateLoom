const {test}=require('node:test');
const assert=require('node:assert/strict');
const vm=require('node:vm');
const fs=require('node:fs');
const path=require('node:path');
function processor(){
 let Processor;
 vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../src/scrub-processor.js'),'utf8'),{sampleRate:48000,AudioWorkletProcessor:class{constructor(){this.port={};}},registerProcessor:(name,p)=>Processor=p});
 return new Processor();
}
function render(p,blocks=1){let out;for(let n=0;n<blocks;n++){out=[new Float32Array(128)];p.process([],[out]);}return out[0];}
test('follows forward/reverse movement and stops without repeated grains',()=>{
 const p=processor(),samples=Float32Array.from({length:48000},(_,i)=>i/48000);
 p.port.onmessage({data:{type:'load',channels:[samples]}});
 p.port.onmessage({data:{type:'move',from:.3,speed:1}});
 render(p,6);let out=render(p);assert.ok(out[127]>out[0]);
 p.port.onmessage({data:{type:'move',from:.6,speed:-1}});
 render(p,6);out=render(p);assert.ok(out[127]<out[0]);
 p.port.onmessage({data:{type:'stop'}});out=render(p,30);assert.ok(Math.max(...out.map(Math.abs))<.0001);
});
test('idle gestures fade out; stationary movement emits no DC',()=>{
 const p=processor();p.port.onmessage({data:{type:'load',channels:[new Float32Array(48000).fill(.5)]}});
 p.port.onmessage({data:{type:'move',from:.2,speed:0}});assert.equal(Math.max(...render(p)),0);
 p.port.onmessage({data:{type:'move',from:.2,speed:1}});assert.ok(Math.max(...render(p,5))>.1);
 assert.ok(Math.max(...render(p,70))<.0001);
});
