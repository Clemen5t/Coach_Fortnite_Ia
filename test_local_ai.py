import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from local_ai import LocalAI, usable, parse_advice, AdviceGate

class Handler(BaseHTTPRequestHandler):
    mode='ok';seen=[];entered=threading.Event();release=threading.Event()
    def log_message(self,*args):pass
    def do_POST(self):
        body=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        Handler.seen.append((self.path,body));status=200
        if self.path=='/api/show':
            result={'capabilities':['completion','vision']}
            if Handler.mode=='remote':result['remote_host']='https://example.invalid'
            if Handler.mode=='novision':result['capabilities']=['completion']
            if Handler.mode=='missing':status=404;result={'error':'not found'}
        else:
            if Handler.mode=='slow':Handler.entered.set();Handler.release.wait(3)
            if 'Question du joueur :' in body.get('prompt',''):
                result={'done':True,'done_reason':'stop','response':'Recule derrière le mur à droite, puis recharge.'}
            else:
                result={'done':True,'done_reason':'stop','response':json.dumps({'category':'cover','evidence':'Impacts visibles sur le mur droit','advice':'Passe derrière le mur à droite.'})}
            if Handler.mode=='truncated':result['done_reason']='length'
        data=json.dumps(result).encode()
        try:
            self.send_response(status);self.send_header('Content-Length',str(len(data)));self.end_headers();self.wfile.write(data)
        except (BrokenPipeError,ConnectionResetError):pass

class Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        threading.Thread(target=cls.server.serve_forever,daemon=True).start()
    @classmethod
    def tearDownClass(cls):cls.server.shutdown();cls.server.server_close()
    def setUp(self):
        Handler.mode='ok';Handler.seen=[];Handler.entered.clear();Handler.release.clear()
        self.client=LocalAI(threading.Event(),self.server.server_port)
    def test_local_image_request(self):
        self.client.verify();self.assertEqual(self.client.generate('dGVzdA=='),'Passe derrière le mur à droite.')
        path,body=Handler.seen[-1];self.assertEqual(path,'/api/generate');self.assertEqual(body['images'],['dGVzdA==']);self.assertFalse(body['stream'])
    def test_ask_uses_question_and_image(self):
        text=self.client.ask('aW1hZ2U=','Je prends le fight ?','Joueur: avant')
        self.assertIn('mur à droite',text)
        _,body=Handler.seen[-1];self.assertEqual(body['images'],['aW1hZ2U=']);self.assertIn('Je prends le fight ?',body['prompt']);self.assertIn('Contexte récent',body['prompt'])
    def test_refuse_remote(self):
        Handler.mode='remote'
        with self.assertRaisesRegex(RuntimeError,'distant'):self.client.verify()
    def test_missing(self):
        Handler.mode='missing'
        with self.assertRaisesRegex(RuntimeError,'Installer-modele'):self.client.verify()
    def test_nonvision(self):
        Handler.mode='novision'
        with self.assertRaisesRegex(RuntimeError,'Vision'):self.client.verify()
    def test_truncated(self):
        Handler.mode='truncated';self.assertEqual(self.client.generate('x'),'SILENCE')
    def test_staleness(self):
        self.assertTrue(usable('Couvert',.8,2,''));self.assertFalse(usable('Couvert',2.1,2,''));self.assertFalse(usable('SILENCE.',.8,2,''));self.assertFalse(usable('Couvert',.8,2,'Couvert'))
    def test_cancel_pending(self):
        Handler.mode='slow';errors=[]
        def work():
            try:self.client.generate('x')
            except Exception as e:errors.append(e)
        thread=threading.Thread(target=work);thread.start();self.assertTrue(Handler.entered.wait(2));self.client.cancel();thread.join(1)
        try:self.assertFalse(thread.is_alive());self.assertTrue(errors)
        finally:Handler.release.set()
    def test_generic_rejected(self):
        for text in ['Trouver une meilleure couverture immédiate.','Chercher une position de sécurité.','Chercher une couverture, vite, et stable.']:
            self.assertEqual(parse_advice(json.dumps({'category':'cover','evidence':'Le joueur est dehors','advice':text}))[:2],('SILENCE','none'))
    def test_category_cooldown(self):
        gate=AdviceGate();self.assertTrue(gate.allow('cover',100));self.assertFalse(gate.allow('cover',110));self.assertFalse(gate.allow('heal',105));self.assertTrue(gate.allow('heal',110));self.assertTrue(gate.allow('cover',131))
    def test_invalid_evidence(self):
        self.assertEqual(parse_advice('{"category":"cover","evidence":"","advice":"Construis un mur."}')[:2],('SILENCE','none'))
    def test_cancel_before_request(self):
        self.client.cancel()
        with self.assertRaises(InterruptedError):self.client.verify()
        self.assertEqual(Handler.seen,[])

if __name__=='__main__':unittest.main()
