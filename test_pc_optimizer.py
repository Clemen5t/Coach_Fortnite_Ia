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


class RebootHelperTests(unittest.TestCase):
    def test_firmware_reboot_uses_fw_flag(self):
        with patch.object(pc.os,'name','nt'),patch.object(pc,'is_admin',return_value=True),patch.object(pc,'_run',return_value=('', '', 0)) as run:
            self.assertIn('UEFI',pc.reboot_to_firmware())
        run.assert_called_once_with(['shutdown.exe','/r','/fw','/t','0'])

    def test_firmware_error_keeps_windows_exit_code(self):
        with patch.object(pc.os,'name','nt'),patch.object(pc,'is_admin',return_value=True),patch.object(pc,'_run',return_value=('', 'Erreur système', 203)):
            with self.assertRaisesRegex(RuntimeError,'203'):
                pc.reboot_to_firmware()

    def test_advanced_startup_uses_options_flag(self):
        with patch.object(pc.os,'name','nt'),patch.object(pc,'is_admin',return_value=True),patch.object(pc,'_run',return_value=('', '', 0)) as run:
            self.assertIn('démarrage avancé',pc.reboot_to_advanced_startup())
        run.assert_called_once_with(['shutdown.exe','/r','/o','/t','0'])


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

class MemoryAndPersistenceTests(unittest.TestCase):
    def test_memory_part_number_detects_corsair_target(self):
        target,confidence,profile=pc._memory_target_from_part('CMH5X16G1B52C40A2',4800)
        self.assertEqual(target,5200)
        self.assertEqual(confidence,'part_number')

    def test_memory_profile_flags_4800_vs_5200(self):
        data={'ram':[{
            'PartNumber':'CMH5X16G1B52C40A2','ConfiguredClockSpeed':4800,'Speed':4800,
            'Capacity':17179869184,'Manufacturer':'Unknown'
        }]}
        result=pc.memory_profile_analysis(data)
        self.assertTrue(result['profile_likely_off'])
        self.assertEqual(result['current_mt'],4800)
        self.assertEqual(result['target_mt'],5200)

    def test_desired_features_path_is_global_across_app_locations(self):
        with tempfile.TemporaryDirectory() as local:
            with patch.dict(pc.os.environ,{'LOCALAPPDATA':local}):
                a=pc.optimizer_desired_path(Path(local)/'AcolyteA')
                b=pc.optimizer_desired_path(Path(local)/'AcolyteB')
                self.assertEqual(a,b)


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

    def test_apply_fortnite_profile_creates_backup_and_finishes(self):
        class FakeBackend:
            def __init__(self):self.values={}
            def read(self,spec):return self.values.get(repr(spec),{'exists':False})
            def write(self,spec,value):self.values[repr(spec)]=value

        with tempfile.TemporaryDirectory() as folder:
            base=Path(folder)
            cfg=base/'GameUserSettings.ini'
            cfg.write_text(
                '[/Script/FortniteGame.FortGameUserSettings]\n'
                'bUseVSync=True\n'
                'bMotionBlur=True\n'
                'bUseDynamicResolution=True\n'
                'FrameRateLimit=240.000000\n\n'
                '[ScalabilityGroups]\n'
                'sg.ShadowQuality=3\n',
                encoding='utf-8'
            )
            profile_dir=base/'profile'
            profile_file=profile_dir/'fortnite-profile.json'
            exe=r'C:\\Games\\Fortnite\\FortniteClient-Win64-Shipping.exe'
            fake=FakeBackend()
            with patch.object(pc.os,'name','nt'), \
                 patch.object(pc,'GAME_PROFILE_DIR',profile_dir), \
                 patch.object(pc,'FORTNITE_PROFILE_FILE',profile_file), \
                 patch.object(pc,'game_process_running',return_value={'running':False}), \
                 patch.object(pc,'_fortnite_executable',return_value=exe), \
                 patch.object(pc,'_fortnite_config_path',return_value=cfg), \
                 patch.object(pc,'WindowsSettings',return_value=fake), \
                 patch.object(pc,'fortnite_profile_status',return_value={'score':80,'profile_applied':True}):
                result=pc.apply_fortnite_profile(base)

            backup=profile_dir/'Fortnite-GameUserSettings.backup.ini'
            self.assertTrue(backup.exists())
            self.assertTrue(profile_file.exists())
            self.assertIn('Profil Fortnite compétitif appliqué',result['message'])
            text=cfg.read_text(encoding='utf-8')
            self.assertIn('bUseVSync=False',text)
            self.assertIn('sg.ShadowQuality=0',text)

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


