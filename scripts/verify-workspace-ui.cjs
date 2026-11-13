// Run against Vite or the production build with fixture IPC, without touching a library.
// CRATELOOM_PLAYWRIGHT  path of the playwright package (default: resolve 'playwright')
// CRATELOOM_UI_ENGINE   chromium (default) or webkit
// CRATELOOM_UI_URL      frontend to drive (default http://localhost:1420); a plain Vite server, not the engine-backed preview.
//                       Vite listens on localhost only, which need not answer on 127.0.0.1.
// CRATELOOM_UI_OUTPUT   directory for screenshots and checks.json (default: <tmp>/crateloom-workspace-check/<engine>)
const {chromium,webkit}=require(process.env.CRATELOOM_PLAYWRIGHT || 'playwright');
const engine=process.env.CRATELOOM_UI_ENGINE || 'chromium';
const baseUrl=process.env.CRATELOOM_UI_URL || 'http://localhost:1420';
const fs=require('fs'); const assert=require('node:assert/strict');
(async()=>{
 const browser=await ({chromium,webkit}[engine]).launch({headless:true});
 const page=await browser.newPage({viewport:{width:1280,height:900},deviceScaleFactor:1});
 const errors=[];page.on('pageerror',error=>errors.push(error.message));
 const wav=Buffer.alloc(44+44100*2*8);wav.write('RIFF');wav.writeUInt32LE(wav.length-8,4);wav.write('WAVEfmt ',8);wav.writeUInt32LE(16,16);wav.writeUInt16LE(1,20);wav.writeUInt16LE(1,22);wav.writeUInt32LE(44100,24);wav.writeUInt32LE(88200,28);wav.writeUInt16LE(2,32);wav.writeUInt16LE(16,34);wav.write('data',36);wav.writeUInt32LE(wav.length-44,40);for(let i=44;i<wav.length;i+=2)wav.writeInt16LE(Math.round(Math.sin(i/2/44100*440*2*Math.PI)*5000),i);
 await page.route('**/__qa/audio.wav',route=>route.fulfill({contentType:'audio/wav',body:wav}));
 await page.route('**/__qa/cover-*',route=>route.fulfill({contentType:'image/svg+xml',body:'<svg xmlns="http://www.w3.org/2000/svg" width="100" height="100"><rect width="100" height="100" fill="#53504a"/><path d="M0 68L100 20V100H0Z" fill="#a39b89"/><circle cx="56" cy="42" r="20" fill="#d6d0c4"/></svg>'}));
 await page.addInitScript(()=>{
 localStorage.setItem('theme','dark');
 // Tracks whose saved analysis has another version are analysed again, so the fixture matches the handshake.
 const ANALYSIS_VERSION='librosa-0.11.0-grid-6';
 // Record what each Cue Editor transition animates, when the browser has view transitions.
 const startTransition=document.startViewTransition?.bind(document);
 if(startTransition){window.__qaViewTransitions=true;document.startViewTransition=update=>{
   const transition=startTransition(update);
   transition.ready.then(()=>{window.__qaTransition=document.getAnimations().map(a=>{const target=a.effect?.target,pseudo=a.effect?.pseudoElement||'';const frames=(a.effect?.getKeyframes?.() || []).map(({offset,computedOffset,easing,composite,...values})=>JSON.stringify(values));return {pseudo,kind:a.constructor.name,name:a.animationName||'',still:frames.every(frame=>frame===frames[0]),chrome:!pseudo && !!target?.matches && (target.matches('html,body,#root,.app,main,.sidebar,.titlebar,.transport') || !!target.closest('.sidebar,.titlebar,.transport'))};});},()=>{window.__qaTransition='skipped';});
   return transition;
 };}
 const songs=[['Low (feat. T-Pain)','Flo Rida, T-Pain'],['The Days (Notion Remix)','CHRYSTAL'],["Don\u2019t Stop the Music",'MITCH DB, Level 8, MusicByDavid'],['Starships','Nicki Minaj'],['One More Time','Daft Punk']];
 const tracks=songs.map(([title,artist],i)=>({id:'t'+i,title,artist,path:'/audio.wav',duration:8,codec:location.search.includes('fallback')?'pcm_s16be':'mp3',sample_rate:44100,missing:false,cues:[],sections:[],analysis:null,structure_analysis:null,grid_offset_us:0,metadata:{values:{title,artist},pending:{},artwork_path:'/cover-0'},detail_analysis:{version:ANALYSIS_VERSION,cache_path:'',cache_key:'x'}}));
 const playlists=[{id:'p',name:'Friday selections',track_ids:JSON.parse(localStorage.getItem('qaOrder') || '["t0","t1","t2","t3","t4"]')}];
 let report={id:'set',name:'Friday selections',watch:null,youtube_browser:null,playlist_id:'p',warning:null,tracks:songs.map(([title,artist],i)=>({entry_id:'e'+i,position:i,at:i<4?i*180:null,title,artist,duration:180+i*23,artwork_path:'/cover-'+i,evidence:['Spotify'],track_id:i===0?'t0':null,playback_track_id:i===0?'t0':null,download_id:null,download_state:null,error:null,needs_review:i>0,links:[{label:'Spotify',url:'https://open.spotify.com/track/123'}],source_options:i===0?[]:Array.from({length:i===1?10:(location.search.includes('sole')?1:2)},(_,j)=>({title:j===0?`${artist} \u2014 ${title} (Extended Mix)`:`${artist} \u2014 ${title} (${['Official Audio','Official Music Video','Lyrics','Club Edit'][j%4]})`,url:`https://www.youtube.com/watch?v=${i}${j}123456789`,duration:180+j*12,compatible:true,recommended:j===0,recommendation_reason:'Extended mix'}))}))};
 const tasks=new Map();let count=0;
 const savedTracks=JSON.parse(localStorage.getItem('qaMetadataTracks') || 'null');if(savedTracks)tracks.splice(0,tracks.length,...savedTracks);
 let metadataBatch=JSON.parse(localStorage.getItem('qaMetadataBatch') || 'null');
 const copy=value=>JSON.parse(JSON.stringify(value));
 const batchSnapshot=()=>{
   if(!metadataBatch)return null;
   const elapsed=Date.now()-metadataBatch.started;
   if(metadataBatch.state==='cancelling'){
     metadataBatch.state='cancelled';metadataBatch.items.forEach(item=>{if(['queued','running'].includes(item.state))item.state='cancelled';});
   }else if(metadataBatch.state==='running'){
     const done=elapsed>metadataBatch.duration?metadataBatch.items.length:elapsed>800?1:0;
     metadataBatch.items.forEach((item,i)=>{
       if(i>=done || ['applied','needs_review'].includes(item.state))return;
       const track=tracks.find(t=>t.id===item.id);
       const choice={provider:'Apple Music',recording_id:String(101+i),release_id:'456',values:{...track.metadata.values,album:'Automatic album',date:'2007-10-09',genre:'House'},rank:99,match_status:'strong',duration:track.duration,duration_difference:0,version:'',reasons:[]};
       if(metadataBatch.items.length>1 && i===metadataBatch.items.length-1){
         item.state='needs_review';item.error='No usable album cover was found.';
         track.metadata.automatic_review={state:'needs_review',reason:item.error,candidates:[choice],warnings:[]};
       }else{
         item.state='applied';
         // A run started with write_tags also writes each applied track; the write itself can fail.
         const wrote=metadataBatch.write_tags && !metadataBatch.fail_writes;
         if(metadataBatch.write_tags)Object.assign(item,wrote?{written:true,backup:'/backups/'+track.id}:{written:false,write_error:'The file is read-only.'});
         track.metadata={values:choice.values,pending:wrote?{}:choice.values,artwork_path:'/cover-'+track.id,artwork_pending:!wrote,source:{provider:choice.provider,recording_id:choice.recording_id,release_id:choice.release_id},automatic:{rank:99,match_status:'strong',reviewed:false},automatic_review:{state:'applied',candidates:[choice],warnings:[],query:{title:track.title,artist:track.artist,version:''}}};
       }
     });
     if(done===metadataBatch.items.length)metadataBatch.state='completed';
   }
   metadataBatch.processed=metadataBatch.items.filter(item=>!['queued','running','cancelled'].includes(item.state)).length;
   for(const state of ['applied','needs_review','failed','skipped','cancelled'])metadataBatch[state]=metadataBatch.items.filter(item=>item.state===state).length;
   metadataBatch.written=metadataBatch.items.filter(item=>item.written===true).length;metadataBatch.write_failed=metadataBatch.items.filter(item=>item.write_error).length;
   localStorage.setItem('qaMetadataTracks',JSON.stringify(tracks));localStorage.setItem('qaMetadataBatch',JSON.stringify(metadataBatch));return copy(metadataBatch);
 };
 window.__TAURI_INTERNALS__={convertFileSrc:path=>path.startsWith('/cover')?'/__qa'+path:'/__qa/audio.wav',invoke:async(cmd,args)=>{
 if(cmd==='open_reference')return;
 if(cmd!=='engine_call')return;
 const {method,params:p}=args;
 switch(method){
 case 'handshake':return {fast_sections:false,semantic_analysis:false,structure_max_duration:1800,analysis_version:ANALYSIS_VERSION};
 case 'library.list':return copy(tracks);
 case 'playlists.list':{const snapshot=copy(playlists);if(window.__qaStaleNext){window.__qaStaleNext=false;window.__qaStaleStarted=true;await new Promise(resolve=>setTimeout(resolve,400));}return snapshot;}
 case 'acquisitions.list':case 'sets.pending_analysis':return [];
 case 'acquisitions.clear_completed':case 'acquisitions.clear_failed':return {cleared:0};
 case 'providers.capabilities':return {download_directory:'/Music/CrateLoom',max_bytes:0,providers:[]};
 case 'imports.latest':return null;
 case 'metadata.batch.latest':return batchSnapshot();
 case 'metadata.batch.start':{
   window.__qaMetadataStarted=p.track_ids;window.__qaMetadataStartParams=copy(p);metadataBatch={id:'batch'+Date.now()+'-'+count++,write_tags:p.write_tags===true,fail_writes:!!window.__qaFailTagWrites,state:'running',total:p.track_ids.length,started:Date.now(),duration:window.__qaSlowMetadata?15000:1400,items:p.track_ids.map(id=>({id,title:tracks.find(t=>t.id===id).title,state:'running',stage:'Finding best metadata'}))};return batchSnapshot();
 }
 case 'metadata.batch.get':return batchSnapshot();
 case 'metadata.batch.cancel':metadataBatch.state='cancelling';return copy(metadataBatch);
 case 'sets.capabilities':return {fingerprinting:true,spotify_connected:false,soundcloud_connected:false};
 case 'sets.scan':{window.__qaLastScanParams=copy(p);if(window.__qaScanReject){const text=window.__qaScanReject;window.__qaScanReject=null;throw new Error(text);}
   const id='scan'+count++;tasks.set(id,{scan:true,audio:p.fingerprint,url:p.url,browser:p.youtube_browser,started:Date.now(),cancelled:0});return {id,state:'running',cancellable:true,progress:{stage:'Reading published tracklist',completed:0,total:null,unit:'steps'}};}
 case 'sets.cancel_task':{const scan=tasks.get(p.task_id);scan.cancelled=Date.now();return {id:p.task_id,state:'cancelling',cancellable:true};}
 case 'sets.cancel_scan':report.scan={...report.scan,automatic:false,running:false,cancelling:false,progress:undefined};return copy(report);
 case 'sets.auto_scan':window.__qaAutoScanParams=copy(p);if('youtube_browser' in p)report.youtube_browser=p.youtube_browser;report.scan={...report.scan,automatic:p.enabled,running:false,progress:undefined};return copy(report);
 case 'sets.list':return [{id:'set',name:report.name,playlist_id:report.playlist_id,entries:report.tracks.length,imported:1,with_download:0,offset:null,duration:null,automatic:0,created_at:null}];
 case 'sets.get':{const snapshot=copy(report);if(window.__qaStaleSetNext){window.__qaStaleSetNext=false;window.__qaStaleSetStarted=true;await new Promise(resolve=>setTimeout(resolve,400));}return snapshot;}
 case 'playback.prepare':{const id='playback'+count++;tasks.set(id,{end:Date.now()+350,playback:true});window.__qaPlaybackRequests=(window.__qaPlaybackRequests||0)+1;return {id,state:'running'};}
 case 'sets.artwork':{const id='art'+count++;tasks.set(id,{end:Date.now()+100});return {id,state:'running'};}
 case 'sets.add_source':{window.__qaSourceRequests=(window.__qaSourceRequests||0)+1;const id='source'+count++;tasks.set(id,{end:Date.now()+1800,entry_id:p.entry_id});return {id,state:'running'};}
 case 'metadata.get':{const track=tracks.find(t=>t.id===p.id);return track.metadata.automatic_review?copy(track.metadata):{values:{title:'Low (feat. T-Pain)',artist:'Flo Rida,T-Pain',album:'',date:'',genre:''},pending:{},artwork_path:null,source:null};}
 case 'metadata.search':{const id='lookup'+count++;tasks.set(id,{lookup:true,end:Date.now()+2500});return {id,state:'running'};}
 case 'metadata.save':{
   if(window.__qaFailMetadataSave){window.__qaFailMetadataSave=false;throw new Error('Metadata save failed');}
   await new Promise(resolve=>setTimeout(resolve,80));
   window.__qaSavedSource=p.source;window.__qaMetadataSaves=[...(window.__qaMetadataSaves || []),{id:p.id,values:copy(p.values)}];
   const track=tracks.find(t=>t.id===p.id);track.metadata={...track.metadata,values:p.values,pending:p.values,artwork_path:p.artwork_path,source:p.source};
   return {metadata:copy(track.metadata)};
 }
 case 'metadata.job':{const found=tasks.get(p.id);if(found.scan){
   if(window.__qaScanError)return {id:p.id,state:'failed',cancellable:true,result:null,error:window.__qaScanError};
   if(found.cancelled)return {id:p.id,state:Date.now()-found.cancelled>150?'cancelled':'cancelling',cancellable:true,result:null};
   if(window.__qaFinishScan){report.watch=found.url || report.watch;report.youtube_browser=found.browser ?? null;report.scan={offset:300,duration:1800,retry_at:0,automatic:true,running:true,stalled:false,fast:true,samples:7,pending_matches:2,progress:{stage:'Identifying tracks',completed:7,total:null,unit:'samples',position:360,duration:1800,tracks_found:2}};return {id:p.id,state:'completed',cancellable:true,result:copy(report)};}
   return {id:p.id,state:'running',cancellable:true,progress:found.audio?{stage:'Identifying tracks',completed:7,total:null,unit:'samples',position:60,duration:3783,tracks_found:2}:{stage:'Reading playlist entries',completed:12,total:52,unit:'entries'}};
 }if(found.lookup){return {id:p.id,state:Date.now()<found.end?'running':'completed',result:{query:{title:'Low',artist:'Flo Rida,T-Pain',version:''},candidates:[{provider:'Apple Music',recording_id:'275496141',release_id:'275496110',values:{title:'Low (feat. T-Pain)',artist:'Flo Rida',album:'Mail On Sunday',genre:'Hip-Hop/Rap',date:'2007-10-09'},duration:231.4,duration_difference:1.5,match_status:'strong',reasons:[]}]}};}
const task=tasks.get(p.id);if(Date.now()<task.end)return {id:p.id,state:'running'};if(task.playback)return {id:p.id,state:'completed',result:{path:'/audio.wav'}};if(task.entry_id){const row=report.tracks.find(r=>r.entry_id===task.entry_id);row.download_id='dl'+row.entry_id;row.download_state='queued';}return {id:p.id,state:'completed',result:copy(report)};}
 case 'sets.reorder':await new Promise(resolve=>setTimeout(resolve,150));report.tracks=p.entry_ids.map((id,position)=>({...report.tracks.find(row=>row.entry_id===id),position}));return copy(report);
 case 'track.playback':case 'track.get':return copy(tracks.find(t=>t.id===p.id));
 case 'cues.save':{const track=tracks.find(t=>t.id===p.id);track.cues=copy(p.cues);window.__qaCueSaves=[...(window.__qaCueSaves || []),copy(p.cues)];return copy(track);}
 case 'playlists.save':{window.__qaSaves=(window.__qaSaves||0)+1;await new Promise(resolve=>setTimeout(resolve,150));if(window.__qaFailNext){window.__qaFailNext=false;throw new Error('Save failed');}
   let playlist=playlists.find(item=>item.id===p.id);if(!playlist){playlist={id:'created'+count++,name:p.name,track_ids:[]};playlists.push(playlist);}
   playlist.name=p.name;playlist.track_ids=p.track_ids;if(playlist.id==='p')localStorage.setItem('qaOrder',JSON.stringify(p.track_ids));return copy(playlist);}
 case 'playlists.delete':{const at=playlists.findIndex(item=>item.id===p.id);if(at<0)throw new Error('Playlist not found');window.__qaPlaylistDeletes=[...(window.__qaPlaylistDeletes || []),playlists[at].name];playlists.splice(at,1);return {deleted:true};}
 case 'library.remove':{window.__qaLibraryRemovals=[...(window.__qaLibraryRemovals || []),copy(p.ids)];let removed=0;
   for(const id of p.ids){const at=tracks.findIndex(t=>t.id===id);if(at>=0){tracks.splice(at,1);removed++;}}
   playlists.forEach(item=>{item.track_ids=item.track_ids.filter(id=>!p.ids.includes(id));});return {removed};}
 // Anything the interface asks for that this fixture does not answer fails the run at the end.
 default:{const seen=JSON.parse(localStorage.getItem('qaUnhandled') || '[]');if(!seen.includes(method))localStorage.setItem('qaUnhandled',JSON.stringify([...seen,method]));throw new Error('Unhandled '+method);}
 }
 }};
 });
 const checks={},failures=[];const output=process.env.CRATELOOM_UI_OUTPUT || require('node:path').join(require('node:os').tmpdir(),'crateloom-workspace-check',engine);fs.mkdirSync(output,{recursive:true});
 const wait=()=>page.waitForTimeout(250);
 const order=()=>page.locator('.track-name strong').allTextContents();
 // WebKit ends innerText with a line break where Chromium does not.
 const text=locator=>locator.innerText().then(value=>value.trim());
 // The metadata window must keep one frame. Sample its box on every animation frame while something loads.
 const frameSettled=()=>page.locator('.metadata-editor').evaluate(el=>Promise.all(el.getAnimations().map(a=>a.finished.catch(()=>{}))).then(()=>{}));
 const frameBox=()=>page.locator('.metadata-editor').evaluate(el=>{const r=el.getBoundingClientRect();return [r.x,r.y,r.width,r.height].join(',');});
 const watchFrame=()=>page.evaluate(()=>{window.__qaFrames=new Set();window.__qaWatchFrame=true;const tick=()=>{const el=document.querySelector('.metadata-editor');if(el){const r=el.getBoundingClientRect();window.__qaFrames.add([r.x,r.y,r.width,r.height].join(','));}if(window.__qaWatchFrame)requestAnimationFrame(tick);};tick();});
 const watchedFrames=()=>page.evaluate(()=>{window.__qaWatchFrame=false;return [...window.__qaFrames];});
 async function drag(handle,row){const a=await handle.boundingBox(),b=await row.boundingBox();assert.ok(a&&b);await page.mouse.move(a.x+a.width/2,a.y+a.height/2);await page.mouse.down();await page.mouse.move(b.x+Math.min(b.width/2,120),b.y+b.height/2,{steps:12});await page.waitForTimeout(60);await page.mouse.up();await wait();}
 await page.goto(baseUrl);
 await page.getByRole('button',{name:'Collapse sidebar',exact:true}).click();await wait();
 assert.equal(await page.locator('.sidebar').evaluate(el=>Math.round(el.getBoundingClientRect().width)),64);
 assert.equal(await page.locator('.sidebar button[aria-label="Expand sidebar"]').count(),0);
 assert.equal(await page.locator('.titlebar button[aria-label="Expand sidebar"]').count(),1);
 await page.reload();await page.getByRole('button',{name:'Expand sidebar',exact:true}).click();await wait();
 assert.equal(await page.locator('.sidebar').evaluate(el=>Math.round(el.getBoundingClientRect().width)),216);
 checks.sidebar='titlebar toggle, collapsed preference survives reload';
 await page.locator('.playlist-nav .nav').first().click();await wait();
 assert.equal((await order())[0],'Low (feat. T-Pain)');
 await drag(page.locator('.track-row .reorder-handle').first(),page.locator('.track-row').nth(2));
 assert.deepEqual((await order()).slice(0,3),['The Days (Notion Remix)','Don’t Stop the Music','Low (feat. T-Pain)']);
 await page.getByRole('button',{name:'Move Low (feat. T-Pain) up',exact:true}).click();await wait();
 assert.equal((await order())[1],'Low (feat. T-Pain)');
 await page.getByRole('button',{name:'Reorder Low (feat. T-Pain). Use up or down arrow keys.',exact:true}).focus();await page.keyboard.press('ArrowUp');await wait();assert.equal((await order())[0],'Low (feat. T-Pain)');
 await page.getByLabel('Search tracks',{exact:true}).fill('Low');await wait();
 assert.deepEqual(await order(),['Low (feat. T-Pain)']);
 await page.getByRole('button',{name:'Move Low (feat. T-Pain) down',exact:true}).click();await wait();
 await page.getByLabel('Search tracks',{exact:true}).fill('');await wait();
 assert.equal((await order())[1],'Low (feat. T-Pain)');
 const saved=await order();await page.reload();await page.locator('.playlist-nav .nav').first().click();await wait();assert.deepEqual(await order(),saved);
 await page.evaluate(()=>window.__qaFailNext=true);
 await page.getByRole('button',{name:'Move Low (feat. T-Pain) up',exact:true}).click();await wait();assert.deepEqual(await order(),saved);
 await page.getByRole('alert').filter({hasText:'The new order could not be saved: Save failed'}).waitFor();
 await page.getByRole('button',{name:'Dismiss',exact:true}).click();
 // There is no Refresh button any more: the library is read again when the window regains focus, at most once every five seconds.
 await page.evaluate(()=>window.__qaStaleNext=true);
 await page.waitForFunction(()=>{if(!window.__qaStaleStarted)window.dispatchEvent(new Event('focus'));return window.__qaStaleStarted;},null,{polling:250,timeout:15000});
 await page.getByRole('button',{name:'Move Low (feat. T-Pain) up',exact:true}).click();await page.waitForTimeout(650);assert.equal((await order())[0],'Low (feat. T-Pain)');
 checks.playlist='mouse drag, keyboard, buttons, filtered move, reload persistence, failed save rollback, stale refresh on window focus cannot revert order';
 await page.getByRole('button',{name:'Edit metadata for Low (feat. T-Pain)',exact:true}).click();
 await page.getByLabel('Metadata title',{exact:true}).waitFor();await frameSettled();
 const openFrame=await frameBox();assert.equal(openFrame.split(',').slice(2).join('x'),'1040x720');
 const columns=await page.evaluate(()=>['.metadata-details','.metadata-sources'].map(selector=>{const r=document.querySelector(selector).getBoundingClientRect();return {left:r.left,right:r.right,top:r.top};}));
 assert.ok(columns[1].left>=columns[0].right && Math.abs(columns[1].top-columns[0].top)<1,JSON.stringify(columns));
 await watchFrame();
 await page.getByRole('button',{name:'Find matches',exact:true}).click();await page.locator('.metadata-skeleton-match').first().waitFor();await page.waitForTimeout(500);
 const candidate=page.locator('.metadata-match').first();assert.equal(await candidate.isEnabled(),true);
 assert.equal(await text(candidate.locator('.metadata-match-status')),'Close match');await candidate.click();
 assert.equal(await text(candidate.locator('.metadata-match-status')),'Selected');
 await page.getByRole('button',{name:'Save to library',exact:true}).click();
 await page.waitForFunction(()=>window.__qaSavedSource?.provider==='Apple Music');
 assert.equal(await page.evaluate(()=>window.__qaSavedSource.provider),'Apple Music');
 // One search control: once there are results it asks the catalogs again.
 await page.getByRole('button',{name:'Search again',exact:true}).waitFor();assert.equal(await page.getByRole('button',{name:'Find matches',exact:true}).count(),0);
 await page.waitForFunction(()=>!document.querySelector('.metadata-sources-head button').disabled);
 assert.deepEqual(await watchedFrames(),[openFrame]);
 assert.match(await text(page.locator('.metadata-staged')),/^Saved in the library, not yet written to the file: /);
 // The artwork is the cover control: keyboard opens its menu, Escape closes only the menu.
 for(const gone of ['Choose image','Find release cover','Remove cover'])assert.equal(await page.getByRole('button',{name:gone,exact:true}).count(),0);
 const coverButton=page.getByRole('button',{name:'Cover art: add a cover',exact:true});await coverButton.focus();await page.keyboard.press('Enter');
 const coverMenu=page.getByRole('menu',{name:'Cover art: add a cover',exact:true});await coverMenu.waitFor();
 assert.deepEqual((await coverMenu.getByRole('menuitem').allInnerTexts()).map(item=>item.split('\n')[0].trim()),['Choose an image file…','Use the release cover']);
 await page.keyboard.press('Escape');await coverMenu.waitFor({state:'detached'});
 assert.equal(await page.locator('.metadata-editor').count(),1);assert.equal(await coverButton.evaluate(el=>el===document.activeElement),true);
 await wait();await page.screenshot({path:output+'/metadata-dark.png'});
 checks.metadata='early match selectable during lookup with its plain-word grade; provider identity and fields save correctly; cover actions live in the artwork menu';
 checks.metadataFrame='two columns in a '+openFrame.split(',').slice(2).join('x')+' frame that keeps its box from opening through skeleton rows, arriving matches and the finished lookup';
 await page.getByRole('button',{name:'Close metadata editor',exact:true}).click();
 await page.getByRole('button',{name:'Import DJ Set',exact:true}).click();await page.getByRole('button',{name:'History',exact:true}).click();await page.locator('.set-history-open').click();await wait();
 await drag(page.locator('.set-tracklist .reorder-handle').first(),page.locator('.set-tracklist > li').nth(2));
 const setOrder=()=>page.locator('.set-track-info > strong').allTextContents();assert.equal((await setOrder())[2],'Low (feat. T-Pain)');
 await page.getByRole('button',{name:'Move Low (feat. T-Pain) up',exact:true}).click();await wait();assert.equal((await setOrder())[1],'Low (feat. T-Pain)');
 await page.getByRole('button',{name:'Reorder Low (feat. T-Pain). Use up or down arrow keys.',exact:true}).focus();await page.keyboard.press('ArrowUp');await wait();assert.equal((await setOrder())[0],'Low (feat. T-Pain)');
 // Rows are numbered by position; a timed entry also shows its time, with or without a mix link.
 const setTimes=await page.locator('.set-time').allTextContents();assert.deepEqual(setTimes.slice(0,3),['0100:00','0203:00','0306:00']);assert.equal(setTimes.at(-1),'05');
 assert.equal(await page.locator('.set-tracklist > li').nth(1).locator('.set-source-choice.is-recommended .use-source').count(),1);
 await page.getByRole('button',{name:'Other sources \xb7 9',exact:true}).click();
 const otherSources=page.locator('.set-tracklist > li').nth(1).locator('.set-source-options .use-source');assert.equal(await otherSources.count(),9);await otherSources.first().click();
 await page.evaluate(()=>window.__qaStaleSetNext=true);await page.waitForFunction(()=>window.__qaStaleSetStarted);
 await page.getByRole('button',{name:'Move Low (feat. T-Pain) down',exact:true}).click();await page.waitForTimeout(650);assert.equal((await setOrder())[1],'Low (feat. T-Pain)');
 assert.equal(await page.evaluate(()=>window.__qaSourceRequests),1);
 checks.set='mouse drag, keyboard, buttons, display numbering with entry times, recommended source beside the other sources, stale background fetch cannot revert order';
 await wait();await page.screenshot({path:output+'/set-dark.png'});
 await page.getByLabel('Appearance').selectOption('light');await wait();await wait();await page.screenshot({path:output+'/set-light.png'});
 await page.setViewportSize({width:900,height:650});await wait();await wait();await page.screenshot({path:output+'/compact-light.png'});
 assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
 await page.emulateMedia({reducedMotion:'reduce'});
 await page.getByRole('button',{name:'Collapse sidebar',exact:true}).click();await wait();assert.equal(await page.locator('.sidebar').evaluate(el=>Math.round(el.getBoundingClientRect().width)),64);
 checks.layouts='dark/light desktop, compact 900x650, reduced motion';

 await page.getByRole('button',{name:'All Tracks',exact:true}).click();await page.setViewportSize({width:1280,height:900});await wait();
 await page.getByRole('button',{name:'Expand sidebar',exact:true}).click();await wait();await page.getByLabel('Appearance').selectOption('dark');await wait();
 const autoButton=page.getByRole('button',{name:/Auto metadata/});assert.match(await text(autoButton),/\xb7 all 5 tracks$/);
 await page.locator('.track-row input[type=checkbox]').nth(0).check();await page.locator('.track-row input[type=checkbox]').nth(1).check();
 assert.match(await text(autoButton),/\xb7 2 selected$/);await autoButton.click();
 // The first run explains itself and waits for Start; later runs start at once.
 const explainer=page.getByRole('group',{name:'About Auto metadata',exact:true});await explainer.waitFor();
 assert.match(await explainer.innerText(),/the 2 selected tracks/);assert.equal(await page.evaluate(()=>window.__qaMetadataStarted),undefined);
 await explainer.getByRole('button',{name:'Cancel',exact:true}).click();assert.equal(await explainer.count(),0);assert.equal(await page.evaluate(()=>window.__qaMetadataStarted),undefined);
 await autoButton.click();await explainer.getByRole('button',{name:'Start',exact:true}).click();
 await page.waitForFunction(()=>window.__qaMetadataStarted);
 assert.deepEqual(await page.evaluate(()=>window.__qaMetadataStarted),['t0','t1']);
 // Writing to files is off unless its checkbox is ticked.
 const autoWrite=page.getByLabel('Write tags to the files automatically',{exact:true});assert.equal(await autoWrite.isChecked(),false);
 assert.deepEqual(await page.evaluate(()=>window.__qaMetadataStartParams),{track_ids:['t0','t1'],write_tags:false});
 assert.equal(await page.evaluate(()=>localStorage.getItem('autoMetadataExplained')),'1');
 assert.equal(await autoButton.isDisabled(),true);
 await page.locator('.metadata-batch-progress progress').waitFor();
 await wait();await page.screenshot({path:output+'/auto-metadata-running.png'});
 await page.getByText('Metadata analysis complete',{exact:true}).waitFor();
 assert.match(await page.locator('.metadata-batch-summary').innerText(),/1 applied with covers.*1 need review/);
 assert.doesNotMatch(await page.locator('.metadata-batch-summary').innerText(),/written/);
 assert.match(await page.locator('.metadata-batch-explain').innerText(),/Their files are unchanged until you write tags\./);
 assert.equal(await text(page.locator('.track-row .track-status').first()),'Tags not written');
 await page.getByRole('button',{name:'Review results',exact:true}).click();
 await page.getByText(/Applied automatically/).waitFor();
 assert.equal(await page.getByLabel('Metadata album',{exact:true}).inputValue(),'Automatic album');
 assert.equal(await page.locator('.metadata-match').count(),1);
 assert.equal(await page.locator('.metadata-match').getAttribute('aria-pressed'),'true');
 await page.waitForFunction(()=>{const image=document.querySelector('.metadata-cover-button img');return image?.complete && image.naturalWidth>0;});
 // A track with a cover offers to replace or remove it from the artwork itself.
 await page.getByRole('button',{name:'Cover art: change or remove',exact:true}).click();
 assert.deepEqual((await page.getByRole('menu',{name:'Cover art: change or remove',exact:true}).getByRole('menuitem').allInnerTexts()).map(item=>item.split('\n')[0].trim()),['Choose an image file…','Use the release cover','Remove cover']);
 await page.keyboard.press('Escape');
 await frameSettled();await wait();await page.screenshot({path:output+'/auto-metadata-review-dark.png'});
 const reviewFrame=await frameBox();await watchFrame();
 await page.getByRole('button',{name:'Save and next track',exact:true}).click();
 await page.locator('.metadata-window').getByText('No usable album cover was found.',{exact:true}).waitFor();
 await wait();assert.deepEqual(await watchedFrames(),[reviewFrame]);
 checks.metadataFrameNext='the frame keeps its box while saving and loading the next track';
 await page.getByLabel('Metadata album',{exact:true}).fill('Reviewed second album');
 await page.getByRole('button',{name:'Save and previous track',exact:true}).click();
 await page.getByText(/Applied automatically/).waitFor();
 assert.equal((await page.evaluate(()=>window.__qaMetadataSaves)).at(-1).id,'t1');
 assert.equal((await page.evaluate(()=>window.__qaMetadataSaves)).at(-1).values.album,'Reviewed second album');
 await page.evaluate(()=>window.__qaFailMetadataSave=true);
 await page.getByRole('button',{name:'Save and next track',exact:true}).click();
 await page.getByRole('alert').filter({hasText:'Metadata save failed'}).waitFor();
 assert.equal(await page.getByLabel('Metadata album',{exact:true}).inputValue(),'Automatic album');
 assert.match(await page.locator('.metadata-navigation').innerText(),/1 \/ 2/);
 await page.getByRole('button',{name:'Save and next track',exact:true}).click();
 await page.locator('.metadata-window').getByText('No usable album cover was found.',{exact:true}).waitFor();
 assert.equal(await page.getByLabel('Metadata album',{exact:true}).inputValue(),'Reviewed second album');
 await page.getByRole('button',{name:'Close metadata editor',exact:true}).click();
 assert.equal(await page.locator('.panel-presence[data-state=closing] .metadata-window').count(),1);
 await page.locator('.metadata-window').waitFor({state:'detached'});
 // After a restart the finished run is still there, and its results open from the strip.
 await page.reload();await page.getByText('Metadata analysis complete',{exact:true}).waitFor();
 await page.getByRole('button',{name:'Review results',exact:true}).click();await page.getByText(/Applied automatically/).waitFor();
 assert.match(await page.locator('.metadata-navigation').innerText(),/1 \/ 2/);
 await page.getByRole('button',{name:'Close metadata editor',exact:true}).click();
 await page.getByLabel('Appearance').selectOption('light');await wait();
 await page.setViewportSize({width:900,height:650});await wait();await page.screenshot({path:output+'/auto-metadata-complete-light.png'});
 assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
 if(await page.getByRole('button',{name:'Clear track selection',exact:true}).count())await page.getByRole('button',{name:'Clear track selection',exact:true}).click();
 await page.getByLabel('Search tracks',{exact:true}).fill('Low');assert.match(await text(autoButton),/\xb7 1 shown$/);
 // Auto-write: the choice is remembered, sent as write_tags, and the strip says what reached the files.
 assert.equal(await page.locator('.auto-write-note').count(),0);await autoWrite.check();
 assert.match(await text(page.locator('.auto-write-note')),/^Writes only tracks where a match and a cover are found\. Each file is backed up first\.$/);
 assert.equal(await page.evaluate(()=>localStorage.getItem('autoMetadataWriteTags')),'1');
 await page.reload();await page.getByText('Metadata analysis complete',{exact:true}).waitFor();assert.equal(await autoWrite.isChecked(),true);
 // The fixture starts every load in the dark theme; this part runs in the light one.
 await page.getByLabel('Appearance').selectOption('light');await wait();
 await page.getByLabel('Search tracks',{exact:true}).fill('Starships');assert.match(await text(autoButton),/\xb7 1 shown$/);await autoButton.click();
 await page.waitForFunction(()=>window.__qaMetadataStartParams);
 assert.deepEqual(await page.evaluate(()=>window.__qaMetadataStartParams),{track_ids:['t3'],write_tags:true});
 await page.locator('.metadata-batch-progress progress').waitFor();assert.equal(await autoWrite.isDisabled(),true);
 await page.getByText('Metadata analysis complete',{exact:true}).waitFor();
 assert.match(await page.locator('.metadata-batch-summary').innerText(),/1 applied and written to files/);
 assert.match(await page.locator('.metadata-batch-explain').innerText(),/written into their files; a backup of each original was kept\./);
 assert.doesNotMatch(await page.locator('.metadata-batch-progress').innerText(),/unchanged until you write tags|not written/);
 assert.equal(await text(page.locator('.track-row .track-status').first()),'');
 await wait();await page.screenshot({path:output+'/auto-write-written-light.png'});
 // The window in the light theme: full size at 1280x800, one column with the same frame rules at 700px.
 await page.setViewportSize({width:1280,height:800});await page.getByRole('button',{name:'Edit metadata for Starships',exact:true}).click();
 await page.getByLabel('Metadata title',{exact:true}).waitFor();await frameSettled();assert.equal((await frameBox()).split(',').slice(2).join('x'),'1040x712');
 assert.equal(await text(page.locator('.metadata-staged')),'The library and the file’s tags match.');
 await wait();await page.screenshot({path:output+'/metadata-light.png'});
 await page.setViewportSize({width:700,height:800});await wait();
 const narrow=await page.evaluate(()=>{const a=document.querySelector('.metadata-details').getBoundingClientRect(),b=document.querySelector('.metadata-sources').getBoundingClientRect(),f=document.querySelector('.metadata-editor').getBoundingClientRect();return {below:b.top>=a.bottom-1,width:f.width,height:f.height,inside:f.left>=0 && f.right<=innerWidth && f.top>=0 && f.bottom<=innerHeight};});
 assert.deepEqual(narrow,{below:true,width:652,height:712,inside:true});
 await page.screenshot({path:output+'/metadata-narrow-light.png'});
 await page.getByRole('button',{name:'Close metadata editor',exact:true}).click();await page.locator('.metadata-window').waitFor({state:'detached'});
 await page.setViewportSize({width:900,height:650});await wait();
 // A write that fails keeps the applied details, says why, and leads to the Write tags dialog.
 await page.getByLabel('Search tracks',{exact:true}).fill('One More');await page.evaluate(()=>{window.__qaFailTagWrites=true;window.__qaMetadataStartParams=null;});await autoButton.click();
 await page.locator('.metadata-batch-progress progress').waitFor();await page.getByText('Metadata analysis complete',{exact:true}).waitFor();
 assert.match(await page.locator('.metadata-batch-summary').innerText(),/1 applied with covers \xb7 0 written to files/);
 const unwritten=page.locator('.metadata-batch-reasons').filter({hasText:'Why 1 was not written to its file'});
 assert.match(await unwritten.innerText(),/One More Time\s+The file is read-only\./);
 assert.match(await page.locator('.metadata-batch-explain').innerText(),/1 track is saved in the library only, and its file is unchanged until you write tags\./);
 assert.equal(await text(page.locator('.track-row .track-status').first()),'Tags not written');
 await wait();await page.screenshot({path:output+'/auto-write-failed-light.png'});
 await page.getByRole('button',{name:'Write tags for this track…',exact:true}).click();
 const writeDialog=page.getByRole('dialog',{name:'Write tags to files',exact:true});await writeDialog.getByText('To write · 1 file',{exact:true}).waitFor();
 assert.match(await writeDialog.innerText(),/One More Time/);
 // Nothing is written from here: the fixture has no tag writer, and the dialog is only closed.
 await writeDialog.getByRole('button',{name:'Cancel',exact:true}).click();await writeDialog.waitFor({state:'detached'});
 await page.evaluate(()=>{window.__qaFailTagWrites=false;});await autoWrite.uncheck();
 assert.equal(await page.evaluate(()=>localStorage.getItem('autoMetadataWriteTags')),'0');assert.equal(await page.locator('.auto-write-note').count(),0);
 checks.autoWrite='checkbox off by default, remembered across reload, sent as write_tags; results strip reports written files, a failed write with its reason, and reopens it in the Write tags dialog';
 await page.getByLabel('Search tracks',{exact:true}).fill('');assert.match(await text(autoButton),/\xb7 all 5 tracks$/);await page.evaluate(()=>window.__qaSlowMetadata=true);
 await autoButton.click();await page.getByRole('button',{name:'Cancel analysis',exact:true}).click();
 assert.equal(await explainer.count(),0);assert.equal((await page.evaluate(()=>window.__qaMetadataStarted)).length,5);
 assert.equal((await page.evaluate(()=>window.__qaMetadataStartParams)).write_tags,false);
 await page.getByText('Metadata analysis cancelled',{exact:true}).waitFor();
 assert.equal(await autoButton.isEnabled(),true);
 assert.match(await page.locator('.metadata-batch-progress').innerText(),/5 tracks left unchanged/);
 checks.automaticMetadata='scope wording, first-run explainer with Start and Cancel, progress, covers, saved review, previous/next save the correct track, failed save prevents navigation, closing presence, restart and cancellation';

 await page.emulateMedia({reducedMotion:'no-preference'});
 await page.setViewportSize({width:1280,height:900});await page.getByLabel('Appearance').selectOption('dark');await wait();
 // Opening or closing the Cue Editor animates the content area and the artwork/title/artist morph, never the window chrome.
 const viewTransitions=await page.evaluate(()=>!!window.__qaViewTransitions);
 const transition=async(action,shared=['track-artwork','track-title','track-artist'])=>{
   await page.evaluate(()=>window.__qaTransition=null);await action();
   if(!viewTransitions)return;
   await page.waitForFunction(()=>window.__qaTransition);
   const seen=await page.evaluate(()=>window.__qaTransition);assert.notEqual(seen,'skipped');
   const parts=seen.map(item=>item.pseudo).filter(Boolean),detail=JSON.stringify(seen);
   for(const part of ['old(workspace)','new(workspace)',...shared.map(name=>`group(${name})`)])assert.ok(parts.includes(`::view-transition-${part}`),`${part} missing: ${detail}`);
   assert.equal(parts.includes('::view-transition-group(workspace)'),false,detail);
   // Browsers always give the root group a geometry animation; with an unchanged window its keyframes are identical.
   // A hover transition on a button is not part of the view transition, so only animations count for the chrome.
   assert.deepEqual(seen.filter(item=>(/\(root\)$/.test(item.pseudo) && !(item.pseudo==='::view-transition-group(root)' && item.still)) || (item.chrome && item.kind!=='CSSTransition')),[],detail);
 };
 const heading=()=>text(page.locator('main > header h1'));
 await page.locator('.track-name').first().click();await page.waitForFunction(()=>!document.querySelector('.transport .now').disabled);
 assert.deepEqual(await page.evaluate(()=>['body','#root','.app','.titlebar','.sidebar','main','.transport'].map(selector=>getComputedStyle(document.querySelector(selector)).viewTransitionName || 'none')),Array(7).fill('none'));
 await transition(()=>page.locator('.transport .now').click());await page.getByRole('heading',{name:'Cue Editor',exact:true}).waitFor();await page.waitForTimeout(450);
 assert.equal(await page.locator('.editor .player-artwork').count(),1);assert.equal(await page.locator('.transport').count(),0);
 await wait();await page.screenshot({path:output+'/cue-editor-dark.png'});

 // A cue marker: a click or a jitter under four pixels selects it, only a real drag moves it.
 const cueSaves=()=>page.evaluate(()=>window.__qaCueSaves || []);
 await page.locator('.cue-pad-empty').first().click();await page.waitForFunction(()=>window.__qaCueSaves?.length===1);
 const marker=page.locator('.wave .marker');await marker.waitFor();await page.locator('.cue-editor').waitFor();
 const cuePosition=()=>text(page.locator('.cue-pad small').first());assert.equal(await cuePosition(),'0:00.00');
 const markerBox=await marker.boundingBox(),waveBox=await page.locator('.wave').boundingBox(),mx=markerBox.x+markerBox.width/2,my=markerBox.y+markerBox.height/2;
 assert.equal(await page.evaluate(([x,y])=>!!document.elementFromPoint(x,y)?.closest('.marker'),[mx,my]),true);
 await page.mouse.move(mx,my);await page.mouse.down();await page.mouse.up();await page.waitForTimeout(350);
 assert.equal((await cueSaves()).length,1);assert.equal(await cuePosition(),'0:00.00');assert.equal(await marker.getAttribute('data-selected'),'true');
 await page.mouse.move(mx,my);await page.mouse.down();await page.mouse.move(mx+3,my,{steps:3});await page.mouse.up();await page.waitForTimeout(350);
 assert.equal((await cueSaves()).length,1);assert.equal(await cuePosition(),'0:00.00');
 await page.mouse.move(mx,my);await page.mouse.down();await page.mouse.move(mx+120,my,{steps:10});await page.waitForTimeout(60);await page.mouse.up();
 await page.waitForFunction(()=>window.__qaCueSaves?.length===2);
 // The detail wave shows duration / zoom seconds (8 s / 16) centred on the playhead, which is at the start.
 const movedCue=(await cueSaves()).at(-1)[0].position_us,expectedCue=((mx+120-waveBox.x)/waveBox.width-.5)*.5*1e6;
 assert.ok(movedCue>0 && Math.abs(movedCue-expectedCue)<3000,JSON.stringify({movedCue,expectedCue}));
 await page.waitForFunction(()=>document.querySelector('.cue-pad small')?.textContent!=='0:00.00');
 assert.equal(await page.getByRole('button',{name:'Undo cue change',exact:true}).isEnabled(),true);
 checks.cueMarkers='clicking or nudging a cue marker selects it without saving, a drag saves the cue at the release position';

 await transition(()=>page.getByRole('button',{name:'Back to library',exact:true}).click());await page.locator('.transport .now').waitFor();await page.waitForTimeout(400);
 assert.equal(await heading(),'All Tracks');assert.match(await page.locator('.track-row .track-status').first().innerText(),/1 cue/);
 checks.playerTransition='whole artwork/title/artist button opens cues, shared elements render, only the content area and the shared elements animate (root, sidebar, titlebar and player do not), return to library succeeds'+(viewTransitions?'':' [this browser has no view transitions: the change is applied directly]');

 // The editor opens over the workspace it was called from and goes back there, with that workspace as it was left.
 await page.locator('.playlist-nav .nav').first().click();await wait();assert.equal(await heading(),'Friday selections');
 await page.getByRole('button',{name:'Edit cues',exact:true}).click();await page.getByRole('heading',{name:'Cue Editor',exact:true}).waitFor();
 await page.getByRole('button',{name:'Back to library',exact:true}).click();await page.locator('.library[data-playlist=true]').waitFor();assert.equal(await heading(),'Friday selections');
 await page.getByRole('button',{name:'Import DJ Set',exact:true}).click();await page.getByRole('button',{name:'History',exact:true}).click();await page.locator('.set-history-open').click();await page.locator('.set-report').waitFor();
 await transition(()=>page.locator('.transport .now').click());await page.getByRole('heading',{name:'Cue Editor',exact:true}).waitFor();
 assert.equal(await page.locator('.set-workspace').isHidden(),true);
 await transition(()=>page.getByRole('button',{name:'Back to Import DJ Set',exact:true}).click());await page.locator('.set-report').waitFor();
 assert.equal(await heading(),'Import DJ Set');assert.equal(await page.locator('.editor').count(),0);
 await page.getByRole('button',{name:'Find Music',exact:true}).click();await page.getByLabel('Song name or link',{exact:true}).fill('Daft Punk One More Time');
 await page.locator('.transport .now').click();await page.getByRole('heading',{name:'Cue Editor',exact:true}).waitFor();
 assert.equal(await page.locator('.discovery-workspace').isHidden(),true);
 await page.getByRole('button',{name:'Back to Find Music',exact:true}).click();await page.locator('.discovery-workspace').waitFor();
 assert.equal(await heading(),'Find Music');assert.equal(await page.getByLabel('Song name or link',{exact:true}).inputValue(),'Daft Punk One More Time');
 // Choosing a destination in the sidebar also leaves the editor.
 await page.locator('.transport .now').click();await page.getByRole('heading',{name:'Cue Editor',exact:true}).waitFor();
 await page.getByRole('button',{name:'All Tracks',exact:true}).click();await page.locator('.library').waitFor();assert.equal(await heading(),'All Tracks');assert.equal(await page.locator('.editor').count(),0);
 checks.editorReturn='Back returns to the playlist, Import DJ Set (open report kept) and Find Music (search text kept) the editor was opened from; a sidebar destination leaves the editor';

 await page.locator('.playlist-nav .nav').first().click();await wait();
 await page.getByRole('button',{name:'Export options',exact:true}).click();await wait();
 const option=page.getByLabel('Include audio files',{exact:true});await option.check();
 const checkboxSize=await option.boundingBox();assert.ok(checkboxSize.width<=20,JSON.stringify(checkboxSize));
 await wait();await page.screenshot({path:output+'/library-toolbar-dark.png'});
 await page.getByRole('button',{name:'Export options',exact:true}).click();
 await page.getByRole('button',{name:'Add music',exact:true}).click();
 assert.deepEqual(await page.getByRole('menuitem').locator('.menu-item-text > span').allTextContents(),['Files…','Folder…']);
 await page.keyboard.press('Escape');assert.equal(await page.getByRole('menu').count(),0);
 await page.setViewportSize({width:600,height:540});await page.getByRole('button',{name:'Collapse sidebar',exact:true}).click();await wait();
 assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
 await wait();await page.screenshot({path:output+'/library-snapped-dark.png'});
 // The cancelled run has nothing to review, so the editor is opened for a ticked row.
 assert.equal(await page.getByRole('button',{name:'Review results',exact:true}).isDisabled(),true);
 await page.getByLabel('Select Low (feat. T-Pain)',{exact:true}).check();await page.getByRole('button',{name:'Edit metadata',exact:true}).click();
 await page.getByLabel('Metadata title',{exact:true}).waitFor();await wait();
 await frameSettled();
 const panelBounds=await page.locator('.metadata-window').boundingBox();assert.ok(panelBounds.x>=0 && panelBounds.x+panelBounds.width<=600 && panelBounds.y>=0 && panelBounds.y+panelBounds.height<=540,JSON.stringify(panelBounds));
 // Under 760px the matches sit below the form in one scrolling column, and the frame still holds while they load.
 const stacked=await page.evaluate(()=>{const a=document.querySelector('.metadata-details').getBoundingClientRect(),b=document.querySelector('.metadata-sources').getBoundingClientRect(),body=document.querySelector('.metadata-editor-body');return {below:b.top>=a.bottom-1,sameLeft:Math.abs(a.left-b.left)<1,overflow:body.scrollWidth>body.clientWidth};});
 assert.deepEqual(stacked,{below:true,sameLeft:true,overflow:false});
 const snappedFrame=await frameBox();await watchFrame();
 await page.getByRole('button',{name:'Search again',exact:true}).click();await page.locator('.metadata-skeleton-match').first().waitFor();await page.locator('.metadata-match').first().waitFor();await wait();
 assert.deepEqual(await watchedFrames(),[snappedFrame]);
 await wait();await page.screenshot({path:output+'/metadata-snapped-dark.png'});
 await page.getByRole('button',{name:'Close metadata editor',exact:true}).click();await page.locator('.metadata-window').waitFor({state:'detached'});
 await page.getByRole('button',{name:'Clear track selection',exact:true}).click();
 checks.toolbar='separate context and task groups, labeled export options, correctly sized checkbox, Add music menu, 600px snapped layout';

 await page.setViewportSize({width:1280,height:900});
 if(await page.getByRole('button',{name:'Expand sidebar',exact:true}).count())await page.getByRole('button',{name:'Expand sidebar',exact:true}).click();
 await page.getByLabel('Appearance').selectOption('dark');
 await page.getByRole('button',{name:'Import DJ Set',exact:true}).click();await page.getByRole('button',{name:'New scan',exact:true}).click();
 const link=page.getByLabel('Mix or playlist link',{exact:true}),access=page.getByLabel('YouTube access',{exact:true}),scanButton=page.getByRole('button',{name:'Scan',exact:true});
 const cancelled=()=>page.getByText(/^Scan cancelled\./).waitFor();
 // The YouTube access choice belongs to a YouTube link; each way of scanning keeps its own input.
 assert.equal(await page.getByRole('radio',{name:'A link',exact:true}).isChecked(),true);assert.equal(await page.locator('.set-access').count(),0);
 await link.fill('https://www.youtube.com/watch?v=RvRhUHTV_8k');await access.waitFor();assert.equal(await access.inputValue(),'');
 await page.getByRole('radio',{name:'A pasted tracklist',exact:true}).check();await page.getByLabel('Tracklist',{exact:true}).waitFor();assert.equal(await link.count(),0);
 await page.getByRole('radio',{name:'A link',exact:true}).check();assert.equal(await link.inputValue(),'https://www.youtube.com/watch?v=RvRhUHTV_8k');
 await scanButton.click();await page.getByText('12 of 52 tracklist entries read',{exact:true}).waitFor();
 const centered=async()=>page.locator('.set-progress-stage').evaluate(stage=>{const box=stage.getBoundingClientRect(),card=stage.querySelector('.scan-progress').getBoundingClientRect();return {x:Math.abs(box.x+box.width/2-card.x-card.width/2),y:Math.abs(box.y+box.height/2-card.y-card.height/2)};});
 let alignment=await centered();assert.ok(alignment.x<2 && alignment.y<2,JSON.stringify(alignment));
 assert.equal(await link.count(),1);
 assert.equal(await link.isDisabled(),true);
 await page.getByRole('button',{name:'History',exact:true}).click();await page.locator('.set-history-open').click();await page.locator('.set-report').waitFor();
 // The open set has its own tab, named after the set.
 assert.equal(await text(page.locator('.set-navigation-open')),'Friday selections');assert.equal(await page.locator('.set-navigation-open').isEnabled(),true);
 assert.equal(await page.locator('.set-progress-stage').count(),0);
 await page.getByRole('button',{name:'View scan',exact:true}).click();
 await wait();await page.screenshot({path:output+'/tracklist-progress-dark.png'});
 await page.getByRole('button',{name:'Cancel scan',exact:true}).click();await cancelled();
 assert.equal(await link.inputValue(),'https://www.youtube.com/watch?v=RvRhUHTV_8k');
 await page.locator('.set-check input').check();
 await scanButton.click();await page.getByText('7 audio samples checked',{exact:true}).waitFor();
 assert.equal(await page.evaluate(()=>window.__qaLastScanParams.fingerprint),true);
 assert.equal(await page.getByRole('progressbar',{name:'Scan progress',exact:true}).getAttribute('value'),null);
 alignment=await centered();assert.ok(alignment.x<2 && alignment.y<2,JSON.stringify(alignment));
 await page.getByLabel('Appearance').selectOption('light');await page.setViewportSize({width:900,height:650});await wait();
 alignment=await centered();await wait();await page.screenshot({path:output+'/audio-progress-compact-light.png'});assert.ok(alignment.x<2 && alignment.y<2,JSON.stringify(alignment));
 assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
 await page.getByRole('button',{name:'Cancel scan',exact:true}).click();await cancelled();
 await scanButton.click();await page.evaluate(()=>window.__qaFinishScan=true);
 // The finished tracklist opens as a report whose audio scan continues by itself and can be paused there.
 const scanStatus=page.locator('.set-scan-status'),pauseScan=page.getByRole('button',{name:'Pause scan',exact:true}),resumeScan=page.getByRole('button',{name:'Resume audio scan',exact:true});
 await scanStatus.getByText('Scanning audio',{exact:true}).waitFor();
 assert.match(await scanStatus.innerText(),/7 audio samples checked[\s\S]*Audio position 6:00 \/ 30:00 \xb7 2 recordings found[\s\S]*Uses public YouTube access \(pause to change\)\./);
 // DESIGN.md: the continuation's stop control stays visible above the player. Recorded instead of thrown so the checks after it still run.
 const cancelBounds=await pauseScan.boundingBox(),footerBounds=await page.locator('.transport').boundingBox();if(!(cancelBounds.y+cancelBounds.height<=footerBounds.y))failures.push('Pause scan is not fully above the player at 900x650: '+JSON.stringify({cancelBounds,footerBounds}));
 await wait();await page.screenshot({path:output+'/audio-continuation-light.png'});
 await pauseScan.click();await resumeScan.waitFor();
 assert.match(await scanStatus.innerText(),/Audio scan paused[\s\S]*Checked up to 05:00 of 30:00\.[\s\S]*7 audio samples saved \xb7 2 matches waiting to be placed/);
 // A paused scan of a YouTube set resumes with the access chosen beside the Resume button.
 const resumeAccess=page.locator('.set-resume-access').getByLabel('YouTube access for this set',{exact:true});
 assert.equal(await resumeAccess.inputValue(),'');await resumeAccess.selectOption('firefox');
 assert.match(await page.locator('.set-resume-access').innerText(),/The new choice is saved with this set when you resume\./);
 await resumeScan.click();await pauseScan.waitFor();
 assert.deepEqual(await page.evaluate(()=>window.__qaAutoScanParams),{id:'set',enabled:true,youtube_browser:'firefox'});
 assert.match(await scanStatus.innerText(),/Uses the Firefox session \(pause to change\)\./);
 await pauseScan.click();await resumeScan.waitFor();
 assert.equal(await resumeAccess.inputValue(),'firefox');assert.match(await resumeAccess.locator('option:checked').innerText(),/\(saved with this set\)/);
 // A scan that finishes while another view is open must not take the view away.
 await page.getByRole('button',{name:'New scan',exact:true}).click();
 assert.equal(await link.inputValue(),'');await link.fill('https://www.youtube.com/watch?v=RvRhUHTV_8k');await page.evaluate(()=>window.__qaFinishScan=false);
 await scanButton.click();
 await page.getByRole('button',{name:'History',exact:true}).click();await page.getByText(/^Scan running/).waitFor();await page.evaluate(()=>window.__qaFinishScan=true);
 await page.waitForTimeout(1200);assert.equal(await page.getByRole('button',{name:'History',exact:true}).getAttribute('aria-pressed'),'true');
 assert.equal(await page.locator('.set-history-panel').count(),1);assert.equal(await page.getByText(/^Scan running/).count(),0);
 await page.locator('.set-navigation-open').click();await page.locator('.set-report').waitFor();
 await page.getByRole('button',{name:'New scan',exact:true}).click();
 await link.fill('https://www.youtube.com/watch?v=LTFCuhofnN8');
 await page.evaluate(()=>{window.__qaScanError='YouTube requires age-verified access to this mix. Choose your signed-in browser under YouTube access, then retry.';window.__qaFinishScan=false;});
 await scanButton.click();
 // An access failure is shown inside the YouTube access block under the link field, not as a general form error.
 const accessAlert=page.locator('.set-access').getByRole('alert');
 await accessAlert.filter({hasText:'YouTube requires age-verified access'}).waitFor();
 assert.equal(await page.getByRole('alert').filter({hasText:'YouTube requires age-verified access'}).count(),1);
 assert.equal(await page.locator('.set-access').getAttribute('data-error'),'true');
 const linkBounds=await link.boundingBox(),accessBounds=await page.locator('.set-access').boundingBox();assert.ok(accessBounds.y>=linkBounds.y+linkBounds.height && accessBounds.y+accessBounds.height<=900,JSON.stringify({linkBounds,accessBounds}));
 assert.equal(await link.inputValue(),'https://www.youtube.com/watch?v=LTFCuhofnN8');
 assert.equal(await access.inputValue(),'');assert.equal(await page.evaluate(()=>window.__qaLastScanParams.youtube_browser),null);
 await access.selectOption('chrome');assert.equal(await accessAlert.count(),0);
 assert.equal(await page.evaluate(()=>localStorage.getItem('setImport.youtubeAccess')),'chrome');
 await page.evaluate(()=>window.__qaScanError=null);
 await scanButton.click();
 await page.getByRole('button',{name:'Cancel scan',exact:true}).waitFor();
 assert.equal(await page.evaluate(()=>window.__qaLastScanParams.youtube_browser),'chrome');
 await page.getByRole('button',{name:'Cancel scan',exact:true}).click();await cancelled();
 // The same when sets.scan itself refuses: the error sits in the access block and the next scan carries the new choice.
 await page.evaluate(()=>window.__qaScanReject='YouTube still requires age verification with the Chrome session. Sign in to YouTube in Chrome with an age-verified account, check that this video plays there, then retry.');
 await scanButton.click();await accessAlert.filter({hasText:'YouTube still requires age verification with the Chrome session'}).waitFor();
 assert.equal(await page.getByRole('alert').count(),1);assert.equal(await link.isEnabled(),true);assert.equal(await page.locator('.set-progress-stage').count(),0);
 assert.equal(await page.evaluate(()=>window.__qaLastScanParams.youtube_browser),'chrome');
 await access.selectOption('safari');assert.equal(await accessAlert.count(),0);assert.match(await page.locator('.set-access-guide').innerText(),/Full Disk Access/);
 await scanButton.click();await page.getByRole('button',{name:'Cancel scan',exact:true}).waitFor();
 assert.equal(await page.evaluate(()=>window.__qaLastScanParams.youtube_browser),'safari');
 await page.getByRole('button',{name:'Cancel scan',exact:true}).click();await cancelled();
 // The choice is remembered across a restart and is only sent with YouTube links.
 await page.reload();await page.getByRole('button',{name:'Import DJ Set',exact:true}).click();
 assert.equal(await page.locator('.set-access').count(),0);
 await link.fill('https://youtu.be/LTFCuhofnN8');assert.equal(await access.inputValue(),'safari');
 await scanButton.click();await page.getByRole('button',{name:'Cancel scan',exact:true}).waitFor();
 assert.equal(await page.evaluate(()=>window.__qaLastScanParams.youtube_browser),'safari');
 await page.getByRole('button',{name:'Cancel scan',exact:true}).click();await cancelled();
 await link.fill('https://open.spotify.com/playlist/37i9dQZF1DX4dyzvuaRJ0n');assert.equal(await page.locator('.set-access').count(),0);
 await scanButton.click();await page.getByRole('button',{name:'Cancel scan',exact:true}).waitFor();
 assert.equal(await page.evaluate(()=>window.__qaLastScanParams.youtube_browser),null);
 await page.getByRole('button',{name:'Cancel scan',exact:true}).click();await cancelled();
 checks.youtubeAccess='access block only for YouTube links, public is the default, an access error from the scan task or from sets.scan itself appears inside the block, the chosen browser is sent with the next scan, survives a reload and is not sent with other links; a paused YouTube set resumes with the access chosen there';
 checks.setScanning='scan-from modes keep their input, tracklist/audio progress centered on desktop and compact layouts, initial scan cancellation, input preservation, honest unknown totals, automatic continuation pause, saved samples and Resume, a scan finishing elsewhere keeps the current view';

 // Deleting a playlist and removing a track both ask first; Cancel and Escape change nothing.
 const dialog=page.getByRole('alertdialog'),status=text=>page.getByRole('status').filter({hasText:text}).waitFor();
 await page.getByLabel('New playlist name',{exact:true}).fill('Disposable crate');await page.getByRole('button',{name:'Create playlist',exact:true}).click();
 await page.getByRole('heading',{name:'Disposable crate',exact:true}).waitFor();assert.equal(await page.locator('.playlist-nav .nav').count(),2);
 const deletePlaylist=async()=>{await page.getByRole('button',{name:'Playlist',exact:true}).click();await page.getByRole('menuitem',{name:/^Delete playlist…/}).click();await dialog.getByRole('heading',{name:'Delete playlist “Disposable crate”?',exact:true}).waitFor();};
 await deletePlaylist();assert.match(await dialog.innerText(),/No tracks or audio files are deleted\./);
 assert.equal(await page.evaluate(()=>document.activeElement?.textContent),'Cancel');
 await dialog.getByRole('button',{name:'Cancel',exact:true}).click();await dialog.waitFor({state:'detached'});
 assert.equal(await page.evaluate(()=>window.__qaPlaylistDeletes),undefined);assert.equal(await page.locator('.playlist-nav .nav').count(),2);
 await deletePlaylist();await dialog.getByRole('button',{name:'Delete playlist',exact:true}).click();await dialog.waitFor({state:'detached'});
 assert.deepEqual(await page.evaluate(()=>window.__qaPlaylistDeletes),['Disposable crate']);
 await status('Deleted the playlist “Disposable crate”. Its tracks are still in your library.');
 assert.equal(await heading(),'All Tracks');assert.deepEqual(await page.locator('.playlist-nav .nav .playlist-name').allTextContents(),['Friday selections']);
 const removeTrack=async()=>{await page.getByRole('button',{name:'Remove…',exact:true}).click();await dialog.getByRole('heading',{name:'Remove 1 track from the library?',exact:true}).waitFor();};
 assert.equal(await page.getByRole('button',{name:'Remove…',exact:true}).isDisabled(),true);
 await page.getByLabel('Select One More Time',{exact:true}).check();
 await removeTrack();assert.match(await dialog.innerText(),/The audio file stays on disk and is never deleted\./);
 await page.keyboard.press('Escape');await dialog.waitFor({state:'detached'});
 assert.equal(await page.evaluate(()=>window.__qaLibraryRemovals),undefined);assert.equal((await order()).length,5);
 await removeTrack();await dialog.getByRole('button',{name:'Remove from library',exact:true}).click();await dialog.waitFor({state:'detached'});
 assert.deepEqual(await page.evaluate(()=>window.__qaLibraryRemovals),[['t4']]);
 await status('Removed 1 track from the library. The audio file was not deleted.');
 assert.deepEqual(await order(),['Low (feat. T-Pain)','The Days (Notion Remix)','Don’t Stop the Music','Starships']);
 assert.match(await text(page.locator('.playlist-nav .nav').first()),/4$/);assert.equal(await page.getByRole('button',{name:'Clear track selection',exact:true}).count(),0);
 checks.removal='playlist deletion and library removal ask in a dialog (Cancel focused, Cancel and Escape change nothing), then call playlists.delete and library.remove; tracks of a deleted playlist stay';

 checks.viewTransitions=viewTransitions;
 checks.unhandledEngineCalls=await page.evaluate(()=>JSON.parse(localStorage.getItem('qaUnhandled') || '[]'));assert.deepEqual(checks.unhandledEngineCalls,[]);
 checks.errors=errors;assert.deepEqual(errors,[]);checks.failures=failures;fs.writeFileSync(output+'/checks.json',JSON.stringify(checks,null,2));console.log(JSON.stringify(checks,null,2));await browser.close();
 assert.deepEqual(failures,[]);
})().catch(e=>{console.error(e);process.exit(1);});
