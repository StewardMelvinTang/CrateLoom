import json
import math
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
import wave
import array

PYTHON = os.environ.get('CRATELOOM_STRUCTURE_PYTHON')

@unittest.skipUnless(PYTHON and Path(PYTHON).is_file(), 'Set CRATELOOM_STRUCTURE_PYTHON to the optional model environment')
class StructureJobs(unittest.TestCase):
    def test_model_sections_preserve_manual_edits(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); audio=root/'click.wav'; rate=22050
            samples=array.array('h',[0])*(rate*15)
            for beat in range(1,30):
                start=beat*rate//2
                for i in range(441): samples[start+i]=round(20000*math.sin(2*math.pi*1000*i/rate)*math.exp(-i/100))
            with wave.open(str(audio),'wb') as f:
                f.setnchannels(1); f.setsampwidth(2); f.setframerate(rate); f.writeframes(samples.tobytes())
            env={**os.environ,'CRATELOOM_DATA':str(root/'data'),'PYTHONPATH':str(Path(__file__).resolve().parents[1])}
            proc=subprocess.Popen([sys.executable,'-m','crateloom'],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,env=env)
            def call(method,params):
                proc.stdin.write(json.dumps({'protocol_version':1,'request_id':method,'method':method,'params':params})+'\n'); proc.stdin.flush()
                response=json.loads(proc.stdout.readline()); self.assertNotIn('error',response); return response['result']
            try:
                track=call('library.import',{'path':str(audio)})
                cue={'slot':0,'position_us':500000,'label':'Manual'}
                section={'id':'manual','start_us':0,'end_us':2000000,'label':'Intro'}
                call('cues.save',{'id':track['id'],'cues':[cue]})
                call('sections.save',{'id':track['id'],'sections':[section]})
                job=call('structure.enqueue',{'id':track['id']}); self.assertEqual(job['kind'],'structure')
                cue={'slot':0,'position_us':750000,'label':'Edited while analyzing'}
                call('cues.save',{'id':track['id'],'cues':[cue]})
                deadline=time.monotonic()+180
                while time.monotonic()<deadline:
                    job=call('jobs.get',{'id':job['id']})
                    if job['state'] not in ('running','queued'): break
                    time.sleep(.25)
                self.assertEqual(job['state'],'succeeded',job)
                result=call('track.get',{'id':track['id']})
                self.assertEqual(result['cues'],[cue]); self.assertIn(section,result['sections'])
                self.assertEqual(result['structure_analysis']['weight_license'],'CC-BY-NC-SA-4.0')
                self.assertTrue(result['structure_analysis']['suggestions'])
                automatic=[x for x in result['sections'] if x.get('origin')=='model']
                self.assertTrue(automatic); self.assertTrue(all(x['start_us']>=section['end_us'] for x in automatic))
                edited=dict(automatic[0], label='Verse', origin='manual')
                updated=call('sections.save',{'id':track['id'],'sections':[section,edited]})
                self.assertEqual(updated['sections'][1]['label'],'Verse')
                cached=call('structure.enqueue',{'id':track['id']})
                self.assertEqual(cached['stage'],'Using cached analysis')
                again=call('track.get',{'id':track['id']}); self.assertIn(edited['id'],[x['id'] for x in again['sections']]); self.assertEqual(again['sections'][1]['label'],'Verse')
            finally:
                proc.stdin.close(); proc.wait(timeout=10); proc.stdout.close(); proc.stderr.close()
