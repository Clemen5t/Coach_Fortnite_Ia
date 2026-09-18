"""Optimisation Windows 11 locale pour Acolyte. Réglages mesurables, prudents et réversibles."""
import ctypes, ipaddress, json, os, re, socket, statistics, subprocess, time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

BACKUP_NAME='pc-optimizer-backup.json'

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
    """Mesure ICMP Windows native, sans dépendre de PowerShell/.NET."""
    host=str(ipaddress.IPv4Address(host))
    count=int(count)
    if not 1 <= count <= 100:raise ValueError('Nombre de requêtes invalide.')
    result={'host':host,'avg':None,'min':None,'max':None,'jitter':None,
            'loss':None,'received':0,'sent':count,'status':'error','detail':''}
    executable=str(Path(os.environ.get('SystemRoot',r'C:\Windows'))/'System32'/'PING.EXE')
    try:
        out,err,code=_run([executable,'-4','-n',str(count),'-w','1500',host],
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
}
RUN_KEY = r'Software\Microsoft\Windows\CurrentVersion\Run'
BALANCED = '381b4222-f694-41f0-9685-ff5bb260df2e'
USB_SUB = '2a737441-1930-4402-8d77-b2bebba308a3'
USB_SETTING = '48e6b7a6-50f5-4782-a5d4-53bb8f07e226'
JOURNAL_NAME = 'pc-optimizer-state-v2.json'
_MUTATION_LOCK = threading.Lock()
OPTIONS = {
    'game': ('Activer le mode Jeu Windows', 'Active les préférences Game Bar du compte courant.'),
    'captures': ('Désactiver les captures Xbox Game Bar', 'Désactive les captures Game Bar ; aucun changement dans OBS.'),
    'balanced': ('Utiliser le plan Équilibré', 'Change le plan actif, sans modifier les fréquences CPU.'),
    'usb': ('Tester sans suspension USB sur secteur', 'Dépannage de déconnexions uniquement ; consommation potentiellement accrue.'),
    'ads': ('Désactiver l’identifiant publicitaire Windows', 'Réglage de confidentialité du compte courant, entièrement restaurable.'),
    'suggestions': ('Réduire les suggestions promotionnelles Windows', 'Désactive deux suggestions du compte courant, entièrement restaurables.'),
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
            key, name = self._registry_target(spec)
            try:
                with self.reg.OpenKey(self.reg.HKEY_CURRENT_USER, key) as handle:
                    value, regtype = self.reg.QueryValueEx(handle, name)
            except FileNotFoundError:
                return {'exists': False}
            if regtype not in (self.reg.REG_DWORD, self.reg.REG_SZ, self.reg.REG_EXPAND_SZ):
                raise ValueError('Type de registre non pris en charge : '+name)
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
        raise ValueError('Type de réglage invalide.')

    def _registry_target(self, spec):
        if spec.get('id') in REG_SETTINGS:return REG_SETTINGS[spec['id']]
        if spec.get('id') == 'startup' and isinstance(spec.get('name'), str) and spec['name'] and '\x00' not in spec['name']:
            return RUN_KEY, spec['name']
        raise ValueError('Réglage de registre non autorisé.')

    def write(self, spec, value):
        kind = spec['kind']
        if kind == 'registry':
            key, name = self._registry_target(spec)
            if value['exists']:
                with self.reg.CreateKeyEx(self.reg.HKEY_CURRENT_USER, key, 0, self.reg.KEY_SET_VALUE) as handle:
                    self.reg.SetValueEx(handle, name, 0, value['type'], value['value'])
            else:
                try:
                    with self.reg.OpenKey(self.reg.HKEY_CURRENT_USER, key, 0, self.reg.KEY_SET_VALUE) as handle:
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
        else:raise ValueError('Type de réglage invalide.')
        if self.read(spec) != value:raise RuntimeError('La vérification du réglage a échoué.')


def _save_state(path, data):
    import tempfile
    path = Path(path)
    fd, temp = tempfile.mkstemp(prefix='pc-state-', suffix='.tmp', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.flush();os.fsync(f.fileno())
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):os.unlink(temp)


def _load_state(root, backend):
    path = Path(root)/JOURNAL_NAME
    if not path.exists():
        if (Path(root)/BACKUP_NAME).exists():
            raise RuntimeError('Une sauvegarde de l’ancien optimiseur existe. Elle est incomplète : aucun nouveau réglage ne sera appliqué ni cette sauvegarde écrasée. Conserve pc-optimizer-backup.json pour examiner la restauration des anciens changements.')
        return {'schema': 2, 'identity': backend.identity, 'entries': []}
    state = json.loads(path.read_text(encoding='utf-8'))
    if state.get('schema') != 2 or state.get('identity') != backend.identity or not isinstance(state.get('entries'), list):
        raise ValueError('Sauvegarde invalide ou créée sur un autre PC/compte Windows.')
    return state


@contextlib.contextmanager
def _exclusive(root):
    # Verrou de processus + verrou de fichier, libérés automatiquement après un crash.
    if not _MUTATION_LOCK.acquire(blocking=False):raise RuntimeError('Une modification est déjà en cours.')
    try:
        with open(Path(root)/'pc-optimizer.lock', 'a+b') as handle:
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
    path = Path(root)/JOURNAL_NAME
    with _exclusive(root):
        state = _load_state(root, backend)
        if any(e.get('pending') for e in state['entries']):
            raise RuntimeError('Une opération a été interrompue. Clique sur Restaurer avant de continuer.')
        # Lire tous les réglages avant toute modification.
        before = [(spec, value, backend.read(spec)) for spec, value in changes]
        touched = []
        try:
            for spec, value, previous in before:
                if previous == value:continue
                entry = next((e for e in state['entries'] if e['spec'] == spec), None)
                if entry is not None and previous != entry['applied']:
                    raise RuntimeError('Ce réglage a changé hors d’Acolyte. Restaure d’abord la sauvegarde.')
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
            if failures:raise RuntimeError('Échec ; restauration partielle. Sauvegarde conservée. '+str(exc)+' / '+'; '.join(failures)) from exc
            raise RuntimeError('Échec ; changements de cette opération annulés. '+str(exc)) from exc
        return {'changed': len(touched), 'backup': str(path)}


def optimize(root, options=None, backend=None):
    options = list(options if options is not None else ['game'])
    if not options or not set(options).issubset(OPTIONS):raise ValueError('Sélectionne au moins un réglage valide.')
    backend = backend or WindowsSettings()
    changes = []
    def dword(key, value):changes.append(({'kind': 'registry', 'id': key}, {'exists': True, 'type': 4, 'value': value}))
    if 'game' in options:
        dword('game_auto', 1);dword('game_allow', 1)
    if 'captures' in options:
        dword('capture', 0);dword('dvr', 0)
    if 'ads' in options:dword('ads', 0)
    if 'suggestions' in options:
        dword('suggestions', 0);dword('suggestions_2', 0)
    if 'balanced' in options:changes.append(({'kind': 'power'}, BALANCED))
    if 'usb' in options:
        plan = BALANCED if 'balanced' in options else backend.read({'kind': 'power'})
        changes.append(({'kind': 'usb', 'plan': plan}, 0))
    result = _apply_changes(root, changes, backend)
    if not result['changed']:return 'Les réglages sélectionnés ont déjà les valeurs demandées. Aucun changement.'
    return f"{result['changed']} réglage(s) modifié(s) et vérifié(s).\nSauvegarde initiale conservée : {result['backup']}\nAucun gain de FPS n’est garanti. Teste les mêmes usages avant/après."


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
                _save_state(Path(root)/JOURNAL_NAME, state)
            except Exception as exc:errors.append(str(exc))
        if errors:raise RuntimeError('Restauration partielle ; sauvegarde conservée pour réessayer : '+'; '.join(errors))
        archive = Path(root)/('pc-optimizer-restored-'+uuid.uuid4().hex+'.json')
        os.replace(Path(root)/JOURNAL_NAME, archive)
        return 'Tous les réglages suivis par cette version ont été restaurés et vérifiés.\nLes désinstallations et les changements manuels dans Windows/AMD/BIOS ne sont pas inclus.'


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

def detected_games():
    games=[]
    manifests=Path(os.environ.get('ProgramData',r'C:\ProgramData'))/'Epic'/'EpicGamesLauncher'/'Data'/'Manifests'
    if manifests.exists():
        for path in manifests.glob('*.item'):
            try:
                data=json.loads(path.read_text(encoding='utf-8'))
                name=str(data.get('DisplayName') or data.get('AppName') or '').strip()
                loc=str(data.get('InstallLocation') or '').strip()
                if name and loc:games.append({'launcher':'Epic Games','name':name,'path':loc})
            except Exception:pass
    # Jeux connus présents sans dépendre d'un launcher précis.
    candidates=[
        ('Fortnite',Path(os.environ.get('ProgramFiles',r'C:\Program Files'))/'Epic Games'/'Fortnite'),
        ('Fortnite',Path('C:/Program Files/Epic Games/Fortnite')),
    ]
    known={(g['name'].lower(),g['path'].lower()) for g in games}
    for name,path in candidates:
        if path.exists() and (name.lower(),str(path).lower()) not in known:
            games.append({'launcher':'Détection locale','name':name,'path':str(path)})
    return games

def bios_diagnostics():
    data=_strict_json("""$ram=@(Get-CimInstance Win32_PhysicalMemory|Select Manufacturer,PartNumber,Capacity,Speed,ConfiguredClockSpeed)
$fw='Inconnu'
try{$fw=(Get-ComputerInfo -Property BiosFirmwareType).BiosFirmwareType}catch{}
$virt=(Get-CimInstance Win32_Processor|Select -First 1 VirtualizationFirmwareEnabled,VMMonitorModeExtensions,SecondLevelAddressTranslationExtensions)
[ordered]@{firmware=$fw;ram=$ram;virtualization=$virt}""")
    rows=_rows(data.get('ram') if isinstance(data,dict) else None)
    hint=[]
    for row in rows:
        try:
            rated=int(row.get('Speed') or 0);configured=int(row.get('ConfiguredClockSpeed') or 0)
            if rated and configured and rated>configured:
                hint.append(f"RAM {row.get('PartNumber','')}: {configured} MT/s configurés pour {rated} MT/s annoncés. Vérifie EXPO/XMP dans le BIOS.")
        except Exception:pass
    note='\n'.join(hint) if hint else 'La vitesse déclarée par Windows ne suffit pas à confirmer EXPO/XMP ; vérifie le BIOS pour une confirmation.'
    return note+'\n\n'+json.dumps(data,ensure_ascii=False,indent=2)

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
}


def open_panel(name):
    import webbrowser
    target = LINKS[name]
    if target.startswith('https:'):webbrowser.open(target)
    elif os.name == 'nt':os.startfile(target)
    else:raise RuntimeError('Ce panneau nécessite Windows.')
