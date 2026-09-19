"""Mises à jour depuis un dépôt GitHub public, sans Git ni jeton."""
import io
import json
import os
import hashlib
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import zipfile

APP_ID = 'coach-fortnite-local'
FILES = {'coach.py','local_ai.py','pc_optimizer.py','updater.py','requirements.txt','Lancer.bat',
         'Installer-modele.bat','LIRE-MOI.txt','README.md','VERSION',
         'update-manifest.json','test_local_ai.py','test_updater.py','.gitignore'}
REQUIRED = {'coach.py','local_ai.py','updater.py','requirements.txt','VERSION','update-manifest.json','Lancer.bat'}
# Compatibilité : un ancien updater (<=1.2.6) ne connaît pas encore pc_optimizer.py.
# Le bootstrap ci-dessous autorise uniquement ce nouveau module officiel lors de la transition.
BOOTSTRAP_FILES = {'pc_optimizer.py','AcolyteLauncher.pyw'}
LIMIT = 12 * 1024 * 1024

def updater_data_dir(root):
    """État de l'updater dans un dossier utilisateur réellement inscriptible.
    Aucun fichier d'état n'est requis dans le dossier de l'application.
    """
    root=Path(root).resolve()
    key=hashlib.sha256(str(root).casefold().encode('utf-8')).hexdigest()[:16]
    candidates=[
        Path(os.environ.get('LOCALAPPDATA') or '')/'AcolyteFortnite'/'Updater'/key,
        Path.home()/'AppData'/'Local'/'AcolyteFortnite'/'Updater'/key,
        Path(tempfile.gettempdir())/'AcolyteFortnite-Updater'/key,
    ]
    last=None
    for folder in candidates:
        if not str(folder):continue
        try:
            folder.mkdir(parents=True,exist_ok=True)
            probe=folder/'.write-test'
            probe.write_text('ok',encoding='utf-8')
            probe.unlink()
            return folder
        except OSError as exc:
            last=exc
    raise PermissionError('Aucun dossier utilisateur inscriptible pour les mises à jour.') from last

def updater_state_file(root):
    return updater_data_dir(root)/'update-state.json'

def updater_pending_file(root):
    return updater_data_dir(root)/'pending.json'


def read_json(path, default=None):
    try:return json.loads(Path(path).read_text(encoding='utf-8'))
    except (FileNotFoundError,PermissionError,OSError,json.JSONDecodeError):
        return default

def write_json(path, data):
    path=Path(path)
    path.parent.mkdir(parents=True,exist_ok=True)
    tmp=path.with_name(path.name+'.tmp')
    try:
        tmp.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')
        os.replace(tmp,path)
    finally:
        try:
            if tmp.exists():tmp.unlink()
        except OSError:pass

def normalize_repo(value):
    value=value.strip().rstrip('/')
    if value.startswith('https://github.com/'):value=value[len('https://github.com/'):]
    if value.endswith('.git'):value=value[:-4]
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9-]*/[A-Za-z0-9_.-]+',value):
        raise ValueError('Indique un dépôt GitHub public : https://github.com/compte/depot')
    if value.split('/')[1] in ('.','..'):raise ValueError('Nom de dépôt invalide.')
    return value

class SafeRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,req,fp,code,msg,headers,newurl):
        parsed=urllib.parse.urlparse(newurl)
        if parsed.scheme!='https' or parsed.hostname not in {'api.github.com','codeload.github.com'}:
            raise ValueError('Redirection de téléchargement refusée.')
        return super().redirect_request(req,fp,code,msg,headers,newurl)

def fetch(url):
    req=urllib.request.Request(url,headers={'User-Agent':'CoachFortnite-Updater','Accept':'application/vnd.github+json'})
    try:
        with urllib.request.build_opener(SafeRedirect()).open(req,timeout=25) as r:
            data=r.read(LIMIT+1)
        if len(data)>LIMIT:raise ValueError('Archive trop volumineuse.')
        return data
    except urllib.error.HTTPError as e:
        if e.code==404:raise RuntimeError('Dépôt introuvable ou privé. Cette version utilise les dépôts publics.') from None
        if e.code in (403,429):raise RuntimeError('Limite GitHub atteinte. Réessaie plus tard.') from None
        raise

