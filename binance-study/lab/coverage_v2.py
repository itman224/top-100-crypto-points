#!/usr/bin/env python3
"""Create transparent, reproducible inventory; classifications are hypotheses, not results."""
from __future__ import annotations
import inspect,json,hashlib,importlib
from collections import Counter
from pathlib import Path
from importlib.metadata import version
import pandas_ta_classic as ta
from indicators import SIGNALS,FILTERS
ROOT=Path(__file__).resolve().parent
CATALOG=ROOT/'indicator_catalog.json'
OUTPUT=ROOT/'coverage_report.json'
FRAMES=['5m','15m','1h','4h','1d']
# Restricted allowlists. Anything not explicitly understood stays "requires_review".
# A proposed directional mapping is a hypothesis, not proof that computation,
# input availability, warm-up or a trading rule is suitable for all parameters.
OVERLAYS=set('alma dema ema fwma hma hwma jma kama linreg mcgd midpoint midprice pwma rma sinwma sma swma t3 tema trima tsf vidya vwma wma zlma'.split())
ZERO_OSC=set('ao apo cci cmo coppock er fisher hist mom ppo pvo roc rocp slope trix tsi'.split())
BOUNDED=set('mfi rsi rsx stoch stochf stochrsi uo'.split())
BANDS=set('bbands donchian kc'.split())
REGIME=set('adx atr natr trange chop choppiness stdev variance entropy'.split())
PATTERNS=set('cdl_doji cdl_inside cdl_pattern'.split())
TRANSFORMS=set('avgprice hl2 hlc3 ohlc4 medprice typprice wcp ha'.split())
UNSAFE={'ichimoku':'Displaced cloud output may include forward-projected values; use a causality-reviewed adapter.', 'dpo':'Default centering/offset semantics require explicit causal verification.', 'vwap':'Reset period/index requirements require explicit session and warm-up validation.'}

def semantics(name,category):
    if name in UNSAFE:return 'requires_causality_review',UNSAFE[name]
    if name in OVERLAYS:return 'price_overlay','Candidate: compare the completed close with the causally aligned overlay; verify output units and warm-up.'
    if name=='willr':return 'bounded_minus100_0','Candidate: reversal at predeclared bounded thresholds; confirm bounds and warm-up.'
    if name in BOUNDED:return 'bounded_0_100','Candidate: reversal at predeclared bounded thresholds; confirm output column and warm-up.'
    if name in ZERO_OSC:return 'oscillator_zero','Candidate: zero-cross or sign hypothesis only after confirming output is signed and centered at zero.'
    if name in BANDS:return 'band','Candidate: use strictly historical band threshold; distinguish simultaneous close from previously known bounds.'
    if name in REGIME:return 'regime_filter','Candidate: condition an independently defined entry; magnitude alone does not establish direction.'
    if name in PATTERNS:return 'pattern','Pattern polarity and availability must be explicitly validated; doji may be neutral.'
    if category in ('math','performance') or name in TRANSFORMS:return 'non_directional_transform','Computational transform or performance statistic; no intrinsic validated long/short semantics.'
    return 'requires_review','Output semantics and a justified causal directional or filter hypothesis have not yet been verified.'

