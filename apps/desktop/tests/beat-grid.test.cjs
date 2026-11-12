const {test}=require('node:test'),assert=require('node:assert/strict'),ts=require('typescript'),fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
const out={};vm.runInNewContext(ts.transpileModule(fs.readFileSync(path.join(__dirname,'../src/beat-grid.ts'),'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText,{exports:out});
const {completeBeats,lowerBound,beatCoordinate,jumpBeats}=out;
test('fills silent intro, dropped detections and outro without removing detected beats',()=>{
 const beats=Array.from(completeBeats([10,10.5,11,13,13.5],20,120,null));
 assert.deepEqual(beats,Array.from({length:40},(_,i)=>i*.5));
 assert.equal(lowerBound(beats,10),20);assert.equal(lowerBound(beats,10.01),21);
 assert.equal(beats.filter((_,i)=>i%4===0).length,10);
});
test('preserves variable detected timing and shifted fixed-grid phase',()=>{
 const raw=[.2,.7,1.3,2,2.8],beats=completeBeats(raw,4,100,null,.01);
 for(const beat of raw)assert.ok(beats.includes(Math.round((beat+.01)*1e6)/1e6));
 assert.deepEqual(Array.from(completeBeats([],3,120,.2,-.3)),[.4,.9,1.4,1.9,2.4,2.9]);
 assert.equal(completeBeats([],20,null,null).length,0);
});
test('beat jump preserves off-beat phase, works in both directions and clamps edges',()=>{
 const beats=completeBeats([2,2.5,3,3.5],20,120,null);
 assert.equal(jumpBeats(beats,1.125,16,20),9.125);
 assert.equal(jumpBeats(beats,9.125,-16,20),1.125);
 assert.equal(jumpBeats(beats,1,.25,20),1.125);
 assert.equal(jumpBeats(beats,1,-16,20),0);
 assert.equal(jumpBeats(beats,19,16,20),20);
});
test('jump follows the beat timeline across tempo changes rather than multiplying global BPM',()=>{
 const beats=[0,.5,1.2,2,2.9];
 assert.ok(Math.abs(jumpBeats(beats,.25,1,3)-.85)<1e-9);
 assert.ok(Math.abs(jumpBeats(beats,.85,-1,3)-.25)<1e-9);
 assert.ok(Math.abs(beatCoordinate(beats,1.6)-2.5)<1e-9);
});
test('frontend completion agrees with the backend anchor-validation grid',()=>{
 const cases=[[[2,2.5,3,5,5.5],8,120,null,0],[[.2,.7,1.3,2,2.8],4,100,null,.01],[[],3,120,.2,-.3],[[10,10.5,11,13],20,120,null,0]];
 const result=require('node:child_process').spawnSync('python3',['-c','import json,sys;from crateloom.beat_grid import complete_beats;print(json.dumps([complete_beats(*c) for c in json.load(sys.stdin)]))'],{input:JSON.stringify(cases),encoding:'utf8',env:{...process.env,PYTHONPATH:path.resolve(__dirname,'../../../engine')}});
 assert.equal(result.status,0,result.stderr);
 JSON.parse(result.stdout).forEach((expected,i)=>assert.deepEqual(Array.from(completeBeats(...cases[i])),expected));
});