def unpack(data):
    """Ne jamais extraire un chemin arbitraire de l'archive."""
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        infos=z.infolist()
        if len(infos)>1000 or sum(i.file_size for i in infos)>LIMIT:
            raise ValueError('Archive trop volumineuse.')
        candidates=[i.filename for i in infos if i.filename.endswith('/update-manifest.json')]
        if len(candidates)!=1:raise ValueError('Manifeste de mise à jour absent ou ambigu.')
        base=candidates[0][:-len('update-manifest.json')]
        manifest=json.loads(z.read(candidates[0]))
        if manifest.get('app_id')!=APP_ID or manifest.get('schema')!=1:
            raise ValueError('Cette archive ne correspond pas à Coach Fortnite.')
        names=manifest.get('files')
        if not isinstance(names,list) or any(not isinstance(n,str) for n in names):raise ValueError('Liste de fichiers invalide.')
        allowed=FILES|BOOTSTRAP_FILES
        if len(names)!=len(set(names)) or not REQUIRED.issubset(names) or not set(names).issubset(allowed):
            raise ValueError('Fichiers de mise à jour non autorisés ou manquants.')
        result={}
        for name in names:
            matches=[i for i in infos if i.filename==base+name]
            if len(matches)!=1:raise ValueError('Fichier absent ou dupliqué : '+name)
            info=matches[0]
            if (info.external_attr>>16)&0o170000==0o120000:raise ValueError('Lien symbolique refusé.')
            result[name]=z.read(info)
            if name.endswith('.py'):compile(result[name],name,'exec')
        version=result['VERSION'].decode().strip()
        if not re.fullmatch(r'\d+\.\d+\.\d+',version):raise ValueError('Version invalide.')
        return result,version

def plan(root,repo,branch,download=fetch):
    repo=normalize_repo(repo);branch=branch.strip() or 'main'
    meta=json.loads(download('https://api.github.com/repos/'+repo+'/commits/'+urllib.parse.quote(branch,safe='')))
    sha=meta.get('sha','')
    if not re.fullmatch('[a-f0-9]{40}',sha):raise ValueError('Révision GitHub invalide.')
    state=read_json(updater_state_file(root),{})
    if state.get('repo')==repo and state.get('sha')==sha:return None
    files,version=unpack(download('https://codeload.github.com/'+repo+'/zip/'+sha))
    return {'repo':repo,'sha':sha,'version':version,'files':files}

def restore(root,journal,backup_root=None):
    root=Path(root)
    backup_root=Path(backup_root) if backup_root is not None else updater_data_dir(root)
    backup=backup_root/journal['backup']
    if not re.fullmatch(r'backup-[a-f0-9]+',journal['backup']):raise ValueError('Sauvegarde invalide.')
    for name,existed in journal['old'].items():
        if name not in FILES:raise ValueError('Restauration invalide.')
        target=root/name
        if existed:shutil.copyfile(backup/name,target)
        elif target.exists():target.unlink()

def _recover_legacy(root):
    """Récupère au mieux une mise à jour interrompue par une ancienne version."""
    root=Path(root)
    legacy=root/'.updates'
    path=legacy/'pending.json'
    try:journal=read_json(path)
    except (OSError,PermissionError):return False
    if not journal:return False
    restore(root,journal,legacy)
    try:path.unlink()
    except OSError:pass
    return True

def recover(root):
    root=Path(root)
    path=updater_pending_file(root)
    journal=read_json(path)
    if journal:
        restore(root,journal);path.unlink()
        return True
    return _recover_legacy(root)

def apply(root,update,replace=os.replace):
    root=Path(root).resolve();recover(root)
    folder=updater_data_dir(root)
    backup=folder/('backup-'+uuid.uuid4().hex);backup.mkdir()
    contents=dict(update['files'])
    if not set(contents).issubset(FILES):raise ValueError('Fichier non autorisé.')
    old={}
    for name in contents:
        target=root/name
        if target.is_symlink():raise ValueError('Fichier local symbolique refusé.')
        old[name]=target.exists()
        if old[name]:shutil.copyfile(target,backup/name)
    journal={'backup':backup.name,'old':old}
    pending=updater_pending_file(root)
    with tempfile.TemporaryDirectory(prefix='stage-',dir=folder) as stage:
        for name,data in contents.items():(Path(stage)/name).write_bytes(data)
        write_json(pending,journal)
        try:
            for name in contents:replace(Path(stage)/name,root/name)
            write_json(updater_state_file(root),{k:update[k] for k in ('repo','sha','version')})
            pending.unlink()
        except Exception:
            restore(root,journal)
            try:pending.unlink()
            except OSError:pass
            raise
    # L'ancien .update-state.json éventuel est volontairement ignoré.
    # Il peut appartenir à une ancienne ACL/admin ; ne jamais le toucher.
    return backup


