from contextlib import ExitStack
import json
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
from crateloom import analysis_worker, fast_sections, playback, preparation, set_artwork, set_import, structure_worker


class Workspace(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        for name, value in [('ROOT', self.root), ('DB', self.root / 'library.sqlite3'), ('CACHE', self.root / 'cache')]:
            self.stack.enter_context(patch.object(api, name, value))
        api.CACHE.mkdir()

    def audio(self, name='track.aiff', duration=2):
        path = self.root / name
        frequency = 300 + sum(name.encode()) % 500
        subprocess.run(['ffmpeg', '-nostdin', '-v', 'error', '-f', 'lavfi', '-i', f'sine=frequency={frequency}:duration={duration}', str(path)], check=True)
        return api.dispatch('library.import', {'path': str(path)})

    def saved(self, count=3):
        rows = [set_import.listed_item(index * 20, f'Artist - Song {index}', 'Test') for index in range(count)]
        return set_import.save_report(api, 'Set', None, rows, None)

    def finish(self, task):
        deadline = time.monotonic() + 8
        while task['state'] == 'running' and time.monotonic() < deadline:
            time.sleep(.01)
            task = preparation.dispatch(api, None, 'metadata.job', {'id': task['id']})
        self.assertEqual(task['state'], 'completed', task)
        return task['result']

    def test_recommendations_keep_ten_prefer_extended_and_preserve_named_remix(self):
        row = {'artist': 'Artist', 'title': 'Song', 'duration': 180, 'source_options': [
            {'title': 'Artist Song (Official Music Video)', 'url': 'video', 'duration': 230},
            {'title': 'Artist Song (Extended Mix)', 'url': 'extended', 'duration': 260},
            {'title': 'Artist Song (Other Remix)', 'url': 'wrong', 'duration': 170},
            *[{'title': 'Artist Song Audio', 'url': str(i), 'duration': 180} for i in range(12)],
        ]}
        best = set_import.recommend_sources(row)
        self.assertEqual(best['url'], 'extended')
        self.assertEqual(len(row['source_options']), 10)
        self.assertEqual(sum(bool(option.get('recommended')) for option in row['source_options']), 1)
        row['title'] = 'Song (DJ Remix)'
        self.assertIsNone(set_import.recommend_sources(row))
        row['source_options'].append({'title': 'Artist Song (DJ Extended Remix)', 'url': 'named-extended', 'duration': 270})
        self.assertEqual(set_import.recommend_sources(row)['url'], 'named-extended')

    def test_sole_compatible_source_is_resolved_without_extra_review(self):
        row = set_import.listed_item(0, 'Artist - Song', 'Test')
        row['needs_review'] = True
        video = {'title': 'Artist - Song (Official Audio)', 'channel': 'Artist', 'duration': 180, 'id': '12345678901'}
        wrong = dict(video, id='98765432109', title='Artist - Song (Different Remix)')
        with patch.object(set_import.youtube_audio, 'search', return_value=[wrong, video]), patch.object(set_import.youtube_audio, 'resolve', return_value=({'kind': 'direct', 'duration': 180}, None, {})) as resolve:
            source, error = set_import.find_video_source(api, row, None)
        self.assertIsNotNone(source)
        self.assertIsNone(error)
        self.assertEqual(resolve.call_count, 1)
        self.assertEqual(sum(option['compatible'] for option in row['source_options']), 1)

    def test_sole_exact_soundcloud_source_is_accepted_when_authenticated(self):
        row = set_import.listed_item(0, 'Artist - Song', 'Test')
        row['needs_review'] = True
        entry = {'title': 'Song', 'uploader': 'Artist', 'duration': 180, 'url': 'https://soundcloud.com/artist/song'}
        with patch.object(set_import.soundcloud_download, 'search', return_value=[entry]), patch.object(set_import.soundcloud_download, 'configured', return_value=True), patch.object(set_import, 'soundcloud_source', return_value={'id': 'source'}) as resolve:
            self.assertEqual(set_import.find_soundcloud_source(api, row), {'id': 'source'})
        resolve.assert_called_once_with(api, entry['url'])

    def test_source_queue_accepts_more_than_two_and_bounds_running_work(self):
        release = threading.Event()
        started, lock = [], threading.Lock()
        def work():
            with lock:
                started.append(1)
            release.wait(5)
            return True
        try:
            tasks = [preparation.launch(work) for _ in range(9)]
            deadline = time.monotonic() + 2
            while len(started) < 4 and time.monotonic() < deadline:
                time.sleep(.01)
            self.assertEqual(len(started), 4)
            self.assertEqual(len(tasks), 9)
        finally:
            release.set()
        for task in tasks:
            self.assertTrue(self.finish(task))

    def test_reorder_uses_stable_ids_and_updates_playlist_order(self):
        report = self.saved()
        first, second = self.audio('first.wav'), self.audio('second.wav')
        playlist = api.dispatch('playlists.save', {'name': 'Set'})
        with api.connect() as db:
            db.execute('UPDATE dj_sets SET playlist_id=? WHERE id=?', (playlist['id'], report['id']))
            set_import.attach_track(db, report['id'], 0, first['id'])
            set_import.attach_track(db, report['id'], 1, second['id'])
        ids = [row['entry_id'] for row in report['tracks']][::-1]
        reordered = api.dispatch('sets.reorder', {'id': report['id'], 'entry_ids': ids})
        self.assertEqual([row['entry_id'] for row in reordered['tracks']], ids)
        self.assertEqual([row['position'] for row in reordered['tracks']], [0, 1, 2])
        self.assertEqual(api.dispatch('playlists.list', {})[0]['track_ids'], [second['id'], first['id']])
        late = self.audio('late.wav')
        with api.connect() as db:
            set_import.attach_track(db, report['id'], 0, late['id'])
        self.assertEqual(api.dispatch('playlists.list', {})[0]['track_ids'], [late['id'], second['id'], first['id']])
        with self.assertRaises(ValueError):
            api.dispatch('sets.reorder', {'id': report['id'], 'entry_ids': ids[:-1]})

    def test_pending_source_stays_on_same_song_after_reorder(self):
        report = self.saved()
        playlist = api.dispatch('playlists.save', {'name': 'Set'})
        with api.connect() as db:
            db.execute('UPDATE dj_sets SET playlist_id=? WHERE id=?', (playlist['id'], report['id']))
        entered, release = threading.Event(), threading.Event()
        def lookup(*args):
            entered.set()
            release.wait(5)
            return None, 'Test selection result'
        with patch.object(set_import, 'source_for', side_effect=lookup):
            task = api.dispatch('sets.add_source', {'id': report['id'], 'entry_id': report['tracks'][0]['entry_id'], 'url': 'https://artist.example/song.wav'})
            try:
                self.assertTrue(entered.wait(2))
                api.dispatch('sets.reorder', {'id': report['id'], 'entry_ids': [row['entry_id'] for row in report['tracks']][::-1]})
            finally:
                release.set()
            self.finish(task)
        rows = api.dispatch('sets.get', {'id': report['id']})['tracks']
        self.assertEqual(rows[2]['title'], 'Song 0')
        self.assertEqual(rows[2]['error'], 'Test selection result')
        self.assertIsNone(rows[0]['error'])

    def test_artwork_arriving_after_import_is_staged_without_changing_audio(self):
        report = self.saved(1)
        track = self.audio('original.wav')
        original = Path(track['path']).read_bytes()
        artwork = self.root / 'artwork' / 'cover.png'
        artwork.parent.mkdir()
        artwork.write_bytes(b'cached-image')
        playlist = api.dispatch('playlists.save', {'name': 'Set'})
        with api.connect() as db:
            db.execute('UPDATE dj_sets SET playlist_id=? WHERE id=?', (playlist['id'], report['id']))
            set_import.attach_track(db, report['id'], 0, track['id'])
        with patch.object(set_artwork, 'find', return_value=str(artwork)):
            result = set_artwork.enrich(api, report['id'])
        self.assertEqual(result['tracks'][0]['artwork_path'], str(artwork))
        metadata = api.dispatch('track.playback', {'id': track['id']})['metadata']
        self.assertEqual(metadata['artwork_path'], str(artwork))
        self.assertTrue(metadata['artwork_pending'])
        self.assertEqual(Path(track['path']).read_bytes(), original)

    def test_playback_avoids_hashing_and_caches_webview_compatible_copy(self):
        track = self.audio()
        original = Path(track['path']).read_bytes()
        with patch.object(api, 'verify_asset', side_effect=AssertionError('Playback must not hash the audio')):
            self.assertEqual(api.dispatch('track.playback', {'id': track['id']})['id'], track['id'])
        result = playback.prepare(api, track)
        duration, stream, _ = api.probe(result['path'])
        self.assertEqual(stream['codec_name'], 'pcm_s16le')
        self.assertAlmostEqual(duration, track['duration'], places=2)
        with patch.object(playback.subprocess, 'run', side_effect=AssertionError('Cached copy must be reused')):
            self.assertEqual(playback.prepare(api, track), result)
        self.assertEqual(Path(track['path']).read_bytes(), original)

    def test_fast_sections_job_preserves_manual_sections_and_cues(self):
        track = self.audio('sections.wav', 12)
        cue = {'slot': 0, 'position_us': 1000000, 'label': 'Manual'}
        section = {'id': 'manual', 'start_us': 0, 'end_us': 1000000, 'label': 'Verse'}
        api.dispatch('cues.save', {'id': track['id'], 'cues': [cue]})
        api.dispatch('sections.save', {'id': track['id'], 'sections': [section]})
        job = api.dispatch('structure.enqueue', {'id': track['id'], 'mode': 'fast'})
        deadline = time.monotonic() + 8
        while job['state'] == 'running' and time.monotonic() < deadline:
            time.sleep(.02)
            job = api.dispatch('jobs.get', {'id': job['id']})
        self.assertEqual(job['state'], 'succeeded', job)
        updated = api.dispatch('track.get', {'id': track['id']})
        self.assertEqual(updated['cues'], [cue])
        self.assertIn(section, updated['sections'])
        self.assertEqual(updated['structure_analysis']['version'], fast_sections.VERSION)
        self.assertTrue(all(row['start_us'] >= 1000000 for row in updated['sections'] if row.get('origin') == 'model'))

    def test_fast_boundaries_respond_to_energy_changes(self):
        energy = [.2] * 32 + [.9] * 48 + [.1] * 32 + [.8] * 48 + [.2] * 32
        result = fast_sections.estimate(energy, [0] * len(energy), 96)
        self.assertGreaterEqual(len(result), 4)
        self.assertEqual(result[0]['start_us'], 0)
        self.assertEqual(result[-1]['end_us'], 96000000)
        self.assertTrue(all(a['end_us'] == b['start_us'] for a, b in zip(result, result[1:])))

    def poll(self, job):
        deadline = time.monotonic() + 8
        while job['state'] == 'running' and time.monotonic() < deadline:
            time.sleep(.02)
            job = api.dispatch('jobs.get', {'id': job['id']})
        return job

    def test_fast_sections_without_an_interpreter_reports_its_own_reason(self):
        track = self.audio('nopython.wav')
        with patch.object(api.sys, 'frozen', True, create=True), patch.dict(api.os.environ), patch.object(api.shutil, 'which', return_value=None):
            api.os.environ.pop('CRATELOOM_ANALYSIS_PYTHON', None)
            with self.assertRaisesRegex(ValueError, 'Fast section estimates are unavailable') as raised:
                api.dispatch('structure.enqueue', {'id': track['id'], 'mode': 'fast'})
            self.assertIsNone(api.dispatch('structure.enqueue', {'id': track['id'], 'mode': 'fast', 'automatic': True}))
        self.assertNotIn('Structure model', str(raised.exception))

    def test_finished_jobs_leave_no_status_file_and_stay_readable(self):
        self.assertEqual((api.ANALYSIS_VERSION, api.STRUCTURE_VERSION), (analysis_worker.VERSION, structure_worker.VERSION))
        track = self.audio('status.wav', 12)
        job = self.poll(api.dispatch('structure.enqueue', {'id': track['id'], 'mode': 'fast'}))
        self.assertEqual(job['state'], 'succeeded', job)
        self.assertEqual(list(api.CACHE.glob('*.status.json')), [])
        self.assertEqual(api.dispatch('jobs.get', {'id': job['id']}), job)
        self.assertEqual(api.dispatch('structure.enqueue', {'id': track['id'], 'mode': 'fast'})['stage'], 'Using cached analysis')

    def test_unreadable_status_file_does_not_break_job_polling(self):
        track = self.audio('stuck.wav')
        status = api.CACHE / 'stuck.status.json'
        status.write_text('{"state": "runn')
        worker = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'], start_new_session=True)
        self.addCleanup(api.stop_worker, worker)
        with patch.dict(api.WORKERS, {'stuck': worker}):
            with api.connect() as db:
                db.execute("INSERT INTO jobs (id,track_id,state,stage,status_path,output_path,cache_path,cache_key,error,kind) VALUES ('stuck',?,'running','Starting',?,?,?,'key',NULL,'beat')",
                           (track['id'], str(status), str(api.CACHE / 'stuck.partial.json'), str(api.CACHE / 'key.json')))
            self.assertEqual(api.dispatch('jobs.get', {'id': 'stuck'})['state'], 'running')
            with self.assertRaisesRegex(ValueError, 'Another analysis is running'):
                api.dispatch('analysis.enqueue', {'id': track['id']})
            with self.assertRaisesRegex(ValueError, 'still running'):
                api.dispatch('library.remove', {'ids': [track['id']]})
            api.stop_worker(worker)
            failed = api.dispatch('jobs.get', {'id': 'stuck'})
        self.assertEqual((failed['state'], failed['error']), ('failed', 'Analysis worker exited unexpectedly'))
        self.assertFalse(status.exists())
        self.assertEqual(api.dispatch('jobs.get', {'id': 'stuck'}), failed)

    def test_playback_cache_evicts_least_recently_used_copies(self):
        first, second = self.audio('one.wav'), self.audio('two.wav')
        originals = [Path(track['path']).read_bytes() for track in (first, second)]
        old = Path(playback.prepare(api, first)['path'])
        os.utime(old, (1000, 1000))
        self.assertEqual(playback.prepare(api, first)['path'], str(old))
        self.assertGreater(old.stat().st_mtime, 1000)  # A cache hit counts as recent use.
        os.utime(old, (1000, 1000))
        abandoned = old.with_name('abandoned.partial.wav')
        abandoned.write_bytes(b'interrupted')
        os.utime(abandoned, (1000, 1000))
        with patch.object(playback, 'MAX_CACHE_BYTES', old.stat().st_size):
            new = Path(playback.prepare(api, second)['path'])
        self.assertTrue(new.is_file())
        self.assertFalse(old.exists())
        self.assertFalse(abandoned.exists())
        with patch.object(playback, 'MAX_CACHE_BYTES', 0):
            self.assertEqual(playback.prepare(api, second)['path'], str(new))
        self.assertTrue(new.is_file())  # The copy just handed out is never evicted.
        self.assertEqual([Path(track['path']).read_bytes() for track in (first, second)], originals)
        Path(first['path']).unlink()
        with self.assertRaisesRegex(ValueError, 'missing'):
            playback.prepare(api, first)

    def test_playlist_delete_keeps_tracks_and_unlinks_the_set_report(self):
        report = self.saved(1)
        track = self.audio('stay.wav')
        playlist = api.dispatch('playlists.save', {'name': 'Set', 'track_ids': [track['id']]})
        other = api.dispatch('playlists.save', {'name': 'Other', 'track_ids': [track['id']]})
        with api.connect() as db:
            db.execute('UPDATE dj_sets SET playlist_id=? WHERE id=?', (playlist['id'], report['id']))
            set_import.attach_track(db, report['id'], 0, track['id'])
        self.assertEqual(api.dispatch('playlists.delete', {'id': playlist['id']}), {'deleted': True})
        self.assertEqual(api.dispatch('playlists.list', {}), [other])
        self.assertEqual([row['id'] for row in api.dispatch('library.list', {})], [track['id']])
        self.assertTrue(Path(track['path']).is_file())
        saved = api.dispatch('sets.get', {'id': report['id']})
        self.assertIsNone(saved['playlist_id'])
        self.assertEqual(saved['tracks'][0]['track_id'], track['id'])
        with api.connect() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM playlist_tracks WHERE playlist_id=?', (playlist['id'],)).fetchone()[0], 0)
        for missing in (playlist['id'], 'unknown', None):
            with self.subTest(id=missing), self.assertRaisesRegex(ValueError, 'Playlist not found'):
                api.dispatch('playlists.delete', {'id': missing})

    def test_library_trash_moves_the_file_to_the_trash_and_removes_the_track(self):
        keep, gone = self.audio('keep-trash.wav'), self.audio('gone-trash.wav')
        api.dispatch('playlists.save', {'name': 'Set', 'track_ids': [keep['id'], gone['id']]})
        bin_dir = self.root / 'fake-bin'; bin_dir.mkdir(exist_ok=True)
        trash_dir = self.root / 'Trash'; trash_dir.mkdir(exist_ok=True)
        fake = bin_dir / 'trash'
        fake.write_text(f'#!/bin/sh\nmv "$1" "{trash_dir}/"\n'); fake.chmod(0o755)
        with patch.object(api, 'TRASH', str(fake)), patch.object(api.sys, 'platform', 'darwin'):
            self.assertEqual(api.dispatch('library.trash', {'ids': [gone['id']]}), {'removed': 1, 'trashed': 1, 'not_trashed': []})
        self.assertFalse(Path(gone['path']).exists())
        self.assertTrue((trash_dir / Path(gone['path']).name).is_file())
        self.assertTrue(Path(keep['path']).is_file())
        self.assertEqual([row['id'] for row in api.dispatch('library.list', {})], [keep['id']])
        self.assertEqual(api.dispatch('playlists.list', {})[0]['track_ids'], [keep['id']])
        # A file the Trash refuses keeps its track, and nothing is removed.
        fake.write_text('#!/bin/sh\nexit 1\n')
        with patch.object(api, 'TRASH', str(fake)), patch.object(api.sys, 'platform', 'darwin'), self.assertRaisesRegex(ValueError, 'could not be moved to the Trash'):
            api.dispatch('library.trash', {'ids': [keep['id']]})
        self.assertTrue(Path(keep['path']).is_file())
        self.assertEqual(len(api.dispatch('library.list', {})), 1)

    def test_library_remove_drops_records_but_never_the_audio(self):
        report = self.saved(2)
        keep, gone = self.audio('keep.wav'), self.audio('gone.wav')
        original = Path(gone['path']).read_bytes()
        playlist = api.dispatch('playlists.save', {'name': 'Set', 'track_ids': [keep['id'], gone['id']]})
        api.dispatch('cues.save', {'id': gone['id'], 'cues': [{'slot': 0, 'position_us': 500000, 'label': 'Cue'}]})
        api.dispatch('sections.save', {'id': gone['id'], 'sections': [{'id': 'intro', 'start_us': 0, 'end_us': 1000000, 'label': 'Intro'}]})
        api.dispatch('cues.save', {'id': keep['id'], 'cues': [{'slot': 1, 'position_us': 250000, 'label': 'Kept'}]})
        with api.connect() as db:
            db.execute('UPDATE dj_sets SET playlist_id=? WHERE id=?', (playlist['id'], report['id']))
            db.execute("INSERT INTO acquisitions(id,state,stage,review,created_at,updated_at,track_id,path) VALUES ('download','completed','Imported','{}','now','now',?,?)", (gone['id'], gone['path']))
            set_import.attach_track(db, report['id'], 0, gone['id'], cue_pending=True)
            set_import.attach_track(db, report['id'], 1, keep['id'])
            db.execute("UPDATE dj_set_items SET download_id='download' WHERE set_id=? AND position=0", (report['id'],))
            db.execute("INSERT INTO jobs (id,track_id,state,stage,status_path,output_path,cache_path,cache_key,error,kind) VALUES ('done',?,'succeeded','Complete','s','o','c','k',NULL,'beat')", (gone['id'],))
        cached = api.CACHE / 'kept-analysis.json'
        cached.write_text('{}')
        for ids in ([], 'all', [1], None):
            with self.subTest(ids=ids), self.assertRaises(ValueError):
                api.dispatch('library.remove', {'ids': ids})
        self.assertEqual(api.dispatch('library.remove', {'ids': [gone['id'], 'unknown', gone['id']]}), {'removed': 1})
        self.assertEqual(Path(gone['path']).read_bytes(), original)
        self.assertTrue(cached.is_file())
        self.assertEqual([row['id'] for row in api.dispatch('library.list', {})], [keep['id']])
        self.assertEqual(api.dispatch('track.get', {'id': keep['id']})['cues'][0]['label'], 'Kept')
        self.assertEqual(api.dispatch('playlists.list', {})[0]['track_ids'], [keep['id']])
        rows = api.dispatch('sets.get', {'id': report['id']})['tracks']
        self.assertEqual([(row['track_id'], row['download_id']) for row in rows], [(None, None), (keep['id'], None)])
        with api.connect() as db:
            for table in ('cues', 'sections', 'jobs', 'playlist_tracks'):
                self.assertEqual(db.execute(f'SELECT count(*) FROM {table} WHERE track_id=?', (gone['id'],)).fetchone()[0], 0, table)
            self.assertEqual(db.execute('SELECT sum(cue_pending) FROM dj_set_items WHERE set_id=?', (report['id'],)).fetchone()[0], 0)
            self.assertEqual(tuple(db.execute("SELECT state,track_id FROM acquisitions WHERE id='download'").fetchone()), ('completed', None))
        self.assertEqual(api.dispatch('library.remove', {'ids': [gone['id']]}), {'removed': 0})
        again = api.dispatch('library.import', {'path': gone['path']})
        self.assertFalse(again['duplicate'])
        self.assertNotEqual(again['id'], gone['id'])
