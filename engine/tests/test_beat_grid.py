import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from crateloom.beat_grid import complete_beats


class BeatGrid(unittest.TestCase):
    def test_completion_preserves_detected_positions_and_covers_whole_track(self):
        raw = [2, 2.5, 3, 5, 5.5]
        self.assertEqual(complete_beats(raw, 8, 120), [i*.5 for i in range(16)])
        self.assertEqual(raw, [2, 2.5, 3, 5, 5.5])
        self.assertEqual(complete_beats([], 3, 120, .2, -.3), [.4, .9, 1.4, 1.9, 2.4, 2.9])
        self.assertEqual(complete_beats([], 8), [])

    def test_inferred_anchor_is_saved_and_restored_without_rewriting_detected_cues(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);audio=root/'grid.wav';data=root/'data'
            subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','sine=duration=8','-y',str(audio)],check=True)
            env={**os.environ,'CRATELOOM_DATA':str(data),'PYTHONPATH':str(Path(__file__).resolve().parents[1])}
            def response(method, params):
                p=subprocess.run([sys.executable,'-m','crateloom'],input=json.dumps({'protocol_version':1,'request_id':'grid','method':method,'params':params})+'\n',capture_output=True,text=True,env=env,check=True)
                return json.loads(p.stdout)
            def call(method,params):
                reply=response(method,params);self.assertNotIn('error',reply);return reply['result']
            track=call('library.import',{'path':str(audio)})
            cache=data/'test-beats.json';original=json.dumps({'beats':[2,2.5,3,5,5.5],'tempo':120});cache.write_text(original)
            detail={'cache_path':str(cache),'cache_key':'fixture','tempo':120,'version':'librosa-0.11.0-rgb-5'}
            with sqlite3.connect(data/'library.sqlite3') as db:
                db.execute('UPDATE tracks SET detail_analysis=? WHERE id=?',(json.dumps(detail),track['id']))
            saved=call('grid.set',{'id':track['id'],'anchor_us':500000})
            self.assertEqual(saved['grid_anchor_us'],500000)
            restored=call('grid.restore',{'id':track['id'],'state':{'bpm':None,'origin_us':None,'anchor_us':500000,'offset_us':10000}})
            self.assertEqual(restored['grid_anchor_us'],500000)
            self.assertEqual(restored['grid_offset_us'],10000)
            self.assertIn('error',response('grid.set',{'id':track['id'],'anchor_us':123456}))
            result=call('cues.auto',{'id':track['id']})
            self.assertEqual(result['track']['cues'][0]['position_us'],2010000)
            self.assertEqual(cache.read_text(),original)


if __name__ == '__main__': unittest.main()