# === Acolyte.exe releases ===
EXE_LIMIT = 450 * 1024 * 1024
EXE_NAME = 'Acolyte.exe'
EXE_HASH_NAME = 'Acolyte.exe.sha256'

def version_tuple(value):
    text=str(value or '').strip().lstrip('vV')
    if not re.fullmatch(r'\d+\.\d+\.\d+',text):
        raise ValueError('Version invalide : '+str(value))
    return tuple(int(x) for x in text.split('.'))

class ReleaseRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,req,fp,code,msg,headers,newurl):
        parsed=urllib.parse.urlparse(newurl)
        host=(parsed.hostname or '').lower()
        allowed=(
            host=='github.com' or
            host=='release-assets.githubusercontent.com' or
            host.endswith('.githubusercontent.com')
        )
        if parsed.scheme!='https' or not allowed:
            raise ValueError('Redirection de release refusée.')
        return super().redirect_request(req,fp,code,msg,headers,newurl)

def _release_json(url):
    req=urllib.request.Request(url,headers={
        'User-Agent':'Acolyte-Updater',
        'Accept':'application/vnd.github+json'
    })
    try:
        with urllib.request.urlopen(req,timeout=25) as response:
            data=response.read(2*1024*1024+1)
    except urllib.error.HTTPError as exc:
        if exc.code==404:return None
        if exc.code in (403,429):raise RuntimeError('Limite GitHub atteinte. Réessaie plus tard.') from None
        raise
    if len(data)>2*1024*1024:raise ValueError('Réponse release trop volumineuse.')
    return json.loads(data.decode('utf-8'))

def plan_exe(repo,current_version,allow_equal=False,release_json=_release_json):
    repo=normalize_repo(repo)
    meta=release_json('https://api.github.com/repos/'+repo+'/releases/latest')
    if not meta:return None
    if meta.get('draft') or meta.get('prerelease'):return None
    version=str(meta.get('tag_name') or '').lstrip('vV').strip()
    remote=version_tuple(version);current=version_tuple(current_version)
    if remote<current or (remote==current and not allow_equal):return None
    assets={str(a.get('name')):a for a in (meta.get('assets') or []) if isinstance(a,dict)}
    exe=assets.get(EXE_NAME);digest=assets.get(EXE_HASH_NAME)
    if not exe or not digest:
        raise RuntimeError('La release '+version+' ne contient pas Acolyte.exe et sa somme SHA-256.')
    exe_url=str(exe.get('browser_download_url') or '')
    hash_url=str(digest.get('browser_download_url') or '')
    if not exe_url.startswith('https://github.com/') or not hash_url.startswith('https://github.com/'):
        raise RuntimeError('URL de release GitHub invalide.')
    size=int(exe.get('size') or 0)
    if size and not 1_000_000<=size<=EXE_LIMIT:
        raise RuntimeError('Taille de Acolyte.exe anormale.')
    return {'repo':repo,'version':version,'exe_url':exe_url,'hash_url':hash_url,'size':size}

def _download_release(url,limit):
    req=urllib.request.Request(url,headers={'User-Agent':'Acolyte-Updater'})
    opener=urllib.request.build_opener(ReleaseRedirect())
    with opener.open(req,timeout=90) as response:
        total=0;parts=[]
        while True:
            chunk=response.read(1024*512)
            if not chunk:break
            total+=len(chunk)
            if total>limit:raise RuntimeError('Téléchargement de release trop volumineux.')
            parts.append(chunk)
    return b''.join(parts)

