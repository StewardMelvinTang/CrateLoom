import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import zipfile

from crateloom import scan_audio, youtube_audio

WATCH = 'https://www.youtube.com/watch?v=dQw4w9WgXcQ'


def archive(path, entries):
    """entries: name -> (bytes, mode)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, 'w') as bundle:
        for name, (data, mode) in entries.items():
            info = zipfile.ZipInfo(name)
            info.create_system = 3
            info.external_attr = mode << 16
            bundle.writestr(info, data)
    return hashlib.sha256(path.read_bytes()).hexdigest()


HELPER = b'#!/bin/sh\necho 2026.10.26\n'
GOOD = {'yt-dlp_macos': (HELPER, 0o100755), '_internal/': (b'', 0o40755), '_internal/Python': (b'lib', 0o100755),
        '_internal/base_library.zip': (b'data', 0o100644)}


class OnedirRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.home = Path(self.temp.name)
        environment = {key: value for key, value in os.environ.items() if key != 'CRATELOOM_YT_DLP'}
        environment['HOME'] = str(self.home)
        self.stack = [patch.dict(os.environ, environment, clear=True),
                      patch.dict(youtube_audio.ONEDIR, {'path': None, 'failed': None}),
                      patch.object(sys, 'platform', 'darwin')]
        for item in self.stack:
            item.start()
        self.tools = self.home/'Library/Caches/CrateLoom/tools'
        self.onefile = self.tools/'2026.10.26'/'yt-dlp'
        self.onefile.parent.mkdir(parents=True)
        self.onefile.write_bytes(b'#!/bin/sh\necho onefile\n')
        self.onefile.chmod(0o755)

    def tearDown(self):
        for item in reversed(self.stack):
            item.stop()
        self.temp.cleanup()

    def pinned(self, entries=GOOD, where=None):
        digest = archive(where or self.tools/'2026.10.26'/'yt-dlp_macos.zip', entries)
        return patch.object(youtube_audio, 'ONEDIR_SHA256', digest)

    def extracted(self):
        """Unpacked folders of any pin of this release."""
        return sorted(path.name for path in self.tools.iterdir() if path.name.startswith('yt-dlp-2026.10.26-') and path.name.endswith('-dir'))

    def extracting(self):
        return sorted(path.name for path in self.tools.iterdir() if '-extract-' in path.name)

    def first_use(self):
        """The first call answers with the onefile helper at once; the unpacked build is prepared behind it."""
        first = youtube_audio.executable()
        thread = youtube_audio.ONEDIR['thread']
        if thread is not None:
            thread.join(30)
            self.assertFalse(thread.is_alive())
        return first, youtube_audio.executable()

    def test_onedir_preferred_after_verified_extraction(self):
        with self.pinned():
            first, binary = self.first_use()
            target = youtube_audio.onedir_target()
            self.assertTrue(target.name.startswith('yt-dlp-2026.10.26-') and target.name.endswith('-dir'))
            self.assertEqual(first, str(self.onefile))
            self.assertEqual(binary, str(target/'yt-dlp_macos'))
            self.assertEqual(subprocess.run([binary, '--version'], capture_output=True, text=True).stdout.strip(), '2026.10.26')
            self.assertTrue(os.access(target/'_internal'/'Python', os.X_OK))
            self.assertFalse(os.access(target/'_internal'/'base_library.zip', os.X_OK))
            # Later calls and later processes reuse the folder without hashing the archive again.
            with patch.object(youtube_audio, 'file_sha256', side_effect=AssertionError('hashed again')):
                self.assertEqual(youtube_audio.executable(), binary)
                youtube_audio.ONEDIR.update(path=None)
                self.assertEqual(youtube_audio.executable(), binary)
            self.assertEqual(self.extracting(), [])

    def test_checksum_mismatch_falls_back_to_onefile(self):
        archive(self.tools/'2026.10.26'/'yt-dlp_macos.zip', GOOD)
        self.assertEqual(self.first_use(), (str(self.onefile), str(self.onefile)))
        self.assertEqual(self.extracted(), [])
        self.assertIn('checksum mismatch', (self.tools/'yt-dlp-onedir.log').read_text())
        # The failure is remembered for this process instead of hashing on every call.
        with patch.object(youtube_audio, 'file_sha256', side_effect=AssertionError('hashed again')):
            self.assertEqual(youtube_audio.executable(), str(self.onefile))

    def test_unusable_archives_fall_back_silently(self):
        cases = {
            'unsafe path': dict(GOOD, **{'../escaped': (b'x', 0o100644)}),
            'no executable': {'_internal/Python': (b'lib', 0o100755)},
            'wrong version': dict(GOOD, **{'yt-dlp_macos': (b'#!/bin/sh\necho 2001.01.01\n', 0o100755)}),
            'link outside': dict(GOOD, **{'_internal/link': (b'../../../outside', 0o120777)}),
        }
        for reason, entries in cases.items():
            with self.subTest(reason), self.pinned(entries):
                youtube_audio.ONEDIR.update(path=None, failed=None)
                self.assertEqual(self.first_use(), (str(self.onefile), str(self.onefile)))
                self.assertEqual(self.extracted(), [])
                self.assertFalse((self.tools/'escaped').exists())
                self.assertEqual(self.extracting(), [])
        # No archive at all is not worth a diagnostic beyond the note.
        (self.tools/'2026.10.26'/'yt-dlp_macos.zip').unlink()
        youtube_audio.ONEDIR.update(path=None, failed=None)
        self.assertEqual(self.first_use(), (str(self.onefile), str(self.onefile)))

    def test_explicit_helper_still_wins(self):
        with self.pinned(), patch.dict(os.environ, {'CRATELOOM_YT_DLP': str(self.onefile)}):
            self.assertEqual(youtube_audio.executable(), str(self.onefile))
        self.assertEqual(self.extracted(), [])

    def test_bundled_resource_is_used_by_the_frozen_engine(self):
        app = self.home/'CrateLoom.app'/'Contents'
        engine = app/'MacOS'/'crateloom-engine'
        engine.parent.mkdir(parents=True)
        engine.write_bytes(b'engine')
        with self.pinned(where=app/'Resources'/'yt-dlp_macos.zip'), \
             patch.object(sys, 'frozen', True, create=True), patch.object(sys, 'executable', str(engine)):
            self.assertEqual(self.first_use()[1], str(youtube_audio.onedir_target()/'yt-dlp_macos'))

    def test_damaged_bundled_archive_falls_through_to_the_cached_one(self):
        app = self.home/'CrateLoom.app'/'Contents'
        engine = app/'MacOS'/'crateloom-engine'
        engine.parent.mkdir(parents=True)
        engine.write_bytes(b'engine')
        (app/'Resources').mkdir()
        (app/'Resources'/'yt-dlp_macos.zip').write_bytes(b'truncated')
        with self.pinned(), patch.object(sys, 'frozen', True, create=True), patch.object(sys, 'executable', str(engine)):
            self.assertEqual(self.first_use()[1], str(youtube_audio.onedir_target()/'yt-dlp_macos'))

    def test_first_use_never_waits_for_the_extraction(self):
        release = threading.Event()
        def slow():
            release.wait(10)
            raise ValueError('stopped')
        with patch.object(youtube_audio, 'install_onedir', side_effect=slow):
            started = time.monotonic()
            self.assertEqual(youtube_audio.executable(), str(self.onefile))
            # A second caller (another thread, or the capabilities request) does not wait either.
            self.assertEqual(youtube_audio.executable(), str(self.onefile))
            self.assertLess(time.monotonic()-started, .5)
            release.set()
            youtube_audio.ONEDIR['thread'].join(10)
        self.assertIn('stopped', (self.tools/'yt-dlp-onedir.log').read_text())

    def test_interrupted_extractions_are_removed(self):
        with self.pinned():
            self.first_use()
            # An engine killed part-way leaves folders that are only minutes old.
            for name in (youtube_audio.leftover_prefix('extract')+'abc123', youtube_audio.leftover_prefix('stale')+'42'):
                (self.tools/name/'_internal').mkdir(parents=True)
                (self.tools/name/'_internal'/'Python').write_bytes(b'lib')
            youtube_audio.ONEDIR.update(path=None)
            self.assertEqual(self.first_use()[0], str(youtube_audio.onedir_target()/'yt-dlp_macos'))
            self.assertEqual(youtube_audio.leftovers(), [])
            (self.tools/(youtube_audio.leftover_prefix('extract')+'def456')).mkdir()
            youtube_audio.ONEDIR.update(path=None)
            youtube_audio.install_onedir()
            self.assertEqual(youtube_audio.leftovers(), [])

    def test_concurrent_extraction_is_safe(self):
        results = []
        start = threading.Barrier(3)
        def install():
            start.wait()
            results.append(youtube_audio.install_onedir())
        with self.pinned():
            threads = [threading.Thread(target=install) for _ in range(3)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(30)
            # A second process extracting the same release meets the finished folder.
            engine = Path(youtube_audio.__file__).resolve().parent.parent
            other = subprocess.run([sys.executable, '-c', 'from crateloom import youtube_audio as y; y.ONEDIR_SHA256=%r; print(y.install_onedir())' % youtube_audio.ONEDIR_SHA256],
                                   capture_output=True, text=True, timeout=60,
                                   env=dict(os.environ, PYTHONPATH=str(engine), PYTHONDONTWRITEBYTECODE='1'))
            target = youtube_audio.onedir_target()
        expected = str(target/'yt-dlp_macos')
        self.assertEqual(results, [expected]*3)
        self.assertEqual(other.stdout.strip(), expected, other.stderr)
        self.assertEqual(sorted(path.name for path in self.tools.iterdir() if not path.name.startswith('.yt-dlp-2026')),
                         ['2026.10.26', target.name])

    def other_pin(self):
        """A ready folder and an extraction in progress of another build of the same release."""
        with patch.object(youtube_audio, 'ONEDIR_SHA256', 'a'*64):
            folder = youtube_audio.onedir_target()
            extracting = self.tools/(youtube_audio.leftover_prefix('extract')+'busy')
        (folder/'_internal').mkdir(parents=True)
        (folder/'_internal'/'Python').write_bytes(b'lib')
        (folder/'yt-dlp_macos').write_bytes(HELPER)
        (folder/'yt-dlp_macos').chmod(0o755)
        (folder/'.crateloom-verified').write_text('a'*64)
        (extracting/'_internal').mkdir(parents=True)
        return folder, extracting

    def test_other_pins_folders_and_extractions_are_never_removed(self):
        # An engine pinned to another archive hash may be running yt-dlp from its folder or extracting.
        folder, extracting = self.other_pin()
        with self.pinned():
            self.assertEqual(self.first_use()[1], str(youtube_audio.onedir_target()/'yt-dlp_macos'))
            self.assertNotEqual(youtube_audio.onedir_target(), folder)
            youtube_audio.install_onedir()
            # The ready-and-leftovers path of this pin does not touch them either.
            (self.tools/(youtube_audio.leftover_prefix('stale')+'1')).mkdir()
            youtube_audio.ONEDIR.update(path=None)
            self.first_use()
        self.assertTrue((folder/'yt-dlp_macos').is_file())
        self.assertEqual((folder/'.crateloom-verified').read_text(), 'a'*64)
        self.assertTrue(extracting.is_dir())
        with patch.object(youtube_audio, 'ONEDIR_SHA256', 'a'*64):
            self.assertEqual(youtube_audio.onedir_ready(folder), str(folder/'yt-dlp_macos'))

    def test_folder_without_its_python_library_is_not_used(self):
        with self.pinned():
            binary = self.first_use()[1]
            target = youtube_audio.onedir_target()
            (target/'_internal'/'Python').unlink()
            self.assertIsNone(youtube_audio.onedir_ready(target))
            youtube_audio.ONEDIR.update(path=None, failed='stop re-extracting in this test')
            self.assertEqual(youtube_audio.executable(), str(self.onefile))
            self.assertNotEqual(binary, str(self.onefile))

    def test_folder_that_stops_starting_falls_back_once_to_onefile(self):
        calls = []
        def process(args, timeout, process_group=False):
            calls.append(args[0])
            if args[0] != str(self.onefile):
                raise subprocess.CalledProcessError(255, args, '', "[PYI-4242:ERROR] Failed to load Python shared library '/x/_internal/Python'")
            return SimpleNamespace(stdout='ok')
        api = SimpleNamespace(import_process=process)
        with self.pinned():
            binary = self.first_use()[1]
            target = youtube_audio.onedir_target()
            self.assertEqual(youtube_audio.run_helper(api, binary, ['--version'], 5).stdout, 'ok')
            self.assertEqual(calls, [binary, str(self.onefile)])
            # Later calls in this process use the onefile helper; the next start extracts the folder again.
            self.assertEqual(youtube_audio.executable(), str(self.onefile))
            self.assertFalse((target/'.crateloom-verified').exists())
            self.assertIn('failed to start', (self.tools/'yt-dlp-onedir.log').read_text())
            youtube_audio.ONEDIR.update(path=None, failed=None)
            self.assertEqual(self.first_use()[1], binary)

    def test_ytdlp_own_errors_do_not_drop_the_folder(self):
        def process(args, timeout, process_group=False):
            raise subprocess.CalledProcessError(1, args, '', 'ERROR: [youtube] dQw4w9WgXcQ: Video unavailable')
        api = SimpleNamespace(import_process=process)
        with self.pinned():
            binary = self.first_use()[1]
            with self.assertRaises(subprocess.CalledProcessError):
                youtube_audio.run_helper(api, binary, ['--', WATCH], 5)
            self.assertEqual(youtube_audio.executable(), binary)


class SearchBoundsTests(unittest.TestCase):
    def setUp(self):
        self.cache = patch.dict(youtube_audio.SEARCH_CACHE, clear=True)
        self.cache.start()

    def tearDown(self):
        self.cache.stop()

    def test_search_slots_bound_every_search_and_results_are_cached(self):
        active = []
        most = [0]
        lock = threading.Lock()
        def process(args, timeout, process_group=False):
            with lock:
                active.append(1)
                most[0] = max(most[0], len(active))
            time.sleep(.05)
            with lock:
                active.pop()
            line = json.dumps({'id': 'dQw4w9WgXcQ', 'title': args[-1], 'channel': 'C', 'duration': 200})
            return SimpleNamespace(stdout=line)
        api = SimpleNamespace(import_process=process, check_import_cancel=lambda: None)
        with patch.object(youtube_audio, 'executable', return_value='/bin/yt-dlp'):
            threads = [threading.Thread(target=youtube_audio.search, args=(api, f'query {i}')) for i in range(10)]
            threads += [threading.Thread(target=youtube_audio.search_entries, args=(api, f'scsearch6:q {i}', 'id,title,duration', 'SoundCloud')) for i in range(4)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(10)
            self.assertEqual(most[0], 4)
            with patch.object(youtube_audio, 'search_entries', side_effect=AssertionError('searched again')):
                first = youtube_audio.search(api, 'query 3')
                first[0]['title'] = 'changed by caller'
                self.assertEqual(youtube_audio.search(api, 'query 3')[0]['title'], 'ytsearch15:query 3')
            with patch.object(youtube_audio, 'SEARCH_TTL', -1), patch.object(youtube_audio, 'search_entries', return_value=[]) as fresh:
                youtube_audio.search(api, 'query 4 fresh')
                youtube_audio.search(api, 'query 4 fresh')
                self.assertEqual(fresh.call_count, 2)

    def test_waiting_for_a_slot_stays_cancellable(self):
        class Cancelled(Exception):
            pass
        def cancel():
            raise Cancelled()
        api = SimpleNamespace(import_process=None, check_import_cancel=cancel)
        taken = [youtube_audio.SEARCH_SLOTS.acquire() for _ in range(4)]
        try:
            with patch.object(youtube_audio, 'executable', return_value='/bin/yt-dlp'), self.assertRaises(Cancelled):
                youtube_audio.search_entries(api, 'ytsearch15:x', 'id', 'YouTube')
        finally:
            for _ in taken:
                youtube_audio.SEARCH_SLOTS.release()


class MixAcquisitionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        def atomic(path, data):
            Path(path).parent.mkdir(parents=True, exist_ok=True)
            Path(path).write_bytes(data)
        def file_hash(path):
            return hashlib.sha256(Path(path).read_bytes()).hexdigest()
        self.api = SimpleNamespace(ROOT=root, atomic=atomic, file_hash=file_hash)

    def tearDown(self):
        self.temp.cleanup()

    def test_second_obtain_waits_then_reuses_the_saved_audio(self):
        entered = threading.Event()
        release = threading.Event()
        calls = []
        def transfer(api, reference, watch, vid, check):
            calls.append(vid)
            self.assertEqual(scan_audio.ACQUIRING, 1)
            entered.set()
            release.wait(5)
            folder = api.ROOT/'set-audio'/vid
            folder.mkdir(parents=True, exist_ok=True)
            source = folder/'mix.m4a'
            source.write_bytes(b'mix audio')
            api.atomic(folder/'source.json', json.dumps({'watch': WATCH, 'filename': 'mix.m4a', 'size': source.stat().st_size,
                                                         'hash': api.file_hash(source), 'duration': 60}).encode())
            return source
        results = []
        with patch.object(scan_audio, 'transfer_mix', transfer):
            threads = [threading.Thread(target=lambda: results.append(scan_audio.obtain(self.api, WATCH, lambda: None))) for _ in range(2)]
            threads[0].start()
            self.assertTrue(entered.wait(5))
            threads[1].start()
            self.assertFalse(scan_audio.wait_until_idle(.1))
            release.set()
            for thread in threads:
                thread.join(5)
        self.assertEqual(calls, ['dQw4w9WgXcQ'])
        self.assertEqual(len(results), 2)
        self.assertEqual(results[0], results[1])
        self.assertEqual(scan_audio.ACQUIRING, 0)
        self.assertTrue(scan_audio.wait_until_idle(0))

    def test_waiting_obtain_is_cancellable_and_counter_recovers_from_errors(self):
        lock = scan_audio.watch_lock((str(self.api.ROOT), 'dQw4w9WgXcQ'))
        class Cancelled(Exception):
            pass
        def check():
            raise Cancelled()
        with lock, self.assertRaises(Cancelled):
            scan_audio.obtain(self.api, WATCH, check)
        with patch.object(scan_audio, 'transfer_mix', side_effect=ValueError('The public audio connections did not complete.')):
            with self.assertRaises(ValueError):
                scan_audio.obtain(self.api, WATCH, lambda: None)
        self.assertEqual(scan_audio.ACQUIRING, 0)


if __name__ == '__main__':
    unittest.main()
