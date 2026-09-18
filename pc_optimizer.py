"""Optimisation Windows 11 locale pour Acolyte. Réglages mesurables, prudents et réversibles."""
import ctypes, ipaddress, json, os, re, socket, statistics, subprocess, time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

BACKUP_NAME='pc-optimizer-backup.json'

def _run(args, timeout=35):
    p=subprocess.run(args,capture_output=True,text=True,errors="replace",timeout=timeout,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    return (p.stdout or '').strip(),(p.stderr or '').strip(),p.returncode

def _ps(script, timeout=40):
    encoded=__import__('base64').b64encode(script.encode('utf-16le')).decode()
    return _run(['powershell.exe','-NoProfile','-ExecutionPolicy','Bypass','-EncodedCommand',encoded],timeout)

def is_admin():
    try:return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:return False

def _ps_json(script):
    out,err,code=_ps(script+' | ConvertTo-Json -Depth 6 -Compress')
    if code or not out:return None
    try:return json.loads(out)
    except Exception:return None

def analyze():
    info=_ps_json("""$cpu=(Get-CimInstance Win32_Processor|Select -First 1 -Expand Name)
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


def backup(root):
    path=Path(root)/BACKUP_NAME
    script="""$b=[ordered]@{}
$b.power=(powercfg /getactivescheme|Out-String)
$b.hags=(Get-ItemProperty 'HKLM:\\SYSTEM\\CurrentControlSet\\Control\\GraphicsDrivers' -Name HwSchMode -ErrorAction SilentlyContinue).HwSchMode
$b.game=(Get-ItemProperty 'HKCU:\\Software\\Microsoft\\GameBar' -ErrorAction SilentlyContinue|Select AllowAutoGameMode,AutoGameModeEnabled)
$b.dvr=(Get-ItemProperty 'HKCU:\\System\\GameConfigStore' -ErrorAction SilentlyContinue).GameDVR_Enabled
$b.capture=(Get-ItemProperty 'HKCU:\\Software\\Microsoft\\Windows\\CurrentVersion\\GameDVR' -ErrorAction SilentlyContinue).AppCaptureEnabled
$b.rss=@(Get-NetAdapterRss -ErrorAction SilentlyContinue|Select Name,Enabled)
$b.rsc=@(Get-NetAdapterRsc -ErrorAction SilentlyContinue|Select Name,IPv4Enabled,IPv6Enabled)
$b"""
    data=_ps_json(script) or {}
    path.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')
    return path

def optimize(root, profile='Auto recommandé'):
    if not is_admin():raise PermissionError("L'optimisation nécessite Acolyte lancé en administrateur.")
    backup(root)
    competitive='Compétitif' in profile
    script="""New-Item 'HKCU:\\Software\\Microsoft\\GameBar' -Force|Out-Null
Set-ItemProperty 'HKCU:\\Software\\Microsoft\\GameBar' AllowAutoGameMode 1 -Type DWord -Force
Set-ItemProperty 'HKCU:\\Software\\Microsoft\\GameBar' AutoGameModeEnabled 1 -Type DWord -Force
New-Item 'HKLM:\\SYSTEM\\CurrentControlSet\\Control\\GraphicsDrivers' -Force|Out-Null
Set-ItemProperty 'HKLM:\\SYSTEM\\CurrentControlSet\\Control\\GraphicsDrivers' HwSchMode 2 -Type DWord -Force
New-Item 'HKCU:\\System\\GameConfigStore' -Force|Out-Null
Set-ItemProperty 'HKCU:\\System\\GameConfigStore' GameDVR_Enabled 0 -Type DWord -Force
New-Item 'HKCU:\\Software\\Microsoft\\Windows\\CurrentVersion\\GameDVR' -Force|Out-Null
Set-ItemProperty 'HKCU:\\Software\\Microsoft\\Windows\\CurrentVersion\\GameDVR' AppCaptureEnabled 0 -Type DWord -Force
powercfg /setactive SCHEME_BALANCED|Out-Null
netsh int tcp set global rss=enabled|Out-Null
netsh int tcp set global autotuninglevel=normal|Out-Null
Get-NetAdapter -Physical -ErrorAction SilentlyContinue|Where-Object Status -eq 'Up'|ForEach-Object{
 try{Enable-NetAdapterRss -Name $_.Name -ErrorAction Stop}catch{}
 try{Set-NetAdapterPowerManagement -Name $_.Name -AllowComputerToTurnOffDevice Disabled -ErrorAction Stop}catch{}
 $p=Get-NetAdapterAdvancedProperty -Name $_.Name -ErrorAction SilentlyContinue|Where-Object {$_.DisplayName -match 'Energy.Efficient|Green Ethernet|Gigabit Lite|Power Saving|Économie.*énergie'}
 foreach($x in $p){try{if($x.ValidDisplayValues -contains 'Disabled'){Set-NetAdapterAdvancedProperty -Name $_.Name -RegistryKeyword $x.RegistryKeyword -DisplayValue 'Disabled' -NoRestart -ErrorAction Stop}}catch{}}
}"""
    out,err,code=_ps(script,60)
    if code:raise RuntimeError(err or out or 'Optimisation Windows échouée.')
    _run(['ipconfig','/flushdns'])
    return {'profile':profile,'competitive':competitive,'backup':str(Path(root)/BACKUP_NAME)}

def restore(root):
    path=Path(root)/BACKUP_NAME
    if not path.exists():raise FileNotFoundError('Aucune sauvegarde PC trouvée.')
    b=json.loads(path.read_text(encoding='utf-8'))
    power=b.get('power','');m=re.search(r'[0-9a-fA-F-]{36}',power or '')
    if m:_run(['powercfg','/setactive',m.group(0)])
    h=b.get('hags')
    if h is None:_ps("Remove-ItemProperty 'HKLM:\\SYSTEM\\CurrentControlSet\\Control\\GraphicsDrivers' HwSchMode -ErrorAction SilentlyContinue")
    else:_ps("Set-ItemProperty 'HKLM:\\SYSTEM\\CurrentControlSet\\Control\\GraphicsDrivers' HwSchMode "+str(int(h))+" -Type DWord -Force")
    for row in b.get('rss') or []:
        name=str(row.get('Name','')).replace("'","''")
        cmd='Enable' if row.get('Enabled') else 'Disable'
        _ps(f"{cmd}-NetAdapterRss -Name '{name}' -ErrorAction SilentlyContinue")
    for row in b.get('rsc') or []:
        name=str(row.get('Name','')).replace("'","''");v4='$true' if row.get('IPv4Enabled') else '$false';v6='$true' if row.get('IPv6Enabled') else '$false'
        _ps(f"Set-NetAdapterRsc -Name '{name}' -IPv4Enabled {v4} -IPv6Enabled {v6} -Confirm:$false -ErrorAction SilentlyContinue")
    return True
