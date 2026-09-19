"""Optimisation Windows 11 locale pour Acolyte. Réglages mesurables, prudents et réversibles."""
import ctypes, hashlib, ipaddress, json, os, re, shutil, socket, statistics, subprocess, sys, tempfile, time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

BACKUP_NAME='pc-optimizer-backup.json'

def optimizer_base_dir():
    base=Path(os.environ.get('LOCALAPPDATA') or tempfile.gettempdir())/'AcolyteFortnite'/'Optimizer'
    base.mkdir(parents=True,exist_ok=True)
    return base

def optimizer_data_dir(root):
    """Journal technique par installation ; les préférences utilisateur sont globales."""
    root=Path(root or Path(sys.executable).resolve().parent).resolve()
    base=optimizer_base_dir()
    key=hashlib.sha256(str(root).casefold().encode('utf-8')).hexdigest()[:16]
    folder=base/key
    folder.mkdir(parents=True,exist_ok=True)
    return folder

def optimizer_state_path(root):
    return optimizer_data_dir(root)/JOURNAL_NAME

def optimizer_desired_path(root=None):
    # Global : ne change plus lors d'une mise à jour, d'un nouvel EXE ou d'un déplacement de l'app.
    return optimizer_base_dir()/DESIRED_NAME

def optimizer_lock_path(root):
    return optimizer_data_dir(root)/'pc-optimizer.lock'

def _migrate_optimizer_file(root,name):
    target=optimizer_data_dir(root)/name
    if target.exists():return target
    old=Path(root)/name
    if old.exists():
        try:
            target.write_bytes(old.read_bytes())
        except OSError:
            pass
    return target


