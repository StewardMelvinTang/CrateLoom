from contextlib import ExitStack
import email.message
import io
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from crateloom import __main__ as api
from crateloom import acquisitions, download_http, playlist_sources, preparation, providers, set_import, set_scan_jobs, shazam_fast, shazam_scan, shazam_worker, soundcloud_download


class AudioResponse(io.BytesIO):
    status = 200
    url = 'https://artist.example/track.wav'

    def __init__(self, audio):
        super().__init__(audio)
        self.headers = email.message.Message()
        self.headers['Content-Type'] = 'audio/wav'
        self.headers['Content-Length'] = str(len(audio))


class SetImport(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory())).resolve()
        for name, value in [('ROOT',self.root),('DB',self.root/'library.sqlite3'),('CACHE',self.root/'cache')]:
            self.stack.enter_context(patch.object(api, name, value))
        self.addCleanup(self.close_manager)

    def close_manager(self):
        manager = acquisitions.MANAGERS.pop(str(self.root), None)
        if manager:
            manager.close()

    def finish(self, task):
        deadline = time.monotonic()+8
        while task['state'] == 'running' and time.monotonic() < deadline:
            time.sleep(.02)
            task = api.dispatch('metadata.job', {'id':task['id']})
        self.assertEqual(task['state'], 'completed', task)
        return task['result']

    def settle(self, task):
        deadline = time.monotonic()+8
        while task['state'] in ('running','cancelling') and time.monotonic() < deadline:
            time.sleep(.02)
            task = api.dispatch('metadata.job', {'id':task['id']})
        self.assertNotIn(task['state'], ('running','cancelling'), task)
        return task

    def with_playlist(self, saved):
        playlist = api.dispatch('playlists.save', {'name':'Set','track_ids':[]})
        with api.connect() as db:
            db.execute('UPDATE dj_sets SET playlist_id=? WHERE id=?', (playlist['id'],saved['id']))
        return saved

    def recognizer(self, body):
        executable = self.root/'recognizer'
        executable.write_text('#!'+sys.executable+'\n'+body)
        executable.chmod(0o755)
        return str(executable)

    def fixture(self, name, frequency, *, artist=None, title=None, duration=2):
        dest = self.root/name
        args = ['ffmpeg','-v','error','-f','lavfi','-i',f'sine=frequency={frequency}:duration={duration}']
        if artist:
            args += ['-metadata',f'artist={artist}','-metadata',f'title={title}']
        subprocess.run([*args,str(dest)],check=True)
        return dest

    def test_set_ingest_reuse_download_and_prepare_in_set_order(self):
        reuse = api.dispatch('library.import', {'path':str(self.fixture('existing.wav', 550, artist='Artist', title='Second'))})
        audio = self.fixture('source.wav', 440).read_bytes()
        lookup = {'url':AudioResponse.url,'filename':'track.wav','declared_bytes':len(audio)}
        task = api.dispatch('sets.scan', {'tracklist':
            '00:00 Artist - First https://artist.example/track.wav\n1:08:01 Artist - Second\n1:08:30 ID - ID'})
        report = self.finish(task)
        self.assertEqual([r['at'] for r in report['tracks']], [0,4081,4110])
        self.assertNotIn('listed_links', report['tracks'][0])
        self.assertEqual([r['playback_track_id'] for r in report['tracks']], [None, reuse['id'], None])
        self.assertIsNone(report['tracks'][1]['track_id'])
        with patch.object(set_import, 'reference_for', return_value=None), \
             patch.object(download_http, 'inspect', return_value=lookup), \
             patch.object(download_http, 'open_url', side_effect=lambda *a, **k: AudioResponse(audio)):
            result = self.finish(api.dispatch('sets.prepare', {'id':report['id'],'name':'My Set'}))
            deadline = time.monotonic()+8
            while time.monotonic() < deadline:
                result = api.dispatch('sets.get', {'id':result['id']})
                if result['tracks'][0]['track_id']:
                    break
                time.sleep(.02)
        first, second, unknown = result['tracks']
        self.assertEqual(first['download_state'], 'completed', first)
        self.assertEqual(first['playback_track_id'], first['track_id'])
        self.assertEqual(second['track_id'], reuse['id'])
        self.assertTrue(unknown['unknown'])
        self.assertIsNone(unknown['track_id'])
        self.assertEqual(api.dispatch('playlists.list', {}), [
            {'id':result['playlist_id'],'name':'My Set','track_ids':[first['track_id'],second['track_id']]}
        ])
        self.assertEqual(Path(api.dispatch('track.get', {'id':first['track_id']})['path']).read_bytes(), audio)
        with api.connect() as db:
            track = api.track(db, first['track_id'])
            cache = self.root/'beats.json'
            cache.write_text('{}')
            api.store_analysis(db, track, 'qa', 'key', cache,
                               {'version':api.ANALYSIS_VERSION,'tempo':120,'warnings':[],'beats':[.125,.625,1.125]}, 'beat')
            self.assertEqual(api.track(db, track['id'])['cues'][0]['position_us'], 125000)
        result2 = self.finish(api.dispatch('sets.prepare', {'id':report['id'],'name':'Do not duplicate'}))
        self.assertEqual(result2['playlist_id'], result['playlist_id'])
        self.assertEqual(len(api.dispatch('acquisitions.list', {})),1)

    def test_slow_source_lookup_does_not_block_later_playlist_download(self):
        first=self.fixture('slow.wav',440).read_bytes()
        second=self.fixture('fast.wav',550).read_bytes()
        files={'slow.wav':first,'fast.wav':second}
        release=threading.Event()
        report=self.finish(api.dispatch('sets.scan',{'tracklist':
            '00:00 Artist - Slow https://artist.example/slow.wav\n00:20 Artist - Fast https://artist.example/fast.wav'}))

        def inspect(url):
            name=url.rsplit('/',1)[-1]
            if name=='slow.wav' and not release.wait(5):
                raise ValueError('Timed out waiting for the second track')
            return {'url':url,'filename':name,'declared_bytes':len(files[name])}

        def download(url,**kwargs):
            return AudioResponse(files[url.rsplit('/',1)[-1]])

        with patch.object(set_import,'reference_for',return_value=None), \
             patch.object(download_http,'inspect',side_effect=inspect), \
             patch.object(download_http,'open_url',side_effect=download):
            task=api.dispatch('sets.prepare',{'id':report['id']})
            try:
                deadline=time.monotonic()+4
                while time.monotonic()<deadline:
                    report=api.dispatch('sets.get',{'id':report['id']})
                    if report['tracks'][1]['track_id']:
                        break
                    time.sleep(.02)
                self.assertIsNotNone(report['tracks'][1]['track_id'])
                self.assertIsNone(report['tracks'][0]['track_id'])
            finally:
                release.set()
            self.finish(task)
            deadline=time.monotonic()+4
            while time.monotonic()<deadline:
                report=api.dispatch('sets.get',{'id':report['id']})
                if report['tracks'][0]['track_id']:
                    break
                time.sleep(.02)
        tracks=[item['track_id'] for item in report['tracks']]
        self.assertTrue(all(tracks))
        self.assertEqual(api.dispatch('playlists.list',{})[0]['track_ids'],tracks)

    def test_version_conflict_and_overlaps(self):
        rows = set_import.parse_tracklist('00:00 Artist - Track (DJ Remix)', 'Video description')
        data = {'hits': [
            {'start':2,'end':42,'title':'Track','artist':'Artist','key':'123','samples':4},
            {'start':55,'end':85,'title':'Second','artist':'Another','key':'456','samples':3},
            {'start':85,'end':115,'title':'Second','artist':'Another','key':'456','samples':3},
        ]}
        rows = set_import.merge_tracklists(rows, set_import.fingerprint_tracks(data))
        self.assertEqual([row['title'] for row in rows], ['Track (DJ Remix)','Second'])
        self.assertTrue(rows[0]['needs_review'])
        self.assertEqual(rows[1]['played'], 60)
        self.assertTrue(rows[1]['needs_review'])

    def test_video_search_repairs_credits_and_keeps_the_named_edit(self):
        original = {'id':'LDY_XyxBu8A','title':'SZA - Snooze (Official Video)','channel':'SZA','duration':280}
        row = set_import.parse_tracklist('0:00 Snooze - SZA', 'Video description')[0]
        matches, _ = set_import.video_match(row, [original])
        self.assertEqual(matches[0][2:], ('SZA', 'Snooze'))
        sativa = set_import.parse_tracklist('0:00 Sativa - Jhené Aiko ft. Swae Lee', 'Video description')[0]
        alternate = {'id':'5x-d3pabd5o','title':'Jhené Aiko - Sativa ft. Rae Sremmurd','channel':'Jhené Aiko','duration':348}
        release = dict(alternate, id='3tnb2o-cV_0', title='Jhené Aiko - Sativa ft. Swae Lee (Official Audio)', duration=276)
        matches, _ = set_import.video_match(sativa, [alternate, release])
        self.assertEqual([match[1][-11:] for match in matches], [release['id']])

        row = set_import.parse_tracklist('0:00 All That Matters - Decemberkid Edit - Justin Bieber', 'Video description')[0]
        self.assertEqual((row['artist'], row['title']), ('Justin Bieber', 'All That Matters (Decemberkid Edit)'))
        wrong = {'id':'JC2yu2a9sHk','title':'Justin Bieber - All That Matters','channel':'Justin Bieber','duration':200}
        right = dict(wrong, id='7e5oCXqaB8A', title='Justin Bieber - All That Matters (Decemberkid Edit)')
        matches, options = set_import.video_match(row, [wrong, right])
        self.assertEqual([match[1][-11:] for match in matches], [right['id']])
        self.assertEqual(len(options), 2)
        with patch.object(set_import, 'reference_for', return_value=None), \
             patch.object(set_import.youtube_audio, 'search', return_value=[wrong, right]), \
             patch.object(set_import.youtube_audio, 'resolve', return_value=(
                 {'kind':'direct','url':'https://www.youtube.com/watch?v='+right['id']}, None, {})) as resolve:
            source, error = set_import.source_for(api, row, None)
        self.assertIsNone(error)
        self.assertEqual(source['values']['title'], 'All That Matters (Decemberkid Edit)')
        self.assertTrue(resolve.call_args.args[1][1].endswith(right['id']))

    def test_shazam_windows_merge_adjacent_results_and_keep_gaps(self):
        stream = io.BytesIO(b'\0' * shazam_worker.RATE * 2 * 28)
        hits = []
        tracks = [
            {'title':'First','subtitle':'Artist','key':'123'},
            {'title':'First','subtitle':'Artist','key':'123'},
            {'title':'Second','subtitle':'Artist','key':'456'},
        ]
        spans = list(shazam_worker.windows(stream))
        self.assertEqual([(start,length) for start,length,_ in spans],[(0,12),(10,12),(20,8)])
        for (start,length,clip), track in zip(spans,tracks):
            self.assertEqual(clip[:4],b'RIFF')
            shazam_worker.add_match(hits,start,length,{'track':track})
        self.assertEqual([(hit['start'],hit['end'],hit['samples']) for hit in hits],[(0,20,2),(20,28,1)])
        shazam_worker.add_match(hits,40,12,{'track':tracks[2]})
        self.assertEqual([hit['start'] for hit in hits],[0,20,40])

    def test_mix_without_tracklist_fingerprints_with_ranges_and_gaps(self):
        match = {'start':24,'end':56,'title':'Song','artist':'Artist','key':'123','samples':3}
        with patch.object(set_import, 'video_info', return_value={'title':'Mix','duration':100}), \
             patch.object(shazam_scan, 'scan', return_value={'hits':[match]}) as scan:
            report = self.finish(api.dispatch('sets.scan', {'url':'https://youtu.be/RvRhUHTV_8k'}))
        scan.assert_called_once_with(api,'https://www.youtube.com/watch?v=RvRhUHTV_8k',None)
        self.assertEqual([(r['at'],r['end'],r['unknown']) for r in report['tracks']],
                         [(0,24,True),(24,56,False),(56,100,True)])
        self.assertFalse(report['tracks'][1]['needs_review'])
        self.assertIn('approximate',report['warning'])

    def test_partial_fingerprint_does_not_label_unscanned_audio(self):
        match = {'start':0,'end':10,'title':'Song','artist':'Artist','key':'123','samples':1}
        fixture = self.fixture('mix.wav',440)
        with patch.object(shazam_scan,'scan',return_value={
            'duration':100,'hits':[match],'scanned_until':10,'warning':'Shazam is rate limiting requests.',
        }):
            report = self.finish(api.dispatch('sets.scan',{'path':str(fixture),'fingerprint':True}))
        self.assertEqual(len(report['tracks']),1)
        self.assertTrue(report['tracks'][0]['needs_review'])
        self.assertIn('Partial scan',report['warning'])

    def test_resume_recovers_old_report_and_preserves_existing_playlist_positions(self):
        watch = 'https://www.youtube.com/watch?v=RvRhUHTV_8k'
        first = {'start':0,'end':30,'title':'First','artist':'Artist','key':'123','samples':3}
        rows = set_import.fingerprint_timeline(set_import.fingerprint_tracks({'hits':[first]}),30)
        saved = set_import.save_report(api,'Legacy mix',watch,rows,'Partial scan. Shazam is rate limiting requests.')
        self.assertEqual(saved['scan']['offset'],30)
        local = self.fixture('first.wav',440)
        playlist = api.dispatch('playlists.save',{'name':'Existing playlist','track_ids':[]})
        with api.connect() as db:
            db.execute('UPDATE dj_sets SET playlist_id=? WHERE id=?',(playlist['id'],saved['id']))
        before = self.finish(api.dispatch('sets.add_local',{'id':saved['id'],'position':0,'path':str(local)}))
        self.assertTrue(before['tracks'][0]['track_id'])
        continued = dict(first,start=30,end=40,samples=1)
        later = dict(first,start=40,end=60,title='Second',key='456',samples=2)
        with patch.object(shazam_scan,'scan',return_value={'duration':60,'hits':[continued,later]}) as provider:
            resumed = self.finish(api.dispatch('sets.resume_scan',{'id':saved['id']}))
        provider.assert_called_once_with(api,watch,None,offset=30,expected_duration=None)
        self.assertEqual(resumed['id'],saved['id'])
        self.assertEqual(resumed['playlist_id'],playlist['id'])
        self.assertEqual(resumed['tracks'][0]['track_id'],before['tracks'][0]['track_id'])
        self.assertEqual([(r['position'],r['title'],r['end']) for r in resumed['tracks']],[(0,'First',40),(1,'Second',60)])
        self.assertIsNone(resumed['scan']['offset'])

    def test_rate_limited_scan_saves_offset_without_matches_and_enforces_cooldown(self):
        watch = 'https://www.youtube.com/watch?v=RvRhUHTV_8k'
        with patch.object(set_import,'video_info',return_value={'title':'Mix','duration':100}), \
             patch.object(shazam_scan,'scan',return_value={
                 'duration':100,'hits':[],'scanned_until':10,'cooldown':90,'warning':'Shazam is rate limiting requests.',
             }) as provider:
            saved = self.finish(api.dispatch('sets.scan',{'url':watch}))
            self.assertEqual(saved['scan']['offset'],10)
            with self.assertRaisesRegex(ValueError,'cooling down'):
                set_import.resume_scan(api,saved['id'])
        self.assertEqual(provider.call_count,1)

    def test_auto_scan_waits_and_continues_without_creating_a_second_report(self):
        watch = 'https://www.youtube.com/watch?v=RvRhUHTV_8k'
        saved = set_import.save_report(api,'Mix',watch,[],'Partial scan.',
                                       checkpoint=(30,90,None,time.time()+90))
        sid = saved['id']
        with patch.object(shazam_scan,'scan',side_effect=[
            {'duration':90,'hits':[],'scanned_until':60,'cooldown':90},
            {'duration':90,'hits':[{'start':60,'end':90,'title':'Track','artist':'Artist','key':'123','samples':3}]},
        ]) as provider:
            self.assertFalse(set_import.auto_scan_step(api))
            self.assertEqual(provider.call_count,0)
            for offset in (60,None):
                with api.connect() as db:
                    db.execute('UPDATE dj_set_scan SET retry_at=0 WHERE set_id=?',(sid,))
                self.assertTrue(set_import.auto_scan_step(api))
                report = api.dispatch('sets.get',{'id':sid})
                self.assertEqual(report['scan']['offset'],offset)
            self.assertEqual([call.kwargs['offset'] for call in provider.call_args_list],[30,60])
        self.assertFalse(report['scan']['automatic'])
        self.assertEqual(len(api.dispatch('sets.list',{})),1)
        self.assertEqual(report['tracks'][-1]['title'],'Track')

    def test_auto_scan_pauses_after_repeated_stalls_and_can_be_reenabled(self):
        saved = set_import.save_report(api,'Mix',None,[],'Partial scan.',
                                       checkpoint=(10,100,'mix.wav',0))
        sid = saved['id']
        with patch.object(shazam_scan,'scan',return_value={
            'duration':100,'hits':[],'scanned_until':10,'cooldown':90,'warning':'Rate limited',
        }) as provider:
            for _ in range(set_import.MAX_STALLED_ATTEMPTS):
                with api.connect() as db:
                    db.execute('UPDATE dj_set_scan SET retry_at=0 WHERE set_id=?',(sid,))
                self.assertTrue(set_import.auto_scan_step(api))
            self.assertFalse(set_import.auto_scan_step(api))
            self.assertEqual(provider.call_count,set_import.MAX_STALLED_ATTEMPTS)
        self.assertTrue(api.dispatch('sets.get',{'id':sid})['scan']['stalled'])
        resumed = api.dispatch('sets.auto_scan',{'id':sid,'enabled':True})['scan']
        self.assertTrue(resumed['automatic'])
        self.assertFalse(resumed['stalled'])
        self.assertFalse(api.dispatch('sets.auto_scan',{'id':sid,'enabled':False})['scan']['automatic'])

    def test_fast_scan_refines_changes_and_skips_stable_intervals(self):
        first = {'key':'1','title':'First','artist':'Artist'}
        second = {'key':'2','title':'Second','artist':'Artist'}
        self.assertEqual(shazam_fast.next_samples(0,180,{}),[0,60,120,168])
        samples = {0:first,60:first,120:second,168:second}
        self.assertEqual(shazam_fast.next_samples(0,180,samples),[90])
        samples[90] = second
        self.assertEqual(shazam_fast.next_samples(0,180,samples),[75])
        samples[75] = first
        self.assertEqual(shazam_fast.next_samples(0,180,samples),[])
        self.assertEqual([(r['key'],r['start'],r['end']) for r in shazam_fast.timeline(0,180,samples)],
                         [('1',0,82.5),('2',82.5,180)])

    def test_fast_scan_persists_samples_before_advancing_the_batch_offset(self):
        saved = set_import.save_report(api,'Mix',None,[],None,checkpoint=(0,300,'mix.wav',0),
                                       plan={'samples':{},'serial':False})
        plan = {'samples':{'0':None,'60':None},'serial':True}
        with patch.object(shazam_scan,'scan',return_value={
            'duration':300,'hits':[],'scanned_until':0,'cooldown':90,'plan':plan,'warning':'Rate limited',
        }):
            self.assertTrue(set_import.auto_scan_step(api))
        report = api.dispatch('sets.get',{'id':saved['id']})
        self.assertEqual((report['scan']['offset'],report['scan']['samples']),(0,2))
        self.assertTrue(report['scan']['automatic'])
        with api.connect() as db:
            self.assertEqual(set_import.scan_checkpoint(db,report)['plan'],plan)
            self.assertEqual(db.execute('SELECT failures FROM dj_set_auto_scan').fetchone()[0],0)

    def test_history_deletion_preserves_existing_playlist_and_track(self):
        source = self.fixture('history.wav',440)
        rows = set_import.parse_tracklist('0:00 Artist - Track','Pasted tracklist')
        saved = set_import.save_report(api,'Old mix',None,rows,None,
                                       checkpoint=(0,90,str(source),0),plan={'samples':{}})
        sid = saved['id']
        playlist = api.dispatch('playlists.save',{'name':'Keep playlist','track_ids':[]})
        with api.connect() as db:
            db.execute('UPDATE dj_sets SET playlist_id=? WHERE id=?',(playlist['id'],sid))
        report = self.finish(api.dispatch('sets.add_local',{'id':sid,'position':0,'path':str(source)}))
        tid = report['tracks'][0]['track_id']
        for index in range(51):
            set_import.save_report(api,f'Later mix {index}',None,[],None)
        history = api.dispatch('sets.list',{})
        self.assertEqual(len(history),52)
        self.assertEqual(history[-1]['imported'],1)
        self.assertEqual(api.dispatch('sets.delete',{'id':sid}),{'deleted':sid})
        self.assertEqual(len(api.dispatch('sets.list',{})),51)
        self.assertIn(tid,api.dispatch('playlists.list',{})[0]['track_ids'])
        self.assertTrue(source.is_file())
        with api.connect() as db:
            self.assertFalse(db.execute('SELECT 1 FROM dj_set_auto_scan WHERE set_id=?',(sid,)).fetchone())

    def test_playlist_import_keeps_source_order_and_duplicates_in_report(self):
        source = 'https://music.youtube.com/playlist?list=PL12345678'
        track = playlist_sources.entry('Same title', 'Artist', 'YouTube Music', 'https://music.youtube.com/watch?v=RvRhUHTV_8k', 194)
        with patch.object(playlist_sources, 'extractor', return_value=('List',[track,playlist_sources.entry(None,None,'YouTube Music'),track],None)):
            report = self.finish(api.dispatch('sets.scan', {'url':source}))
        self.assertEqual(report['watch'], source)
        self.assertEqual([(r['position'],r['at'],r['unknown']) for r in report['tracks']],
                         [(0,None,False),(1,None,True),(2,None,False)])

    def test_authorized_spotify_playlist_reads_later_pages(self):
        url = 'https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M'
        first = {'item':{'type':'track','name':'First','artists':[{'name':'Artist'}],'duration_ms':2000}}
        last = {'item':{'type':'track','name':'Last','artists':[{'name':'Artist'}],'duration_ms':3000}}
        with patch.object(playlist_sources, 'spotify_api', side_effect=[
            {'name':'List'}, {'items':[first]*50,'next':'next'}, {'items':[last],'next':None},
        ]) as request:
            title, tracks, warning = playlist_sources.read(api, ('Spotify',url), 'token')
        self.assertEqual((title,len(tracks),tracks[-1]['title'],warning), ('List',51,'Last',None))
        self.assertIn('offset=50', request.call_args.args[0])

    def test_soundcloud_only_uses_creator_enabled_original(self):
        response = {'downloadable':True,'download_url':'https://api.soundcloud.com/tracks/123/download'}
        with patch.object(soundcloud_download, 'request', side_effect=[response,{'location':'https://media.example/original.wav'}]), \
             patch.object(download_http, 'inspect', return_value={'filename':'original.wav'}):
            self.assertEqual(soundcloud_download.resolve('https://soundcloud.com/artist/track')[1], 'https://media.example/original.wav')
        with patch.object(soundcloud_download, 'request', return_value=dict(response, downloadable=False)) as request:
            with self.assertRaisesRegex(ValueError, 'creator'):
                soundcloud_download.resolve('https://soundcloud.com/artist/track')
            self.assertEqual(request.call_count, 1)

    def test_soundcloud_search_requires_exact_downloadable_version(self):
        track = {'title':'Artist - Song (DJ Remix)','downloadable':True,
                 'permalink_url':'https://soundcloud.com/artist/dj-remix','duration':180000}
        alternate = dict(track, title='Artist - Song', permalink_url='https://soundcloud.com/artist/original')
        with patch.object(soundcloud_download, 'request', return_value=[track, alternate]):
            self.assertEqual(soundcloud_download.find('Artist','Song (DJ Remix)',set_import.key,180),track['permalink_url'])
            self.assertIsNone(soundcloud_download.find('Artist','Song (DJ Remix)',set_import.key,210))

    def test_explicit_resume_is_not_held_back_by_the_stall_backoff(self):
        saved = set_import.save_report(api,'Mix',None,[],'Partial scan.',checkpoint=(10,100,'mix.wav',0))
        sid = saved['id']
        cooldown = shazam_scan.RATE_LIMIT_COOLDOWN
        def retry_at(value=None):
            with api.connect() as db:
                if value is not None:
                    db.execute('UPDATE dj_set_scan SET retry_at=? WHERE set_id=?',(value,sid))
                return db.execute('SELECT retry_at FROM dj_set_scan WHERE set_id=?',(sid,)).fetchone()[0]
        with patch.object(shazam_scan,'scan',return_value={
            'duration':100,'hits':[],'scanned_until':10,'cooldown':cooldown,'warning':'Rate limited',
        }) as provider:
            for _ in range(set_import.MAX_STALLED_ATTEMPTS):
                retry_at(0)
                self.assertTrue(set_import.auto_scan_step(api))
            self.assertGreater(retry_at(),time.time()+cooldown*4)
            resumed = api.dispatch('sets.auto_scan',{'id':sid,'enabled':True})['scan']
            self.assertTrue(resumed['automatic'])
            self.assertLessEqual(resumed['retry_at'],time.time()+cooldown)
            self.assertGreater(resumed['retry_at'],time.time()+cooldown-10)
            # A genuine rate-limit cooldown is never shortened.
            genuine = retry_at(time.time()+cooldown/2)
            self.assertEqual(api.dispatch('sets.auto_scan',{'id':sid,'enabled':True})['scan']['retry_at'],genuine)
            api.dispatch('sets.auto_scan',{'id':sid,'enabled':False})
            calls = provider.call_count
            retry_at(time.time()+cooldown*8)
            failed = self.settle(api.dispatch('sets.resume_scan',{'id':sid}))
            wait = int(failed['error'].split('Resume in ')[1].split()[0])
            self.assertTrue(cooldown-10 <= wait <= cooldown,failed)
            self.assertLessEqual(retry_at(),time.time()+cooldown)
            self.assertEqual(provider.call_count,calls)
            retry_at(0)
            self.assertEqual(self.settle(api.dispatch('sets.resume_scan',{'id':sid}))['state'],'completed')
            self.assertEqual(provider.call_count,calls+1)

    def test_add_local_reports_an_unknown_entry_or_position(self):
        rows = set_import.parse_tracklist('0:00 Artist - Track','Pasted tracklist')
        saved = self.with_playlist(set_import.save_report(api,'Mix',None,rows,None))
        source = self.fixture('local.wav',440)
        for target in ({'position':7},{'entry_id':'999999'}):
            with self.subTest(target=target), self.assertRaisesRegex(ValueError,'Set entry not found'):
                api.dispatch('sets.add_local',{'id':saved['id'],'path':str(source),**target})
        with self.assertRaisesRegex(ValueError,'Choose a local audio file'):
            api.dispatch('sets.add_local',{'id':saved['id'],'path':str(source)})
        entry = saved['tracks'][0]['entry_id']
        report = self.finish(api.dispatch('sets.add_local',{'id':saved['id'],'entry_id':entry,'path':str(source)}))
        self.assertTrue(report['tracks'][0]['track_id'])

    def test_apply_recommended_keeps_started_tasks_when_the_request_queue_fills(self):
        rows = []
        for index in range(3):
            row = set_import.listed_item(index*20,f'Artist - Song {index}','Test')
            row['source_options'] = [{'title':f'Artist Song {index} Audio','url':f'https://artist.example/{index}','duration':180}]
            rows.append(row)
        saved = self.with_playlist(set_import.save_report(api,'Set',None,rows,None))
        full = ValueError('The request queue is full. Try again after a few requests finish.')
        started = [{'id':'one','state':'running'},{'id':'two','state':'running'}]
        with patch.object(preparation,'launch',side_effect=[*started,full]) as launch:
            tasks = api.dispatch('sets.apply_recommended',{'id':saved['id']})
        self.assertEqual(launch.call_count,3)
        self.assertEqual([(task['id'],task['entry_id']) for task in tasks],
                         [('one',saved['tracks'][0]['entry_id']),('two',saved['tracks'][1]['entry_id'])])
        with patch.object(preparation,'launch',side_effect=full):
            with self.assertRaisesRegex(ValueError,'queue is full'):
                api.dispatch('sets.apply_recommended',{'id':saved['id']})

    def test_repeated_source_lookup_does_not_duplicate_links(self):
        recording = '11111111-1111-4111-8111-111111111111'
        reference = {'id':'reference','recording_id':recording,'values':{},
                     'source_url':'https://musicbrainz.org/recording/'+recording}
        relations = {'relations':[{'type-id':'other','url':{'resource':'https://soundcloud.com/artist/track'}},
                                  {'type-id':set_import.FREE_DOWNLOAD,'url':{'resource':'https://artist.example/free.wav'}}]}
        shazam = {'label':'Shazam','url':'https://www.shazam.com/track/123'}
        item = set_import.listed_item(0,'Artist - Track','Pasted tracklist')
        item.update(needs_review=False,links=[shazam,dict(shazam)])
        with patch.object(set_import,'reference_for',return_value=reference), \
             patch.object(providers,'musicbrainz',return_value=relations), \
             patch.object(download_http,'inspect',side_effect=ValueError('Unavailable')), \
             patch.object(set_import,'find_video_source',return_value=(None,None)), \
             patch.object(set_import,'find_soundcloud_source',return_value=None):
            for _ in range(3):
                source, error = set_import.source_for(api,item,None)
                self.assertIsNone(source)
        self.assertEqual([link['label'] for link in item['links']],['Shazam','MusicBrainz','SoundCloud','Creator download'])

    def test_deleting_a_report_stops_its_running_scan_cleanly(self):
        for automatic in (False,True):
            with self.subTest(automatic=automatic):
                saved = set_import.save_report(api,'Mix',None,[],None,checkpoint=(0,120,'mix.wav',0),plan={'samples':{}})
                sid = saved['id']
                entered, release = threading.Event(), threading.Event()
                def recognize(*args,**kwargs):
                    control = set_scan_jobs.current()
                    control.partial = {'duration':120,'scanned_until':60,'hits':[]}
                    entered.set();release.wait(5);control.check()
                    return {'duration':120,'hits':[]}
                outcome = []
                with patch.object(shazam_scan,'scan',side_effect=recognize):
                    if automatic:
                        thread = threading.Thread(target=lambda:outcome.append(set_import.auto_scan_step(api)))
                        thread.start()
                    else:
                        task = api.dispatch('sets.resume_scan',{'id':sid})
                    try:
                        self.assertTrue(entered.wait(2))
                        self.assertEqual(api.dispatch('sets.delete',{'id':sid}),{'deleted':sid})
                    finally:
                        release.set()
                    if automatic:
                        thread.join(3)
                        self.assertEqual(outcome,[False])
                    else:
                        task = self.settle(task)
                        self.assertEqual((task['state'],task['error']),('cancelled',None))
                self.assertEqual(api.dispatch('sets.list',{}),[])
                self.assertIsNone(set_scan_jobs.for_report(api,sid))
                with api.connect() as db:
                    for table in ('dj_set_scan','dj_set_scan_work','dj_set_auto_scan','dj_set_items'):
                        self.assertEqual(db.execute(f'SELECT count(*) FROM {table}').fetchone()[0],0,table)

    def test_cancel_task_disables_continuation_before_the_job_publishes_its_report(self):
        entered, release = threading.Event(), threading.Event()
        def scan(*args):
            control = set_scan_jobs.current()
            with control.lock:
                saved = set_import.save_report(api,'Mix',None,[],'Partial scan.',checkpoint=(10,100,'mix.wav',0))
                control.result = saved
            entered.set();release.wait(5)
            return saved
        with patch.object(set_import,'scan',side_effect=scan):
            task = api.dispatch('sets.scan',{'tracklist':'0:00 Artist - Track'})
            try:
                self.assertTrue(entered.wait(2))
                self.assertIsNone(api.dispatch('metadata.job',{'id':task['id']})['result'])
                stopped = api.dispatch('sets.cancel_task',{'task_id':task['id']})
                self.assertFalse(stopped['result']['scan']['automatic'])
            finally:
                release.set()
            finished = self.settle(task)
        self.assertEqual(finished['state'],'cancelled')
        self.assertFalse(api.dispatch('sets.get',{'id':finished['result']['id']})['scan']['automatic'])

    def test_cancel_task_on_a_manual_resume_keeps_the_scan_paused(self):
        saved = set_import.save_report(api,'Mix',None,[],None,checkpoint=(0,120,'mix.wav',0),plan={'samples':{}})
        self.assertTrue(saved['scan']['automatic'])
        entered, release = threading.Event(), threading.Event()
        def recognize(*args,**kwargs):
            control = set_scan_jobs.current()
            control.partial = {'duration':120,'scanned_until':0,'hits':[],
                               'plan':{'samples':{'0':{'key':'1','title':'First','artist':'Artist'}}}}
            entered.set();release.wait(5);control.check()
        with patch.object(shazam_scan,'scan',side_effect=recognize):
            task = api.dispatch('sets.resume_scan',{'id':saved['id']})
            try:
                self.assertTrue(entered.wait(2))
                stopped = api.dispatch('sets.cancel_task',{'task_id':task['id']})
                self.assertFalse(stopped['result']['scan']['automatic'])
            finally:
                release.set()
            self.assertEqual(self.settle(task)['state'],'cancelled')
        report = api.dispatch('sets.get',{'id':saved['id']})
        self.assertFalse(report['scan']['automatic'])
        self.assertEqual(report['scan']['pending_matches'],1)
        self.assertFalse(set_import.auto_scan_step(api))

    def test_whole_mix_scan_keeps_partial_matches_when_the_worker_times_out(self):
        source = self.fixture('mix.wav',440,duration=30)
        tracklist = '0:00 Artist - First\n0:20 Artist - Second'
        reporting = self.recognizer('''
import json,os,time
from pathlib import Path
target=Path(os.environ['CRATELOOM_SCAN_PROGRESS'])
target.write_text(json.dumps({'progress':{'stage':'Identifying tracks','completed':10,'total':30,'unit':'seconds','samples':1},'partial':{'duration':30,'scanned_until':10,'hits':[{'start':0,'end':10,'title':'First','artist':'Artist','key':'123','samples':1}]}}))
time.sleep(60)
''')
        with patch.object(shazam_scan,'interpreter',return_value=reporting), \
             patch.object(shazam_scan,'NEXT_SCAN_AT',0), patch.object(shazam_scan,'WORKER_TIMEOUT',2.5):
            report = self.finish(api.dispatch('sets.scan',{'path':str(source),'fingerprint':True,'tracklist':tracklist}))
        self.assertEqual([(row['title'],row['evidence']) for row in report['tracks']],
                         [('First',['Pasted tracklist','Fingerprint']),('Second',['Pasted tracklist'])])
        self.assertTrue(report['warning'].startswith('Partial scan. The Shazam scan timed out'),report['warning'])
        self.assertFalse(shazam_scan.WORKERS)
        silent = self.recognizer('import time\ntime.sleep(60)\n')
        with patch.object(shazam_scan,'interpreter',return_value=silent), \
             patch.object(shazam_scan,'NEXT_SCAN_AT',0), patch.object(shazam_scan,'WORKER_TIMEOUT',.3):
            report = self.finish(api.dispatch('sets.scan',{'path':str(source),'fingerprint':True,'tracklist':tracklist}))
        self.assertEqual([row['evidence'] for row in report['tracks']],[['Pasted tracklist']]*2)
        self.assertIn('timed out. Try a shorter mix',report['warning'])

    def test_fast_scan_reports_undecodable_audio_in_one_line(self):
        with self.assertRaisesRegex(ValueError,'FFmpeg could not decode the mix audio'):
            shazam_fast.decode(self.root/'missing.wav',0)
        stubs = self.root/'stubs'
        (stubs/'shazamio').mkdir(parents=True)
        (stubs/'aiohttp_retry.py').write_text('def ExponentialRetry(**options):return None\n')
        (stubs/'shazamio'/'__init__.py').write_text('class Shazam:\n    def __init__(self,**options):pass\n')
        (stubs/'shazamio'/'client.py').write_text(
            'class HTTPClient:\n    def __init__(self,**options):pass\n'
            '    async def __aenter__(self):return self\n    async def __aexit__(self,*args):pass\n')
        (stubs/'shazamio'/'exceptions.py').write_text('class RateLimited(Exception):pass\n')
        engine = Path(set_import.__file__).resolve().parent.parent
        worker = subprocess.run([sys.executable,'-m','crateloom.shazam_worker',str(self.root/'missing.wav'),'30','0','10800','fast'],
                                input='{}',capture_output=True,text=True,timeout=60,
                                env=dict(os.environ,PYTHONPATH=os.pathsep.join((str(stubs),str(engine))),PYTHONDONTWRITEBYTECODE='1'))
        self.assertEqual(worker.returncode,1)
        self.assertEqual(worker.stderr.strip(),'FFmpeg could not decode the mix audio.')

    def test_attaching_artwork_uses_the_running_engine_module(self):
        rows = set_import.parse_tracklist('0:00 Artist - Track','Pasted tracklist')
        artwork = self.root/'artwork'/'cover.png'
        artwork.parent.mkdir()
        artwork.write_bytes(b'cached-image')
        rows[0]['artwork_path'] = str(artwork)
        saved = self.with_playlist(set_import.save_report(api,'Mix',None,rows,None))
        source = self.fixture('local.wav',440)
        # Under ``python -m crateloom`` the running module is ``__main__``; importing it by name loads a second copy.
        import crateloom
        with patch.dict(sys.modules,{'crateloom.__main__':None}), patch.dict(vars(crateloom)):
            del crateloom.__main__
            report = set_import.add_local(api,saved['id'],saved['tracks'][0]['entry_id'],str(source))
        track = api.dispatch('track.playback',{'id':report['tracks'][0]['track_id']})
        self.assertEqual(track['metadata']['artwork_path'],str(artwork))

