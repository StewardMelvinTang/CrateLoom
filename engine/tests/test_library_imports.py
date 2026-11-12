import json,os,shutil,subprocess,sys,tempfile,time,unittest
from pathlib import Path

class LibraryImports(unittest.TestCase):
    def test_folder_duplicates_failures_relink_and_restart(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);folder=root/'folder';folder.mkdir();(folder/'nested').mkdir()
            a=folder/'a.wav';subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','sine=frequency=440:duration=2','-y',str(a)],check=True)
            shutil.copyfile(a,folder/'nested'/'copy.wav');(folder/'broken.mp3').write_bytes(b'not music');(folder/'notes.txt').write_text('ignored')
            b=folder/'nested'/'b.wav';subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','sine=frequency=880:duration=2','-y',str(b)],check=True)
            env={**os.environ,'CRATELOOM_DATA':str(root/'data'),'PYTHONPATH':str(Path(__file__).resolve().parents[1])}
            def start():return subprocess.Popen([sys.executable,'-m','crateloom'],stdin=subprocess.PIPE,stdout=subprocess.PIPE,text=True,env=env)
            proc=start()
            def response(method,params={}):
                proc.stdin.write(json.dumps({'protocol_version':1,'request_id':'t','method':method,'params':params})+'\n');proc.stdin.flush();return json.loads(proc.stdout.readline())
            def call(method,params={}):
                r=response(method,params);self.assertNotIn('error',r);return r['result']
            def finish(job):
                deadline=time.monotonic()+15
                while job['state'] in ('scanning','running') and time.monotonic()<deadline:
                    time.sleep(.05);job=call('imports.get',{'id':job['id']})
                self.assertEqual(job['state'],'completed',job);return job
            try:
                j=finish(call('imports.start',{'folder':str(folder)}))
                self.assertEqual((j['total'],j['imported'],j['duplicates'],j['failed']),(4,2,1,1))
                self.assertEqual(call('imports.get',{'id':j['id'],'cursor':4})['items'],[])
                tracks=call('library.list');self.assertEqual(len(tracks),2)
                t=next(t for t in tracks if t['path']==str(a.resolve()));cue={'slot':0,'position_us':500000,'label':'Preserved'}
                call('cues.save',{'id':t['id'],'cues':[cue]})
                relocated=root/'moved.wav';a.rename(relocated)
                self.assertTrue(next(x for x in call('library.list') if x['id']==t['id'])['missing'])
                self.assertIn('error',response('track.relink',{'id':t['id'],'path':str(b)}))
                fixed=call('track.relink',{'id':t['id'],'path':str(relocated)})
                self.assertEqual(fixed['cues'],[cue]);self.assertEqual(fixed['path'],str(relocated.resolve()));self.assertFalse(fixed['missing'])
                dup=call('library.import',{'path':str(folder/'nested'/'copy.wav')});self.assertEqual(dup['id'],t['id']);self.assertTrue(dup['duplicate'])
                repeated=finish(call('imports.start',{'paths':[str(relocated),str(b)]}))
                self.assertEqual((repeated['imported'],repeated['duplicates']),(0,2))
                proc.stdin.close();proc.wait(timeout=5);proc.stdout.close();proc=start()
                self.assertEqual(call('track.get',{'id':t['id']})['cues'],[cue]);self.assertEqual(call('imports.latest')['id'],repeated['id'])
                self.assertEqual(call('track.get',{'id':t['id']})['path'],str(relocated.resolve()))
            finally:
                proc.stdin.close();proc.wait(timeout=5);proc.stdout.close()

    def test_cancel_scan_and_keep_library_usable(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);folder=root/'files';folder.mkdir()
            for i in range(400): (folder/f'{i}.mp3').write_bytes(b'bad')
            env={**os.environ,'CRATELOOM_DATA':str(root/'data'),'PYTHONPATH':str(Path(__file__).resolve().parents[1])}
            p=subprocess.Popen([sys.executable,'-m','crateloom'],stdin=subprocess.PIPE,stdout=subprocess.PIPE,text=True,env=env)
            def call(m,params={}):
                p.stdin.write(json.dumps({'protocol_version':1,'request_id':'t','method':m,'params':params})+'\n');p.stdin.flush();r=json.loads(p.stdout.readline());self.assertNotIn('error',r);return r['result']
            try:
                j=call('imports.start',{'folder':str(folder)});call('imports.cancel',{'id':j['id']})
                deadline=time.monotonic()+5
                while time.monotonic()<deadline:
                    j=call('imports.get',{'id':j['id']})
                    if j['state']=='cancelled':break
                    time.sleep(.05)
                self.assertEqual(j['state'],'cancelled',j);self.assertEqual(call('library.list'),[])
            finally:p.stdin.close();p.wait(timeout=5);p.stdout.close()