def _run(args, timeout=35, encoding=None):
    p=subprocess.run(args,capture_output=True,text=True,encoding=encoding,errors="replace",timeout=timeout,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    return (p.stdout or '').strip(),(p.stderr or '').strip(),p.returncode

def _ps(script, timeout=40):
    script='[Console]::OutputEncoding=[Text.Encoding]::UTF8; '+script
    encoded=__import__('base64').b64encode(script.encode('utf-16le')).decode()
    return _run(['powershell.exe','-NoProfile','-ExecutionPolicy','Bypass','-EncodedCommand',encoded],timeout,encoding='utf-8')

def is_admin():
    try:return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:return False

def _ps_json(script):
    out,err,code=_ps(script+' | ConvertTo-Json -Depth 6 -Compress')
    if code or not out:return None
    try:return json.loads(out)
    except Exception:return None

def analyze():
    info=_strict_json("""$cpu=(Get-CimInstance Win32_Processor|Select -First 1 -Expand Name)
$gpu=(Get-CimInstance Win32_VideoController|Select -Expand Name)-join ', '
$board=(Get-CimInstance Win32_BaseBoard|Select -First 1 -Expand Product)
$bios=(Get-CimInstance Win32_BIOS|Select -First 1 -Expand SMBIOSBIOSVersion)
$os=Get-CimInstance Win32_OperatingSystem
$nic=Get-NetAdapter -Physical -ErrorAction SilentlyContinue|Where-Object Status -eq 'Up'|Sort-Object LinkSpeed -Descending|Select -First 1
[ordered]@{cpu=$cpu;gpu=$gpu;board=$board;bios=$bios;windows=$os.Caption;build=$os.BuildNumber;ram=[math]::Round($os.TotalVisibleMemorySize/1MB,1);nic=$nic.Name;nic_desc=$nic.InterfaceDescription;link=$nic.LinkSpeed}""") or {}
    info['admin']=is_admin()
    return info

def _gateway():
    out,err,code=_ps("""$r=Get-NetRoute -DestinationPrefix '0.0.0.0/0' -ErrorAction SilentlyContinue|Sort-Object RouteMetric,InterfaceMetric|Select -First 1 -ExpandProperty NextHop; $r""")
    return out.strip() if not code and out.strip() else None

def ping(host='1.1.1.1', count=16):
    """Mesure ICMP Windows native. Accepte une IPv4 ou un nom DNS explicite."""
    original=str(host or '').strip()
    if not original:raise ValueError('Hôte vide.')
    try:
        address=str(ipaddress.IPv4Address(original))
    except ValueError:
        if len(original)>253 or not re.fullmatch(r'[A-Za-z0-9.-]+',original):
            raise ValueError('Nom d’hôte invalide.')
        try:address=socket.gethostbyname(original)
        except OSError as exc:raise RuntimeError('Résolution DNS impossible pour '+original+' : '+str(exc)) from exc
    count=int(count)
    if not 1 <= count <= 100:raise ValueError('Nombre de requêtes invalide.')
    result={'host':original,'address':address,'avg':None,'min':None,'max':None,'jitter':None,
            'loss':None,'received':0,'sent':count,'status':'error','detail':''}
    executable=str(Path(os.environ.get('SystemRoot',r'C:\Windows'))/'System32'/'PING.EXE')
    try:
        out,err,code=_run([executable,'-4','-n',str(count),'-w','1500',address],
                          timeout=count*2.5+5)
    except (OSError,subprocess.SubprocessError) as exc:
        result['detail']='Impossible de lancer ou terminer ping.exe : '+str(exc)
        return result
    # TTL identifie une réponse IPv4 de la cible, contrairement à un message
    # « destination inaccessible » envoyé par le routeur. Les résumés sont exclus.
    replies=[line for line in out.splitlines() if re.search(r'\bTTL\s*=',line,re.I)]
    values=[]
    for line in replies:
        match=re.search(r'([=<])\s*(\d+(?:[.,]\d+)?)\s*ms\b',line,re.I)
        if not match:
            result['detail']='Réponse reçue, mais durée illisible : '+line.strip()
            return result
        # « <1 ms » est conservé comme borne supérieure, jamais comme 0 ms.
        values.append(float(match.group(2).replace(',','.')))
    if code not in (0,1) or len(values)>count:
        result['detail']='Échec de ping.exe : '+(err or out or str(code))[-1200:]
        return result
    if not values:
        result['status']='no_response'
        result['detail']=(
            'Aucune réponse ICMP exploitable : connexion, filtrage ICMP ou erreur locale à vérifier.'
            +'\n'+(err or out or 'Aucune sortie de ping.exe.')[-1200:])
        return result
    result.update(avg=statistics.mean(values),min=min(values),max=max(values),
                  jitter=statistics.mean(abs(b-a) for a,b in zip(values,values[1:])) if len(values)>1 else None,
                  loss=100*(count-len(values))/count,received=len(values),status='ok')
    return result


def format_benchmark(rows):
    lines=['BENCHMARK RÉSEAU — ICMP Windows',
           'Résolution : 1 ms ; les réponses <1 ms sont comptées à 1 ms.']
    for row in rows:
        if row.get('status')!='ok':
            lines.append(row['host']+' | mesure indisponible | '+row.get('detail','Erreur inconnue'))
            continue
        jitter=f"{row['jitter']:.1f} ms" if row['jitter'] is not None else 'indisponible (une seule réponse)'
        lines.append(f"{row['host']} | moyenne {row['avg']:.1f} ms | min {row['min']:.1f} | max {row['max']:.1f} | jitter {jitter} | sans réponse {row['loss']:.1f}% | réponses {row['received']}/{row['sent']}")
    return '\n'.join(lines)


def benchmark():
    try:gw=_gateway()
    except (OSError,subprocess.SubprocessError):gw=None
    targets=list(dict.fromkeys(([gw] if gw else [])+['1.1.1.1','8.8.8.8']))
    with ThreadPoolExecutor(max_workers=3) as pool:
        return list(pool.map(ping,targets))


FORTNITE_PING_ENDPOINTS={
    'Europe • France':'ping-fr.ds.on.epicgames.com',
    'Europe • Germany':'ping-de.ds.on.epicgames.com',
    'Europe • United Kingdom':'ping-gb.ds.on.epicgames.com',
    'NA • Virginia':'ping-va.ds.on.epicgames.com',
    'NA • Ohio':'ping-oh.ds.on.epicgames.com',
    'NA • Iowa':'ping-ia.ds.on.epicgames.com',
    'NA • Texas':'ping-tx.ds.on.epicgames.com',
    'NA • Oregon':'ping-or.ds.on.epicgames.com',
    'NA • Northern California':'ping-ncal.ds.on.epicgames.com',
    'NA • Mexico':'ping-mx.ds.on.epicgames.com',
    'Brazil • São Paulo':'ping-sao.ds.on.epicgames.com',
    'Asia • Tokyo':'ping-tok.ds.on.epicgames.com',
    'Oceania • Sydney':'ping-syd.ds.on.epicgames.com',
    'Middle East • Bahrain':'ping-bah.ds.on.epicgames.com',
    'Middle East • Mumbai':'ping-mum.ds.on.epicgames.com',
    'Middle East • Israel':'ping-il.ds.on.epicgames.com',
}

def fortnite_region_benchmark(samples=8):
    rows=[]
    for region,host in FORTNITE_PING_ENDPOINTS.items():
        try:
            row=ping(host,count=max(3,min(20,int(samples))))
            row['region']=region;rows.append(row)
        except Exception as exc:
            rows.append({'region':region,'host':host,'avg':9999.0,'min':9999.0,'max':9999.0,'jitter':9999.0,'loss':100.0,'received':0,'sent':samples,'error':str(exc)})
    valid=[x for x in rows if float(x.get('loss',100))<100 and float(x.get('avg',9999))<9999]
    valid.sort(key=lambda x:(float(x.get('avg',9999))+float(x.get('jitter',0))*1.5+float(x.get('loss',0))*8))
    return {'best':valid[0] if valid else None,'regions':rows,'note':'Mesure ICMP indicative. Fortnite utilise du trafic UDP et le serveur réel peut varier selon la sous-région et le matchmaking.'}

def _network_quality_score(rows):
    vals=[x for x in rows if x and float(x.get('loss',100))<100 and float(x.get('avg',0))>0]
    if not vals:return 1e9
    return statistics.mean(float(x['avg'])+1.5*float(x.get('jitter') or 0)+8.0*float(x.get('loss') or 0) for x in vals)

def autotune_network_low_latency(root):
    """Teste le profil RSC/Interrupt Moderation et le conserve uniquement si la mesure ne régresse pas."""
    backend=WindowsSettings()
    nic=_active_nic_info();name=str(nic.get('Name') or '')
    if not name:raise RuntimeError('Aucune carte réseau physique active détectée.')
    targets=[]
    try:
        gw=_gateway()
        if gw:targets.append(gw)
    except Exception:pass
    targets.extend(['1.1.1.1','ping-eu.ds.on.epicgames.com'])
    targets=list(dict.fromkeys(targets))
    before=[ping(x,count=12) for x in targets]
    baseline=_network_quality_score(before)
    result=optimize(root,['net_low_latency'],backend)
    time.sleep(1.0)
    after=[ping(x,count=12) for x in targets]
    tuned=_network_quality_score(after)
    keep=(tuned<=baseline*1.03)
    if not keep:
        restore_feature(root,'net_low_latency',backend)
    return {
        'kept':keep,'before':before,'after':after,
        'before_score':baseline,'after_score':tuned,
        'message':('Profil faible latence conservé.' if keep else 'Profil faible latence annulé automatiquement car la mesure a régressé.'),
        'details':result
    }


# Réglages directs limités et documentés ; aucune optimisation réseau forcée.
import contextlib
import platform
import threading
import uuid

REG_SETTINGS = {
    'game_auto': (r'Software\Microsoft\GameBar', 'AutoGameModeEnabled'),
    'game_allow': (r'Software\Microsoft\GameBar', 'AllowAutoGameMode'),
    'capture': (r'Software\Microsoft\Windows\CurrentVersion\GameDVR', 'AppCaptureEnabled'),
    'dvr': (r'System\GameConfigStore', 'GameDVR_Enabled'),
    'ads': (r'Software\Microsoft\Windows\CurrentVersion\AdvertisingInfo', 'Enabled'),
    'suggestions': (r'Software\Microsoft\Windows\CurrentVersion\ContentDeliveryManager', 'SystemPaneSuggestionsEnabled'),
    'suggestions_2': (r'Software\Microsoft\Windows\CurrentVersion\ContentDeliveryManager', 'SubscribedContent-338388Enabled'),
    'silent_installs': (r'Software\Microsoft\Windows\CurrentVersion\ContentDeliveryManager', 'SilentInstalledAppsEnabled'),
    'tailored': (r'Software\Microsoft\Windows\CurrentVersion\Privacy', 'TailoredExperiencesWithDiagnosticDataEnabled'),
    'wer_disabled': (r'Software\Microsoft\Windows\Windows Error Reporting', 'Disabled'),
    'location': (r'Software\Microsoft\Windows\CurrentVersion\CapabilityAccessManager\ConsentStore\location', 'Value'),
    'online_speech': (r'Software\Microsoft\Speech_OneCore\Settings\OnlineSpeechPrivacy', 'HasAccepted'),
    'storage_sense': (r'Software\Microsoft\Windows\CurrentVersion\StorageSense\Parameters\StoragePolicy', '01'),
    'background_apps': (r'Software\Microsoft\Windows\CurrentVersion\BackgroundAccessApplications', 'GlobalUserDisabled'),
    'widgets': (r'Software\Microsoft\Windows\CurrentVersion\Explorer\Advanced', 'TaskbarDa'),
    'mouse_speed': (r'Control Panel\Mouse', 'MouseSpeed'),
    'mouse_threshold1': (r'Control Panel\Mouse', 'MouseThreshold1'),
    'mouse_threshold2': (r'Control Panel\Mouse', 'MouseThreshold2'),
    'edge_boost': (r'Software\Policies\Microsoft\Edge', 'StartupBoostEnabled'),
    'edge_background': (r'Software\Policies\Microsoft\Edge', 'BackgroundModeEnabled'),
    'copilot': (r'Software\Policies\Microsoft\Windows\WindowsCopilot', 'TurnOffWindowsCopilot'),
    'classic_context': (r'Software\Classes\CLSID\{86ca1aa0-34aa-4e8b-a509-50c905bae2a2}\InprocServer32', ''),
    'hags': ('HKLM', r'SYSTEM\CurrentControlSet\Control\GraphicsDrivers', 'HwSchMode'),
    'fast_startup': ('HKLM', r'SYSTEM\CurrentControlSet\Control\Session Manager\Power', 'HiberbootEnabled'),
    'delivery_p2p': ('HKLM', r'SOFTWARE\Policies\Microsoft\Windows\DeliveryOptimization', 'DODownloadMode'),
    'telemetry_level': ('HKLM', r'SOFTWARE\Policies\Microsoft\Windows\DataCollection', 'AllowTelemetry'),
    'vbs': ('HKLM', r'SYSTEM\CurrentControlSet\Control\DeviceGuard', 'EnableVirtualizationBasedSecurity'),
    'hvci': ('HKLM', r'SYSTEM\CurrentControlSet\Control\DeviceGuard\Scenarios\HypervisorEnforcedCodeIntegrity', 'Enabled'),
    'explorer_sync_ads': (r'Software\Microsoft\Windows\CurrentVersion\Explorer\Advanced', 'ShowSyncProviderNotifications'),
    'explorer_launch_to': (r'Software\Microsoft\Windows\CurrentVersion\Explorer\Advanced', 'LaunchTo'),
}
RUN_KEY = r'Software\Microsoft\Windows\CurrentVersion\Run'
BALANCED = '381b4222-f694-41f0-9685-ff5bb260df2e'
USB_SUB = '2a737441-1930-4402-8d77-b2bebba308a3'
USB_SETTING = '48e6b7a6-50f5-4782-a5d4-53bb8f07e226'
JOURNAL_NAME = 'pc-optimizer-state-v2.json'
DESIRED_NAME = 'pc-desired-features.json'
_MUTATION_LOCK = threading.Lock()
OPTIONS = {
    'game': ('Mode Jeu Windows', 'Active les préférences gaming Windows du compte courant.'),
    'captures': ('Désactiver les captures Xbox Game Bar', 'Coupe la capture DVR en arrière-plan ; OBS n’est pas modifié.'),
    'balanced': ('Plan d’alimentation Équilibré', 'Base stable recommandée pour Ryzen X3D.'),
    'usb': ('Désactiver la suspension USB sur secteur', 'Dépannage de déconnexions uniquement.'),
    'ads': ('Désactiver l’identifiant publicitaire', 'Réglage de confidentialité du compte courant.'),
    'suggestions': ('Réduire les suggestions Windows', 'Réduit les contenus promotionnels et suggestions.'),
    'silent_installs': ('Bloquer les installations silencieuses suggérées', 'Empêche Content Delivery Manager d’installer des apps suggérées.'),
    'tailored': ('Désactiver les expériences personnalisées', 'Réduit l’utilisation des données de diagnostic pour personnaliser Windows.'),
    'error_reporting': ('Désactiver les rapports d’erreurs utilisateur', 'Désactive Windows Error Reporting pour le compte courant.'),
    'location': ('Désactiver la géolocalisation pour les apps', 'Refuse l’accès à la localisation pour le compte courant.'),
    'online_speech': ('Désactiver la reconnaissance vocale en ligne', 'Coupe le consentement à la reconnaissance vocale connectée.'),
    'storage_sense_off': ('Désactiver Storage Sense', 'Évite les nettoyages automatiques en arrière-plan.'),
    'background_apps': ('Limiter les apps Store en arrière-plan', 'Réduit l’activité d’applications Microsoft Store en arrière-plan.'),
    'widgets_off': ('Désactiver Widgets', 'Masque Widgets de la barre des tâches.'),
    'mouse_accel_off': ('Optimiser la souris pour le jeu', 'Désactive l’accélération Enhance Pointer Precision.'),
    'edge_background_off': ('Réduire Edge en arrière-plan', 'Désactive Startup Boost et le mode arrière-plan via stratégie utilisateur.'),
    'copilot_off': ('Désactiver Windows Copilot', 'Applique la stratégie utilisateur qui masque Copilot.'),
    'classic_context': ('Menu contextuel classique', 'Restaure le menu clic droit classique de Windows 11.'),
    'hags_on': ('Activer HAGS', 'Active la planification GPU accélérée par matériel ; benchmark recommandé.'),
    'fast_startup_off': ('Désactiver le démarrage rapide', 'Évite l’hibernation hybride au démarrage ; peut aider certains pilotes.'),
    'hibernation_off': ('Désactiver l’hibernation', 'Libère hiberfil.sys sur un PC fixe ; désactive aussi le démarrage rapide.'),
    'sysmain_off': ('Désactiver SysMain', 'Option avancée ; peut aider certains scénarios mais n’est pas appliquée intelligemment.'),
    'net_power': ('Optimiser l’alimentation de la carte réseau', 'Désactive l’extinction automatique de la carte active.'),
    'net_eee': ('Désactiver EEE / Green Ethernet', 'Désactive les économies d’énergie Ethernet quand le pilote expose ces options.'),
    'nagle_off': ('Tester sans Nagle sur l’interface active', 'Option avancée : peut réduire la latence de petits paquets, à comparer avant/après.'),
    'p2p_off': ('Désactiver le partage P2P des mises à jour', 'Force Delivery Optimization à ne pas utiliser le P2P.'),
    'amd_gpu': ('Optimisation GPU AMD', 'Applique les réglages Windows gaming sûrs + priorité GPU élevée pour Fortnite ; aucun overclocking.'),
    'telemetry_min': ('Réduire la télémétrie Windows', 'Force le niveau de données de diagnostic au minimum autorisé par l’édition de Windows.'),
    'vbs_off': ('Désactiver VBS / Memory Integrity', 'Option avancée : peut réduire une surcharge de virtualisation mais diminue la sécurité. Redémarrage requis.'),
    'explorer_tweaks': ('Optimiser l’Explorateur Windows', 'Réduit les notifications promotionnelles de l’Explorateur et ouvre directement Ce PC.'),
    'background_services': ('Réduire les services de télémétrie', 'Désactive uniquement les services de télémétrie Windows ciblés quand ils existent.'),
    'tcp_baseline': ('Réactivité réseau Windows', 'Réactive RSS et conserve l’auto-tuning TCP en mode normal pour une base saine.'),
    'net_low_latency': ('Profil carte réseau faible latence', 'Désactive RSC et la modération des interruptions uniquement si le pilote les expose ; RSS reste actif. À valider par mesure avant/après.'),
}
APP_CANDIDATES = {
    'Microsoft.BingNews': 'Actualités Microsoft',
    'Microsoft.BingWeather': 'Météo Microsoft',
    'Microsoft.MicrosoftSolitaireCollection': 'Microsoft Solitaire',
    'Microsoft.Getstarted': 'Conseils Windows',
    'Clipchamp.Clipchamp': 'Clipchamp',
}


def _strict_json(script):
    wrapped = "$ErrorActionPreference='Stop'; try { $result = & {\n" + script + "\n}; ConvertTo-Json -InputObject $result -Depth 8 -Compress } catch { [Console]::Error.WriteLine($_.Exception.Message); exit 1 }"
    out, err, code = _ps(wrapped)
    if code or not out:
        raise RuntimeError(err or out or 'Le diagnostic Windows ne renvoie aucune donnée.')
    try:
        return json.loads(out.lstrip('\ufeff'))
    except ValueError as exc:
        raise RuntimeError('Réponse Windows illisible : '+out[:250]) from exc


def _rows(data):
    return data if isinstance(data, list) else ([] if data is None else [data])


def _guid(value):
    return (ctypes.c_ubyte * 16).from_buffer_copy(uuid.UUID(value).bytes_le)


class WindowsSettings:
    def __init__(self):
        if os.name != 'nt':raise RuntimeError('Ces actions nécessitent Windows.')
        import winreg
        self.reg = winreg
        self.identity = {'machine': platform.node(), 'sid': _strict_json('[Security.Principal.WindowsIdentity]::GetCurrent().User.Value')}

    def read(self, spec):
        kind = spec['kind']
        if kind == 'registry':
            hive, key, name = self._registry_target(spec)
            try:
                with self.reg.OpenKey(hive, key) as handle:
                    value, regtype = self.reg.QueryValueEx(handle, name)
            except FileNotFoundError:
                return {'exists': False}
            if regtype not in (self.reg.REG_DWORD, self.reg.REG_SZ, self.reg.REG_EXPAND_SZ):
                raise ValueError('Type de registre non pris en charge : '+str(name))
            return {'exists': True, 'value': value, 'type': regtype}
        if kind == 'power':
            out, err, code = _run(['powercfg.exe', '/getactivescheme'])
            match = re.search(r'\b[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}\b', out)
            if code or not match:raise RuntimeError(err or 'Plan actif illisible.')
            return match.group().lower()
        if kind == 'usb':
            value = ctypes.c_ulong()
            code = ctypes.windll.powrprof.PowerReadACValueIndex(None, ctypes.byref(_guid(spec['plan'])), ctypes.byref(_guid(USB_SUB)), ctypes.byref(_guid(USB_SETTING)), ctypes.byref(value))
            if code:raise OSError(code, 'Lecture de la suspension USB impossible sur ce plan.')
            return value.value
        if kind == 'hibernate':
            try:
                with self.reg.OpenKey(self.reg.HKEY_LOCAL_MACHINE, r'SYSTEM\CurrentControlSet\Control\Power') as handle:
                    value,_=self.reg.QueryValueEx(handle,'HibernateEnabled')
                return bool(value)
            except FileNotFoundError:return False
        if kind == 'service':
            name=spec['name']
            script=f"$s=Get-CimInstance Win32_Service -Filter \"Name='{name.replace(chr(39),chr(39)*2)}'\" -ErrorAction Stop; [ordered]@{{StartMode=$s.StartMode;State=$s.State}}"
            return _strict_json(script)
        if kind == 'net_power':
            name=spec['name'].replace("'","''")
            return _strict_json(f"$p=Get-NetAdapterPowerManagement -Name '{name}' -ErrorAction Stop; [ordered]@{{AllowComputerToTurnOffDevice=[string]$p.AllowComputerToTurnOffDevice}}")
        if kind == 'net_eee':
            name=spec['name'].replace("'","''")
            return _strict_json(f"@($x=Get-NetAdapterAdvancedProperty -Name '{name}' -ErrorAction SilentlyContinue|Where-Object {{$_.DisplayName -match 'Energy.Efficient|Green Ethernet|Gigabit Lite|Power Saving|Économie.*énergie'}}; $x|Select-Object RegistryKeyword,DisplayValue,ValidDisplayValues)")
        if kind == 'tcp_baseline':
            name=spec['name'].replace("'","''")
            return _strict_json(f"$r=Get-NetAdapterRss -Name '{name}' -ErrorAction Stop; $t=Get-NetTCPSetting -SettingName Internet -ErrorAction Stop; [ordered]@{{rss=[bool]$r.Enabled;autotuning=[string]$t.AutoTuningLevelLocal}}")
        if kind == 'net_low_latency':
            name=spec['name'].replace("'","''")
            return _strict_json(f"""$rss=Get-NetAdapterRss -Name '{name}' -ErrorAction SilentlyContinue
$rsc=Get-NetAdapterRsc -Name '{name}' -ErrorAction SilentlyContinue
$props=@(Get-NetAdapterAdvancedProperty -Name '{name}' -ErrorAction SilentlyContinue |
 Where-Object {{$_.DisplayName -match 'Interrupt.*Moderation|Interrupt Moderation|Modération.*interrupt'}} |
 Sort-Object RegistryKeyword |
 Select-Object RegistryKeyword,DisplayName,DisplayValue,RegistryValue,ValidDisplayValues,ValidRegistryValues)
[ordered]@{{rss=if($rss){{[bool]$rss.Enabled}}else{{$null}};rsc_ipv4=if($rsc){{[bool]$rsc.IPv4Enabled}}else{{$null}};rsc_ipv6=if($rsc){{[bool]$rsc.IPv6Enabled}}else{{$null}};interrupt=$props}}""")
        raise ValueError('Type de réglage invalide.')

    def _registry_target(self, spec):
        if spec.get('id') in REG_SETTINGS:
            target=REG_SETTINGS[spec['id']]
            if len(target)==2:root,key,name='HKCU',target[0],target[1]
            else:root,key,name=target
            hive=self.reg.HKEY_LOCAL_MACHINE if root=='HKLM' else self.reg.HKEY_CURRENT_USER
            return hive,key,name
        if spec.get('id') == 'startup' and isinstance(spec.get('name'), str) and spec['name'] and '\x00' not in spec['name']:
            return self.reg.HKEY_CURRENT_USER,RUN_KEY,spec['name']
        if spec.get('id') in ('fortnite_gpu','game_gpu') and isinstance(spec.get('name'),str) and spec['name'] and '\x00' not in spec['name']:
            name=str(spec['name'])
            if not name.lower().endswith('.exe') or not Path(name).is_absolute():
                raise ValueError('Exécutable de jeu invalide.')
            return self.reg.HKEY_CURRENT_USER,r'Software\Microsoft\DirectX\UserGpuPreferences',name
        if spec.get('id') in ('nagle_tcp','nagle_ack'):
            path=str(spec.get('path') or '')
            prefix='SYSTEM\\CurrentControlSet\\Services\\Tcpip\\Parameters\\Interfaces\\'
            if not path.startswith(prefix):raise ValueError('Interface TCP non autorisée.')
            name='TCPNoDelay' if spec['id']=='nagle_tcp' else 'TcpAckFrequency'
            return self.reg.HKEY_LOCAL_MACHINE,path,name
        raise ValueError('Réglage de registre non autorisé.')

    def write(self, spec, value):
        kind = spec['kind']
        if kind == 'registry':
            hive, key, name = self._registry_target(spec)
            if value['exists']:
                with self.reg.CreateKeyEx(hive, key, 0, self.reg.KEY_SET_VALUE) as handle:
                    self.reg.SetValueEx(handle, name, 0, value['type'], value['value'])
            else:
                try:
                    with self.reg.OpenKey(hive, key, 0, self.reg.KEY_SET_VALUE) as handle:
                        self.reg.DeleteValue(handle, name)
                except FileNotFoundError:pass
        elif kind == 'power':
            value = str(uuid.UUID(value))
            out, err, code = _run(['powercfg.exe', '/setactive', value])
            if code:raise RuntimeError(err or out or 'Changement du plan refusé.')
        elif kind == 'usb':
            if value not in (0, 1):raise ValueError('Valeur USB invalide.')
            code = ctypes.windll.powrprof.PowerWriteACValueIndex(None, ctypes.byref(_guid(spec['plan'])), ctypes.byref(_guid(USB_SUB)), ctypes.byref(_guid(USB_SETTING)), value)
            if code:raise OSError(code, 'Modification USB refusée.')
            active = self.read({'kind': 'power'})
            if active == spec['plan']:
                out, err, code = _run(['powercfg.exe', '/setactive', active])
                if code:raise RuntimeError(err or out or 'Activation USB refusée.')
        elif kind == 'hibernate':
            out,err,code=_run(['powercfg.exe','/hibernate','on' if value else 'off'])
            if code:raise RuntimeError(err or out or 'Modification de l’hibernation refusée.')
        elif kind == 'service':
            name=spec['name'];start=str(value.get('StartMode') or 'Manual')
            sc_map={'Auto':'auto','Automatic':'auto','Manual':'demand','Disabled':'disabled'}
            out,err,code=_run(['sc.exe','config',name,'start=',sc_map.get(start,start.lower())])
            if code:raise RuntimeError(err or out or 'Configuration du service refusée.')
            desired=str(value.get('State') or 'Stopped').lower()
            if desired=='running':_run(['sc.exe','start',name],timeout=20)
            else:_run(['sc.exe','stop',name],timeout=20)
        elif kind == 'net_power':
            name=spec['name'].replace("'","''");state=str(value.get('AllowComputerToTurnOffDevice') or 'Disabled')
            script=f"Set-NetAdapterPowerManagement -Name '{name}' -AllowComputerToTurnOffDevice {state} -ErrorAction Stop"
            out,err,code=_ps(script)
            if code:raise RuntimeError(err or out or 'Modification alimentation réseau refusée.')
        elif kind == 'net_eee':
            name=spec['name'].replace("'","''")
            rows=value if isinstance(value,list) else ([] if value is None else [value])
            changed=0
            for row in rows:
                keyword=str(row.get('RegistryKeyword') or '').strip().replace("'","''")
                if not keyword:continue
                display_raw=row.get('DisplayValue')
                display='' if display_raw is None else str(display_raw).strip()
                registry_raw=row.get('RegistryValue')
                if display:
                    safe_display=display.replace("'","''")
                    script=f"Set-NetAdapterAdvancedProperty -Name '{name}' -RegistryKeyword '{keyword}' -DisplayValue '{safe_display}' -NoRestart -ErrorAction Stop"
                elif registry_raw not in (None,'',[]):
                    vals=registry_raw if isinstance(registry_raw,list) else [registry_raw]
                    vals=[str(x).strip() for x in vals if str(x).strip()!='']
                    if not vals:continue
                    psvals="@("+",".join("'"+x.replace("'","''")+"'" for x in vals)+")"
                    script=f"Set-NetAdapterAdvancedProperty -Name '{name}' -RegistryKeyword '{keyword}' -RegistryValue {psvals} -NoRestart -ErrorAction Stop"
                else:
                    # Anciennes sauvegardes pouvaient ne pas contenir RegistryValue.
                    # Ne jamais envoyer -DisplayValue '' au pilote.
                    continue
                out,err,code=_ps(script)
                if code:
                    raise RuntimeError("Le pilote réseau a refusé le réglage "+keyword+". Relance le scan : Acolyte resynchronisera l’état réel sans réessayer une valeur vide.")
                changed+=1
            if rows and changed==0:
                raise RuntimeError("Aucune valeur EEE/Green Ethernet restaurable n’est exposée par ce pilote.")
        elif kind == 'tcp_baseline':
            name=spec['name'].replace("'","''")
            if value.get('rss'):
                out,err,code=_ps(f"Enable-NetAdapterRss -Name '{name}' -ErrorAction Stop")
            else:
                out,err,code=_ps(f"Disable-NetAdapterRss -Name '{name}' -ErrorAction Stop")
            if code:raise RuntimeError(err or out or 'Modification RSS refusée.')
            level=str(value.get('autotuning') or 'Normal')
            out,err,code=_ps(f"Set-NetTCPSetting -SettingName Internet -AutoTuningLevelLocal {level} -ErrorAction Stop")
            if code:
                out,err,code=_run(['netsh.exe','int','tcp','set','global','autotuninglevel='+level.lower()])
                if code:raise RuntimeError(err or out or 'Modification Auto-Tuning refusée.')
        elif kind == 'net_low_latency':
            name=spec['name'].replace("'","''")
            rss=value.get('rss')
            if rss is not None:
                cmd='Enable-NetAdapterRss' if rss else 'Disable-NetAdapterRss'
                out,err,code=_ps(f"{cmd} -Name '{name}' -ErrorAction Stop")
                if code:raise RuntimeError(err or out or 'Modification RSS refusée.')
            v4=value.get('rsc_ipv4');v6=value.get('rsc_ipv6')
            if v4 is not None or v6 is not None:
                args=[]
                if v4 is not None:
                    args.append('-IPv4Enabled '+('$True' if bool(v4) else '$False'))
                if v6 is not None:
                    args.append('-IPv6Enabled '+('$True' if bool(v6) else '$False'))
                out,err,code=_ps(f"Set-NetAdapterRsc -Name '{name}' {' '.join(args)} -NoRestart -ErrorAction Stop")
                if code:raise RuntimeError(err or out or 'Modification RSC refusée.')
            for row in value.get('interrupt') or []:
                keyword=str(row.get('RegistryKeyword') or '').strip().replace("'","''")
                display=str(row.get('DisplayValue') or '').strip()
                registry=row.get('RegistryValue')
                if not keyword:continue
                if display:
                    safe_display=display.replace("'","''")
                    out,err,code=_ps(f"Set-NetAdapterAdvancedProperty -Name '{name}' -RegistryKeyword '{keyword}' -DisplayValue '{safe_display}' -NoRestart -ErrorAction Stop")
                elif registry not in (None,'',[]):
                    vals=registry if isinstance(registry,list) else [registry]
                    vals=[str(x) for x in vals]
                    pv="@("+",".join("'"+x.replace("'","''")+"'" for x in vals)+")"
                    out,err,code=_ps(f"Set-NetAdapterAdvancedProperty -Name '{name}' -RegistryKeyword '{keyword}' -RegistryValue {pv} -NoRestart -ErrorAction Stop")
                else:
                    continue
                if code:raise RuntimeError(err or out or 'Modification de la modération des interruptions refusée.')
        else:raise ValueError('Type de réglage invalide.')
        if kind not in ('service','net_eee','net_low_latency') and self.read(spec) != value:
            raise RuntimeError('La vérification du réglage a échoué.')


def _save_state(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True,exist_ok=True)
    fd, temp = tempfile.mkstemp(prefix='pc-state-', suffix='.tmp', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.flush();os.fsync(f.fileno())
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):os.unlink(temp)


def desired_features(root=None):
    path=optimizer_desired_path(root)
    if not path.exists():
        # Migration best-effort de toutes les anciennes installations Acolyte.
        merged={}
        candidates=[]
        if root is not None:
            candidates.extend([Path(root)/DESIRED_NAME,optimizer_data_dir(root)/DESIRED_NAME])
        try:candidates.extend(optimizer_base_dir().glob('*/'+DESIRED_NAME))
        except OSError:pass
        for old in candidates:
            try:
                data=json.loads(Path(old).read_text(encoding='utf-8'))
                for k,v in data.items():
                    if k in OPTIONS and bool(v):merged[k]=True
            except (OSError,ValueError,TypeError):pass
        if merged:
            _save_state(path,merged)
    try:
        data=json.loads(path.read_text(encoding='utf-8'))
        return {k:bool(v) for k,v in data.items() if k in OPTIONS}
    except (FileNotFoundError,ValueError,OSError):return {}

def set_feature_desired(root, option, enabled):
    if option not in OPTIONS:raise ValueError('Fonctionnalité inconnue.')
    data=desired_features(root)
    if enabled:data[option]=True
    else:data.pop(option,None)
    path=optimizer_desired_path(root)
    temp=path.with_suffix('.tmp')
    temp.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')
    os.replace(temp,path)
    return data

def desired_drift(root, states):
    wanted=desired_features(root)
    return [key for key in wanted if states.get(key) is False]

def reapply_persistent_features(root, options=None):
    options=list(options if options is not None else desired_features(root).keys())
    applied=[];failed=[]
    for option in options:
        if option not in OPTIONS:continue
        try:
            optimize(root,[option])
            applied.append(option)
        except Exception as exc:
            # Un pilote peut ne plus exposer le même réglage après mise à jour/redémarrage.
            # On coupe la persistance de CE réglage seulement pour éviter une boucle d'erreur.
            set_feature_desired(root,option,False)
            message=str(exc).replace('\r',' ').replace('\n',' ')
            if len(message)>260:message=message[:257]+'...'
            failed.append({'option':option,'name':OPTIONS[option][0],'error':message})
    return {'applied':applied,'failed':failed}

def _load_state(root, backend):
    path=_migrate_optimizer_file(root,JOURNAL_NAME)
    if not path.exists():
        return {'schema':2,'identity':backend.identity,'entries':[],'quarantined':[]}
    try:
        state=json.loads(path.read_text(encoding='utf-8'))
    except (OSError,ValueError,json.JSONDecodeError) as exc:
        # Journal illisible/corrompu : on le préserve et repart proprement.
        try:
            broken=path.with_name('pc-optimizer-broken-'+str(int(time.time()))+'.json')
            os.replace(path,broken)
        except OSError:pass
        return {'schema':2,'identity':backend.identity,'entries':[],'quarantined':[{'reason':'journal illisible','error':str(exc)}]}
    if state.get('schema')!=2 or state.get('identity')!=backend.identity or not isinstance(state.get('entries'),list):
        try:
            incompatible=path.with_name('pc-optimizer-incompatible-'+str(int(time.time()))+'.json')
            os.replace(path,incompatible)
        except OSError:pass
        return {'schema':2,'identity':backend.identity,'entries':[],'quarantined':[{'reason':'journal incompatible'}]}
    state.setdefault('quarantined',[])
    return state

def _reconcile_pending_state(root,state,backend):
    """Répare automatiquement un journal laissé pending par un crash/arrêt forcé."""
    path=optimizer_state_path(root)
    changed=False
    kept=[]
    for entry in state.get('entries',[]):
        if not entry.get('pending'):
            kept.append(entry);continue
        spec=entry.get('spec')
        target=entry.get('target')
        try:
            current=backend.read(spec)
            # Si l’écriture avait réellement abouti, on finalise simplement.
            # Sinon on prend l’état Windows actuel comme nouvelle référence appliquée.
            entry['applied']=current
            entry['pending']=False
            entry.pop('target',None)
            entry['recovered_at']=time.strftime('%Y-%m-%d %H:%M:%S')
            entry['recovery']='target_applied' if current==target else 'actual_state_resynced'
            kept.append(entry)
        except Exception as exc:
            # Un ancien spec/pilote peut ne plus exister. On ne bloque pas Acolyte :
            # l’entrée est conservée à part pour audit mais n’est plus réappliquée.
            q=dict(entry)
            q['pending']=False
            q['quarantined_at']=time.strftime('%Y-%m-%d %H:%M:%S')
            q['reason']='Impossible de relire ce réglage après interruption'
            q['error']=str(exc)[:500]
            state.setdefault('quarantined',[]).append(q)
        changed=True
    state['entries']=kept
    if changed:_save_state(path,state)
    return changed

@contextlib.contextmanager
def _exclusive(root):
    # Verrou de processus + verrou de fichier, libérés automatiquement après un crash.
    if not _MUTATION_LOCK.acquire(blocking=False):raise RuntimeError('Une modification est déjà en cours.')
    try:
        with open(optimizer_lock_path(root), 'a+b') as handle:
            handle.seek(0, 2)
            if handle.tell() == 0:handle.write(b'0');handle.flush()
            handle.seek(0)
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            try:yield
            finally:
                handle.seek(0)
                if os.name == 'nt':msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    finally:_MUTATION_LOCK.release()


def _apply_changes(root, changes, backend):
    path=optimizer_state_path(root)
    with _exclusive(root):
        state=_load_state(root,backend)
        _reconcile_pending_state(root,state,backend)
        # Lire tous les réglages avant toute modification.
        before = [(spec, value, backend.read(spec)) for spec, value in changes]
        touched = []
        current_spec=None
        try:
            for spec, value, previous in before:
                current_spec=spec
                if previous == value:continue
                entry = next((e for e in state['entries'] if e['spec'] == spec), None)
                if entry is not None and previous != entry.get('applied'):
                    # Windows, un pilote ou l’utilisateur a modifié la valeur depuis la dernière
                    # exécution. On se resynchronise sur la réalité au lieu de bloquer Acolyte.
                    # La nouvelle valeur devient le nouvel état de référence pour Restaurer.
                    entry.update(original=previous,applied=previous,pending=False)
                    entry.pop('target',None)
                    _save_state(path,state)
                if entry is None:
                    entry = {'spec': spec, 'original': previous, 'applied': previous}
                    state['entries'].append(entry)
                entry['pending'] = True
                entry['target'] = value
                _save_state(path, state)  # Journal durable AVANT l’écriture Windows.
                touched.append((entry, previous))
                backend.write(spec, value)
                entry.update(applied=value, pending=False)
                _save_state(path, state)
        except Exception as exc:
            failures = []
            for entry, previous in reversed(touched):
                try:
                    backend.write(entry['spec'], previous)
                    entry.update(applied=previous, pending=False)
                except Exception as rollback:failures.append(str(rollback))
            _save_state(path, state)
            setting=''
            if current_spec:
                ident=current_spec.get('id') or current_spec.get('name') or current_spec.get('kind') or 'réglage inconnu'
                setting=' Réglage en échec : '+str(ident)+'.'
            if failures:raise RuntimeError('Échec ; restauration partielle. Sauvegarde conservée.'+setting+' '+str(exc)+' / '+'; '.join(failures)) from exc
            raise RuntimeError('Échec ; changements de cette opération annulés.'+setting+' '+str(exc)) from exc
        return {'changed': len(touched), 'backup': str(path)}



def _active_nic_info():
    data=_strict_json("""$n=Get-NetAdapter -Physical -ErrorAction SilentlyContinue|Where-Object Status -eq 'Up'|Sort-Object LinkSpeed -Descending|Select-Object -First 1 Name,InterfaceGuid,InterfaceDescription
if($null -eq $n){$null}else{$n}""")
    return data if isinstance(data,dict) else {}

def _fortnite_executable():
    for game in detected_games():
        if str(game.get('name','')).lower()!='fortnite':continue
        base=Path(str(game.get('path') or ''))
        candidates=[
            base/'FortniteGame'/'Binaries'/'Win64'/'FortniteClient-Win64-Shipping.exe',
            base/'FortniteClient-Win64-Shipping.exe'
        ]
        for path in candidates:
            if path.exists():return str(path.resolve())
    return None

def _feature_changes(option, backend):
    changes=[]
    def dword(key,value):changes.append(({'kind':'registry','id':key},{'exists':True,'type':4,'value':int(value)}))
    def string(key,value):changes.append(({'kind':'registry','id':key},{'exists':True,'type':1,'value':str(value)}))
    if option=='game':
        dword('game_auto',1);dword('game_allow',1)
    elif option=='captures':
        dword('capture',0);dword('dvr',0)
    elif option=='balanced':
        changes.append(({'kind':'power'},BALANCED))
    elif option=='usb':
        plan=backend.read({'kind':'power'})
        changes.append(({'kind':'usb','plan':plan},0))
    elif option=='ads':
        dword('ads',0)
    elif option=='suggestions':
        dword('suggestions',0);dword('suggestions_2',0)
    elif option=='silent_installs':
        dword('silent_installs',0)
    elif option=='tailored':
        dword('tailored',0)
    elif option=='error_reporting':
        dword('wer_disabled',1)
    elif option=='location':
        string('location','Deny')
    elif option=='online_speech':
        dword('online_speech',0)
    elif option=='storage_sense_off':
        dword('storage_sense',0)
    elif option=='background_apps':
        dword('background_apps',1)
    elif option=='widgets_off':
        dword('widgets',0)
    elif option=='mouse_accel_off':
        string('mouse_speed','0');string('mouse_threshold1','0');string('mouse_threshold2','0')
    elif option=='edge_background_off':
        dword('edge_boost',0);dword('edge_background',0)
    elif option=='copilot_off':
        dword('copilot',1)
    elif option=='classic_context':
        string('classic_context','')
    elif option=='hags_on':
        dword('hags',2)
    elif option=='fast_startup_off':
        dword('fast_startup',0)
    elif option=='hibernation_off':
        changes.append(({'kind':'hibernate'},False))
    elif option=='sysmain_off':
        changes.append(({'kind':'service','name':'SysMain'},{'StartMode':'Disabled','State':'Stopped'}))
    elif option in ('net_power','net_eee','nagle_off'):
        nic=_active_nic_info()
        name=str(nic.get('Name') or '')
        if not name:raise RuntimeError('Aucune carte réseau physique active détectée.')
        if option=='net_power':
            changes.append(({'kind':'net_power','name':name},{'AllowComputerToTurnOffDevice':'Disabled'}))
        elif option=='net_eee':
            spec={'kind':'net_eee','name':name}
            current=backend.read(spec)
            rows=current if isinstance(current,list) else ([] if current is None else [current])
            target=[]
            for row in rows:
                keyword=str(row.get('RegistryKeyword') or '').strip()
                values=row.get('ValidDisplayValues') or []
                if isinstance(values,str):values=[values]
                chosen=next((str(v).strip() for v in values if str(v).strip().lower() in ('disabled','désactivé','off')),None)
                if not keyword or not chosen:continue
                target.append({
                    'RegistryKeyword':keyword,
                    'DisplayName':row.get('DisplayName'),
                    'DisplayValue':chosen,
                    'RegistryValue':row.get('RegistryValue'),
                    'ValidDisplayValues':values,
                    'ValidRegistryValues':row.get('ValidRegistryValues')
                })
            if not target:raise RuntimeError('Le pilote réseau n’expose aucune option EEE/Green Ethernet désactivable automatiquement.')
            changes.append((spec,target))
        else:
            guid=str(nic.get('InterfaceGuid') or '').strip('{} ')
            if not guid:raise RuntimeError('GUID de l’interface réseau introuvable.')
            path=r'SYSTEM\CurrentControlSet\Services\Tcpip\Parameters\Interfaces\{'+guid+'}'
            changes.append(({'kind':'registry','id':'nagle_tcp','path':path},{'exists':True,'type':4,'value':1}))
            changes.append(({'kind':'registry','id':'nagle_ack','path':path},{'exists':True,'type':4,'value':1}))
    elif option=='p2p_off':
        dword('delivery_p2p',0)
    elif option=='amd_gpu':
        # Profil automatique volontairement limité à des réglages Windows réversibles :
        # pas de clé Adrenalin privée, pas d'OC/UV caché.
        for sub in ('game','captures','balanced','hags_on'):
            changes.extend(_feature_changes(sub,backend))
        exe=_fortnite_executable()
        if exe:
            changes.append(({'kind':'registry','id':'fortnite_gpu','name':exe},{'exists':True,'type':1,'value':'GpuPreference=2;'}))
    elif option=='telemetry_min':
        dword('telemetry_level',1)
    elif option=='vbs_off':
        dword('vbs',0);dword('hvci',0)
    elif option=='explorer_tweaks':
        dword('explorer_sync_ads',0);dword('explorer_launch_to',1)
    elif option=='background_services':
        for service in ('DiagTrack','dmwappushservice'):
            out,err,code=_run(['sc.exe','query',service])
            if code==0:
                changes.append(({'kind':'service','name':service},{'StartMode':'Disabled','State':'Stopped'}))
        if not changes:raise RuntimeError('Aucun service de télémétrie ciblé n’est présent sur ce Windows.')
    elif option=='tcp_baseline':
        nic=_active_nic_info();name=str(nic.get('Name') or '')
        if not name:raise RuntimeError('Aucune carte réseau physique active détectée.')
        changes.append(({'kind':'tcp_baseline','name':name},{'rss':True,'autotuning':'Normal'}))
    elif option=='net_low_latency':
        nic=_active_nic_info();name=str(nic.get('Name') or '')
        if not name:raise RuntimeError('Aucune carte réseau physique active détectée.')
        spec={'kind':'net_low_latency','name':name}
        current=backend.read(spec)
        target={
            'rss':True,
            'rsc_ipv4':False if current.get('rsc_ipv4') is not None else None,
            'rsc_ipv6':False if current.get('rsc_ipv6') is not None else None,
            'interrupt':[]
        }
        for row in current.get('interrupt') or []:
            values=row.get('ValidDisplayValues') or []
            if isinstance(values,str):values=[values]
            chosen=next((str(v).strip() for v in values if str(v).strip().casefold() in ('disabled','désactivé','off')),None)
            if not chosen:continue
            target['interrupt'].append({
                'RegistryKeyword':row.get('RegistryKeyword'),
                'DisplayName':row.get('DisplayName'),
                'DisplayValue':chosen,
                'RegistryValue':row.get('RegistryValue'),
                'ValidDisplayValues':values,
                'ValidRegistryValues':row.get('ValidRegistryValues')
            })
        if current.get('rsc_ipv4') is None and current.get('rsc_ipv6') is None and not target['interrupt']:
            raise RuntimeError('Ce pilote ne permet pas à Acolyte d’appliquer un profil faible latence mesurable.')
        changes.append((spec,target))
    else:
        raise ValueError('Option inconnue : '+str(option))
    # Déduplique les mêmes specs quand un profil composite les ajoute.
    unique=[]
    seen=set()
    for spec,value in changes:
        key=json.dumps(spec,sort_keys=True,ensure_ascii=False)
        if key in seen:continue
        seen.add(key);unique.append((spec,value))
    return unique

ADMIN_OPTIONS={
    'balanced','usb','hags_on','fast_startup_off','hibernation_off','sysmain_off',
    'net_power','net_eee','nagle_off','p2p_off','amd_gpu','telemetry_min',
    'vbs_off','background_services','tcp_baseline','net_low_latency'
}

def _spec_requires_admin(spec, backend=None):
    kind=str(spec.get('kind') or '')
    if kind in ('power','usb','hibernate','service','net_power','net_eee','tcp_baseline','net_low_latency'):
        return True
    if kind=='registry':
        ident=spec.get('id')
        if ident in ('nagle_tcp','nagle_ack'):
            return True
        target=REG_SETTINGS.get(ident)
        if target and len(target)==3 and target[0]=='HKLM':
            return True
    return False

def option_requires_admin(option, backend=None):
    option=str(option)
    if option not in OPTIONS:return False
    backend=backend or WindowsSettings()
    try:
        return any(_spec_requires_admin(spec,backend) for spec,_ in _feature_changes(option,backend))
    except Exception:
        # Fallback conservateur : mieux vaut demander l'UAC que laisser Windows refuser.
        return option in ADMIN_OPTIONS

def options_require_admin(options, backend=None):
    backend=backend or WindowsSettings()
    return any(option_requires_admin(x,backend) for x in (options or []))

def _is_access_denied_error(exc):
    msg=str(exc).casefold()
    return (
        isinstance(exc,PermissionError) or
        'winerror 5' in msg or
        'accès refusé' in msg or
        'access denied' in msg or
        'permission denied' in msg or
        'erreur 5' in msg
    )

def _write_result_json(path,data):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    tmp=path.with_suffix(path.suffix+'.tmp')
    try:
        tmp.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')
        os.replace(tmp,path)
    finally:
        try:
            if tmp.exists():tmp.unlink()
        except OSError:pass

def _apply_batch_local(root,enable,disable):
    results=[]
    if enable:
        results.append(apply_selected(enable,root))
    for key in disable:
        results.append(restore_feature(root,key))
    return '\n'.join(str(x) for x in results if x)

def _elevated_batch_main(request_path):
    request_path=Path(request_path).resolve()
    result_path=request_path.with_suffix('.result.json')
    try:
        if not is_admin():
            raise PermissionError('Le processus élevé ne possède pas les droits administrateur.')
        req=json.loads(request_path.read_text(encoding='utf-8'))
        root=Path(req['root']).resolve()
        enable=[x for x in req.get('enable',[]) if x in OPTIONS]
        disable=[x for x in req.get('disable',[]) if x in OPTIONS]
        message=_apply_batch_local(root,enable,disable)
        _write_result_json(result_path,{'ok':True,'message':message})
        return 0
    except Exception as exc:
        _write_result_json(result_path,{'ok':False,'error':str(exc)})
        return 1

def _apply_batch_elevated(root,enable,disable,timeout=240):
    folder=optimizer_data_dir(root)/'Elevation'
    folder.mkdir(parents=True,exist_ok=True)
    token=uuid.uuid4().hex
    request_path=folder/(token+'.request.json')
    result_path=request_path.with_suffix('.result.json')
    request={'root':str(Path(root).resolve()),'enable':list(enable),'disable':list(disable)}
    _write_result_json(request_path,request)
    params=subprocess.list2cmdline([str(Path(__file__).resolve()),'--elevated-batch',str(request_path)])
    code=ctypes.windll.shell32.ShellExecuteW(
        None,'runas',str(Path(sys.executable).resolve()),params,str(Path(root).resolve()),0)
    if code<=32:
        try:request_path.unlink()
        except OSError:pass
        if code==5:
            raise PermissionError('Autorisation administrateur refusée. Accepte la fenêtre Windows UAC pour appliquer ces réglages.')
        raise OSError(int(code),'Impossible de lancer l’opération en administrateur.')
    deadline=time.monotonic()+timeout
    while time.monotonic()<deadline:
        if result_path.exists():
            try:
                data=json.loads(result_path.read_text(encoding='utf-8'))
            finally:
                try:request_path.unlink()
                except OSError:pass
                try:result_path.unlink()
                except OSError:pass
            if data.get('ok'):return str(data.get('message') or 'Changements appliqués.')
            raise RuntimeError(str(data.get('error') or 'L’opération administrateur a échoué.'))
        time.sleep(0.2)
    raise TimeoutError('La demande administrateur n’a pas terminé. Vérifie si une fenêtre UAC attend une réponse.')

def apply_batch(root,enable=None,disable=None):
    """Applique un lot avec une seule élévation UAC quand Windows l’exige.
    Si Windows refuse malgré le précontrôle, Acolyte réessaie automatiquement en administrateur.
    """
    enable=[x for x in (enable or []) if x in OPTIONS]
    disable=[x for x in (disable or []) if x in OPTIONS]
    all_options=list(dict.fromkeys(enable+disable))
    if not all_options:return 'Aucun changement demandé.'
    if is_admin():
        return _apply_batch_local(root,enable,disable)

    backend=WindowsSettings()
    if options_require_admin(all_options,backend):
        return _apply_batch_elevated(root,enable,disable)

    try:
        return _apply_batch_local(root,enable,disable)
    except Exception as exc:
        if _is_access_denied_error(exc):
            # Certains builds/politiques Windows protègent aussi des réglages normalement utilisateur.
            # Le premier essai a été rollbacké par _apply_changes : on peut réessayer proprement élevé.
            return _apply_batch_elevated(root,enable,disable)
        raise

def option_states(backend=None):
    backend=backend or WindowsSettings()
    states={}
    for option in OPTIONS:
        try:
            changes=_feature_changes(option,backend)
            if not changes:
                states[option]=None;continue
            ok=True
            for spec,target in changes:
                current=backend.read(spec)
                if spec.get('kind')=='net_eee':
                    cur_rows=current if isinstance(current,list) else ([] if current is None else [current])
                    wanted={str(x.get('RegistryKeyword')):str(x.get('DisplayValue')) for x in target}
                    actual={str(x.get('RegistryKeyword')):str(x.get('DisplayValue')) for x in cur_rows}
                    if any(actual.get(k)!=v for k,v in wanted.items()):ok=False;break
                elif current!=target:
                    ok=False;break
            states[option]=ok
        except Exception as exc:
            states[option]=None
    return states

def optimize(root, options=None, backend=None):
    options=list(options if options is not None else ['game'])
    if not options or not set(options).issubset(OPTIONS):
        raise ValueError('Sélectionne au moins un réglage valide.')
    backend=backend or WindowsSettings()
    changes=[]
    for option in options:
        changes.extend(_feature_changes(option,backend))
    # Déduplique les specs, la dernière cible identique gagne.
    merged={}
    order=[]
    for spec,value in changes:
        key=json.dumps(spec,sort_keys=True,ensure_ascii=False)
        if key not in merged:order.append(key)
        merged[key]=(spec,value)
    changes=[merged[k] for k in order]
    result=_apply_changes(root,changes,backend)
    for option in options:
        set_feature_desired(root,option,True)
    if not result['changed']:
        return 'Les réglages sélectionnés ont déjà les valeurs demandées. Leur état est marqué comme persistant.'
    return (
        f"{result['changed']} réglage(s) modifié(s) et vérifié(s).\n"
        f"Sauvegarde initiale conservée : {result['backup']}\n"
        "Les réglages sont relus depuis Windows et réappliqués par Acolyte s’ils dérivent après un redémarrage.\n"
        "Aucun gain de FPS n’est garanti : compare avec le benchmark avant/après."
    )


def restore_feature(root, option, backend=None):
    """Restaure uniquement les réglages que cette fonctionnalité a modifiés.
    Si la fonctionnalité était déjà active avant Acolyte, aucune origine fiable n'existe :
    on ne devine pas une valeur de désactivation.
    """
    if option not in OPTIONS:raise ValueError('Fonctionnalité inconnue.')
    backend=backend or WindowsSettings()
    specs=[spec for spec,_ in _feature_changes(option,backend)]
    path=optimizer_state_path(root)
    with _exclusive(root):
        state=_load_state(root,backend)
        matches=[e for e in state['entries'] if any(e.get('spec')==spec for spec in specs)]
        if not matches:
            return "Cette fonctionnalité n’a pas de sauvegarde Acolyte à restaurer. Elle était probablement déjà configurée ainsi avant Acolyte ; aucun état antérieur n’est inventé."
        errors=[]
        restored=0
        for entry in reversed(matches):
            try:
                backend.write(entry['spec'],entry['original'])
                state['entries'].remove(entry);restored+=1
                _save_state(path,state)
            except Exception as exc:errors.append(str(exc))
        if errors:raise RuntimeError('Restauration partielle de la fonctionnalité : '+'; '.join(errors))
        if not state['entries'] and path.exists():
            archive=optimizer_data_dir(root)/('pc-optimizer-restored-'+uuid.uuid4().hex+'.json')
            os.replace(path,archive)
        set_feature_desired(root,option,False)
        return f'{restored} réglage(s) restauré(s) pour {OPTIONS[option][0]}.'

def restore(root, backend=None):
    backend = backend or WindowsSettings()
    with _exclusive(root):
        state = _load_state(root, backend)
        if not state['entries']:return 'Aucun réglage à restaurer.'
        errors = []
        for entry in reversed(state['entries'][:]):
            try:
                backend.write(entry['spec'], entry['original'])
                state['entries'].remove(entry)
                _save_state(optimizer_state_path(root),state)
            except Exception as exc:errors.append(str(exc))
        if errors:raise RuntimeError('Restauration partielle ; sauvegarde conservée pour réessayer : '+'; '.join(errors))
        archive=optimizer_data_dir(root)/('pc-optimizer-restored-'+uuid.uuid4().hex+'.json')
        os.replace(optimizer_state_path(root),archive)
        desired=optimizer_desired_path(root)
        if desired.exists():
            try:desired.unlink()
            except OSError:pass
        return 'Tous les réglages suivis par cette version ont été restaurés et vérifiés.\nLa persistance automatique des optimisations a également été effacée.\nLes désinstallations et les changements manuels dans Windows/AMD/BIOS ne sont pas inclus.'


def startup_items():
    backend = WindowsSettings();reg = backend.reg;items = []
    try:
        with reg.OpenKey(reg.HKEY_CURRENT_USER, RUN_KEY) as key:
            for i in range(reg.QueryInfoKey(key)[1]):
                name, value, kind = reg.EnumValue(key, i)
                if kind in (reg.REG_SZ, reg.REG_EXPAND_SZ):items.append({'name': name, 'command': value})
    except FileNotFoundError:pass
    return items


def disable_startup(root, name):
    backend = WindowsSettings();spec = {'kind': 'registry', 'id': 'startup', 'name': name}
    if not backend.read(spec)['exists']:raise RuntimeError('Cette entrée de démarrage n’existe plus.')
    result = _apply_changes(root, [(spec, {'exists': False})], backend)
    return f"Démarrage automatique retiré pour {name}. Le programme reste installé.\nRestaurer réactive les entrées suivies. {result['changed']} changement."


def removable_apps():
    data = _strict_json("@(Get-AppxPackage | Where-Object { -not $_.NonRemovable -and -not $_.IsFramework } | Select-Object Name,PackageFullName)")
    return [{'name': APP_CANDIDATES[r['Name']], 'package': r['Name']} for r in _rows(data) if r['Name'] in APP_CANDIDATES]


def remove_app(root, package):
    if package not in APP_CANDIDATES:raise ValueError('Application non autorisée.')
    # Identifiant strictement issu de la liste fermée, sans saisie PowerShell libre.
    with _exclusive(root):
        out, err, code = _ps("$ErrorActionPreference='Stop'; Get-AppxPackage -Name '"+package+"' | Remove-AppxPackage -ErrorAction Stop", 120)
        if code:raise RuntimeError(err or out or 'Désinstallation refusée.')
        if any(r['package'] == package for r in removable_apps()):raise RuntimeError('Application encore présente après la désinstallation.')
    return APP_CANDIDATES[package]+' désinstallé pour le compte courant.\nRéinstallation via Microsoft Store ; les données locales ne sont pas sauvegardées par Acolyte.'


def clear_windows_cache():
    """Nettoyage prudent du cache Windows.
    Ne purge pas la standby list et ne supprime pas le cache shaders DirectX,
    car cela peut dégrader temporairement les performances en jeu.
    """
    if game_process_running().get('running'):
        raise RuntimeError('Ferme Fortnite avant de vider les caches Windows afin d’éviter de supprimer des fichiers utilisés par le jeu.')
    before=checkup()
    temp_result=cleanup_temp(1)

    dns_ok=False;dns_detail=''
    out,err,code=_run(['ipconfig.exe','/flushdns'],timeout=20)
    dns_ok=(code==0)
    dns_detail=(out or err or '').strip()

    delivery='Non disponible'
    try:
        out,err,code=_ps("""$cmd=Get-Command Delete-DeliveryOptimizationCache -ErrorAction SilentlyContinue
if($null -eq $cmd){'INDISPONIBLE'}else{
 try{Delete-DeliveryOptimizationCache -Force -ErrorAction Stop;'OK'}catch{'ERREUR: '+$_.Exception.Message}
}""",timeout=60)
        delivery=(out or err or 'Non disponible').strip()
    except Exception as exc:
        delivery='Erreur: '+str(exc)

    after=checkup()
    freed=max(0.0,float(before.get('temp_size_mb') or 0)-float(after.get('temp_size_mb') or 0))
    return {
        'temp_cleanup':temp_result,
        'freed_temp_mb':round(freed,1),
        'dns_flushed':dns_ok,
        'dns_detail':dns_detail,
        'delivery_optimization_cache':delivery,
        'shader_cache':'Conservé volontairement pour éviter une recompilation des shaders et des stutters au prochain lancement.',
        'standby_memory':'Non purgée : vider la mémoire standby n’améliore pas durablement les FPS et peut forcer Windows à recharger des données.',
        'note':'Nettoyage prudent terminé. Les caches qui peuvent dégrader le premier lancement d’un jeu ont été conservés.'
    }


def _delete_tree_files(root):
    root=Path(root)
    removed=0;freed=0
    if not root.exists():return removed,freed
    for path in sorted(root.rglob('*'),key=lambda p:len(p.parts),reverse=True):
        try:
            if path.is_file() and not path.is_symlink():
                size=path.stat().st_size;path.unlink();removed+=1;freed+=size
            elif path.is_dir():
                try:path.rmdir()
                except OSError:pass
        except (OSError,PermissionError):pass
    return removed,freed

def reset_gpu_shader_cache():
    if game_process_running().get('running'):
        raise RuntimeError('Ferme Fortnite avant de réinitialiser les caches shaders.')
    local=Path(os.environ.get('LOCALAPPDATA',Path.home()/'AppData'/'Local'))
    targets=[
        local/'D3DSCache',
        local/'AMD'/'DxCache',
        local/'AMD'/'GLCache',
        local/'AMD'/'VkCache',
        local/'NVIDIA'/'DXCache',
        local/'NVIDIA'/'GLCache',
    ]
    total_count=0;total_bytes=0;used=[]
    for target in targets:
        count,size=_delete_tree_files(target)
        if count:
            total_count+=count;total_bytes+=size;used.append(str(target))
    return {
        'files_removed':total_count,
        'freed_mb':round(total_bytes/1024**2,1),
        'locations':used,
        'warning':'Le prochain lancement d’un jeu peut présenter des stutters temporaires pendant la recompilation des shaders.'
    }

def refresh_network_cache():
    commands=[
        (['ipconfig.exe','/flushdns'],'DNS'),
        (['arp.exe','-d','*'],'ARP'),
        (['nbtstat.exe','-R'],'NetBIOS'),
    ]
    result={}
    for args,name in commands:
        try:
            out,err,code=_run(args,timeout=30)
            result[name]={'ok':code==0,'detail':(out or err)[-1200:]}
        except Exception as exc:
            result[name]={'ok':False,'detail':str(exc)}
    return result

def optimize_disks():
    if not is_admin():raise PermissionError('Relance Acolyte en administrateur pour optimiser les disques.')
    out,err,code=_run(['defrag.exe','/C','/O','/U','/V'],timeout=1800)
    if code not in (0,1):raise RuntimeError(err or out or 'Optimisation des disques échouée.')
    return (out or err or 'Optimisation des disques terminée.')[-12000:]

def repair_system_files():
    if not is_admin():raise PermissionError('Relance Acolyte en administrateur pour réparer Windows.')
    dism_out,dism_err,dism_code=_run(['DISM.exe','/Online','/Cleanup-Image','/RestoreHealth'],timeout=2400)
    if dism_code!=0:raise RuntimeError('DISM a échoué : '+(dism_err or dism_out)[-3000:])
    sfc_out,sfc_err,sfc_code=_run(['sfc.exe','/scannow'],timeout=2400)
    if sfc_code not in (0,1,2):raise RuntimeError('SFC a échoué : '+(sfc_err or sfc_out)[-3000:])
    return {'DISM':(dism_out or dism_err)[-5000:],'SFC':(sfc_out or sfc_err)[-5000:]}

def clear_windows_history():
    appdata=Path(os.environ.get('APPDATA',Path.home()/'AppData'/'Roaming'))
    local=Path(os.environ.get('LOCALAPPDATA',Path.home()/'AppData'/'Local'))
    roots=[
        appdata/'Microsoft'/'Windows'/'Recent',
        local/'Microsoft'/'Windows'/'Explorer',
    ]
    removed=0;freed=0
    for root in roots:
        if not root.exists():continue
        if root.name=='Explorer':
            candidates=list(root.glob('thumbcache_*.db'))+list(root.glob('iconcache_*.db'))
        else:
            candidates=[p for p in root.rglob('*') if p.is_file()]
        for path in candidates:
            try:
                size=path.stat().st_size;path.unlink();removed+=1;freed+=size
            except (OSError,PermissionError):pass
    return {'files_removed':removed,'freed_mb':round(freed/1024**2,1),'note':'Les éléments verrouillés par Explorer sont conservés.'}

SCHEDULED_TASK_BACKUP='scheduled-tasks-backup.json'
SCHEDULED_TASKS=[
    r'\Microsoft\Windows\Application Experience\Microsoft Compatibility Appraiser',
    r'\Microsoft\Windows\Customer Experience Improvement Program\Consolidator',
    r'\Microsoft\Windows\Customer Experience Improvement Program\UsbCeip',
    r'\Microsoft\Windows\Feedback\Siuf\DmClient',
    r'\Microsoft\Windows\Feedback\Siuf\DmClientOnScenarioDownload',
]

def optimize_scheduled_tasks(root):
    if not is_admin():raise PermissionError('Relance Acolyte en administrateur pour modifier les tâches planifiées.')
    rows=[]
    for full in SCHEDULED_TASKS:
        path,name=full.rsplit('\\',1);taskpath=path+'\\'
        script=f"$t=Get-ScheduledTask -TaskPath '{taskpath.replace(chr(39),chr(39)*2)}' -TaskName '{name.replace(chr(39),chr(39)*2)}' -ErrorAction SilentlyContinue; if($t){{[ordered]@{{Path=$t.TaskPath;Name=$t.TaskName;State=[string]$t.State;Enabled=[bool]$t.Settings.Enabled}}}}"
        try:data=_strict_json(script)
        except Exception:data=None
        if not isinstance(data,dict):continue
        rows.append(data)
    path=Path(root)/SCHEDULED_TASK_BACKUP
    path.write_text(json.dumps(rows,ensure_ascii=False,indent=2),encoding='utf-8')
    disabled=0
    for row in rows:
        taskpath=str(row['Path']).replace("'","''");name=str(row['Name']).replace("'","''")
        out,err,code=_ps(f"Disable-ScheduledTask -TaskPath '{taskpath}' -TaskName '{name}' -ErrorAction Stop|Out-Null")
        if code==0:disabled+=1
    return f'{disabled} tâche(s) de télémétrie désactivée(s). Sauvegarde : {path}'

def restore_scheduled_tasks(root):
    path=Path(root)/SCHEDULED_TASK_BACKUP
    if not path.exists():return 'Aucune sauvegarde de tâches planifiées.'
    rows=json.loads(path.read_text(encoding='utf-8'))
    restored=0
    for row in rows:
        if not row.get('Enabled'):continue
        taskpath=str(row['Path']).replace("'","''");name=str(row['Name']).replace("'","''")
        out,err,code=_ps(f"Enable-ScheduledTask -TaskPath '{taskpath}' -TaskName '{name}' -ErrorAction Stop|Out-Null")
        if code==0:restored+=1
    return f'{restored} tâche(s) réactivée(s).'

DNS_BACKUP_NAME='dns-backup.json'
DNS_CANDIDATES=[
    ('Cloudflare',['1.1.1.1','1.0.0.1']),
    ('Google',['8.8.8.8','8.8.4.4']),
    ('Quad9',['9.9.9.9','149.112.112.112']),
]

def _dns_probe(server):
    times=[]
    for _ in range(3):
        started=time.perf_counter()
        out,err,code=_ps(f"Resolve-DnsName -Name 'www.microsoft.com' -Server '{server}' -DnsOnly -NoHostsFile -ErrorAction Stop|Out-Null",timeout=10)
        if code==0:times.append((time.perf_counter()-started)*1000)
    return statistics.median(times) if times else None

def auto_dns(root):
    if not is_admin():raise PermissionError('Relance Acolyte en administrateur pour changer les DNS.')
    nic=_active_nic_info();name=str(nic.get('Name') or '')
    if not name:raise RuntimeError('Aucune carte réseau active.')
    safe=name.replace("'","''")
    previous=_strict_json(f"$x=Get-DnsClientServerAddress -InterfaceAlias '{safe}' -AddressFamily IPv4 -ErrorAction Stop; @($x.ServerAddresses)")
    if isinstance(previous,str):previous=[previous]
    results=[]
    for provider,servers in DNS_CANDIDATES:
        latency=_dns_probe(servers[0]);results.append({'provider':provider,'servers':servers,'ms':latency})
    valid=[r for r in results if r['ms'] is not None]
    if not valid:raise RuntimeError('Aucun résolveur DNS candidat ne répond correctement.')
    best=min(valid,key=lambda x:x['ms'])
    Path(root,DNS_BACKUP_NAME).write_text(json.dumps({'adapter':name,'servers':previous},ensure_ascii=False,indent=2),encoding='utf-8')
    arr=",".join("'"+x+"'" for x in best['servers'])
    out,err,code=_ps(f"Set-DnsClientServerAddress -InterfaceAlias '{safe}' -ServerAddresses ({arr}) -ErrorAction Stop")
    if code:raise RuntimeError(err or out or 'Changement DNS refusé.')
    _run(['ipconfig.exe','/flushdns'])
    return {'selected':best,'tested':results,'note':'Le DNS influence surtout la résolution de noms, pas le ping d’une partie déjà connectée.'}

def restore_dns(root):
    path=Path(root)/DNS_BACKUP_NAME
    if not path.exists():return 'Aucune sauvegarde DNS.'
    data=json.loads(path.read_text(encoding='utf-8'));name=str(data['adapter']).replace("'","''")
    servers=data.get('servers') or []
    if servers:
        arr=",".join("'"+str(x)+"'" for x in servers)
        script=f"Set-DnsClientServerAddress -InterfaceAlias '{name}' -ServerAddresses ({arr}) -ErrorAction Stop"
    else:
        script=f"Set-DnsClientServerAddress -InterfaceAlias '{name}' -ResetServerAddresses -ErrorAction Stop"
    out,err,code=_ps(script)
    if code:raise RuntimeError(err or out or 'Restauration DNS refusée.')
    return 'DNS restaurés.'

def set_max_refresh_rate():
    if os.name!='nt':raise RuntimeError('Cette action nécessite Windows.')
    from ctypes import wintypes
    CCHDEVICENAME=32;CCHFORMNAME=32
    class DEVMODEW(ctypes.Structure):
        _fields_=[
            ('dmDeviceName',wintypes.WCHAR*CCHDEVICENAME),('dmSpecVersion',wintypes.WORD),('dmDriverVersion',wintypes.WORD),
            ('dmSize',wintypes.WORD),('dmDriverExtra',wintypes.WORD),('dmFields',wintypes.DWORD),
            ('dmOrientation',ctypes.c_short),('dmPaperSize',ctypes.c_short),('dmPaperLength',ctypes.c_short),('dmPaperWidth',ctypes.c_short),
            ('dmScale',ctypes.c_short),('dmCopies',ctypes.c_short),('dmDefaultSource',ctypes.c_short),('dmPrintQuality',ctypes.c_short),
            ('dmPositionX',ctypes.c_long),('dmPositionY',ctypes.c_long),('dmDisplayOrientation',wintypes.DWORD),('dmDisplayFixedOutput',wintypes.DWORD),
            ('dmColor',ctypes.c_short),('dmDuplex',ctypes.c_short),('dmYResolution',ctypes.c_short),('dmTTOption',ctypes.c_short),('dmCollate',ctypes.c_short),
            ('dmFormName',wintypes.WCHAR*CCHFORMNAME),('dmLogPixels',wintypes.WORD),('dmBitsPerPel',wintypes.DWORD),
            ('dmPelsWidth',wintypes.DWORD),('dmPelsHeight',wintypes.DWORD),('dmDisplayFlags',wintypes.DWORD),('dmDisplayFrequency',wintypes.DWORD),
            ('dmICMMethod',wintypes.DWORD),('dmICMIntent',wintypes.DWORD),('dmMediaType',wintypes.DWORD),('dmDitherType',wintypes.DWORD),
            ('dmReserved1',wintypes.DWORD),('dmReserved2',wintypes.DWORD),('dmPanningWidth',wintypes.DWORD),('dmPanningHeight',wintypes.DWORD)]
    ENUM_CURRENT_SETTINGS=-1;CDS_UPDATEREGISTRY=1;DISP_CHANGE_SUCCESSFUL=0;DM_DISPLAYFREQUENCY=0x400000
    user32=ctypes.windll.user32
    current=DEVMODEW();current.dmSize=ctypes.sizeof(DEVMODEW)
    if not user32.EnumDisplaySettingsW(None,ENUM_CURRENT_SETTINGS,ctypes.byref(current)):
        raise RuntimeError('Impossible de lire le mode d’affichage actuel.')
    best=int(current.dmDisplayFrequency);mode=0
    while True:
        candidate=DEVMODEW();candidate.dmSize=ctypes.sizeof(DEVMODEW)
        if not user32.EnumDisplaySettingsW(None,mode,ctypes.byref(candidate)):break
        mode+=1
        if candidate.dmPelsWidth==current.dmPelsWidth and candidate.dmPelsHeight==current.dmPelsHeight and candidate.dmBitsPerPel==current.dmBitsPerPel:
            best=max(best,int(candidate.dmDisplayFrequency))
    if best<=int(current.dmDisplayFrequency):
        return f'Écran principal déjà au maximum détecté : {current.dmDisplayFrequency} Hz.'
    current.dmDisplayFrequency=best;current.dmFields|=DM_DISPLAYFREQUENCY
    result=user32.ChangeDisplaySettingsW(ctypes.byref(current),CDS_UPDATEREGISTRY)
    if result!=DISP_CHANGE_SUCCESSFUL:raise RuntimeError('Windows a refusé le changement de fréquence : '+str(result))
    return f'Écran principal réglé de manière persistante sur {best} Hz.'

def uninstall_onedrive():
    if game_process_running().get('running'):
        raise RuntimeError('Ferme Fortnite avant cette opération.')
    system=Path(os.environ.get('SystemRoot',r'C:\Windows'))
    candidates=[system/'SysWOW64'/'OneDriveSetup.exe',system/'System32'/'OneDriveSetup.exe']
    setup=next((x for x in candidates if x.exists()),None)
    if setup is None:raise RuntimeError('OneDriveSetup.exe introuvable.')
    out,err,code=_run([str(setup),'/uninstall'],timeout=180)
    if code not in (0,None):raise RuntimeError(err or out or 'Désinstallation OneDrive échouée.')
    return 'Désinstallation OneDrive lancée. Cette action n’est pas annulée par Restaurer ; OneDrive peut être réinstallé depuis Microsoft.'

def clear_windows_update_cache():
    if not is_admin():raise PermissionError('Relance Acolyte en administrateur.')
    services=('wuauserv','bits')
    for service in services:_run(['net.exe','stop',service],timeout=30)
    root=Path(os.environ.get('SystemRoot',r'C:\Windows'))/'SoftwareDistribution'/'Download'
    count,size=_delete_tree_files(root)
    for service in reversed(services):_run(['net.exe','start',service],timeout=30)
    return {'files_removed':count,'freed_mb':round(size/1024**2,1),'note':'Cache de téléchargement Windows Update nettoyé ; Windows reconstruira ce qui est nécessaire.'}

def network_diagnostics():
    data=_strict_json("""$nic=Get-NetAdapter -Physical -ErrorAction SilentlyContinue|Where-Object Status -eq 'Up'|Sort-Object LinkSpeed -Descending|Select -First 1
if($null -eq $nic){[ordered]@{status='Aucune interface réseau active'}}else{
 $ip=Get-NetIPConfiguration -InterfaceIndex $nic.ifIndex -ErrorAction SilentlyContinue
 $rss=Get-NetAdapterRss -Name $nic.Name -ErrorAction SilentlyContinue
 $rsc=Get-NetAdapterRsc -Name $nic.Name -ErrorAction SilentlyContinue
 $pm=Get-NetAdapterPowerManagement -Name $nic.Name -ErrorAction SilentlyContinue
 [ordered]@{
  name=$nic.Name;description=$nic.InterfaceDescription;link=$nic.LinkSpeed
  ipv4=(@($ip.IPv4Address.IPAddress)-join ', ');gateway=(@($ip.IPv4DefaultGateway.NextHop)-join ', ')
  dns=(@($ip.DNSServer.ServerAddresses)-join ', ')
  rss=$rss.Enabled;rsc_ipv4=$rsc.IPv4Enabled;rsc_ipv6=$rsc.IPv6Enabled
  allow_power_off=$pm.AllowComputerToTurnOffDevice
 }}""")
    return 'Diagnostic réseau en lecture seule. Acolyte ne force aucun tweak réseau sans mesure.\n\n'+json.dumps(data,ensure_ascii=False,indent=2)

def _steam_root():
    candidates=[
        Path(os.environ.get('ProgramFiles(x86)',r'C:\Program Files (x86)'))/'Steam',
        Path(os.environ.get('ProgramFiles',r'C:\Program Files'))/'Steam'
    ]
    if os.name=='nt':
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER,r'Software\Valve\Steam') as h:
                value,_=winreg.QueryValueEx(h,'SteamPath')
                if value:candidates.insert(0,Path(value))
        except Exception:pass
    return next((p for p in candidates if p.exists()),None)

