const {test}=require('node:test'),assert=require('node:assert/strict'),ts=require('typescript'),fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
const exportsObject={};
vm.runInNewContext(ts.transpileModule(fs.readFileSync(path.join(__dirname,'../src/wheel-input.ts'),'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText,{exports:exportsObject});
const {WheelInput}=exportsObject;
const event=(y,x=0,t=0)=>({deltaX:x,deltaY:y,timeStamp:t,deltaMode:0});
test('mouse detents zoom in pixel and line modes',()=>{const w=new WheelInput();assert.equal(w.action(event(100),'auto'),'zoom');assert.equal(w.action({...event(3),deltaMode:1},'auto'),'zoom');});
test('trackpad acceleration keeps scrubbing through a gesture',()=>{const w=new WheelInput();for(const e of [event(2.5,0,0),event(64,0,16),event(120,0,32),event(5,0,48)])assert.equal(w.action(e,'auto'),'scrub');assert.equal(w.action(event(100,0,1000),'auto'),'zoom');});
test('horizontal scroll scrubs; pinch zooms; explicit mode handles ambiguous devices',()=>{const w=new WheelInput();assert.equal(w.action(event(0,60),'auto'),'scrub');assert.equal(w.action({...event(1),ctrlKey:true},'trackpad'),'zoom');assert.equal(w.action(event(100),'trackpad'),'scrub');assert.equal(w.action(event(2),'mouse'),'zoom');});
