"""Optimisation Windows 11 locale pour Acolyte. Réglages mesurables, prudents et réversibles."""
import ctypes, json, os, re, socket, statistics, subprocess, time
from pathlib import Path

BACKUP_NAME='pc-optimizer-backup.json'

def _run(args, timeout=35):
    p=subprocess.run(args,capture_output=True,text=True,timeout=timeout,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
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
    script=f"""$r=Test-Connection -ComputerName '{host}' -Count {int(count)} -ErrorAction SilentlyContinue
$vals=@($r|ForEach-Object {{$_.ResponseTime}}|Where-Object {{$_ -ne $null}})
$sent={int(count)}
$recv=$vals.Count
if($recv -eq 0){{[ordered]@{{host='{host}';avg=0;min=0;max=0;jitter=0;loss=100;received=0}}}}
else{{
  $jit=0
  if($recv -gt 1){{$diff=@();for($i=1;$i -lt $recv;$i++){{$diff += [math]::Abs([double]$vals[$i]-[double]$vals[$i-1])}};$jit=($diff|Measure-Object -Average).Average}}
  [ordered]@{{host='{host}';avg=($vals|Measure-Object -Average).Average;min=($vals|Measure-Object -Minimum).Minimum;max=($vals|Measure-Object -Maximum).Maximum;jitter=$jit;loss=(100.0*($sent-$recv)/$sent);received=$recv}}
}}"""
    data=_ps_json(script) or {'host':host,'avg':0,'min':0,'max':0,'jitter':0,'loss':100,'received':0}
    for k in ('avg','min','max','jitter','loss'):
        try:data[k]=float(data.get(k,0) or 0)
        except Exception:data[k]=0.0
    return data

def benchmark():
    targets=[]
    gw=_gateway()
    if gw:targets.append(gw)
    targets += ['1.1.1.1','8.8.8.8']
    seen=set();rows=[]
    for host in targets:
        if host in seen:continue
        seen.add(host);rows.append(ping(host))
    return rows

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
