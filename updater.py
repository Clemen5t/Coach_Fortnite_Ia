"""Mises à jour depuis un dépôt GitHub public, sans Git ni jeton."""
import io
import json
import os
import hashlib
from pathlib import Path
import re
import shutil
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

if __name__=='__main__':
    import sys
    if sys.argv[1:]==['--recover']:
        if recover(Path(__file__).resolve().parent):print('Mise à jour interrompue : ancienne version restaurée.')
