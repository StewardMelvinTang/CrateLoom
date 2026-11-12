from contextlib import ExitStack
import copy
import json
from pathlib import Path
import subprocess
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from crateloom import __main__ as api, metadata_batch, metadata_lookup, preparation


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

    def start(self, tracks):
        return api.dispatch('metadata.batch.start', {'track_ids': [t['id'] for t in tracks]})

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


if __name__=='__main__':unittest.main()
