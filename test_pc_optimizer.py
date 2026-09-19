import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import pc_optimizer as pc


class BenchmarkTests(unittest.TestCase):
    def run_ping(self, output, count=3, code=0):
        with patch.object(pc, '_run', return_value=(output, '', code)):
            return pc.ping(count=count)

    def test_french_with_loss_and_submillisecond(self):
        row=self.run_ping('Réponse de 1.1.1.1 : octets=32 temps<1 ms TTL=58\nDélai dépassé.\nRéponse de 1.1.1.1 : octets=32 temps=9 ms TTL=58')
        self.assertEqual(row['received'],2)
        self.assertEqual(row['avg'],5)
        self.assertEqual(row['jitter'],8)
        self.assertAlmostEqual(row['loss'],100/3)

    def test_english_ignores_summary_and_unreachable(self):
        row=self.run_ping('Reply from 192.168.1.1: Destination host unreachable.\nReply from 1.1.1.1: bytes=32 time=12ms TTL=58\nMinimum = 12ms, Maximum = 12ms, Average = 12ms')
        self.assertEqual(row['received'],1)
        self.assertIsNone(row['jitter'])
        self.assertEqual(row['avg'],12)

    def test_no_reply_is_not_zero_latency(self):
        row=self.run_ping('Request timed out.', code=1)
        self.assertEqual(row['status'],'no_response')
        self.assertIsNone(row['avg'])
        self.assertIsNone(row['loss'])
        self.assertIn('indisponible',pc.format_benchmark([row]))
        self.assertNotIn('0.0 ms',pc.format_benchmark([row]))

    def test_missing_command(self):
        with patch.object(pc,'_run',side_effect=FileNotFoundError('missing')):
            row=pc.ping()
        self.assertEqual(row['status'],'error')
        self.assertIn('missing',row['detail'])

    def test_unreadable_reply_is_error(self):
        row=self.run_ping('Reply from 1.1.1.1: bytes=32 time=? TTL=58')
        self.assertEqual(row['status'],'error')
        self.assertIsNone(row['loss'])

    def test_validate_destination_before_execution(self):
        with self.assertRaises(ValueError):pc.ping("1.1.1.1';evil")

    def test_targets_and_fallback(self):
        with patch.object(pc,'_gateway',side_effect=FileNotFoundError),patch.object(pc,'ping',side_effect=lambda h: h):
            self.assertEqual(pc.benchmark(),['1.1.1.1','8.8.8.8'])


class StateSyncTests(unittest.TestCase):
    class FakeBackend:
        identity={'machine':'test','sid':'test'}
        def __init__(self):
            self.values={}
        def read(self,spec):
            return self.values.get(repr(spec),{'exists':False})
        def write(self,spec,value):
            self.values[repr(spec)]=value

    def test_external_change_is_resynced_instead_of_failing(self):
        backend=self.FakeBackend()
        spec={'kind':'registry','id':'game_auto'}
        target={'exists':True,'type':4,'value':1}
        backend.values[repr(spec)]={'exists':True,'type':4,'value':0}
        with tempfile.TemporaryDirectory() as root:
            first=pc._apply_changes(root,[(spec,target)],backend)
            self.assertEqual(first['changed'],1)
            # Changement externe après l'application Acolyte.
            backend.values[repr(spec)]={'exists':True,'type':4,'value':0}
            second=pc._apply_changes(root,[(spec,target)],backend)
            self.assertEqual(second['changed'],1)
            self.assertEqual(backend.read(spec),target)
            # Restaurer doit revenir au nouvel état externe, pas à un état obsolète.
            pc.restore(root,backend)
            self.assertEqual(backend.read(spec),{'exists':True,'type':4,'value':0})

    def test_feature_catalog_contains_premium_categories(self):
        for key in ('amd_gpu','telemetry_min','vbs_off','net_power','net_eee','tcp_baseline','explorer_tweaks'):
            self.assertIn(key,pc.OPTIONS)

def healthy_scan(link='2.5 Gbps'):
    return {
        'health': {
            'defender_available': True,
            'antivirus_enabled': True,
            'realtime_enabled': True,
            'signature_age_days': 0,
            'firewall': [{'Enabled': True}, {'Enabled': True}, {'Enabled': True}],
            'physical_disks': [{'HealthStatus': 'Healthy'}],
            'pending_reboot': False,
        },
        'settings': {
            'game_auto': {'exists': True, 'value': 1},
            'capture': {'exists': True, 'value': 0},
            'dvr': {'exists': True, 'value': 0},
            'power_plan': pc.BALANCED,
        },
        'bios': {'hints': []},
        'maintenance': {'temp_size_mb': 500, 'disks': [{'drive': 'C:/', 'free_gb': 300, 'total_gb': 1000}]},
        'startup_count': 4,
        'network': {'status': 'online', 'rss': True, 'description': 'Realtek Gaming 2.5GbE Family Controller', 'link': link},
    }

class HealthScoreTests(unittest.TestCase):
    def test_healthy_configuration_scores_100(self):
        score, rec, ok, breakdown = pc.score_scan(healthy_scan())
        self.assertEqual(score, 100)
        self.assertEqual(sum(breakdown.values()), 100)

    def test_1gb_link_on_25gbe_is_information_only(self):
        score, rec, ok, breakdown = pc.score_scan(healthy_scan('1 Gbps'))
        self.assertEqual(score, 100)
        self.assertTrue(any(x.get('title') == 'Lien Ethernet à 1 Gbit/s' for x in rec))

    def test_bad_configuration_drops_score_materially(self):
        scan=healthy_scan()
        scan['health'].update(antivirus_enabled=False,realtime_enabled=False,pending_reboot=True)
        scan['health']['firewall']=[{'Enabled':False}]
        scan['health']['physical_disks']=[{'HealthStatus':'Warning'}]
        scan['settings']['game_auto']={'exists':True,'value':0}
        scan['settings']['capture']={'exists':True,'value':1}
        scan['settings']['dvr']={'exists':True,'value':1}
        scan['settings']['power_plan']='00000000-0000-0000-0000-000000000000'
        scan['bios']['hints']=['EXPO à vérifier']
        scan['maintenance']['disks']=[{'drive':'C:/','free_gb':40,'total_gb':1000}]
        scan['startup_count']=25
        scan['network']['status']='offline'
        score, rec, ok, breakdown=pc.score_scan(scan)
        self.assertLess(score,30)
        self.assertEqual(score,sum(breakdown.values()))

if __name__=='__main__':unittest.main()