def _steam_libraries(root):
    if not root:return []
    libs=[root]
    path=root/'steamapps'/'libraryfolders.vdf'
    try:text=path.read_text(encoding='utf-8',errors='replace')
    except OSError:return libs
    for value in re.findall(r'"path"\s*"([^"]+)"',text,re.I):
        p=Path(value.replace('\\\\','\\'))
        if p.exists() and p not in libs:libs.append(p)
    return libs

def _detect_steam_games():
    root=_steam_root();games=[]
    for lib in _steam_libraries(root):
        steamapps=lib/'steamapps'
        if not steamapps.exists():continue
        for manifest in steamapps.glob('appmanifest_*.acf'):
            try:
                text=manifest.read_text(encoding='utf-8',errors='replace')
                def val(key):
                    m=re.search(r'"'+re.escape(key)+r'"\s*"([^"]*)"',text,re.I)
                    return m.group(1).strip() if m else ''
                name=val('name');installdir=val('installdir');appid=val('appid')
                path=steamapps/'common'/installdir
                if name and installdir and path.exists():
                    games.append({'launcher':'Steam','name':name,'path':str(path),'app_id':appid})
            except Exception:pass
    return games

def _detect_epic_games():
    games=[]
    manifests=Path(os.environ.get('ProgramData',r'C:\ProgramData'))/'Epic'/'EpicGamesLauncher'/'Data'/'Manifests'
    if manifests.exists():
        for path in manifests.glob('*.item'):
            try:
                data=json.loads(path.read_text(encoding='utf-8'))
                name=str(data.get('DisplayName') or data.get('AppName') or '').strip()
                loc=str(data.get('InstallLocation') or '').strip()
                if name and loc and Path(loc).exists():
                    games.append({'launcher':'Epic Games','name':name,'path':loc,'app_id':str(data.get('AppName') or '')})
            except Exception:pass
    return games