class PersistenceAndMemoryTests(unittest.TestCase):
    def test_desired_features_survive_install_path_change(self):
        with tempfile.TemporaryDirectory() as local, tempfile.TemporaryDirectory() as root1, tempfile.TemporaryDirectory() as root2:
            with patch.dict(pc.os.environ,{'LOCALAPPDATA':local}):
                pc.set_feature_desired(root1,'ads',True)
                pc.set_feature_desired(root1,'mouse_accel_off',True)
                state=pc.desired_features(root2)
                self.assertTrue(state.get('ads'))
                self.assertTrue(state.get('mouse_accel_off'))
                self.assertEqual(pc.optimizer_desired_path(root1),pc.optimizer_desired_path(root2))

    def test_corsair_part_number_infers_5200_from_current_4800(self):
        target,confidence,profile=pc._memory_target_from_part('CMH5X16G1B52C40A2',4800)
        self.assertEqual(target,5200)
        self.assertEqual(confidence,'part_number')
        data={'ram':[{
            'PartNumber':'CMH5X16G1B52C40A2','Manufacturer':'Unknown',
            'Capacity':17179869184,'Speed':4800,'ConfiguredClockSpeed':4800
        },{
            'PartNumber':'CMH5X16G1B52C40A2','Manufacturer':'Unknown',
            'Capacity':17179869184,'Speed':4800,'ConfiguredClockSpeed':4800
        }]}
        result=pc.memory_profile_analysis(data)
        self.assertEqual(result['current_mt'],4800)
        self.assertEqual(result['target_mt'],5200)
        self.assertTrue(result['profile_likely_off'])
        self.assertFalse(result['can_apply_from_windows'])

    def test_steam_manifest_detection_lists_installed_game(self):
        with tempfile.TemporaryDirectory() as folder:
            steam=Path(folder)/'Steam'
            steamapps=steam/'steamapps'
            game=steamapps/'common'/'Rocket League'
            game.mkdir(parents=True)
            (steamapps/'appmanifest_252950.acf').write_text(
                '"AppState"\\n{\\n"appid" "252950"\\n"name" "Rocket League"\\n"installdir" "Rocket League"\\n}',
                encoding='utf-8'
            )
            with patch.object(pc,'_steam_root',return_value=steam):
                rows=pc._detect_steam_games()
            self.assertEqual(len(rows),1)
            self.assertEqual(rows[0]['name'],'Rocket League')
            self.assertEqual(rows[0]['launcher'],'Steam')


