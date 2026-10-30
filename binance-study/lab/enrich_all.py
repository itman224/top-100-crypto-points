#!/usr/bin/env python3
"""Sequential shared-archive event annotation and exploratory risk sensitivity."""
import hashlib,json,sys
from pathlib import Path
from importlib.metadata import version
from data import load_symbol
from event_features import annotate_symbol
from risk_backtest import run, selected, VERSION
from backtest import experiment_hash
ROOT=Path(__file__).resolve().parent; PROJECT=ROOT.parent
expected_risk_hash=(ROOT/'risk_code.sha256').read_text().strip()
if hashlib.sha256((ROOT/'risk_backtest.py').read_bytes()).hexdigest()!=expected_risk_hash:
 raise SystemExit('risk_backtest.py changed: review and migrate or clear risk checkpoints before updating risk_code.sha256')
pick=selected(ROOT/'results/results.json')
selhash=hashlib.sha256(json.dumps(pick,sort_keys=True).encode()).hexdigest()
h=hashlib.sha256()
for name in ('event_features.py','data.py','indicators.py'): h.update((ROOT/name).read_bytes())
for entry in json.loads((PROJECT/'output/universe.json').read_text())['symbols']:
 s=entry['symbol']; meta=ROOT/'results/event_features'/f'{s}.meta.json'; dest=ROOT/'results/event_features'/f'{s}.jsonl.gz'; risk=ROOT/'results/risk'/f'{s}.json'
 try:
  paths=sorted((PROJECT/'output/events'/s).glob('*.jsonl.gz'))
  hashes={str(p.relative_to(PROJECT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
  m=json.loads(meta.read_text()) if meta.exists() else {}
  feature_ok=dest.exists() and m.get('experiment_hash')==h.hexdigest() and m.get('event_file_sha256')==hashes and m.get('pandas_ta_classic_version')==version('pandas-ta-classic') and not any('unavailable' in k for k in m.get('quality_counts',{})) and not any(x.get('status')=='unavailable' for x in m.get('market_data_quality',{}).get('sources',[]))
  q=json.loads(risk.read_text()) if risk.exists() else {}
  risk_ok=q.get('version')==VERSION and q.get('selection_hash')==selhash and q.get('experiment_hash')==experiment_hash()
  if feature_ok and risk_ok:
   print(f'{s}: both checkpoints',flush=True);continue
  loaded=load_symbol(s)
  if not feature_ok: annotate_symbol(s,loaded=loaded)
  if not risk_ok: run(s,pick,ROOT/'results/risk',loaded=loaded)
 except Exception as exc:
  print(f'{s}: FAILED {type(exc).__name__}: {exc}',flush=True)
