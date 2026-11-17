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
 await page.route('**/__qa/analysis-*',route=>route.fulfill({contentType:'application/json',body:JSON.stringify({waveform:{peaks:Array.from({length:400},(_,i)=>.25+.6*Math.abs(Math.sin(i/9))),rgb:Array.from({length:400},(_,i)=>[.9,.2+.5*(i%40<8),.1])}})}));
 await page.route('**/__qa/cover-*',route=>route.fulfill({contentType:'image/svg+xml',body:'<svg xmlns="http://www.w3.org/2000/svg" width="100" height="100"><rect width="100" height="100" fill="#53504a"/><path d="M0 68L100 20V100H0Z" fill="#a39b89"/><circle cx="56" cy="42" r="20" fill="#d6d0c4"/></svg>'}));
 await page.addInitScript(()=>{
 localStorage.setItem('theme','dark');
 // Tracks whose saved analysis has another version are analysed again, so the fixture matches the handshake.
 const ANALYSIS_VERSION='librosa-0.11.0-grid-7';
 // Record what each Cue Editor transition animates, when the browser has view transitions.
 const startTransition=document.startViewTransition?.bind(document);
 if(startTransition){window.__qaViewTransitions=true;document.startViewTransition=update=>{
   const transition=startTransition(update);
   transition.ready.then(()=>{window.__qaTransition=document.getAnimations().map(a=>{const target=a.effect?.target,pseudo=a.effect?.pseudoElement||'';const frames=(a.effect?.getKeyframes?.() || []).map(({offset,computedOffset,easing,composite,...values})=>JSON.stringify(values));return {pseudo,kind:a.constructor.name,name:a.animationName||'',still:frames.every(frame=>frame===frames[0]),chrome:!pseudo && !!target?.matches && (target.matches('html,body,#root,.app,main,.sidebar,.titlebar,.transport') || !!target.closest('.sidebar,.titlebar,.transport'))};});},()=>{window.__qaTransition='skipped';});
   return transition;
 };}
 const songs=[['Low (feat. T-Pain)','Flo Rida, T-Pain'],['The Days (Notion Remix)','CHRYSTAL'],["Don\u2019t Stop the Music",'MITCH DB, Level 8, MusicByDavid'],['Starships','Nicki Minaj'],['One More Time','Daft Punk']];
 const tracks=songs.map(([title,artist],i)=>({id:'t'+i,title,artist,path:'/audio.wav',duration:8,codec:location.search.includes('fallback')?'pcm_s16be':'mp3',sample_rate:44100,missing:false,cues:[],sections:[],analysis:null,structure_analysis:null,grid_offset_us:0,metadata:{values:{title,artist},pending:{},artwork_path:'/cover-0'},detail_analysis:{version:ANALYSIS_VERSION,cache_path:'',cache_key:'x',tempo:[101,124.5,128,null,123][i],key:[{camelot:'8A',name:'A minor',short:'Am',strength:.7},{camelot:'11B',name:'A major',short:'A',strength:.6},null,{camelot:'2A',name:'E♭ minor',short:'Ebm',strength:.5},{camelot:'10B',name:'D major',short:'D',strength:.6}][i]}}));
 const playlists=[{id:'p',name:'Friday selections',track_ids:JSON.parse(localStorage.getItem('qaOrder') || '["t0","t1","t2","t3","t4"]')}];
 // What a running audio scan has heard but not yet placed (report.provisional).
 const HEARD=[{at:312,end:372,title:'Nightcall',artist:'Kavinsky',samples:2},{at:401,end:430,title:'Midnight City',artist:'M83',samples:1}];
 const IDLE_RECOGNITION={state:'none',confirmed:0,added:0,warning:null};
 let report={id:'set',name:'Friday selections',watch:null,youtube_browser:null,playlist_id:'p',warning:null,discovery:{pending:0,running:0},recognition:IDLE_RECOGNITION,provisional:[],tracks:songs.map(([title,artist],i)=>({entry_id:'e'+i,position:i,at:i<4?i*180:null,title,artist,duration:180+i*23,artwork_path:'/cover-'+i,evidence:['Spotify'],track_id:i===0?'t0':null,playback_track_id:i===0?'t0':null,download_id:null,download_state:null,error:null,needs_review:i>0,source_search:null,links:[{label:'Spotify',url:'https://open.spotify.com/track/123'}],source_options:i===0?[]:Array.from({length:i===1?10:(location.search.includes('sole')?1:2)},(_,j)=>({title:j===0?`${artist} \u2014 ${title} (Extended Mix)`:`${artist} \u2014 ${title} (${['Official Audio','Official Music Video','Lyrics','Club Edit'][j%4]})`,url:`https://www.youtube.com/watch?v=${i}${j}123456789`,duration:180+j*12,compatible:true,recommended:j===0,recommendation_reason:'Extended mix'}))}))};
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
 // Test hooks: shorten a track (its media stays 8 s long, as WebKit reports DASH files) and record copies.
 window.__qaSetDuration=(id,seconds)=>{tracks.find(t=>t.id===id).duration=seconds;};
 // Give a track a saved waveform and detected sections, as analysis would.
 window.__qaSetWave=id=>{const t=tracks.find(t=>t.id===id);t.detail_analysis={...t.detail_analysis,cache_path:'/analysis-'+id+'.json',revision:'wave'};t.sections=[{id:'s1',start_us:0,end_us:3000000,label:'Intro',origin:'auto'},{id:'s2',start_us:3000000,end_us:8000000,label:'Up',origin:'auto'}];};
 Object.defineProperty(navigator,'clipboard',{configurable:true,value:{writeText:async value=>{window.__qaClipboard=value;}}});
 const storageDefault={path:'/Users/qa/.crateloom',source:'default',available:true,volume:'This Mac',external:false,default_path:'/Users/qa/.crateloom',
   usage:{bytes:6.4e9,files:812,groups:[{label:'Downloads',bytes:4.1e9},{label:'DJ set audio',bytes:1.9e9},{label:'Analysis',bytes:3.2e8},{label:'Library and other files',bytes:8e7}]},free:2.1e10,previous:null,moving:null};
 const storageState=()=>{try{return JSON.parse(localStorage.getItem('qaStorage'))||JSON.parse(JSON.stringify(storageDefault));}catch{return JSON.parse(JSON.stringify(storageDefault));}};
 const saveStorage=value=>localStorage.setItem('qaStorage',JSON.stringify(value));
 window.__TAURI_INTERNALS__={convertFileSrc:path=>path.startsWith('/cover')||path.startsWith('/analysis')?'/__qa'+path:'/__qa/audio.wav',invoke:async(cmd,args)=>{
 if(cmd==='open_reference')return;
 if(cmd==='restart_engine'){const restarts=JSON.parse(localStorage.getItem('qaRestarts') || '0');localStorage.setItem('qaRestarts',String(restarts+1));const st=storageState();if(st.moving&&st.moving.state!=='running'){st.moving=null;saveStorage(st);}return;}
 if(cmd==='plugin:dialog|open')return window.__qaOpenPath ?? null;
 if(cmd!=='engine_call')return;
 const {method,params:p}=args;
 switch(method){
 case 'storage.status':{const st=storageState();if(st.moving?.state==='running'){st.moving.copied=Math.min(st.moving.total,st.moving.copied+st.moving.total/2);if(st.moving.copied>=st.moving.total){Object.assign(st,{path:st.moving.target,volume:'SHARED SSD 2',external:true,source:'chosen',previous:{path:'/Users/qa/.crateloom',volume:'This Mac',bytes:6.4e9}});st.moving.state='done';}saveStorage(st);}return copy(st);}
 case 'storage.check':{window.__qaStorageChecks=[...(window.__qaStorageChecks||[]),p];if(p.default)return {target:'/Users/qa/.crateloom',volume:'This Mac',external:false,mode:'move',problem:null,needed:6.4e9,files:812,free:4e10};
   if(p.path==='/Volumes/SHARED SSD 2/Old')return {target:p.path,volume:'SHARED SSD 2',external:true,mode:'existing',problem:null,library:{tracks:42,changed_at:1760000000}};
   return {target:p.path+'/CrateLoom Library',volume:'SHARED SSD 2',external:true,mode:'move',problem:null,needed:6.4e9,files:812,free:3.1e11};}
 case 'storage.move':{window.__qaStorageMoves=[...(window.__qaStorageMoves||[]),p];const st=storageState();st.moving={state:'running',stage:'Copying',target:p.path+'/CrateLoom Library',copied:0,total:6.4e9,files_done:0,files:812,current:'downloads/track-1.m4a',error:null};saveStorage(st);return copy(st);}
 case 'storage.use':{const st=storageState();Object.assign(st,{available:true,path:p.default?'/Users/qa/.crateloom':p.path,source:p.default?'default':'chosen',volume:p.default?'This Mac':'SHARED SSD 2',external:!p.default});saveStorage(st);localStorage.setItem('qaStorageUse',JSON.stringify(p));return copy(st);}
 case 'storage.cancel':return copy(storageState());
 case 'storage.discard_previous':{const st=storageState();st.previous=null;saveStorage(st);return {...copy(st),freed:6.4e9};}
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
 case 'sets.cancel_scan':if('__qaSavedWarning' in window){report.warning=window.__qaSavedWarning;delete window.__qaSavedWarning;}report.scan={...report.scan,automatic:false,running:false,cancelling:false,progress:undefined};if(report.recognition.state==='running')report.recognition={...report.recognition,state:'paused'};return copy(report);
 // Recognizing a published tracklist's audio continues as the report's own audio scan.
 case 'sets.recognize':window.__qaRecognizeParams=copy(p);report.recognition={state:'running',confirmed:2,added:1,warning:null};report.provisional=HEARD;
   report.scan={offset:0,sampled_until:240,duration:3783,retry_at:0,automatic:true,running:true,stalled:false,fast:true,samples:3,pending_matches:0,progress:{stage:'Identifying tracks',completed:3,total:null,unit:'samples',position:240,duration:3783,tracks_found:2}};return copy(report);
 case 'sets.auto_scan':window.__qaAutoScanParams=copy(p);if('youtube_browser' in p)report.youtube_browser=p.youtube_browser;report.scan={...report.scan,automatic:p.enabled,running:false,progress:undefined};return copy(report);
 case 'sets.list':return [{id:'set',name:report.name,playlist_id:report.playlist_id,entries:report.tracks.length,imported:1,with_download:0,offset:null,duration:null,automatic:0,created_at:null}];
 case 'sets.get':{
   // Shazam asked the scan to wait: the engine reports when it resumes.
   if(window.__qaShazamWait){window.__qaShazamWait=false;const resume=Date.now()/1000+95;report.scan={...report.scan,sampled_until:391,automatic:true,running:false,retry_at:resume,samples:8,pending_matches:8,progress:{stage:'Waiting for Shazam',completed:8,total:null,unit:'samples',position:391,duration:6730,tracks_found:4,resume_at:resume}};}
   // A stalled attempt (here a full disk) waits too, but not for Shazam; its raw engine text stays under Details.
   if(window.__qaStallWait){window.__qaStallWait=false;window.__qaSavedWarning=report.warning;const resume=Date.now()/1000+95;report.warning="Partial scan. [Errno 28] No space left on device: '/Users/qa/Library/Caches/CrateLoom/set-audio'";report.scan={...report.scan,automatic:true,running:false,retry_at:resume,progress:{stage:'Waiting before the next attempt',completed:300,total:1800,unit:'seconds',resume_at:resume}};}
   // A row with no artist but a listed link (report.listed_link), and back.
   if(window.__qaListedRow===true){window.__qaListedRow=false;window.__qaListedArtist=report.tracks[2].artist;Object.assign(report.tracks[2],{artist:'',listed_link:true});}
   if(window.__qaListedRow==='restore'){window.__qaListedRow=false;report.tracks[2].artist=window.__qaListedArtist;delete report.tracks[2].listed_link;}
   const snapshot=copy(report);if(window.__qaStaleSetNext){window.__qaStaleSetNext=false;window.__qaStaleSetStarted=true;await new Promise(resolve=>setTimeout(resolve,400));}return snapshot;}
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
 case 'metadata.cover':{window.__qaCoverRequests=[...(window.__qaCoverRequests||[]),copy(p)];const id='cover'+count++;tasks.set(id,{cover:true,end:Date.now()+300});return {id,state:'running'};}
 case 'metadata.job':{const found=tasks.get(p.id);if(found.cover)return Date.now()<found.end?{id:p.id,state:'running',result:null,error:null}:{id:p.id,state:'completed',result:{path:'/cover-1'},error:null};if(found.scan){
   // A failure is either the raw text of an older engine or {error, error_detail}.
   if(window.__qaScanError){const failure=window.__qaScanError;return {id:p.id,state:'failed',cancellable:true,result:null,...(typeof failure==='string'?{error:failure}:failure)};}
   if(found.cancelled)return {id:p.id,state:Date.now()-found.cancelled>150?'cancelled':'cancelling',cancellable:true,result:null};
   // A published tracklist is saved at once; sources for its entries are then looked for in the background.
   if(window.__qaFinishPublished){report.watch=found.url || report.watch;report.youtube_browser=found.browser ?? null;report.playlist_id=null;delete report.scan;report.recognition=IDLE_RECOGNITION;report.provisional=[];report.discovery={pending:2,running:1};
     report.tracks.forEach((row,i)=>{row.source_search=[null,'searching','ready','choose','queued'][i];});return {id:p.id,state:'completed',cancellable:true,result:copy(report)};}
   if(window.__qaFinishScan){report.watch=found.url || report.watch;report.youtube_browser=found.browser ?? null;report.recognition=IDLE_RECOGNITION;report.discovery={pending:0,running:0};report.provisional=HEARD;report.scan={offset:300,sampled_until:360,duration:1800,retry_at:0,automatic:true,running:true,stalled:false,fast:true,samples:7,pending_matches:2,progress:{stage:'Identifying tracks',completed:7,total:null,unit:'samples',position:360,duration:1800,tracks_found:2}};return {id:p.id,state:'completed',cancellable:true,result:copy(report)};}
   return {id:p.id,state:'running',cancellable:true,progress:found.audio?{stage:'Identifying tracks',completed:7,total:null,unit:'samples',position:60,duration:3783,tracks_found:2}:{stage:'Reading playlist entries',completed:12,total:52,unit:'entries'}};
 }if(found.lookup){return {id:p.id,state:Date.now()<found.end?'running':'completed',result:{query:{title:'Low',artist:'Flo Rida,T-Pain',version:''},candidates:[{provider:'Apple Music',recording_id:'275496141',release_id:'275496110',values:{title:'Low (feat. T-Pain)',artist:'Flo Rida',album:'Mail On Sunday',genre:'Hip-Hop/Rap',date:'2007-10-09'},duration:231.4,duration_difference:1.5,match_status:'strong',reasons:[]}]}};}
const task=tasks.get(p.id);if(Date.now()<task.end)return {id:p.id,state:'running'};if(task.playback)return {id:p.id,state:'completed',result:{path:'/audio.wav'}};if(task.entry_id){const row=report.tracks.find(r=>r.entry_id===task.entry_id);row.download_id='dl'+row.entry_id;row.download_state='queued';}return {id:p.id,state:'completed',result:copy(report)};}
 case 'sets.reorder':await new Promise(resolve=>setTimeout(resolve,150));report.tracks=p.entry_ids.map((id,position)=>({...report.tracks.find(row=>row.entry_id===id),position}));return copy(report);
 case 'track.playback':case 'track.get':return copy(tracks.find(t=>t.id===p.id));
 case 'library.reveal':window.__qaReveals=[...(window.__qaReveals || []),p.id];return {revealed:true};
 case 'library.source_options':{window.__qaSourceSearches=[...(window.__qaSourceSearches || []),copy(p)];const track=tracks.find(t=>t.id===p.id);
   const option=(id,title,channel,duration,extra)=>({title,url:'https://www.youtube.com/watch?v='+id,duration,channel,verified:false,matched:true,compatible:true,current:false,music_video:false,difference:Math.round((duration-track.duration)*10)/10,...extra});
   const result={query:p.query || track.artist+' '+track.title,current:'https://www.youtube.com/watch?v=CURRENTxxx1',options:[
     option('TOPICxxxxx1','Low (feat. T-Pain)','Flo Rida',231,{topic:true,recommended:true,recommendation_reason:'Audio-only upload'}),
     option('CURRENTxxx1','Flo Rida - Low ft. T-Pain [Official Video]','Flo Rida',262,{verified:true,current:true,music_video:true}),
     option('LIVExxxxxx1','Flo Rida - Low (Live)','Flo Rida',240,{compatible:false}),
     option('OTHERxxxxx1','Something else','Nobody',200,{matched:false,compatible:false})]};
   window.__qaSourceJob=result;return {id:'source-job',state:'running',error:null,result:null};}
 case 'analysis.enqueue':window.__qaAnalysisQueued=[...(window.__qaAnalysisQueued || []),p.id];return null;
 case 'library.preview_source':window.__qaPreviews=[...(window.__qaPreviews || []),copy(p)];return {id:'preview-'+(p.url || 'current'),state:'running',error:null,result:null};
 case 'library.job':return {id:p.id,state:'completed',error:null,result:p.id.startsWith('preview-')?{preview_path:'/previews/'+p.id+'.m4a'}:p.id==='source-job'?copy(window.__qaSourceJob):{id:'acq-change',state:'queued'}};
 case 'library.change_source':window.__qaSourceChanges=[...(window.__qaSourceChanges || []),copy(p)];return {id:'change-job',state:'running',error:null,result:null};
 case 'library.replace_file':{window.__qaSourceFiles=[...(window.__qaSourceFiles || []),copy(p)];const track=tracks.find(t=>t.id===p.id);track.path=p.path;return {...copy(track),previous_deleted:true};}
 case 'cues.save':{const track=tracks.find(t=>t.id===p.id);track.cues=copy(p.cues);window.__qaCueSaves=[...(window.__qaCueSaves || []),copy(p.cues)];return copy(track);}
 case 'playlists.save':{window.__qaSaves=(window.__qaSaves||0)+1;await new Promise(resolve=>setTimeout(resolve,150));if(window.__qaFailNext){window.__qaFailNext=false;throw new Error('Save failed');}
   let playlist=playlists.find(item=>item.id===p.id);if(!playlist){playlist={id:'created'+count++,name:p.name,track_ids:[]};playlists.push(playlist);}
   playlist.name=p.name;playlist.track_ids=p.track_ids;if(playlist.id==='p')localStorage.setItem('qaOrder',JSON.stringify(p.track_ids));return copy(playlist);}
 case 'playlists.delete':{const at=playlists.findIndex(item=>item.id===p.id);if(at<0)throw new Error('Playlist not found');window.__qaPlaylistDeletes=[...(window.__qaPlaylistDeletes || []),playlists[at].name];playlists.splice(at,1);return {deleted:true};}
 case 'library.trash':{window.__qaTrashed=[...(window.__qaTrashed || []),copy(p.ids)];let removed=0;
   for(const id of p.ids){const at=tracks.findIndex(t=>t.id===id);if(at>=0){tracks.splice(at,1);removed++;}}
   playlists.forEach(item=>{item.track_ids=item.track_ids.filter(id=>!p.ids.includes(id));});return {removed,trashed:removed,not_trashed:[]};}
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
 let lifted=null;
 async function drag(handle,row){const a=await handle.boundingBox(),b=await row.boundingBox();assert.ok(a&&b);await page.mouse.move(a.x+a.width/2,a.y+a.height/2);await page.mouse.down();await page.mouse.move(b.x+Math.min(b.width/2,120),b.y+b.height/2,{steps:12});await page.waitForTimeout(60);
  // What the drag shows while the row is held: a copy following the pointer, the source dimmed in place.
  lifted=await page.evaluate(()=>{const ghost=document.querySelector('.reorder-ghost');return ghost?{text:ghost.textContent,sources:document.querySelectorAll('.reorder-source').length,pointer:getComputedStyle(ghost).pointerEvents}:null;});
  await page.mouse.up();await wait();}
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
 assert.ok(lifted && lifted.text.includes('Low (feat. T-Pain)') && lifted.sources===1 && lifted.pointer==='none','a copy of the dragged row follows the pointer: '+JSON.stringify(lifted));
 assert.equal(await page.locator('.reorder-ghost, .reorder-source').count(),0,'the drag preview is removed on drop');
 checks.dragPreview='dragging a playlist row shows a copy of it under the pointer and dims the row; both are gone after the drop';
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
 // Swapping puts each field's text in the other; swapping again restores both.
 const fieldPair=async()=>[await page.getByLabel('Metadata title',{exact:true}).inputValue(),await page.getByLabel('Metadata artist',{exact:true}).inputValue()];
 const beforeSwap=await fieldPair();const swap=page.getByRole('button',{name:'Swap title and artist',exact:true});
 await swap.click();assert.deepEqual(await fieldPair(),[beforeSwap[1],beforeSwap[0]]);
 await swap.click();assert.deepEqual(await fieldPair(),beforeSwap);
 checks.metadataSwap='Swap title and artist exchanges the two fields and swaps back';
 await watchFrame();
 await page.getByRole('button',{name:'Find matches',exact:true}).click();await page.locator('.metadata-skeleton-match').first().waitFor();await page.waitForTimeout(500);
 const candidate=page.locator('.metadata-match').first();assert.equal(await candidate.isEnabled(),true);
 assert.equal(await text(candidate.locator('.metadata-match-status')),'Close match');await candidate.click();
 assert.equal(await text(candidate.locator('.metadata-match-status')),'Selected');
 // The track had no cover, so the chosen release's cover is fetched and applied at once.
 await page.waitForFunction(()=>document.querySelector('.metadata-cover-button img')?.getAttribute('src')?.includes('/cover-1'));
 assert.equal((await page.evaluate(()=>window.__qaCoverRequests))[0].provider,'Apple Music');
 checks.metadataAutoCover='choosing a match for a track without a cover fetches that release cover (metadata.cover) and shows it';
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
 // The cover fetched with the match is in place, so the menu offers to change or remove it.
 const coverButton=page.getByRole('button',{name:'Cover art: change or remove',exact:true});await coverButton.focus();await page.keyboard.press('Enter');
 const coverMenu=page.getByRole('menu',{name:'Cover art: change or remove',exact:true});await coverMenu.waitFor();
 assert.deepEqual((await coverMenu.getByRole('menuitem').allInnerTexts()).map(item=>item.split('\n')[0].trim()),['Choose an image file…','Use the release cover','Remove cover']);
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
 await autoWrite.check();
 // The option explains itself in its tooltip; no note is added under it.
 assert.equal(await page.locator('.auto-write-note').count(),0);
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
 // In a playlist, Add music also offers tracks already in the library.
 assert.deepEqual(await page.getByRole('menuitem').locator('.menu-item-text > span').allTextContents(),await page.locator('.library[data-playlist=true]').count()?['From your library…','Files…','Folder…']:['Files…','Folder…']);
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
 const accessToggle=page.getByRole('button',{name:/^Access: \w+, change YouTube access$/});
 const cancelled=()=>page.getByText(/^Scan cancelled\./).waitFor();
 // One link row and one line of help: no recognition checkbox, and no token fields until something needs them.
 assert.equal(await page.getByRole('radio',{name:'A link',exact:true}).isChecked(),true);assert.equal(await page.locator('.set-access').count(),0);
 for(const gone of ['.set-check','.set-connections','.set-token'])assert.equal(await page.locator(gone).count(),0,gone+' should not be shown');
 assert.deepEqual(await page.locator('.set-form .set-hint').allInnerTexts(),['Reads the published tracklist; if there is none, recognizes the audio with Shazam.']);
 // (a) Share and tracking parameters leave a pasted link at once, and the scan is sent without them.
 await link.fill('https://open.spotify.com/playlist/37i9dQZF1DX4dyzvuaRJ0n?si=abc');assert.equal(await link.inputValue(),'https://open.spotify.com/playlist/37i9dQZF1DX4dyzvuaRJ0n');
 assert.equal(await accessToggle.count(),0);
 // (d) A Spotify authorization failure asks for a token right there, and only then.
 await page.evaluate(()=>window.__qaScanReject='Spotify authorization failed. Check playlist access or clear the expired token.');
 await scanButton.click();const spotifyAlert=page.getByRole('alert').filter({hasText:'Spotify authorization failed.'});await spotifyAlert.waitFor();
 assert.deepEqual(await page.evaluate(()=>{const p=window.__qaLastScanParams;return {url:p.url,fingerprint:p.fingerprint,youtube_browser:p.youtube_browser};}),{url:'https://open.spotify.com/playlist/37i9dQZF1DX4dyzvuaRJ0n',fingerprint:false,youtube_browser:null});
 const spotifyToken=page.getByLabel('Spotify access token');assert.equal(await spotifyToken.isVisible(),true);
 assert.equal(await page.getByRole('button',{name:'Connect and scan again',exact:true}).isDisabled(),true);
 await spotifyAlert.getByRole('button',{name:'Dismiss message',exact:true}).click();assert.equal(await spotifyToken.count(),0);
 // (b) The YouTube access choice belongs to a YouTube link: a toggle at the end of the link row opens it.
 await link.fill('https://www.youtube.com/watch?v=RvRhUHTV_8k&si=share');assert.equal(await link.inputValue(),'https://www.youtube.com/watch?v=RvRhUHTV_8k');
 assert.equal(await page.locator('.set-form-line').filter({has:link}).locator('.set-access-toggle').count(),1);
 assert.equal(await access.count(),0);assert.equal(await text(accessToggle),'Access: Public');assert.equal(await accessToggle.getAttribute('aria-expanded'),'false');
 await accessToggle.click();await access.waitFor();assert.equal(await access.inputValue(),'');assert.equal(await accessToggle.getAttribute('aria-expanded'),'true');
 assert.equal(await page.locator('.set-access-guide').isVisible(),true);assert.equal(await page.locator('.set-access').getAttribute('data-error'),'false');
 await wait();await page.screenshot({path:output+'/new-scan-access-dark.png'});
 await accessToggle.click();assert.equal(await page.locator('.set-access').count(),0);
 // Each way of scanning keeps its own input.
 await page.getByRole('radio',{name:'A pasted tracklist',exact:true}).check();await page.getByLabel('Tracklist',{exact:true}).waitFor();assert.equal(await link.count(),0);
 // Recognizing a pasted list's audio needs its YouTube mix link, and the hint says so.
 assert.match(await text(page.locator('.set-hint')),/Entries are used as written\. Add the YouTube mix link to recognize its audio from the report too\.$/);
 await page.getByRole('radio',{name:'A link',exact:true}).check();assert.equal(await link.inputValue(),'https://www.youtube.com/watch?v=RvRhUHTV_8k');
 await scanButton.click();await page.getByText('12 of 52 tracklist entries read',{exact:true}).waitFor();
 assert.equal(await page.evaluate(()=>window.__qaLastScanParams.fingerprint),false);
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
 // The tracklist stage stays centred in the compact light layout.
 await page.getByLabel('Appearance').selectOption('light');await page.setViewportSize({width:900,height:650});await wait();
 await scanButton.click();await page.getByText('12 of 52 tracklist entries read',{exact:true}).waitFor();
 alignment=await centered();await wait();await page.screenshot({path:output+'/tracklist-progress-compact-light.png'});assert.ok(alignment.x<2 && alignment.y<2,JSON.stringify(alignment));
 assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
 await page.getByRole('button',{name:'Cancel scan',exact:true}).click();await cancelled();
 // (e) A published tracklist opens at once; sources are found in the background and each entry says where its search stands.
 await page.evaluate(()=>window.__qaFinishPublished=true);await scanButton.click();await page.locator('.set-report').waitFor();
 const recognizeButton=page.getByRole('button',{name:'Recognize audio too',exact:true});await recognizeButton.waitFor();
 assert.deepEqual((await page.locator('.set-tracklist .set-status').allInnerTexts()).map(item=>item.trim()),['In library','Finding sources…','Source ready','Choose a version','Waiting to search']);
 assert.equal(await page.locator('.set-tracklist > li').nth(1).locator('.set-status .spin').count(),1);
 assert.equal(await text(page.getByRole('button',{name:'Create playlist and find tracks',exact:true})),'Create playlist and find tracks');
 assert.equal(await text(page.locator('.set-sources-ready')),'1 source ready · still searching');
 // An entry with no artist but a listed link is prepared from that link: it stays a ready source, not a title-only entry.
 await page.evaluate(()=>window.__qaListedRow=true);await page.waitForFunction(()=>!document.querySelectorAll('.set-tracklist > li')[2].innerText.includes('MITCH DB'));
 assert.equal(await text(page.locator('.set-sources-ready')),'1 source ready · still searching');
 assert.doesNotMatch(await text(page.locator('.set-playlist-create small')),/without an artist/);
 await page.evaluate(()=>window.__qaListedRow='restore');await page.waitForFunction(()=>document.querySelectorAll('.set-tracklist > li')[2].innerText.includes('MITCH DB'));
 await wait();await page.screenshot({path:output+'/sources-and-recognize-compact-light.png'});
 assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
 // "Recognize audio too" replaces the old checkbox: it runs as the report's audio scan and streams what it hears.
 const scanStatus=page.locator('.set-scan-status'),pauseScan=page.getByRole('button',{name:'Pause scan',exact:true}),resumeScan=page.getByRole('button',{name:'Resume audio scan',exact:true});
 await recognizeButton.click();await page.waitForFunction(()=>window.__qaRecognizeParams);
 assert.deepEqual(await page.evaluate(()=>window.__qaRecognizeParams),{id:'set',youtube_browser:null});
 await scanStatus.getByText('Recognizing audio · 2 confirmed, 1 added',{exact:true}).waitFor();assert.equal(await recognizeButton.count(),0);
 assert.match(await scanStatus.innerText(),/3 audio samples checked[\s\S]*confirmed and new entries appear below\./);
 const heardList=page.locator('.set-provisional');
 assert.match(await text(heardList),/^Heard so far \(placed when the scan finishes\)\s+05:12\s+Kavinsky – Nightcall\s+06:41\s+M83 – Midnight City\s+Heard once$/);
 assert.equal(await heardList.locator('button, a, input').count(),0);
 let pauseBounds=await pauseScan.boundingBox(),playerBounds=await page.locator('.transport').boundingBox();if(!(pauseBounds.y+pauseBounds.height<=playerBounds.y))failures.push('Pause scan (recognition) is not fully above the player at 900x650: '+JSON.stringify({pauseBounds,playerBounds}));
 await wait();await page.screenshot({path:output+'/recognize-running-compact-light.png'});
 await pauseScan.click();await resumeScan.waitFor();
 assert.match(await scanStatus.innerText(),/Audio scan paused[\s\S]*2 confirmed, 1 added so far/);
 checks.sourceSearch='a published tracklist opens at once; rows show Waiting to search, Finding sources… (spinner), Source ready and Choose a version before a playlist exists; the header counts ready sources beside an unchanged Create playlist and find tracks';
 checks.shazamWait='a scan Shazam asked to wait shows Waiting for Shazam with a countdown from progress.resume_at, says how far a sampled scan has got (not 0:00) and keeps Pause scan; a stall backoff shows the engine\'s neutral Waiting before the next attempt, and its raw OSError warning reads as one sentence with the engine text under Details';
 checks.recognize='Recognize audio too on a YouTube tracklist report sends sets.recognize {id, youtube_browser}, shows Recognizing audio with confirmed and added counts in the scan status, lists heard recordings without actions, and pauses';
 // A finished scan opens as a report whose audio scan continues by itself and can be paused there.
 await page.getByRole('button',{name:'New scan',exact:true}).click();assert.equal(await link.inputValue(),'');
 await link.fill('https://www.youtube.com/watch?v=RvRhUHTV_8k');await page.evaluate(()=>{window.__qaFinishPublished=false;});
 await scanButton.click();await page.evaluate(()=>window.__qaFinishScan=true);
 await scanStatus.getByText('Scanning audio',{exact:true}).waitFor();
 assert.match(await scanStatus.innerText(),/7 audio samples checked[\s\S]*Sampled up to 6:00 of 30:00 \xb7 2 recordings found[\s\S]*Uses public YouTube access \(pause to change\)\./);
 // An unknown total animates the bar instead of claiming a value.
 assert.equal(await scanStatus.getByRole('progressbar',{name:'Scan progress',exact:true}).getAttribute('value'),null);
 assert.equal(await page.locator('.set-provisional li').count(),2);
 // DESIGN.md: the continuation's stop control stays visible above the player. Recorded instead of thrown so the checks after it still run.
 const cancelBounds=await pauseScan.boundingBox(),footerBounds=await page.locator('.transport').boundingBox();if(!(cancelBounds.y+cancelBounds.height<=footerBounds.y))failures.push('Pause scan is not fully above the player at 900x650: '+JSON.stringify({cancelBounds,footerBounds}));
 await wait();await page.screenshot({path:output+'/audio-continuation-light.png'});
 await pauseScan.click();await resumeScan.waitFor();
 assert.match(await scanStatus.innerText(),/Audio scan paused[\s\S]*Sampled up to 06:00 of 30:00\.[\s\S]*7 audio samples saved \xb7 2 matches waiting to be placed/);
 await wait();await page.screenshot({path:output+'/audio-paused-compact-light.png'});
 // A paused scan of a YouTube set resumes with the access chosen beside the Resume button.
 const resumeAccess=page.locator('.set-resume-access').getByLabel('YouTube access for this set',{exact:true});
 assert.equal(await resumeAccess.inputValue(),'');await resumeAccess.selectOption('firefox');
 assert.match(await page.locator('.set-resume-access').innerText(),/The new choice is saved with this set when you resume\./);
 await resumeScan.click();await pauseScan.waitFor();
 assert.deepEqual(await page.evaluate(()=>window.__qaAutoScanParams),{id:'set',enabled:true,youtube_browser:'firefox'});
 assert.match(await scanStatus.innerText(),/Uses the Firefox session \(pause to change\)\./);
 // While Shazam asks to wait, the scan says so and counts down to the time it gave.
 await page.evaluate(()=>window.__qaShazamWait=true);await scanStatus.getByText('Waiting for Shazam',{exact:true}).waitFor();
 assert.match(await text(scanStatus.locator('.scan-progress-countdown')),/^Continues automatically in 1:3\d$/);
 assert.equal(await scanStatus.locator('.scan-progress').getAttribute('data-state'),'waiting');assert.equal(await pauseScan.isVisible(),true);
 // A sampled scan that waits says how far it has sampled, not the batch start ("0:00 checked"), and says the sample count once.
 {const waiting=await scanStatus.innerText();assert.match(waiting,/8 audio samples checked[\s\S]*Sampled up to 6:31 of 1:52:10 \xb7 4 recordings found/);
  assert.doesNotMatch(waiting,/0:00 of|samples saved/);}
 await pauseScan.click();await resumeScan.waitFor();
 await resumeScan.click();await pauseScan.waitFor();
 await page.evaluate(()=>window.__qaStallWait=true);await scanStatus.getByText('Waiting before the next attempt',{exact:true}).waitFor();
 assert.equal(await scanStatus.getByText('Waiting for Shazam',{exact:true}).count(),0);
 assert.match(await text(scanStatus.locator('.scan-progress-countdown')),/^Continues automatically in 1:3\d$/);
 assert.match(await scanStatus.innerText(),/Last attempt: The scan stopped because of an internal error\. Try again\./);
 assert.equal(await text(scanStatus.locator('.set-error-text')),'The scan stopped because of an internal error. Try again.');
 assert.match(await scanStatus.locator('.set-error-details code').textContent(),/^\[Errno 28\] No space left on device/);assert.equal(await scanStatus.locator('.set-error-details .disclosure-toggle').getAttribute('aria-expanded'),'false');
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
 // (b) An access failure opens the YouTube access block by itself, under the link field, with the alert inside.
 const accessAlert=page.locator('.set-access').getByRole('alert');
 await accessAlert.filter({hasText:'YouTube requires age-verified access'}).waitFor();
 assert.equal(await page.getByRole('alert').filter({hasText:'YouTube requires age-verified access'}).count(),1);
 assert.equal(await page.locator('.set-access').getAttribute('data-error'),'true');assert.equal(await accessToggle.getAttribute('aria-expanded'),'true');
 const linkBounds=await link.boundingBox(),accessBounds=await page.locator('.set-access').boundingBox();assert.ok(accessBounds.y>=linkBounds.y+linkBounds.height && accessBounds.y+accessBounds.height<=900,JSON.stringify({linkBounds,accessBounds}));
 assert.equal(await link.inputValue(),'https://www.youtube.com/watch?v=LTFCuhofnN8');
 assert.equal(await access.inputValue(),'');assert.equal(await page.evaluate(()=>window.__qaLastScanParams.youtube_browser),null);
 await access.selectOption('chrome');assert.equal(await accessAlert.count(),0);assert.equal(await text(accessToggle),'Access: Chrome');
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
 await link.fill('https://youtu.be/LTFCuhofnN8');assert.equal(await text(accessToggle),'Access: Safari');assert.equal(await access.count(),0);
 await accessToggle.click();assert.equal(await access.inputValue(),'safari');
 await scanButton.click();await page.getByRole('button',{name:'Cancel scan',exact:true}).waitFor();
 assert.equal(await page.evaluate(()=>window.__qaLastScanParams.youtube_browser),'safari');
 await page.getByRole('button',{name:'Cancel scan',exact:true}).click();await cancelled();
 await link.fill('https://open.spotify.com/playlist/37i9dQZF1DX4dyzvuaRJ0n');assert.equal(await page.locator('.set-access').count(),0);assert.equal(await accessToggle.count(),0);
 await scanButton.click();await page.getByRole('button',{name:'Cancel scan',exact:true}).waitFor();
 assert.equal(await page.evaluate(()=>window.__qaLastScanParams.youtube_browser),null);
 await page.getByRole('button',{name:'Cancel scan',exact:true}).click();await cancelled();
 // (c) Engine failures read as a sentence; the raw text is only under Details.
 const rawError='Error -3 while decompressing data: incorrect header check',restartSentence='CrateLoom was updated while it was open. Restart CrateLoom to continue.';
 await page.evaluate(raw=>window.__qaScanError=raw,rawError);await scanButton.click();
 const restart=page.getByRole('alert').filter({hasText:restartSentence});await restart.waitFor();
 assert.equal(await text(restart.locator('.set-error-text')),restartSentence);
 const rawPlaces=()=>page.evaluate(raw=>[...document.querySelectorAll('.set-workspace *')].filter(el=>[...el.childNodes].some(node=>node.nodeType===3 && node.textContent.includes(raw))).map(el=>!!el.closest('.set-error-details .disclosure-content')),rawError);
 assert.deepEqual(await rawPlaces(),[true]);
 const details=restart.getByRole('button',{name:'Details',exact:true});assert.equal(await details.getAttribute('aria-expanded'),'false');
 await details.click();await restart.getByText(rawError,{exact:true}).waitFor({state:'visible'});
 await wait();await page.screenshot({path:output+'/scan-error-details-dark.png'});
 await restart.getByRole('button',{name:'Dismiss message',exact:true}).click();
 // A newer engine sends its own sentence with error_detail; the detail is offered the same way.
 await page.evaluate(()=>window.__qaScanError={error:'A file or network operation failed. Try again.',error_detail:'OSError: [Errno 5] Input/output error'});await scanButton.click();
 const plainAlert=page.getByRole('alert').filter({hasText:'A file or network operation failed. Try again.'});await plainAlert.waitFor();
 const plainDetails=plainAlert.getByRole('button',{name:'Details',exact:true});await plainDetails.click();assert.equal(await plainDetails.getAttribute('aria-expanded'),'true');
 await plainAlert.locator('code').waitFor({state:'visible'});assert.equal(await plainAlert.locator('code').textContent(),'OSError: [Errno 5] Input/output error');
 await page.evaluate(()=>window.__qaScanError=null);await plainAlert.getByRole('button',{name:'Dismiss message',exact:true}).click();
 checks.scanErrors='raw engine text (zlib) shows the restart sentence with the original only under Details; an engine sentence with error_detail shows that detail under Details';
 checks.trackingParams='si and other share parameters are removed from a filled link and from the scan request';
 checks.tokensOnDemand='no token fields or Advanced disclosure on the form; a Spotify authorization error reveals the Spotify token field beside it';
 checks.youtubeAccess='access toggle (Access: Public) only for YouTube links, at the end of the link row, reveals the access block and guide; public is the default; an access error from the scan task or from sets.scan itself opens the block by itself with the alert inside; the chosen browser is sent with the next scan, survives a reload, is named on the toggle and is not sent with other links; a paused YouTube set resumes with the access chosen there';
 checks.setScanning='one helper line, scan-from modes keep their input, links are sent with fingerprint:false, tracklist progress centered on desktop and compact layouts, initial scan cancellation, input preservation, honest unknown totals in the report scan status, streamed hits, automatic continuation pause, saved samples and Resume, a scan finishing elsewhere keeps the current view';

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

 // Sorting, column resizing, the row context menu, and the end of a track whose media runs longer than its real length.
 {
  // The fixture library comes back whole on reload: t0 101 BPM 8A, t1 124.5 11B, t2 128 no key, t3 no tempo 2A, t4 123 10B.
  await page.reload();await page.getByRole('button',{name:'All Tracks',exact:true}).click();await wait();
  const head=page.locator('.table-head'),sortButton=name=>head.getByRole('button',{name:new RegExp('^'+name+'[.,]')});
  const unsorted=await order();
  assert.deepEqual(unsorted,['Low (feat. T-Pain)','The Days (Notion Remix)','Don’t Stop the Music','Starships','One More Time']);
  await sortButton('BPM').click();await wait();
  assert.deepEqual(await order(),['Low (feat. T-Pain)','One More Time','The Days (Notion Remix)','Don’t Stop the Music','Starships']);
  assert.equal(await sortButton('BPM').getAttribute('aria-label'),'BPM, sorted ascending. Sort by tempo: descending');
  await sortButton('BPM').click();await wait();
  assert.deepEqual(await order(),['Don’t Stop the Music','The Days (Notion Remix)','One More Time','Low (feat. T-Pain)','Starships'],'descending keeps tracks without a tempo last');
  await sortButton('BPM').click();await wait();assert.deepEqual(await order(),unsorted);
  assert.deepEqual(await page.locator('.track-row .track-key').allTextContents(),['8A','11B','—','2A','10B']);
  await sortButton('Key').click();await wait();
  const byKey=['Starships','Low (feat. T-Pain)','One More Time','The Days (Notion Remix)','Don’t Stop the Music'];
  assert.deepEqual(await order(),byKey);
  await page.reload();await page.getByRole('button',{name:'All Tracks',exact:true}).click();await wait();
  assert.deepEqual(await order(),byKey,'the sort survives a reload');
  // Sorted playlists cannot be reordered by hand.
  await page.locator('.playlist-nav .nav').first().click();await wait();
  assert.equal(await page.locator('.track-row .reorder-handle').first().isDisabled(),true);
  assert.equal(await page.locator('.track-row .reorder-handle').first().getAttribute('title'),'Clear the sort to reorder');
  await sortButton('Key').click();await wait();await sortButton('Key').click();await wait();
  assert.equal(await page.locator('.track-row .reorder-handle').first().isDisabled(),false);
  await page.getByRole('button',{name:'All Tracks',exact:true}).click();await wait();
  assert.deepEqual(await order(),unsorted);
  checks.sorting='BPM ascending, descending (no tempo last), then back to library order; Key sorts by Camelot; the sort persists; sorted playlists lock reordering';

  const artist=head.locator('.column-artist'),resizer=head.getByRole('separator',{name:'Resize Artist column',exact:true});
  const width=()=>artist.evaluate(el=>Math.round(el.getBoundingClientRect().width));
  const before=await width(),grip=await resizer.boundingBox();
  await page.mouse.move(grip.x+grip.width/2,grip.y+grip.height/2);await page.mouse.down();
  await page.mouse.move(grip.x+grip.width/2+80,grip.y+grip.height/2,{steps:8});await page.mouse.up();await wait();
  const wider=await width();assert.ok(Math.abs(wider-before-80)<=2,`artist column grew by ${wider-before}px`);
  assert.equal(await page.locator('.track-row .track-artist').first().evaluate(el=>Math.round(el.getBoundingClientRect().width)),wider,'rows follow the header');
  await page.reload();await page.getByRole('button',{name:'All Tracks',exact:true}).click();await wait();assert.equal(await width(),wider,'the width survives a reload');
  await resizer.focus();await page.keyboard.press('ArrowRight');await wait();assert.equal(await width(),wider+8);
  await resizer.dblclick();await wait();assert.ok(Math.abs(await width()-before)<=1,'double-click restores the default width');
  // The handle between Title and Artist belongs to Title: dragging it right widens Title, and Artist keeps its place after it.
  const title=head.locator('.select-visible'),titleWidth=()=>title.evaluate(el=>Math.round(el.getBoundingClientRect().width));
  const titleGrip=await head.getByRole('separator',{name:'Resize Title column',exact:true}).boundingBox(),titleBefore=await titleWidth(),artistLeft=await artist.evaluate(el=>Math.round(el.getBoundingClientRect().left));
  assert.ok(Math.abs(titleGrip.x+titleGrip.width/2-(await title.evaluate(el=>el.getBoundingClientRect().right))-8)<=2,'the Title handle sits on the gap right of Title');
  await page.mouse.move(titleGrip.x+titleGrip.width/2,titleGrip.y+titleGrip.height/2);await page.mouse.down();
  await page.mouse.move(titleGrip.x+titleGrip.width/2+60,titleGrip.y+titleGrip.height/2,{steps:8});await page.mouse.up();await wait();
  assert.ok(Math.abs(await titleWidth()-titleBefore-60)<=2,`title column grew by ${await titleWidth()-titleBefore}px`);
  assert.ok(Math.abs(await artist.evaluate(el=>Math.round(el.getBoundingClientRect().left))-artistLeft-60)<=2,'artist moves along with the title edge');
  await head.getByRole('separator',{name:'Resize Title column',exact:true}).dblclick();await wait();assert.ok(Math.abs(await titleWidth()-titleBefore)<=1);
  checks.columnResize='each handle sits on its column\'s right edge: dragging Artist\'s or Title\'s widens that column by the drag distance, rows follow, persists, arrow keys step 8px, double-click resets';

  // # is the list's own order and sorts like the other columns.
  assert.deepEqual(await page.locator('.track-row .track-order').allTextContents(),['1','2','3','4','5']);
  await sortButton('#').click();await wait();assert.deepEqual(await order(),unsorted);
  await sortButton('#').click();await wait();assert.deepEqual(await order(),[...unsorted].reverse());
  assert.deepEqual(await page.locator('.track-row .track-order').allTextContents(),['5','4','3','2','1'],'each row keeps its own number');
  await sortButton('#').click();await wait();assert.deepEqual(await order(),unsorted);
  checks.numberColumn='# shows each track\'s place in the list, sorts ascending and descending, then back';

  // The window itself never scrolls or rubber-bands; only the lists inside it do.
  assert.deepEqual(await page.evaluate(()=>[getComputedStyle(document.documentElement).overflow,getComputedStyle(document.body).overflow,getComputedStyle(document.body).overscrollBehaviorY,document.scrollingElement.scrollHeight<=window.innerHeight]),['hidden','hidden','none',true]);
  checks.windowScroll='html and body do not scroll and have overscroll-behavior none';

  const firstRow=page.locator('.track-row').first(),menu=page.getByRole('menu',{name:'Actions for Low (feat. T-Pain)',exact:true});
  const menuItems=async panel=>(await panel.locator(':scope > [role=menuitem]').allTextContents()).map(text=>text.trim());
  await firstRow.locator('.track-artist').click({button:'right'});await menu.waitFor();
  assert.deepEqual(await menuItems(menu),['Play','Edit cues','Edit metadata…','Add to playlist','Copy','Change source…','Reveal in Finder','Remove from library…','Delete…']);
  assert.equal(await page.evaluate(()=>document.activeElement?.textContent),'Play');
  assert.equal(await page.evaluate(()=>document.querySelector('[role=menu]').parentElement===document.body),true,'the menu is drawn on top of the whole window');
  // Copy is a submenu: hovering opens it beside the item.
  await menu.getByRole('menuitem',{name:'Copy',exact:true}).hover();
  const copyMenu=page.getByRole('menu',{name:'Copy',exact:true});await copyMenu.waitFor();
  assert.deepEqual(await menuItems(copyMenu),['Title','Artist','Artist and title']);
  await copyMenu.getByRole('menuitem',{name:'Artist',exact:true}).click();await menu.waitFor({state:'detached'});
  assert.equal(await page.evaluate(()=>window.__qaClipboard),'Flo Rida, T-Pain');
  await firstRow.click({button:'right',position:{x:500,y:20}});await menu.getByRole('menuitem',{name:'Copy',exact:true}).hover();
  await copyMenu.getByRole('menuitem',{name:'Artist and title',exact:true}).click();
  assert.equal(await page.evaluate(()=>window.__qaClipboard),'Flo Rida, T-Pain - Low (feat. T-Pain)');
  // The keyboard reaches the submenu too: Right opens it with its first item focused, Left goes back.
  await firstRow.click({button:'right',position:{x:500,y:20}});await menu.waitFor();
  await page.keyboard.press('ArrowDown');assert.equal(await page.evaluate(()=>document.activeElement?.textContent),'Edit cues');
  await menu.getByRole('menuitem',{name:'Copy',exact:true}).focus();await page.keyboard.press('ArrowRight');await copyMenu.waitFor();
  assert.equal(await page.evaluate(()=>document.activeElement?.textContent),'Title');
  await page.keyboard.press('ArrowLeft');await copyMenu.waitFor({state:'detached'});assert.equal(await page.evaluate(()=>document.activeElement?.textContent),'Copy');
  await page.keyboard.press('ArrowRight');await page.keyboard.press('Enter');await menu.waitFor({state:'detached'});
  assert.equal(await page.evaluate(()=>window.__qaClipboard),'Low (feat. T-Pain)');
  // Add to playlist lists every playlist; one that already has the track is marked Added.
  await firstRow.click({button:'right',position:{x:500,y:20}});await menu.getByRole('menuitem',{name:'Add to playlist',exact:true}).click();
  const addMenu=page.getByRole('menu',{name:'Add to playlist',exact:true});await addMenu.waitFor();
  assert.deepEqual(await menuItems(addMenu),['Friday selectionsAdded','New playlist']);
  assert.equal(await addMenu.getByRole('menuitem',{name:/^Friday selections/}).isDisabled(),true);
  await page.keyboard.press('Escape');await addMenu.waitFor({state:'detached'});await page.keyboard.press('Escape');await menu.waitFor({state:'detached'});
  await firstRow.click({button:'right',position:{x:500,y:20}});await menu.getByRole('menuitem',{name:'Reveal in Finder',exact:true}).click();
  assert.deepEqual(await page.evaluate(()=>window.__qaReveals),['t0']);
  // The ⋯ button on a row opens the same menu.
  await firstRow.hover();await firstRow.getByRole('button',{name:'More actions for Low (feat. T-Pain)',exact:true}).click();await menu.waitFor();
  await page.keyboard.press('Escape');await menu.waitFor({state:'detached'});
  assert.equal(await page.evaluate(()=>document.activeElement?.getAttribute('aria-label')),'More actions for Low (feat. T-Pain)','focus returns to the ⋯ button');
  checks.contextMenu='right-click or the row ⋯ button opens Play, Edit cues, Edit metadata, Add to playlist ›, Copy ›, Change source, Reveal in Finder, Remove from library and Delete, drawn above the whole window; submenus open on hover, click or Right and close with Left; copies the text, calls library.reveal, Escape closes and restores focus';

  // Change source: other versions with the original audio first, a pasted link, or a file.
  const changeDialog=page.getByRole('dialog',{name:'Change source',exact:true});
  await firstRow.click({button:'right',position:{x:500,y:20}});await menu.getByRole('menuitem',{name:'Change source…',exact:true}).click();
  await changeDialog.waitFor();await changeDialog.getByRole('list',{name:'Versions of this track',exact:true}).waitFor();
  assert.deepEqual(await page.evaluate(()=>window.__qaSourceSearches),[{id:'t0'}]);
  assert.equal(await changeDialog.getByLabel('Search for versions',{exact:true}).inputValue(),'Flo Rida, T-Pain Low (feat. T-Pain)');
  const versionRows=changeDialog.getByRole('list',{name:'Versions of this track',exact:true}).getByRole('listitem');
  assert.equal(await versionRows.count(),3);
  assert.match(await versionRows.first().innerText(),/Low \(feat\. T-Pain\)[\s\S]*Flo Rida · 3:51 · 3:43 longer than now[\s\S]*Audio-only upload/);
  assert.match(await versionRows.nth(1).innerText(),/Current source[\s\S]*Music video/);
  assert.equal(await versionRows.nth(1).getByRole('button',{name:/^Use /}).isDisabled(),true,'the source in use cannot be chosen again');
  assert.match(await versionRows.nth(2).innerText(),/Different version/);
  assert.equal(await changeDialog.getByRole('list',{name:'Other results',exact:true}).getByRole('listitem').count(),1);
  // The track is shown as labelled fields; no long explanation.
  assert.match(await changeDialog.innerText(),/Title\s*Low \(feat\. T-Pain\)\s*Artist\s*Flo Rida, T-Pain\s*Length\s*\d+:\d\d/i);
  assert.doesNotMatch(await changeDialog.innerText(),/cues and playlists stay|measured again/);
  // Listen first: the current audio from the preview bar, then another version from its row.
  const previewBar=changeDialog.getByRole('group',{name:'Preview',exact:true});
  await previewBar.getByRole('button',{name:'Play preview',exact:true}).click();
  await previewBar.getByRole('button',{name:'Pause preview',exact:true}).waitFor();
  assert.deepEqual(await page.evaluate(()=>window.__qaPreviews),[{id:'t0'}]);
  assert.match(await previewBar.innerText(),/Current audio/);
  await versionRows.first().getByRole('button',{name:'Play Low (feat. T-Pain)',exact:true}).click();
  await versionRows.first().getByRole('button',{name:'Pause Low (feat. T-Pain)',exact:true}).waitFor();
  assert.deepEqual((await page.evaluate(()=>window.__qaPreviews)).at(-1),{id:'t0',url:'https://www.youtube.com/watch?v=TOPICxxxxx1'});
  assert.match(await previewBar.innerText(),/Low \(feat\. T-Pain\)/);
  await previewBar.getByLabel('Preview position',{exact:true}).fill('4');
  await versionRows.first().getByRole('button',{name:'Pause Low (feat. T-Pain)',exact:true}).click();
  await previewBar.getByRole('button',{name:'Play preview',exact:true}).waitFor();
  // The source in use plays the track's own file, without another download.
  await versionRows.nth(1).getByRole('button',{name:/^Play /}).click();await previewBar.getByRole('button',{name:'Pause preview',exact:true}).waitFor();
  assert.equal((await page.evaluate(()=>window.__qaPreviews)).length,2,'the current audio is fetched once');
  await previewBar.getByRole('button',{name:'Pause preview',exact:true}).click();
  checks.changeSourcePreview='Change source shows title, artist and length as fields without the long note; the preview bar plays the current audio, each row plays its version (library.preview_source with its link), the bar shows what is playing and scrubs, and the current source reuses the fetched file';
  await wait();await page.screenshot({path:output+'/change-source.png'});
  // Your own words search again.
  await changeDialog.getByLabel('Search for versions',{exact:true}).fill('low flo rida slowed');await changeDialog.getByRole('button',{name:'Search',exact:true}).click();await wait();
  assert.deepEqual((await page.evaluate(()=>window.__qaSourceSearches)).at(-1),{id:'t0',query:'low flo rida slowed'});
  await versionRows.first().getByRole('button',{name:'Use Low (feat. T-Pain)',exact:true}).click();
  await changeDialog.getByText('Downloading the new version.',{exact:true}).waitFor();
  assert.deepEqual(await page.evaluate(()=>window.__qaSourceChanges),[{id:'t0',url:'https://www.youtube.com/watch?v=TOPICxxxxx1'}]);
  await changeDialog.getByRole('button',{name:'Done',exact:true}).click();await changeDialog.waitFor({state:'detached'});
  // A pasted link.
  await firstRow.click({button:'right',position:{x:500,y:20}});await menu.getByRole('menuitem',{name:'Change source…',exact:true}).click();await changeDialog.waitFor();
  await changeDialog.getByLabel('Or paste a link',{exact:true}).fill('https://soundcloud.com/artist/low-edit');await changeDialog.getByRole('button',{name:'Use link',exact:true}).click();
  await changeDialog.getByText('Downloading the new version.',{exact:true}).waitFor();
  assert.deepEqual((await page.evaluate(()=>window.__qaSourceChanges)).at(-1),{id:'t0',url:'https://soundcloud.com/artist/low-edit'});
  await page.keyboard.press('Escape');await changeDialog.waitFor({state:'detached'});
  // A file on this Mac replaces the audio at once.
  await page.evaluate(()=>{window.__qaOpenPath='/music/Low (album version).wav';});
  await firstRow.click({button:'right',position:{x:500,y:20}});await menu.getByRole('menuitem',{name:'Change source…',exact:true}).click();await changeDialog.waitFor();
  await changeDialog.getByRole('button',{name:'Choose a file on this Mac…',exact:true}).click();await changeDialog.waitFor({state:'detached'});
  assert.deepEqual(await page.evaluate(()=>window.__qaSourceFiles),[{id:'t0',path:'/music/Low (album version).wav'}]);
  await page.getByText(/^Audio replaced\. Tempo, key and beat grid are being measured again; the previous download was deleted\.$/).waitFor();
  // The new audio is measured again.
  await page.waitForFunction(()=>(window.__qaAnalysisQueued || []).includes('t0'));
  await page.evaluate(()=>{window.__qaOpenPath=null;});
  checks.changeSource='Change source lists versions with the audio-only upload recommended, the current source locked, music videos and other versions marked; searches your own words, downloads a chosen version or pasted link, and replaces the audio from a file, then queues its analysis';

  // The media is 8 s long but the track is 2 s: playback stops at 0:02 and moves on.
  await page.evaluate(()=>window.__qaSetDuration('t0',2));
  await page.getByRole('button',{name:'Play Low (feat. T-Pain)',exact:true}).click();
  await page.locator('.player-title').getByText('The Days (Notion Remix)',{exact:true}).waitFor({timeout:6000});
  checks.trackEnd='a track whose media runs past its measured length ends at that length and the next track starts';
  await page.evaluate(()=>window.__qaSetDuration('t0',8));
 }

 // Playlists: add from the library through Add music, from a track's menu, and the player bar's menu.
 {
  await page.reload();await page.getByRole('button',{name:'All Tracks',exact:true}).click();await wait();
  // The Downloads button is an icon with its name kept for assistive technology.
  const downloadsButton=page.getByRole('button',{name:'Downloads',exact:true});
  assert.equal((await downloadsButton.innerText()).trim(),'');assert.equal(await downloadsButton.getAttribute('title'),'Downloads');
  await page.getByLabel('New playlist name',{exact:true}).fill('Warm up');await page.getByRole('button',{name:'Create playlist',exact:true}).click();
  await page.locator('.library[data-playlist=true]').waitFor();await page.keyboard.press('Escape');await wait();
  await page.getByRole('button',{name:'Add music',exact:true}).click();
  assert.deepEqual((await page.getByRole('menu').last().getByRole('menuitem').allInnerTexts()).map(text=>text.split('\n')[0].trim()),['From your library…','Files…','Folder…']);
  await page.getByRole('menuitem',{name:/^From your library/}).click();
  const picker=page.getByRole('dialog',{name:'Add to “Warm up”',exact:true});await picker.waitFor();
  assert.equal(await picker.getByRole('listitem').count(),5);
  await picker.getByLabel('Search your library',{exact:true}).fill('days');
  assert.equal(await picker.getByRole('listitem').count(),1);
  await picker.getByLabel('Select all shown',{exact:true}).check();
  await picker.getByLabel('Search your library',{exact:true}).fill('');
  await picker.getByRole('checkbox',{name:'Add Low (feat. T-Pain)',exact:true}).check();
  await picker.getByRole('button',{name:'Add 2 tracks',exact:true}).click();await picker.waitFor({state:'detached'});
  await page.getByText('Added 2 tracks to “Warm up”.',{exact:true}).waitFor();
  assert.deepEqual(await page.locator('.track-row .track-name strong').allTextContents(),['Low (feat. T-Pain)','The Days (Notion Remix)'],'library order is kept');
  // In a playlist, the menu can also take the track out of it.
  const warmRow=page.locator('.track-row').first(),warmMenu=page.getByRole('menu',{name:'Actions for Low (feat. T-Pain)',exact:true});
  await warmRow.click({button:'right',position:{x:500,y:20}});await warmMenu.waitFor();
  await warmMenu.getByRole('menuitem',{name:'Remove from “Warm up”',exact:true}).click();await wait();
  assert.deepEqual(await page.locator('.track-row .track-name strong').allTextContents(),['The Days (Notion Remix)']);
  // From All Tracks, a track's menu adds it to another playlist.
  await page.getByRole('button',{name:'All Tracks',exact:true}).click();await wait();
  const row=page.locator('.track-row').nth(2),title=(await row.locator('.track-name strong').textContent()).trim();
  const rowMenu=page.getByRole('menu',{name:`Actions for ${title}`,exact:true});
  await row.click({button:'right',position:{x:500,y:20}});await rowMenu.getByRole('menuitem',{name:'Add to playlist',exact:true}).hover();
  await page.getByRole('menu',{name:'Add to playlist',exact:true}).getByRole('menuitem',{name:/^Warm up/}).click();
  await page.getByText(`Added 1 track to “Warm up”.`,{exact:true}).waitFor();
  assert.match(await page.locator('.playlist-nav .nav',{hasText:'Warm up'}).innerText(),/Warm up\s*2/);
  checks.addToPlaylist='a playlist\'s Add music offers From your library, Files and Folder; the picker searches, selects all shown, adds in library order and skips tracks already there; a track\'s menu adds it to another playlist and, inside a playlist, removes it from that playlist';

  // The player bar: right-click on the track or its ⋯ button opens the same menu, above the bar.
  await page.evaluate(()=>window.__qaSetWave('t2'));
  await row.locator('.track-name').click();await wait();
  // The track in the player shows its waveform with sections under its row; clicking it plays from there.
  const rowWave=page.getByRole('slider',{name:`Waveform of ${title}`,exact:true});await rowWave.waitFor();
  assert.equal(await page.locator('.track-row-wave').count(),1);
  assert.equal(await page.evaluate(()=>document.querySelector('.track-row-wave').previousElementSibling.querySelector('.track-name strong').textContent),title);
  assert.deepEqual(await rowWave.locator('.section-band').allTextContents(),['Intro','Up']);
  await page.waitForFunction(()=>document.querySelector('.track-row-wave canvas')?.width>0);
  await wait();await page.screenshot({path:output+'/row-wave.png'});
  const waveBox=await rowWave.boundingBox();await rowWave.click({position:{x:waveBox.width*.5,y:12}});
  await page.locator('footer .play[aria-label=Pause]').waitFor();
  assert.ok(await page.evaluate(()=>Number(document.querySelector('.track-row-wave .overview').getAttribute('aria-valuenow'))>=3),'clicking the waveform seeks there');
  await page.locator('footer .play').click();
  checks.rowWave='the track in the player shows its waveform, sections and playhead under its row; clicking it seeks there and plays';
  const nowArea=page.locator('.now-area'),playerMenu=page.getByRole('menu',{name:`Actions for ${title}`,exact:true});
  await nowArea.click({button:'right'});await playerMenu.waitFor();
  assert.equal((await playerMenu.locator(':scope > [role=menuitem]').first().textContent()).trim(),'Play');
  const covered=await page.evaluate(()=>{const menu=document.querySelector('.context-menu');const box=menu.getBoundingClientRect();const footer=document.querySelector('footer.transport').getBoundingClientRect();
    const points=[[box.left+box.width/2,box.bottom-6],[box.left+box.width/2,box.top+6]];return {inside:points.every(([x,y])=>menu.contains(document.elementFromPoint(x,y))),overlapsFooter:box.bottom>footer.top};});
  assert.equal(covered.inside,true,'nothing covers the menu, not even the player bar');
  await page.keyboard.press('Escape');await playerMenu.waitFor({state:'detached'});
  await page.getByRole('button',{name:`More actions for ${title}`,exact:true}).last().click();await playerMenu.waitFor();
  const placed=await page.evaluate(()=>{const box=document.querySelector('.context-menu').getBoundingClientRect(),button=document.querySelector('.now-more').getBoundingClientRect();return box.bottom<=button.top+1&&box.top>=0;});
  assert.equal(placed,true,'the ⋯ menu opens upward from its button and stays in the window');
  await wait();await page.screenshot({path:output+'/player-menu.png'});
  // Delete moves the file to the Trash after asking.
  await playerMenu.getByRole('menuitem',{name:'Delete…',exact:true}).click();
  const confirmDelete=page.getByRole('alertdialog',{name:`Delete “${title}”?`,exact:true});await confirmDelete.waitFor();
  assert.match(await confirmDelete.innerText(),/moved to the Trash, and the track is removed from CrateLoom[\s\S]*put the file back from the Trash/);
  await wait();await page.screenshot({path:output+'/delete-confirm.png'});
  await confirmDelete.getByRole('button',{name:'Move to Trash',exact:true}).click();await confirmDelete.waitFor({state:'detached'});
  await page.getByText('Moved 1 file to the Trash and removed the track from the library.',{exact:true}).waitFor();
  assert.equal(await page.locator('.track-row').count(),4);
  assert.equal((await page.locator('.player-title').textContent()).trim(),'No track selected');
  checks.playerMenu='right-click on the player\'s track or its ⋯ button opens the track menu above the player bar; Delete asks, then moves the file to the Trash, removes the track and clears the player';
 }

 // Library location: shows what the library holds, moves it to a chosen folder, then restarts on it.
 {
  await page.evaluate(()=>{window.__qaOpenPath='/Volumes/SHARED SSD 2';});
  await page.getByRole('button',{name:'Library location…',exact:true}).click();
  const storageDialog=page.getByRole('dialog',{name:'Library location',exact:true});await storageDialog.waitFor();
  await storageDialog.getByText('/Users/qa/.crateloom',{exact:true}).waitFor();
  assert.match(await storageDialog.innerText(),/6\.4 GB in 812 files · 21 GB free on this drive/);
  assert.deepEqual((await storageDialog.getByRole('list',{name:'What the library holds',exact:true}).getByRole('listitem').allInnerTexts()).map(text=>text.trim().replace(/\s*\n\s*/,'\n')),['Downloads\n4.1 GB','DJ set audio\n1.9 GB','Analysis\n320 MB','Library and other files\n80 MB']);
  assert.equal(await storageDialog.getByRole('button',{name:'Move back to this Mac',exact:true}).count(),0,'the default folder offers no move back');
  await storageDialog.getByRole('button',{name:'Choose a folder…',exact:true}).click();
  const planCard=storageDialog.getByRole('group',{name:'New library location',exact:true});await planCard.waitFor();
  assert.match(await planCard.innerText(),/\/Volumes\/SHARED SSD 2\/CrateLoom Library[\s\S]*copies 812 files \(6\.4 GB\) to “SHARED SSD 2”, which has 310 GB free/);
  await wait();await page.screenshot({path:output+'/storage-plan.png'});
  await planCard.getByRole('button',{name:'Move library',exact:true}).click();
  await storageDialog.getByRole('progressbar',{name:'Moving the library',exact:true}).waitFor();
  assert.equal(await storageDialog.getByRole('button',{name:'Close',exact:true}).isDisabled(),true,'the window stays open while moving');
  assert.deepEqual(await page.evaluate(()=>window.__qaStorageMoves),[{path:'/Volumes/SHARED SSD 2'}]);
  // The move finishes, the engine restarts and the window reloads on the new library, saying so once.
  await page.getByText(/^Library moved to \/Volumes\/SHARED SSD 2\/CrateLoom Library\. The earlier copy is still on this Mac; delete it in Library location to free 6\.4 GB\.$/).waitFor({timeout:8000});
  assert.equal(await page.evaluate(()=>localStorage.getItem('qaRestarts')),'1');
  await page.getByRole('button',{name:'Library location…',exact:true}).click();await storageDialog.waitFor();
  await storageDialog.getByText('SHARED SSD 2',{exact:true}).waitFor();
  await storageDialog.getByRole('button',{name:'Move back to this Mac',exact:true}).waitFor();
  await storageDialog.getByRole('button',{name:'Delete earlier copy…',exact:true}).click();
  const discard=page.getByRole('alertdialog',{name:'Delete the earlier copy?',exact:true});await discard.waitFor();
  assert.match(await discard.innerText(),/permanently deletes \/Users\/qa\/\.crateloom \(6\.4 GB\)\. It cannot be undone/);
  await discard.getByRole('button',{name:'Delete',exact:true}).click();await discard.waitFor({state:'detached'});
  await storageDialog.getByText('Deleted the earlier copy and freed 6.4 GB.',{exact:true}).waitFor();
  assert.equal(await storageDialog.getByRole('button',{name:'Delete earlier copy…',exact:true}).count(),0);
  // A folder that already has a library is used as it is.
  await page.evaluate(()=>{window.__qaOpenPath='/Volumes/SHARED SSD 2/Old';});
  await storageDialog.getByRole('button',{name:'Choose a folder…',exact:true}).click();await planCard.waitFor();
  assert.match(await planCard.innerText(),/already has a CrateLoom library with 42 tracks/);
  await planCard.getByRole('button',{name:'Cancel',exact:true}).click();
  await page.keyboard.press('Escape');await storageDialog.waitFor({state:'detached'});
  checks.storageLocation='Library location shows the folder, drive, size by kind and free space; choosing a folder plans the move with sizes, moving shows progress and locks the window, then the engine restarts and the window reloads with a one-time notice; the earlier copy can be deleted after confirming; a folder with a library is offered as-is';

  // With the drive disconnected, the app explains which drive to connect instead of opening an empty library.
  await page.evaluate(()=>{const st=JSON.parse(localStorage.getItem('qaStorage'));st.available=false;st.usage=null;localStorage.setItem('qaStorage',JSON.stringify(st));window.__qaOpenPath=null;});
  await page.reload();
  await page.getByRole('heading',{name:'Connect “SHARED SSD 2”',exact:true}).waitFor();
  assert.equal(await page.locator('.track-row').count(),0);
  await wait();await page.screenshot({path:output+'/storage-missing.png'});
  await page.evaluate(()=>{const st=JSON.parse(localStorage.getItem('qaStorage'));st.available=true;localStorage.setItem('qaStorage',JSON.stringify(st));});
  await page.getByRole('button',{name:'Try again',exact:true}).click();
  await page.locator('.track-row').first().waitFor();
  assert.equal(await page.evaluate(()=>localStorage.getItem('qaRestarts')),'2');
  checks.storageMissing='a library on a disconnected drive shows Connect “drive” with Try again, Choose another library and Use this Mac; Try again restarts the engine and opens the library once it is back';
  await page.evaluate(()=>{localStorage.removeItem('qaStorage');localStorage.removeItem('qaRestarts');});
 }

 checks.viewTransitions=viewTransitions;
 checks.unhandledEngineCalls=await page.evaluate(()=>JSON.parse(localStorage.getItem('qaUnhandled') || '[]'));assert.deepEqual(checks.unhandledEngineCalls,[]);
 checks.errors=errors;assert.deepEqual(errors,[]);checks.failures=failures;fs.writeFileSync(output+'/checks.json',JSON.stringify(checks,null,2));console.log(JSON.stringify(checks,null,2));await browser.close();
 assert.deepEqual(failures,[]);
})().catch(e=>{console.error(e);process.exit(1);});
