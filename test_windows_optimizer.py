import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import pc_optimizer as pc


class FakeWindows:
    identity={'machine':'test-pc','sid':'user-a'}
    def __init__(self):
        self.values={};self.writes=[];self.fail_at=None;self.fail_restore=False
    def key(self,spec):return json.dumps(spec,sort_keys=True)
    def read(self,spec):
        if self.key(spec) in self.values:return copy.deepcopy(self.values[self.key(spec)])
        return {'exists':False} if spec['kind']=='registry' else (pc.BALANCED if spec['kind']=='power' else 1)
    def write(self,spec,value):
        self.writes.append((spec,value))
        # Simulate failure even after a partially successful OS write.
        self.values[self.key(spec)]=copy.deepcopy(value)
        if len(self.writes)==self.fail_at or (self.fail_restore and value=={'exists':False}):raise OSError('denied')


class WindowsOptimizerTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name);self.backend=FakeWindows()
    def tearDown(self):self.temp.cleanup()
    def state(self):return json.loads((self.root/pc.JOURNAL_NAME).read_text())
    def apply(self,options):return pc.optimize(self.root,options,self.backend)
    def test_repeated_apply_preserves_original(self):
        self.apply(['game']);original=self.state()['entries'][0]['original']
        self.apply(['game','captures'])
        self.assertEqual(self.state()['entries'][0]['original'],original)
        pc.restore(self.root,self.backend)
        self.assertTrue(all(value=={'exists':False} for value in self.backend.values.values()))
        self.assertFalse((self.root/pc.JOURNAL_NAME).exists())
    def test_rollback_partial_write(self):
        self.backend.fail_at=2
        with self.assertRaisesRegex(RuntimeError,'annulés'):self.apply(['game'])
        self.assertTrue(all(value=={'exists':False} for value in self.backend.values.values()))
        self.assertFalse(any(e['pending'] for e in self.state()['entries']))
    def test_rollback_failure_keeps_pending(self):
        self.backend.fail_at=2;self.backend.fail_restore=True
        with self.assertRaisesRegex(RuntimeError,'partielle'):self.apply(['game'])
        self.assertTrue(any(e['pending'] for e in self.state()['entries']))
        with self.assertRaisesRegex(RuntimeError,'interrompue'):self.apply(['game'])
        self.backend.fail_restore=False
        pc.restore(self.root,self.backend)
    def test_backup_write_failure_changes_nothing(self):
        with patch.object(pc,'_save_state',side_effect=OSError('disk full')):
            with self.assertRaises(OSError):self.apply(['game'])
        self.assertEqual(self.backend.writes,[])
    def test_identity_mismatch(self):
        self.apply(['game']);state=self.state();state['identity']['sid']='other-user'
        (self.root/pc.JOURNAL_NAME).write_text(json.dumps(state));n=len(self.backend.writes)
        with self.assertRaises(ValueError):pc.restore(self.root,self.backend)
        self.assertEqual(len(self.backend.writes),n)
    def test_legacy_backup_is_not_overwritten(self):
        p=self.root/pc.BACKUP_NAME;p.write_text('{"power":"old"}')
        with self.assertRaisesRegex(RuntimeError,'ancien'):self.apply(['game'])
        self.assertEqual(p.read_text(),'{"power":"old"}');self.assertEqual(self.backend.writes,[])
    def test_external_changes_detected(self):
        self.apply(['game']);spec={'kind':'registry','id':'game_auto'}
        self.backend.values[self.backend.key(spec)]={'exists':True,'value':2,'type':4}
        with self.assertRaisesRegex(RuntimeError,'hors'):self.apply(['game'])
    def test_interrupted_write_can_restore(self):
        self.apply(['game']);state=self.state();state['entries'][0]['pending']=True
        (self.root/pc.JOURNAL_NAME).write_text(json.dumps(state))
        pc.restore(self.root,self.backend)
        self.assertTrue(all(v=={'exists':False} for v in self.backend.values.values()))
    def test_restore_failure_can_retry(self):
        self.apply(['game']);self.backend.fail_restore=True
        with self.assertRaisesRegex(RuntimeError,'partielle'):pc.restore(self.root,self.backend)
        self.assertTrue((self.root/pc.JOURNAL_NAME).exists())
        self.backend.fail_restore=False
        self.assertIn('restaurés',pc.restore(self.root,self.backend))
    def test_power_and_usb_restore_exactly(self):
        spec={'kind':'power'};old='aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee'
        self.backend.values[self.backend.key(spec)]=old
        self.apply(['balanced','usb'])
        self.assertEqual(self.backend.read(spec),pc.BALANCED)
        pc.restore(self.root,self.backend)
        self.assertEqual(self.backend.read(spec),old)
        self.assertEqual(self.backend.read({'kind':'usb','plan':pc.BALANCED}),1)
    def test_no_usb_or_power_change_by_default(self):
        self.apply(['game'])
        self.assertTrue(all(s['kind']=='registry' for s,v in self.backend.writes))
    def test_startup_restore_preserves_expand_string(self):
        spec={'kind':'registry','id':'startup','name':'Example'}
        original={'exists':True,'value':'%APPDATA%\\example.exe --arg','type':2}
        self.backend.values[self.backend.key(spec)]=original
        pc._apply_changes(self.root,[(spec,{'exists':False})],self.backend)
        pc.restore(self.root,self.backend)
        self.assertEqual(self.backend.read(spec),original)
    def test_invalid_removal_never_executes(self):
        with patch.object(pc,'_ps') as ps:
            with self.assertRaises(ValueError):pc.remove_app(self.root,"Microsoft.WindowsStore'; evil")
            ps.assert_not_called()
    def test_noop(self):
        self.apply(['game']);n=len(self.backend.writes)
        self.assertIn('Aucun changement',self.apply(['game']))
        self.assertEqual(n,len(self.backend.writes))
    def test_null_inventory(self):
        with patch.object(pc,'_strict_json',return_value=None):self.assertEqual(pc.removable_apps(),[])
    def test_unreadable_snapshot_never_writes(self):
        with patch.object(self.backend,'read',side_effect=OSError('cannot read')):
            with self.assertRaises(OSError):self.apply(['game'])
        self.assertEqual(self.backend.writes,[])

if __name__=='__main__':unittest.main()
