import io,json,tempfile,unittest,zipfile
from pathlib import Path
from unittest.mock import patch
import updater

def archive(extra=None):
    files={name:b'# valid\n' for name in updater.REQUIRED}
    files['VERSION']=b'0.3.1\n'
    manifest={'app_id':updater.APP_ID,'schema':1,'files':list(files)}
    if extra:manifest['files'].append(extra)
    files['update-manifest.json']=json.dumps(manifest).encode()
    stream=io.BytesIO()
    with zipfile.ZipFile(stream,'w') as z:
        for name,data in files.items():z.writestr('repo-abc/'+name,data)
        if extra:z.writestr('repo-abc/'+extra,b'bad')
    return stream.getvalue()

class Tests(unittest.TestCase):
    def test_repo(self):
        self.assertEqual(updater.normalize_repo('https://github.com/name/coach.git'),'name/coach')
        for s in ['https://evil.example/name/repo','name/../x','name/..']:
            with self.assertRaises(ValueError):updater.normalize_repo(s)
    def test_manifest(self):
        files,version=updater.unpack(archive());self.assertEqual(version,'0.3.1')
        for bad in ['settings.json','../outside.py','.venv/python.exe']:
            with self.assertRaises(ValueError):updater.unpack(archive(bad))
    def test_pinned_download(self):
        urls=[]
        def download(url):
            urls.append(url)
            return json.dumps({'sha':'a'*40}).encode() if len(urls)==1 else archive()
        with tempfile.TemporaryDirectory() as folder:
            result=updater.plan(folder,'name/coach','main',download)
            self.assertTrue(urls[-1].endswith('/'+'a'*40));self.assertEqual(result['version'],'0.3.1')
    def test_install_preserves_settings(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);(root/'settings.json').write_text('original settings');(root/'.venv').mkdir()
            files,version=updater.unpack(archive())
            updater.apply(root,{'repo':'name/coach','sha':'a'*40,'version':version,'files':files})
            self.assertEqual((root/'settings.json').read_text(),'original settings');self.assertTrue((root/'.venv').is_dir())
            self.assertFalse((root/'.updates/pending.json').exists())
    def test_rollback(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);(root/'coach.py').write_text('old code')
            files,version=updater.unpack(archive());calls=0
            def fail(src,dst):
                nonlocal calls
                calls+=1
                if calls==3:raise OSError('disk failure')
                return updater.os.replace(src,dst)
            with self.assertRaises(OSError):updater.apply(root,{'repo':'name/coach','sha':'a'*40,'version':version,'files':files},replace=fail)
            self.assertEqual((root/'coach.py').read_text(),'old code')
            self.assertFalse((root/'VERSION').exists())
    def test_interrupted_recovery(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);backup=root/'.updates'/'backup-abc';backup.mkdir(parents=True)
            (backup/'coach.py').write_text('old');(root/'coach.py').write_text('partial new')
            updater.write_json(root/'.updates/pending.json',{'backup':'backup-abc','old':{'coach.py':True}})
            self.assertTrue(updater.recover(root));self.assertEqual((root/'coach.py').read_text(),'old')
            self.assertFalse(updater.recover(root))
    def test_current(self):
        with tempfile.TemporaryDirectory() as folder:
            updater.write_json(Path(folder)/'.update-state.json',{'repo':'name/coach','sha':'b'*40})
            self.assertIsNone(updater.plan(folder,'name/coach','main',lambda u:json.dumps({'sha':'b'*40}).encode()))

if __name__=='__main__':unittest.main()
