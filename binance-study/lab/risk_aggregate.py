#!/usr/bin/env python3
"""Aggregate checkpointed fixed-stop risk sensitivities without market downloads."""
import argparse, collections, hashlib, json, math, os, unittest, tempfile
from pathlib import Path
from datetime import datetime, timezone
from backtest import experiment_hash

ROOT=Path(__file__).resolve().parent

def expected_hash(selection):
    picks=[(s['strategy'],s['timeframe'],s['direction']) for s in selection['strategies'] if s.get('validation_pass')]
    return hashlib.sha256(json.dumps(picks,sort_keys=True).encode()).hexdigest()

def infer_profit_parts(row):
    # Only segment aggregates were persisted. Invert PF = positive / absolute
    # negative and sum = positive - absolute negative; PF=1,sum=0 is indeterminate.
    n=float(row['net_sum']); pf=row.get('profit_factor')
    if pf is None:
        return (max(0.0,n),0.0) if n>=-1e-12 else None
    if not math.isfinite(pf) or pf<0:return None
    if abs(pf-1)<1e-10:return None
    loss=n/(pf-1)
    if loss < -1e-10:return None
    loss=max(0.0,loss)
    return (pf*loss,loss)

def aggregate(risk_dir, selection_path, universe_path):
    selection=json.loads(Path(selection_path).read_text());universe=json.loads(Path(universe_path).read_text())
    requested={x['symbol'] if isinstance(x,dict) else x for x in universe['symbols']}; wanted=expected_hash(selection)
    groups={}; done=set(); skipped=[]; study_gaps={}; quality_unknown=[]
    study_start=datetime(2026,4,1,tzinfo=timezone.utc); study_end=datetime(2026,10,28,tzinfo=timezone.utc)
    for p in sorted(Path(risk_dir).glob('*.json')):
        obj=json.loads(p.read_text()); sym=obj.get('symbol')
        if sym not in requested or obj.get('selection_hash')!=wanted or obj.get('version')!='risk-one-bar-v1' or obj.get('experiment_hash')!=experiment_hash():
            skipped.append({'path':str(p),'reason':'outside universe or incompatible version, experiment, or selection'});continue
        if sym in done: raise ValueError('duplicate symbol '+sym)
        done.add(sym)
        quality=obj.get('data_quality')
        if not isinstance(quality,dict) or 'missing_ranges' not in quality: quality_unknown.append(sym)
        else:
            gaps=[]
            for start,end in quality['missing_ranges']:
                a=datetime.fromisoformat(start.replace('Z','+00:00')); b=datetime.fromisoformat(end.replace('Z','+00:00'))
                if a<study_end and b>study_start: gaps.append([max(a,study_start).isoformat(),min(b,study_end).isoformat()])
            if gaps: study_gaps[sym]=gaps
        for row in obj['records']:
            key=tuple(row[x] for x in ('strategy','timeframe','direction','split'))
            g=groups.setdefault(key,{'trades':0,'wins':0,'gross_sum':0.,'net_sum':0.,'mfe_total':0.,'mae_total':0.,'net_positive_sum':0.,'net_negative_abs_sum':0.,'profit_parts_complete':True,'stop_count':0,'take_count':0,'ambiguous_count':0,'symbols':set(),'segments':0})
            n=int(row['trades']); wins=round(n*row['win_rate'])
            if n<0 or not 0<=wins<=n or abs(wins-n*row['win_rate'])>1e-6:raise ValueError('bad counts')
            g['trades']+=n;g['wins']+=wins;g['gross_sum']+=row['gross_sum'];g['net_sum']+=row['net_sum'];g['mfe_total']+=row['mfe_full_bar_mean']*n;g['mae_total']+=row['mae_full_bar_mean']*n
            for field in ('stop_count','take_count','ambiguous_count'):g[field]+=row[field]
            g['symbols'].add(sym);g['segments']+=1
            parts=infer_profit_parts(row)
            if parts is None:g['profit_parts_complete']=False
            else:g['net_positive_sum']+=parts[0];g['net_negative_abs_sum']+=parts[1]
    rows=[]
    for (strategy,tf,direction,split),g in sorted(groups.items()):
        n=g['trades']; complete=g['profit_parts_complete']; loss=g['net_negative_abs_sum']
        rows.append({'strategy':strategy,'timeframe':tf,'direction':direction,'split':split,'symbols':len(g['symbols']),'segments':g['segments'],'trades':n,'wins':g['wins'],'win_rate':g['wins']/n if n else None,'gross_return_sum':g['gross_sum'],'net_return_sum':g['net_sum'],'gross_average_return':g['gross_sum']/n if n else None,'net_average_return':g['net_sum']/n if n else None,'full_bar_mfe_mean':g['mfe_total']/n if n else None,'full_bar_mae_mean':g['mae_total']/n if n else None,'profit_factor':g['net_positive_sum']/loss if complete and loss else None,'profit_factor_complete':complete,'profit_factor_note':'Derived from segment profit factor and net sum; omitted if any segment is indeterminate or has no pooled loss.','stop_count':g['stop_count'],'take_count':g['take_count'],'ambiguous_count':g['ambiguous_count']})
    return {'schema_version':'1.0','status':'complete' if done==requested and not study_gaps and not quality_unknown else 'partial','selection_hash':wanted,'requested_symbols':len(requested),'completed_symbols':len(done),'missing_symbols':sorted(requested-done),'excluded_checkpoints':skipped,'study_missing_ranges':study_gaps,'quality_unknown_symbols':sorted(quality_unknown),'methodology':{'population':'independent same-bar trades, not portfolio performance','pooling':'trade-weighted means and summed returns; rates derived from pooled counts','profit_factor':'pooled positive divided by pooled absolute negative returns; reconstructed from persisted segment aggregates; indeterminate if segment PF=1','mfe_mae':'full candle extrema, upper bounds because exit may precede extreme','cost':'0.1% round trip','stop_take':'1% SL, 2% TP, stop first on ambiguous bars','funding':'not modeled','selection':'train/validation selection; holdout exploratory'},'results':rows}

