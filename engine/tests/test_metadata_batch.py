from contextlib import ExitStack
import copy
import json
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import urllib.request

from crateloom import __main__ as api, metadata_batch, metadata_lookup, preparation

WRITER = os.environ.get('CRATELOOM_METADATA_PYTHON')


def candidate(title='Song', rank=99, provider='Apple Music', recording_id='123', status='strong'):
    return {'provider': provider, 'recording_id': recording_id, 'release_id': '456',
            'rank': rank, 'match_status': status, 'title_similarity': 1, 'artist_similarity': 1,
            'values': {'title': title, 'artist': 'Artist', 'album': 'Album', 'date': '2001', 'genre': 'House'},
            'duration': 2, 'duration_difference': 0, 'version': '', 'reasons': []}


class MetadataBatch(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        for name, value in [('ROOT', self.root), ('DB', self.root/'library.sqlite3'), ('CACHE', self.root/'cache')]:
            self.stack.enter_context(patch.object(api, name, value))
        api.CACHE.mkdir()
        self.cover = self.root/'artwork'/'cover.png'
        self.cover.parent.mkdir()
        subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','color=c=red:s=32x32','-frames:v','1','-threads','1','-y',str(self.cover)], check=True)

    def track(self, index=0, artist='Artist'):
        audio = self.root/f'track-{index}.wav'
        subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i',f'sine=frequency={440+index*100}:duration=2','-y',str(audio)], check=True)
        track = api.dispatch('library.import', {'path': str(audio)})
        api.dispatch('metadata.save', {'id': track['id'], 'values': {'title': f'Song {index}', 'artist': artist}})
        return api.dispatch('track.get', {'id': track['id']})

    def finish(self, batch):
        deadline = time.monotonic()+8
        while batch['state'] in metadata_batch.ACTIVE and time.monotonic()<deadline:
            time.sleep(.01)
            batch = api.dispatch('metadata.batch.get', {'id': batch['id']})
        self.assertNotIn(batch['state'], metadata_batch.ACTIVE, batch)
        return batch

    def start(self, tracks, **options):
        return api.dispatch('metadata.batch.start', {'track_ids': [t['id'] for t in tracks], **options})

    def mocked_writes(self, write):
        """Route metadata.write to `write(track_id, params)`; everything else stays real."""
        real = preparation.dispatch
        def dispatch(engine, db, method, params):
            if method == 'metadata.write':
                return write(params['id'], params)
            return real(engine, db, method, params)
        self.stack.enter_context(patch.object(preparation, 'tag_writer_python', return_value='python'))
        self.stack.enter_context(patch.object(preparation, 'dispatch', side_effect=dispatch))

    def test_applies_best_compatible_match_with_cover_and_preserves_audio_and_cues(self):
        track = self.track()
        original = Path(track['path']).read_bytes()
        cue = {'slot':0,'position_us':500000,'label':'Keep'}
        api.dispatch('cues.save', {'id':track['id'],'cues':[cue]})
        rejected = candidate('Wrong remix', 100, status='rejected')
        best = candidate('Correct title', 99)
        weaker = candidate('Other release', 90, recording_id='789')
        result = {'candidates':[rejected,weaker,best], 'query':{'title':'Song','artist':'Artist','version':''}, 'warnings':[]}
        with patch.object(metadata_lookup,'search',return_value=result),patch.object(preparation,'fetch_cover',return_value=str(self.cover)) as cover:
            batch = self.finish(self.start([track]))
        self.assertEqual(batch['applied'],1,batch)
        self.assertEqual(cover.call_args.args[1]['recording_id'],'123')
        updated = api.dispatch('track.get',{'id':track['id']})
        self.assertEqual(updated['title'],'Correct title')
        self.assertEqual(updated['metadata']['values']['album'],'Album')
        self.assertEqual(updated['metadata']['artwork_path'],str(self.cover))
        self.assertTrue(updated['metadata']['artwork_pending'])
        self.assertEqual(updated['cues'],[cue])
        self.assertEqual(Path(track['path']).read_bytes(),original)
        with patch.object(metadata_lookup,'search',side_effect=AssertionError('Review must use saved results')):
            review = api.dispatch('metadata.get',{'id':track['id']})
        self.assertEqual(review['automatic_review']['candidates'][0]['recording_id'],'123')
        self.assertFalse(review['automatic']['reviewed'])
        api.dispatch('metadata.save',{'id':track['id'],'values':{}})
        self.assertTrue(api.dispatch('metadata.get',{'id':track['id']})['automatic']['reviewed'])

    def test_uses_next_similar_release_if_highest_ranked_cover_is_unavailable(self):
        track = self.track()
        high = candidate(rank=100, provider='MusicBrainz', recording_id='mb')
        lower = candidate(rank=99)
        def fetch(api, source, url):
            if source['provider']=='MusicBrainz':raise ValueError('No cover')
            return str(self.cover)
        with patch.object(metadata_lookup,'search',return_value={'candidates':[high,lower]}),patch.object(preparation,'fetch_cover',side_effect=fetch) as cover:
            batch = self.finish(self.start([track]))
        self.assertEqual(batch['applied'],1)
        self.assertEqual(cover.call_count,2)
        self.assertEqual(api.dispatch('metadata.get',{'id':track['id']})['source']['provider'],'Apple Music')

    def test_never_applies_metadata_without_a_verified_cover(self):
        track = self.track()
        before = copy.deepcopy(track['metadata'])
        with patch.object(metadata_lookup,'search',return_value={'candidates':[candidate()]}),patch.object(preparation,'fetch_cover',side_effect=ValueError('Invalid image')):
            batch = self.finish(self.start([track]))
        self.assertEqual(batch['needs_review'],1)
        self.assertEqual(batch['applied'],0)
        self.assertEqual(api.dispatch('track.get',{'id':track['id']})['metadata'],before)
        self.assertIn('cover',api.dispatch('metadata.get',{'id':track['id']})['automatic_review']['reason'])

    def test_reversed_title_and_artist_are_put_back_by_a_catalog_match(self):
        # A "Song - Artist" playlist imported as artist "2 . Ain't No Way", title "Chris Brown".
        reversed_track = self.track(0)
        api.dispatch('metadata.save', {'id': reversed_track['id'], 'values': {'title': 'Chris Brown', 'artist': "2 . Ain't No Way"}})
        unmatched = self.track(1)
        found = candidate("Ain't No Way")
        found['values']['artist'] = 'Chris Brown'
        searched = []
        def search(engine, request, identity, duration, *args):
            searched.append((identity['title'], identity['artist']))
            return {'candidates': [found] if identity == {'title': "Ain't No Way", 'artist': 'Chris Brown', 'version': ''} else []}
        with patch.object(metadata_lookup, 'search', side_effect=search), patch.object(preparation, 'fetch_cover', return_value=str(self.cover)):
            batch = self.finish(self.start([reversed_track, unmatched]))
        self.assertEqual((batch['applied'], batch['needs_review']), (1, 1), batch)
        self.assertEqual(sorted(searched), sorted([('Chris Brown', "Ain't No Way"), ("Ain't No Way", 'Chris Brown'),
                                                   ('Song 1', 'Artist'), ('Artist', 'Song 1')]))
        updated = api.dispatch('track.get', {'id': reversed_track['id']})
        self.assertEqual((updated['title'], updated['artist']), ("Ain't No Way", 'Chris Brown'))
        # Nothing matched either way: the track is left as it was, for review.
        self.assertEqual(api.dispatch('track.get', {'id': unmatched['id']})['title'], 'Song 1')
    def test_unknown_artist_and_weak_similarity_stay_for_review(self):
        tracks = [self.track(0,artist=''),self.track(1)]
        weak = candidate(rank=97)
        weak['title_similarity']=.8
        with patch.object(metadata_lookup,'search',return_value={'candidates':[weak]}),patch.object(preparation,'fetch_cover') as cover:
            batch = self.finish(self.start(tracks))
        self.assertEqual(batch['needs_review'],2)
        cover.assert_not_called()

    def test_concurrent_manual_edit_is_preserved(self):
        track = self.track()
        def search(*args,**kwargs):
            api.dispatch('metadata.save',{'id':track['id'],'values':{'title':'My manual title'}})
            return {'candidates':[candidate()]}
        with patch.object(metadata_lookup,'search',side_effect=search),patch.object(preparation,'fetch_cover',return_value=str(self.cover)):
            batch = self.finish(self.start([track]))
        self.assertEqual(batch['skipped'],1)
        self.assertEqual(api.dispatch('track.get',{'id':track['id']})['title'],'My manual title')

    def test_cancel_prevents_pending_and_running_tracks_from_being_applied(self):
        tracks = [self.track(i) for i in range(3)]
        entered, release = threading.Event(),threading.Event()
        def search(*args,**kwargs):
            entered.set();release.wait(5)
            return {'candidates':[candidate()]}
        with patch.object(metadata_lookup,'search',side_effect=search),patch.object(preparation,'fetch_cover') as cover:
            batch = self.start(tracks)
            try:
                self.assertTrue(entered.wait(2))
                with self.assertRaisesRegex(ValueError,'already running'):self.start(tracks)
                cancelled = api.dispatch('metadata.batch.cancel',{'id':batch['id']})
                self.assertEqual(cancelled['state'],'cancelling')
            finally:release.set()
            finished = self.finish(batch)
        self.assertEqual(finished['state'],'cancelled')
        self.assertEqual(finished['applied'],0)
        self.assertEqual(finished['processed'],0)
        self.assertEqual(finished['cancelled'],3)
        cover.assert_not_called()

    def test_failure_on_one_track_does_not_stop_other_tracks(self):
        tracks = [self.track(i) for i in range(2)]
        def search(api,request,identity,*args,**kwargs):
            if identity['title']=='Song 0':raise OSError('Catalog unavailable')
            return {'candidates':[candidate()]}
        with patch.object(metadata_lookup,'search',side_effect=search),patch.object(preparation,'fetch_cover',return_value=str(self.cover)):
            batch = self.finish(self.start(tracks))
        self.assertEqual(batch['state'],'completed')
        self.assertEqual(batch['failed'],1)
        self.assertEqual(batch['applied'],1)
        self.assertIn('unavailable',api.dispatch('metadata.get',{'id':tracks[0]['id']})['automatic_review']['reason'])

    def test_journal_recovers_results_and_marks_unfinished_work_interrupted(self):
        track = self.track()
        with patch.object(metadata_lookup,'search',return_value={'candidates':[candidate()]}),patch.object(preparation,'fetch_cover',return_value=str(self.cover)):
            batch = self.finish(self.start([track]))
        recovered = metadata_batch.BatchManager(api)
        self.assertEqual(recovered.snapshot()['applied'],1)
        self.assertEqual(recovered.snapshot()['id'],batch['id'])
        recovered.job['state']='running';recovered.job['items'][0]['state']='queued';recovered.save()
        interrupted = metadata_batch.BatchManager(api).snapshot()
        self.assertEqual(interrupted['state'],'interrupted')
        self.assertEqual(interrupted['cancelled'],1)

    def test_input_validation_and_missing_files(self):
        track = self.track()
        for ids in ([],[track['id'],track['id']],['missing'],[1]):
            with self.subTest(ids=ids),self.assertRaises(ValueError):
                api.dispatch('metadata.batch.start',{'track_ids':ids})
        Path(track['path']).unlink()
        with patch.object(metadata_lookup,'search') as search:
            batch = self.finish(self.start([track]))
        self.assertEqual(batch['skipped'],1)
        search.assert_not_called()

    def test_apple_search_artwork_avoids_an_extra_lookup_and_is_validated(self):
        image = self.cover.read_bytes()
        with patch.object(preparation,'request',return_value=image) as remote,patch.object(metadata_lookup,'cached_request',side_effect=AssertionError('Extra lookup')):
            path = preparation.fetch_cover(api,{'provider':'Apple Music','recording_id':'123'},'https://is1-ssl.mzstatic.com/image/100x100bb.jpg')
        self.assertEqual(Path(path).read_bytes(),image)
        self.assertIn('600x600bb',remote.call_args.args[0])

    def fresh(self, name):
        audio = self.root/name
        subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i',f'sine=frequency={500+len(name)*10}:duration=2','-metadata','title=Song','-metadata','artist=Artist','-y',str(audio)], check=True)
        track = api.dispatch('library.import', {'path': str(audio)})
        self.assertIsNone(track['metadata'])
        return track

    def test_opening_metadata_during_a_batch_does_not_skip_the_track(self):
        track = self.fresh('fresh.wav')
        opened = []
        def search(*args,**kwargs):
            # The editor reads this track between batch start and the final write.
            opened.append(api.dispatch('metadata.get',{'id':track['id']}))
            return {'candidates':[candidate('Catalog title')]}
        with patch.object(metadata_lookup,'search',side_effect=search),patch.object(preparation,'fetch_cover',return_value=str(self.cover)):
            batch = self.finish(self.start([track]))
        self.assertEqual(len(opened),1)
        self.assertEqual((batch['applied'],batch['skipped']),(1,0),batch)
        self.assertEqual(api.dispatch('track.get',{'id':track['id']})['title'],'Catalog title')

    def test_fingerprint_ignores_the_stored_baseline_but_not_staged_edits(self):
        track = self.fresh('baseline.wav')
        before = metadata_batch.fingerprint(track)
        api.dispatch('metadata.get',{'id':track['id']})
        stored = api.dispatch('track.get',{'id':track['id']})
        self.assertIsNotNone(stored['metadata'])
        self.assertEqual(metadata_batch.fingerprint(stored),before)
        api.dispatch('metadata.save',{'id':track['id'],'values':{'title':'Song','artist':'Artist'}})
        self.assertEqual(metadata_batch.fingerprint(api.dispatch('track.get',{'id':track['id']})),before)
        api.dispatch('metadata.save',{'id':track['id'],'values':{'album':'Mine'}})
        edited = metadata_batch.fingerprint(api.dispatch('track.get',{'id':track['id']}))
        self.assertNotEqual(edited,before)
        api.dispatch('metadata.save',{'id':track['id'],'values':{},'artwork_path':str(self.cover)})
        self.assertNotEqual(metadata_batch.fingerprint(api.dispatch('track.get',{'id':track['id']})),edited)

    def test_unusable_catalog_date_is_dropped_instead_of_failing_the_track(self):
        track = self.track()
        odd = candidate()
        odd['values']['date'] = 'circa 2001'
        with patch.object(metadata_lookup,'search',return_value={'candidates':[odd]}),patch.object(preparation,'fetch_cover',return_value=str(self.cover)):
            batch = self.finish(self.start([track]))
        self.assertEqual(batch['applied'],1,batch)
        self.assertNotIn('date',api.dispatch('track.get',{'id':track['id']})['metadata']['pending'])

    def test_invalid_cached_cover_is_downloaded_again_once(self):
        image = self.cover.read_bytes()
        url = 'https://is1-ssl.mzstatic.com/image/100x100bb.jpg'
        release = '788cb31f-de60-4339-9152-f3edeef9f966'
        for source,name in [({'provider':'Apple Music','recording_id':'77'},'apple-77.image'),({'release_id':release},'release-'+release+'-1200.image')]:
            with self.subTest(name=name):
                cached = self.root/'artwork'/name
                cached.write_bytes(b'truncated download')
                with patch.object(preparation,'request',return_value=image) as remote:
                    path = preparation.fetch_cover(api,source,url)
                    self.assertEqual(Path(path).read_bytes(),image)
                    self.assertEqual(cached.read_bytes(),image)
                    preparation.fetch_cover(api,source,url)
                    self.assertEqual(remote.call_count,1)
                cached.write_bytes(b'truncated download')
                with patch.object(preparation,'request',return_value=b'still not an image') as remote,self.assertRaises(ValueError):
                    preparation.fetch_cover(api,source,url)
                self.assertEqual(remote.call_count,1)

    def test_auto_write_writes_only_tracks_applied_in_this_run_and_keeps_staged_metadata_on_failure(self):
        ok, broken, uncovered, unmatched, bystander = [self.track(i) for i in range(5)]
        # The bystander has staged changes from earlier and is not part of this run.
        api.dispatch('metadata.save', {'id': bystander['id'], 'values': {'album': 'Staged earlier'}})
        staged_earlier = api.dispatch('track.get', {'id': bystander['id']})['metadata']
        calls = []
        def write(track_id, params):
            calls.append((track_id, params.get('revision')))
            if track_id == broken['id']:
                raise ValueError('The file is read-only.')
            return {'track': None, 'backup': '/backups/' + track_id, 'written': True, 'artwork_embedded': True}
        self.mocked_writes(write)
        def search(engine, request, identity, *args, **kwargs):
            # Song 3 is in no catalog, in either order of title and artist.
            return {'candidates': [] if 'Song 3' in (identity['title'], identity['artist']) else [candidate(identity['title'] + ' (catalog)')]}
        def fetch(engine, source, url):
            if source['values']['title'].startswith('Song 2'):
                raise ValueError('No cover')
            return str(self.cover)
        with patch.object(metadata_lookup, 'search', side_effect=search), patch.object(preparation, 'fetch_cover', side_effect=fetch):
            batch = self.finish(self.start([ok, broken, uncovered, unmatched], write_tags=True))
        self.assertTrue(batch['write_tags'])
        self.assertEqual((batch['applied'], batch['needs_review'], batch['written'], batch['write_failed']), (2, 2, 1, 1), batch)
        self.assertEqual({tid for tid, _ in calls}, {ok['id'], broken['id']})
        items = {item['id']: item for item in batch['items']}
        for track in (ok, broken):
            # Each write is tied to exactly what the batch staged.
            staged = api.dispatch('metadata.get', {'id': track['id']})
            self.assertIn((track['id'], staged['revision']), calls)
        self.assertEqual((items[ok['id']]['state'], items[ok['id']]['written'], items[ok['id']]['artwork_embedded']), ('applied', True, True))
        self.assertNotIn('write_error', items[ok['id']])
        self.assertEqual((items[broken['id']]['state'], items[broken['id']]['written'], items[broken['id']]['write_error']),
                         ('applied', False, 'The file is read-only.'))
        kept = api.dispatch('track.get', {'id': broken['id']})
        self.assertEqual(kept['title'], 'Song 1 (catalog)')
        self.assertTrue(kept['metadata']['pending'])
        self.assertTrue(kept['metadata']['artwork_pending'])
        for track in (uncovered, unmatched):
            self.assertEqual(items[track['id']]['state'], 'needs_review')
            self.assertNotIn('written', items[track['id']])
        self.assertEqual(api.dispatch('track.get', {'id': bystander['id']})['metadata'], staged_earlier)
        # The journal keeps the option and the outcome.
        recovered = metadata_batch.BatchManager(api).snapshot()
        self.assertEqual((recovered['write_tags'], recovered['written'], recovered['write_failed']), (True, 1, 1))

    def test_auto_write_is_off_unless_asked_and_older_journals_still_load(self):
        track = self.track()
        self.mocked_writes(lambda *args: self.fail('Nothing may be written unless write_tags is true'))
        with patch.object(metadata_lookup, 'search', return_value={'candidates': [candidate()]}), patch.object(preparation, 'fetch_cover', return_value=str(self.cover)):
            batch = self.finish(self.start([track]))
        self.assertEqual((batch['applied'], batch['write_tags'], batch['written'], batch['write_failed']), (1, False, 0, 0))
        self.assertNotIn('written', batch['items'][0])
        self.assertTrue(api.dispatch('track.get', {'id': track['id']})['metadata']['pending'])
        # A journal from before the option existed has no write_tags key.
        old = metadata_batch.BatchManager(api)
        del old.job['write_tags']
        old.save()
        loaded = metadata_batch.BatchManager(api).snapshot()
        self.assertEqual((loaded['applied'], loaded['written']), (1, 0))
        self.assertNotIn('write_tags', loaded)

    def test_auto_write_never_starts_after_stop_and_never_interrupts_a_write(self):
        tracks = [self.track(i) for i in range(3)]
        writing, release_write, release_search = threading.Event(), threading.Event(), threading.Event()
        calls = []
        def write(track_id, params):
            calls.append(track_id)
            writing.set()
            self.assertTrue(release_write.wait(5))
            return {'track': None, 'backup': None, 'written': True}
        self.mocked_writes(write)
        first = threading.Lock()
        def search(*args, **kwargs):
            # One track goes straight through to its write; the rest wait in lookup.
            if not first.acquire(blocking=False):
                release_search.wait(5)
            return {'candidates': [candidate()]}
        with patch.object(metadata_lookup, 'search', side_effect=search), patch.object(preparation, 'fetch_cover', return_value=str(self.cover)):
            batch = self.start(tracks, write_tags=True)
            try:
                self.assertTrue(writing.wait(5))
                self.assertEqual(api.dispatch('metadata.batch.cancel', {'id': batch['id']})['state'], 'cancelling')
                release_search.set()
                time.sleep(.1)
                # The write that had started is still running; the batch waits for it.
                self.assertEqual(api.dispatch('metadata.batch.get', {'id': batch['id']})['state'], 'cancelling')
            finally:
                release_search.set()
                release_write.set()
            finished = self.finish(batch)
        self.assertEqual((finished['state'], finished['applied'], finished['written'], finished['cancelled']), ('cancelled', 1, 1, 2), finished)
        self.assertEqual(len(calls), 1)

    def test_auto_write_is_skipped_when_stop_arrives_between_apply_and_write(self):
        track = self.track()
        self.mocked_writes(lambda *args: self.fail('A write must not start after Stop'))
        store = metadata_batch.BatchManager.store_review
        def stop_after_apply(manager, saved, result, state, *args, **kwargs):
            store(manager, saved, result, state, *args, **kwargs)
            if state == 'applied':
                api.dispatch('metadata.batch.cancel', {'id': manager.job['id']})
        with patch.object(metadata_lookup, 'search', return_value={'candidates': [candidate()]}), patch.object(preparation, 'fetch_cover', return_value=str(self.cover)), \
                patch.object(metadata_batch.BatchManager, 'store_review', stop_after_apply):
            finished = self.finish(self.start([track], write_tags=True))
        item = finished['items'][0]
        self.assertEqual((finished['state'], item['state'], item['written']), ('cancelled', 'applied', False), finished)
        self.assertNotIn('write_error', item)
        self.assertTrue(api.dispatch('track.get', {'id': track['id']})['metadata']['pending'])

    def test_auto_write_does_not_overwrite_an_edit_made_after_the_apply(self):
        track = self.track()
        original = Path(track['path']).read_bytes()
        store = metadata_batch.BatchManager.store_review
        def edit_after_apply(manager, saved, result, state, *args, **kwargs):
            store(manager, saved, result, state, *args, **kwargs)
            if state == 'applied':
                api.dispatch('metadata.save', {'id': track['id'], 'values': {'title': 'My later edit'}})
        # The real metadata.write refuses the stale revision before it needs the tag writer.
        with patch.object(preparation, 'tag_writer_python', return_value='python'), patch.object(metadata_lookup, 'search', return_value={'candidates': [candidate()]}), \
                patch.object(preparation, 'fetch_cover', return_value=str(self.cover)), patch.object(metadata_batch.BatchManager, 'store_review', edit_after_apply):
            finished = self.finish(self.start([track], write_tags=True))
        item = finished['items'][0]
        self.assertEqual((item['state'], item['written']), ('applied', False), finished)
        self.assertIn('changed after review', item['write_error'])
        self.assertEqual(api.dispatch('track.get', {'id': track['id']})['title'], 'My later edit')
        self.assertEqual(Path(track['path']).read_bytes(), original)

    def test_auto_write_needs_the_tag_writer_before_anything_starts(self):
        track = self.track()
        with patch.dict(os.environ, {'CRATELOOM_METADATA_PYTHON': str(self.root/'missing-python')}), patch.object(metadata_lookup, 'search') as search:
            with self.assertRaisesRegex(ValueError, 'Tag writer unavailable'):
                self.start([track], write_tags=True)
            with self.assertRaisesRegex(ValueError, 'write_tags'):
                self.start([track], write_tags='yes')
        self.assertIsNone(api.dispatch('metadata.batch.latest', {}))
        search.assert_not_called()

    @unittest.skipUnless(WRITER and Path(WRITER).is_file(), 'Set CRATELOOM_METADATA_PYTHON')
    def test_auto_write_puts_tags_and_cover_in_the_file_with_a_verified_backup(self):
        for extension, codec, embeds in [('mp3', 'libmp3lame', True), ('ogg', 'vorbis', False)]:
            with self.subTest(extension=extension):
                audio = self.root/('auto.' + extension)
                subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','sine=frequency=440:duration=2','-ac','2','-strict','-2','-c:a',codec,
                                '-metadata','title=Song','-metadata','artist=Artist','-y',str(audio)], check=True)
                original = audio.read_bytes()
                track = api.dispatch('library.import', {'path': str(audio)})
                cue = {'slot': 0, 'position_us': 500000, 'label': 'Keep'}
                api.dispatch('cues.save', {'id': track['id'], 'cues': [cue]})
                with patch.object(metadata_lookup, 'search', return_value={'candidates': [candidate('Catalog title')]}), patch.object(preparation, 'fetch_cover', return_value=str(self.cover)):
                    batch = self.finish(self.start([track], write_tags=True))
                item = batch['items'][0]
                self.assertEqual((batch['applied'], batch['written'], batch['write_failed']), (1, 1, 0), batch)
                self.assertEqual((item['state'], item['written'], item['artwork_embedded']), ('applied', True, embeds), item)
                self.assertEqual('write_note' in item, not embeds)
                self.assertEqual(Path(item['backup']).read_bytes(), original)
                after = api.dispatch('track.get', {'id': track['id']})
                self.assertEqual(after['audio_hash'], track['audio_hash'])
                self.assertEqual(after['cues'], [cue])
                self.assertEqual((after['metadata']['pending'], after['metadata']['artwork_pending'], after['metadata']['artwork_path']), ({}, False, str(self.cover)))
                probe = json.loads(subprocess.run(['ffprobe','-v','error','-show_format','-show_streams','-of','json',str(audio)], capture_output=True, text=True, check=True).stdout)
                tags = {k.lower(): v for k, v in {**probe['format'].get('tags', {}), **probe['streams'][0].get('tags', {})}.items()}
                self.assertEqual((tags['title'], tags['album']), ('Catalog title', 'Album'))
                self.assertEqual(any(s.get('disposition', {}).get('attached_pic') for s in probe['streams']), embeds)

    def test_insecure_redirect_is_refused_before_it_is_followed(self):
        handler = preparation.SecureRedirects()
        original = urllib.request.Request('https://coverartarchive.org/release/x/front-1200')
        with self.assertRaisesRegex(ValueError,'Insecure provider redirect'):
            handler.redirect_request(original,None,307,'Temporary Redirect',{},'http://mirror.example/cover.jpg')
        followed = handler.redirect_request(original,None,302,'Found',{},'https://mirror.example/cover.jpg')
        self.assertEqual(followed.full_url,'https://mirror.example/cover.jpg')
        redirects = [h for h in preparation.OPENER.handlers if isinstance(h,urllib.request.HTTPRedirectHandler)]
        self.assertEqual([type(h) for h in redirects],[preparation.SecureRedirects])


if __name__=='__main__':unittest.main()
