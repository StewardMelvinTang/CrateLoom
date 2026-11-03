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

ANALYZER = os.environ.get('CRATELOOM_ANALYSIS_PYTHON')

@unittest.skipUnless(ANALYZER and Path(ANALYZER).is_file(), 'Set CRATELOOM_ANALYSIS_PYTHON to the pinned environment')
class AnalysisJobs(unittest.TestCase):
    def test_cancel_success_cache_and_preserve_manual_cue(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); wav = root / 'click.wav'; rate=22050
            samples=array.array('h', [0]) * (rate*15)
            for beat in range(1,30):
                start=beat*rate//2
                for i in range(441):
                    samples[start+i]=round(20000*math.sin(2*math.pi*1000*i/rate)*math.exp(-i/100))
            with wave.open(str(wav),'wb') as f:
                f.setnchannels(1); f.setsampwidth(2); f.setframerate(rate); f.writeframes(samples.tobytes())
            env={**os.environ,'CRATELOOM_DATA':str(root/'data'),'PYTHONPATH':str(Path(__file__).resolve().parents[1])}
            p=subprocess.Popen([sys.executable,'-m','crateloom'],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,env=env)
            def call(method,params):
                p.stdin.write(json.dumps({'protocol_version':1,'request_id':method,'method':method,'params':params})+'\n'); p.stdin.flush()
                response=json.loads(p.stdout.readline()); self.assertNotIn('error',response); return response['result']
            try:
                t=call('library.import',{'path':str(wav)}); cue={'slot':0,'position_us':500000,'label':'Manual'}
                call('cues.save',{'id':t['id'],'cues':[cue]})
                j=call('analysis.enqueue',{'id':t['id']}); cancelled=call('jobs.cancel',{'id':j['id']}); self.assertEqual(cancelled['state'],'cancelled')
                j=call('analysis.enqueue',{'id':t['id']}); deadline=time.monotonic()+90
                while time.monotonic()<deadline:
                    j=call('jobs.get',{'id':j['id']})
                    if j['state'] not in ('queued','running'): break
                    time.sleep(.2)
                self.assertEqual(j['state'],'succeeded',j)
                t=call('track.get',{'id':t['id']}); self.assertEqual(t['cues'],[cue])
                data=json.loads(Path(t['detail_analysis']['cache_path']).read_text())
                self.assertTrue(114<=data['tempo']<=126); self.assertGreater(len(data['beats']),20)
                self.assertGreater(len(data['waveform']['peaks']), 20000)  # ~1378 windows per second
                self.assertLess(len(data['waveform']['rgb']),len(data['waveform']['peaks']))
                self.assertAlmostEqual(max(data['waveform']['peaks']),1.0,places=3)
                self.assertEqual(min(data['waveform']['peaks']),0.0)
                import base64, struct
                raw=base64.b64decode(data['waveform']['signal']['data'])
                self.assertEqual(len(raw),math.ceil(15*rate/8)*4)
                pairs=list(struct.iter_unpack('<hh',raw))
                self.assertTrue(any(lo>0 or hi<0 for lo,hi in pairs))  # actual signed oscillations
                self.assertTrue(all(lo<=hi for lo,hi in pairs))
                self.assertIsNone(data['sections']); self.assertIsNone(data['downbeats'])
                anchor=round(data['beats'][0]*1000000)
                grid=call('grid.set',{'id':t['id'],'anchor_us':anchor}); self.assertEqual(grid['grid_anchor_us'],anchor)
                shifted=call('grid.offset',{'id':t['id'],'offset_us':10000}); self.assertEqual(shifted['grid_offset_us'],10000)
                self.assertEqual(call('track.get',{'id':t['id']})['grid_offset_us'],10000)
                section={'id':'s1','start_us':0,'end_us':1000000,'label':'Intro'}
                saved=call('sections.save',{'id':t['id'],'sections':[section]}); self.assertEqual(saved['sections'],[section])
                saved=call('cues.save',{'id':t['id'],'cues':[dict(cue,color='#ff0088')]}); self.assertEqual(saved['cues'][0]['color'],'#ff0088')
                exported=call('export.rekordbox',{'id':t['id'],'path':str(root/'playlist.xml')})
                import xml.etree.ElementTree as ET
                mark=ET.parse(exported['xml']).find('.//POSITION_MARK'); self.assertEqual((mark.get('Red'),mark.get('Green'),mark.get('Blue')),('255','0','136'))
                cached=call('analysis.enqueue',{'id':t['id']}); self.assertEqual(cached['stage'],'Using cached analysis')
                self.assertEqual(call('cues.propose',{'id':t['id']})['cues'],[])
                shifted_outro=round((data['beats'][5]+.01)*1000000)
                outro={'id':'s2','start_us':shifted_outro,'end_us':shifted_outro+300000,'label':'Outro'}
                call('sections.save',{'id':t['id'],'sections':[section,outro]})
                proposals=call('cues.propose',{'id':t['id']})['cues']
                self.assertEqual([(item['slot'],item['position_us']) for item in proposals],[(6,shifted_outro)])
                fixed=call('grid.fixed',{'id':t['id'],'bpm':130,'origin_us':1000000,'bar_one':True})
                self.assertEqual((fixed['grid_bpm'],fixed['grid_origin_us'],fixed['grid_anchor_us']),(130,1000000,1000000))
                self.assertEqual(call('track.get',{'id':t['id']})['grid_bpm'],130)
                reset=call('grid.restore',{'id':t['id'],'state':{'bpm':None,'origin_us':None,'anchor_us':None,'offset_us':0}})
                self.assertIsNone(reset['grid_bpm']); self.assertIsNone(reset['grid_anchor_us'])
                restored=call('grid.restore',{'id':t['id'],'state':{'bpm':130,'origin_us':1000000,'anchor_us':1000000,'offset_us':10000}})
                self.assertEqual((restored['grid_bpm'],restored['grid_offset_us']),(130,10000))
                # Replacing an older analyzer cache of the same stream preserves a fixed grid.
                import sqlite3
                with sqlite3.connect(root/'data'/'library.sqlite3') as db:
                    detail=json.loads(db.execute('SELECT detail_analysis FROM tracks WHERE id=?',(t['id'],)).fetchone()[0]); detail['cache_key']='old-version-key'
                    db.execute('UPDATE tracks SET detail_analysis=?,fixed_grid_key=?,grid_cache_key=?,offset_cache_key=? WHERE id=?',(json.dumps(detail),'old-version-key','old-version-key','old-version-key',t['id']))
                upgraded=call('analysis.enqueue',{'id':t['id']})
                self.assertEqual(upgraded['state'],'succeeded')
                preserved=call('track.get',{'id':t['id']})
                self.assertEqual((preserved['grid_bpm'],preserved['grid_anchor_us'],preserved['grid_offset_us']),(130,1000000,10000))
                generated=call('cues.auto',{'id':t['id']})
                self.assertGreater(generated['added'],0); self.assertGreater(generated['free_slots'],0); self.assertEqual(generated['track']['cues'][0]['label'],'Manual')
                self.assertEqual(call('cues.auto',{'id':t['id']})['added'],0)

                forced=call('analysis.enqueue',{'id':t['id'],'force':True}); self.assertEqual(forced['state'],'running')
                self.assertEqual(call('jobs.cancel',{'id':forced['id']})['state'],'cancelled')
                self.assertEqual(call('track.get',{'id':t['id']})['cues'][0]['label'],'Manual')
                changed=call('analysis.enqueue',{'id':t['id'],'force':True})
                original=wav.read_bytes()
                with wav.open('r+b') as f:
                    f.seek(-100,2); value=f.read(1); f.seek(-1,1); f.write(bytes([value[0] ^ 1]))
                deadline=time.monotonic()+90
                while time.monotonic()<deadline:
                    changed=call('jobs.get',{'id':changed['id']})
                    if changed['state'] not in ('queued','running'): break
                    time.sleep(.2)
                self.assertEqual(changed['state'],'failed',changed)
                self.assertIn('changed',changed['error'])
                wav.write_bytes(original)
                self.assertEqual(call('track.get',{'id':t['id']})['cues'][0]['label'],'Manual')
                if os.name == 'posix':
                    other=root/'second.wav'
                    other.write_bytes(wav.read_bytes())
                    # Different duration/stream avoids the already completed cache.
                    with wave.open(str(other),'wb') as f:
                        f.setnchannels(1); f.setsampwidth(2); f.setframerate(rate); f.writeframes(samples[:rate*14].tobytes())
                    new_track=call('library.import',{'path':str(other)})
                    call('analysis.enqueue',{'id':new_track['id']})
                    worker_list=subprocess.run(['pgrep','-P',str(p.pid)],capture_output=True,text=True)
                    worker_pid=int(worker_list.stdout.split()[0]) if worker_list.stdout.strip() else None
                    self.assertTrue(call('system.shutdown',{})['stopping'])
                    p.wait(timeout=8)
                    if worker_pid:
                        with self.assertRaises(ProcessLookupError): os.kill(worker_pid,0)

            finally:
                p.stdin.close(); p.wait(timeout=10); p.stdout.close(); p.stderr.close()