def stage_exe_release(update,root):
    version=update['version']
    folder=updater_data_dir(root)/'Exe'/version
    folder.mkdir(parents=True,exist_ok=True)
    staged=folder/(EXE_NAME+'.new')
    expected_text=_download_release(update['hash_url'],64*1024).decode('utf-8','replace')
    match=re.search(r'\b([A-Fa-f0-9]{64})\b',expected_text)
    if not match:raise RuntimeError('Somme SHA-256 de la release invalide.')
    expected=match.group(1).lower()
    data=_download_release(update['exe_url'],EXE_LIMIT)
    if len(data)<1_000_000 or data[:2]!=b'MZ':
        raise RuntimeError('Acolyte.exe téléchargé n’est pas un exécutable Windows valide.')
    actual=hashlib.sha256(data).hexdigest()
    if actual!=expected:
        raise RuntimeError('La vérification SHA-256 de Acolyte.exe a échoué.')
    tmp=staged.with_suffix('.tmp')
    tmp.write_bytes(data);os.replace(tmp,staged)
    return staged

def exe_install_dir():
    base=Path(os.environ.get('LOCALAPPDATA') or (Path.home()/'AppData'/'Local'))
    folder=base/'AcolyteFortnite'/'App'
    folder.mkdir(parents=True,exist_ok=True)
    return folder

def _create_exe_shortcut(target):
    if os.name!='nt':return
    target=Path(target).resolve()
    script=(
        "$ErrorActionPreference='Stop';"
        "$desk=[Environment]::GetFolderPath('Desktop');"
        "$ws=New-Object -ComObject WScript.Shell;"
        "$lnk=Join-Path $desk 'Acolyte Fortnite.lnk';"
        "$s=$ws.CreateShortcut($lnk);"
        "$s.TargetPath='"+str(target).replace("'","''")+"';"
        "$s.WorkingDirectory='"+str(target.parent).replace("'","''")+"';"
        "$s.IconLocation='"+str(target).replace("'","''")+",0';"
        "$s.Description='Acolyte Fortnite';"
        "$s.Save()"
    )
    subprocess.run(
        ['powershell.exe','-NoProfile','-ExecutionPolicy','Bypass','-Command',script],
        capture_output=True,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0),timeout=30
    )

def _schedule_self_replace(staged,current_exe):
    staged=Path(staged).resolve();current=Path(current_exe).resolve()
    helper=updater_data_dir(current.parent)/('replace-'+uuid.uuid4().hex+'.ps1')
    pid=os.getpid()
    ps1=f"""$ErrorActionPreference='Stop'
$pidToWait={pid}
$src={json.dumps(str(staged))}
$dst={json.dumps(str(current))}
try {{ Wait-Process -Id $pidToWait -ErrorAction SilentlyContinue }} catch {{}}
Start-Sleep -Milliseconds 600
$ok=$false
for($i=0;$i -lt 30;$i++){{
  try {{
    Copy-Item -LiteralPath $src -Destination $dst -Force
    $ok=$true
    break
  }} catch {{
    Start-Sleep -Milliseconds 500
  }}
}}
if(-not $ok){{ exit 12 }}
Start-Process -FilePath $dst -WorkingDirectory (Split-Path -Parent $dst)
Remove-Item -LiteralPath $src -Force -ErrorAction SilentlyContinue
Remove-Item -LiteralPath $PSCommandPath -Force -ErrorAction SilentlyContinue
"""
    helper.write_text(ps1,encoding='utf-8')
    subprocess.Popen(
        ['powershell.exe','-NoProfile','-ExecutionPolicy','Bypass','-WindowStyle','Hidden','-File',str(helper)],
        cwd=str(current.parent),
        creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0)
    )

def install_exe_release(update,root,current_exe=None):
    """Installe Acolyte.exe dans LocalAppData ou remplace l'EXE en cours après sa fermeture."""
    root=Path(root).resolve()
    staged=stage_exe_release(update,root)
    target=exe_install_dir()/EXE_NAME
    current=Path(current_exe).resolve() if current_exe else None

    if current is not None and current==target.resolve():
        _schedule_self_replace(staged,current)
        return {'version':update['version'],'target':str(target),'mode':'self_update'}

    tmp=target.with_suffix('.exe.tmp')
    shutil.copyfile(staged,tmp);os.replace(tmp,target)
    _create_exe_shortcut(target)
    try:staged.unlink()
    except OSError:pass
    if os.name=='nt':
        os.startfile(str(target))
    return {'version':update['version'],'target':str(target),'mode':'transition'}

if __name__=='__main__':
    import sys
    if sys.argv[1:]==['--recover']:
        if recover(Path(__file__).resolve().parent):print('Mise à jour interrompue : ancienne version restaurée.')
