import contextlib
import email.message
import io
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from crateloom import __main__ as api
from crateloom import acquisitions, providers, download_http, youtube_audio


class Response:
    def __init__(self, data, length=True, gate=None, mime='audio/wav'):
        self.body=io.BytesIO(data);self.url='https://artist.example/audio.wav?token=private';self.status=200;self.gate=gate
        self.headers=email.message.Message();self.headers['Content-Type']=mime;self.headers['ETag']='"v1"'
        if length:self.headers['Content-Length']=str(len(data))
    def read1(self,n):
        if self.gate:self.gate.wait(2)
        return self.body.read(n)
    def __enter__(self):return self
    def __exit__(self,*args):self.body.close()


class Acquisitions(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name).resolve();self.stack=contextlib.ExitStack()
        for name,value in [('ROOT',self.root),('DB',self.root/'library.sqlite3'),('CACHE',self.root/'analysis-cache')]:self.stack.enter_context(patch.object(api,name,value))
        self.wav=self.root/'fixture.wav'
        subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','sine=duration=1','-y',str(self.wav)],check=True)
        self.audio=self.wav.read_bytes()
        with api.connect():pass
        self.source=providers.register(api,{'kind':'direct','provider':'Direct HTTPS','url':'https://artist.example/audio.wav?token=private','resolved_url':'https://artist.example/audio.wav?token=private','filename':'../../artist.wav','mime':'audio/wav','declared_bytes':len(self.audio),'etag':'"v1"'})
    def tearDown(self):
        manager=acquisitions.MANAGERS.pop(str(self.root),None)
        if manager:manager.close()
        self.stack.close();self.tmp.cleanup()
    def enqueue(self,**extras):return api.dispatch('acquisitions.enqueue',{'source_id':self.source['id'],'values':{'title':'Reviewed title','artist':'Artist'},**extras})
    def finish(self,jid,states=('completed','failed','cancelled','interrupted')):
        deadline=time.monotonic()+8
        while time.monotonic()<deadline:
            job=next(j for j in api.dispatch('acquisitions.list',{}) if j['id']==jid)
            if job['state'] in states:return job
            time.sleep(.02)
        self.fail('Job did not reach expected state: '+str(job))
    def test_verified_download_import_metadata_and_duplicate_keep_existing_cues(self):
        with patch.object(download_http,'open_url',side_effect=lambda *a,**k:Response(self.audio)):
            job=self.finish(self.enqueue(version='Club Edit')['id']);self.assertEqual(job['state'],'completed',job)
            target=Path(job['path']);self.assertEqual(target.read_bytes(),self.audio);self.assertEqual(target.parent,self.root/'downloads')
            self.assertEqual(job['quality']['codec'],'pcm_s16le');self.assertFalse(job['quality']['converted']);self.assertNotIn('token=',json.dumps(job))
            track=api.dispatch('track.get',{'id':job['track_id']});self.assertEqual(track['title'],'Reviewed title (Club Edit)')
            api.dispatch('cues.save',{'id':track['id'],'cues':[{'slot':0,'position_us':500000,'label':'Keep'}]})
            duplicate=self.finish(self.enqueue(values={'title':'Do not replace'})['id']);self.assertTrue(duplicate['duplicate']);self.assertEqual(duplicate['track_id'],track['id'])
            self.assertEqual(api.dispatch('track.get',{'id':track['id']})['cues'][0]['label'],'Keep')
            self.assertEqual(api.dispatch('track.get',{'id':track['id']})['title'],'Reviewed title (Club Edit)')
            self.assertEqual(len(list((self.root/'downloads').iterdir())),1)
            self.assertEqual(list((self.root/'acquisition-work').iterdir()),[])
            self.assertEqual(api.dispatch('acquisitions.clear_completed', {}), {'cleared':2})
            self.assertEqual(api.dispatch('acquisitions.list', {}), [])
            self.assertTrue(target.is_file())
            self.assertEqual(api.dispatch('track.get',{'id':track['id']})['cues'][0]['label'],'Keep')
    def test_youtube_download_reuses_fresh_media_and_keeps_page_provenance(self):
        m4a=self.root/'fixture.m4a'
        subprocess.run(['ffmpeg','-v','error','-i',str(self.wav),'-c:a','aac',str(m4a)],check=True)
        audio=m4a.read_bytes()
        signed='https://r1.googlevideo.com/videoplayback?token=ephemeral'
        url='https://music.youtube.com/watch?v=uXn-zOt68V8'
        data={'id':'uXn-zOt68V8','title':'Test Artist - Test Title (Lyrics)','duration':1,'ext':'m4a',
              'protocol':'https','format_id':'140','url':signed,'filesize':len(audio),'availability':'public'}
        process=api.import_process
        def run(args,timeout,**options):
            if args[0]=='/tools/yt-dlp':
                self.assertEqual(args[-1],'https://www.youtube.com/watch?v=uXn-zOt68V8')
                self.assertIn('--skip-download',args)
                self.assertIn('--ignore-config',args)
                self.assertIn('--no-plugin-dirs',args)
                return SimpleNamespace(stdout=json.dumps(data))
            return process(args,timeout,**options)
        def response(url,**kwargs):
            self.assertEqual(url,signed)
            r=Response(audio,mime='audio/mp4');r.url=signed
            return r
        with patch.object(youtube_audio,'executable',return_value='/tools/yt-dlp'),patch.object(api,'import_process',side_effect=run) as runner,patch.object(download_http,'open_url',side_effect=response):
            result=providers.resolve(api,url+'&si=tracking')
            source=result['candidates'][0]
            self.assertEqual(source['values'],{'title':'Test Title','artist':'Test Artist'})
            self.assertNotIn('ephemeral',json.dumps(result))
            job=self.finish(api.dispatch('acquisitions.enqueue',{'source_id':source['id'],'values':source['values']})['id'])
            self.assertEqual(job['state'],'completed',job)
            self.assertEqual(Path(job['path']).read_bytes(),audio)
            self.assertEqual(job['source_url'],url)
            self.assertFalse(job['quality']['converted'])
            self.assertNotIn('ephemeral',json.dumps(job))
            selectors=[c.args[0][c.args[0].index('-f')+1] for c in runner.call_args_list if c.args[0][0]=='/tools/yt-dlp']
            self.assertEqual(selectors,['bestaudio[ext=m4a][protocol=https][vcodec=none]'])
            source=providers.resolve(api,url)['candidates'][0]
            with patch.object(download_http,'open_url',side_effect=[
                download_http.SourceHTTPError(403,'Expired'),response(signed),
            ]):
                retry=self.finish(api.dispatch('acquisitions.enqueue',{'source_id':source['id'],'values':source['values']})['id'])
            self.assertTrue(retry['duplicate'],retry)
            selectors=[c.args[0][c.args[0].index('-f')+1] for c in runner.call_args_list if c.args[0][0]=='/tools/yt-dlp']
            self.assertEqual(selectors[-1],'140[ext=m4a][protocol=https][vcodec=none]')
        with api.connect() as db:
            self.assertNotIn('ephemeral',db.execute('select review from acquisitions where id=?',(job['id'],)).fetchone()[0])

    def test_youtube_dash_download_is_repacked_into_a_regular_m4a(self):
        # YouTube serves fragmented MP4; WebKit reports those as twice their length.
        dash=self.root/'fixture-dash.m4a'
        subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','sine=duration=2','-c:a','aac','-movflags','+frag_keyframe+empty_moov+default_base_moof','-brand','dash','-f','mp4','-y',str(dash)],check=True)
        audio=dash.read_bytes()
        probe=lambda path:json.loads(subprocess.run(['ffprobe','-v','error','-show_format','-show_streams','-of','json',str(path)],check=True,capture_output=True,text=True).stdout)
        self.assertTrue(acquisitions.is_dash(probe(dash)))
        signed='https://r1.googlevideo.com/videoplayback?token=ephemeral'
        url='https://www.youtube.com/watch?v=uXn-zOt68V8'
        data={'id':'uXn-zOt68V8','title':'Test Artist - Test Title','duration':2,'ext':'m4a','protocol':'https','format_id':'140','url':signed,'filesize':len(audio),'availability':'public'}
        process=api.import_process
        def run(args,timeout,**options):
            if args[0]=='/tools/yt-dlp':return SimpleNamespace(stdout=json.dumps(data))
            return process(args,timeout,**options)
        def response(url,**kwargs):
            r=Response(audio,mime='audio/mp4');r.url=signed
            return r
        with patch.object(youtube_audio,'executable',return_value='/tools/yt-dlp'),patch.object(api,'import_process',side_effect=run),patch.object(download_http,'open_url',side_effect=response):
            source=providers.resolve(api,url)['candidates'][0]
            job=self.finish(api.dispatch('acquisitions.enqueue',{'source_id':source['id'],'values':source['values']})['id'])
        self.assertEqual(job['state'],'completed',job)
        target=Path(job['path']);saved=probe(target)
        self.assertFalse(acquisitions.is_dash(saved))
        self.assertNotIn(b'moof',target.read_bytes()[:4096])
        self.assertTrue(job['quality']['repacked']);self.assertFalse(job['quality']['converted'])
        self.assertEqual(job['quality']['sha256'],__import__('hashlib').sha256(target.read_bytes()).hexdigest())
        self.assertEqual(job['quality']['bytes'],target.stat().st_size)
        self.assertAlmostEqual(float(saved['format']['duration']),float(probe(dash)['format']['duration']),places=2)
        # The audio packets are copied unchanged.
        stream_hash=lambda path:subprocess.run(['ffmpeg','-v','error','-i',str(path),'-map','0:a:0','-c','copy','-f','hash','-hash','sha256','-'],check=True,capture_output=True,text=True).stdout
        self.assertEqual(stream_hash(target),stream_hash(dash))
        self.assertEqual(list((self.root/'acquisition-work').iterdir()),[])
    def test_youtube_download_waits_until_the_audio_address_is_served(self):
        # YouTube answers 403 for an audio address until its available_at time, a few seconds after it is issued.
        m4a=self.root/'fixture.m4a'
        subprocess.run(['ffmpeg','-v','error','-i',str(self.wav),'-c:a','aac',str(m4a)],check=True)
        audio=m4a.read_bytes()
        signed='https://r1.googlevideo.com/videoplayback?token=waits'
        url='https://www.youtube.com/watch?v=uXn-zOt68V8'
        data={'id':'uXn-zOt68V8','title':'Test Artist - Test Title','duration':1,'ext':'m4a','protocol':'https','format_id':'140','url':signed,'filesize':len(audio),'availability':'public'}
        process=api.import_process
        def run(args,timeout,**options):
            if args[0]=='/tools/yt-dlp':
                self.assertIn('available_at',args[args.index('--print')+1])
                return SimpleNamespace(stdout=json.dumps({**data,'available_at':time.time()+1.2}))
            return process(args,timeout,**options)
        opened=[]
        def response(url,**kwargs):
            opened.append(time.monotonic())
            r=Response(audio,mime='audio/mp4');r.url=signed
            return r
        with patch.object(youtube_audio,'executable',return_value='/tools/yt-dlp'),patch.object(api,'import_process',side_effect=run),patch.object(download_http,'open_url',side_effect=response):
            source=providers.resolve(api,url)['candidates'][0]
            issued=time.monotonic()
            job=self.finish(api.dispatch('acquisitions.enqueue',{'source_id':source['id'],'values':source['values']})['id'])
        self.assertEqual(job['state'],'completed',job)
        self.assertGreaterEqual(opened[0]-issued,1.0)
    def test_waiting_for_a_youtube_address_can_be_cancelled(self):
        youtube_audio.note_ready('https://r1.googlevideo.com/videoplayback?token=later',time.time()+30)
        def check():raise acquisitions.ImportCancelled()
        started=time.monotonic()
        with self.assertRaises(acquisitions.ImportCancelled):
            youtube_audio.wait_until_ready('https://r1.googlevideo.com/videoplayback?token=later',check)
        self.assertLess(time.monotonic()-started,1)
        # Addresses with no available_at, or one already passed, are fetched at once.
        youtube_audio.note_ready('https://r1.googlevideo.com/videoplayback?token=past',time.time()-5)
        youtube_audio.note_ready('https://r1.googlevideo.com/videoplayback?token=none',None)
        started=time.monotonic()
        for name in ('past','none','unknown'):
            youtube_audio.wait_until_ready('https://r1.googlevideo.com/videoplayback?token='+name,check)
        self.assertLess(time.monotonic()-started,.1)
    def test_unicode_filename_stays_within_filesystem_byte_limit(self):
        providers.CANDIDATES[self.source['id']]['value']['filename']='音楽'*90+'.wav'
        with patch.object(download_http,'open_url',side_effect=lambda *a,**k:Response(self.audio)):
            job=self.finish(self.enqueue()['id']);self.assertEqual(job['state'],'completed',job)
        self.assertLess(len(Path(job['path']).name.encode('utf-8')),255)
        self.assertEqual(Path(job['path']).read_bytes(),self.audio)
    def test_invalid_audio_fails_without_import_and_retry_uses_persisted_review(self):
        invalid=b'<html>Not audio</html>'.ljust(len(self.audio),b' ')
        with patch.object(download_http,'open_url',side_effect=lambda *a,**k:Response(invalid)):
            failed=self.finish(self.enqueue()['id']);self.assertEqual(failed['state'],'failed');self.assertIn('not readable audio',failed['error'])
        self.assertEqual(api.dispatch('library.list',{}),[]);self.assertFalse((self.root/'downloads').exists())
        with patch.object(download_http,'open_url',side_effect=lambda *a,**k:Response(self.audio)):
            retried=api.dispatch('acquisitions.retry',{'id':failed['id']});done=self.finish(retried['id']);self.assertEqual(done['state'],'completed',done);self.assertEqual(done['attempt'],2)
    def test_retry_is_not_refused_while_a_set_prepare_fills_the_queue(self):
        manager=acquisitions.manager(api)
        with patch.object(manager,'ensure_worker'):
            failed=self.enqueue()
            with api.connect() as db:
                db.execute("UPDATE acquisitions SET state='failed' WHERE id=?",(failed['id'],))
                for _ in range(acquisitions.QUEUE_CAPACITY+5):
                    manager.enqueue_in(db,{'source_id':self.source['id'],'values':{'title':'Set entry'}},capacity=acquisitions.SET_CAPACITY)
            with self.assertRaisesRegex(ValueError,'queue is full'):self.enqueue()
            retried=api.dispatch('acquisitions.retry',{'id':failed['id']})
            self.assertEqual((retried['state'],retried['attempt']),('queued',2))
    def test_parallel_queue_bounds_active_jobs_and_cancels_without_partial_imports(self):
        gate=threading.Event()
        with patch.object(download_http,'open_url',side_effect=lambda *a,**k:Response(self.audio,gate=gate)):
            jobs=[self.enqueue()['id'] for _ in range(acquisitions.PER_SOURCE)]
            try:
                for jid in jobs:
                    self.finish(jid,('downloading',))
                waiting=self.enqueue()
                self.assertEqual(waiting['state'],'queued')
                self.assertEqual(api.dispatch('acquisitions.cancel',{'id':waiting['id']})['state'],'cancelled')
                for jid in jobs:
                    api.dispatch('acquisitions.cancel',{'id':jid})
            finally:
                gate.set()
            for jid in jobs:
                self.assertEqual(self.finish(jid)['state'],'cancelled')
        self.assertEqual(api.dispatch('library.list',{}),[]);self.assertEqual(list((self.root/'acquisition-work').iterdir()),[])

    def test_ranges_assemble_exact_file_and_fall_back_when_source_ignores_range(self):
        manager=acquisitions.manager(api)
        audio=self.audio
        size=len(audio)
        signed='https://r1.googlevideo.com/videoplayback?token=ephemeral'
        source={'url':'https://www.youtube.com/watch?v=uXn-zOt68V8','declared_bytes':size}
        partial=self.root/'range.part'

        def range_response(url,headers):
            start,end=map(int,headers['Range'].removeprefix('bytes=').split('-'))
            response=Response(audio[start:end+1])
            response.url=signed
            response.status=206
            response.headers['Content-Range']=f'bytes {start}-{end}/{size}'
            return response

        with patch.object(acquisitions,'RANGE_SIZE',8192), patch.object(download_http,'open_url',side_effect=range_response) as request:
            _,received,digest=manager.transfer('test',source,signed,{},partial,lambda:None,ranges=True)
            self.assertGreater(request.call_count,2)
            self.assertEqual(received,size)
            self.assertEqual(partial.read_bytes(),audio)
            self.assertEqual(digest,api.file_hash(self.wav))
        partial.unlink()
        with patch.object(acquisitions,'RANGE_SIZE',8192), patch.object(download_http,'open_url',side_effect=lambda *a,**k:Response(audio)) as request:
            manager.transfer('test',source,signed,{},partial,lambda:None,ranges=True)
            self.assertIn('Range',request.call_args_list[0].kwargs['headers'])
            self.assertEqual(request.call_args_list[1].kwargs['headers'],{})
            self.assertEqual(partial.read_bytes(),audio)

    def test_rate_limited_source_waits_and_retries_while_another_host_downloads(self):
        other=providers.register(api,{'kind':'direct','provider':'Direct HTTPS','url':'https://other.example/audio.wav',
                                      'filename':'track.wav','declared_bytes':len(self.audio)})
        attempts=[]
        def response(url,**kwargs):
            if 'artist.example' in url:
                attempts.append(time.monotonic())
                if len(attempts)==1:
                    raise download_http.SourceHTTPError(429,'Rate limited','2')
            return Response(self.audio)
        with patch.object(download_http,'open_url',side_effect=response):
            first=self.enqueue()
            self.assertEqual(self.finish(first['id'],('queued',))['state'],'queued')
            second=api.dispatch('acquisitions.enqueue',{'source_id':other['id'],'values':{'title':'Other host'}})
            self.assertEqual(self.finish(second['id'])['state'],'completed')
            self.assertEqual(self.finish(first['id'])['state'],'completed')
            self.assertGreaterEqual(attempts[1]-attempts[0],2)
    def test_unknown_length_download_is_bounded(self):
        self.source['declared_bytes']=None
        # Registry entries are the reviewed source snapshots, not caller URLs.
        providers.CANDIDATES[self.source['id']]['value']['declared_bytes']=None
        with patch.object(download_http,'open_url',side_effect=lambda *a,**k:Response(self.audio,length=False)),patch.object(download_http,'MAX_BYTES',100):
            job=self.finish(self.enqueue()['id']);self.assertEqual(job['state'],'failed');self.assertIn('512 MB',job['error'])
        self.assertEqual(api.dispatch('library.list',{}),[])
    def test_reference_duration_conflict_prevents_wrong_metadata_import(self):
        reference=providers.register(api,{'kind':'reference','values':{'title':'Different version'},'duration':300,'source_url':'https://musicbrainz.org/recording/reference'})
        with patch.object(download_http,'open_url',side_effect=lambda *a,**k:Response(self.audio)):
            job=self.finish(self.enqueue(reference_id=reference['id'])['id']);self.assertEqual(job['state'],'failed');self.assertIn('correct version',job['error'])
        self.assertEqual(api.dispatch('library.list',{}),[])
    def test_truncated_or_changed_sources_do_not_enter_library(self):
        for kind in ('truncated','size','etag'):
            response=Response(self.audio)
            if kind=='truncated':response.body=io.BytesIO(self.audio[:100])
            if kind=='size':response.headers.replace_header('Content-Length',str(len(self.audio)+1))
            if kind=='etag':response.headers.replace_header('ETag','"v2"')
            with patch.object(download_http,'open_url',return_value=response):
                job=self.finish(self.enqueue()['id']);self.assertEqual(job['state'],'failed',job)
            self.assertEqual(api.dispatch('library.list',{}),[])
            self.assertEqual(list((self.root/'acquisition-work').iterdir()),[])
    def test_cancellation_during_import_rolls_back_track_and_staged_metadata(self):
        original=api.import_track
        def cancelled(db,path):
            original(db,path)
            raise acquisitions.ImportCancelled()
        with patch.object(download_http,'open_url',side_effect=lambda *a,**k:Response(self.audio)),patch.object(api,'import_track',side_effect=cancelled):
            job=self.finish(self.enqueue()['id']);self.assertEqual(job['state'],'cancelled')
        self.assertEqual(api.dispatch('library.list',{}),[])
        self.assertEqual(list((self.root/'downloads').iterdir()),[])
    def test_queued_review_resumes_after_process_restart_without_provider_registry(self):
        review={'source':self.source,'fields':{'title':'Persisted review'},'version':''};jid='00000000-0000-4000-8000-000000000002'
        with api.connect() as db:db.execute("INSERT INTO acquisitions(id,state,stage,review,created_at,updated_at) VALUES (?,'queued','Queued',?,'now','now')",(jid,json.dumps(review)))
        providers.CANDIDATES.pop(self.source['id'])
        with patch.object(download_http,'open_url',side_effect=lambda *a,**k:Response(self.audio)):
            job=self.finish(jid);self.assertEqual(job['state'],'completed',job)
        self.assertEqual(api.dispatch('track.get',{'id':job['track_id']})['title'],'Persisted review')
    def test_download_path_never_overwrites_existing_audio(self):
        manager=acquisitions.manager(api)
        with patch.object(manager,'ensure_worker'):
            job=self.enqueue()
        folder=self.root/'downloads';folder.mkdir()
        # The reviewed filename contains traversal; only its basename can be used.
        target=folder/f"track-{job['id']}-1-artist.wav";target.write_bytes(b'keep existing')
        with patch.object(download_http,'open_url',side_effect=lambda *a,**k:Response(self.audio)):
            done=self.finish(job['id']);self.assertEqual(done['state'],'failed')
        self.assertEqual(target.read_bytes(),b'keep existing');self.assertEqual(api.dispatch('library.list',{}),[])
    def test_process_restart_marks_active_jobs_interrupted_and_cleans_partial(self):
        review={'source':self.source,'fields':{'title':'Reviewed title'},'version':''};jid='00000000-0000-4000-8000-000000000001'
        with api.connect() as db:db.execute("INSERT INTO acquisitions(id,state,stage,review,created_at,updated_at) VALUES (?,'downloading','Downloading',?,'now','now')",(jid,json.dumps(review)))
        work=self.root/'acquisition-work';work.mkdir();partial=work/(jid+'-1.part');partial.write_bytes(b'partial')
        job=api.dispatch('acquisitions.list',{})[0];self.assertEqual(job['state'],'interrupted');self.assertFalse(partial.exists())
        with patch.object(download_http,'open_url',side_effect=lambda *a,**k:Response(self.audio)):
            result=self.finish(api.dispatch('acquisitions.retry',{'id':jid})['id']);self.assertEqual(result['state'],'completed',result)
    def test_schema_v10_upgrade_keeps_library_and_creates_backup(self):
        track=api.dispatch('library.import',{'path':str(self.wav)})
        with sqlite3.connect(api.DB) as db:db.execute('PRAGMA user_version=10')
        with api.connect() as db:self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0],11)
        self.assertEqual(api.dispatch('track.get',{'id':track['id']})['path'],str(self.wav))
        backups=list(self.root.glob('library-before-v11-*.sqlite3'));self.assertEqual(len(backups),1)
        with sqlite3.connect(backups[0]) as db:self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0],10)


