from contextlib import ExitStack
import errno
import hashlib
import json
from pathlib import Path, PurePosixPath, PureWindowsPath
import subprocess
import tempfile
import time
import unittest
from unittest.mock import patch
from urllib.parse import unquote, urlsplit
import xml.etree.ElementTree as ET

from crateloom import __main__ as api, rekordbox_export as exporter


class PlaylistExport(unittest.TestCase):
    def setUp(self):
        self.stack=ExitStack()
        self.addCleanup(self.stack.close)
        self.root=Path(self.stack.enter_context(tempfile.TemporaryDirectory())).resolve()
        for key,value in [('ROOT',self.root),('DB',self.root/'library.sqlite3'),('CACHE',self.root/'analysis-cache')]:
            self.stack.enter_context(patch.object(api,key,value))
        self.sources=[self.root/'音 & #1.wav',self.root/'Second space.wav']
        self.ids=[]
        for index,source in enumerate(self.sources):
            subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i',f'sine=frequency={440+index*110}:duration=2',str(source)],check=True)
            track=api.dispatch('library.import',{'path':str(source)})
            self.ids.append(track['id'])
            api.dispatch('cues.save',{'id':track['id'],'cues':[{'slot':7 if index else 0,'position_us':1234567,'label':'Cue & 中文','color':'#12abef'}]})
        self.playlist=api.dispatch('playlists.save',{'name':'Set <音 & Space>','track_ids':self.ids[::-1]})

    def test_order_cues_verified_copies_and_manifest_round_trip(self):
        dest=self.root/'Set 音.xml'
        task=api.dispatch('export.enqueue',{'playlist_id':self.playlist['id'],'copy_audio':True,'path':str(dest)})
        deadline=time.monotonic()+5
        while task['state']=='running' and time.monotonic()<deadline:
            time.sleep(.03)
            task=api.dispatch('metadata.job',{'id':task['id']})
        self.assertEqual(task['state'],'completed',task)
        result=task['result'];self.assertEqual(result['track_count'],2)
        root=ET.parse(dest).getroot()
        collection=root.find('COLLECTION');node=root.find('./PLAYLISTS/NODE/NODE')
        self.assertEqual(node.get('Name'),self.playlist['name'])
        self.assertEqual(collection.get('Entries'),'2')
        self.assertEqual([n.get('Key') for n in node],['1','2'])
        self.assertEqual([n.get('Name') for n in collection],['Second space','音 & #1'])
        self.assertEqual([n.get('Num') for n in collection.findall('.//POSITION_MARK')],['7','0'])
        self.assertTrue(all(n.get('Start')=='1.234567' and n.get('Green')=='171' for n in collection.findall('.//POSITION_MARK')))
        self.assertIsNone(collection.find('.//TEMPO'))
        manifest=json.loads(Path(result['manifest']).read_text());backup=json.loads(Path(result['backup']).read_text())
        self.assertEqual(manifest['xml_sha256'],hashlib.sha256(dest.read_bytes()).hexdigest())
        self.assertEqual([t['id'] for t in backup['tracks']],self.ids[::-1])
        for item,entry,original in zip(collection,manifest['tracks'],self.sources[::-1]):
            copied=Path(unquote(urlsplit(item.get('Location')).path))
            self.assertEqual(copied,self.root/entry['relative_audio'])
            self.assertNotEqual(copied,original)
            self.assertEqual(copied.read_bytes(),original.read_bytes())
            self.assertEqual(entry['file_sha256'],hashlib.sha256(copied.read_bytes()).hexdigest())

    def test_missing_track_does_not_create_a_partial_export(self):
        self.sources[0].unlink()
        dest=self.root/'Missing.xml'
        with self.assertRaisesRegex(ValueError,'missing'):
            api.dispatch('export.rekordbox',{'track_ids':self.ids,'path':str(dest)})
        self.assertFalse(list(self.root.glob('Missing*')))

    def test_only_reviewed_fixed_grids_export_with_offset(self):
        cache = self.root/'beats.json'
        cache.write_text(json.dumps({'beats':[.125,.75,1.375]}))
        for tid in self.ids:
            with api.connect() as db:
                db.execute('UPDATE tracks SET detail_analysis=? WHERE id=?',
                           (json.dumps({'cache_path':str(cache),'cache_key':'qa'}), tid))
        first, second = self.ids
        api.dispatch('grid.fixed', {'id':first,'bpm':96.0,'origin_us':125000,'bar_one':True})
        api.dispatch('grid.offset', {'id':first,'offset_us':12000})
        api.dispatch('grid.set', {'id':second,'anchor_us':125000})
        dest=self.root/'Grid.xml'
        result=api.dispatch('export.rekordbox', {'track_ids':self.ids,'path':str(dest)})
        a,b=ET.parse(dest).findall('./COLLECTION/TRACK')
        self.assertEqual(result['grid_count'],1)
        self.assertEqual(a.attrib['AverageBpm'],'96.0')
        self.assertEqual(a.find('TEMPO').attrib, {'Inizio':'0.137000','Bpm':'96.0','Metro':'4/4','Battito':'1'})
        self.assertEqual(a.find('POSITION_MARK').attrib['Start'],'1.234567')
        self.assertIsNone(b.find('TEMPO'))
        api.dispatch('grid.fixed', {'id':second,'bpm':96.0,'origin_us':125000,'bar_one':False})
        dest=self.root/'Unreviewed.xml'
        self.assertEqual(api.dispatch('export.rekordbox', {'id':second,'path':str(dest)})['grid_count'],0)

    def test_no_overwrite_and_rollback_if_publication_fails(self):
        dest=self.root/'Keep.xml';dest.write_bytes(b'keep')
        with self.assertRaisesRegex(ValueError,'already exist'):
            api.dispatch('export.rekordbox',{'track_ids':self.ids,'path':str(dest)})
        self.assertEqual(dest.read_bytes(),b'keep')
        dest=self.root/'Rollback.xml'
        publish=exporter.publish
        def failing(src,target,created):
            if target==dest:raise OSError('simulated publish failure')
            publish(src,target,created)
        with patch.object(exporter,'publish',side_effect=failing),self.assertRaises(OSError):
            api.dispatch('export.rekordbox',{'track_ids':self.ids,'path':str(dest),'copy_audio':True})
        self.assertFalse(list(self.root.glob('Rollback*')))
        self.assertTrue(all(path.exists() for path in self.sources))

    def test_fat_style_fallback_preserves_original_reference(self):
        dest=self.root/'Original.xml'
        with patch.object(exporter.os,'link',side_effect=OSError(errno.ENOTSUP,'hard links unavailable')):
            result=api.dispatch('export.rekordbox',{'track_ids':self.ids,'path':str(dest)})
        self.assertIsNone(result['audio_directory'])
        track=ET.parse(dest).find('./COLLECTION/TRACK')
        self.assertEqual(Path(unquote(urlsplit(track.get('Location')).path)),self.sources[0])
        self.assertIsNone(json.loads(Path(result['manifest']).read_text())['tracks'][0]['relative_audio'])

    def test_file_url_posix_drive_and_unc(self):
        self.assertEqual(exporter.location(PurePosixPath('/音 & #.wav')),'file://localhost/%E9%9F%B3%20%26%20%23.wav')
        self.assertEqual(exporter.location(PureWindowsPath('D:/DJ set/音.wav')),'file://localhost/D:/DJ%20set/%E9%9F%B3.wav')
        self.assertEqual(exporter.location(PureWindowsPath('//server/share/DJ #.wav')),'file://server/share/DJ%20%23.wav')