def _detect_riot_games():
    games=[]
    candidates=[
        ('VALORANT',Path(r'C:\Riot Games\VALORANT')),
        ('League of Legends',Path(r'C:\Riot Games\League of Legends')),
    ]
    for name,path in candidates:
        if path.exists():games.append({'launcher':'Riot Games','name':name,'path':str(path)})
    return games

def _game_executable(game):
    root=Path(str(game.get('path') or ''))
    if not root.exists():return None
    name=str(game.get('name') or '').casefold()
    preferred=[]
    all_exe=[]
    try:
        for exe in root.rglob('*.exe'):
            lower=exe.name.casefold()
            if any(x in lower for x in ('uninstall','crashreport','reporter','launcher','easyanticheat','battleye','redistributable','setup','installer')):
                continue
            try:
                if exe.stat().st_size<1_000_000:continue
            except OSError:continue
            all_exe.append(exe)
            stem=exe.stem.casefold().replace('-',' ').replace('_',' ')
            words=[x for x in re.split(r'\W+',name) if len(x)>=4]
            score=sum(1 for w in words if w in stem)
            if score:preferred.append((score,exe.stat().st_size,exe))
    except OSError:pass
    if preferred:
        preferred.sort(key=lambda x:(x[0],x[1]),reverse=True)
        return str(preferred[0][2])
    if all_exe:
        all_exe.sort(key=lambda x:x.stat().st_size if x.exists() else 0,reverse=True)
        return str(all_exe[0])
    return None

