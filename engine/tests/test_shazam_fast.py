import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from crateloom import errors, shazam_fast, shazam_scan, shazam_worker

RateLimited = type('RateLimited', (Exception,), {})
ClientPayloadError = type('ClientPayloadError', (Exception,), {})
ClientConnectorError = type('ClientConnectorError', (OSError,), {})


def track(key, offset=None):
    reply = {'track': {'key': key, 'title': 'Song '+key, 'subtitle': 'Artist'}, 'matches': []}
    if offset is not None:
        reply['matches'] = [{'id': key, 'offset': offset, 'timeskew': 0.0001, 'frequencyskew': 0}]
    return reply


class FakeShazam:
    """Answers lookups from a script: at -> list of replies or exceptions, last one repeats."""

    def __init__(self, script, delay=0):
        self.script = script
        self.delay = delay
        self.calls = []
        self.in_flight = 0
        self.most_in_flight = 0

    def modules(self):
        fake = self

        class Client:
            def __init__(self, **options): pass
            async def __aenter__(self): return self
            async def __aexit__(self, *args): pass

        class Recognizer:
            def __init__(self, **options): self.core_recognizer = self
            async def recognize_bytes(self, value, options): return value
            async def send_recognize_request_v2(self, sig): return await fake.reply(sig)

        return {'aiohttp_retry': SimpleNamespace(ExponentialRetry=lambda **options: None),
                'shazamio': SimpleNamespace(Shazam=Recognizer),
                'shazamio.client': SimpleNamespace(HTTPClient=Client)}

    async def reply(self, at):
        self.calls.append((at, time.monotonic()))
        self.in_flight += 1
        self.most_in_flight = max(self.most_in_flight, self.in_flight)
        try:
            await asyncio.sleep(self.delay)
            answers = self.script(at) if callable(self.script) else self.script.get(at, [{'matches': []}])
            if isinstance(answers, list):
                count = sum(1 for call, _ in self.calls if call == at)
                answers = answers[min(count, len(answers))-1]
            if isinstance(answers, BaseException):
                raise answers
            return answers
        finally:
            self.in_flight -= 1


class ShazamFastTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source = self.root/'mix.wav'
        self.source.write_bytes(b'audio')
        self.stages = []

    def tearDown(self):
        self.temp.cleanup()

    def run_scan(self, fake, duration, plan=None, **constants):
        values = dict(REQUEST_INTERVAL=.04, MIN_INTERVAL=.02, LIMIT_PAUSE=.05, BAD_REPLY_PAUSE=(.01, .02), HEARTBEAT=.02)
        values.update(constants)
        record = lambda progress, partial: self.stages.append((progress['stage'], progress.get('resume_at')))
        with patch.dict(sys.modules, fake.modules()), \
             patch.object(shazam_fast, 'decode', lambda source, at: at+1000), \
             patch.object(shazam_fast, 'report_progress', record), \
             patch.multiple(shazam_fast, **values):
            return asyncio.run(shazam_fast.scan(self.source, duration, 0, plan or {}))

    def test_paces_start_to_start_and_adapts_down(self):
        fake = FakeShazam(lambda at: track('A'), delay=.005)
        result = self.run_scan(fake, 300, SPEEDUP_RUN=2, SPEEDUP_STEP=.01)
        self.assertEqual([at-1000 for at, _ in fake.calls], [0, 60, 120, 180, 240, 288])
        self.assertEqual(fake.most_in_flight, 1)
        starts = [stamp for _, stamp in fake.calls]
        # Start-to-start gaps follow the interval, which falls after each clean run to the floor.
        for gap, interval in zip([b-a for a, b in zip(starts, starts[1:])], [.04, .04, .03, .03, .02]):
            self.assertGreaterEqual(gap, interval-.005)
        self.assertEqual(result['pacing']['interval'], .02)
        self.assertEqual(result['pacing']['episodes'], 0)
        self.assertEqual(result['plan']['samples'], {})
        self.assertNotIn('failure', result)

    def test_429_pauses_then_returns_cooldown_on_second(self):
        fake = FakeShazam(lambda at: RateLimited('limited') if at == 1060 else track('A'))
        result = self.run_scan(fake, 300)
        tries = [stamp for at, stamp in fake.calls if at == 1060]
        self.assertEqual(len(tries), 2)
        self.assertGreaterEqual(tries[1]-tries[0], .04)
        self.assertEqual(result['failure']['code'], 'rate_limited')
        self.assertIn('RateLimited', result['failure']['detail'])
        self.assertEqual(result['pacing']['episodes'], 2)
        self.assertEqual(result['cooldown'], shazam_fast.cooldown_for(2))
        self.assertEqual(result['cooldown'], 180)
        self.assertAlmostEqual(result['pacing']['interval'], .16)
        self.assertEqual(list(result['plan']['samples']), [0])
        # The pause keeps reporting progress (the heartbeat) with a resume time.
        waits = [resume for stage, resume in self.stages if stage == 'Waiting for Shazam']
        self.assertGreaterEqual(len(waits), 2)
        self.assertTrue(all(resume and resume > time.time()-5 for resume in waits))
        # Earlier episodes within the hour lengthen the cooldown, capped at 15 minutes.
        result = self.run_scan(FakeShazam(lambda at: RateLimited('limited')), 300,
                               plan={'pacing': {'interval': .1, 'episodes': 5, 'limited_at': time.time()-60}})
        self.assertEqual(result['cooldown'], 900)
        self.assertEqual(result['pacing']['episodes'], 7)
        self.assertAlmostEqual(result['pacing']['interval'], .4)
        # Episodes older than an hour are forgotten, and so is the slowdown they caused.
        old = shazam_fast.pacing_state({'interval': 8, 'episodes': 4, 'limited_at': time.time()-7200})
        self.assertEqual((old['episodes'], old['interval']), (0, shazam_fast.REQUEST_INTERVAL))
        self.assertEqual(shazam_fast.pacing_state({'interval': 8})['interval'], shazam_fast.REQUEST_INTERVAL)
        # A recent episode keeps it; a pace learned from clean runs is kept either way.
        self.assertEqual(shazam_fast.pacing_state({'interval': 8, 'episodes': 1, 'limited_at': time.time()-60})['interval'], 8)
        self.assertEqual(shazam_fast.pacing_state({'interval': 1.5, 'limited_at': time.time()-7200})['interval'], 1.5)

    def test_bad_reply_retried_once_and_classified(self):
        fake = FakeShazam({1060: [ClientPayloadError('Can not decode content-encoding: gzip'), track('A')]})
        result = self.run_scan(fake, 120)
        self.assertEqual([at for at, _ in fake.calls].count(1060), 2)
        self.assertNotIn('failure', result)
        self.assertEqual([hit['key'] for hit in result['hits']], ['A'])
        # A sample that fails again after its one retry is skipped.
        fake = FakeShazam({1060: [ClientPayloadError('payload')]})
        result = self.run_scan(fake, 120)
        self.assertEqual([at for at, _ in fake.calls].count(1060), 2)
        self.assertNotIn('failure', result)
        # Three unreadable replies in a row are throttling: pause once, then stop with a cooldown.
        fake = FakeShazam(lambda at: ClientPayloadError('payload'))
        result = self.run_scan(fake, 300)
        self.assertEqual(result['failure']['code'], 'rate_limited')
        self.assertIn('ClientPayloadError', result['failure']['detail'])
        self.assertEqual(result['cooldown'], 180)
        self.assertIn('Waiting for Shazam', [stage for stage, _ in self.stages])
        # No sample is recorded as a miss during the outage; a later pass asks them again.
        self.assertEqual(result['plan']['samples'], {})
        # With hints and answers in between, only the outage samples stay unanswered.
        fake = FakeShazam(lambda at: track('A') if at == 1000 else ClientPayloadError('payload'))
        result = self.run_scan(fake, 300, plan={'hints': [0, 100, 200]})
        self.assertEqual(result['failure']['code'], 'rate_limited')
        self.assertNotIn(None, result['plan']['samples'].values())
        # A sample unreadable twice while Shazam answers others is a miss of its own.
        fake = FakeShazam(lambda at: ClientPayloadError('payload') if at == 1060 else track('A'))
        result = self.run_scan(fake, 300)
        self.assertNotIn('failure', result)
        self.assertEqual([at for at, _ in fake.calls].count(1060), 2)
        # Connection and certificate failures stop at once and are named.
        CertificateError = type('ClientConnectorCertificateError', (ClientConnectorError,), {})
        for error, code, cooldown in ((ClientConnectorError('refused'), 'unreachable', 15),
                                      (asyncio.TimeoutError(), 'unreachable', 15),
                                      (CertificateError('bad certificate'), 'tls', None)):
            fake = FakeShazam(lambda at, error=error: error)
            result = self.run_scan(fake, 120)
            self.assertEqual(len(fake.calls), 1)
            self.assertEqual(result['failure']['code'], code)
            self.assertEqual(result.get('cooldown'), cooldown)

    def test_json_without_track_or_matches_is_bad_reply(self):
        with self.assertRaises(shazam_fast.BadReply):
            shazam_fast.recording({'status': 500, 'message': 'Internal error'})
        with self.assertRaises(shazam_fast.BadReply):
            shazam_fast.recording('<html>')
        self.assertIsNone(shazam_fast.recording({'matches': [], 'tagid': 'x'}))
        match = shazam_fast.recording(track('123', offset=41.5))
        self.assertEqual(match, {'key': '123', 'title': 'Song 123', 'artist': 'Artist', 'offset': 41.5, 'skew': .0001})
        self.assertNotIn('offset', shazam_fast.recording(track('123')))
        fake = FakeShazam({1060: [{'status': 503}, track('B')]})
        result = self.run_scan(fake, 120)
        self.assertEqual([at for at, _ in fake.calls].count(1060), 2)
        self.assertEqual(sorted(hit['key'] for hit in result['hits']), ['B'])

    def test_offset_skips_bisection(self):
        a, b = {'key': 'A', 'title': 'A', 'artist': 'X'}, {'key': 'B', 'title': 'B', 'artist': 'X'}
        samples = {0: a, 60: b, 120: b, 168: b}
        self.assertEqual(shazam_fast.next_samples(0, 180, samples), [30])
        samples[60] = dict(b, offset=25.0)
        # The start is the earliest B can enter, so one lookup just after it checks B is already playing.
        self.assertEqual(shazam_fast.next_samples(0, 180, samples), [45])
        samples[45] = dict(b, offset=10.0)
        self.assertEqual(shazam_fast.next_samples(0, 180, samples), [])
        hits = shazam_fast.timeline(0, 180, samples)
        self.assertEqual([(hit['key'], hit['start'], hit['end']) for hit in hits], [('A', 0, 35.0), ('B', 35.0, 180)])
        del samples[45]
        samples[60] = dict(b, offset=15.0)
        self.assertEqual(shazam_fast.next_samples(0, 180, samples), [])
        # An estimate outside the interval is ignored and the interval is bisected.
        samples[60] = dict(b, offset=90.0)
        self.assertEqual(shazam_fast.next_samples(0, 180, samples), [30])
        # Two lookups that agree on the start confirm a recording.
        hits = shazam_fast.timeline(0, 100, {0: a, 50: dict(b, offset=10.0), 70: None, 90: dict(b, offset=50.5)})
        self.assertEqual([(hit['key'], hit['start'], hit['end'], hit['samples'], hit['confirmed']) for hit in hits],
                         [('A', 0, 40.0, 1, False), ('B', 40.0, 100, 2, True)])
        fake = FakeShazam({1000: [track('A')], 1060: [track('B', offset=15)], 1120: [track('B')], 1168: [track('B')]})
        self.run_scan(fake, 180)
        self.assertEqual(sorted(at-1000 for at, _ in fake.calls), [0, 60, 120, 168])

    def test_cued_in_track_boundary_is_refined_past_its_recording_start(self):
        # B enters at 600 s from 30 s into its recording, so its recording "starts" at 570 s.
        def mix(at):
            at -= 1000
            return track('A', offset=float(at)) if at < 600 else track('B', offset=float(at-570))
        fake = FakeShazam(mix)
        result = self.run_scan(fake, 720)
        hits = [(hit['key'], hit['start']) for hit in result['hits']]
        self.assertEqual([key for key, _ in hits], ['A', 'B'])
        self.assertLessEqual(abs(hits[1][1]-600), shazam_fast.REFINE_STEP/2)

    def test_hints_sampled_first_and_cover_anchors(self):
        self.assertEqual(shazam_fast.next_samples(0, 300, {}, hints=[45, 150, 400]), [45, 150])
        a, b = {'key': 'A', 'title': 'A', 'artist': 'X'}, {'key': 'B', 'title': 'B', 'artist': 'X'}
        self.assertEqual(shazam_fast.next_samples(0, 300, {45: a, 150: b}, hints=[45, 150]), [0, 240, 288])
        fake = FakeShazam(lambda at: track('A'))
        result = self.run_scan(fake, 300, plan={'hints': [150, 45]})
        self.assertEqual([at-1000 for at, _ in fake.calls][:2], [45, 150])
        self.assertNotIn(60, [at-1000 for at, _ in fake.calls])
        self.assertNotIn('hints', result['plan'])

    def test_decode_failures_skip_samples_but_unreadable_mix_fails(self):
        fake = FakeShazam(lambda at: track('A'))
        def decode(source, at):
            if at == 60:
                raise ValueError('FFmpeg could not decode the mix audio.')
            return at+1000
        with patch.dict(sys.modules, fake.modules()), patch.object(shazam_fast, 'decode', decode), \
             patch.multiple(shazam_fast, REQUEST_INTERVAL=0, MIN_INTERVAL=0):
            asyncio.run(shazam_fast.scan(self.source, 180, 0, {}))
        self.assertNotIn(1060, [at for at, _ in fake.calls])
        def broken(source, at):
            raise ValueError('FFmpeg could not decode the mix audio.')
        with patch.dict(sys.modules, fake.modules()), patch.object(shazam_fast, 'decode', broken):
            with self.assertRaisesRegex(ValueError, 'could not decode'):
                asyncio.run(shazam_fast.scan(self.source, 600, 0, {}))

    def test_batch_seconds_bounds_the_pass(self):
        fake = FakeShazam(lambda at: track('A'))
        with patch.dict(sys.modules, fake.modules()), patch.object(shazam_fast, 'decode', lambda source, at: at+1000), \
             patch.multiple(shazam_fast, REQUEST_INTERVAL=0, MIN_INTERVAL=0):
            whole = asyncio.run(shazam_fast.scan(self.source, 900, 0, {}))
            part = asyncio.run(shazam_fast.scan(self.source, 900, 0, {}, 300))
        self.assertNotIn('scanned_until', whole)
        self.assertEqual(whole['hits'][0]['end'], 900)
        self.assertEqual(part['scanned_until'], 300)

    def test_worker_reports_failures_as_json(self):
        engine = Path(shazam_fast.__file__).resolve().parent.parent
        worker = subprocess.run([sys.executable, '-m', 'crateloom.shazam_worker', str(self.root/'missing.wav'), '30', '0', '10800', 'fast'],
                                input='{}', capture_output=True, text=True, timeout=60,
                                env=dict(os.environ, PYTHONPATH=str(engine), PYTHONDONTWRITEBYTECODE='1'))
        self.assertEqual(worker.returncode, 1)
        failure = json.loads(worker.stdout)['failure']
        self.assertIn(failure['code'], ('decode', 'runtime'))


class WorkerPlumbingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source = self.root/'mix.wav'
        self.source.write_bytes(b'audio')
        def atomic(path, data):
            Path(path).write_bytes(data)
        def stop_worker(proc):
            if proc.poll() is None:
                proc.kill()
                proc.wait()
        self.api = SimpleNamespace(ROOT=self.root, probe=lambda path: (300.0, None, None), atomic=atomic, stop_worker=stop_worker)

    def tearDown(self):
        self.temp.cleanup()

    def worker(self, body):
        executable = self.root/'worker'
        executable.write_text('#!'+sys.executable+'\nimport json,os,sys,time\nfrom pathlib import Path\n'+body)
        executable.chmod(0o755)
        return str(executable)

    def run_worker(self, body, plan=None, **options):
        return shazam_scan.run_worker(self.api, self.worker(body), self.source, 0, None, 10800, plan, **options)

    def test_worker_crash_never_forwards_stderr(self):
        crash = 'sys.stderr.write("Traceback (most recent call last):\\nzlib.error: Error -3 while decompressing data: incorrect header check\\n")\nsys.exit(1)\n'
        with self.assertRaises(ValueError) as caught:
            self.run_worker(crash, plan={})
        self.assertEqual(str(caught.exception), 'Audio recognition stopped unexpectedly. Try again.')
        message, code, detail = errors.public_error(caught.exception)
        self.assertEqual((message, code), ('Audio recognition stopped unexpectedly. Try again.', 'engine_error'))
        self.assertIn('incorrect header check', detail)
        internal = 'print(json.dumps({"failure":{"code":"internal","detail":"KeyError: x"}}))\nsys.stderr.write("KeyError: x\\n")\nsys.exit(1)\n'
        with self.assertRaises(ValueError) as caught:
            self.run_worker(internal, plan={})
        self.assertEqual(str(caught.exception), 'Audio recognition stopped unexpectedly. Try again.')
        self.assertIn('KeyError: x', caught.exception.detail)
        decode = 'print(json.dumps({"failure":{"code":"decode","detail":"ValueError: x","message":"FFmpeg could not decode the mix audio."}}))\nsys.exit(1)\n'
        with self.assertRaisesRegex(ValueError, '^FFmpeg could not decode the mix audio.$'):
            self.run_worker(decode, plan={})
        runtime = 'print(json.dumps({"failure":{"code":"runtime","detail":"ModuleNotFoundError: shazamio"}}))\nsys.exit(1)\n'
        with self.assertRaisesRegex(ValueError, '^Install the ShazamIO scanner'):
            self.run_worker(runtime, plan={})
        with self.assertRaisesRegex(ValueError, '^Audio recognition stopped unexpectedly'):
            self.run_worker('print("not json")\n', plan={})

    def test_rate_limit_failure_gets_engine_sentence_and_pacing_is_persisted(self):
        seen = self.root/'seen.json'
        body = ('Path(%r).write_text(sys.stdin.readline())\n' % str(seen) +
                'print(json.dumps({"hits":[],"scanned_until":0,"plan":{"samples":{"0":None},"serial":False},"cooldown":180,'
                '"pacing":{"interval":4,"episodes":2,"limited_at":123.0},"failure":{"code":"rate_limited","detail":"RateLimited: x"}}))\n')
        result = self.run_worker(body, plan={'samples': {}, 'serial': False, 'hints': [30]})
        self.assertEqual(result['warning'], 'Shazam is limiting requests from this Mac. The scan continues automatically in about 3 minutes.')
        self.assertEqual(result['cooldown'], 180)
        self.assertEqual(result['failure']['code'], 'rate_limited')
        self.assertEqual(json.loads((self.root/'shazam-pacing.json').read_text()), {'interval': 4, 'episodes': 2, 'limited_at': 123.0})
        self.assertEqual(json.loads(seen.read_text()), {'samples': {}, 'serial': False, 'hints': [30], 'pacing': {}})
        self.run_worker(body, plan={})
        self.assertEqual(json.loads(seen.read_text())['pacing'], {'interval': 4, 'episodes': 2, 'limited_at': 123.0})
        for code, sentence in (('unreachable', 'CrateLoom could not reach Shazam. The scan continues when the connection is back.'),
                               ('bad_reply', 'Shazam sent a reply CrateLoom could not read.'),
                               ('tls', 'The secure connection to Shazam failed.')):
            self.assertEqual(shazam_scan.explain({'failure': {'code': code}})['warning'], sentence)
        self.assertIn('about 1 minute.', shazam_scan.explain({'failure': {'code': 'rate_limited'}, 'cooldown': 45})['warning'])

    def test_partials_stream_when_tracks_change_and_watchdog_keeps_them(self):
        body = ('target=Path(os.environ["CRATELOOM_SCAN_PROGRESS"])\n'
                'def write(found,samples):\n'
                '    target.with_suffix(".tmp").write_text(json.dumps({"progress":{"stage":"Identifying tracks","tracks_found":found},'
                '"partial":{"hits":[],"scanned_until":0,"duration":300,"plan":{"samples":samples,"serial":False}}}))\n'
                '    target.with_suffix(".tmp").replace(target)\n'
                'write(1,{"0":{"key":"A","title":"A","artist":"X"}})\ntime.sleep(.4)\n'
                'write(1,{"0":{"key":"A","title":"A","artist":"X"},"60":None})\ntime.sleep(.4)\n'
                'write(2,{"0":{"key":"A","title":"A","artist":"X"},"60":None,"120":{"key":"B","title":"B","artist":"X"}})\ntime.sleep(.4)\n'
                'time.sleep(60)\n')
        partials = []
        with patch.object(shazam_scan, 'WORKER_TIMEOUT', 1.2):
            started = time.monotonic()
            result = self.run_worker(body, plan={}, on_partial=partials.append)
        self.assertLess(time.monotonic()-started, 10)
        # Only changes in the number of tracks stream before the 10 s interval.
        self.assertEqual([len(partial['plan']['samples']) for partial in partials], [1, 3])
        self.assertEqual(len(result['plan']['samples']), 3)
        self.assertIn('timed out before the end of the mix', result['warning'])
        self.assertFalse(shazam_scan.WORKERS)

    def test_worker_failure_saves_answers_newer_than_the_last_streamed_partial(self):
        # Within the 10 s interval, with the same number of tracks, the later samples are not streamed;
        # a worker that then exits with an error must still leave them saved.
        body = ('target=Path(os.environ["CRATELOOM_SCAN_PROGRESS"])\n'
                'def write(samples):\n'
                '    target.with_suffix(".tmp").write_text(json.dumps({"progress":{"stage":"Identifying tracks","tracks_found":1},'
                '"partial":{"hits":[],"scanned_until":0,"duration":300,"plan":{"samples":samples,"serial":False}}}))\n'
                '    target.with_suffix(".tmp").replace(target)\n'
                'write({"0":{"key":"A","title":"A","artist":"X"}})\ntime.sleep(.4)\n'
                'write({"0":{"key":"A","title":"A","artist":"X"},"60":None,"120":None})\n'
                'print(json.dumps({"failure":{"code":"internal","detail":"OSError: x"}}))\nsys.exit(1)\n')
        partials = []
        with self.assertRaisesRegex(ValueError, '^Audio recognition stopped unexpectedly'):
            self.run_worker(body, plan={}, on_partial=partials.append)
        self.assertEqual(len(partials[-1]['plan']['samples']), 3)
        # A reply that cannot be read keeps them as well.
        partials.clear()
        with self.assertRaisesRegex(ValueError, '^Audio recognition stopped unexpectedly'):
            self.run_worker(body.replace('print(json.dumps({"failure":{"code":"internal","detail":"OSError: x"}}))\nsys.exit(1)\n', 'print("not json")\n'),
                            plan={}, on_partial=partials.append)
        self.assertEqual(len(partials[-1]['plan']['samples']), 3)

    def test_scan_accepts_on_partial_and_hints(self):
        captured = {}
        def batch(api, watch, path, **options):
            captured.update(options)
            return {'hits': [], 'duration': 10}
        with patch.object(shazam_scan, 'scan_batch', batch), patch.object(shazam_scan, 'NEXT_SCAN_AT', 0):
            shazam_scan.scan(self.api, None, str(self.source), plan={'samples': {}, 'hints': [5]}, on_partial=print)
        self.assertEqual(captured['plan'], {'samples': {}, 'hints': [5]})
        self.assertIs(captured['on_partial'], print)

    def test_rate_limit_cooldown_is_saved_for_other_reports_and_a_restart(self):
        limited = {'hits': [], 'duration': 10, 'cooldown': 180, 'failure': {'code': 'rate_limited'}}
        with patch.object(shazam_scan, 'scan_batch', lambda *args, **options: dict(limited)), patch.object(shazam_scan, 'NEXT_SCAN_AT', 0):
            shazam_scan.scan(self.api, None, str(self.source), plan={})
        saved = json.loads((self.root/'shazam-pacing.json').read_text())
        self.assertAlmostEqual(saved['limited_until'], time.time()+180, delta=5)
        # A restarted engine (no cooldown in memory) still waits.
        with patch.object(shazam_scan, 'scan_batch', side_effect=AssertionError('Inside the cooldown')), patch.object(shazam_scan, 'NEXT_SCAN_AT', 0):
            with self.assertRaisesRegex(shazam_scan.ScanBusy, 'cooling down'):
                shazam_scan.scan(self.api, None, str(self.source), plan={})
        # The worker's pacing save keeps the cooldown; once it has passed, scans run again.
        shazam_scan.save_pacing(self.api, {'interval': 4, 'episodes': 1, 'limited_at': 1.0})
        self.assertIn('limited_until', json.loads((self.root/'shazam-pacing.json').read_text()))
        (self.root/'shazam-pacing.json').write_text(json.dumps(dict(saved, limited_until=time.time()-1)))
        self.assertEqual(shazam_scan.saved_cooldown(self.api), 0)


if __name__ == '__main__':
    unittest.main()
