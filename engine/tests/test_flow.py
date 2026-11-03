import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET

class Flow(unittest.TestCase):
    def test_protocol_import_cues_restart_export(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); audio=root/'音 & sample.wav'
            subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','sine=frequency=440:duration=2','-y',str(audio)],check=True)
            env={**os.environ,'CRATELOOM_DATA':str(root/'data'),'PYTHONPATH':str(Path(__file__).resolve().parents[1])}
            def request(method,params):
                req={'protocol_version':1,'request_id':'test','method':method,'params':params}
                process=subprocess.run([sys.executable,'-m','crateloom'],input=json.dumps(req)+'\n',text=True,capture_output=True,env=env,check=True)
                response=json.loads(process.stdout); self.assertNotIn('error',response); return response['result']
            self.assertEqual(request('handshake',{})['version'],'0.1.0')
            track=request('library.import',{'path':str(audio)})
            self.assertEqual(request('library.import',{'path':str(audio)})['id'],track['id'])
            analyzed=request('analysis.run',{'id':track['id']}); self.assertGreater(len(analyzed['analysis']['peaks']),0)
            cues=[{'slot':0,'position_us':1234567,'label':'A & 中文'}]
            request('cues.save',{'id':track['id'],'cues':cues})
            self.assertEqual(request('track.get',{'id':track['id']})['cues'],cues)
            xml=root/'playlist.xml'; request('export.rekordbox',{'id':track['id'],'path':str(xml)})
            parsed=ET.parse(xml); mark=parsed.find('.//POSITION_MARK'); self.assertEqual(mark.attrib['Num'],'0'); self.assertEqual(mark.attrib['Name'],'A & 中文'); self.assertAlmostEqual(float(mark.attrib['Start']),1.234567,places=6)
            self.assertIn('%E9%9F%B3%20%26%20sample.wav',parsed.find('.//COLLECTION/TRACK').attrib['Location'])
            self.assertEqual(json.loads(xml.with_suffix('.crateloom.json').read_text())['track']['cues'],cues)
            self.assertTrue(audio.exists())

    def test_invalid_cues_do_not_replace_saved_cues(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            audio = root / 'test.wav'
            subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i',
                            'sine=frequency=440:duration=2', '-y', str(audio)], check=True)
            env = {**os.environ, 'CRATELOOM_DATA': str(root / 'data'),
                   'PYTHONPATH': str(Path(__file__).resolve().parents[1])}
            p = subprocess.Popen([sys.executable, '-m', 'crateloom'], stdin=subprocess.PIPE,
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env)
            try:
                def call(method, params):
                    p.stdin.write(json.dumps({'protocol_version': 1, 'request_id': 'x',
                                              'method': method, 'params': params}) + '\n')
                    p.stdin.flush()
                    return json.loads(p.stdout.readline())
                p.stdin.write('invalid json\n'); p.stdin.flush()
                self.assertIn('error', json.loads(p.stdout.readline()))
                track = call('library.import', {'path': str(audio)})['result']
                valid = [{'slot': 0, 'position_us': 250000, 'label': 'Start'}]
                self.assertEqual(call('cues.save', {'id': track['id'], 'cues': valid})['result']['cues'], valid)
                for invalid in ([{'slot': 0, 'position_us': -1, 'label': 'bad'}],
                                [{'slot': 0, 'position_us': 5000000, 'label': 'bad'}],
                                valid + valid):
                    self.assertIn('error', call('cues.save', {'id': track['id'], 'cues': invalid}))
                    self.assertEqual(call('track.get', {'id': track['id']})['result']['cues'], valid)
            finally:
                p.stdin.close()
                p.wait(timeout=10)
                p.stdout.close()
                p.stderr.close()