def _detect_common_library_games():
    """Détection best-effort de bibliothèques sans parser de format propriétaire.
    On inspecte uniquement le premier niveau de dossiers connus pour éviter les scans lourds.
    """
    env_pf=Path(os.environ.get('ProgramFiles',r'C:\Program Files'))
    env_pf86=Path(os.environ.get('ProgramFiles(x86)',r'C:\Program Files (x86)'))
    roots=[
        ('Xbox',Path(r'C:\XboxGames')),
        ('EA App',env_pf/'EA Games'),
        ('EA App',env_pf/'Electronic Arts'),
        ('Ubisoft Connect',env_pf86/'Ubisoft'/'Ubisoft Game Launcher'/'games'),
        ('Ubisoft Connect',env_pf/'Ubisoft'/'Ubisoft Game Launcher'/'games'),
        ('GOG',env_pf86/'GOG Galaxy'/'Games'),
        ('GOG',env_pf/'GOG Galaxy'/'Games'),
    ]
    games=[]
    ignored={'support','redist','redistributables','launcher','update','installer','commonredist','directx'}
    for launcher,root in roots:
        if not root.exists() or not root.is_dir():continue
        try:
            children=list(root.iterdir())[:250]
        except OSError:continue
        for child in children:
            if not child.is_dir() or child.name.casefold() in ignored:continue
            game={'launcher':launcher,'name':child.name,'path':str(child)}
            exe=_game_executable(game)
            if exe:
                game['exe']=exe
                games.append(game)
    return games

def detected_games():
    games=_detect_epic_games()+_detect_steam_games()+_detect_riot_games()+_detect_common_library_games()
    # Fortnite fallback si le manifeste Epic manque.
    candidates=[
        ('Fortnite',Path(os.environ.get('ProgramFiles',r'C:\Program Files'))/'Epic Games'/'Fortnite'),
        ('Fortnite',Path(r'C:\Program Files\Epic Games\Fortnite')),
    ]
    for name,path in candidates:
        if path.exists():games.append({'launcher':'Détection locale','name':name,'path':str(path)})
    unique={}
    for game in games:
        key=(str(game.get('name') or '').casefold(),str(game.get('path') or '').casefold())
        if key in unique:continue
        row=dict(game)
        row['exe']=row.get('exe') or _game_executable(row)
        row['profile']='fortnite' if str(row.get('name') or '').casefold()=='fortnite' else 'generic'
        unique[key]=row
    return sorted(unique.values(),key=lambda x:(str(x.get('launcher')),str(x.get('name')).casefold()))

def _safe_game_key(game):
    raw=(str(game.get('launcher') or '')+'|'+str(game.get('name') or '')+'|'+str(game.get('path') or '')).casefold()
    return hashlib.sha256(raw.encode('utf-8')).hexdigest()[:20]

def generic_game_profile_status(game):
    exe=game.get('exe') or _game_executable(game)
    state_path=GAME_PROFILE_DIR/('generic-'+_safe_game_key(game)+'.json')
    applied=False
    try:
        data=json.loads(state_path.read_text(encoding='utf-8'))
        applied=bool(data.get('applied')) and str(data.get('exe') or '').casefold()==str(exe or '').casefold()
    except Exception:pass
    return {'applied':applied,'exe':exe,'supported':bool(exe),'state_path':str(state_path)}

def apply_generic_game_profile(root,game):
    if os.name!='nt':raise RuntimeError('Profil jeu disponible uniquement sous Windows.')
    game=dict(game or {})
    if str(game.get('name') or '').casefold()=='fortnite':
        return apply_fortnite_profile(root)
    exe=game.get('exe') or _game_executable(game)
    if not exe:raise RuntimeError("Acolyte n’a pas trouvé l’exécutable principal de ce jeu.")
    backend=WindowsSettings()
    GAME_PROFILE_DIR.mkdir(parents=True,exist_ok=True)
    state_path=GAME_PROFILE_DIR/('generic-'+_safe_game_key(game)+'.json')
    spec={'kind':'registry','id':'game_gpu','name':exe}
    original=backend.read(spec)
    target={'exists':True,'type':1,'value':'GpuPreference=2;'}
    # Réglages Windows communs, réversibles et déjà gérés par Acolyte.
    optimize(root,['game','captures'],backend)
    backend.write(spec,target)
    state={
        'schema':1,'applied':True,'game':game.get('name'),'launcher':game.get('launcher'),
        'path':game.get('path'),'exe':exe,'gpu_original':original,'gpu_target':target,
        'applied_at':time.strftime('%Y-%m-%d %H:%M:%S')
    }
    _save_state(state_path,state)
    return {
        'message':str(game.get('name') or 'Jeu')+' optimisé.',
        'changes':['Mode Jeu Windows activé','Captures DVR en arrière-plan désactivées','GPU haute performance forcé pour cet exécutable'],
        'game':game,'state':state
    }

# === PROFILS D'OPTIMISATION PAR JEU ===
GAME_PROFILE_DIR = Path(os.environ.get('LOCALAPPDATA', str(Path.home()/'AppData'/'Local'))) / 'AcolyteFortnite' / 'GameProfiles'
FORTNITE_PROFILE_FILE = GAME_PROFILE_DIR / 'fortnite-profile.json'

def _fortnite_config_path():
    local=Path(os.environ.get('LOCALAPPDATA', str(Path.home()/'AppData'/'Local')))
    candidates=[
        local/'FortniteGame'/'Saved'/'Config'/'WindowsClient'/'GameUserSettings.ini',
        local/'FortniteGame'/'Saved'/'Config'/'Windows'/'GameUserSettings.ini',
    ]
    return next((p for p in candidates if p.exists()),candidates[0])

def _read_ini_values(path):
    path=Path(path)
    result={}
    if not path.exists():return result
    section=''
    try:text=path.read_text(encoding='utf-8-sig',errors='replace')
    except OSError:return result
    for raw in text.splitlines():
        line=raw.strip()
        if not line or line.startswith((';','#')):continue
        if line.startswith('[') and line.endswith(']'):
            section=line[1:-1].strip();continue
        if '=' not in line:continue
        key,value=line.split('=',1)
        result[(section,key.strip().casefold())]=value.strip()
    return result

FORTNITE_COMPETITIVE_SETTINGS={
    '/Script/FortniteGame.FortGameUserSettings':{
        'bUseVSync':'False',
        'bMotionBlur':'False',
        'bUseDynamicResolution':'False',
        'FrameRateLimit':'0.000000',
    },
    'ScalabilityGroups':{
        'sg.ViewDistanceQuality':'0',
        'sg.AntiAliasingQuality':'0',
        'sg.ShadowQuality':'0',
        'sg.GlobalIlluminationQuality':'0',
        'sg.ReflectionQuality':'0',
        'sg.PostProcessQuality':'0',
        'sg.TextureQuality':'0',
        'sg.EffectsQuality':'0',
        'sg.FoliageQuality':'0',
        'sg.ShadingQuality':'0',
    },
}

def _patch_ini_text(text,settings):
    lines=text.splitlines()
    newline='\r\n' if '\r\n' in text else '\n'
    section_positions={}
    current=''
    key_positions={}
    for idx,raw in enumerate(lines):
        stripped=raw.strip()
        if stripped.startswith('[') and stripped.endswith(']'):
            current=stripped[1:-1].strip()
            section_positions[current]=idx
            continue
        if '=' in stripped and current:
            key=stripped.split('=',1)[0].strip().casefold()
            key_positions[(current,key)]=idx

    for section,values in settings.items():
        if section not in section_positions:
            if lines and lines[-1].strip():lines.append('')
            lines.append('['+section+']')
            section_positions[section]=len(lines)-1
        for key,value in values.items():
            pair=(section,key.casefold())
            if pair in key_positions:
                lines[key_positions[pair]]=key+'='+value
            else:
                # Insère juste avant la section suivante si possible, sinon à la fin.
                start=section_positions[section]
                insert_at=len(lines)
                for j in range(start+1,len(lines)):
                    st=lines[j].strip()
                    if st.startswith('[') and st.endswith(']'):
                        insert_at=j;break
                lines.insert(insert_at,key+'='+value)
                # Recalcule les index car l'insertion décale les lignes.
                return _patch_ini_text(newline.join(lines)+newline,settings)
    return newline.join(lines)+(newline if text.endswith(('\n','\r')) or lines else '')

def _fortnite_profile_targets(exe):
    return [
        ({'kind':'registry','id':'game_auto'},{'exists':True,'type':4,'value':1}),
        ({'kind':'registry','id':'game_allow'},{'exists':True,'type':4,'value':1}),
        ({'kind':'registry','id':'capture'},{'exists':True,'type':4,'value':0}),
        ({'kind':'registry','id':'dvr'},{'exists':True,'type':4,'value':0}),
        ({'kind':'registry','id':'fortnite_gpu','name':exe},{'exists':True,'type':1,'value':'GpuPreference=2;'}),
    ]

def _hash_file(path):
    path=Path(path)
    if not path.exists():return None
    h=hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda:f.read(1024*1024),b''):h.update(chunk)
    return h.hexdigest()

def _fortnite_config_match(path):
    values=_read_ini_values(path)
    total=0;matched=0;details={}
    for section,rows in FORTNITE_COMPETITIVE_SETTINGS.items():
        for key,wanted in rows.items():
            total+=1
            actual=values.get((section,key.casefold()))
            ok=(str(actual).casefold()==str(wanted).casefold())
            matched+=1 if ok else 0
            details[key]={'ok':ok,'actual':actual,'wanted':wanted}
    return matched,total,details

def fortnite_profile_status():
    exe=_fortnite_executable()
    cfg=_fortnite_config_path()
    installed=bool(exe)
    running=game_process_running().get('running',False)
    backend=None
    reg_ok=0;reg_total=0;registry={}
    if installed and os.name=='nt':
        try:
            backend=WindowsSettings()
            for spec,target in _fortnite_profile_targets(exe):
                reg_total+=1
                current=backend.read(spec);ok=(current==target)
                reg_ok+=1 if ok else 0
                registry[str(spec.get('id'))]={'ok':ok,'current':current,'target':target}
        except Exception as exc:
            registry['error']=str(exc)
    cfg_match,cfg_total,cfg_details=_fortnite_config_match(cfg)
    try:
        state=json.loads(FORTNITE_PROFILE_FILE.read_text(encoding='utf-8')) if FORTNITE_PROFILE_FILE.exists() else {}
    except Exception:state={}
    score=0
    if installed:score+=10
    if reg_total:score+=round(35*(reg_ok/reg_total))
    if cfg_total and cfg.exists():score+=round(40*(cfg_match/cfg_total))
    history=benchmark_history(1) if 'benchmark_history' in globals() else []
    measured=False
    if history:
        measured=True
        last=history[-1]
        avg=float(last.get('avg_fps') or 0);low=float(last.get('one_percent_low') or 0);low01=float(last.get('point_one_percent_low') or 0)
        duration=max(1.0,float(last.get('duration_seconds') or 60))
        stutters=float(last.get('stutters_33ms') or 0)*60.0/duration
        stability=(low/avg) if avg else 0
        tail=(low01/avg) if avg else 0
        bench_points=0
        bench_points+=6 if stability>=0.70 else 4 if stability>=0.55 else 2 if stability>=0.40 else 0
        bench_points+=4 if tail>=0.50 else 3 if tail>=0.35 else 1 if tail>=0.20 else 0
        bench_points+=5 if stutters<=1 else 3 if stutters<=5 else 1 if stutters<=10 else 0
        score+=bench_points
    return {
        'game':'Fortnite','supported':True,'installed':installed,'running':running,
        'exe':exe,'config_path':str(cfg),'config_exists':cfg.exists(),
        'registry_ok':reg_ok,'registry_total':reg_total,'registry':registry,
        'config_ok':cfg_match,'config_total':cfg_total,'config_details':cfg_details,
        'profile_applied':bool(state.get('applied')),
        'applied_at':state.get('applied_at'),'score':max(0,min(100,int(score))),
        'benchmark_measured':measured
    }

def apply_fortnite_profile(root):
    if os.name!='nt':raise RuntimeError('Profil Fortnite disponible uniquement sous Windows.')
    if game_process_running().get('running'):
        raise RuntimeError('Ferme Fortnite avant d’appliquer son profil afin de ne pas écraser un fichier de configuration en cours d’utilisation.')
    exe=_fortnite_executable()
    if not exe:raise RuntimeError('Fortnite n’est pas détecté. Lance Epic Games Launcher et vérifie que le jeu est installé.')
    cfg=_fortnite_config_path()
    if not cfg.exists():
        raise RuntimeError('GameUserSettings.ini est introuvable. Lance Fortnite une première fois, ferme-le puis réessaie.')

    GAME_PROFILE_DIR.mkdir(parents=True,exist_ok=True)
    backend=WindowsSettings()
    current_state={}
    try:
        if FORTNITE_PROFILE_FILE.exists():
            current_state=json.loads(FORTNITE_PROFILE_FILE.read_text(encoding='utf-8'))
    except Exception:current_state={}

    backup=GAME_PROFILE_DIR/'Fortnite-GameUserSettings.backup.ini'
    if not current_state.get('applied') or not backup.exists():
        shutil.copy2(cfg,backup)

    registry_backup=current_state.get('registry_backup')
    if not registry_backup:
        registry_backup=[]
        for spec,target in _fortnite_profile_targets(exe):
            registry_backup.append({'spec':spec,'original':backend.read(spec),'target':target})

    original_text=cfg.read_text(encoding='utf-8-sig',errors='replace')
    patched=_patch_ini_text(original_text,FORTNITE_COMPETITIVE_SETTINGS)
    temp=cfg.with_suffix('.acolyte.tmp')
    touched=[]
    try:
        for row in registry_backup:
            spec=row['spec'];target=row['target']
            previous=backend.read(spec)
            if previous!=target:
                backend.write(spec,target);touched.append((spec,previous))
        temp.write_text(patched,encoding='utf-8')
        os.replace(temp,cfg)
    except Exception:
        for spec,previous in reversed(touched):
            try:backend.write(spec,previous)
            except Exception:pass
        try:
            if temp.exists():temp.unlink()
        except OSError:pass
        raise

    state={
        'schema':1,'game':'Fortnite','applied':True,
        'applied_at':time.strftime('%Y-%m-%d %H:%M:%S'),
        'config_path':str(cfg),'backup_path':str(backup),
        'applied_hash':_hash_file(cfg),'registry_backup':registry_backup,
        'profile':'competitive-max-fps-v1'
    }
    tmp=FORTNITE_PROFILE_FILE.with_suffix('.tmp')
    tmp.write_text(json.dumps(state,ensure_ascii=False,indent=2),encoding='utf-8')
    os.replace(tmp,FORTNITE_PROFILE_FILE)
    status=fortnite_profile_status()
    return {
        'message':'Profil Fortnite compétitif appliqué.',
        'changes':[
            'Mode Jeu Windows activé','Captures Game DVR désactivées',
            'Fortnite forcé sur le GPU haute performance',
            'V-Sync, Motion Blur et résolution dynamique désactivés',
            'Qualité Scalability réglée au minimum pour viser les FPS/latence'
        ],
        'status':status,'backup':str(backup)
    }

def restore_fortnite_profile(root):
    if game_process_running().get('running'):
        raise RuntimeError('Ferme Fortnite avant de restaurer son profil.')
    if not FORTNITE_PROFILE_FILE.exists():
        return {'message':'Aucune sauvegarde de profil Fortnite à restaurer.','restored':False}
    state=json.loads(FORTNITE_PROFILE_FILE.read_text(encoding='utf-8'))
    cfg=Path(state.get('config_path') or _fortnite_config_path())
    backup=Path(state.get('backup_path') or '')
    if not backup.exists():raise RuntimeError('Sauvegarde Fortnite introuvable.')

    # Si l'utilisateur a modifié Fortnite après le profil, garde une copie avant restauration.
    current_hash=_hash_file(cfg)
    applied_hash=state.get('applied_hash')
    preserved=None
    if cfg.exists() and current_hash and applied_hash and current_hash!=applied_hash:
        preserved=GAME_PROFILE_DIR/('Fortnite-pre-restore-'+time.strftime('%Y%m%d-%H%M%S')+'.ini')
        shutil.copy2(cfg,preserved)

    shutil.copy2(backup,cfg)
    backend=WindowsSettings()
    skipped=[]
    for row in reversed(state.get('registry_backup') or []):
        spec=row.get('spec');original=row.get('original');target=row.get('target')
        if not spec:continue
        try:
            current=backend.read(spec)
            # Ne restaure que si le réglage est toujours celui posé par ce profil.
            if current==target:backend.write(spec,original)
            else:skipped.append(str(spec.get('id') or spec.get('kind')))
        except Exception as exc:
            skipped.append(str(spec.get('id') or spec.get('kind'))+': '+str(exc))
    state['applied']=False;state['restored_at']=time.strftime('%Y-%m-%d %H:%M:%S')
    tmp=FORTNITE_PROFILE_FILE.with_suffix('.tmp')
    tmp.write_text(json.dumps(state,ensure_ascii=False,indent=2),encoding='utf-8')
    os.replace(tmp,FORTNITE_PROFILE_FILE)
    return {
        'message':'Profil Fortnite restauré.',
        'restored':True,'preserved_current':str(preserved) if preserved else None,
        'skipped_registry':skipped
    }

