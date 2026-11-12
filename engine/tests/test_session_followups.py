from contextlib import ExitStack
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from crateloom import __main__ as api
from crateloom import acquisitions, providers, set_import


class Followups(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory())).resolve()
        for name, value in [('ROOT',self.root),('DB',self.root/'library.sqlite3'),('CACHE',self.root/'cache')]:
            self.stack.enter_context(patch.object(api,name,value))
        self.addCleanup(acquisitions.shutdown)

    def test_typed_query_without_a_separator_finds_its_artist(self):
        payload = {'recordings':[
            {'id':'1','title':'One More Time (Tribute)','artist-credit':[{'name':'Cover Band'}]},
            {'id':'2','title':'One More Time','artist-credit':[{'name':'Daft Punk'}]}]}
        with patch.object(providers,'musicbrainz',return_value=payload) as request:
            identity = providers.credit_split(providers.search_identity('Daft Punk One More Time'))
            self.assertEqual((identity['artist'],identity['title']),('Daft Punk','One More Time'))
            self.assertIn('artist:(daft punk one more time)',request.call_args.args[1]['query'])
            reversed_order = providers.credit_split(providers.search_identity('one more time daft punk'))
            self.assertEqual((reversed_order['artist'],reversed_order['title']),('daft punk','one more time'))
            credited = providers.search_identity('Daft Punk - One More Time')
            calls = request.call_count
            self.assertEqual(providers.credit_split(credited),credited)
            self.assertEqual(request.call_count,calls)
        with patch.object(providers,'musicbrainz',return_value={'recordings':[{'id':'3','title':'Something Else','artist-credit':[{'name':'Nobody'}]}]}):
            unknown = providers.search_identity('some words nobody matches')
            self.assertEqual(providers.credit_split(unknown),unknown)
        with patch.object(providers,'musicbrainz',side_effect=ValueError('MusicBrainz is unavailable.')):
            self.assertEqual(providers.credit_split(unknown),unknown)

    def test_set_list_agrees_with_the_report_about_an_older_partial_scan(self):
        rows = [{'at':0,'end':30,'artist':'Artist','title':'First','version':'','unknown':False,'evidence':['Fingerprint']}]
        saved = set_import.save_report(api,'Mix','https://www.youtube.com/watch?v=RvRhUHTV_8k',rows,'Partial scan. Shazam is rate limiting requests.',automatic=False)
        with api.connect() as db:
            db.execute('DELETE FROM dj_set_auto_scan WHERE set_id=?',(saved['id'],))
            db.execute('DELETE FROM dj_set_scan_work WHERE set_id=?',(saved['id'],))
            db.execute('DELETE FROM dj_set_scan WHERE set_id=?',(saved['id'],))
        report = api.dispatch('sets.get',{'id':saved['id']})
        listed = api.dispatch('sets.list',{})[0]
        self.assertEqual(report['scan']['offset'],30)
        self.assertEqual(listed['offset'],30)
        self.assertNotIn('warning',listed)

    def test_saved_youtube_access_is_reported_and_can_be_changed_on_resume(self):
        saved = set_import.save_report(api,'Mix','https://www.youtube.com/watch?v=RvRhUHTV_8k',[],None,checkpoint=(0,120,None,0),plan={'samples':{}},automatic=False)
        sid = saved['id']
        self.assertIsNone(api.dispatch('sets.get',{'id':sid})['youtube_browser'])
        with patch.object(set_import,'AUTO_WAKE'):
            report = api.dispatch('sets.auto_scan',{'id':sid,'enabled':False,'youtube_browser':'chrome'})
            self.assertEqual(report['youtube_browser'],'chrome')
            self.assertEqual(api.dispatch('sets.auto_scan',{'id':sid,'enabled':False})['youtube_browser'],'chrome')
            self.assertIsNone(api.dispatch('sets.auto_scan',{'id':sid,'enabled':False,'youtube_browser':None})['youtube_browser'])
            with self.assertRaisesRegex(ValueError,'browser session'):
                api.dispatch('sets.auto_scan',{'id':sid,'enabled':False,'youtube_browser':'/tmp/profile'})

    def test_connections_are_reported_and_failed_downloads_can_be_cleared(self):
        with patch.object(set_import,'SPOTIFY_TOKEN','token'),patch.dict('os.environ',{'CRATELOOM_SOUNDCLOUD_TOKEN':''}):
            status = api.dispatch('sets.capabilities',{})
        self.assertTrue(status['spotify_connected'])
        self.assertFalse(status['soundcloud_connected'])
        review = json.dumps({'source':{'url':'https://example.org/a.mp3','filename':'a.mp3'},'fields':{'title':'A','artist':'B'}})
        with api.connect() as db:
            for jid,state in (('one','failed'),('two','completed'),('three','cancelled')):
                db.execute("INSERT INTO acquisitions(id,state,stage,review,created_at,updated_at) VALUES (?,?,?,?,?,?)",(jid,state,state,review,'now','now'))
        with patch.object(acquisitions.Manager,'ensure_worker'):
            self.assertEqual(api.dispatch('acquisitions.clear_failed',{}),{'cleared':2})
            self.assertEqual([job['id'] for job in api.dispatch('acquisitions.list',{})],['two'])
            api.dispatch('acquisitions.retry',{'id':'one'})
            self.assertEqual(sorted(job['id'] for job in api.dispatch('acquisitions.list',{})),['one','two'])


if __name__=='__main__':unittest.main()