def main():
    prior=json.loads(CATALOG.read_text()); records=[]
    results=ROOT/'results/results.json'; actual={}
    if results.exists():
        z=json.loads(results.read_text())
        for row in z.get('leaderboard',[]):
            n=row.get('strategy'); actual.setdefault(n,{'timeframes':set(),'splits':set(),'directions':set(),'rows':0})
            actual[n]['timeframes'].add(row.get('timeframe'));actual[n]['splits'].add(row.get('split'));actual[n]['directions'].add(row.get('direction'));actual[n]['rows']+=1
    for old in prior['indicators']:
        name=old['name']; family,detail=semantics(name,old['category'])
        variants=old.get('implemented_signal_variants',[])
        bench=[v['name'] for v in variants if v['name'] in actual]
        # Filters are inputs to combinations rather than standalone directional experiments.
        filter_names=[v['name'] for v in variants if v['name'] in FILTERS]
        entries=[{'name':v['name'],'role':'filter' if v['name'] in FILTERS else 'directional','rule':v.get('rule'),'minimum_warmup_bars':v.get('warmup_min_bars'),'benchmarked_as_standalone':v['name'] in bench,'participates_in_benchmarked_combinations':v['name'] in filter_names} for v in variants]
        status='benchmarked' if bench else ('filter_in_benchmark' if filter_names else 'not_screened')
        records.append({'name':name,'category':old['category'],'kind':'indicator','available_as_callable':old['available_as_callable'], 'runtime_execution_verified':bool(variants) and name!='donchian', 'prior_implementation':'manual prior 20-bar high/low channel; not a call to library donchian' if name=='donchian' else ('pandas-ta-classic direct call' if variants else None), 'required_inputs':old.get('required_inputs',[]),'signature':old.get('signature'),'parameters':old.get('parameters',[]),'timeframes_requested':FRAMES,'semantics':family,'hypothesis_guidance':detail,'signal_validity':'explicit causal rule implemented and tested' if variants else 'candidate only; no validated signal mapping', 'prior_status':status,'prior_variants':entries,'warmup':old.get('warmup'),'exclusion_reason':detail if family=='non_directional_transform' else None,'pending_reason':None if status!='not_screened' else 'not part of prior initial screen; applicable computation and causal signal require implementation and validation'})
    for p in prior['candlestick_patterns']:
        records.append({'name':p['name'],'category':'candles','kind':'candlestick_pattern','available_as_callable':p['available'],'native_available':p['native_available'],'runtime_execution_verified':False,'required_inputs':['open','high','low','close'],'parameters':[],'timeframes_requested':FRAMES,'semantics':'pattern','hypothesis_guidance':'Use native signed pattern detection only after verifying polarity, warm-up and no future dependency; neutral patterns have no directional meaning by themselves.', 'signal_validity':'requires explicit causal polarity review','prior_status':'not_screened','prior_variants':[],'warmup':{'minimum_bars':None,'status':'pattern-specific; unverified'},'exclusion_reason':None if p['available'] else 'Runtime dependency or native implementation unavailable','pending_reason':'not part of prior initial screen'})
    summary=Counter(r['prior_status'] for r in records);sem=Counter(r['semantics'] for r in records)
    result={'schema_version':'2.0','library':'pandas-ta-classic','version':version('pandas-ta-classic'),'git_revision':prior['git_revision'],'catalog_sha256':hashlib.sha256(CATALOG.read_bytes()).hexdigest(),'timeframes':FRAMES,'coverage_units':'224 category capabilities plus 62 candlestick pattern names; cdl_pattern capability and its pattern names are related, not independent indicators','summary':{'capabilities':len(prior['indicators']),'patterns':len(prior['candlestick_patterns']),'status_counts':dict(summary),'semantics_counts':dict(sem),'prior_directional_rule_variants':len(SIGNALS),'prior_filter_variants':len(FILTERS),'prior_directional_standalones_in_results':len([n for n in SIGNALS if n in actual]),'prior_missing_directional_standalones':[n for n in SIGNALS if n not in actual]},'prior_screen_explanation':'Initial engineering implementation deliberately limited the first runnable study to explicit causal hypotheses and a bounded execution budget; no evidence establishes that unscreened studies are uninformative. Omission is an unfinished scope, including execution-time/resource prioritization, not an analytical exclusion.','limitations':['Catalog callability is not proof a capability works for each input, parameter, or dependency.', 'The Donchian-style breakout uses a manually shifted rolling channel, not a direct pandas-ta-classic donchian call; raw relative volume is also calculated manually.','Rules and warm-up must be verified separately; no strategy claim follows from a catalog classification.','Existing holdout has been inspected and must not be treated as an untouched confirmation set.'],'capabilities':records}
    temp=OUTPUT.with_suffix('.tmp'); temp.write_text(json.dumps(result,indent=2,allow_nan=False)+'\n'); temp.replace(OUTPUT)
    print(json.dumps(result['summary'],indent=2))
if __name__=='__main__':main()