def bios_diagnostics():
    data=bios_snapshot()
    mem=data.get('memory_profile') or {}
    lines=[
        'Diagnostic RAM automatique',
        f"Vitesse actuelle : {mem.get('current_mt') or '?'} MT/s",
        f"Cible estimée : {mem.get('target_mt') or '?'} MT/s",
        f"État : {mem.get('status','INCONNU')}",
        '',
    ]
    if mem.get('profile_likely_off'):
        lines.append("Conclusion : le profil mémoire semble désactivé. Acolyte peut confirmer ce diagnostic sans ouvrir le BIOS.")
    else:
        lines.append("Conclusion : aucune sous-fréquence évidente détectée par Windows.")
    lines.extend([
        "Acolyte ne modifie pas automatiquement fréquence/tension/timings DDR5 depuis Windows : ces paramètres sont appliqués avant le chargement de Windows.",
        '',
        json.dumps(data,ensure_ascii=False,indent=2)
    ])
    return '\n'.join(lines)

def checkup():
    temp=Path(os.environ.get('TEMP',Path.home()/'AppData'/'Local'/'Temp'))
    count=0;size=0
    try:
        for p in temp.rglob('*'):
            try:
                if p.is_file():
                    count+=1;size+=p.stat().st_size
            except OSError:pass
    except OSError:pass
    disks=[]
    try:
        import shutil
        for letter in ('C:/','D:/','E:/'):
            if Path(letter).exists():
                total,used,free=shutil.disk_usage(letter)
                disks.append({'drive':letter,'free_gb':round(free/1024**3,1),'total_gb':round(total/1024**3,1)})
    except Exception:pass
    return {
        'temp_files':count,'temp_size_mb':round(size/1024**2,1),'temp_path':str(temp),
        'disks':disks,'note':'Le nettoyage proposé supprime seulement les fichiers temporaires utilisateur anciens de plus de 7 jours et ignore les fichiers verrouillés.'
    }

def cleanup_temp(days=7):
    root=Path(os.environ.get('TEMP',Path.home()/'AppData'/'Local'/'Temp')).resolve()
    cutoff=time.time()-max(1,int(days))*86400
    removed=0;freed=0
    for p in list(root.rglob('*')):
        try:
            if not p.is_file() or p.is_symlink():continue
            rp=p.resolve()
            if root not in rp.parents:continue
            st=p.stat()
            if st.st_mtime>cutoff:continue
            size=st.st_size
            p.unlink()
            removed+=1;freed+=size
        except (OSError,PermissionError):pass
    return f"{removed} fichier(s) temporaire(s) anciens supprimés, {freed/1024**2:.1f} Mo libérés. Les fichiers récents/verrouillés ont été conservés."

def diagnostics(category):
    scripts = {
        'ram': "@(Get-CimInstance Win32_PhysicalMemory | Select-Object Manufacturer,PartNumber,Capacity,Speed,ConfiguredClockSpeed)",
        'gpu': "@(Get-CimInstance Win32_VideoController | Select-Object Name,DriverVersion,DriverDate)",
        'usb': "@(Get-PnpDevice -Class USB -PresentOnly | Select-Object Status,FriendlyName)",
    }
    if category == 'network':return network_diagnostics()
    if category == 'games':return json.dumps(detected_games(),ensure_ascii=False,indent=2) if detected_games() else 'Aucun jeu détecté dans les manifests Epic/chemins connus.'
    if category == 'bios':return bios_diagnostics()
    if category == 'checkup':return json.dumps(checkup(),ensure_ascii=False,indent=2)
    if category == 'power':
        out, err, code = _run(['powercfg.exe', '/list'])
        if code:raise RuntimeError(err or out)
        return out
    if category not in scripts:raise ValueError('Diagnostic inconnu.')
    data = _strict_json(scripts[category])
    notes = {
        'ram': 'Valeurs rapportées par Windows. La fréquence seule ne confirme pas EXPO/XMP. Activation à vérifier dans le BIOS, selon le kit mémoire et le manuel de la carte mère.',
        'gpu': 'Pilotes installés. La disponibilité d’une mise à jour se vérifie chez le fabricant. Aucun overclocking ou réglage de tension automatique.',
        'usb': 'Garde la suspension sélective USB par défaut. Le test optionnel concerne uniquement des déconnexions de périphériques sur secteur.',
    }
    return notes[category]+'\n\n'+json.dumps(data, ensure_ascii=False, indent=2)


LINKS = {
    'apps': 'ms-settings:appsfeatures', 'startup': 'ms-settings:startupapps',
    'updates': 'ms-settings:windowsupdate', 'graphics': 'ms-settings:display-advancedgraphics',
    'game': 'ms-settings:gaming-gamemode', 'power': 'ms-settings:powersleep',
    'amd': 'https://www.amd.com/en/support/download/drivers.html',
    'nvidia': 'https://www.nvidia.com/Download/index.aspx',
    'intel': 'https://www.intel.com/content/www/us/en/support/detect.html',
    'board': 'https://www.msi.com/Motherboard/B650-GAMING-PLUS-WIFI/support',
    'store': 'ms-windows-store://home',
    'privacy': 'ms-settings:privacy-general',
}


def open_amd_software():
    if os.name!='nt':raise RuntimeError('AMD Software nécessite Windows.')
    candidates=[
        Path(os.environ.get('ProgramFiles',r'C:\Program Files'))/'AMD'/'CNext'/'CNext'/'RadeonSoftware.exe',
        Path(r'C:\Program Files\AMD\CNext\CNext\RadeonSoftware.exe')
    ]
    exe=next((x for x in candidates if x.exists()),None)
    if exe:
        os.startfile(str(exe));return str(exe)
    open_panel('amd')
    return 'AMD Software introuvable : page officielle AMD ouverte.'

def open_graphics_settings():
    if os.name!='nt':raise RuntimeError('Ce panneau nécessite Windows.')
    os.startfile('ms-settings:display-advancedgraphics')
    return 'Paramètres graphiques Windows ouverts.'

def reboot_to_firmware():
    """Demande à Windows de redémarrer directement vers l'interface firmware UEFI.
    Ne modifie aucune valeur BIOS/EXPO.
    """
    if os.name!='nt':raise RuntimeError('Le redémarrage UEFI nécessite Windows.')
    if not is_admin():raise PermissionError('Les droits administrateur sont requis pour redémarrer vers l’UEFI.')
    out,err,code=_run(['shutdown.exe','/r','/fw','/t','0'])
    if code:raise RuntimeError(err or out or 'Windows a refusé le redémarrage vers l’UEFI.')
    return 'Redémarrage vers l’UEFI demandé.'

def open_panel(name):
    import webbrowser
    target = LINKS[name]
    if target.startswith('https:'):webbrowser.open(target)
    elif os.name == 'nt':os.startfile(target)
    else:raise RuntimeError('Ce panneau nécessite Windows.')


# === ACOLYTE PERFORMANCE PREMIUM ===
# Façade structurée pour la nouvelle interface. Les fonctions historiques restent
# disponibles afin de conserver la compatibilité avec les versions précédentes.

_legacy_analyze = analyze

def analyze():
    info = _legacy_analyze()
    raw = str(info.get('gpu',''))
    gpus = [x.strip() for x in raw.split(',') if x.strip()]
    usable = [x for x in gpus if 'Parsec Virtual Display Adapter' not in x and 'Microsoft Basic' not in x]
    def rank(name):
        n=name.lower()
        if '7900 xt' in n:return 100
        if 'radeon rx' in n:return 90
        if 'geforce rtx' in n:return 90
        if 'geforce gtx' in n:return 80
        if 'arc a' in n:return 75
        if 'radeon(tm) graphics' in n:return 10
        return 40
    preferred=max(usable,key=rank) if usable else (gpus[0] if gpus else 'Inconnu')
    info['gpu_all']=usable or gpus
    info['gpu']=preferred
    return info

def network_snapshot():
    return _strict_json("""$physical=$true
$nic=Get-NetAdapter -Physical -ErrorAction SilentlyContinue|Where-Object Status -eq 'Up'|Sort-Object LinkSpeed -Descending|Select-Object -First 1
if($null -eq $nic){
 $physical=$false
 $nic=Get-NetAdapter -ErrorAction SilentlyContinue|Where-Object {$_.Status -eq 'Up' -and $_.Name -notmatch 'Loopback'}|Sort-Object LinkSpeed -Descending|Select-Object -First 1
}
if($null -eq $nic){[ordered]@{status='offline'}}else{
 $ip=$null;$rss=$null;$rsc=$null;$pm=$null
 try{$ip=Get-NetIPConfiguration -InterfaceIndex $nic.ifIndex -ErrorAction Stop}catch{}
 try{$rss=Get-NetAdapterRss -Name $nic.Name -ErrorAction Stop}catch{}
 try{$rsc=Get-NetAdapterRsc -Name $nic.Name -ErrorAction Stop}catch{}
 try{$pm=Get-NetAdapterPowerManagement -Name $nic.Name -ErrorAction Stop}catch{}
 [ordered]@{
  status='online';physical=$physical;name=$nic.Name;description=$nic.InterfaceDescription;link=$nic.LinkSpeed
  ipv4=if($ip){(@($ip.IPv4Address.IPAddress)-join ', ')}else{''}
  gateway=if($ip){(@($ip.IPv4DefaultGateway.NextHop)-join ', ')}else{''}
  dns=if($ip){(@($ip.DNSServer.ServerAddresses)-join ', ')}else{''}
  rss=if($rss){[bool]$rss.Enabled}else{$null}
  rsc_ipv4=if($rsc){[bool]$rsc.IPv4Enabled}else{$null}
  rsc_ipv6=if($rsc){[bool]$rsc.IPv6Enabled}else{$null}
  allow_power_off=if($pm){[string]$pm.AllowComputerToTurnOffDevice}else{$null}
 }}""")

def _memory_target_from_part(part_number,spd_speed=0):
    """Inférence prudente du débit commercial pour quelques références DDR5.
    Retourne (target, confidence, profile_hint). Aucune écriture firmware.
    """
    part=str(part_number or '').strip().upper().replace(' ','')
    target=int(spd_speed or 0)
    confidence='spd'
    profile='XMP/EXPO'
    # Corsair Vengeance : les références contiennent souvent B52/B56/Z60 etc.
    match=re.search(r'(?:B|Z)(48|50|52|54|56|58|60|62|64|66|68|70|72)(?:C|Z)',part)
    if not match:
        match=re.search(r'(48|50|52|54|56|58|60|62|64|66|68|70|72)C\d{2}',part)
    if match:
        inferred=int(match.group(1))*100
        if inferred>=target:
            target=inferred;confidence='part_number'
    if 'Z' in part[-8:]:profile='EXPO/XMP'
    elif part.startswith(('CMH','CMK','CMP')):profile='XMP/EXPO'
    return target,confidence,profile

def memory_profile_analysis(data=None):
    data=data or bios_snapshot(raw=True)
    rows=_rows(data.get('ram') if isinstance(data,dict) else None)
    modules=[];current_values=[];target_values=[]
    for row in rows:
        try:
            configured=int(row.get('ConfiguredClockSpeed') or 0)
            spd=int(row.get('Speed') or 0)
        except (TypeError,ValueError):
            configured=spd=0
        target,confidence,profile=_memory_target_from_part(row.get('PartNumber'),spd)
        current_values.append(configured)
        target_values.append(target)
        state='unknown'
        if configured and target:
            state='active_or_stock' if configured>=target-100 else 'profile_likely_off'
        modules.append({
            'part_number':str(row.get('PartNumber') or '').strip(),
            'manufacturer':str(row.get('Manufacturer') or '').strip(),
            'slot':str(row.get('DeviceLocator') or row.get('BankLabel') or '').strip(),
            'capacity':int(row.get('Capacity') or 0),
            'spd_mt':spd,'current_mt':configured,'target_mt':target,
            'target_confidence':confidence,'profile_hint':profile,'state':state,
            'configured_voltage_mv':row.get('ConfiguredVoltage')
        })
    current=min([x for x in current_values if x] or [0])
    target=max([x for x in target_values if x] or [0])
    likely_off=bool(current and target and current<target-100)
    return {
        'current_mt':current,'target_mt':target,
        'profile_likely_off':likely_off,
        'status':'PROFIL MÉMOIRE PROBABLEMENT INACTIF' if likely_off else ('VITESSE CIBLE ATTEINTE' if current and target else 'ÉTAT NON CONFIRMÉ'),
        'modules':modules,
        'can_apply_from_windows':False,
        'reason':'La fréquence, la tension et les timings DDR5 sont initialisés par le firmware avant Windows. Acolyte peut les diagnostiquer et les tester, mais ne les modifie pas automatiquement depuis Windows.'
    }

def bios_snapshot(raw=False):
    data=_strict_json("""$ram=@(Get-CimInstance Win32_PhysicalMemory|Select Manufacturer,PartNumber,Capacity,Speed,ConfiguredClockSpeed,ConfiguredVoltage,MinVoltage,MaxVoltage,DeviceLocator,BankLabel,SMBIOSMemoryType)
$fw='Inconnu'
try{$fw=(Get-ComputerInfo -Property BiosFirmwareType).BiosFirmwareType}catch{}
$virt=Get-CimInstance Win32_Processor|Select -First 1 VirtualizationFirmwareEnabled,VMMonitorModeExtensions,SecondLevelAddressTranslationExtensions
[ordered]@{firmware=$fw;ram=$ram;virtualization=$virt}""")
    if raw:return data
    analysis=memory_profile_analysis(data)
    data['memory_profile']=analysis
    data['hints']=[]
    if analysis.get('profile_likely_off'):
        data['hints'].append(
            f"RAM à {analysis.get('current_mt')} MT/s ; cible estimée {analysis.get('target_mt')} MT/s. "
            "Le profil mémoire XMP/EXPO paraît inactif."
        )
    return data

def settings_snapshot(backend=None):
    backend=backend or WindowsSettings()
    out={}
    mapping={
        'game_auto':{'kind':'registry','id':'game_auto'},
        'game_allow':{'kind':'registry','id':'game_allow'},
        'capture':{'kind':'registry','id':'capture'},
        'dvr':{'kind':'registry','id':'dvr'},
        'ads':{'kind':'registry','id':'ads'},
        'suggestions':{'kind':'registry','id':'suggestions'},
        'suggestions_2':{'kind':'registry','id':'suggestions_2'},
        'hags':{'kind':'registry','id':'hags'},
        'fast_startup':{'kind':'registry','id':'fast_startup'},
    }
    for key,spec in mapping.items():
        try:out[key]=backend.read(spec)
        except Exception as exc:out[key]={'error':str(exc)}
    try:out['power_plan']=backend.read({'kind':'power'})
    except Exception as exc:out['power_plan']='Erreur: '+str(exc)
    try:out['feature_states']=option_states(backend)
    except Exception as exc:out['feature_states_error']=str(exc);out['feature_states']={}
    try:out['feature_desired']=desired_features()
    except Exception:out['feature_desired']={}
    return out

def windows_health_snapshot():
    """État Windows utilisé par le score. Lecture seule ; les valeurs indisponibles restent neutres."""
    try:
        data=_strict_json("""$mp=Get-MpComputerStatus -ErrorAction SilentlyContinue
$fw=@(Get-NetFirewallProfile -ErrorAction SilentlyContinue|Select-Object Name,Enabled)
$pd=@(Get-PhysicalDisk -ErrorAction SilentlyContinue|Select-Object FriendlyName,HealthStatus,OperationalStatus)
$pending=((Test-Path 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Component Based Servicing\RebootPending') -or (Test-Path 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\WindowsUpdate\Auto Update\RebootRequired'))
[ordered]@{
 defender_available=($null -ne $mp)
 antivirus_enabled=if($null -ne $mp){$mp.AntivirusEnabled}else{$null}
 realtime_enabled=if($null -ne $mp){$mp.RealTimeProtectionEnabled}else{$null}
 signature_age_days=if($null -ne $mp){$mp.AntivirusSignatureAge}else{$null}
 firewall=$fw
 physical_disks=$pd
 pending_reboot=$pending
}""")
        return data if isinstance(data,dict) else {}
    except Exception as exc:
        return {'error':str(exc)}

def full_scan():
    system=analyze()
    try:network=network_snapshot()
    except Exception as exc:network={'status':'error','error':str(exc)}
    try:bios=bios_snapshot()
    except Exception as exc:bios={'error':str(exc),'ram':[],'hints':[]}
    try:maintenance=checkup()
    except Exception as exc:maintenance={'error':str(exc),'disks':[]}
    try:settings=settings_snapshot()
    except Exception as exc:settings={'error':str(exc)}
    try:health=windows_health_snapshot()
    except Exception as exc:health={'error':str(exc)}
    try:games=detected_games()
    except Exception:games=[]
    try:startup_count=len(startup_items())
    except Exception:startup_count=None
    try:fortnite=fortnite_profile_status()
    except Exception as exc:fortnite={'game':'Fortnite','supported':True,'installed':False,'error':str(exc),'score':0}
    scan={'system':system,'network':network,'bios':bios,'maintenance':maintenance,
          'settings':settings,'health':health,'games':games,'startup_count':startup_count,
          'game_profiles':{'fortnite':fortnite}}

    legacy_score,recommendations,positives,breakdown=score_scan(scan)

    # Santé = état Windows/stockage/réseau. Les réglages gaming sont volontairement exclus.
    health_points=sum(int(breakdown.get(k,0)) for k in (
        'Sécurité Windows','Stockage / entretien','Démarrage','Réseau','État Windows'))
    health_score=round(health_points/70*100)

    gaming_score,gaming_breakdown,gaming_recos=gaming_score_scan(scan)
    recommendations.extend(gaming_recos)
    overall=round(health_score*0.45+gaming_score*0.55)

    scan['score']=overall
    scan['health_score']=health_score
    scan['gaming_score']=gaming_score
    scan['legacy_score']=legacy_score
    scan['recommendations']=recommendations
    scan['positives']=positives
    scan['score_breakdown']=breakdown
    scan['gaming_breakdown']=gaming_breakdown
    scan['score_note']="Score Acolyte = 45 % santé Windows + 55 % performance gaming. Le score gaming réserve des points au profil du jeu et au benchmark réel ; il ne note pas le prix du matériel."
    return scan

def _reg_is(snapshot, value):
    return isinstance(snapshot,dict) and snapshot.get('exists') and snapshot.get('value')==value

