import ast
import threading
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

    def test_job_executes_before_ui_callback(self):
        # Extract the actual method without importing Windows/audio/GUI dependencies.
        tree=ast.parse(Path('coach.py').read_text())
        method=next(n for n in ast.walk(tree) if isinstance(n,ast.FunctionDef) and n.name=='pc_job')
        namespace={'threading':threading}
        exec(compile(ast.Module(body=[method],type_ignores=[]),'coach.py','exec'),namespace)
        main=threading.get_ident();finished=threading.Event();callbacks=[];calls=[]
        class Root:
            def after(self, delay, callback):
                callbacks.append(callback);finished.set()
        class Status:
            def set(self, value):pass
        class App:
            root=Root();pc_status=Status()
            def pc_done(self,result):calls.append(result)
            def pc_error(self,error):calls.append(error)
        def work():
            self.assertNotEqual(threading.get_ident(),main)
            return 'done'
        namespace['pc_job'](App(),'Test',work)
        self.assertTrue(finished.wait(2))
        self.assertEqual(calls,[])
        callbacks.pop()()
        self.assertEqual(calls,['done'])

if __name__=='__main__':unittest.main()
