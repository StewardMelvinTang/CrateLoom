import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from crateloom import __main__ as api
from crateloom.analysis_worker import KEY_MIN_STRENGTH, MAJOR_PROFILE, MINOR_PROFILE, camelot, key_from_chroma

ANALYZER = os.environ.get('CRATELOOM_ANALYSIS_PYTHON')
# The published Camelot wheel.
WHEEL = {('B', False): '1B', ('F♯', False): '2B', ('D♭', False): '3B', ('A♭', False): '4B', ('E♭', False): '5B', ('B♭', False): '6B',
         ('F', False): '7B', ('C', False): '8B', ('G', False): '9B', ('D', False): '10B', ('A', False): '11B', ('E', False): '12B',
         ('G♯', True): '1A', ('E♭', True): '2A', ('B♭', True): '3A', ('F', True): '4A', ('C', True): '5A', ('G', True): '6A',
         ('D', True): '7A', ('A', True): '8A', ('E', True): '9A', ('B', True): '10A', ('F♯', True): '11A', ('C♯', True): '12A'}


def rotated(profile, tonic):
    return [profile[(pitch - tonic) % 12] for pitch in range(12)]


class KeyFromChroma(unittest.TestCase):
    def test_every_key_maps_to_its_camelot_code_and_name(self):
        seen = set()
        for tonic in range(12):
            for minor, profile in ((False, MAJOR_PROFILE), (True, MINOR_PROFILE)):
                key = key_from_chroma(rotated(profile, tonic))
                note, mode = key['name'].split(' ')
                self.assertEqual(mode, 'minor' if minor else 'major')
                self.assertEqual(key['camelot'], WHEEL[(note, minor)], key)
                self.assertEqual(key['camelot'], camelot(tonic, minor))
                self.assertAlmostEqual(key['strength'], 1.0, places=3)
                seen.add(key['camelot'])
        self.assertEqual(len(seen), 24)

    def test_short_names_are_rekordbox_style(self):
        self.assertEqual(key_from_chroma(rotated(MINOR_PROFILE, 9))['short'], 'Am')
        self.assertEqual(key_from_chroma(rotated(MINOR_PROFILE, 1))['short'], 'C#m')
        self.assertEqual(key_from_chroma(rotated(MAJOR_PROFILE, 10))['short'], 'Bb')

    def test_a_minor_triad_progression(self):
        # Am, Dm, E (harmonic minor), Am: A, C, D, E, F, G♯ weighted by use.
        chroma = [0] * 12
        for chord in ((9, 0, 4), (2, 5, 9), (4, 8, 11), (9, 0, 4)):
            for pitch in chord:
                chroma[pitch] += 1
        self.assertEqual(key_from_chroma(chroma)['camelot'], '8A')

    def test_no_tonal_centre_gives_no_key(self):
        self.assertIsNone(key_from_chroma([1] * 12))
        self.assertIsNone(key_from_chroma([1.04, .97, 1.02, .99, 1.03, .96, 1.01, 1.05, .98, 1.0, .97, 1.02]))  # noise-flat
        self.assertIsNone(key_from_chroma([0] * 12))
        self.assertIsNone(key_from_chroma([1] * 11))
        self.assertIsNone(key_from_chroma([float('nan')] + [1] * 11))
        self.assertIsNone(key_from_chroma([-1] + [1] * 11))
        # A whole-tone cluster correlates weakly with every key.
        cluster = [1 if pitch % 2 == 0 else 0 for pitch in range(12)]
        key = key_from_chroma(cluster)
        self.assertTrue(key is None or key['strength'] >= KEY_MIN_STRENGTH)


class StoredKey(unittest.TestCase):
    def test_analysis_validation_accepts_a_key_and_rejects_bad_ones(self):
        good = {'camelot': '8A', 'name': 'A minor', 'short': 'Am', 'strength': .7, 'margin': .1}
        def check(key):
            data = {'version': api.ANALYSIS_VERSION, 'duration': 1.0, 'tempo': None, 'beats': [], 'grid': None, 'key': key, 'waveform': None, 'warnings': []}
            try:
                api.validate_analysis(data, 1.0)
            except ValueError as error:
                return str(error)
        self.assertEqual(check(good), 'Invalid waveform')  # the key passed; the dummy waveform did not
        self.assertEqual(check(None), 'Invalid waveform')
        for bad in ({**good, 'camelot': '13A'}, {**good, 'camelot': '8C'}, {**good, 'name': ''}, {**good, 'strength': float('nan')},
                    {**good, 'strength': True}, {k: v for k, v in good.items() if k != 'short'}, 'Am'):
            self.assertEqual(check(bad), 'Invalid key', bad)

    def test_export_writes_the_key_as_tonality(self):
        from crateloom import rekordbox_export
        track = {'title': 'T', 'artist': 'A', 'duration': 60, 'cues': [], 'metadata': None, 'grid_bpm': None,
                 'detail_analysis': {'key': {'camelot': '8A', 'name': 'A minor', 'short': 'Am', 'strength': .7}}}
        with patch.object(rekordbox_export, 'reviewed_grid', return_value=None):
            xml = rekordbox_export.document([track], [Path('/music/t.m4a')], 'Set', '1')
        self.assertIn(b'Tonality="Am"', xml)
        track['detail_analysis'] = None
        with patch.object(rekordbox_export, 'reviewed_grid', return_value=None):
            self.assertNotIn(b'Tonality', rekordbox_export.document([track], [Path('/music/t.m4a')], 'Set', '1'))