def score_scan(scan):
    """Score de santé/configuration, pas un indice de puissance matérielle."""
    rec=[];ok=[]
    breakdown={
        'Sécurité Windows':25,
        'Performances gaming':30,
        'Stockage / entretien':20,
        'Démarrage':10,
        'Réseau':10,
        'État Windows':5,
    }

    # --- Sécurité Windows : 25 pts ---
    health=scan.get('health') or {}
    if health.get('defender_available'):
        if health.get('antivirus_enabled') is False:
            breakdown['Sécurité Windows']-=8
            rec.append({'level':'important','title':'Antivirus Microsoft Defender désactivé','detail':'La protection antivirus Windows est désactivée. Acolyte ne la désactive jamais.','section':'checkup'})
        else:ok.append('Antivirus Microsoft Defender actif.')
        if health.get('realtime_enabled') is False:
            breakdown['Sécurité Windows']-=9
            rec.append({'level':'important','title':'Protection en temps réel désactivée','detail':'La protection en temps réel de Defender est désactivée.','section':'checkup'})
        else:ok.append('Protection Defender en temps réel active.')
        try:
            age=int(health.get('signature_age_days'))
            if age>7:
                breakdown['Sécurité Windows']-=3
                rec.append({'level':'info','title':'Signatures Defender anciennes','detail':f'Les signatures antivirus ont {age} jours. Vérifie Windows Update.','section':'updates'})
        except (TypeError,ValueError):pass

    fw=health.get('firewall') or []
    fw=_rows(fw)
    if fw:
        disabled=[x for x in fw if x.get('Enabled') is False]
        if disabled:
            breakdown['Sécurité Windows']-=5
            rec.append({'level':'important','title':'Pare-feu Windows partiellement désactivé','detail':'Au moins un profil du pare-feu Windows est désactivé.','section':'checkup'})
        else:ok.append('Pare-feu Windows actif sur les profils détectés.')

    # --- Performances gaming : 30 pts ---
    settings=scan.get('settings') or {}
    if not _reg_is(settings.get('game_auto'),1):
        breakdown['Performances gaming']-=8
        rec.append({'level':'important','title':'Mode Jeu Windows','detail':'Le Mode Jeu n’est pas confirmé actif pour ce compte.','section':'performance','option':'game'})
    else:ok.append('Mode Jeu Windows actif.')

    if _reg_is(settings.get('capture'),1) or _reg_is(settings.get('dvr'),1):
        breakdown['Performances gaming']-=3
        rec.append({'level':'info','title':'Captures Game Bar','detail':'Les captures en arrière-plan sont actives. Désactive-les si tu ne les utilises pas.','section':'performance','option':'captures'})

    if str(settings.get('power_plan','')).lower()!=BALANCED:
        breakdown['Performances gaming']-=4
        rec.append({'level':'info','title':'Plan d’alimentation','detail':'Le plan Équilibré AMD/Windows est conseillé comme base stable pour un Ryzen X3D.','section':'performance','option':'balanced'})
    else:ok.append('Plan d’alimentation Équilibré actif.')

    bios=scan.get('bios') or {}
    hints=bios.get('hints') if isinstance(bios,dict) else []
    if hints:
        breakdown['Performances gaming']-=10
        rec.append({'level':'important','title':'Mémoire RAM à vérifier','detail':hints[0],'section':'bios'})
    else:ok.append('Aucune différence évidente de fréquence RAM détectée par Windows.')

    # --- Stockage / entretien : 20 pts ---
    maintenance=scan.get('maintenance') or {}
    disk_health=_rows(health.get('physical_disks') or [])
    unhealthy=[d for d in disk_health if str(d.get('HealthStatus','')).lower() not in ('healthy','sain','')]
    if unhealthy:
        breakdown['Stockage / entretien']-=8
        rec.append({'level':'important','title':'Santé du stockage à vérifier','detail':'Windows signale au moins un disque avec un état différent de Healthy.','section':'checkup'})
    elif disk_health:ok.append('État physique des disques signalé Healthy.')

    for disk in maintenance.get('disks') or []:
        if str(disk.get('drive','')).upper().startswith('C'):
            total=float(disk.get('total_gb') or 0);free=float(disk.get('free_gb') or 0)
            ratio=(free/total) if total else 1
            if ratio<0.10:
                breakdown['Stockage / entretien']-=10
                rec.append({'level':'important','title':'Disque système presque plein','detail':f"Seulement {free:.1f} Go libres sur {total:.1f} Go.",'section':'checkup'})
            elif ratio<0.20:
                breakdown['Stockage / entretien']-=5
                rec.append({'level':'info','title':'Espace disque système','detail':f"{free:.1f} Go libres sur {total:.1f} Go.",'section':'checkup'})
            else:ok.append('Espace libre du disque système correct.')

    try:
        temp_mb=float(maintenance.get('temp_size_mb') or 0)
        if temp_mb>10240:
            breakdown['Stockage / entretien']-=2
            rec.append({'level':'info','title':'Cache temporaire volumineux','detail':f'{temp_mb/1024:.1f} Go de fichiers TEMP détectés.','section':'checkup'})
        elif temp_mb>5120:
            breakdown['Stockage / entretien']-=1
    except (TypeError,ValueError):pass

    # --- Démarrage : 10 pts ---
    count=scan.get('startup_count')
    if isinstance(count,int):
        if count>20:
            breakdown['Démarrage']-=7
            rec.append({'level':'info','title':'Démarrage très chargé','detail':f'{count} entrées Run détectées. Vérifie celles qui sont inutiles.','section':'startup'})
        elif count>15:
            breakdown['Démarrage']-=5
            rec.append({'level':'info','title':'Démarrage chargé','detail':f'{count} entrées Run détectées.','section':'startup'})
        elif count>8:
            breakdown['Démarrage']-=2
            rec.append({'level':'info','title':'Applications au démarrage','detail':f'{count} entrées Run détectées.','section':'startup'})
        else:ok.append('Nombre d’entrées Run raisonnable.')

    # --- Réseau : 10 pts ---
    net=scan.get('network') or {}
    if net.get('status')!='online':
        breakdown['Réseau']-=8
        rec.append({'level':'important','title':'Interface réseau non confirmée','detail':'Acolyte ne détecte pas d’interface réseau physique active.','section':'network'})
    else:
        ok.append('Interface réseau physique active.')
        if net.get('rss') is False:
            breakdown['Réseau']-=2
            rec.append({'level':'info','title':'RSS réseau désactivé','detail':'Receive Side Scaling est désactivé sur la carte active.','section':'network'})
    desc=str(net.get('description','')).lower();link=str(net.get('link','')).lower()
    if ('2.5' in desc or '2,5' in desc) and ('1 gbps' in link or '1 gb/s' in link or '1 gbit' in link):
        # Information seulement : une liaison 1 Gb/s n’augmente pas automatiquement le ping.
        rec.append({'level':'info','title':'Lien Ethernet à 1 Gbit/s','detail':'La carte semble supporter 2,5 GbE mais la liaison négocie 1 Gbit/s. Cela limite le débit maximal, pas nécessairement la latence.','section':'network'})

    # --- État Windows : 5 pts ---
    if health.get('pending_reboot') is True:
        breakdown['État Windows']-=3
        rec.append({'level':'info','title':'Redémarrage Windows en attente','detail':'Windows signale qu’un redémarrage est nécessaire pour finaliser une mise à jour ou modification.','section':'updates'})
    else:ok.append('Aucun redémarrage Windows en attente détecté.')

    # Clamp par catégorie puis somme.
    max_points={'Sécurité Windows':25,'Performances gaming':30,'Stockage / entretien':20,'Démarrage':10,'Réseau':10,'État Windows':5}
    for key,maxv in max_points.items():
        breakdown[key]=max(0,min(maxv,int(breakdown[key])))
    score=sum(breakdown.values())
    return score,rec,ok,breakdown

def gaming_score_scan(scan):
    """Score gaming strict : configuration mesurable + profil du jeu + benchmark réel."""
    breakdown={'Windows gaming':0,'Profil Fortnite':0,'RAM / BIOS':0,'Benchmark réel':0}
    rec=[]

    settings=scan.get('settings') or {}
    states=settings.get('feature_states') or {}
    health=scan.get('health') or {}
    net=scan.get('network') or {}

    # Windows gaming : 30 pts.
    if states.get('game') is True:breakdown['Windows gaming']+=10
    else:rec.append({'level':'important','title':'Mode Jeu non confirmé','detail':'Le score gaming attend le Mode Jeu Windows actif.','section':'performance','option':'game'})

    if states.get('captures') is True:breakdown['Windows gaming']+=5
    else:rec.append({'level':'info','title':'Captures en arrière-plan','detail':'Game DVR/captures ne sont pas confirmés désactivés.','section':'performance','option':'captures'})

    if states.get('balanced') is True:breakdown['Windows gaming']+=5
    else:rec.append({'level':'info','title':'Plan d’alimentation gaming','detail':'Le plan Équilibré n’est pas confirmé comme base Ryzen X3D.','section':'performance','option':'balanced'})

    if net.get('status')=='online' and net.get('rss') is not False:breakdown['Windows gaming']+=5
    if health.get('pending_reboot') is not True:breakdown['Windows gaming']+=5

    # Profil Fortnite : 35 pts.
    profile=((scan.get('game_profiles') or {}).get('fortnite') or {})
    if profile.get('installed'):
        breakdown['Profil Fortnite']+=5
        rt=int(profile.get('registry_total') or 0);ro=int(profile.get('registry_ok') or 0)
        ct=int(profile.get('config_total') or 0);co=int(profile.get('config_ok') or 0)
        if rt:breakdown['Profil Fortnite']+=round(15*ro/rt)
        if ct and profile.get('config_exists'):breakdown['Profil Fortnite']+=round(15*co/ct)
        if breakdown['Profil Fortnite']<30:
            rec.append({'level':'info','title':'Profil Fortnite non optimisé','detail':'Le profil compétitif Fortnite n’est pas entièrement appliqué.','section':'games'})
    else:
        rec.append({'level':'info','title':'Fortnite non détecté','detail':'Aucun score de profil Fortnite ne peut être calculé.','section':'games'})

    # RAM / BIOS : 15 pts.
    bios=scan.get('bios') or {}
    hints=bios.get('hints') if isinstance(bios,dict) else []
    if hints:
        breakdown['RAM / BIOS']=5
        rec.append({'level':'important','title':'RAM / BIOS à vérifier','detail':str(hints[0]),'section':'bios'})
    else:
        breakdown['RAM / BIOS']=15

    # Benchmark réel : 20 pts. Pas de bonus gratuit si rien n'a été mesuré.
    history=benchmark_history(1)
    if history:
        last=history[-1]
        avg=float(last.get('avg_fps') or 0)
        low=float(last.get('one_percent_low') or 0)
        low01=float(last.get('point_one_percent_low') or 0)
        duration=max(1.0,float(last.get('duration_seconds') or 60))
        stutters=float(last.get('stutters_33ms') or 0)*60.0/duration
        r1=(low/avg) if avg else 0
        r01=(low01/avg) if avg else 0
        breakdown['Benchmark réel'] += 8 if r1>=0.70 else 6 if r1>=0.55 else 3 if r1>=0.40 else 0
        breakdown['Benchmark réel'] += 5 if r01>=0.50 else 4 if r01>=0.35 else 2 if r01>=0.20 else 0
        breakdown['Benchmark réel'] += 7 if stutters<=1 else 5 if stutters<=5 else 2 if stutters<=10 else 0
        if breakdown['Benchmark réel']<13:
            rec.append({'level':'important','title':'Stabilité Fortnite à améliorer','detail':'Le dernier benchmark montre des 1% lows/0,1% lows ou des stutters perfectibles.','section':'games'})
    else:
        rec.append({'level':'info','title':'Benchmark Fortnite manquant','detail':'20 points gaming restent non validés tant qu’un benchmark en jeu n’a pas été effectué.','section':'games'})

    maxima={'Windows gaming':30,'Profil Fortnite':35,'RAM / BIOS':15,'Benchmark réel':20}
    for key,maxv in maxima.items():breakdown[key]=max(0,min(maxv,int(breakdown[key])))
    return sum(breakdown.values()),breakdown,rec

def complete_gaming_profile_options():
    """Profil PC gaming global : uniquement des changements réversibles et suivis."""
    info=analyze()
    gpu=str(info.get('gpu') or '').casefold()
    cpu=str(info.get('cpu') or '').casefold()
    options=[
        'game','captures','background_apps','mouse_accel_off',
        'p2p_off','net_power','net_eee','tcp_baseline'
    ]
    # Ryzen X3D : conserver Balanced comme base stable ; AMD publie aussi ses tests Ryzen en Balanced.
    if 'x3d' in cpu or 'ryzen' in cpu:options.append('balanced')
    if 'radeon rx' in gpu:options.append('amd_gpu')
    return list(dict.fromkeys(options))

def apply_complete_gaming_profile(root):
    """Applique la base PC, met l'écran au Hz max et A/B teste le profil réseau faible latence."""
    if os.name!='nt':raise RuntimeError('Le profil complet nécessite Windows.')
    report={'options':complete_gaming_profile_options(),'steps':[]}
    report['steps'].append({'name':'Base Windows / GPU / réseau','result':apply_batch(root,report['options'],[])})
    try:
        report['steps'].append({'name':'Fréquence écran','result':set_max_refresh_rate()})
    except Exception as exc:
        report['steps'].append({'name':'Fréquence écran','warning':str(exc)})
    try:
        net=autotune_network_low_latency(root)
        report['network_autotune']=net
        report['steps'].append({'name':'Auto-tune réseau','result':net.get('message')})
    except Exception as exc:
        report['steps'].append({'name':'Auto-tune réseau','warning':str(exc)})
    report['reboot_recommended']=True
    report['note']='Le profil évite les tweaks timer/HPET, les suppressions massives de services, les purges mémoire et les overclocks automatiques. Mesure ensuite Fortnite avant/après.'
    return report

def optimization_research_audit():
    """Catalogue intégré des tweaks vus dans les guides/vidéos et de la politique Acolyte."""
    return [
        {'name':'Pilotes GPU/chipset/BIOS','status':'À maintenir','action':'Diagnostic + liens officiels','reason':'Les pilotes/firmwares et la RAM correctement configurée font partie des fondamentaux mesurables.'},
        {'name':'Mode Jeu / Game DVR','status':'Automatique','action':'Game Mode ON, captures OFF','reason':'Réglages Windows gaming explicites et réversibles.'},
        {'name':'GPU haute performance par jeu','status':'Automatique','action':'Profil Windows par exécutable','reason':'Évite qu’un jeu parte sur le mauvais GPU et reste réversible.'},
        {'name':'HAGS','status':'Mesuré','action':'Activé dans le profil AMD, benchmark conseillé','reason':'Le bénéfice dépend du jeu/pilote ; pas présenté comme gain garanti.'},
        {'name':'RSS + TCP Auto-Tuning Normal','status':'Automatique','action':'Base réseau saine','reason':'Microsoft recommande RSS sur NIC compatible et Auto-Tuning Normal pour les scénarios TCP standards.'},
        {'name':'RSC + Interrupt Moderation','status':'A/B automatique','action':'Test faible latence puis rollback si régression','reason':'Peut réduire la latence au prix de CPU/débit selon le matériel.'},
        {'name':'DNS','status':'Optionnel','action':'Mesure du résolveur uniquement','reason':'Accélère surtout la résolution de noms, pas le ping d’une partie déjà connectée.'},
        {'name':'EXPO/XMP','status':'Diagnostic BIOS','action':'Détection des fréquences, pas d’écriture BIOS','reason':'Gain réel possible, mais l’activation BIOS doit rester contrôlée et testée pour la stabilité.'},
        {'name':'PBO / Curve Optimizer / OC GPU-RAM','status':'Manuel avancé','action':'Non automatisé','reason':'Peut gagner des performances mais implique tension, température et stabilité propres à chaque puce.'},
        {'name':'VBS / Memory Integrity','status':'Avancé','action':'Option séparée avec avertissement sécurité','reason':'Peut avoir un coût selon les charges mais désactiver réduit la sécurité.'},
        {'name':'-LANPLAY','status':'Non appliqué','action':'Audit seulement','reason':'Argument Unreal documenté pour réseau LAN ; il peut augmenter les mises à jour et saturer la bande passante, ce n’est pas un boost Fortnite garanti.'},
        {'name':'-NOSPLASH','status':'Non appliqué','action':'Audit seulement','reason':'Supprime l’image de démarrage ; aucun gain FPS en partie.'},
        {'name':'-NOTEXTURESTREAMING','status':'Non appliqué','action':'Audit seulement','reason':'Charge les textures au lieu de les streamer et peut augmenter fortement l’usage VRAM.'},
        {'name':'-USEALLAVAILABLECORES','status':'Non appliqué','action':'Audit seulement','reason':'Argument Unreal existant, mais pas de preuve qu’il améliore Fortnite actuel sur un 7800X3D.'},
        {'name':'HPET / bcdedit timers / timer hacks','status':'Refusé','action':'Jamais automatique','reason':'Tweaks Internet souvent non mesurés, pouvant dégrader la latence ou perturber l’horloge système.'},
        {'name':'NetworkThrottlingIndex / SystemResponsiveness','status':'Refusé','action':'Jamais automatique','reason':'Clés historiques fréquemment copiées sans validation actuelle et sans bénéfice universel.'},
        {'name':'Purge standby RAM','status':'Refusé','action':'Jamais automatique','reason':'Force Windows à recharger des données et peut provoquer plus de stutters.'},
        {'name':'Custom Windows / services massivement supprimés','status':'Refusé','action':'Pas de debloat destructif','reason':'Les gains sont difficiles à attribuer et le risque de casse, sécurité ou mises à jour est élevé.'},
    ]

COMPETITIVE_SAFE_OPTIONS=(
    'game','captures','balanced','mouse_accel_off',
    'net_power','tcp_baseline','p2p_off'
)

def gaming_research_audit(scan=None):
    """Synthèse des optimisations retenues après validation par sources officielles et mesure locale.
    Les réglages privés/non documentés de pilotes et les tweaks placebo restent exclus.
    """
    scan=scan or {}
    system=scan.get('system') or {}
    settings=scan.get('settings') or {}
    states=settings.get('feature_states') or {}
    gpu=str(system.get('gpu') or '').lower()
    cpu=str(system.get('cpu') or '').lower()
    cards=[]

    def add(key,title,status,detail,action='none',risk='Faible',source=''):
        cards.append({'key':key,'title':title,'status':status,'detail':detail,'action':action,'risk':risk,'source':source})

    add('game_mode','Mode Jeu Windows',
        'OK' if states.get('game') is True else 'À appliquer',
        'Conservé dans le pack compétitif. Réglage Windows réversible.',
        'game','Faible','Microsoft / Windows Gaming')
    add('captures','Captures Game DVR',
        'OK' if states.get('captures') is True else 'À appliquer',
        'Les captures en arrière-plan sont coupées dans le profil compétitif quand elles ne sont pas utilisées.',
        'captures','Faible','Microsoft / Windows Gaming')
    add('balanced','Plan Équilibré Ryzen',
        'OK' if states.get('balanced') is True else 'À appliquer',
        'Sur Ryzen X3D, Acolyte conserve le plan Équilibré comme base stable au lieu de forcer un plan exotique.',
        'balanced','Faible','AMD / Microsoft')
    add('mouse','Accélération souris',
        'OK' if states.get('mouse_accel_off') is True else 'À appliquer',
        'Enhance Pointer Precision est désactivé pour une réponse souris plus prévisible.',
        'mouse_accel_off','Faible','Windows')
    add('network','Base réseau',
        'OK' if states.get('tcp_baseline') is True else 'À appliquer',
        'RSS actif et TCP Auto-Tuning Normal. Les tweaks réseau agressifs restent séparés et mesurables.',
        'tcp_baseline','Faible','Microsoft Networking')

    if 'radeon rx 7' in gpu or 'rx 7900' in gpu:
        add('amd_antilag','AMD Radeon Anti-Lag','À vérifier dans Adrenalin',
            'Anti-Lag est pertinent pour un profil compétitif. Acolyte n’écrit pas de clés Adrenalin privées : réglage guidé via AMD Software.',
            'open_amd','Faible','AMD Radeon Anti-Lag')
        add('amd_chill','AMD Radeon Chill','À laisser désactivé en compétitif',
            'Chill limite dynamiquement les FPS et n’est pas interopérable avec Anti-Lag/Boost. Acolyte ne l’active pas dans le profil compétitif.',
            'open_amd','Faible','AMD Radeon Chill')
        add('hypr_rx','AMD HYPR-RX / AFMF','Optionnel',
            'Compatible RX 7000. Pour le jeu compétitif, Acolyte ne l’active pas automatiquement car la génération d’images augmente le FPS affiché sans remplacer les images réellement rendues.',
            'open_amd','Variable','AMD HYPR-RX')

    add('hags','HAGS',
        'OK' if states.get('hags_on') is True else 'À tester',
        'HAGS reste une option mesurable : Acolyte ne la considère pas comme un gain garanti. Test avant/après recommandé.',
        'hags_on','Variable','Windows Graphics')
    add('windowed_opt','Optimisations jeux fenêtrés','À vérifier',
        'Windows 11 peut réduire la latence de présentation pour les jeux DX10/DX11 fenêtrés ou sans bordure. Réglage guidé, pas de clé privée forcée.',
        'open_graphics','Faible','Microsoft Support')
    add('shader_cache','Cache shaders Fortnite','Uniquement en dépannage',
        'Epic recommande de vider le cache DX/AMD en cas de gros stutters DX12. Acolyte ne le purge pas systématiquement car la recompilation peut provoquer des saccades temporaires.',
        'shader_cache','Faible','Epic Games Support')
    add('fortnite_perf','Fortnite Performance Renderer','Optionnel',
        'Epic recommande le mode Performance pour viser davantage de FPS. Acolyte ne change pas ton renderer automatiquement afin d’éviter d’écraser ton choix DX12/Performance.',
        'fortnite_settings','Faible','Epic Games Support')

    excluded=[
        'HPET / useplatformclock / timer hacks',
        'NetworkThrottlingIndex forcé',
        'désactivation globale de Defender / pare-feu / Windows Update',
        'suppression massive de services Windows',
        'priorités/affinités CPU forcées sans benchmark',
        'overclock ou undervolt automatique',
        'clés privées AMD Adrenalin non documentées',
        'custom Windows modifié automatiquement'
    ]
    return {'cards':cards,'excluded':excluded,'cpu':system.get('cpu'),'gpu':system.get('gpu')}

