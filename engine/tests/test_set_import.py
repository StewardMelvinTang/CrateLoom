from contextlib import ExitStack
import email.message
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
from unittest.mock import ANY, patch

from crateloom import __main__ as api
from crateloom import acquisitions, download_http, playlist_sources, preparation, providers, scan_audio, set_discovery, set_import, set_scan_jobs, shazam_fast, shazam_scan, shazam_worker, soundcloud_download, youtube_tracklist
from crateloom.library_imports import ImportCancelled

WATCH = 'https://www.youtube.com/watch?v=RvRhUHTV_8k'


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
        # Source discovery searches the network; test_set_discovery covers it with fakes.
        self.stack.enter_context(patch.object(set_discovery, 'schedule', return_value=0))
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

    def test_playlist_music_video_is_swapped_for_the_audio_only_upload(self):
        mv_id, topic_id, audio_id = 'MVxxxxxxxx1', 'TOPICxxxxx1', 'AUDIOxxxxx1'
        topic = {'id':topic_id,'title':"Ain't No Way (You Won't Love Me)",'channel':'Chris Brown - Topic','duration':203}
        audio = {'id':audio_id,'title':"Chris Brown - Ain't No Way (Official Audio)",'channel':'ChrisBrownVEVO','duration':204}
        listed_video = {'id':mv_id,'title':"Chris Brown - Ain't No Way (Official Video)",'channel':'ChrisBrownVEVO','duration':251}
        def resolved(raw_title, duration, topic_upload=False):
            return {'kind':'direct','duration':duration,'raw_title':raw_title,'topic':topic_upload,'values':{'title':"Ain't No Way",'artist':''}}
        def run(listed, results):
            row = playlist_sources.entry("Ain't No Way", 'Chris Brown', 'YouTube Music', 'https://music.youtube.com/watch?v='+mv_id, listed['duration'])
            pages = {mv_id: resolved(listed['raw_title'], listed['duration'], listed.get('topic', False)),
                     topic_id: resolved(topic['title'], topic['duration'], True), audio_id: resolved(audio['title'], audio['duration'])}
            def resolve(engine, reference, *args, **kwargs):
                vid = reference[2][-11:]
                return dict(pages[vid], url='https://www.youtube.com/watch?v='+vid), None, {}
            with patch.object(set_import.youtube_audio, 'resolve', side_effect=resolve) as resolver, \
                 patch.object(set_import.youtube_audio, 'search', return_value=results) as search:
                source, error = set_import.source_for(api, row, None)
            self.assertIsNone(error)
            return providers.CANDIDATES[source['source_id']]['value']['url'][-11:], row, search, resolver

        # Labelled a music video: the Topic upload is used, and the video stays linked.
        used, row, _, _ = run({'raw_title':listed_video['title'],'duration':251}, [listed_video, audio, topic])
        self.assertEqual(used, topic_id)
        self.assertEqual(row['music_video_skipped'], 'https://music.youtube.com/watch?v='+mv_id)
        self.assertEqual(row['links'], [{'label':'YouTube Music','url':'https://music.youtube.com/watch?v='+mv_id}])
        # Unlabelled but 48 s longer than the song: still a music video.
        used, _, _, _ = run({'raw_title':"Chris Brown - Ain't No Way",'duration':251}, [listed_video, topic])
        self.assertEqual(used, topic_id)
        # Without a Topic upload an "Official Audio" upload does.
        used, _, _, _ = run({'raw_title':listed_video['title'],'duration':251}, [listed_video, audio])
        self.assertEqual(used, audio_id)
        # The same length as the song: the linked video is kept.
        used, _, _, resolver = run({'raw_title':"Chris Brown - Ain't No Way",'duration':205}, [topic])
        self.assertEqual((used, resolver.call_count), (mv_id, 1))
        # Already the audio-only upload: no search at all.
        used, _, search, _ = run({'raw_title':"Ain't No Way",'duration':203,'topic':True}, [topic])
        self.assertEqual(used, mv_id)
        search.assert_not_called()
        # No audio upload found: the linked video is kept rather than guessing.
        used, _, search, _ = run({'raw_title':listed_video['title'],'duration':251}, [listed_video])
        self.assertEqual(used, mv_id)
        # ... after one more search aimed at the audio upload.
        self.assertEqual([call.args[1] for call in search.call_args_list], ["Chris Brown Ain't No Way", "Chris Brown Ain't No Way audio"])

    def test_audio_only_uploads_rank_above_music_videos(self):
        row = playlist_sources.entry('Video Games', 'Lana Del Rey', 'Spotify', None, 282)
        row['source_options'] = [
            {'title':'Lana Del Rey - Video Games (Official Music Video)','url':'https://www.youtube.com/watch?v=aaaaaaaaaaa','duration':290,'verified':True},
            {'title':'Video Games','url':'https://www.youtube.com/watch?v=bbbbbbbbbbb','duration':282,'topic':True},
            {'title':'Lana Del Rey - Video Games','url':'https://www.youtube.com/watch?v=ccccccccccc','duration':282,'verified':True}]
        best = set_import.recommend_sources(row)
        self.assertEqual((best['url'][-11:], best['recommendation_reason']), ('bbbbbbbbbbb', 'Audio-only upload'))
        # A song named "Video Games" is not a music video, and a lyric video carries the song's audio.
        self.assertFalse(set_import.music_video('Lana Del Rey - Video Games', 'Video Games'))
        self.assertFalse(set_import.music_video('Song (Official Lyric Video)', 'Song'))
        self.assertTrue(set_import.music_video('Song (Official Video)', 'Song'))
        self.assertTrue(set_import.music_video('[MV] Song', 'Song'))
        self.assertTrue(set_import.music_video('Song M/V', 'Song'))
        # A catalog length stands in for a missing listed one.
        self.assertEqual(set_import.expected_length({'duration':None,'catalog_duration':203}), 203)

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

    def audio_ready(self, duration=100):
        """A recognizer is installed and the mix audio is already saved locally."""
        mix = self.fixture('downloaded.wav',440)
        self.stack.enter_context(patch.object(shazam_scan,'interpreter',return_value=sys.executable))
        self.stack.enter_context(patch.object(shazam_scan,'video_audio',return_value=mix))
        self.stack.enter_context(patch.object(api,'probe',return_value=(duration,None,None)))
        return mix

    def resume(self, sid):
        with api.connect() as db:
            db.execute('UPDATE dj_set_scan SET retry_at=0 WHERE set_id=?',(sid,))
        self.assertTrue(set_import.auto_scan_step(api))
        return api.dispatch('sets.get',{'id':sid})

    def test_mix_without_tracklist_fingerprints_with_ranges_and_gaps(self):
        self.audio_ready()
        match = {'start':24,'end':56,'title':'Song','artist':'Artist','key':'123','samples':3}
        with patch.object(set_import, 'video_info', return_value={'title':'Mix','duration':100}), \
             patch.object(shazam_scan, 'scan', side_effect=AssertionError('Recognition continues after the report')):
            saved = self.finish(api.dispatch('sets.scan', {'url':'https://youtu.be/RvRhUHTV_8k'}))
        self.assertEqual((saved['tracks'],saved['scan']['offset'],saved['scan']['automatic']),([],0,True))
        with patch.object(shazam_scan, 'scan', return_value={'duration':100,'hits':[match]}) as scan:
            report = self.resume(saved['id'])
        scan.assert_called_once_with(api,'https://www.youtube.com/watch?v=RvRhUHTV_8k',None,offset=0,expected_duration=100,
                                     on_partial=ANY,plan={'samples':{},'serial':False})
        self.assertEqual([(r['at'],r['end'],r['unknown']) for r in report['tracks']],
                         [(0,24,True),(24,56,False),(56,100,True)])
        self.assertFalse(report['tracks'][1]['needs_review'])
        self.assertIn('approximate',report['warning'])

    def test_partial_fingerprint_does_not_label_unscanned_audio(self):
        self.audio_ready()
        match = {'start':0,'end':10,'title':'Song','artist':'Artist','key':'123','samples':1}
        fixture = self.fixture('mix.wav',440)
        saved = self.finish(api.dispatch('sets.scan',{'path':str(fixture),'fingerprint':True}))
        self.assertEqual((saved['name'],saved['scan']['offset'],saved['scan']['duration']),('mix',0,100))
        with patch.object(shazam_scan,'scan',return_value={
            'duration':100,'hits':[match],'scanned_until':10,'warning':'Shazam is rate limiting requests.',
        }) as scan:
            report = self.resume(saved['id'])
        self.assertEqual(scan.call_args.args[2],str(fixture))
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
        provider.assert_called_once_with(api,watch,None,offset=30,expected_duration=None,on_partial=ANY)
        self.assertEqual(resumed['id'],saved['id'])
        self.assertEqual(resumed['playlist_id'],playlist['id'])
        self.assertEqual(resumed['tracks'][0]['track_id'],before['tracks'][0]['track_id'])
        self.assertEqual([(r['position'],r['title'],r['end']) for r in resumed['tracks']],[(0,'First',40),(1,'Second',60)])
        self.assertIsNone(resumed['scan']['offset'])

    def test_rate_limited_scan_saves_offset_without_matches_and_enforces_cooldown(self):
        watch = 'https://www.youtube.com/watch?v=RvRhUHTV_8k'
        self.audio_ready()
        with patch.object(set_import,'video_info',return_value={'title':'Mix','duration':100}), \
             patch.object(shazam_scan,'scan',return_value={
                 'duration':100,'hits':[],'scanned_until':10,'cooldown':90,'warning':'Shazam is rate limiting requests.',
             }) as provider:
            saved = self.resume(self.finish(api.dispatch('sets.scan',{'url':watch}))['id'])
            self.assertEqual(saved['scan']['offset'],10)
            self.assertEqual((saved['scan']['progress']['stage'],saved['scan']['progress']['resume_at']),
                             ('Waiting for Shazam',saved['scan']['retry_at']))
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
            for attempt in range(set_import.MAX_STALLED_ATTEMPTS):
                with api.connect() as db:
                    db.execute('UPDATE dj_set_scan SET retry_at=0 WHERE set_id=?',(sid,))
                self.assertTrue(set_import.auto_scan_step(api))
                if attempt == 0:
                    # The backoff after an attempt without progress is not Shazam's cooldown.
                    waiting = api.dispatch('sets.get',{'id':sid})['scan']['progress']
                    self.assertEqual(waiting['stage'],'Waiting before the next attempt')
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

    def test_apply_recommended_single_job(self):
        rows = []
        for index in range(3):
            row = set_import.listed_item(index*20,f'Artist - Song {index}','Test')
            row['source_options'] = [{'title':f'Artist Song {index} Audio','url':f'https://artist.example/{index}','duration':180}]
            rows.append(row)
        saved = self.with_playlist(set_import.save_report(api,'Set',None,rows,None))
        jobs = []
        def launch(fn):
            jobs.append(fn)
            return {'id':'one','state':'running'}
        with patch.object(preparation,'launch',side_effect=launch):
            tasks = api.dispatch('sets.apply_recommended',{'id':saved['id']})
        self.assertEqual(len(jobs),1)
        self.assertEqual([(task['id'],task['entry_id']) for task in tasks],
                         [('one',track['entry_id']) for track in saved['tracks']])
        index = set_import.build_library_index
        with patch.dict(set_import.INDEX_CACHE,clear=True), patch.object(set_import,'build_library_index',side_effect=index) as built, \
             patch.object(set_import,'source_for',return_value=(None,'Review the suggested versions below.')) as lookup:
            result = jobs[0]()
        self.assertEqual([call.args[2] for call in lookup.call_args_list],[f'https://artist.example/{index}' for index in range(3)])
        self.assertEqual(built.call_count,1)
        self.assertEqual([track['error'] for track in result['tracks']],['Review the suggested versions below.']*3)
        full = ValueError('The request queue is full. Try again after a few requests finish.')
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
        with patch.object(shazam_scan,'interpreter',return_value=sys.executable), \
             patch.object(shazam_scan,'scan',side_effect=AssertionError('The list is published first')):
            saved = self.finish(api.dispatch('sets.scan',{'path':str(source),'fingerprint':True,'tracklist':tracklist}))
        self.assertEqual([row['evidence'] for row in saved['tracks']],[['Pasted tracklist']]*2)
        self.assertEqual((saved['scan']['offset'],saved['recognition']['state']),(0,'running'))
        timed_out = {'duration':30,'scanned_until':10,'hits':[{'start':0,'end':10,'title':'First','artist':'Artist','key':'123','samples':1}],
                     'plan':{'samples':{'5':{'key':'123','title':'First','artist':'Artist'}},'serial':False},
                     'warning':'The Shazam scan timed out before the end of the mix. The matches found so far were kept.'}
        with patch.object(shazam_scan,'scan',return_value=timed_out) as scan:
            report = self.resume(saved['id'])
        self.assertEqual(scan.call_args.args[2],str(source))
        self.assertEqual([(row['title'],row['evidence']) for row in report['tracks']],
                         [('First',['Pasted tracklist','Fingerprint']),('Second',['Pasted tracklist'])])
        self.assertIsNone(report['warning'])
        self.assertTrue(report['recognition']['warning'].startswith('The Shazam scan timed out'),report['recognition'])
        self.assertEqual((report['scan']['offset'],report['recognition']['confirmed']),(10,1))

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
        # The worker reports a classified failure on stdout; older workers used stderr.
        reply = worker.stdout.strip() or '{}'
        if reply.startswith('{') and 'failure' in reply:
            self.assertEqual(__import__('json').loads(reply)['failure']['code'],'decode')
        else:
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

    # Overlapped steps: the report is published before recognition starts.

    def test_comments_and_audio_run_concurrently(self):
        mix = self.fixture('mix.wav',440)
        page = {'id':'RvRhUHTV_8k','title':'Mix','duration':100,'description':''}
        def comments(*args):
            time.sleep(.3)
            return []
        def audio(api_module, watch, check):
            time.sleep(.3)
            return mix
        with patch.object(youtube_tracklist,'read',return_value=page), patch.object(youtube_tracklist,'read_comments',side_effect=comments), \
             patch.object(scan_audio,'obtain',side_effect=audio) as obtain, patch.object(shazam_scan,'interpreter',return_value=sys.executable), \
             patch.object(api,'probe',return_value=(100,None,None)), patch.object(shazam_scan,'scan',side_effect=AssertionError('Not in the scan job')):
            started = time.monotonic()
            saved = set_scan_jobs.ScanControl(api).run(lambda: set_import.scan(api,WATCH,None,'',False))
            elapsed = time.monotonic()-started
        self.assertLess(elapsed,.45)
        self.assertEqual(obtain.call_count,1)
        self.assertEqual((saved['tracks'],saved['scan']['offset'],saved['scan']['duration']),([],0,100))

    def test_comment_list_cancels_prefetch_without_manifest(self):
        page = {'id':'RvRhUHTV_8k','title':'Mix','duration':300,'description':''}
        comments = ['00:00 Artist - First\n01:00 Artist - Second\n02:00 Artist - Third']
        stopped = threading.Event()
        def audio(api_module, watch, check):
            deadline = time.monotonic()+3
            try:
                while time.monotonic() < deadline:
                    check()
                    set_scan_jobs.check()
                    time.sleep(.02)
            except ImportCancelled:
                stopped.set()
                raise
            folder = self.root/'set-audio'/'RvRhUHTV_8k'
            folder.mkdir(parents=True)
            (folder/'source.json').write_text('{}')
        with patch.object(youtube_tracklist,'read',return_value=page), patch.object(youtube_tracklist,'read_comments',return_value=comments), \
             patch.object(scan_audio,'obtain',side_effect=audio) as obtain, patch.object(shazam_scan,'interpreter',return_value=sys.executable):
            saved = set_scan_jobs.ScanControl(api).run(lambda: set_import.scan(api,WATCH,None,'',False))
            self.assertTrue(stopped.wait(2))
        self.assertEqual(obtain.call_count,1)
        self.assertEqual([row['title'] for row in saved['tracks']],['First','Second','Third'])
        self.assertNotIn('scan',saved)
        self.assertFalse((self.root/'set-audio').exists())

    def test_fingerprint_only_report_created_before_recognition(self):
        self.audio_ready(duration=240)
        with patch.object(set_import,'video_info',return_value={'title':'Mix','duration':240}), \
             patch.object(shazam_scan,'scan',side_effect=AssertionError('Not inside the scan job')):
            saved = self.finish(api.dispatch('sets.scan',{'url':WATCH,'fingerprint':False}))
        self.assertEqual((saved['tracks'],saved['scan']['offset'],saved['scan']['automatic'],saved['provisional']),([],0,True,[]))
        self.assertEqual(saved['recognition']['state'],'none')
        with api.connect() as db:
            self.assertEqual(set_import.scan_checkpoint(db,saved)['plan'],{'samples':{},'serial':False})

    # Streaming results within one pass.

    def test_partial_plan_persisted_during_scan_and_resume_skips_samples(self):
        sid = set_import.save_report(api,'Mix',None,[],None,checkpoint=(0,300,'mix.wav',0),plan={'samples':{},'serial':False})['id']
        heard = {'key':'1','title':'First','artist':'Artist'}
        samples = {'0':heard,'60':None}
        def crash(*args, on_partial=None, **kwargs):
            on_partial({'hits':[],'scanned_until':0,'duration':300,'plan':{'samples':samples,'serial':False}})
            with api.connect() as db:
                self.assertEqual(set_import.scan_checkpoint(db,set_import.get_set(db,sid))['plan']['samples'],samples)
            raise ValueError('Audio recognition stopped unexpectedly. Try again.')
        with patch.object(shazam_scan,'scan',side_effect=crash):
            self.assertTrue(set_import.auto_scan_step(api))
        self.assertEqual(api.dispatch('sets.get',{'id':sid})['scan']['samples'],2)
        with patch.object(shazam_scan,'scan',return_value={'duration':300,'hits':[],'plan':{'samples':samples,'serial':False}}) as provider:
            self.resume(sid)
        self.assertEqual(provider.call_args.kwargs['plan']['samples'],samples)

    def test_plan_writes_do_not_trip_moved_forward_guard(self):
        sid = set_import.save_report(api,'Mix',None,[],None,checkpoint=(0,300,'mix.wav',0),plan={'samples':{}})['id']
        def scan(*args, on_partial=None, **kwargs):
            for count in range(1,4):
                on_partial({'plan':{'samples':{str(i*60):None for i in range(count)}}})
            return {'duration':300,'hits':[],'scanned_until':180,'plan':{'samples':{str(i*60):None for i in range(3)}}}
        with patch.object(shazam_scan,'scan',side_effect=scan):
            report = self.finish(api.dispatch('sets.resume_scan',{'id':sid}))
        self.assertEqual((report['scan']['offset'],report['scan']['samples']),(180,3))
        def moved(*args, on_partial=None, **kwargs):
            with api.connect() as db:
                db.execute('UPDATE dj_set_scan SET offset=240 WHERE set_id=?',(sid,))
            return {'duration':300,'hits':[],'plan':{'samples':{}}}
        with patch.object(shazam_scan,'scan',side_effect=moved):
            failed = self.settle(api.dispatch('sets.resume_scan',{'id':sid}))
        self.assertIn('already moved forward',failed['error'])

    def test_resume_racing_the_automatic_pass_starts_from_its_saved_samples(self):
        sid = set_import.save_report(api,'Mix',None,[],None,checkpoint=(0,300,'mix.wav',0),plan={'samples':{}})['id']
        entered, release = threading.Event(), threading.Event()
        answered = {'0':None,'60':None}
        def limited(*args, on_partial=None, **kwargs):
            entered.set()
            release.wait(5)
            return {'duration':300,'hits':[],'scanned_until':0,'cooldown':180,'plan':{'samples':answered},
                    'failure':{'code':'rate_limited'}}
        with patch.object(shazam_scan,'scan',side_effect=limited) as provider:
            automatic = threading.Thread(target=set_import.auto_scan_step,args=(api,))
            automatic.start()
            try:
                self.assertTrue(entered.wait(2))
                # Another automatic attempt never starts a second pass of the same report.
                with self.assertRaises(shazam_scan.ScanBusy):
                    set_import.resume_scan(api,sid)
                # A Resume that got past the running check before the pass registered waits for it,
                # then reads the checkpoint the pass saved instead of its own stale copy.
                manual = set_scan_jobs.start(api,lambda:set_import.resume_scan(api,sid,manual=True))
                time.sleep(.3)
                self.assertEqual(api.dispatch('metadata.job',{'id':manual['id']})['state'],'running')
            finally:
                release.set()
                automatic.join(5)
            failed = self.settle(manual)
        self.assertEqual(provider.call_count,1)
        self.assertRegex(failed['error'],'cooling down')
        with api.connect() as db:
            self.assertEqual(set_import.scan_checkpoint(db,set_import.get_set(db,sid))['plan']['samples'],answered)

    def test_saved_shazam_cooldown_is_the_wait_shown_and_kept_by_resume(self):
        sid = set_import.save_report(api,'Mix',None,[],'Partial scan.',checkpoint=(10,100,'mix.wav',0),plan={'samples':{}})['id']
        shazam_scan.save_cooldown(api,600)
        def retry_at(value):
            with api.connect() as db:
                db.execute('UPDATE dj_set_scan SET retry_at=? WHERE set_id=?',(value,sid))
        with patch.object(shazam_scan,'scan',side_effect=AssertionError('Inside the cooldown')):
            # The report's own retry time has passed, but Shazam's saved cooldown still holds it.
            retry_at(0)
            self.assertFalse(set_import.auto_scan_step(api))
            scan = api.dispatch('sets.get',{'id':sid})['scan']
            self.assertEqual(scan['progress']['stage'],'Waiting for Shazam')
            self.assertGreater(scan['progress']['resume_at'],time.time()+590)
            self.assertEqual(scan['retry_at'],scan['progress']['resume_at'])
            # Resume and re-enabling never shorten it to the 90 s single cooldown.
            retry_at(time.time()+600)
            resumed = api.dispatch('sets.auto_scan',{'id':sid,'enabled':True})['scan']
            self.assertGreater(resumed['retry_at'],time.time()+590)
            failed = self.settle(api.dispatch('sets.resume_scan',{'id':sid}))
            self.assertRegex(failed['error'],r'Resume in (59\d|600) seconds')

    def test_waiting_sampled_scan_reports_how_far_it_has_sampled(self):
        # A sampled pass keeps its offset at the batch start, so the wait must not read "0:00 checked".
        first, second = ({'key':key,'title':title,'artist':'Artist'} for key,title in (('1','First'),('2','Second')))
        plan = {'samples':{'0':first,'60':first,'152':second,'391':None},'serial':False}
        sid = set_import.save_report(api,'Mix',None,[],'Partial scan.',checkpoint=(0,6730,'mix.wav',0),plan=plan)['id']
        shazam_scan.save_cooldown(api,90)
        with patch.object(shazam_scan,'scan',side_effect=AssertionError('Inside the cooldown')):
            self.assertFalse(set_import.auto_scan_step(api))
        progress = api.dispatch('sets.get',{'id':sid})['scan']['progress']
        self.assertEqual(progress['stage'],'Waiting for Shazam')
        self.assertEqual({field:progress[field] for field in ('unit','completed','position','duration','tracks_found')},
                         {'unit':'samples','completed':4,'position':391,'duration':6730,'tracks_found':2})
        self.assertGreater(progress['resume_at'],time.time()+80)
        # The status line under the scan reads the same reach, not the batch's 0:00 offset.
        self.assertEqual(api.dispatch('sets.get',{'id':sid})['scan']['sampled_until'],391)
        # A pass that has not sampled yet still reports its place in seconds.
        self.assertEqual(set_import.waiting_progress({'offset':120,'duration':600},'Waiting for Shazam',1)['unit'],'seconds')

    def test_report_lists_provisional_hits(self):
        first, second, third = ({'key':key,'title':title,'artist':'Artist'} for key,title in (('1','First'),('2','Second'),('3','Third')))
        plan = {'samples':{'0':first,'60':first,'120':second,'180':second},'serial':False}
        sid = set_import.save_report(api,'Mix',None,[],None,checkpoint=(60,300,'mix.wav',0),plan=plan)['id']
        report = api.dispatch('sets.get',{'id':sid})
        self.assertEqual([(row['title'],row['at'],row['samples']) for row in report['provisional']],[('First',60,1),('Second',90,2)])
        entered, release = threading.Event(), threading.Event()
        def scan(*args, **kwargs):
            control = set_scan_jobs.current()
            control.partial = {'hits':[],'scanned_until':60,'duration':300,'plan':{'samples':dict(plan['samples'],**{'240':third})}}
            entered.set()
            release.wait(5)
            return dict(control.partial)
        with patch.object(shazam_scan,'scan',side_effect=scan):
            task = api.dispatch('sets.resume_scan',{'id':sid})
            try:
                self.assertTrue(entered.wait(2))
                live = api.dispatch('sets.get',{'id':sid})
            finally:
                release.set()
            self.settle(task)
        self.assertTrue(live['scan']['running'])
        self.assertEqual([row['title'] for row in live['provisional']],['First','Second','Third'])
        self.assertTrue(all(set(row) == {'at','end','title','artist','samples'} for row in live['provisional']))

    # Worker failure codes become sentences and cooldowns.

    def test_failure_codes_map_to_sentences_and_cooldowns(self):
        cases = {'rate_limited':90,'unreachable':0,'tls':0,'bad_reply':0}
        for code, cooldown in cases.items():
            with self.subTest(code=code):
                sid = set_import.save_report(api,'Mix',None,[],None,checkpoint=(0,300,'mix.wav',0),plan={'samples':{}})['id']
                result = {'duration':300,'hits':[],'scanned_until':0,'cooldown':cooldown,'plan':{'samples':{}},
                          'failure':{'code':code,'detail':'Client: detail'}}
                before = time.time()
                with patch.object(shazam_scan,'scan',return_value=result):
                    self.assertTrue(set_import.auto_scan_step(api))
                report = api.dispatch('sets.get',{'id':sid})
                with api.connect() as db:
                    failures = db.execute('SELECT failures FROM dj_set_auto_scan WHERE set_id=?',(sid,)).fetchone()[0]
                self.assertNotIn('Client: detail',report['warning'])
                if code == 'rate_limited':
                    self.assertEqual(report['warning'],'Partial scan. Shazam is limiting requests from this Mac. The scan continues automatically in about 2 minutes.')
                    self.assertEqual((failures,report['scan']['automatic']),(0,True))
                    self.assertGreaterEqual(report['scan']['retry_at'],before+90)
                    self.assertEqual(report['scan']['progress']['stage'],'Waiting for Shazam')
                elif code == 'unreachable':
                    self.assertIn('could not reach Shazam',report['warning'])
                    self.assertGreaterEqual(report['scan']['retry_at'],before+15)
                    self.assertTrue(report['scan']['automatic'])
                elif code == 'tls':
                    self.assertEqual(report['warning'],'Partial scan. The secure connection to Shazam failed.')
                    self.assertEqual((report['scan']['automatic'],report['scan']['stalled']),(False,True))
                else:
                    self.assertEqual(report['warning'],'Partial scan. Shazam sent a reply CrateLoom could not read.')
                    self.assertEqual((failures,report['scan']['automatic']),(1,True))
                api.dispatch('sets.auto_scan',{'id':sid,'enabled':False})

    # "Recognize audio too" and the merge into a published list.

    def listed_report(self, text, warning=None):
        return set_import.save_report(api,'Mix',WATCH,set_import.parse_tracklist(text,'Video description'),warning)

    def recognized(self, sid, hits, **result):
        with patch.object(shazam_scan,'interpreter',return_value=sys.executable):
            api.dispatch('sets.recognize',{'id':sid})
        with patch.object(shazam_scan,'scan',return_value={'duration':1800,'hits':hits,'plan':{'samples':{},'serial':False},**result}):
            return self.resume(sid)

    def test_recognize_validates_and_queues(self):
        saved = self.listed_report('1:00 Artist - First\n4:00 Artist - Second')
        sid = saved['id']
        self.assertEqual(saved['recognition'],{'state':'none','confirmed':0,'added':0,'warning':None})
        with patch.object(shazam_scan,'interpreter',return_value=None), self.assertRaisesRegex(ValueError,'Install the ShazamIO'):
            api.dispatch('sets.recognize',{'id':sid})
        playlist = set_import.save_report(api,'List','https://music.youtube.com/playlist?list=PL12345678',
                                          set_import.parse_tracklist('1:00 Artist - First','Pasted tracklist'),None)
        audio = set_import.save_report(api,'Audio',WATCH,set_import.fingerprint_tracks({'hits':[
            {'start':0,'end':30,'title':'Song','artist':'Artist','key':'1','samples':2}]}),None)
        with patch.object(shazam_scan,'interpreter',return_value=sys.executable):
            with self.assertRaisesRegex(ValueError,'single YouTube mix'):
                api.dispatch('sets.recognize',{'id':playlist['id']})
            with self.assertRaisesRegex(ValueError,'comes from the mix audio'):
                api.dispatch('sets.recognize',{'id':audio['id']})
            report = api.dispatch('sets.recognize',{'id':sid,'youtube_browser':'firefox'})
            self.assertEqual((report['recognition']['state'],report['scan']['offset'],report['scan']['duration'],
                              report['scan']['automatic'],report['youtube_browser']),('running',0,None,True,'firefox'))
            with api.connect() as db:
                self.assertEqual(set_import.scan_checkpoint(db,report)['plan'],{'samples':{},'serial':False,'hints':[105,285]})
            with self.assertRaisesRegex(ValueError,'unfinished audio scan'):
                api.dispatch('sets.recognize',{'id':sid})
        self.assertEqual(api.dispatch('sets.delete',{'id':sid}),{'deleted':sid})
        with api.connect() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM dj_set_recognition').fetchone()[0],0)

    def test_merge_rules_each_kind(self):
        track = api.dispatch('library.import',{'path':str(self.fixture('first.wav',440,artist='Artist',title='First'))})
        saved = self.with_playlist(self.listed_report(
            '1:00 Artist - First\n4:00 Artist - Second (Club Mix)\n7:00 Artist - Third\n10:00 ID - ID\n13:00 Artist - Fifth'))
        sid, ids = saved['id'], [row['entry_id'] for row in saved['tracks']]
        bandcamp = [{'label':'Bandcamp','url':'https://artist.example/third'}]
        review = json.dumps({'source':{'url':'https://example.org/a.mp3','filename':'a.mp3'},'fields':{'title':'Second','artist':'Artist'}})
        with api.connect() as db:
            playlist = db.execute('SELECT playlist_id FROM dj_sets WHERE id=?',(sid,)).fetchone()[0]
            db.execute('UPDATE dj_set_items SET track_id=? WHERE rowid=?',(track['id'],ids[0]))
            db.execute('INSERT INTO playlist_tracks(playlist_id,track_id,position) VALUES (?,?,0)',(playlist,track['id']))
            db.execute("INSERT INTO acquisitions(id,state,stage,review,created_at,updated_at) VALUES ('job','queued','queued',?,'now','now')",(review,))
            db.execute("UPDATE dj_set_items SET download_id='job' WHERE rowid=?",(ids[1],))
            db.execute("UPDATE dj_set_items SET detail=json_set(detail,'$.links',json(?)) WHERE rowid=?",(json.dumps(bandcamp),ids[2]))
        manual = [ids[1],ids[0],*ids[2:]]
        api.dispatch('sets.reorder',{'id':sid,'entry_ids':manual})
        with api.connect() as db:
            before = [tuple(row) for row in db.execute('SELECT track_id,position FROM playlist_tracks ORDER BY position')]
        hits = [{'start':0,'end':30,'title':'Opening','artist':'Intro Band','key':'900','samples':2},
                {'start':70,'end':160,'title':'First','artist':'Artist','key':'101','samples':3},
                {'start':250,'end':400,'title':'Second','artist':'Artist','key':'102','samples':3},
                {'start':480,'end':560,'title':'Song','artist':'Other','key':'103','samples':2},
                {'start':610,'end':700,'title':'Tune','artist':'New','key':'104','samples':2},
                {'start':1400,'end':1500,'title':'Track','artist':'Late','key':'105','samples':2}]
        set_discovery.schedule.reset_mock()
        report = self.recognized(sid,hits)
        rows = {row['entry_id']:row for row in report['tracks']}
        order = [row['entry_id'] for row in report['tracks']]
        self.assertEqual(order[:5],manual)
        self.assertEqual([rows[entry]['title'] for entry in order[5:]],['Opening','Track'])
        first, second, third, unknown, fifth = (rows[entry] for entry in ids)
        self.assertEqual((first['track_id'],first['evidence']),(track['id'],['Video description','Fingerprint']))
        self.assertEqual(first['links'],[{'label':'Shazam','url':'https://www.shazam.com/track/101'}])
        self.assertEqual(first['recognized'],{'at':70,'played':90})
        self.assertEqual((second['download_id'],second['version_warning'],second['evidence']),('job','Fingerprint matched a different version.',['Video description']))
        self.assertEqual((third['links'],third['recognized_other']),(bandcamp,[{'at':480,'title':'Song','artist':'Other'}]))
        self.assertEqual((unknown['title'],unknown['artist'],unknown['unknown'],unknown['needs_review']),('Tune','New',False,True))
        self.assertNotIn('recognized',fifth)
        self.assertTrue(all(rows[entry]['needs_review'] and rows[entry]['evidence'] == ['Fingerprint'] for entry in order[5:]))
        self.assertEqual((report['recognition']['state'],report['recognition']['confirmed'],report['recognition']['added']),('complete',1,2))
        self.assertIsNone(report['scan']['offset'])
        with api.connect() as db:
            self.assertEqual([tuple(row) for row in db.execute('SELECT track_id,position FROM playlist_tracks ORDER BY position')],before)
        set_discovery.schedule.assert_called_once_with(api,sid,[int(ids[3]),int(order[5]),int(order[6])])
        # A list the owner has not reordered keeps its time order.
        plain = self.listed_report('1:00 Artist - First\n4:00 Artist - Second')
        report = self.recognized(plain['id'],hits[:1])
        self.assertEqual([row['title'] for row in report['tracks']],['Opening','First','Second'])

    def test_merge_idempotent_on_double_resume(self):
        sid = self.listed_report('1:00 Artist - First')['id']
        first = {'start':70,'end':160,'title':'First','artist':'Artist','key':'101','samples':3}
        new = {'start':890,'end':900,'title':'Tune','artist':'New','key':'104','samples':2}
        self.recognized(sid,[first,new],scanned_until=900)
        later = [dict(new,start=905,end=990),{'start':1400,'end':1500,'title':'Track','artist':'Late','key':'105','samples':2}]
        for _ in range(2):
            with patch.object(shazam_scan,'scan',return_value={'duration':1800,'hits':later,'plan':{'samples':{}}}):
                report = self.resume(sid)
            with api.connect() as db:
                # Replay the same pass, as if the worker's answer arrived twice.
                db.execute('UPDATE dj_set_scan SET offset=900 WHERE set_id=?',(sid,))
                db.execute('UPDATE dj_set_recognition SET completed_at=NULL WHERE set_id=?',(sid,))
                db.execute('UPDATE dj_set_auto_scan SET enabled=1 WHERE set_id=?',(sid,))
        self.assertEqual([row['title'] for row in report['tracks']],['First','Tune','Track'])
        self.assertEqual((report['recognition']['confirmed'],report['recognition']['added']),(1,2))

    def test_merge_warning_not_written_to_set(self):
        sid = self.listed_report('1:00 Artist - First','Published note.')['id']
        report = self.recognized(sid,[],scanned_until=0,cooldown=120,failure={'code':'rate_limited','detail':'RateLimited'})
        self.assertEqual(report['warning'],'Published note.')
        self.assertEqual(report['recognition']['warning'],
                         'Shazam is limiting requests from this Mac. The scan continues automatically in about 2 minutes.')
        self.assertEqual((report['recognition']['state'],report['scan']['automatic']),('running',True))

    def test_auto_step_does_not_double_start_merge(self):
        sid = self.listed_report('1:00 Artist - First')['id']
        with patch.object(shazam_scan,'interpreter',return_value=sys.executable):
            api.dispatch('sets.recognize',{'id':sid})
        entered, release = threading.Event(), threading.Event()
        def scan(*args, **kwargs):
            entered.set()
            release.wait(5)
            return {'duration':1800,'hits':[{'start':70,'end':160,'title':'First','artist':'Artist','key':'101','samples':3}],'plan':{'samples':{}}}
        with patch.object(shazam_scan,'scan',side_effect=scan) as provider:
            worker = threading.Thread(target=set_import.auto_scan_step,args=(api,))
            worker.start()
            try:
                self.assertTrue(entered.wait(2))
                self.assertFalse(set_import.auto_scan_step(api))
                with patch.object(shazam_scan,'interpreter',return_value=sys.executable), self.assertRaisesRegex(ValueError,'already running'):
                    api.dispatch('sets.recognize',{'id':sid})
            finally:
                release.set()
                worker.join(3)
        self.assertEqual(provider.call_count,1)
        self.assertEqual(api.dispatch('sets.get',{'id':sid})['recognition']['confirmed'],1)


    # Reviewed findings: kept prefetch, concurrent writes during prepare, current credits.

    def held_prefetch(self, stopped):
        def audio(api_module, watch, check):
            deadline = time.monotonic()+5
            try:
                while time.monotonic() < deadline:
                    check()
                    set_scan_jobs.check()
                    time.sleep(.02)
            except ImportCancelled:
                stopped.set()
                raise
            raise AssertionError('The prefetch was never stopped.')
        return audio

    def test_kept_prefetch_stops_with_the_report_and_on_scan_errors(self):
        # Titles without artists: no usable published list, so the audio is fetched and kept for recognition.
        page = {'id':'RvRhUHTV_8k','title':'Mix','duration':600,'description':'00:00 Spend Dat Donk\n01:00 Second Song Here\n02:00 Third Song Here'}
        for ending in ('cancel', 'delete', 'too many'):
            with self.subTest(ending):
                stopped = threading.Event()
                with patch.object(youtube_tracklist,'read',return_value=page), patch.object(youtube_tracklist,'read_comments',return_value=[]), \
                     patch.object(scan_audio,'obtain',side_effect=self.held_prefetch(stopped)), patch.object(shazam_scan,'interpreter',return_value=sys.executable), \
                     patch.object(set_import,'MAX_TRACKS',2 if ending == 'too many' else 150):
                    if ending == 'too many':
                        with self.assertRaisesRegex(ValueError,'more than 2 track entries'):
                            set_scan_jobs.ScanControl(api).run(lambda: set_import.scan(api,WATCH,None,'',False))
                        self.assertTrue(stopped.wait(2))
                        continue
                    saved = set_scan_jobs.ScanControl(api).run(lambda: set_import.scan(api,WATCH,None,'',False))
                    self.assertEqual(saved['recognition']['state'],'running')
                    # The scan job has ended; its download keeps going for the queued recognition pass.
                    self.assertFalse(stopped.wait(.3))
                    with api.connect() as db:
                        if ending == 'cancel':
                            set_import.cancel_scan(api,db,saved['id'])
                        else:
                            set_import.delete_report(db,saved['id'])
                    self.assertTrue(stopped.wait(2))
                    deadline = time.monotonic()+2
                    while set_import.PREFETCHES and time.monotonic() < deadline:
                        time.sleep(.02)
                    self.assertEqual(set_import.PREFETCHES,{})

    def test_prepare_keeps_links_and_credits_written_during_its_lookups(self):
        saved = set_import.save_report(api,'Mix',None,set_import.parse_tracklist('00:00 Artist - Song','Pasted tracklist',include_uncredited=True),None)
        sid = saved['id']
        entry = int(saved['tracks'][0]['entry_id'])
        def lookup(api_module, item, explicit, watch=None, discovered=None):
            # Recognition confirms the row and corrects its credit while this lookup runs.
            with api.connect_existing() as db:
                set_discovery.merge_detail(db,sid,entry,None,{'artist':'Corrected Artist','links':[{'label':'Shazam','url':'https://www.shazam.com/track/1'}]},linked=True)
            item['artist'] = 'Lookup Artist'
            set_import.add_link(item,'MusicBrainz','https://musicbrainz.org/recording/1')
            return None, 'No source found.'
        with patch.object(set_import,'source_for',side_effect=lookup):
            set_import.prepare_set(api,sid,'Set')
        with api.connect() as db:
            detail = json.loads(db.execute('SELECT detail FROM dj_set_items WHERE rowid=?',(entry,)).fetchone()[0])
        self.assertEqual(detail['artist'],'Corrected Artist')
        self.assertEqual(sorted(link['url'] for link in detail['links']),['https://musicbrainz.org/recording/1','https://www.shazam.com/track/1'])
        # Without a concurrent write the lookup's credit and links are stored as before.
        saved = set_import.save_report(api,'Mix',None,set_import.parse_tracklist('00:00 Artist - Other','Pasted tracklist',include_uncredited=True),None)
        def plain(api_module, item, explicit, watch=None, discovered=None):
            item['title'] = 'Other (Original Mix)'
            set_import.add_link(item,'MusicBrainz','https://musicbrainz.org/recording/2')
            return None, 'No source found.'
        with patch.object(set_import,'source_for',side_effect=plain):
            set_import.prepare_set(api,saved['id'],'Set 2')
        with api.connect() as db:
            detail = json.loads(db.execute('SELECT detail FROM dj_set_items WHERE set_id=?',(saved['id'],)).fetchone()[0])
        self.assertEqual((detail['title'],[link['url'] for link in detail['links']]),('Other (Original Mix)',['https://musicbrainz.org/recording/2']))

    def test_prepare_sees_a_credit_edit_the_report_cache_has_not(self):
        track = api.dispatch('library.import',{'path':str(self.fixture('song.wav',550,artist='Artist',title='Song'))})
        saved = set_import.save_report(api,'Mix',None,set_import.parse_tracklist('00:00 Artist - Song','Pasted tracklist',include_uncredited=True),None)
        self.assertEqual(api.dispatch('sets.get',{'id':saved['id']})['tracks'][0]['playback_track_id'],track['id'])
        with api.connect() as db:
            db.execute("UPDATE tracks SET artist='Someone Else',title='Another Song' WHERE id=?",(track['id'],))
        with patch.object(set_import,'source_for',return_value=(None,'No source found.')):
            set_import.prepare_set(api,saved['id'],'Set')
        with api.connect() as db:
            self.assertIsNone(db.execute('SELECT track_id FROM dj_set_items WHERE set_id=?',(saved['id'],)).fetchone()[0])