class RevealInFinder(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.root = Path(self.tmp.name).resolve()
        self.patches = [patch.object(api, name, value) for name, value in [('ROOT', self.root), ('DB', self.root / 'library.sqlite3'), ('CACHE', self.root / 'analysis-cache')]]
        for item in self.patches: item.start()
        self.wav = self.root / 'song.wav'
        subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', 'sine=duration=1', '-y', str(self.wav)], check=True)
        self.track = api.dispatch('library.import', {'path': str(self.wav)})
    def tearDown(self):
        for item in self.patches: item.stop()
        self.tmp.cleanup()

    def test_reveals_only_the_tracks_own_file(self):
        calls = []
        real = subprocess.run
        def run(args, *a, **k):
            if args and args[0] == 'open':
                calls.append(args); return subprocess.CompletedProcess(args, 0, b'', b'')
            return real(args, *a, **k)
        with patch.object(api.subprocess, 'run', side_effect=run), patch.object(api.sys, 'platform', 'darwin'):
            self.assertEqual(api.dispatch('library.reveal', {'id': self.track['id']}), {'revealed': True})
            for bad in ({'id': 'nope'}, {'id': None}, {}, {'path': '/etc/hosts'}):
                with self.assertRaises(ValueError): api.dispatch('library.reveal', bad)
            self.wav.unlink()
            with self.assertRaisesRegex(ValueError, 'missing'): api.dispatch('library.reveal', {'id': self.track['id']})
        self.assertEqual(calls, [['open', '-R', '--', str(self.wav)]])


@unittest.skipUnless(ANALYZER and Path(ANALYZER).is_file(), 'Set CRATELOOM_ANALYSIS_PYTHON to the pinned environment')
class EstimateKey(unittest.TestCase):
    def detect(self, body):
        script = '''
import json
import numpy as np, librosa
from crateloom.analysis_worker import estimate_key
rate = 22050
def chord(freqs, seconds):
    t = np.arange(int(rate * seconds)) / rate
    tone = sum(sum(np.sin(2 * np.pi * f * h * t) / h for h in (1, 2, 3)) for f in freqs)
    return tone * np.minimum(1, np.minimum(t, seconds - t) * 20)
note = lambda midi: 440 * 2 ** ((midi - 69) / 12)
''' + body
        env = {**os.environ, 'PYTHONPATH': str(Path(__file__).resolve().parents[1])}
        return json.loads(subprocess.run([ANALYZER, '-c', script], check=True, capture_output=True, text=True, env=env).stdout)

    def test_synthetic_progressions(self):
        result = self.detect('''
rng = np.random.default_rng(3)
kick = np.zeros(rate // 2); kick[:800] = np.sin(2 * np.pi * 55 * np.arange(800) / rate) * np.exp(-np.arange(800) / 150)
def song(chords):
    y = np.concatenate([chord([note(m) for m in c], 2.0) for c in chords * 6])
    drums = np.tile(kick, len(y) // len(kick) + 1)[:len(y)] + rng.normal(0, .02, len(y))
    return (y / np.abs(y).max() + drums).astype(np.float32)
a_minor = [(57, 60, 64), (50, 53, 57), (52, 56, 59), (57, 60, 64)]          # Am Dm E Am
g_major = [(55, 59, 62), (48, 52, 55), (50, 54, 57), (55, 59, 62)]          # G C D G
out = {name: estimate_key(np, librosa, song(chords), rate)['camelot'] for name, chords in (('a_minor', a_minor), ('g_major', g_major))}
noise = rng.normal(0, .1, rate * 10).astype(np.float32)
out['noise'] = estimate_key(np, librosa, noise, rate)
print(json.dumps(out))
''')
        self.assertEqual(result['a_minor'], '8A')
        self.assertEqual(result['g_major'], '9B')
        self.assertIsNone(result['noise'])


if __name__ == '__main__':
    unittest.main()