class ProviderContracts(unittest.TestCase):
    def test_immediate_provider_result_still_returns_pollable_pending_job(self):
        from crateloom import preparation
        with patch.object(preparation.PROVIDER_POOL,'submit',side_effect=lambda target:target()):
            job=preparation.launch(lambda:{'kind':'unavailable'})
        self.assertEqual(job['state'],'running');self.assertIsNone(job['result'])
        finished=preparation.dispatch(None,None,'metadata.job',{'id':job['id']})
        self.assertEqual(finished['state'],'completed');self.assertEqual(finished['result']['kind'],'unavailable')
    def test_head_falls_back_to_range_headers_with_whole_file_size(self):
        response=Response(b'x');response.status=206;response.headers['Content-Range']='bytes 0-0/5000'
        opener=unittest.mock.Mock();opener.open.side_effect=urllib.error.HTTPError('https://artist.example/a.wav',405,'HEAD unsupported',{},io.BytesIO())
        with patch.object(download_http.urllib.request,'build_opener',return_value=opener),patch.object(download_http,'open_url',return_value=response) as request:
            info=download_http.inspect('https://artist.example/a.wav');self.assertEqual(info['declared_bytes'],5000)
            request.assert_called_once_with('https://artist.example/a.wav',headers={'Range':'bytes=0-0'})
    def test_review_is_scoped_and_expires_without_accepting_arbitrary_sources(self):
        class API:ROOT=Path('/one')
        class Other:ROOT=Path('/other')
        source=providers.register(API,{'kind':'direct','url':'https://artist.example/song.wav'})
        with self.assertRaises(ValueError):providers.get(Other,source['id'],'direct')
        with self.assertRaises(ValueError):providers.get(API,source['id'],'reference')
        providers.CANDIDATES[source['id']]['at']-=3601
        with self.assertRaises(ValueError):providers.get(API,source['id'],'direct')
    def test_recent_reviews_survive_a_large_set_prepare(self):
        class API:ROOT=Path('/registry')
        with patch.dict(providers.CANDIDATES,{},clear=True):
            first=providers.register(API,{'kind':'direct','url':'https://artist.example/first.wav'})
            for index in range(providers.MAX_CANDIDATES*2):providers.register(API,{'kind':'reference','duration':index})
            self.assertEqual(providers.get(API,first['id'],'direct')['url'],'https://artist.example/first.wav')
            for item in providers.CANDIDATES.values():item['at']-=providers.CANDIDATE_HOLD+1
            providers.register(API,{'kind':'reference'})
            self.assertEqual(len(providers.CANDIDATES),providers.MAX_CANDIDATES)
            with self.assertRaises(ValueError):providers.get(API,first['id'],'direct')
        with patch.dict(providers.CANDIDATES,{},clear=True),patch.object(providers,'CANDIDATE_LIMIT',providers.MAX_CANDIDATES+10):
            for index in range(providers.MAX_CANDIDATES+40):providers.register(API,{'kind':'reference','duration':index})
            self.assertEqual(len(providers.CANDIDATES),providers.MAX_CANDIDATES+10)
    def test_musicbrainz_requests_are_paced_and_serve_text_search(self):
        from crateloom import preparation
        class API:ROOT=Path('/musicbrainz')
        recording={'id':'11111111-1111-4111-8111-111111111111','title':'Song','length':200000,'score':100,'artist-credit':[{'name':'Artist'}],
                   'releases':[{'id':'22222222-2222-4222-8222-222222222222','title':'Album','status':'Official'}]}
        urls=[];sleeps=[];clock=[100.0]
        def request(url,*args,**kwargs):urls.append(url);return json.dumps({'recordings':[recording]}).encode()
        def sleep(seconds):sleeps.append(seconds);clock[0]+=seconds
        fake=SimpleNamespace(monotonic=lambda:clock[0],sleep=sleep)
        with patch.dict(providers.CANDIDATES,{},clear=True),patch.object(preparation,'request',side_effect=request),patch.object(providers,'time',fake),patch.object(providers,'LAST_MUSICBRAINZ_REQUEST',0):
            data=providers.musicbrainz('recording/'+recording['id'],{'inc':'url-rels','fmt':'json'})
            self.assertEqual(data['recordings'][0]['id'],recording['id'])
            self.assertTrue(urls[0].startswith('https://musicbrainz.org/ws/2/recording/'+recording['id']+'?inc=url-rels'))
            self.assertEqual(sleeps,[])
            found=providers.recording_search(API,'Artist - Song')
            self.assertAlmostEqual(sleeps[0],providers.MUSICBRAINZ_INTERVAL)
            reference=found['candidates'][0]
            self.assertEqual((reference['kind'],reference['recording_id'],reference['values']['artist']),('reference',recording['id'],'Artist'))
            self.assertEqual(providers.get(API,reference['id'],'reference')['release_id'],recording['releases'][0]['id'])
            self.assertEqual(providers.resolve(API,'Artist - Song')['kind'],'references')
            self.assertEqual(len(sleeps),len(urls)-1)
            failure=urllib.error.HTTPError('https://musicbrainz.org/ws/2/recording/',503,'Busy',None,None)
            with patch.object(preparation,'request',side_effect=failure) as busy,self.assertRaisesRegex(ValueError,'MusicBrainz is unavailable'):
                providers.musicbrainz('recording/',{'query':'recording:"Song"','fmt':'json'})
            self.assertEqual(busy.call_count,2)
    def test_private_urls_credentials_and_downgrade_redirects_are_rejected(self):
        for url in ['http://artist.example/song.mp3','file:///tmp/song.mp3','https://127.0.0.1/song.mp3','https://[::1]/song.mp3','https://user:secret@artist.example/song.mp3','https://artist.example:444/song.mp3']:
            with self.subTest(url=url),self.assertRaises(ValueError):download_http.public_url(url)
        self.assertEqual(download_http.public_url('https://artist.example/My Song.mp3'),'https://artist.example/My%20Song.mp3')
        with self.assertRaises(ValueError):download_http.PublicRedirect().redirect_request(None,None,302,'',{},'http://artist.example/file.mp3')
        with patch.object(download_http.socket,'getaddrinfo',return_value=[(2,1,6,'',('127.0.0.1',443))]),patch.object(download_http.socket,'create_connection') as connect:
            with self.assertRaises(ValueError):download_http.PublicConnection('artist.example',timeout=1).connect()
            connect.assert_not_called()
    @patch.object(youtube_audio,'executable',return_value=None)
    def test_service_links_are_references_without_a_downloader(self, executable):
        class API:ROOT=Path('/unused')
        cases=[
            ('https://youtu.be/uXn-zOt68V8?si=private','YouTube','www.youtube.com'),
            ('https://music.youtube.com/watch?v=uXn-zOt68V8&list=private','YouTube Music','www.youtube.com'),
            ('https://soundcloud.com/forss/flickermood?secret_token=private','SoundCloud','soundcloud.com'),
            ('https://open.spotify.com/intl-en/track/0VjIjW4GlUZAMYd2vXMi3b?si=private','Spotify','open.spotify.com'),
        ]
        import urllib.parse
        for url,name,endpoint in cases:
            response=io.BytesIO(json.dumps({'title':'An actual title','author_name':'Uploader','html':'ignored'}).encode())
            with self.subTest(name=name),patch.object(download_http,'inspect') as inspect,patch.object(download_http,'open_url',return_value=response) as fetch:
                result=providers.resolve(API,url)
                self.assertEqual(result['kind'],'service_reference')
                candidate=result['candidates'][0]
                self.assertEqual(candidate['provider'],name)
                self.assertEqual(candidate['values']['artist'],'')
                self.assertFalse(candidate['can_acquire'])
                self.assertNotIn('private',json.dumps(result))
                self.assertEqual(urllib.parse.urlsplit(fetch.call_args.args[0]).hostname,endpoint)
                inspect.assert_not_called()
                with self.assertRaises(ValueError):providers.get(API,candidate['id'],'direct')
        caps=providers.capabilities(API)['providers']
        self.assertTrue(caps[0]['acquisition'])
        self.assertTrue(all(p['url_resolution'] and not p['acquisition'] for p in caps[2:]))
    def test_invalid_service_paths_do_not_fetch(self):
        class API:ROOT=Path('/unused')
        with patch.object(download_http,'open_url') as fetch:
            for url in ('https://www.youtube.com/playlist?list=test','https://music.youtube.com/watch?v=123','https://open.spotify.com/track/123','https://soundcloud.com/name/track/s-secret'):
                with self.assertRaises(ValueError):providers.resolve(API,url)
            fetch.assert_not_called()


if __name__=='__main__':unittest.main()
