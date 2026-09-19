import json
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

    def test_interrupted_pending_state_self_heals(self):
        backend=self.FakeBackend()
        spec={'kind':'registry','id':'game_auto'}
        original={'exists':True,'type':4,'value':0}
        target={'exists':True,'type':4,'value':1}
        backend.values[repr(spec)]=original
        with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as local:
            with patch.dict(pc.os.environ,{'LOCALAPPDATA':local}):
                state={'schema':2,'identity':backend.identity,'entries':[{
                    'spec':spec,'original':original,'applied':original,'pending':True,'target':target
                }]}
                pc._save_state(pc.optimizer_state_path(root),state)
                result=pc._apply_changes(root,[(spec,target)],backend)
                self.assertEqual(result['changed'],1)
                healed=json.loads(pc.optimizer_state_path(root).read_text(encoding='utf-8'))
                self.assertFalse(any(e.get('pending') for e in healed['entries']))
                self.assertEqual(backend.read(spec),target)

    def test_optimizer_state_lives_outside_app_folder(self):
        with tempfile.TemporaryDirectory() as app, tempfile.TemporaryDirectory() as local:
            with patch.dict(pc.os.environ,{'LOCALAPPDATA':local}):
                path=pc.optimizer_state_path(app)
                self.assertTrue(str(path).startswith(str(Path(local))))
                self.assertNotEqual(path.parent,Path(app))

    def test_admin_option_detection(self):
        self.assertTrue(pc.options_require_admin(['hags_on']))
        self.assertTrue(pc.options_require_admin(['amd_gpu']))
        self.assertFalse(pc.options_require_admin(['game','captures']))

    def test_apply_batch_without_admin_for_user_only_options(self):
        with tempfile.TemporaryDirectory() as root:
            with patch.object(pc,'is_admin',return_value=False), \
                 patch.object(pc,'_apply_batch_local',return_value='ok') as local, \
                 patch.object(pc,'_apply_batch_elevated',return_value='elevated') as elevated:
                self.assertEqual(pc.apply_batch(root,['game'],[]),'ok')
                local.assert_called_once()
                elevated.assert_not_called()

    def test_apply_batch_requests_elevation_for_admin_options(self):
        with tempfile.TemporaryDirectory() as root:
            with patch.object(pc,'is_admin',return_value=False), \
                 patch.object(pc,'_apply_batch_local',return_value='local') as local, \
                 patch.object(pc,'_apply_batch_elevated',return_value='elevated') as elevated:
                self.assertEqual(pc.apply_batch(root,['hags_on'],[]),'elevated')
                elevated.assert_called_once()
                local.assert_not_called()

    def test_balanced_requires_admin(self):
        self.assertTrue(pc.options_require_admin(['balanced']))

    def test_unexpected_access_denied_retries_elevated(self):
        with tempfile.TemporaryDirectory() as root:
            denied=RuntimeError('Échec ; changements de cette opération annulés. [WinError 5] Accès refusé')
            with patch.object(pc,'is_admin',return_value=False), \
                 patch.object(pc,'options_require_admin',return_value=False), \
                 patch.object(pc,'_apply_batch_local',side_effect=denied) as local, \
                 patch.object(pc,'_apply_batch_elevated',return_value='elevated') as elevated:
                self.assertEqual(pc.apply_batch(root,['game'],[]),'elevated')
                local.assert_called_once()
                elevated.assert_called_once()

    def test_net_low_latency_is_admin_option(self):
        self.assertIn('net_low_latency',pc.ADMIN_OPTIONS)

    def test_network_quality_score_penalizes_jitter_and_loss(self):
        a=[{'avg':10,'jitter':1,'loss':0}]
        b=[{'avg':10,'jitter':5,'loss':1}]
        self.assertLess(pc._network_quality_score(a),pc._network_quality_score(b))

    def test_complete_profile_avoids_risky_options(self):
        with patch.object(pc,'analyze',return_value={'cpu':'AMD Ryzen 7 7800X3D','gpu':'AMD Radeon RX 7900 XT'}):
            opts=pc.complete_gaming_profile_options()
        self.assertIn('game',opts)
        self.assertIn('amd_gpu',opts)
        self.assertIn('balanced',opts)
        self.assertNotIn('vbs_off',opts)
        self.assertNotIn('sysmain_off',opts)
        self.assertNotIn('nagle_off',opts)

    def test_research_audit_rejects_blind_launch_args(self):
        rows={x['name']:x for x in pc.optimization_research_audit()}
        self.assertEqual(rows['-NOSPLASH']['status'],'Non appliqué')
        self.assertEqual(rows['-NOTEXTURESTREAMING']['status'],'Non appliqué')
        self.assertEqual(rows['HPET / bcdedit timers / timer hacks']['status'],'Refusé')

    def test_ping_hostname_validation(self):
        with patch.object(pc.socket,'gethostbyname',return_value='1.2.3.4'), \
             patch.object(pc,'_run',return_value=('Reply from 1.2.3.4: bytes=32 time=10ms TTL=50\n','',0)):
            row=pc.ping('ping-eu.ds.on.epicgames.com',1)
        self.assertEqual(row['host'],'ping-eu.ds.on.epicgames.com')
        self.assertEqual(row['address'],'1.2.3.4')
        self.assertEqual(row['status'],'ok')

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