class Tests(unittest.TestCase):
    def test_profit_parts(self):
        self.assertEqual(infer_profit_parts({'net_sum':2.,'profit_factor':3.}),(3.,1.))
        self.assertIsNone(infer_profit_parts({'net_sum':0.,'profit_factor':1.}))
        self.assertEqual(infer_profit_parts({'net_sum':2.,'profit_factor':None}),(2.,0.))
    def test_weighting_and_selection(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d); sel={'strategies':[{'strategy':'s','timeframe':'1h','direction':'LONG','validation_pass':True}]}; univ={'symbols':[{'symbol':'A'},{'symbol':'B'}]}
            (p/'selection.json').write_text(json.dumps(sel));(p/'universe.json').write_text(json.dumps(univ));(p/'risk').mkdir()
            for sym,n,mfe,net,pf in [('A',2,.1,2.,3.),('B',1,.4,-1.,0.)]:
                row={'strategy':'s','timeframe':'1h','direction':'LONG','split':'train','trades':n,'win_rate':1/n,'gross_sum':net+.1*n,'net_sum':net,'mfe_full_bar_mean':mfe,'mae_full_bar_mean':.2,'profit_factor':pf,'stop_count':0,'take_count':0,'ambiguous_count':0}
                (p/'risk'/f'{sym}.json').write_text(json.dumps({'symbol':sym,'version':'risk-one-bar-v1','selection_hash':expected_hash(sel),'experiment_hash':experiment_hash(),'records':[row]}))
            obj=aggregate(p/'risk',p/'selection.json',p/'universe.json');r=obj['results'][0]
            self.assertEqual(obj['completed_symbols'],2);self.assertAlmostEqual(r['full_bar_mfe_mean'],.2);self.assertAlmostEqual(r['net_average_return'],1/3);self.assertAlmostEqual(r['profit_factor'],1.5)

def main():
    p=argparse.ArgumentParser();p.add_argument('--risk-dir',default=str(ROOT/'results/risk'));p.add_argument('--selection',default=str(ROOT/'results/results.json'));p.add_argument('--universe',default=str(ROOT.parent/'output/universe.json'));p.add_argument('--output',default=str(ROOT/'results/risk_summary.json'));p.add_argument('--self-test',action='store_true');a=p.parse_args()
    if a.self_test:
        unittest.main(argv=['risk_aggregate'],exit=False);return
    obj=aggregate(a.risk_dir,a.selection,a.universe);out=Path(a.output);out.parent.mkdir(parents=True,exist_ok=True);tmp=out.with_suffix('.tmp');tmp.write_text(json.dumps(obj,indent=2,allow_nan=False)+'\n');os.replace(tmp,out);print(f"{obj['completed_symbols']}/{obj['requested_symbols']} symbols; {len(obj['results'])} risk result rows; {len(obj['excluded_checkpoints'])} excluded")
if __name__=='__main__':main()
