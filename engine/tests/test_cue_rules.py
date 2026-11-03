import sys
from pathlib import Path
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from crateloom.cue_rules import propose, auto_cues

class CueRules(unittest.TestCase):
    def test_confirmed_bars_use_real_beats_and_manual_sections(self):
        beats=[i*.5 for i in range(160)]
        sections=[{'label':'Chorus','start_us':40000000},{'label':'Outro','start_us':70000000}]
        result=propose(beats,sections,[],0,80)
        self.assertEqual([(c['slot'],c['position_us']) for c in result],[(0,0),(2,40000000),(3,8000000),(6,70000000)])
        self.assertEqual([c['slot'] for c in propose(beats,sections,[],None,80)],[0,2,6])
    def test_no_clamping_duplicates_or_unsupported_cues(self):
        beats=[i*.5 for i in range(50)]
        sections=[{'label':'Chorus','start_us':5101000},{'label':'Outro','start_us':7000000}]
        result=propose(beats,sections,[{'slot':0,'position_us':7000000}],0,25)
        self.assertEqual(result,[])
        self.assertEqual(propose([],sections,[],None,25),[])

    def test_auto_cues_fill_free_slots_and_deduplicate(self):
        beats=[i*.5 for i in range(80)]
        sections=[{'label':'Intro','start_us':0},{'label':'Verse','start_us':10100000},{'label':'Chorus','start_us':20090000},{'label':'Outro','start_us':35000000}]
        existing=[{'slot':0,'position_us':0,'label':'Mine'}]
        result=auto_cues(beats,sections,existing,40)
        self.assertEqual([(x['slot'],x['position_us'],x['label']) for x in result],[(1,10000000,'Verse'),(2,20000000,'Chorus'),(3,35000000,'Outro')])
        self.assertEqual(auto_cues([],[],existing,40),[])
