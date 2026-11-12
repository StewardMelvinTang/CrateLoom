from contextlib import ExitStack
import asyncio
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from crateloom import __main__ as api
from crateloom import download_http, playlist_sources, preparation, scan_audio, set_import, set_scan_jobs, shazam_fast, shazam_scan, shazam_worker, youtube_audio, youtube_tracklist
from crateloom.library_imports import ImportCancelled

WATCH = 'https://www.youtube.com/watch?v=RvRhUHTV_8k'


class ScanJobs(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory())).resolve()
        for name, value in [('ROOT',self.root),('DB',self.root/'library.sqlite3'),('CACHE',self.root/'cache')]:
            self.stack.enter_context(patch.object(api,name,value))
        self.stack.enter_context(patch.object(shazam_scan,'NEXT_SCAN_AT',0))

    def finish(self, task):
        deadline = time.monotonic()+8
        while task['state'] in ('running','cancelling') and time.monotonic()<deadline:
            time.sleep(.02)
            task = api.dispatch('metadata.job',{'id':task['id']})
        self.assertNotIn(task['state'],('running','cancelling'),task)
        return task

    def audio(self, name='mix.wav', duration=2):
        path = self.root/name
        args = ['ffmpeg','-v','error','-f','lavfi','-i',f'sine=duration={duration}']
        subprocess.run([*args,'-y',str(path)],check=True)
        return path

    def test_tracklist_scan_can_cancel_without_a_failed_job_or_new_report(self):
        entered, release = threading.Event(),threading.Event()
        def read(*args):
            set_scan_jobs.progress('Reading published tracklist')
            entered.set();release.wait(5);set_scan_jobs.check()
        with patch.object(set_import,'video_info',side_effect=read):
            task = api.dispatch('sets.scan',{'url':WATCH})
            try:
                self.assertTrue(entered.wait(2))
                progress = api.dispatch('metadata.job',{'id':task['id']})
                self.assertTrue(progress['cancellable'])
                self.assertEqual(progress['progress']['stage'],'Reading published tracklist')
                started = time.monotonic()
                stopped = api.dispatch('sets.cancel_task',{'task_id':task['id']})
                self.assertEqual(stopped['state'],'cancelling')
                self.assertLess(time.monotonic()-started,.5)
            finally:release.set()
            finished = self.finish(task)
        self.assertEqual(finished['state'],'cancelled')
        self.assertIsNone(finished['error'])
        self.assertEqual(api.dispatch('sets.list',{}),[])

    def test_cancelling_forced_audio_keeps_the_published_tracklist(self):
        entered, release = threading.Event(),threading.Event()
        def recognize(*args,**kwargs):
            entered.set();release.wait(5);set_scan_jobs.check()
        info = {'title':'Mix','duration':120,'description':'0:00 Artist - First\n1:00 Artist - Second'}
        with patch.object(set_import,'video_info',return_value=info),patch.object(shazam_scan,'scan',side_effect=recognize):
            task = api.dispatch('sets.scan',{'url':WATCH,'fingerprint':True})
            try:
                self.assertTrue(entered.wait(2));api.dispatch('sets.cancel_task',{'task_id':task['id']})
            finally:release.set()
            finished = self.finish(task)
        self.assertEqual(finished['state'],'cancelled')
        self.assertEqual([row['title'] for row in finished['result']['tracks']],['First','Second'])
        self.assertEqual(len(api.dispatch('sets.list',{})),1)

    def test_cancel_terminates_real_recognition_process_and_keeps_checkpoint(self):
        source = self.audio(duration=30)
        executable = self.root/'recognizer'
        executable.write_text('#!'+sys.executable+'\n'+'''
import json,os,time
from pathlib import Path
target=Path(os.environ['CRATELOOM_SCAN_PROGRESS'])
target.write_text(json.dumps({'progress':{'stage':'Identifying tracks','completed':10,'total':30,'unit':'seconds','samples':1},'partial':{'duration':30,'scanned_until':10,'hits':[{'start':0,'end':10,'title':'First','artist':'Artist','key':'123','samples':1}]}}))
time.sleep(60)
''')
        executable.chmod(0o755)
        with patch.object(shazam_scan,'interpreter',return_value=str(executable)):
            task = api.dispatch('sets.scan',{'path':str(source),'fingerprint':True})
            try:
                deadline=time.monotonic()+3
                while time.monotonic()<deadline:
                    latest=api.dispatch('metadata.job',{'id':task['id']})
                    if latest.get('progress',{}).get('samples')==1:break
                    time.sleep(.02)
                self.assertEqual(latest['progress'].get('samples'),1,latest)
            finally:
                api.dispatch('sets.cancel_task',{'task_id':task['id']})
                finished=self.finish(task)
        self.assertEqual(finished['state'],'cancelled')
        self.assertEqual(finished['result']['scan']['offset'],10)
        self.assertFalse(finished['result']['scan']['automatic'])
        with api.connect() as db:
            self.assertEqual(db.execute('SELECT enabled FROM dj_set_auto_scan WHERE set_id=?',(finished['result']['id'],)).fetchone()[0],0)
        self.assertEqual(finished['result']['tracks'][0]['title'],'First')
        self.assertFalse(shazam_scan.WORKERS)

    def test_cancel_automatic_scan_preserves_fast_samples_and_disables_continuation(self):
        saved=set_import.save_report(api,'Mix',None,[],None,checkpoint=(0,120,'mix.wav',0),plan={'samples':{}})
        entered, release=threading.Event(),threading.Event()
        def recognize(*args,**kwargs):
            control=set_scan_jobs.current()
            control.partial={'duration':120,'scanned_until':0,'hits':[],
                             'plan':{'samples':{'0':{'key':'1','title':'First','artist':'Artist'}}}}
            control.publish('Identifying tracks',completed=1,total=None,unit='samples')
            entered.set();release.wait(5);control.check()
        with patch.object(shazam_scan,'scan',side_effect=recognize):
            thread=threading.Thread(target=set_import.auto_scan_step,args=(api,));thread.start()
            try:
                self.assertTrue(entered.wait(2))
                live=api.dispatch('sets.get',{'id':saved['id']})
                self.assertEqual(live['scan']['progress']['completed'],1)
                stopped=api.dispatch('sets.cancel_scan',{'id':saved['id']})
                self.assertFalse(stopped['scan']['automatic'])
            finally:release.set();thread.join(3)
        self.assertFalse(thread.is_alive())
        result=api.dispatch('sets.get',{'id':saved['id']})
        self.assertEqual(result['scan']['samples'],1)
        self.assertEqual(result['scan']['pending_matches'],1)
        self.assertFalse(result['scan']['automatic'])
        with api.connect() as db:
            db.execute('DELETE FROM dj_set_auto_scan WHERE set_id=?',(saved['id'],))
        api.dispatch('sets.cancel_scan',{'id':saved['id']})
        with api.connect() as db:
            self.assertEqual(db.execute('SELECT enabled FROM dj_set_auto_scan WHERE set_id=?',(saved['id'],)).fetchone()[0],0)

    def test_playlist_cancellation_keeps_read_pages(self):
        entered, release=threading.Event(),threading.Event()
        def read(url,token):
            if '/items?' not in url:return {'name':'Playlist'}
            if 'offset=0' in url:
                return {'items':[{'item':{'type':'track','name':'First','artists':[{'name':'Artist'}]}}],'next':'next','total':100}
            entered.set();release.wait(5);set_scan_jobs.check()
        with patch.object(set_import,'SPOTIFY_TOKEN','test-token'),patch.object(playlist_sources,'spotify_api',side_effect=read):
            task=api.dispatch('sets.scan',{'url':'https://open.spotify.com/playlist/31aTChbbcTbY3Sei4kb3nu'})
            try:
                self.assertTrue(entered.wait(2));api.dispatch('sets.cancel_task',{'task_id':task['id']})
            finally:release.set()
            result=self.finish(task)
        self.assertEqual(result['state'],'cancelled')
        self.assertEqual(result['result']['tracks'][0]['title'],'First')

    def test_cancel_before_automatic_worker_registers_prevents_it_starting(self):
        saved=set_import.save_report(api,'Mix',None,[],None,checkpoint=(0,120,'mix.wav',0),plan={'samples':{}})
        entered,release=threading.Event(),threading.Event()
        constructor=set_scan_jobs.ScanControl
        def create(*args):
            entered.set();release.wait(3);return constructor(*args)
        with patch.object(set_scan_jobs,'ScanControl',side_effect=create),patch.object(set_import,'resume_scan') as resume:
            thread=threading.Thread(target=set_import.auto_scan_step,args=(api,));thread.start()
            try:
                self.assertTrue(entered.wait(2))
                api.dispatch('sets.cancel_scan',{'id':saved['id']})
            finally:release.set();thread.join(3)
        resume.assert_not_called()

    def test_public_page_fallback_recovers_tracklist_without_audio(self):
        html='var ytInitialPlayerResponse = '+json.dumps({'videoDetails':{'videoId':'RvRhUHTV_8k','title':'Mix','lengthSeconds':'120','shortDescription':'0:00 Artist - First\n1:00 Artist - Second'}})+';'
        class Response(io.BytesIO):
            def __enter__(self):return self
            def __exit__(self,*args):self.close()
        with patch.object(youtube_audio,'executable',return_value=None),patch.object(download_http,'open_url',return_value=Response(html.encode())),patch.object(shazam_scan,'scan') as audio:
            result=self.finish(api.dispatch('sets.scan',{'url':WATCH}))
        self.assertEqual(result['state'],'completed')
        self.assertEqual(len(result['result']['tracks']),2)
        audio.assert_not_called()

    def test_page_parser_checks_identity_and_recovers_chapters(self):
        player={'videoDetails':{'videoId':'RvRhUHTV_8k','title':'Mix','lengthSeconds':'180'}}
        chapter={'chapterRenderer':{'timeRangeStartMillis':60000,'title':{'simpleText':'Artist - Second'}}}
        html='ytInitialPlayerResponse = '+json.dumps(player)+'; ytInitialData = '+json.dumps({'nested':[chapter]})+';'
        result=youtube_tracklist.from_page(html,'RvRhUHTV_8k')
        self.assertEqual(result['chapters'],[{'start_time':60,'title':'Artist - Second'}])
        with self.assertRaises(ValueError):youtube_tracklist.from_page(html,'HHloSWHKdvw')

    def test_page_description_recovery_does_not_require_an_audio_extractor(self):
        player={'videoDetails':{'videoId':'RvRhUHTV_8k','title':'Mix','lengthSeconds':'180'}}
        initial={'engagementPanels':[{'attributedDescription':{'content':'1. [00:00] Artist - First\n2. Artist - Second [01:00]\n3. 02:00 Artist - Third'}}]}
        html='ytInitialPlayerResponse = '+json.dumps(player)+'; ytInitialData = '+json.dumps(initial)+';'
        data=youtube_tracklist.from_page(html,'RvRhUHTV_8k')
        with patch.object(youtube_tracklist,'read',return_value=data),patch.object(api,'import_process',side_effect=AssertionError('Metadata should not need an audio client')):
            recovered=set_import.video_info(api,WATCH)
        rows=set_import.parse_tracklist(recovered['description'],'Video description')
        self.assertEqual([row['title'] for row in rows],['First','Second','Third'])
        self.assertEqual([row['at'] for row in rows],[0,60,120])

    def test_coherent_comment_tracklist_is_kept_with_explicit_evidence(self):
        page={'id':'RvRhUHTV_8k','title':'Mix','duration':180,'description':''}
        comments=['1:00 Artist - A favourite song',
                  '0:00 Artist - Wrong\n1:00 Artist - Time\n1:00 Artist - Repeated',
                  '00:00 Artist - First\n01:00 Artist - Second\n02:00 Artist - Third']
        with patch.object(youtube_tracklist,'read',return_value=page),patch.object(youtube_tracklist,'read_comments',return_value=comments),patch.object(shazam_scan,'scan',side_effect=AssertionError('Published list is enough')):
            result=self.finish(api.dispatch('sets.scan',{'url':WATCH}))
        self.assertEqual(result['state'],'completed')
        self.assertEqual([row['title'] for row in result['result']['tracks']],['First','Second','Third'])
        self.assertEqual(result['result']['tracks'][0]['evidence'],['Published comment tracklist'])

    def test_named_chapters_survive_when_audio_cannot_be_obtained(self):
        page={'title':'Mix','duration':180,'chapters':[{'start_time':0,'title':'First song'},{'start_time':90,'title':'Second song'}]}
        with patch.object(set_import,'video_info',return_value=page),patch.object(shazam_scan,'scan',side_effect=ValueError('Audio unavailable')):
            result=self.finish(api.dispatch('sets.scan',{'url':WATCH}))
        self.assertEqual(result['state'],'completed')
        self.assertEqual([row['title'] for row in result['result']['tracks']],['First song','Second song'])
        self.assertIn('Audio unavailable',result['result']['warning'])

    def test_successful_audio_with_no_recognition_is_not_reported_as_fetch_failure(self):
        page={'title':'Mix','duration':180,'description':''}
        with patch.object(set_import,'video_info',return_value=page),patch.object(shazam_scan,'scan',return_value={'duration':180,'hits':[]}):
            result=self.finish(api.dispatch('sets.scan',{'url':WATCH}))
        self.assertEqual(result['state'],'completed')
        self.assertTrue(result['result']['tracks'][0]['unknown'])
        self.assertIsNone(result['result']['scan']['offset'])
        self.assertIn('audio was scanned successfully',result['result']['warning'])

    def test_recognition_keeps_named_chapters_that_have_no_artist_credit(self):
        page={'title':'Mix','duration':180,'chapters':[{'start_time':0,'title':'First song'},{'start_time':90,'title':'Second song'}]}
        audio={'duration':180,'hits':[{'start':0,'end':30,'key':'123','artist':'Artist','title':'Different fingerprint','samples':1}]}
        with patch.object(set_import,'video_info',return_value=page),patch.object(shazam_scan,'scan',return_value=audio) as recognize:
            result=self.finish(api.dispatch('sets.scan',{'url':WATCH}))
        self.assertEqual(result['state'],'completed')
        titles=[row['title'] for row in result['result']['tracks']]
        self.assertIn('First song',titles);self.assertIn('Second song',titles)
        self.assertEqual(recognize.call_args.kwargs,{'batch':False})

    def test_extractor_tracklist_identity_must_match_requested_video(self):
        response=SimpleNamespace(stdout=json.dumps({'id':'HHloSWHKdvw','description':'0:00 Artist - Wrong'}))
        with patch.object(youtube_tracklist,'read',side_effect=ValueError('No page')),patch.object(api,'import_process',return_value=response):
            with self.assertRaisesRegex(ValueError,'published tracklist'):set_import.video_info(api,WATCH)

    def test_session_is_explicit_scoped_and_saved_without_cookies(self):
        info={'title':'Mix','duration':120,'description':'0:00 Artist - First\n1:00 Artist - Second'}
        def read(*args):
            self.assertEqual(set_scan_jobs.current().youtube_browser,'firefox')
            self.assertIn('--cookies-from-browser',youtube_audio.runtime_args())
            return info
        with patch.object(set_import,'video_info',side_effect=read):
            result=self.finish(api.dispatch('sets.scan',{'url':WATCH,'youtube_browser':'firefox'}))
        self.assertEqual(result['state'],'completed')
        self.assertNotIn('--cookies-from-browser',youtube_audio.runtime_args())
        with api.connect() as db:
            self.assertEqual(db.execute('SELECT browser FROM dj_set_youtube_access WHERE set_id=?',(result['result']['id'],)).fetchone()[0],'firefox')
            db.execute('INSERT INTO dj_set_scan VALUES (?,?,?,?,?)',(result['result']['id'],0,120,None,0))
        def recognize(*args,**kwargs):
            self.assertEqual(set_scan_jobs.current().youtube_browser,'firefox')
            return {'duration':120,'hits':[]}
        with patch.object(shazam_scan,'scan',side_effect=recognize):
            resumed=self.finish(api.dispatch('sets.resume_scan',{'id':result['result']['id']}))
        self.assertEqual(resumed['state'],'completed')
        self.assertNotIn('--cookies-from-browser',youtube_audio.runtime_args())
        with self.assertRaisesRegex(ValueError,'browser session'):api.dispatch('sets.scan',{'url':WATCH,'youtube_browser':'arbitrary/path'})

    def test_muxed_public_stream_is_remuxed_to_audio_without_reencoding(self):
        source=self.root/'mix.mp4'
        subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','color=size=32x32:duration=2','-f','lavfi','-i','sine=duration=2','-c:v','mpeg4','-c:a','aac','-shortest',str(source)],check=True)
        audio=scan_audio.isolate_audio(api,source)
        self.assertEqual(audio.name,'mix.m4a')
        self.assertFalse(source.exists())
        self.assertGreater(scan_audio.validate(api,audio,expected=2),1.9)

    def test_an_age_warning_on_one_client_does_not_stop_other_connections(self):
        original=self.audio().read_bytes()
        attempts=[]
        def fallback(api,watch,folder,check,client=None):
            attempts.append(client)
            if len(attempts)==1:
                raise ValueError('Age-restricted client response')
            path=folder/'mix.m4a';path.write_bytes(original);return path,2
        with patch.object(youtube_audio,'resolve',side_effect=ValueError('Age-restricted client response')),patch.object(scan_audio,'segmented',side_effect=fallback) as alternate:
            self.assertTrue(scan_audio.obtain(api,WATCH,lambda:None).is_file())
        self.assertEqual(alternate.call_count,2)
        self.assertEqual(attempts,[None,'web_safari'])

    def test_access_classification_uses_final_error_instead_of_client_warnings(self):
        error=subprocess.CalledProcessError(1,['extractor'],stderr='WARNING: age-restricted client skipped\nERROR: Sign in to confirm you are not a bot')
        self.assertEqual(youtube_audio.failure_kind(error),'challenge')
        self.assertEqual(youtube_audio.failure_kind(ValueError('Video unavailable. Try again later.')),'connection')
        self.assertEqual(youtube_audio.failure_kind(ValueError('This video is not available in your country')),'region')

    def test_unreadable_browser_session_is_its_own_failure_kind(self):
        for detail in ('ERROR: could not find chrome cookies database in "/Users/someone/Library/Application Support/Google/Chrome"',
                       "ERROR: could not find firefox cookies database in '/Users/someone/Library/Application Support/Firefox'",
                       "ERROR: [Errno 1] Operation not permitted: '/Users/someone/Library/Containers/com.apple.Safari/Data/Library/Cookies/Cookies.binarycookies'",
                       'WARNING: cannot decrypt v10 cookies: no key found\nERROR: [youtube] abc: Sign in to confirm your age. Use --cookies-from-browser or --cookies for the authentication.'):
            error=subprocess.CalledProcessError(1,['extractor'],stderr=detail)
            self.assertEqual(youtube_audio.failure_kind(error),'session',detail)
        signed_out=subprocess.CalledProcessError(1,['extractor'],stderr='ERROR: [youtube] abc: Sign in to confirm your age. This video may be inappropriate for some users. Use --cookies-from-browser or --cookies for the authentication. See https://github.com/yt-dlp/yt-dlp/wiki/FAQ#how-do-i-pass-cookies-to-yt-dlp for how to manually pass cookies.')
        self.assertEqual(youtube_audio.failure_kind(signed_out),'age')
        line=youtube_audio.diagnostic(subprocess.CalledProcessError(1,['extractor'],stderr=f'WARNING: noise\nERROR: could not open {Path.home()}/Library/x at https://rr1.googlevideo.com/videoplayback?expire=1&sig=secret'))
        self.assertEqual(line,'ERROR: could not open ~/Library/x at https://rr1.googlevideo.com/videoplayback')

    def test_unreadable_session_stops_early_and_names_the_browser(self):
        control=set_scan_jobs.ScanControl(api);control.youtube_browser='chrome'
        failure=subprocess.CalledProcessError(1,['extractor'],stderr='ERROR: could not find chrome cookies database in "/somewhere"')
        with control.activate(),patch.object(youtube_audio,'resolve',side_effect=AssertionError('Anonymous routes cannot use a session')),patch.object(scan_audio,'segmented',side_effect=failure) as native:
            with self.assertRaises(ValueError) as caught:scan_audio.obtain(api,WATCH,lambda:None)
        self.assertEqual(native.call_count,1)
        self.assertIn('could not read the YouTube session from Chrome',str(caught.exception))
        self.assertNotIn('public audio connections',str(caught.exception))
        saved=json.loads((self.root/'set-audio'/'RvRhUHTV_8k'/'attempts.json').read_text())
        self.assertEqual(saved['browser'],'chrome')
        self.assertEqual(saved['attempts'],[{'attempt':1,'route':'native','result':'session','detail':'ERROR: could not find chrome cookies database in "/somewhere"'}])

    def test_selected_session_uses_only_session_capable_routes(self):
        control=set_scan_jobs.ScanControl(api);control.youtube_browser='firefox'
        clients=[]
        def native(api,watch,folder,check,client=None):
            clients.append(client)
            raise subprocess.CalledProcessError(1,['extractor'],stderr='ERROR: [youtube] RvRhUHTV_8k: Sign in to confirm your age. This video may be inappropriate for some users.')
        with control.activate(),patch.object(youtube_audio,'resolve',side_effect=AssertionError('Anonymous routes cannot use a session')),patch.object(scan_audio,'segmented',side_effect=native):
            with self.assertRaises(ValueError) as caught:scan_audio.obtain(api,WATCH,lambda:None)
        self.assertEqual(clients,[None,'web'])
        self.assertIn('age verification with the Firefox session',str(caught.exception))
        public=subprocess.CalledProcessError(1,['extractor'],stderr='ERROR: [youtube] RvRhUHTV_8k: Sign in to confirm your age.')
        with patch.object(youtube_audio,'resolve',side_effect=public),patch.object(scan_audio,'segmented',side_effect=public):
            with self.assertRaisesRegex(ValueError,'Choose your signed-in browser under YouTube access'):scan_audio.obtain(api,WATCH,lambda:None)

    def test_audio_falls_back_to_m4a_and_reuses_verified_cache(self):
        original=self.audio('original.m4a').read_bytes()
        review={'filename':'Mix.m4a','duration':2,'declared_bytes':len(original),'url':WATCH}
        def transfer(self,jid,source,url,headers,target,check,**kwargs):
            check();target.write_bytes(original)
        with patch.object(scan_audio,'segmented',side_effect=ValueError('Native transfer unavailable')),patch.object(youtube_audio,'resolve',side_effect=[ValueError('Opus unavailable'),(review,'https://media.googlevideo.com/audio',{})]) as resolve,patch.object(scan_audio.ScanTransfer,'transfer',new=transfer):
            source=scan_audio.obtain(api,WATCH,lambda:None)
        self.assertEqual(resolve.call_args_list[0].kwargs,{'scan':True})
        self.assertEqual(resolve.call_args.kwargs,{})
        self.assertEqual(source.suffix,'.m4a')
        with patch.object(youtube_audio,'resolve',side_effect=AssertionError('Must reuse cache')):
            self.assertEqual(scan_audio.obtain(api,WATCH,lambda:None),source)

    def test_native_transfer_is_preferred_and_validates_the_same_video(self):
        original=self.audio().read_bytes()
        def segmented(api,watch,folder,check,client=None):
            self.assertEqual(watch,WATCH)
            path=folder/'mix.m4a';path.write_bytes(original);return path,2
        with patch.object(youtube_audio,'resolve',side_effect=AssertionError('Native transfer should run first')) as resolve,patch.object(scan_audio,'segmented',side_effect=segmented) as fallback:
            source=scan_audio.obtain(api,WATCH,lambda:None)
        resolve.assert_not_called()
        self.assertTrue(source.is_file())
        self.assertEqual(fallback.call_count,1)
        with self.assertRaisesRegex(ValueError,'length'):scan_audio.validate(api,source,expected=60)

    def test_segmented_command_keeps_progress_and_audio_only_restrictions(self):
        folder=self.root/'download';folder.mkdir()
        audio=self.audio().read_bytes()
        def run(api,args,timeout,on_line,on_poll):
            self.assertIn('bestaudio[vcodec=none]',args[args.index('--format')+1])
            self.assertIn('--abort-on-unavailable-fragments',args)
            self.assertEqual(args[-1],WATCH)
            path=folder/'mix.m4a';path.write_bytes(audio)
            on_line(json.dumps({'id':'RvRhUHTV_8k','duration':2,'filepath':str(path),'ext':'m4a'}))
        with patch.object(youtube_audio,'executable',return_value='/tools/yt-dlp'),patch.object(set_scan_jobs,'run_process',side_effect=run):
            self.assertEqual(scan_audio.segmented(api,WATCH,folder,lambda:None)[0],folder/'mix.m4a')

    def test_exhausted_connections_report_retry_without_forcing_offline_input(self):
        with patch.object(youtube_audio,'resolve',side_effect=ValueError('Temporary error')),patch.object(scan_audio,'segmented',side_effect=ValueError('Temporary error')) as fallback:
            with self.assertRaises(ValueError) as caught:scan_audio.obtain(api,WATCH,lambda:None)
        self.assertEqual(fallback.call_count,2)
        self.assertIn('retry',str(caught.exception).lower())
        self.assertNotIn('local audio',str(caught.exception).lower())

    def test_download_range_threads_observe_cancellation(self):
        control=set_scan_jobs.ScanControl(api)
        with control.activate():
            transfer=scan_audio.ScanTransfer(api,lambda:None)
        control.cancel()
        control.cancel(closing=True)
        self.assertFalse(control.closing)
        cancelled=[]
        def range_worker():
            try:transfer.check()
            except ImportCancelled:cancelled.append(True)
        worker=threading.Thread(target=range_worker);worker.start();worker.join(1)
        self.assertEqual(cancelled,[True])

    def test_cancelling_scan_is_retained_when_finished_jobs_are_pruned(self):
        jobs={'scan':{'state':'cancelling'},**{str(i):{'state':'completed'} for i in range(151)}}
        with patch.dict(preparation.JOBS,jobs,clear=True),patch.object(preparation.PROVIDER_POOL,'submit'):
            preparation.launch(lambda:None)
            self.assertIn('scan',preparation.JOBS)

    def test_real_worker_algorithms_publish_progress_and_recoverable_partial_results(self):
        source=self.audio(duration=30)
        feedback=self.root/'progress.json'
        class Client:
            def __init__(self,**kwargs):pass
            async def __aenter__(self):return self
            async def __aexit__(self,*args):pass
        class Recognizer:
            def __init__(self,**kwargs):self.core_recognizer=self
            async def recognize_bytes(self,**kwargs):return 'signature'
            async def recognize(self,clip):return {'track':{'key':'123','title':'Song','subtitle':'Artist'}}
            async def send_recognize_request_v2(self,**kwargs):return await self.recognize(None)
        modules={'aiohttp_retry':SimpleNamespace(ExponentialRetry=lambda **kwargs:None),
                 'shazamio':SimpleNamespace(Shazam=Recognizer),'shazamio.client':SimpleNamespace(HTTPClient=Client),
                 'shazamio.exceptions':SimpleNamespace(RateLimited=type('RateLimited',(Exception,),{}))}
        with patch.dict(sys.modules,modules),patch.dict(os.environ,{'CRATELOOM_SCAN_PROGRESS':str(feedback)}),patch.object(shazam_worker,'REQUEST_INTERVAL',0),patch.object(shazam_fast,'REQUEST_INTERVAL',0):
            result=asyncio.run(shazam_worker.scan(source,30,0,30))
            normal=json.loads(feedback.read_text())
            self.assertEqual(normal['progress']['unit'],'seconds')
            self.assertEqual(normal['progress']['completed'],30)
            self.assertEqual(normal['partial']['hits'],result['hits'])
            asyncio.run(shazam_fast.scan(source,30,0,{}))
            fast=json.loads(feedback.read_text())
            self.assertEqual(fast['progress']['unit'],'samples')
            self.assertIsNone(fast['progress']['total'])
            self.assertTrue(fast['partial']['plan']['samples'])


if __name__=='__main__':unittest.main()
