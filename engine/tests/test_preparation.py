import base64,json,os,subprocess,sys,tempfile,unittest
from pathlib import Path

WRITER=os.environ.get('CRATELOOM_METADATA_PYTHON')
class Preparation(unittest.TestCase):
 def test_playlists_staging_and_restart(self):
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp);env={**os.environ,'CRATELOOM_DATA':str(root/'data'),'PYTHONPATH':str(Path(__file__).resolve().parents[1])}
   def request(method,params={}):
    r=subprocess.run([sys.executable,'-m','crateloom'],input=json.dumps({'protocol_version':1,'request_id':'t','method':method,'params':params})+'\n',text=True,capture_output=True,env=env,check=True);out=json.loads(r.stdout);self.assertNotIn('error',out);return out['result']
   ids=[]
   for frequency in (440,880):
    audio=root/f'{frequency}.wav';subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i',f'sine=frequency={frequency}:duration=1','-y',str(audio)],check=True);ids.append(request('library.import',{'path':str(audio)})['id'])
   playlist=request('playlists.save',{'name':'Set one','track_ids':ids})
   self.assertEqual(request('playlists.list')[0]['track_ids'],ids)
   request('playlists.save',{'id':playlist['id'],'name':'Renamed','track_ids':ids[::-1]})
   self.assertEqual(request('playlists.list')[0]['track_ids'],ids[::-1])
   request('playlists.save',{'id':playlist['id'],'name':'Renamed','track_ids':[ids[0]]})
   self.assertEqual(len(request('library.list')),2)
   path=root/'440.wav';original=path.read_bytes();request('metadata.save',{'id':ids[0],'values':{'title':'New title','artist':'New artist'}})
   self.assertEqual(path.read_bytes(),original);self.assertEqual(request('metadata.get',{'id':ids[0]})['pending']['title'],'New title')
   self.assertEqual(request('library.list')[0]['metadata'],None)
   request('metadata.save',{'id':ids[0],'values':{},'source':{'provider':'MusicBrainz','recording_id':'old-recording','release_id':'old-release'}})
   self.assertEqual(request('metadata.get',{'id':ids[0]})['source']['release_id'],'old-release')
   request('metadata.save',{'id':ids[0],'values':{},'source':None})
   self.assertIsNone(request('metadata.get',{'id':ids[0]})['source'])
   self.assertEqual(path.read_bytes(),original)
   self.assertEqual(request('track.get',{'id':ids[0]})['title'],'New title')
   reset=request('metadata.reload',{'id':ids[0]});self.assertEqual(reset['pending'],{});self.assertEqual(reset['values']['title'],'440')

 @unittest.skipUnless(WRITER and Path(WRITER).is_file(),'Set CRATELOOM_METADATA_PYTHON')
 def test_verified_tag_writes_keep_audio_comments_cues_and_backups(self):
  for extension,codec in [('mp3','libmp3lame'),('flac','flac'),('m4a','aac'),('wav','pcm_s16le'),('aiff','pcm_s16be'),('ogg','vorbis')]:
   with self.subTest(extension=extension),tempfile.TemporaryDirectory() as tmp:
    root=Path(tmp);audio=root/('音 & sample.'+extension)
    subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','sine=frequency=440:duration=2','-ac','2','-strict','-2','-c:a',codec,'-metadata','comment=keep this','-metadata','title=Original','-y',str(audio)],check=True)
    original=audio.read_bytes();env={**os.environ,'CRATELOOM_DATA':str(root/'data'),'PYTHONPATH':str(Path(__file__).resolve().parents[1])}
    def request(method,params={}):
     req={'protocol_version':1,'request_id':'t','method':method,'params':params};r=subprocess.run([sys.executable,'-m','crateloom'],input=json.dumps(req)+'\n',text=True,capture_output=True,env=env,check=True);result=json.loads(r.stdout);self.assertNotIn('error',result);return result['result']
    track=request('library.import',{'path':str(audio)});cue={'slot':0,'position_us':500000,'label':'Keep cue'};request('cues.save',{'id':track['id'],'cues':[cue]})
    request('metadata.save',{'id':track['id'],'values':{'title':'New 中文','artist':'Artist','album':'Album','genre':'House','date':'2026'}})
    result=request('metadata.write',{'id':track['id']});self.assertTrue(result['written']);self.assertEqual(Path(result['backup']).read_bytes(),original)
    after=request('track.get',{'id':track['id']});self.assertEqual(after['audio_hash'],track['audio_hash']);self.assertEqual(after['cues'],[cue]);self.assertEqual(after['metadata']['pending'],{})
    probe=json.loads(subprocess.run(['ffprobe','-v','error','-show_format','-of','json',str(audio)],capture_output=True,text=True,check=True).stdout)
    # Vorbis stores tags in its stream rather than format.
    streams=json.loads(subprocess.run(['ffprobe','-v','error','-show_streams','-of','json',str(audio)],capture_output=True,text=True,check=True).stdout)['streams']
    tags={k.lower():v for k,v in {**probe['format'].get('tags',{}),**streams[0].get('tags',{})}.items()}
    self.assertEqual(tags.get('comment'), 'keep this');self.assertEqual(tags['title'],'New 中文')
    self.assertFalse(request('metadata.write',{'id':track['id']})['written'])

 @unittest.skipUnless(WRITER and Path(WRITER).is_file(),'Set CRATELOOM_METADATA_PYTHON')
 def test_stale_write_is_rejected_without_replacing_original(self):
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp);audio=root/'test.flac';subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','sine=duration=1','-y',str(audio)],check=True);before=audio.read_bytes()
   recipe=root/'recipe.json';out=root/'out.json';recipe.write_text(json.dumps({'path':str(audio),'expected_hash':'stale','fields':{'title':'No'},'backup_root':str(root/'backup')}))
   result=subprocess.run([WRITER,'-m','crateloom.tag_writer',str(recipe),str(out)],env={**os.environ,'PYTHONPATH':str(Path(__file__).resolve().parents[1])},capture_output=True,text=True)
   self.assertNotEqual(result.returncode,0);self.assertEqual(audio.read_bytes(),before);self.assertIn('changed',json.loads(out.read_text())['error'])

 @unittest.skipUnless(WRITER and Path(WRITER).is_file(),'Set CRATELOOM_METADATA_PYTHON')
 def test_artwork_embedding_and_identity(self):
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp);audio=root/'cover.mp3';image=root/'cover.png'
   subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','sine=duration=1','-y',str(audio)],check=True)
   subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','color=c=red:s=32x32:d=0.04','-frames:v','1','-threads','1','-y',str(image)],check=True)
   env={**os.environ,'CRATELOOM_DATA':str(root/'data'),'PYTHONPATH':str(Path(__file__).resolve().parents[1])}
   def call(m,p={}):
    r=subprocess.run([sys.executable,'-m','crateloom'],input=json.dumps({'protocol_version':1,'request_id':'t','method':m,'params':p})+'\n',text=True,capture_output=True,env=env,check=True);result=json.loads(r.stdout);self.assertNotIn('error',result);return result['result']
   track=call('library.import',{'path':str(audio)});art=call('metadata.artwork',{'path':str(image)})
   call('metadata.save',{'id':track['id'],'values':{'title':'Cover track'},'artwork_path':art['path']})
   result=call('metadata.write',{'id':track['id']});self.assertTrue(result['written'])
   self.assertEqual(call('track.get',{'id':track['id']})['audio_hash'],track['audio_hash'])
   check=subprocess.run([WRITER,'-c','import sys;from mutagen.mp3 import MP3;print(len(MP3(sys.argv[1]).tags.getall("APIC")))',str(audio)],capture_output=True,text=True,check=True)
   self.assertEqual(check.stdout.strip(),'1')

 def test_metadata_candidates_cache_and_offline_failure(self):
  import time
  from types import SimpleNamespace
  from unittest.mock import patch
  sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
  from crateloom import preparation
  with tempfile.TemporaryDirectory() as tmp:
   state={'values':{'title':'Fixture','artist':'Artist'},'pending':{},'artwork_path':None,'artwork_pending':False}
   def atomic(path,data):path=Path(path);path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(data)
   api=SimpleNamespace(ROOT=Path(tmp),track=lambda db,id:{'duration':120,'metadata':state},atomic=atomic)
   payload={'recordings':[{'id':'r','title':'Fixture','length':121000,'disambiguation':'Live','artist-credit':[{'name':'Artist'}],'releases':[{'id':'release','title':'Album','date':'2020'}]}]}
   def finish(job):
    deadline=time.monotonic()+5
    while job['state']=='running' and time.monotonic()<deadline:time.sleep(.02);job=preparation.dispatch(api,None,'metadata.job',{'id':job['id']})
    return job
   from crateloom import metadata_lookup
   with patch.object(preparation,'request',side_effect=lambda url,**kw:json.dumps(payload if 'musicbrainz.org' in url else {'results':[]}).encode()),patch.dict(metadata_lookup.INTERVALS,{'MusicBrainz':0,'Apple Music':0}):
    job=finish(preparation.dispatch(api,None,'metadata.search',{'id':'t'}))
    self.assertEqual(job['state'],'completed');self.assertEqual(job['result']['candidates'][0]['duration_difference'],1)
    self.assertEqual(job['result']['candidates'][0]['version'],'Live')
   with patch.object(preparation,'request',side_effect=OSError('offline')) as remote,patch.dict(metadata_lookup.INTERVALS,{'MusicBrainz':0,'Apple Music':0}):
    cached=finish(preparation.dispatch(api,None,'metadata.search',{'id':'t'}));self.assertTrue(cached['result']['cached']);remote.assert_not_called()
    stale=finish(preparation.dispatch(api,None,'metadata.search',{'id':'t','refresh':True}));self.assertEqual(stale['state'],'completed');self.assertTrue(stale['result']['partial'])
    api.ROOT=Path(tmp)/'uncached'
    failed=finish(preparation.dispatch(api,None,'metadata.search',{'id':'t','refresh':True}));self.assertEqual(failed['state'],'failed');self.assertIn('could not be reached',failed['error'])

 def test_tag_write_requires_current_review_revision(self):
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp);audio=root/'review.wav';subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','sine=duration=1','-y',str(audio)],check=True);original=audio.read_bytes()
   env={**os.environ,'CRATELOOM_DATA':str(root/'data'),'PYTHONPATH':str(Path(__file__).resolve().parents[1])}
   def response(method,params):
    req={'protocol_version':1,'request_id':'t','method':method,'params':params};r=subprocess.run([sys.executable,'-m','crateloom'],input=json.dumps(req)+'\n',text=True,capture_output=True,env=env,check=True);return json.loads(r.stdout)
   track=response('library.import',{'path':str(audio)})['result'];tid=track['id']
   response('metadata.save',{'id':tid,'values':{'title':'First'}});review=response('metadata.get',{'id':tid})['result']
   response('metadata.save',{'id':tid,'values':{'title':'Second'}})
   stale=response('metadata.write',{'id':tid,'revision':review['revision']});self.assertIn('changed after review',stale['error']['message']);self.assertEqual(original,audio.read_bytes())

 def engine(self,root):
  env={**os.environ,'CRATELOOM_DATA':str(root/'data'),'PYTHONPATH':str(Path(__file__).resolve().parents[1])}
  def response(method,params={}):
   req={'protocol_version':1,'request_id':'t','method':method,'params':params};r=subprocess.run([sys.executable,'-m','crateloom'],input=json.dumps(req)+'\n',text=True,capture_output=True,env=env,check=True);return json.loads(r.stdout)
  def call(method,params={}):
   out=response(method,params);self.assertNotIn('error',out);return out['result']
  return response,call

 def test_new_dates_must_be_iso_but_existing_file_dates_can_be_saved_unchanged(self):
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp);audio=root/'dated.flac'
   subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','sine=duration=1','-metadata','date=May 2020','-y',str(audio)],check=True)
   response,call=self.engine(root);tid=call('library.import',{'path':str(audio)})['id']
   current=call('metadata.get',{'id':tid})['values'];self.assertEqual(current['date'],'May 2020')
   # The editor sends every field back; an untouched legacy date must not block a title edit.
   call('metadata.save',{'id':tid,'values':{**current,'title':'Renamed'}})
   self.assertEqual(call('metadata.get',{'id':tid})['pending'],{'title':'Renamed'})
   for bad in ('June 2021','17/05/2026','2026-5-7','2026-13-01','2007-10-09T07:00:00Z'):
    with self.subTest(date=bad):self.assertIn('YYYY-MM-DD',response('metadata.save',{'id':tid,'values':{'date':bad}})['error']['message'])
   self.assertNotIn('date',call('metadata.get',{'id':tid})['pending'])
   for good in ('2026','2026-05',' 2026-05-17 ',''):
    with self.subTest(date=good):
     call('metadata.save',{'id':tid,'values':{'date':good}});self.assertEqual(call('metadata.get',{'id':tid})['pending']['date'],good.strip())

 @unittest.skipUnless(WRITER and Path(WRITER).is_file(),'Set CRATELOOM_METADATA_PYTHON')
 def test_id3_dates_survive_read_back_and_a_legacy_bad_date_is_refused_clearly(self):
  import sqlite3
  for version in ('3','4'):
   with self.subTest(id3=version),tempfile.TemporaryDirectory() as tmp:
    root=Path(tmp);audio=root/'dated.mp3'
    subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','sine=duration=1','-id3v2_version',version,'-metadata','title=Dated','-y',str(audio)],check=True)
    response,call=self.engine(root);tid=call('library.import',{'path':str(audio)})['id']
    call('metadata.save',{'id':tid,'values':{'date':'2026-05-17'}});self.assertTrue(call('metadata.write',{'id':tid})['written'])
    self.assertEqual(call('metadata.reload',{'id':tid})['values']['date'],'2026-05-17')
    # A date staged before validation existed fails the ID3 read-back; refuse it with a usable message.
    call('metadata.save',{'id':tid,'values':{'album':'Album'}});before=audio.read_bytes()
    with sqlite3.connect(root/'data'/'library.sqlite3') as db:
     state=json.loads(db.execute('SELECT metadata FROM tracks WHERE id=?',(tid,)).fetchone()[0]);state['pending']['date']=state['values']['date']='May 2020'
     db.execute('UPDATE tracks SET metadata=? WHERE id=?',(json.dumps(state),tid))
    db.close()
    refused=response('metadata.write',{'id':tid});self.assertIn('YYYY-MM-DD',refused['error']['message']);self.assertEqual(audio.read_bytes(),before)
    call('metadata.save',{'id':tid,'values':{'date':'2020-05'}});self.assertTrue(call('metadata.write',{'id':tid})['written'])

 @unittest.skipUnless(WRITER and Path(WRITER).is_file(),'Set CRATELOOM_METADATA_PYTHON')
 def test_ogg_write_keeps_text_tags_and_leaves_the_cover_in_the_library(self):
  for extension,codec in [('ogg','vorbis'),('opus','libopus')]:
   with self.subTest(extension=extension),tempfile.TemporaryDirectory() as tmp:
    root=Path(tmp);audio=root/('cover.'+extension);image=root/'cover.png'
    made=subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','sine=duration=2','-ac','2','-strict','-2','-c:a',codec,'-metadata','title=Original','-y',str(audio)])
    if made.returncode:self.skipTest('ffmpeg cannot encode '+codec)
    subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','color=c=red:s=32x32:d=0.04','-frames:v','1','-threads','1','-y',str(image)],check=True)
    response,call=self.engine(root);track=call('library.import',{'path':str(audio)});tid=track['id']
    self.assertFalse(call('metadata.get',{'id':tid})['artwork_supported'])
    art=call('metadata.artwork',{'path':str(image)})['path']
    # The automatic batch stages a cover for every codec, alongside text fields.
    call('metadata.save',{'id':tid,'values':{'title':'Catalog title','album':'Album'},'artwork_path':art})
    review=call('metadata.get',{'id':tid});self.assertTrue(review['artwork_pending'])
    result=call('metadata.write',{'id':tid,'revision':review['revision']})
    self.assertTrue(result['written']);self.assertFalse(result['artwork_embedded']);self.assertIn('library',result['note'])
    after=call('metadata.get',{'id':tid})
    self.assertEqual((after['pending'],after['artwork_pending'],after['artwork_path']),({},False,art))
    self.assertEqual(call('track.get',{'id':tid})['audio_hash'],track['audio_hash'])
    streams=json.loads(subprocess.run(['ffprobe','-v','error','-show_streams','-of','json',str(audio)],capture_output=True,text=True,check=True).stdout)['streams']
    self.assertEqual([s['codec_type'] for s in streams],['audio']);self.assertEqual({k.lower():v for k,v in streams[0]['tags'].items()}['title'],'Catalog title')
    # A cover staged on its own has nothing to write: no backup, no rewrite, nothing left pending.
    call('metadata.save',{'id':tid,'values':{},'artwork_path':None});before=audio.read_bytes()
    alone=call('metadata.write',{'id':tid})
    self.assertEqual((alone['written'],alone['backup'],alone['artwork_embedded']),(False,None,False));self.assertEqual(audio.read_bytes(),before)
    self.assertFalse(call('metadata.get',{'id':tid})['artwork_pending'])