class Acolyte25Tests(unittest.TestCase):
    def test_benchmark_pair_detects_regression(self):
        before={
            'avg_fps':400,'one_percent_low':300,'point_one_percent_low':220,
            'duration_seconds':60,'stutters_33ms':1,'avg_frametime_ms':2.5
        }
        after={
            'avg_fps':360,'one_percent_low':250,'point_one_percent_low':160,
            'duration_seconds':60,'stutters_33ms':8,'avg_frametime_ms':2.8
        }
        result=pc.compare_benchmark_pair(before,after)
        self.assertTrue(result['regression'])
        self.assertFalse(result['improvement'])
        self.assertEqual(result['verdict'],'régression mesurée')

    def test_benchmark_pair_detects_improvement(self):
        before={
            'avg_fps':300,'one_percent_low':180,'point_one_percent_low':120,
            'duration_seconds':60,'stutters_33ms':6,'avg_frametime_ms':3.3
        }
        after={
            'avg_fps':330,'one_percent_low':220,'point_one_percent_low':160,
            'duration_seconds':60,'stutters_33ms':2,'avg_frametime_ms':3.0
        }
        result=pc.compare_benchmark_pair(before,after)
        self.assertTrue(result['improvement'])
        self.assertFalse(result['regression'])
        self.assertEqual(result['verdict'],'amélioration mesurée')

    def test_auto_rollback_setting_roundtrip(self):
        with tempfile.TemporaryDirectory() as folder:
            policy=Path(folder)/'benchmark-policy.json'
            with patch.object(pc,'BENCH_POLICY_FILE',policy):
                self.assertFalse(pc.auto_rollback_enabled())
                pc.set_auto_rollback(True)
                self.assertTrue(pc.auto_rollback_enabled())
                pc.set_auto_rollback(False)
                self.assertFalse(pc.auto_rollback_enabled())

    def test_auto_game_profile_setting_roundtrip(self):
        game={'name':'Rocket League','launcher':'Steam','path':r'C:\Games\RL','exe':r'C:\Games\RL\RocketLeague.exe','profile':'generic'}
        with tempfile.TemporaryDirectory() as folder:
            target=Path(folder)/'auto.json'
            with patch.object(pc,'AUTO_PROFILE_FILE',target):
                self.assertFalse(pc.auto_game_profile_enabled(game))
                pc.set_auto_game_profile(game,True)
                self.assertTrue(pc.auto_game_profile_enabled(game))
                pc.set_auto_game_profile(game,False)
                self.assertFalse(pc.auto_game_profile_enabled(game))

    def test_audit_history_can_restore_one_setting_safely(self):
        class FakeBackend:
            identity={'machine':'test','sid':'test'}
            def __init__(self):self.values={}
            def read(self,spec):return self.values.get(repr(spec),{'exists':False})
            def write(self,spec,value):self.values[repr(spec)]=value

        with tempfile.TemporaryDirectory() as local, tempfile.TemporaryDirectory() as root:
            audit=Path(local)/'audit.json'
            backend=FakeBackend()
            spec={'kind':'registry','id':'game_auto'}
            target={'exists':True,'type':4,'value':1}
            with patch.dict(pc.os.environ,{'LOCALAPPDATA':local}),patch.object(pc,'AUDIT_LOG_FILE',audit):
                pc._apply_changes(root,[(spec,target)],backend)
                rows=pc.optimizer_history(10)
                event=next(x for x in rows if x.get('kind')=='setting_change')
                self.assertEqual(backend.read(spec),target)
                result=pc.restore_history_entry(root,event['id'],backend)
                self.assertTrue(result['restored'])
                self.assertEqual(backend.read(spec),{'exists':False})

    def test_individual_restore_refuses_external_change(self):
        class FakeBackend:
            identity={'machine':'test','sid':'test'}
            def __init__(self):self.values={}
            def read(self,spec):return self.values.get(repr(spec),{'exists':False})
            def write(self,spec,value):self.values[repr(spec)]=value

        with tempfile.TemporaryDirectory() as local, tempfile.TemporaryDirectory() as root:
            audit=Path(local)/'audit.json'
            backend=FakeBackend()
            spec={'kind':'registry','id':'game_auto'}
            target={'exists':True,'type':4,'value':1}
            with patch.dict(pc.os.environ,{'LOCALAPPDATA':local}),patch.object(pc,'AUDIT_LOG_FILE',audit):
                pc._apply_changes(root,[(spec,target)],backend)
                event=next(x for x in pc.optimizer_history(10) if x.get('kind')=='setting_change')
                backend.values[repr(spec)]={'exists':True,'type':4,'value':99}
                with self.assertRaisesRegex(RuntimeError,'changé'):
                    pc.restore_history_entry(root,event['id'],backend)
                self.assertEqual(backend.read(spec),{'exists':True,'type':4,'value':99})

    def test_score_explanation_exposes_point_losses(self):
        scan={
            'score':82,'health_score':90,'gaming_score':75,
            'score_breakdown':{
                'Sécurité Windows':25,'Performances gaming':26,'Stockage / entretien':20,
                'Démarrage':7,'Réseau':10,'État Windows':5
            },
            'gaming_breakdown':{
                'Windows gaming':25,'Profil Fortnite':35,'RAM / BIOS':15,'Benchmark réel':0
            },
            'recommendations':[{'title':'Benchmark Fortnite manquant','detail':'Mesure requise.'}]
        }
        text=pc.score_explanation(scan)
        self.assertIn('Démarrage: 7/10 (-3)',text)
        self.assertIn('Benchmark réel: 0/20 (-20)',text)
        self.assertIn('Benchmark Fortnite manquant',text)

    def test_generic_profile_restore_only_reverts_owned_gpu_value(self):
        class FakeBackend:
            def __init__(self):self.values={}
            def read(self,spec):return self.values.get(repr(spec),{'exists':False})
            def write(self,spec,value):self.values[repr(spec)]=value

        game={'name':'Rocket League','launcher':'Steam','path':r'C:\Games\RL','exe':r'C:\Games\RL\RocketLeague.exe'}
        with tempfile.TemporaryDirectory() as folder:
            base=Path(folder)
            profile_dir=base/'profiles';profile_dir.mkdir()
            state_file=profile_dir/('generic-'+pc._safe_game_key(game)+'.json')
            spec={'kind':'registry','id':'game_gpu','name':game['exe']}
            original={'exists':False}
            target={'exists':True,'type':1,'value':'GpuPreference=2;'}
            pc._write_json_file(state_file,{
                'applied':True,'game':game['name'],'exe':game['exe'],
                'gpu_original':original,'gpu_target':target
            })
            fake=FakeBackend();fake.values[repr(spec)]=target
            with patch.object(pc,'GAME_PROFILE_DIR',profile_dir), \
                 patch.object(pc,'WindowsSettings',return_value=fake), \
                 patch.object(pc,'AUDIT_LOG_FILE',base/'audit.json'):
                result=pc.restore_generic_game_profile(game)
            self.assertTrue(result['restored'])
            self.assertEqual(fake.read(spec),original)


if __name__=='__main__':unittest.main()
