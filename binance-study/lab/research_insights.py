#!/usr/bin/env python3
"""Descriptive research answers from saved pooled benchmark. No holdout tuning."""
import json, os
from pathlib import Path

ROOT=Path(__file__).resolve().parent; inp=ROOT/'results/results.json'; out=ROOT/'results/insights.json'

def label(x):
 return f"{x['strategy']} ({x['timeframe']}, {x['direction']})"

def main():
 d=json.loads(inp.read_text()); rows=d.get('leaderboard',[]); candidates=d.get('strategies',[]); questions=[]
 def add(q,answer,evidence=None,split='validation',status='answered'):
  questions.append({'question':q,'answer':answer,'evidence':evidence or {},'split':split,'status':status})
 def eligible(direction):
  return [r for r in rows if r['split']=='validation' and r['direction']==direction and not r['strategy'].startswith('baseline') and r['metrics'].get('trades',0)>=30 and r['metrics'].get('precision') is not None]
 for direction,word in [('LONG','up'),('SHORT','down')]:
  xs=eligible(direction)
  if xs:
   x=max(xs,key=lambda r:r['metrics']['precision']); m=x['metrics']
   add(f'Which tested signal has the highest validation precision for {word} events?',f"{label(x)}: precision {m['precision']:.4f}, event base rate {m['event_base_rate']:.4f}, {m['trades']} signals. Exploratory maximum; no multiple-testing correction.",{'strategy':x['strategy'],'timeframe':x['timeframe'],'direction':direction,'metrics':m})
  else:add(f'Which tested signal has the highest validation precision for {word} events?','Not enough validation signals (minimum 30).',status='insufficient_data')
 for direction in ('LONG','SHORT'):
  xs=[x for x in candidates if x['direction']==direction and x.get('validation_pass')]
  if xs:
   x=max(xs,key=lambda r:r['validation']['net_average_return']); m=x['validation']
   add(f'Which train-selected {direction} rule has the highest validation net mean?',f"{label(x)}: {m['net_average_return']:.6f} per trade after cost, {m['trades']} trades; train-selected, validation-ranked descriptively.",{'strategy':x['strategy'],'timeframe':x['timeframe'],'direction':direction,'metrics':m})
  else:add(f'Which train-selected {direction} rule has the highest validation net mean?','No candidate currently meets the predeclared validation gate.',status='insufficient_data')
 add('How many train-selected candidates pass validation?',f"{sum(bool(x.get('validation_pass')) for x in candidates)} of {len(candidates)} train-selected candidates pass the validation gate (>=30 trades and positive net mean).",{'candidates':len(candidates),'passed':sum(bool(x.get('validation_pass')) for x in candidates)})
 eligible_hold=[x for x in candidates if x.get('validation_pass')]; passed=[x for x in eligible_hold if x.get('holdout_pass')]
 add('How many frozen candidates pass the holdout gate?',f"{len(passed)} of {len(eligible_hold)} validation-passing, train-selected candidates meet the predeclared holdout gate (>=30 trades, positive mean net return, profit factor >1). This is exploratory evidence, not proof of robustness.",{'evaluated':len(eligible_hold),'passed':len(passed)},split='holdout')
 for filter_name,base,question in [('__volume_surge','donchian_breakout_20','Does a volume surge improve breakout signals?'),('__adx25','ema_trend_20_50','Does a trend-strength filter improve EMA signals?')]:
  pairs=[]
  for r in rows:
   if r['split']!='validation' or r['strategy']!=base+filter_name:continue
   b=next((x for x in rows if x['strategy']==base and x['split']=='validation' and x['timeframe']==r['timeframe'] and x['direction']==r['direction']),None)
   if b and r['metrics']['trades']>=30 and b['metrics']['net_average_return'] is not None and r['metrics']['net_average_return'] is not None:pairs.append({'timeframe':r['timeframe'],'direction':r['direction'],'filtered_net_mean':r['metrics']['net_average_return'],'baseline_net_mean':b['metrics']['net_average_return'],'filtered_trades':r['metrics']['trades'],'baseline_trades':b['metrics']['trades']})
  if pairs:add(question,f"{sum(x['filtered_net_mean']>x['baseline_net_mean'] for x in pairs)} of {len(pairs)} eligible timeframe/direction comparisons improve net mean; subgroup trade counts differ.",{'comparisons':pairs})
  else:add(question,'Not enough matching validation trades to compare.',status='insufficient_data')
 grouped={}
 for r in rows:
  if r['split']=='validation' and r['metrics']['trades']>=30 and r['metrics']['net_average_return'] is not None and not r['strategy'].startswith('baseline'):
   grouped.setdefault(r['timeframe'],[]).append(r['metrics']['net_average_return'])
 if grouped:
  med={k:sorted(v)[len(v)//2] for k,v in grouped.items()}; add('Which timeframe has the highest median validation net mean across tested rules?',f"{max(med,key=med.get)} has the highest descriptive median; correlated rules and trade dependence limit inference.",{'median_by_timeframe':med})
 else:add('Which timeframe is most stable?','Insufficient validation trades to compare.',status='insufficient_data')
 positive=[r for r in rows if r['split']=='validation' and not r['strategy'].startswith('baseline') and r['metrics']['trades']>=30 and (r['metrics']['net_average_return'] or 0)>0]
 add('How many tested validation configurations remain profitable after costs?',f"{len(positive)} of {len([r for r in rows if r['split']=='validation' and not r['strategy'].startswith('baseline') and r['metrics']['trades']>=30])} eligible configurations have positive pooled net mean. Results are in-sample for this descriptive comparison and not independent strategies.",{'positive':len(positive),'cost_roundtrip_fraction':d['methodology']['cost_roundtrip_fraction']})
 # Pre-specified BTC/ETH comparison: 1h unconditional LONG baseline in validation.
 from backtest import VERSION, experiment_hash
 from aggregate import pool
 asset_values={}
 for symbol in ('BTCUSDT','ETHUSDT'):
  path=ROOT/'results/symbols'/f'{symbol}.json'
  if not path.exists(): continue
  obj=json.loads(path.read_text())
  if obj.get('version')!=VERSION or obj.get('experiment_hash')!=experiment_hash(): continue
  matching=[r for r in obj.get('records',[]) if r['timeframe']=='1h' and r['split']=='validation' and r['strategy']=='baseline_always_long' and r['direction']=='LONG']
  if matching: asset_values[symbol]=pool(matching)
 if len(asset_values)==2:
  a,b=asset_values['BTCUSDT'],asset_values['ETHUSDT']
  add('How did the unconditional 1h LONG baseline differ for BTC and ETH in validation?',
      f"BTC net mean {a['net_average_return']:.6f} over {a['trades']} trades; ETH net mean {b['net_average_return']:.6f} over {b['trades']} trades. Independent single-symbol tests; not a portfolio comparison.",{'BTCUSDT':a,'ETHUSDT':b})
  add('How did the base rate of >1% up candles compare for BTC and ETH in validation?',
      f"BTC {a['event_base_rate']:.4%} across {a['valid_samples']} candles; ETH {b['event_base_rate']:.4%} across {b['valid_samples']} candles (1h, validation).",{'BTCUSDT':a['event_base_rate'],'ETHUSDT':b['event_base_rate'],'btc_candles':a['valid_samples'],'eth_candles':b['valid_samples']})
  add('What was the modeled cost effect on the BTC 1h LONG validation baseline?',
      f"Gross mean {a['gross_average_return']:.6f}; net mean {a['net_average_return']:.6f} per trade after fixed 0.001 round-trip cost, across {a['trades']} trades. Funding is unmodeled.",{'gross':a['gross_average_return'],'net':a['net_average_return'],'trades':a['trades']})
 else:
  for q in ('How did BTC and ETH 1h baseline returns differ?','How did BTC and ETH event base rates differ?','What was the modeled cost effect on BTC?'):
   add(q,'The pre-specified BTC and ETH symbol checkpoints are not both available.',status='insufficient_data')
 add('Does multi-timeframe confirmation improve profitability?' ,'Not measured: current engine has no synchronized cross-timeframe signal.','',status='not_tested')
 add('Which rule is most stable across months and symbols?','Not established: monthly per-symbol stability and confidence intervals have not been independently evaluated.','',status='not_tested')
 add('What are the effects of BTC/ETH market regime, hour, and funding?','Not measured: market regime, hour stratification, and verified funding series are not in the current experiment.','',status='not_tested')
 out.parent.mkdir(exist_ok=True); tmp=out.with_suffix('.tmp'); tmp.write_text(json.dumps({'scope':{'symbols_processed':d['symbols_processed'],'requested_symbols':d['requested_symbols'],'status':d['status']},'questions':questions},indent=2,allow_nan=False));os.replace(tmp,out)
 print(f'{len(questions)} questions; {sum(x["status"]=="answered" for x in questions)} answered')
if __name__=='__main__':main()
