import json
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from crateloom.metadata_matching import identity_layers, title_similarity, clean_name, search_identity, catalog_identity, query_plan, rank_candidates, split_version, primary_artist
from crateloom import preparation, metadata_lookup


def recording(id='original', title='Insomnia', artist='Faithless', seconds=217, version='', releases=None):
    return {'id': id, 'title': title, 'artist-credit': [{'name': artist}], 'length': seconds*1000 if seconds else None,
            'disambiguation': version, 'score': '100', 'releases': releases or [{'id': 'release', 'title': 'Insomnia', 'date': '1995', 'status': 'Official'}]}


class MetadataMatching(unittest.TestCase):
    def setUp(self):
        patcher=patch.dict(metadata_lookup.INTERVALS, {'MusicBrainz':0, 'Apple Music':0})
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_owner_filenames_preserve_version_and_strip_download_noise(self):
        cases = [
            ('Faithless - Insomnia (Lyrics).mp3', {'title': 'Insomnia', 'artist': 'Faithless', 'version': ''}),
            ('Work REMIX (Official Audio).mp3', {'title': 'Work', 'artist': '', 'version': 'REMIX'}),
            ('Call_Me_Maybe_Refilled_Remix_KLICKAUD.mp3', {'title': 'Call Me Maybe', 'artist': '', 'version': 'Refilled Remix'}),
            ('Annyeonghaseyo_Jersey_-_Yuki_Chiba_Lil_Moshpit_Wichaya_Jersey_Club_Remix_KLICKAUD.mp3',
             {'title': 'Annyeonghaseyo', 'artist': '', 'version': 'Wichaya Jersey Club Remix'}),
        ]
        for name, expected in cases:
            with self.subTest(name=name): self.assertEqual(search_identity(name), expected)
        self.assertEqual(search_identity('Faithless - Insomnia (Lyrics)', 'Faithless')['title'], 'Insomnia')
        self.assertEqual(clean_name('Official Secrets (Live)'), 'Official Secrets (Live)')
        self.assertEqual(search_identity('Björk - Jóga (Live at Cambridge)')['version'], 'Live at Cambridge')

    def test_wrong_remix_and_five_minute_mismatch_are_excluded(self):
        bad = recording('wrong', 'Faithless - Insomnia (Andrew Peatnuck remix)', 'Andrew Peatnuck', 525.52)
        good = recording()
        ranked = rank_candidates({'recordings': [bad, good]}, 217, search_identity('Faithless - Insomnia'))
        self.assertEqual(ranked[0]['recording_id'], 'original')
        self.assertEqual(ranked[0]['match_status'], 'strong')
        self.assertEqual(ranked[1]['match_status'], 'rejected')
        self.assertIn('Different artist', ranked[1]['reasons'])
        self.assertIn('Duration differs too much', ranked[1]['reasons'])
        self.assertEqual(ranked[1]['duration_difference'], 308.52)

    def test_named_remix_never_falls_back_to_original_or_another_remixer(self):
        identity = search_identity('Call_Me_Maybe_Refilled_Remix_KLICKAUD.mp3')
        recordings = [recording('original', 'Call Me Maybe', 'Carly Rae Jepsen', 183),
                      recording('other', 'Call Me Maybe (Someone Else Remix)', 'Carly Rae Jepsen', 183),
                      recording('right', 'Call Me Maybe', 'Carly Rae Jepsen', 183, 'Refilled Remix')]
        ranked = rank_candidates({'recordings': recordings}, 183, identity)
        self.assertEqual(ranked[0]['recording_id'], 'right')
        self.assertEqual(ranked[0]['match_status'], 'review')  # Artist is not established by this filename.
        self.assertIn('Refilled Remix', ranked[0]['values']['title'])
        self.assertTrue(all(c['match_status']=='rejected' for c in ranked[1:]))
        self.assertTrue(all(c['reasons'] for c in ranked[1:]))

    def test_live_versions_wrong_artist_and_wrong_title_do_not_get_good_rank(self):
        payload = {'recordings': [recording('live', version='Live'), recording('other', artist='Another Artist'), recording('title', title='No Sleep')]}
        self.assertTrue(all(c['match_status']=='rejected' for c in rank_candidates(payload, 217, search_identity('Insomnia', 'Faithless'))))

    def test_missing_duration_is_not_a_strong_text_match(self):
        result = rank_candidates({'recordings': [recording(seconds=None)]}, 217, search_identity('Insomnia', 'Faithless'))
        self.assertEqual(result[0]['match_status'], 'review')
        self.assertIsNone(result[0]['duration_difference'])

    def test_release_preference_does_not_invent_release_date(self):
        r = recording(releases=[{'id': 'compilation', 'title': 'Hits', 'date': '2001', 'status': 'Official', 'release-group': {'secondary-types': ['Compilation']}},
                                {'id': 'single', 'title': 'Insomnia', 'date': '1995', 'status': 'Official'},
                                {'id': 'unknown', 'title': 'Unknown'}])
        r['first-release-date'] = '1995'
        result = rank_candidates({'recordings': [r]}, 217, search_identity('Insomnia', 'Faithless'))
        self.assertEqual(result[0]['release_id'], 'single')
        self.assertEqual(next(c for c in result if c['release_id']=='unknown')['values']['date'], '')

    def test_queries_are_escaped_bounded_and_keep_named_remix(self):
        plan = query_plan(search_identity('Call Me Maybe (Refilled Remix)', 'Carly Rae Jepsen'), 183)
        self.assertLessEqual(len(plan), 3)
        self.assertIn('Refilled'.lower(), plan[0].lower())
        self.assertIn('comment:', plan[0])
        self.assertIn('dur:[', plan[0])
        self.assertNotIn('dur:', plan[-1])
        escaped = query_plan({'title': 'Song" OR artist:*', 'artist': '', 'version': ''}, None)
        self.assertIn('Song\\" OR artist:*"', escaped[0])

    def test_search_fallback_cache_reranking_and_no_state_or_file_writes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state = {'values': {'title': 'Faithless - Insomnia (Lyrics).mp3', 'artist': ''}, 'pending': {}, 'artwork_path': None}
            track = {'duration': 217, 'metadata': state}
            def atomic(path, data): path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(data)
            api = SimpleNamespace(ROOT=root, track=lambda db, id: track, atomic=atomic)
            def finish(job):
                deadline = time.monotonic()+10
                while job['state']=='running' and time.monotonic()<deadline:
                    time.sleep(.01); job = preparation.dispatch(api, None, 'metadata.job', {'id': job['id']})
                self.assertEqual(job['state'], 'completed', job)
                return job['result']
            responses = iter([{'recordings': []}, {'recordings': [recording()]}])
            def request(url, **kwargs):
                return json.dumps(next(responses) if 'musicbrainz.org' in url else {'results':[]}).encode()
            with patch.object(preparation, 'request', side_effect=request) as remote:
                first = finish(preparation.dispatch(api, None, 'metadata.search', {'id': 'test'}))
                self.assertEqual(remote.call_count, 3)
                self.assertEqual(first['query']['artist'], 'Faithless')
                self.assertEqual(first['candidates'][0]['match_status'], 'strong')
            with patch.object(preparation, 'request', side_effect=AssertionError('Should use cache')):
                cached = finish(preparation.dispatch(api, None, 'metadata.search', {'id': 'test'}))
                self.assertTrue(cached['cached'])
            self.assertEqual(state['pending'], {})
            self.assertEqual(state['values']['title'], 'Faithless - Insomnia (Lyrics).mp3')
            # A new duration must be evaluated again, even when provider results are cached.
            track['duration'] = 525.52
            with patch.object(preparation, 'request', side_effect=lambda url,**kw:json.dumps({'recordings': [recording()]} if 'musicbrainz.org' in url else {'results':[]}).encode()):
                reranked = finish(preparation.dispatch(api, None, 'metadata.search', {'id': 'test'}))
                self.assertEqual(reranked['candidates'][0]['match_status'], 'rejected')
                self.assertIn('Duration differs too much', reranked['candidates'][0]['reasons'])

    def test_featured_artist_credit_does_not_hide_famous_song(self):
        identity=catalog_identity('Low (feat. T-Pain)', 'Flo Rida,T-Pain')
        self.assertEqual(identity['title'],'Low')
        self.assertEqual(identity['artist'],'Flo Rida,T-Pain')
        self.assertIn('artist:"Flo Rida"',query_plan(identity,229.9)[0])
        result=metadata_lookup.apple_candidates({'results':[{'kind':'song','trackId':275496141,'collectionId':275496110,
            'trackName':'Low (feat. T-Pain)','artistName':'Flo Rida','trackTimeMillis':231400,
            'collectionName':'Mail On Sunday','releaseDate':'2007-10-09T07:00:00Z','primaryGenreName':'Hip-Hop/Rap'}]},229.900771,identity)
        self.assertEqual(result[0]['match_status'],'strong')
        self.assertEqual(result[0]['values']['title'],'Low (feat. T-Pain)')
        self.assertEqual(result[0]['provider'],'Apple Music')
        self.assertEqual(result[0]['values']['genre'],'Hip-Hop/Rap')

    def test_apple_rejects_wrong_versions_even_with_identical_duration(self):
        items=[{'kind':'song','trackId':i,'trackName':title,'artistName':'Flo Rida',
                'trackTimeMillis':230000,'collectionName':'Album'} for i,title in enumerate([
                    'Low (Travis Barker Remix)','Low (Extended Mix)','Low (Instrumental)','Low (Live)','Low (feat. T-Pain)'])]
        candidates=metadata_lookup.apple_candidates({'results':items},230,search_identity('Low (feat. T-Pain)','Flo Rida,T-Pain'))
        self.assertEqual([c['match_status'] for c in candidates],['rejected']*4+['strong'])

    def test_independent_provider_results_arrive_before_slow_catalog(self):
        import threading
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            def atomic(path,data):path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(data)
            api=SimpleNamespace(ROOT=root,atomic=atomic)
            received=[];fast=threading.Event()
            def request(url,**kwargs):
                if 'musicbrainz.org' in url:
                    self.assertTrue(fast.wait(2),'Apple results should stream before MusicBrainz finishes')
                    raise OSError('503')
                return json.dumps({'results':[{'kind':'song','trackId':1,'trackName':'Insomnia','artistName':'Faithless','trackTimeMillis':217000}]}).encode()
            def progress(result):
                received.append(result)
                if result['candidates']:fast.set()
            result=metadata_lookup.search(api,request,search_identity('Insomnia','Faithless'),217,progress=progress)
            self.assertEqual(received[0]['candidates'][0]['provider'],'Apple Music')
            self.assertEqual(result['candidates'][0]['match_status'],'strong')
            self.assertTrue(result['partial'])
            self.assertIn('MusicBrainz',result['warnings'][0])

    def test_empty_cache_expires_and_stale_results_survive_outage(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            def atomic(path,data):path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(data)
            api=SimpleNamespace(ROOT=root,atomic=atomic)
            url='https://itunes.apple.com/search?term=test'
            with patch.object(preparation,'request',return_value=b'{"results":[]}') as request:
                metadata_lookup.cached_request(api,request,'Apple Music',url)
                metadata_lookup.cached_request(api,request,'Apple Music',url)
                self.assertEqual(request.call_count,1)
                with patch.object(metadata_lookup.time,'time',return_value=time.time()+301):
                    metadata_lookup.cached_request(api,request,'Apple Music',url)
                self.assertEqual(request.call_count,2)
            with patch.object(preparation,'request',side_effect=OSError('offline')) as request:
                payload,cached,warning=metadata_lookup.cached_request(api,request,'Apple Music',url,refresh=True)
                self.assertTrue(cached)
                self.assertIn('saved catalog',warning)

    def test_apple_cover_uses_selected_recording_and_checks_host(self):
        with tempfile.TemporaryDirectory() as tmp:
            api=SimpleNamespace(ROOT=Path(tmp),track=lambda db,id:{'metadata':{'values':{},'pending':{}}})
            def finish(job):
                for _ in range(200):
                    job=preparation.dispatch(api,None,'metadata.job',{'id':job['id']})
                    if job['state']!='running':return job
                    time.sleep(.01)
                self.fail('Cover timed out')
            with patch.object(metadata_lookup,'cached_request',return_value=({'results':[{'kind':'song','trackId':1,'artworkUrl100':'https://example.com/cover.jpg'}]},True,None)),patch.object(preparation,'request') as remote:
                result=finish(preparation.dispatch(api,None,'metadata.cover',{'id':'t','provider':'Apple Music','recording_id':'1'}))
                self.assertEqual(result['state'],'failed')
                remote.assert_not_called()

    def test_cover_requests_high_resolution_and_does_not_reuse_old_thumbnail(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            api = SimpleNamespace(ROOT=root, track=lambda db, id: {'metadata': {'values': {}, 'pending': {}}})
            def atomic(path, data): path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(data)
            api.atomic = atomic
            with patch.object(preparation, 'request', return_value=b'image') as remote, patch.object(preparation, 'validate_image', return_value='verified-image'):
                job = preparation.dispatch(api, None, 'metadata.cover', {'id': 't', 'release_id': '788cb31f-de60-4339-9152-f3edeef9f966'})
                deadline = time.monotonic()+5
                while job['state']=='running' and time.monotonic()<deadline:
                    time.sleep(.01); job = preparation.dispatch(api, None, 'metadata.job', {'id': job['id']})
                self.assertEqual(job['state'], 'completed')
                self.assertTrue(remote.call_args[0][0].endswith('/front-1200'))

    def test_titles_ending_in_a_version_word_are_not_split_without_a_delimiter(self):
        for title in ('In Da Club', 'Born to Live', 'The Original', 'Video Killed the Radio', 'Live', 'Radio', 'Going Acoustic', 'Artist - Live Forever'):
            with self.subTest(title=title): self.assertEqual(split_version(title), (title, ''))
        self.assertEqual(search_identity('50 Cent - In Da Club'), {'title': 'In Da Club', 'artist': '50 Cent', 'version': ''})
        self.assertEqual(search_identity('In Da Club', '50 Cent'), {'title': 'In Da Club', 'artist': '50 Cent', 'version': ''})
        ranked = rank_candidates({'recordings': [recording('club', 'In Da Club', '50 Cent', 193)]}, 193, search_identity('In Da Club', '50 Cent'))
        self.assertEqual(ranked[0]['match_status'], 'strong')

    def test_delimited_or_phrased_versions_are_still_split(self):
        cases = {
            'Song (Live)': ('Song', 'Live'), 'Song [Extended Mix]': ('Song', 'Extended Mix'),
            'Song - Live': ('Song', 'Live'), 'Song - Radio Edit': ('Song', 'Radio Edit'), 'Song – Club': ('Song', 'Club'),
            'Song - 2011 Remaster': ('Song', '2011 Remaster'), 'Song - Remastered 2011': ('Song', 'Remastered 2011'),
            'Song Extended Mix': ('Song', 'Extended Mix'), 'Song Radio Edit': ('Song', 'Radio Edit'), 'Song Club Mix': ('Song', 'Club Mix'),
            'Song Original Mix': ('Song', 'Original Mix'), 'Song Live Version': ('Song', 'Live Version'), 'Song Instrumental': ('Song', 'Instrumental'),
            'In Da Club (Club Mix)': ('In Da Club', 'Club Mix'),
        }
        for title, expected in cases.items():
            with self.subTest(title=title): self.assertEqual(split_version(title), expected)
        self.assertEqual(search_identity('Song - Live', 'Artist'), {'title': 'Song', 'artist': 'Artist', 'version': 'Live'})

    def test_remixer_phrase_keeps_its_word_order(self):
        plan = query_plan(search_identity('Low (Travis Barker Remix)', 'Flo Rida'), 230)
        self.assertIn('recording:"travis barker" OR comment:"travis barker"', plan[0])
        repeated = query_plan({'title': 'Song', 'artist': '', 'version': 'Zed Remix / Zed Alpha Remix'}, None)
        self.assertIn('recording:"zed alpha"', repeated[0])

    def test_primary_artist_keeps_names_that_contain_a_comma_before_the(self):
        cases = {
            'Tyler, The Creator': 'Tyler, The Creator', 'Tyler, The Creator, Kali Uchis': 'Tyler, The Creator',
            'Tyler, The Creator feat. Kali Uchis': 'Tyler, The Creator', 'Tyler, The Creator; Kali Uchis': 'Tyler, The Creator',
            'Flo Rida,T-Pain': 'Flo Rida', 'Flo Rida, T-Pain & Sia': 'Flo Rida', 'Calvin Harris, The Chemical Brothers': 'Calvin Harris',
            'Artist, Theo Parrish': 'Artist', 'Drake; Future': 'Drake', 'Flo Rida ft. T-Pain': 'Flo Rida',
            # Unchanged: serial-comma band names still reduce to their first word for retrieval only.
            'Earth, Wind & Fire': 'Earth',
        }
        for credit, expected in cases.items():
            with self.subTest(credit=credit): self.assertEqual(primary_artist(credit), expected)
        self.assertIn('artist:"Tyler, The Creator"', query_plan(catalog_identity('EARFQUAKE', 'Tyler, The Creator'), 190)[0])
        # The full credit still scores a band name correctly even though retrieval uses its first word.
        ranked = rank_candidates({'recordings': [recording('ewf', 'September', 'Earth, Wind & Fire', 215)]}, 215, search_identity('September', 'Earth, Wind & Fire'))
        self.assertEqual(ranked[0]['match_status'], 'strong')

    def test_a_bracketed_alternative_name_on_one_side_still_matches(self):
        identity = catalog_identity("Ain't No Way", 'Chris Brown')
        ranked = rank_candidates({'recordings': [recording('cb', 'Ain’t No Way (You Won’t Love Me)', 'Chris Brown', 203),
                                                 recording('cb2', "Ain't No Way (Part 2)", 'Chris Brown', 203),
                                                 recording('cb3', "Ain't No Way (Interlude)", 'Chris Brown', 203)]}, 203, identity)
        by_id = {c['recording_id']: c for c in ranked}
        self.assertEqual((by_id['cb']['match_status'], by_id['cb']['title_similarity']), ('strong', .96))
        # A part or an interlude is a different recording, not an alternative name.
        for other in ('cb2', 'cb3'):
            self.assertNotEqual(by_id[other]['match_status'], 'strong')
            self.assertLess(by_id[other]['title_similarity'], .9)
        self.assertEqual(title_similarity('Ain’t No Way (You Won’t Love Me)', "Aint No Way"), .96)
        self.assertEqual(title_similarity('Hold On - Radio Version Two', 'Hold On'), .93)
        self.assertEqual(clean_name('Song (Explicit) [Official Video]'), 'Song')

    def test_search_layers_try_the_exchanged_and_shorter_readings(self):
        layers = [(identity['title'], identity['artist'], note) for identity, note in identity_layers('Chris Brown', "2 . Ain't No Way (You Won't Love Me)")]
        self.assertEqual(layers[:2], [('Chris Brown', "Ain't No Way (You Won't Love Me)", ''),
                                      ("Ain't No Way (You Won't Love Me)", 'Chris Brown', 'title and artist exchanged')])
        self.assertIn(("Ain't No Way", 'Chris Brown', 'title and artist exchanged, without the name in brackets'), layers)
        # An "Artist - Song" title with an uploader in the artist field.
        layers = [(identity['title'], identity['artist']) for identity, _ in identity_layers('Chris Brown - Yeah 3x', 'LyricsVault', loose=True)]
        self.assertIn(('Yeah 3x', 'Chris Brown'), layers)
        self.assertEqual(layers[-1], ('Chris Brown - Yeah 3x', ''))


if __name__ == '__main__': unittest.main()
