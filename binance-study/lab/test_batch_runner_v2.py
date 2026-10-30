import gzip, json, os, subprocess, sys, tempfile, unittest
from unittest import mock
from pathlib import Path

class RunnerTest(unittest.TestCase):
 def test_batches_checkpoint_and_resume(self):
  with tempfile.TemporaryDirectory() as directory:
   root=Path(directory); lab=root/'lab'; lab.mkdir(); (root/'output').mkdir();(root/'output'/'universe.json').write_text(json.dumps({'symbols':[{'symbol':'ONEUSDT'},{'symbol':'TWOUSDT'}]}))
   runner=lab/'batch_runner_v2.py'; runner.write_text((Path(__file__).parent/'batch_runner_v2.py').read_text())
   python=lab/'.venv'/'bin'/'python';python.parent.mkdir(parents=True);python.symlink_to(sys.executable)
   (lab/'data.py').write_text('')
   (lab/'backtest.py').write_text('')
   (lab/'benchmark_v2.py').write_text('''import argparse,gzip,json,pathlib,hashlib\np=argparse.ArgumentParser();p.add_argument("--symbol");a=p.parse_args()\no=pathlib.Path("lab/results/benchmark_v2/symbols");o.mkdir(parents=True,exist_ok=True)\nwith gzip.open(o/(a.symbol+".json.gz"),"wt") as f: json.dump({"symbol":a.symbol,"status":"processed","experiment_hash":hashlib.sha256(pathlib.Path("lab/benchmark_v2.py").read_bytes()).hexdigest(),"input_quality":{"missing_ranges":[]},"registered_hypotheses":{"x":1},"records":[{"indicator":"x","timeframe":tf,"direction":d,"metrics":{"valid_samples":1}} for tf in ["5m","15m","1h","4h","1d"] for d in ["LONG","SHORT"]],"errors":[]},f)\nwith open("calls.txt","a") as f:f.write(a.symbol+"\\n")\n''')
   def call(*args):return subprocess.run([sys.executable,str(runner),*args],cwd=root,capture_output=True,text=True,timeout=20)
   self.assertEqual(call('--max-batches','1').returncode,0)
   ledger=json.loads((lab/'results/benchmark_v2/execution_ledger.json').read_text());self.assertEqual(ledger['counts']['completed'],1);self.assertEqual(ledger['counts']['pending'],1)
   self.assertEqual(call().returncode,0)
   self.assertEqual(call().returncode,0)
   self.assertEqual((root/'calls.txt').read_text().splitlines(),['ONEUSDT','TWOUSDT'])
   ledger=json.loads((lab/'results/benchmark_v2/execution_ledger.json').read_text());self.assertEqual(ledger['counts']['completed'],2);self.assertFalse(ledger['scheduler_status']['persistent_scheduler_active'])
class CoverageTest(unittest.TestCase):
 def test_partial_and_corrupt_checkpoint_are_not_complete(self):
  from lab import batch_runner_v2 as runner
  with tempfile.TemporaryDirectory() as directory:
   root=Path(directory); out=root/'v2'; path=out/'symbols'/'A.json.gz'; path.parent.mkdir(parents=True)
   obj={'symbol':'A','status':'processed_with_errors','experiment_hash':'test','input_quality':{'missing_ranges':[]},'registered_hypotheses':{'rsi':1},'records':[], 'errors':[{'indicator':'rsi','error':'failure'}]}
   with gzip.open(path,'wt') as f:json.dump(obj,f)
   with mock.patch.object(runner,'OUT',out),mock.patch.object(runner,'PROJECT',root),mock.patch.object(runner,'engine_hash',return_value='test'):
    result=runner.inspect_checkpoint('A');self.assertEqual(result['status'],'partial');self.assertEqual(result['indicator_segment_error_count'],1);self.assertEqual(result['missing_indicator_timeframe_directions'],10)
    path.write_bytes(b'corrupt');self.assertEqual(runner.inspect_checkpoint('A')['status'],'pending')
if __name__=='__main__':unittest.main()
