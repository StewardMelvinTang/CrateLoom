from contextlib import ExitStack
import json
from pathlib import Path
import sqlite3
import subprocess
import tempfile
import threading
import time
import unittest
import uuid
from unittest.mock import patch

from crateloom import __main__ as api
from crateloom import acquisitions, scan_audio, set_discovery, set_import, set_scan_jobs, soundcloud_download, youtube_audio
from crateloom.library_imports import ImportCancelled

FINAL = ('ready', 'choose', 'none', 'failed')


def video(vid, title, channel='Artist', duration=200):
    return {'id': vid, 'title': title, 'channel': channel, 'duration': duration, 'channel_is_verified': True}


class Discovery(unittest.TestCase):
    """Source discovery after a scan, with every network step replaced by a fake."""

    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory())).resolve()
        for name, value in [('ROOT', self.root), ('DB', self.root/'library.sqlite3'), ('CACHE', self.root/'cache')]:
            self.stack.enter_context(patch.object(api, name, value))
        self.reference = self.stack.enter_context(patch.object(set_import, 'reference_for', return_value=None))
        self.stack.enter_context(patch.object(soundcloud_download, 'configured', return_value=False))
        self.videos = {}
        self.search = self.stack.enter_context(patch.object(youtube_audio, 'search', side_effect=self.find))
        set_discovery.CLOSING.clear()
        self.addCleanup(self.stop_discovery)
        self.addCleanup(self.close_manager)

    def close_manager(self):
        manager = acquisitions.MANAGERS.pop(str(self.root), None)
        if manager:
            manager.close()

    def stop_discovery(self):
        with set_discovery.LOCK:
            sids = [sid for root, sid in set_discovery.WORK if root == str(self.root)]
        for sid in sids:
            set_discovery.cancel(api, sid)
        set_discovery.CLOSING.clear()
        deadline = time.monotonic()+3
        while time.monotonic() < deadline and any(root == str(self.root) for root, _ in list(set_discovery.WORK)):
            time.sleep(.02)

    def find(self, api_module, query):
        for words, found in self.videos.items():
            if query.startswith(words):
                return list(found)
        return []

    def finish(self, task):
        deadline = time.monotonic()+8
        while task['state'] in ('running', 'cancelling') and time.monotonic() < deadline:
            time.sleep(.02)
            task = api.dispatch('metadata.job', {'id': task['id']})
        self.assertEqual(task['state'], 'completed', task)
        return task['result']

    def settled(self, sid, timeout=8):
        deadline = time.monotonic()+timeout
        while time.monotonic() < deadline:
            report = api.dispatch('sets.get', {'id': sid})
            if not report['discovery']['pending'] and not report['discovery']['running']:
                return report
            time.sleep(.02)
        self.fail(f'Discovery did not finish: {report["discovery"]}')

    def scan(self, tracklist):
        return self.finish(api.dispatch('sets.scan', {'tracklist': tracklist}))

    def test_discovery_starts_after_report_saved_without_enqueue(self):
        self.videos['Artist First'] = [video('AAAAAAAAAA1', 'Artist - First (Official Audio)')]
        self.videos['Artist Second'] = [video('AAAAAAAAAA2', 'Artist - Second (Official Audio)')]
        with patch.object(acquisitions.Manager, 'enqueue_in', side_effect=AssertionError('Discovery never enqueues')) as enqueue:
            saved = self.scan('0:00 Artist - First\n1:00 Artist - Second\n2:00 ID - ID')
            self.assertTrue(all(row['source_search'] in ('queued', 'searching', 'ready') for row in saved['tracks'][:2]), saved['tracks'])
            report = self.settled(saved['id'])
        enqueue.assert_not_called()
        self.assertEqual([row['source_search'] for row in report['tracks']], ['ready', 'ready', None])
        self.assertEqual(report['tracks'][0]['source_options'][0]['url'], 'https://www.youtube.com/watch?v=AAAAAAAAAA1')
        self.assertTrue(report['tracks'][0]['source_options'][0]['recommended'])
        self.assertTrue(all(row['download_id'] is None for row in report['tracks']))
        self.assertEqual(api.dispatch('acquisitions.list', {}), [])

    def test_listed_link_skips_musicbrainz_and_search(self):
        link = 'https://www.youtube.com/watch?v=BBBBBBBBBB1'
        self.reference.side_effect = AssertionError('No catalog lookup for a listed link')
        report = self.settled(self.scan(f'0:00 Artist - Listed {link}')['id'])
        self.assertEqual(report['tracks'][0]['source_search'], 'ready')
        self.search.assert_not_called()
        self.reference.assert_not_called()
        # Creating the playlist uses the listed link the same way, without the catalog.
        row = set_import.listed_item(0, f'Artist - Listed {link}', 'Video description')
        with patch.object(youtube_audio, 'resolve', return_value=({'kind': 'direct', 'url': link, 'duration': 200}, None, {})) as resolve:
            params, error = set_import.source_for(api, row, None)
        self.assertIsNone(error)
        self.assertTrue(params['source_id'])
        self.assertEqual(resolve.call_count, 1)
        self.reference.assert_not_called()

    def test_musicbrainz_does_not_block_ready_state(self):
        self.videos['Artist First'] = [video('CCCCCCCCCC1', 'Artist - First (Official Audio)')]
        entered, release = threading.Event(), threading.Event()
        recording = '11111111-1111-4111-8111-111111111111'
        def catalog(*args):
            entered.set()
            release.wait(5)
            return {'id': 'registered', 'recording_id': None, 'release_id': None, 'values': {'title': 'First'},
                    'source_url': 'https://musicbrainz.org/recording/' + recording}
        self.reference.side_effect = catalog
        saved = self.scan('0:00 Artist - First')
        try:
            report = self.settled(saved['id'])
            self.assertTrue(entered.wait(2))
            self.assertEqual(report['tracks'][0]['source_search'], 'ready')
        finally:
            release.set()
        deadline = time.monotonic()+3
        while time.monotonic() < deadline:
            report = api.dispatch('sets.get', {'id': saved['id']})
            if any(link['label'] == 'MusicBrainz' for link in report['tracks'][0].get('links', [])):
                break
            time.sleep(.02)
        self.assertIn('MusicBrainz', [link['label'] for link in report['tracks'][0]['links']])
        with api.connect() as db:
            stored = json.loads(db.execute('SELECT detail FROM dj_set_items').fetchone()[0])['discovery']
        self.assertEqual(stored['state'], 'ready')
        self.assertNotIn('id', stored['reference'])

    def test_cancel_after_searches_stops_catalog_enrichment(self):
        self.videos['Artist First'] = [video('CCCCCCCCCC1', 'Artist - First (Official Audio)')]
        entered, release = threading.Event(), threading.Event()
        def catalog(*args):
            entered.set()
            release.wait(5)
            return {'id': 'registered', 'recording_id': None, 'release_id': None, 'values': {'title': 'First'},
                    'source_url': 'https://musicbrainz.org/recording/11111111-1111-4111-8111-111111111111'}
        self.reference.side_effect = catalog
        saved = self.scan('0:00 Artist - First')
        try:
            self.settled(saved['id'])
            self.assertTrue(entered.wait(2))
            # The searches are done, but "Create playlist" or delete must still stop the catalog step.
            set_discovery.cancel(api, saved['id'])
        finally:
            release.set()
        deadline = time.monotonic()+3
        while time.monotonic() < deadline and any(root == str(self.root) for root, _ in list(set_discovery.WORK)):
            time.sleep(.02)
        report = api.dispatch('sets.get', {'id': saved['id']})
        self.assertNotIn('MusicBrainz', [link['label'] for link in report['tracks'][0].get('links', [])])
        self.assertEqual(report['tracks'][0]['source_search'], 'ready')

    def test_search_slots_never_exceed_four(self):
        tag = uuid.uuid4().hex[:8]
        active, peak, lock = [0], [0], threading.Lock()
        def process(args, timeout, **kwargs):
            with lock:
                active[0] += 1
                peak[0] = max(peak[0], active[0])
            try:
                time.sleep(.15)
            finally:
                with lock:
                    active[0] -= 1
            return subprocess.CompletedProcess(args, 0, '', '')
        # The real search, so the slots inside it are what bounds the yt-dlp processes.
        with patch.object(youtube_audio, 'search', new=self.real_search), \
             patch.object(youtube_audio, 'executable', return_value='/tools/yt-dlp'), \
             patch.object(api, 'import_process', side_effect=process):
            saved = self.scan('\n'.join(f'{index}:00 Artist{tag} - Song {index} {tag}' for index in range(8)))
            report = self.settled(saved['id'], timeout=15)
        self.assertEqual({row['source_search'] for row in report['tracks']}, {'none'})
        self.assertLessEqual(peak[0], 4)
        self.assertGreaterEqual(peak[0], 3)

    real_search = staticmethod(youtube_audio.search)

    def test_searches_wait_for_mix_acquisition(self):
        self.videos['Artist First'] = [video('DDDDDDDDDD1', 'Artist - First (Official Audio)')]
        scan_audio.acquiring(1)
        try:
            saved = self.scan('0:00 Artist - First')
            time.sleep(.6)
            self.search.assert_not_called()
            self.assertIn(api.dispatch('sets.get', {'id': saved['id']})['tracks'][0]['source_search'], ('queued', 'searching'))
        finally:
            scan_audio.acquiring(-1)
        self.assertEqual(self.settled(saved['id'])['tracks'][0]['source_search'], 'ready')
        self.assertTrue(self.search.called)

    def fake_enqueue(self):
        queued = []
        def enqueue(manager, db, params, **kwargs):
            jid = str(uuid.uuid4())
            review = {'source': {'url': 'https://example.org/a.m4a', 'filename': 'a.m4a'}, 'fields': params.get('values', {})}
            db.execute("INSERT INTO acquisitions(id,state,stage,review,created_at,updated_at) VALUES (?,?,?,?,?,?)",
                       (jid, 'queued', 'queued', json.dumps(review), 'now', 'now'))
            queued.append(params)
            return jid
        self.stack.enter_context(patch.object(acquisitions.Manager, 'enqueue_in', new=enqueue))
        self.stack.enter_context(patch.object(acquisitions.Manager, 'ensure_worker'))
        return queued

    def playlist(self, sid):
        playlist = api.dispatch('playlists.save', {'name': 'Set', 'track_ids': []})
        with api.connect() as db:
            db.execute('UPDATE dj_sets SET playlist_id=? WHERE id=?', (playlist['id'], sid))

    def test_prepare_uses_fresh_discovery_without_search(self):
        self.videos['Artist First'] = [video('EEEEEEEEEE1', 'Artist - First (Official Audio)')]
        self.videos['Artist Second'] = [video('EEEEEEEEEE2', 'Artist - Second (Official Audio)')]
        saved = self.settled(self.scan('0:00 Artist - First\n1:00 Artist - Second\n2:00 Artist - Missing')['id'])
        self.assertEqual([row['source_search'] for row in saved['tracks']], ['ready', 'ready', 'none'])
        deadline = time.monotonic()+3
        while time.monotonic() < deadline:
            with api.connect() as db:
                found = [json.loads(row[0])['discovery'] for row in db.execute('SELECT detail FROM dj_set_items ORDER BY position')]
            if all(item.get('catalog') for item in found):
                break
            time.sleep(.02)
        self.assertTrue(all(item.get('catalog') for item in found), found)
        queued = self.fake_enqueue()
        self.search.reset_mock()
        self.reference.side_effect = AssertionError('A fresh discovery skips the catalog')
        def resolve(api_module, reference, *args, **kwargs):
            return {'kind': 'direct', 'url': reference[1], 'duration': 200, 'values': {'title': '', 'artist': ''}}, None, {}
        with patch.object(youtube_audio, 'resolve', side_effect=resolve) as resolved:
            report = self.finish(api.dispatch('sets.prepare', {'id': saved['id'], 'name': 'Set'}))
        self.assertEqual(self.search.call_count, 0)
        self.assertEqual(resolved.call_count, 2)
        self.assertEqual(len(queued), 2)
        self.assertTrue(all(row['download_id'] for row in report['tracks'][:2]))
        self.assertIn('No matching audio source', report['tracks'][2]['error'])

    def test_merge_detail_preserves_artwork_and_skips_linked_rows(self):
        self.videos['Artist First'] = [video('FFFFFFFFFF1', 'Artist - First (Official Audio)')]
        self.videos['Artist Second'] = [video('FFFFFFFFFF2', 'Artist - Second (Official Audio)')]
        entered, release = threading.Event(), threading.Event()
        def find(api_module, query):
            if query == 'Artist First' and threading.current_thread().name.startswith('set-discovery'):
                entered.set()
                while not release.wait(.05):
                    pass
            return self.find(api_module, query)
        self.search.side_effect = find
        saved = self.scan('0:00 Artist - First\n1:00 Artist - Second')
        sid = saved['id']
        first, second = [row['entry_id'] for row in saved['tracks']]
        self.assertTrue(entered.wait(3))
        deadline = time.monotonic()+3
        while api.dispatch('sets.get', {'id': sid})['tracks'][1]['source_search'] != 'ready' and time.monotonic() < deadline:
            time.sleep(.02)
        artwork = str(self.root/'cover.image')
        with api.connect() as db:
            db.execute("UPDATE dj_set_items SET detail=json_set(detail,'$.artwork_path',?) WHERE rowid=?", (artwork, first))
        self.playlist(sid)
        api.dispatch('sets.reorder', {'id': sid, 'entry_ids': [second, first]})
        queued = self.fake_enqueue()
        def resolve(api_module, reference, *args, **kwargs):
            return {'kind': 'direct', 'url': reference[1], 'duration': 200, 'values': {'title': '', 'artist': ''}}, None, {}
        try:
            # Even if discovery were not stopped, its late write must not undo the prepared entry.
            with patch.object(set_discovery, 'cancel'), patch.object(youtube_audio, 'resolve', side_effect=resolve):
                report = self.finish(api.dispatch('sets.prepare', {'id': sid}))
        finally:
            release.set()
        report = self.settled(sid)
        time.sleep(.1)
        report = api.dispatch('sets.get', {'id': sid})
        self.assertEqual([row['entry_id'] for row in report['tracks']], [second, first])
        self.assertEqual(len(queued), 2)
        self.assertEqual(len(api.dispatch('acquisitions.list', {})), 2)
        by_id = {row['entry_id']: row for row in report['tracks']}
        self.assertEqual(by_id[first]['source_options'][0]['url'][-11:], 'FFFFFFFFFF1')
        self.assertEqual(by_id[second]['source_options'][0]['url'][-11:], 'FFFFFFFFFF2')
        self.assertEqual(by_id[first]['artwork_path'], artwork)
        self.assertTrue(all(row['download_id'] for row in report['tracks']))
        with api.connect() as db:
            stored = json.loads(db.execute('SELECT detail FROM dj_set_items WHERE rowid=?', (first,)).fetchone()[0])
        self.assertEqual(stored['artwork_path'], artwork)
        self.assertNotEqual(stored['discovery']['state'], 'ready')

    def test_delete_during_discovery_is_clean(self):
        entered, release = threading.Event(), threading.Event()
        def find(api_module, query):
            entered.set()
            while not release.wait(.05):
                set_scan_jobs.check()
            return []
        self.search.side_effect = find
        saved = self.scan('0:00 Artist - First\n1:00 Artist - Second')
        try:
            self.assertTrue(entered.wait(3))
            self.assertEqual(api.dispatch('sets.delete', {'id': saved['id']}), {'deleted': saved['id']})
            deadline = time.monotonic()+3
            while set_discovery.active(api, saved['id']) != (set(), set()) and time.monotonic() < deadline:
                time.sleep(.02)
            self.assertEqual(set_discovery.active(api, saved['id']), (set(), set()))
        finally:
            release.set()
        time.sleep(.2)
        with api.connect() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM dj_set_items').fetchone()[0], 0)
        self.assertEqual(api.dispatch('sets.list', {}), [])

    def test_shutdown_stops_discovery_and_kills_ytdlp(self):
        tag = uuid.uuid4().hex[:8]
        started, killed = threading.Event(), threading.Event()
        def process(args, timeout, **kwargs):
            started.set()
            try:
                while True:
                    api.check_import_cancel()
                    time.sleep(.02)
            except ImportCancelled:
                killed.set()
                raise
        with patch.object(youtube_audio, 'search', new=self.real_search), \
             patch.object(youtube_audio, 'executable', return_value='/tools/yt-dlp'), \
             patch.object(api, 'import_process', side_effect=process):
            saved = self.scan(f'0:00 Artist{tag} - Song {tag}')
            self.assertTrue(started.wait(3))
            set_scan_jobs.shutdown()
            self.assertTrue(killed.wait(2))
            report = self.settled(saved['id'])
            self.assertEqual(set_discovery.schedule(api, saved['id']), 0)
        self.assertEqual(report['discovery'], {'pending': 0, 'running': 0})
        # Left half searched, it is offered as waiting and searched again at the next start.
        self.assertEqual(report['tracks'][0]['source_search'], 'queued')

    def test_report_reuses_library_index(self):
        saved = set_import.save_report(api, 'Mix', None, set_import.parse_tracklist('0:00 Artist - First', 'Pasted tracklist'), None)
        index = set_import.build_library_index
        with patch.dict(set_import.INDEX_CACHE, clear=True), patch.object(set_import, 'build_library_index', side_effect=index) as built:
            for _ in range(5):
                api.dispatch('sets.get', {'id': saved['id']})
        self.assertEqual(built.call_count, 1)

    def test_title_only_rows_are_offered_but_never_ready(self):
        self.videos['SPEND DAT DONK'] = [video('GGGGGGGGGG1', 'Spend Dat Donk (C-Bu Edit)', channel='C-Bu')]
        saved = self.scan('0:00 SPEND DAT DONK (C-BU EDIT)\n1:00 Intro')
        report = self.settled(saved['id'])
        offered, short = report['tracks']
        self.assertEqual((offered['artist'], offered['source_search']), ('', 'choose'))
        self.assertIsNone(short['source_search'])
        option = offered['source_options'][0]
        self.assertTrue(option['title_only'])
        self.assertNotIn('recommended', option)
        self.assertNotIn('compatible', option)
        # Creating the playlist never picks a source for it.
        queued = self.fake_enqueue()
        report = self.finish(api.dispatch('sets.prepare', {'id': saved['id']}))
        self.assertEqual(queued, [])
        self.assertIsNone(report['tracks'][0]['download_id'])
        self.assertEqual(api.dispatch('sets.apply_recommended', {'id': saved['id']}), [])

    def test_title_only_rows_not_yet_searched_are_offered_when_the_playlist_is_created(self):
        self.videos['SPEND DAT DONK'] = [video('JJJJJJJJJJ1', 'Spend Dat Donk (C-Bu Edit)', channel='C-Bu')]
        link = 'https://www.youtube.com/watch?v=JJJJJJJJJJ2'
        # The owner creates the playlist before discovery reached these rows.
        with patch.object(set_discovery, 'schedule', return_value=0):
            saved = self.scan(f'0:00 SPEND DAT DONK (C-BU EDIT)\n1:00 Bass Line {link}')
        titled, listed = saved['tracks']
        self.assertEqual((titled['artist'], titled['source_search'], titled['listed_link']), ('', None, False))
        # A row with a listed link but no artist is prepared from its link, not searched by title.
        self.assertEqual((listed['artist'], listed['listed_link']), ('', True))
        queued = self.fake_enqueue()
        def resolve(api_module, reference, *args, **kwargs):
            return {'kind': 'direct', 'url': reference[1], 'duration': 200, 'values': {'title': '', 'artist': ''}}, None, {}
        with patch.object(youtube_audio, 'resolve', side_effect=resolve):
            report = self.finish(api.dispatch('sets.prepare', {'id': saved['id'], 'name': 'Set'}))
        titled, listed = report['tracks']
        self.assertEqual(titled['source_search'], 'choose')
        self.assertEqual(titled['source_options'][0]['url'][-11:], 'JJJJJJJJJJ1')
        self.assertIsNone(titled['download_id'])
        self.assertEqual([params.get('source_id') is not None for params in queued], [True])
        self.assertTrue(listed['download_id'])
        self.assertTrue(self.search.called)
        self.assertTrue(all(call.args[1].startswith('SPEND DAT DONK') for call in self.search.call_args_list))

    def test_failed_create_playlist_leaves_discovery_running(self):
        self.videos['Artist First'] = [video('KKKKKKKKKK1', 'Artist - First (Official Audio)')]
        entered, release = threading.Event(), threading.Event()
        def find(api_module, query):
            entered.set()
            while not release.wait(.05):
                set_scan_jobs.check()
            return self.find(api_module, query)
        self.search.side_effect = find
        saved = self.scan('0:00 Artist - First')
        try:
            self.assertTrue(entered.wait(3))
            real = set_import.preparation.dispatch
            def dispatch(api_module, db, method, params):
                if method == 'playlists.save':
                    raise sqlite3.OperationalError('database is locked')
                return real(api_module, db, method, params)
            with patch.object(set_import.preparation, 'dispatch', side_effect=dispatch):
                task = api.dispatch('sets.prepare', {'id': saved['id'], 'name': 'Set'})
                deadline = time.monotonic()+8
                while task['state'] == 'running' and time.monotonic() < deadline:
                    time.sleep(.02)
                    task = api.dispatch('metadata.job', {'id': task['id']})
            self.assertEqual(task['state'], 'failed')
            # No playlist was created, so the search in progress was never stopped.
            self.assertEqual(len(set_discovery.active(api, saved['id'])[1]), 1)
        finally:
            release.set()
        report = self.settled(saved['id'])
        self.assertIsNone(report['playlist_id'])
        self.assertEqual(report['tracks'][0]['source_search'], 'ready')

    def test_rows_left_waiting_without_a_search_are_queued_again(self):
        self.videos['Artist First'] = [video('LLLLLLLLLL1', 'Artist - First (Official Audio)')]
        real = set_discovery.merge_detail
        lost = []
        def merge(db, sid, entry_id, key, fields, **kwargs):
            # The finished result cannot be saved twice in a row (the library stays busy).
            if isinstance(fields, dict) and fields.get('discovery', {}).get('state') == 'ready' and len(lost) < 2:
                lost.append(entry_id)
                raise sqlite3.OperationalError('database is locked')
            return real(db, sid, entry_id, key, fields, **kwargs)
        with patch.object(set_discovery, 'merge_detail', side_effect=merge), patch.object(set_discovery, 'RETRY_WRITE', 0):
            saved = self.scan('0:00 Artist - First')
            deadline = time.monotonic()+3
            while len(lost) < 2 and time.monotonic() < deadline:
                time.sleep(.02)
            self.assertEqual(len(lost), 2)
            once = self.search.call_count
            # Opening the report finds the row waiting with nothing behind it, and searches it again.
            report = self.settled(saved['id'])
        self.assertEqual(report['tracks'][0]['source_search'], 'ready')
        self.assertEqual(self.search.call_count, 2*once)

    def test_one_failed_result_write_is_retried(self):
        self.videos['Artist First'] = [video('MMMMMMMMMM1', 'Artist - First (Official Audio)')]
        real = set_discovery.merge_detail
        lost = []
        def merge(db, sid, entry_id, key, fields, **kwargs):
            if isinstance(fields, dict) and fields.get('discovery', {}).get('state') == 'ready' and not lost:
                lost.append(entry_id)
                raise sqlite3.OperationalError('database is locked')
            return real(db, sid, entry_id, key, fields, **kwargs)
        with patch.object(set_discovery, 'merge_detail', side_effect=merge), patch.object(set_discovery, 'RETRY_WRITE', 0), \
             patch.object(set_discovery, 'sweep', side_effect=AssertionError('Saved on the second try')):
            report = self.settled(self.scan('0:00 Artist - First')['id'])
        self.assertEqual(len(lost), 1)
        self.assertEqual(report['tracks'][0]['source_search'], 'ready')


if __name__ == '__main__':
    unittest.main()