def apply_competitive_pack(root,scan=None):
    """Pack conservateur et réversible. Les options variables restent hors du lot automatique."""
    if game_process_running().get('running'):
        raise RuntimeError('Ferme Fortnite avant d’appliquer le pack compétitif complet.')
    selected=list(COMPETITIVE_SAFE_OPTIONS)
    result=apply_batch(root,selected,[])
    return {
        'message':'Pack compétitif sûr appliqué.',
        'options':selected,
        'detail':result,
        'note':'HAGS, RSC/Interrupt Moderation, Nagle, VBS, HYPR-RX/AFMF et overclock restent hors du pack automatique et doivent être mesurés ou validés séparément.'
    }

def recommended_options(scan):
    settings=scan.get('settings') or {}
    states=settings.get('feature_states') or {}
    options=[]
    for key in ('game','captures','balanced','net_power'):
        if states.get(key) is False:options.append(key)
    # AMD profile is proposé uniquement si une Radeon RX est détectée et s'il n'est pas déjà actif.
    gpu=str((scan.get('system') or {}).get('gpu','')).lower()
    if 'radeon rx' in gpu and states.get('amd_gpu') is False:
        options.append('amd_gpu')
    return list(dict.fromkeys(options))

def apply_selected(options, root):
    return optimize(root, options)



# === ACOLYTE IN-GAME BENCHMARK ===
# Mesure externe via PresentMon (GameTechDev/PresentMon, licence MIT).
# Aucune injection dans Fortnite, aucune lecture de mémoire du jeu et aucun fichier du jeu modifié.
BENCH_DATA_DIR = Path(os.environ.get('LOCALAPPDATA', str(Path.home()))) / 'AcolyteFortnite' / 'Benchmark'
PRESENTMON_DIR = BENCH_DATA_DIR / 'PresentMon'
PRESENTMON_EXE = PRESENTMON_DIR / 'PresentMon.exe'
PRESENTMON_META = PRESENTMON_DIR / 'metadata.json'
BENCH_HISTORY = BENCH_DATA_DIR / 'history.json'
FORTNITE_PROCESS = 'FortniteClient-Win64-Shipping.exe'
PRESENTMON_REPO_API = 'https://api.github.com/repos/GameTechDev/PresentMon/releases/latest'

def _http_json(url, timeout=25):
    import urllib.request
    req=urllib.request.Request(url,headers={'User-Agent':'Acolyte-Performance','Accept':'application/vnd.github+json'})
    with urllib.request.urlopen(req,timeout=timeout) as response:
        final=response.geturl()
        if not final.startswith('https://api.github.com/'):
            raise RuntimeError('Réponse GitHub inattendue.')
        data=response.read(2*1024*1024+1)
    if len(data)>2*1024*1024:raise RuntimeError('Réponse GitHub trop volumineuse.')
    return json.loads(data.decode('utf-8'))

def presentmon_status():
    meta={}
    try:
        if PRESENTMON_META.exists():meta=json.loads(PRESENTMON_META.read_text(encoding='utf-8'))
    except Exception:meta={}
    return {
        'installed':PRESENTMON_EXE.exists() and PRESENTMON_EXE.stat().st_size>100000,
        'path':str(PRESENTMON_EXE),
        'version':meta.get('version','inconnue'),
        'source':'GameTechDev/PresentMon',
        'license':'MIT'
    }

def install_presentmon():
    """Télécharge uniquement l'exécutable x64 officiel de la dernière release GitHub."""
    import urllib.request, urllib.parse
    meta=_http_json(PRESENTMON_REPO_API)
    tag=str(meta.get('tag_name') or meta.get('name') or 'latest')
    assets=meta.get('assets') or []
    candidates=[]
    for asset in assets:
        name=str(asset.get('name') or '')
        url=str(asset.get('browser_download_url') or '')
        if re.fullmatch(r'PresentMon-[0-9A-Za-z._-]+-x64\.exe',name,re.I) and url.startswith('https://github.com/GameTechDev/PresentMon/releases/download/'):
            candidates.append((name,url,int(asset.get('size') or 0)))
    if not candidates:raise RuntimeError('Exécutable PresentMon x64 officiel introuvable dans la dernière release.')
    name,url,declared_size=candidates[0]
    if declared_size and declared_size>50*1024*1024:raise RuntimeError('Binaire PresentMon anormalement volumineux.')
    PRESENTMON_DIR.mkdir(parents=True,exist_ok=True)
    tmp=PRESENTMON_DIR/(name+'.download')
    req=urllib.request.Request(url,headers={'User-Agent':'Acolyte-Performance'})
    try:
        with urllib.request.urlopen(req,timeout=60) as response, open(tmp,'wb') as out:
            final=urllib.parse.urlparse(response.geturl())
            allowed=(final.scheme=='https' and (
                final.hostname=='github.com' or
                (final.hostname or '').endswith('.githubusercontent.com') or
                final.hostname=='release-assets.githubusercontent.com'))
            if not allowed:raise RuntimeError('Redirection de téléchargement PresentMon refusée.')
            total=0
            while True:
                chunk=response.read(1024*256)
                if not chunk:break
                total+=len(chunk)
                if total>50*1024*1024:raise RuntimeError('Téléchargement PresentMon trop volumineux.')
                out.write(chunk)
        data=tmp.read_bytes()[:2]
        if data!=b'MZ' or tmp.stat().st_size<100000:raise RuntimeError('Le fichier téléchargé n’est pas un exécutable Windows valide.')
        os.replace(tmp,PRESENTMON_EXE)
        PRESENTMON_META.write_text(json.dumps({'version':tag,'asset':name,'source':url,'installed_at':time.strftime('%Y-%m-%d %H:%M:%S')},ensure_ascii=False,indent=2),encoding='utf-8')
    finally:
        try:
            if tmp.exists():tmp.unlink()
        except OSError:pass
    return presentmon_status()

def game_process_running(process_name=FORTNITE_PROCESS):
    try:
        import psutil
        for proc in psutil.process_iter(['name','pid']):
            try:
                if str(proc.info.get('name') or '').lower()==process_name.lower():
                    return {'running':True,'pid':int(proc.info['pid']),'name':proc.info['name']}
            except (psutil.NoSuchProcess,psutil.AccessDenied):pass
    except Exception:pass
    out,err,code=_run(['tasklist.exe','/FI','IMAGENAME eq '+process_name,'/FO','CSV','/NH'])
    if code==0 and process_name.lower() in out.lower():
        return {'running':True,'pid':None,'name':process_name}
    return {'running':False,'pid':None,'name':process_name}

def _percentile(values,p):
    values=sorted(float(x) for x in values)
    if not values:return None
    if len(values)==1:return values[0]
    pos=(len(values)-1)*(float(p)/100.0)
    lo=int(pos);hi=min(lo+1,len(values)-1);frac=pos-lo
    return values[lo]*(1-frac)+values[hi]*frac

def _parse_presentmon_csv(path):
    """Analyse uniquement le swapchain principal du jeu.
    Les CSV PresentMon peuvent contenir plusieurs swapchains pour un même processus ;
    mélanger leurs MsBetweenPresents fausse fortement les FPS.
    """
    import csv
    groups={}
    application=''
    frame_column=''
    time_column=''
    headers=[]
    with open(path,'r',encoding='utf-8-sig',errors='replace',newline='') as f:
        reader=csv.DictReader(f)
        headers=reader.fieldnames or []
        for candidate in ('msBetweenPresents','MsBetweenPresents','FrameTime','frameTime'):
            if candidate in headers:
                frame_column=candidate;break
        for candidate in ('TimeInSeconds','TimeInMs','CPUStartTime','CPUStartQPCTime'):
            if candidate in headers:
                time_column=candidate;break
        if not frame_column:
            raise RuntimeError('Colonne de frametime PresentMon introuvable : '+', '.join(headers[:25]))
        for row in reader:
            if not application:application=str(row.get('Application') or '')
            swap=str(row.get('SwapChainAddress') or 'unknown')
            pid=str(row.get('ProcessID') or '')
            key=(pid,swap)
            try:
                value=float(str(row.get(frame_column,'')).replace(',','.'))
            except (TypeError,ValueError):
                continue
            if not 0.05<=value<=5000:
                continue
            timestamp=None
            if time_column:
                try:
                    timestamp=float(str(row.get(time_column,'')).replace(',','.'))
                    if time_column=='TimeInMs':timestamp/=1000.0
                except (TypeError,ValueError):
                    timestamp=None
            groups.setdefault(key,[]).append((timestamp,value))

    usable={k:v for k,v in groups.items() if len(v)>=120}
    if not usable:
        raise RuntimeError('Aucun swapchain PresentMon ne contient assez de frames exploitables.')

    # Le swapchain principal est celui qui a présenté le plus de frames pendant la capture.
    # C'est essentiel pour les jeux qui créent plusieurs surfaces/swapchains.
    selected_key,rows=max(usable.items(),key=lambda kv:len(kv[1]))
    rows=list(rows)

    # Retire uniquement les bords de capture (pas les stutters internes).
    timed=[r for r in rows if r[0] is not None]
    boundary_trimmed=0
    if len(timed)>=120:
        t0=min(x[0] for x in timed);t1=max(x[0] for x in timed)
        # Les premières/dernières centaines de ms peuvent contenir le changement de focus
        # ou un intervalle entamé avant le démarrage de la capture.
        low=t0+0.75
        high=t1-0.25
        trimmed=[r for r in rows if r[0] is not None and low<=r[0]<=high]
        if len(trimmed)>=120:
            boundary_trimmed=len(rows)-len(trimmed);rows=trimmed
    elif len(rows)>140:
        boundary_trimmed=10
        rows=rows[5:-5]

    raw_values=[float(v) for _,v in rows]
    if len(raw_values)<120:
        raise RuntimeError(f'Capture trop courte après nettoyage : {len(raw_values)} images exploitables.')

    median_ft=statistics.median(raw_values)
    raw_worst=max(raw_values)

    # Les versions modernes de PresentMon ont eu des signalements d'énormes pics ETW
    # à très haut FPS (>~400). On ne masque jamais un stutter normal : uniquement un
    # pic extrême, isolé, entouré de frames revenues immédiatement au niveau normal.
    # Chaque suppression est comptée et signalée dans le résultat.
    values=[]
    artifact_spikes=0
    if median_ft<3.5 and len(raw_values)>=5:
        normal_limit=max(12.0,median_ft*6.0)
        artifact_limit=max(120.0,median_ft*50.0)
        for i,v in enumerate(raw_values):
            if 0<i<len(raw_values)-1 and v>=artifact_limit:
                before=raw_values[i-1];after=raw_values[i+1]
                if before<=normal_limit and after<=normal_limit:
                    artifact_spikes+=1
                    continue
            values.append(v)
    else:
        values=raw_values

    if len(values)<120:
        raise RuntimeError('Trop peu de frames après validation de la capture.')

    # FPS moyen = nombre d'images / temps total, équivalent à 1000 / frametime moyen
    # sur un seul swapchain.
    avg_ft=statistics.mean(values)
    fps_values=[1000.0/x for x in values if x>0]
    fps_avg=1000.0/avg_ft if avg_ft else 0.0

    # Même convention que les métriques FPS percentiles modernes de PresentMon :
    # P1 FPS = 1% des frames sont à cette valeur ou moins.
    low1=_percentile(fps_values,1.0)
    low01=_percentile(fps_values,0.1)

    p95=_percentile(values,95)
    p99=_percentile(values,99)
    p999=_percentile(values,99.9)
    median_ft=statistics.median(values)
    sample=values if len(values)<=360 else [values[round(i*(len(values)-1)/359)] for i in range(360)]

    warnings=[]
    if len(usable)>1:
        warnings.append(f'{len(usable)} swapchains détectés ; Acolyte a sélectionné automatiquement le swapchain principal ({selected_key[1]}).')
    if boundary_trimmed:
        warnings.append(f'{boundary_trimmed} frame(s) de bord de capture ignorée(s).')
    if artifact_spikes:
        warnings.append(f'{artifact_spikes} pic(s) ETW extrême(s) isolé(s) filtré(s) à très haut FPS ; pire valeur brute {raw_worst:.1f} ms.')
    if low1 is not None and low1>fps_avg*1.08:
        warnings.append('Le 1% low dépasse anormalement la moyenne : la capture reste suspecte et doit être répétée.')

    return {
        'application':application,'frame_column':frame_column,
        'process_id':selected_key[0],'swapchain':selected_key[1],
        'swapchains_detected':len(usable),
        'frames':len(values),'raw_frames':len(raw_values),
        'duration_seconds':sum(values)/1000.0,
        'avg_fps':fps_avg,'median_fps':1000.0/median_ft if median_ft else 0,
        'one_percent_low':low1 or 0.0,'point_one_percent_low':low01 or 0.0,
        'avg_frametime_ms':avg_ft,'p95_frametime_ms':p95,'p99_frametime_ms':p99,
        'p999_frametime_ms':p999,'worst_frametime_ms':max(values),
        'raw_worst_frametime_ms':raw_worst,
        'stutters_33ms':sum(1 for x in values if x>33.333),
        'stutters_50ms':sum(1 for x in values if x>50),
        'artifact_spikes_filtered':artifact_spikes,
        'boundary_frames_trimmed':boundary_trimmed,
        'capture_warnings':warnings,
        'frametime_sample':sample
    }

def _benchmark_history_read():
    try:
        data=json.loads(BENCH_HISTORY.read_text(encoding='utf-8'))
        return data if isinstance(data,list) else []
    except (FileNotFoundError,ValueError,OSError):return []

def _benchmark_history_write(rows):
    BENCH_DATA_DIR.mkdir(parents=True,exist_ok=True)
    temp=BENCH_HISTORY.with_suffix('.tmp')
    temp.write_text(json.dumps(rows[-30:],ensure_ascii=False,indent=2),encoding='utf-8')
    os.replace(temp,BENCH_HISTORY)

def benchmark_history(limit=10):
    rows=_benchmark_history_read()
    return rows[-max(1,int(limit)):]

def run_game_benchmark(duration=60,label='Libre',process_name=FORTNITE_PROCESS,delay=5):
    status=presentmon_status()
    if not status['installed']:raise RuntimeError('Moteur PresentMon absent. Installe-le depuis l’écran Jeux.')
    duration=int(duration);delay=int(delay)
    if duration not in (30,60,90,120,180):raise ValueError('Durée de benchmark non autorisée.')
    if not 0<=delay<=10:raise ValueError('Délai de benchmark invalide.')
    process=game_process_running(process_name)
    if not process['running']:raise RuntimeError('Fortnite n’est pas lancé. Ouvre le jeu et entre dans une partie avant de démarrer le benchmark.')
    if not is_admin():raise PermissionError('Pour une capture ETW fiable, relance Acolyte avec « Exécuter en tant qu’administrateur ».')
    BENCH_DATA_DIR.mkdir(parents=True,exist_ok=True)
    stamp=time.strftime('%Y%m%d-%H%M%S')
    csv_path=BENCH_DATA_DIR/f'fortnite-{stamp}.csv'
    target_args=['--process_id',str(process['pid'])] if process.get('pid') else ['--process_name',process_name]
    args=[
        str(PRESENTMON_EXE),*target_args,'--output_file',str(csv_path),
        '--delay',str(delay),'--timed',str(duration),'--terminate_after_timed','--terminate_on_proc_exit',
        '--stop_existing_session','--session_name','AcolyteFortniteBenchmark',
        '--set_circular_buffer_size','8192',
        '--v1_metrics','--exclude_dropped','--no_track_gpu','--no_track_input','--no_track_display','--no_console_stats'
    ]
    cpu_samples=[];ram_samples=[]
    flags=getattr(subprocess,'CREATE_NO_WINDOW',0)
    proc=subprocess.Popen(args,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,encoding='utf-8',errors='replace',creationflags=flags)
    try:
        import psutil
        while proc.poll() is None:
            cpu_samples.append(float(psutil.cpu_percent(interval=0.4)))
            ram_samples.append(float(psutil.virtual_memory().percent))
    except Exception:
        try:proc.wait(timeout=duration+20)
        except subprocess.TimeoutExpired:
            proc.kill();raise RuntimeError('PresentMon n’a pas terminé la capture.')
    try:out,err=proc.communicate(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill();out,err=proc.communicate()
    if proc.returncode not in (0,None):
        raise RuntimeError('PresentMon a échoué : '+(err or out or str(proc.returncode))[-1200:])
    if not csv_path.exists():raise RuntimeError('PresentMon n’a créé aucun fichier CSV. Vérifie que Fortnite était réellement en rendu 3D pendant la capture.')
    result=_parse_presentmon_csv(csv_path)
    result.update({
        'timestamp':time.strftime('%Y-%m-%d %H:%M:%S'),'label':str(label)[:40],
        'requested_seconds':duration,'delay_seconds':delay,'process_name':process_name,'csv_path':str(csv_path),
        'cpu_avg_percent':statistics.mean(cpu_samples) if cpu_samples else None,
        'cpu_max_percent':max(cpu_samples) if cpu_samples else None,
        'ram_avg_percent':statistics.mean(ram_samples) if ram_samples else None,
        'engine_version':status.get('version','inconnue')
    })
    rows=_benchmark_history_read();rows.append(result);_benchmark_history_write(rows)
    return result

def compare_game_benchmarks(before,after):
    keys=('avg_fps','one_percent_low','point_one_percent_low')
    delta={}
    for key in keys:
        a=float(before.get(key) or 0);b=float(after.get(key) or 0)
        delta[key]={'before':a,'after':b,'delta':b-a,'percent':((b-a)/a*100) if a else None}
    fa=float(before.get('avg_frametime_ms') or 0);fb=float(after.get('avg_frametime_ms') or 0)
    delta['avg_frametime_ms']={'before':fa,'after':fb,'delta':fb-fa,'percent':((fb-fa)/fa*100) if fa else None}
    return delta


def open_benchmark_folder():
    BENCH_DATA_DIR.mkdir(parents=True,exist_ok=True)
    if os.name!='nt':raise RuntimeError('Ouverture du dossier disponible uniquement sous Windows.')
    os.startfile(str(BENCH_DATA_DIR))
    return str(BENCH_DATA_DIR)


if __name__=='__main__':
    if len(sys.argv)>=3 and sys.argv[1]=='--elevated-batch':
        raise SystemExit(_elevated_batch_main(sys.argv[2]))