class GameProfileTests(unittest.TestCase):
    def test_fortnite_ini_patch_is_idempotent(self):
        original=(
            '[/Script/FortniteGame.FortGameUserSettings]\n'
            'bUseVSync=True\n'
            'FrameRateLimit=240.000000\n\n'
            '[ScalabilityGroups]\n'
            'sg.ShadowQuality=3\n'
        )
        patched=pc._patch_ini_text(original,pc.FORTNITE_COMPETITIVE_SETTINGS)
        patched2=pc._patch_ini_text(patched,pc.FORTNITE_COMPETITIVE_SETTINGS)
        self.assertEqual(patched,patched2)
        values={}
        section=''
        for raw in patched.splitlines():
            line=raw.strip()
            if line.startswith('[') and line.endswith(']'):
                section=line[1:-1];continue
            if '=' in line:
                k,v=line.split('=',1);values[(section,k)]=v
        self.assertEqual(values[('/Script/FortniteGame.FortGameUserSettings','bUseVSync')],'False')
        self.assertEqual(values[('/Script/FortniteGame.FortGameUserSettings','FrameRateLimit')],'0.000000')
        self.assertEqual(values[('ScalabilityGroups','sg.ShadowQuality')],'0')

    def test_gaming_score_rewards_profile_and_real_benchmark(self):
        scan={
            'settings':{'feature_states':{'game':True,'captures':True,'balanced':True}},
            'health':{'pending_reboot':False},
            'network':{'status':'online','rss':True},
            'bios':{'hints':[]},
            'game_profiles':{'fortnite':{
                'installed':True,'registry_total':5,'registry_ok':5,
                'config_total':14,'config_ok':14,'config_exists':True
            }}
        }
        bench=[{
            'avg_fps':400,'one_percent_low':300,'point_one_percent_low':220,
            'duration_seconds':60,'stutters_33ms':0
        }]
        with patch.object(pc,'benchmark_history',return_value=bench):
            score,breakdown,rec=pc.gaming_score_scan(scan)
        self.assertEqual(score,100)
        self.assertEqual(sum(breakdown.values()),100)

    def test_gaming_score_does_not_award_unmeasured_benchmark(self):
        scan={
            'settings':{'feature_states':{'game':True,'captures':True,'balanced':True}},
            'health':{'pending_reboot':False},
            'network':{'status':'online','rss':True},
            'bios':{'hints':[]},
            'game_profiles':{'fortnite':{
                'installed':True,'registry_total':5,'registry_ok':5,
                'config_total':14,'config_ok':14,'config_exists':True
            }}
        }
        with patch.object(pc,'benchmark_history',return_value=[]):
            score,breakdown,rec=pc.gaming_score_scan(scan)
        self.assertEqual(score,80)
        self.assertEqual(breakdown['Benchmark réel'],0)
        self.assertTrue(any(x.get('title')=='Benchmark Fortnite manquant' for x in rec))


class CompetitiveResearchTests(unittest.TestCase):
    def test_competitive_pack_stays_conservative(self):
        self.assertIn('game',pc.COMPETITIVE_SAFE_OPTIONS)
        self.assertIn('captures',pc.COMPETITIVE_SAFE_OPTIONS)
        self.assertIn('tcp_baseline',pc.COMPETITIVE_SAFE_OPTIONS)
        self.assertNotIn('vbs_off',pc.COMPETITIVE_SAFE_OPTIONS)
        self.assertNotIn('nagle_off',pc.COMPETITIVE_SAFE_OPTIONS)
        self.assertNotIn('net_low_latency',pc.COMPETITIVE_SAFE_OPTIONS)
        self.assertNotIn('hags_on',pc.COMPETITIVE_SAFE_OPTIONS)

    def test_research_audit_marks_amd_competitive_guidance(self):
        scan={
            'system':{'gpu':'AMD Radeon RX 7900 XT','cpu':'AMD Ryzen 7 7800X3D'},
            'settings':{'feature_states':{
                'game':True,'captures':True,'balanced':True,'mouse_accel_off':True,
                'tcp_baseline':True,'hags_on':False
            }}
        }
        audit=pc.gaming_research_audit(scan)
        keys={x['key'] for x in audit['cards']}
        self.assertIn('amd_antilag',keys)
        self.assertIn('amd_chill',keys)
        self.assertIn('hypr_rx',keys)
        self.assertTrue(any('HPET' in x for x in audit['excluded']))

if __name__=='__main__':unittest.main()
