"""Introspect the installed pandas-ta-classic; do not invent signals for studies."""
import inspect, json, importlib
from pathlib import Path
import pandas_ta_classic as ta
from indicators import SIGNALS, FILTERS

selected = {'rsi':['rsi_reversal_7','rsi_reversal_14','rsi_reversal_21'], 'ema':['ema_trend_10_30','ema_trend_20_50'], 'macd':['macd_momentum_12_26_9'],'bbands':['bb_reversal_20_2'],'donchian':['donchian_breakout_20'],'adx':['trend_strength_14'],'roc':['roc_momentum_10'],'cci':['cci_reversal_20'],'stoch':['stoch_reversal_14'],'willr':['willr_reversal_14'],'mfi':['mfi_reversal_14'],'obv':['obv_trend_10']}
inputs={'open','open_','high','low','close','volume','real','benchmark','series','series_a','series_b'}
entries=[]
for category,names in ta.Category.items():
    for name in names:
        f=getattr(ta,name,None)
        try:
            sig=inspect.signature(f)
            params=[{'name':p.name,'required':p.default is inspect.Parameter.empty,'default':None if p.default is inspect.Parameter.empty else repr(p.default)} for p in sig.parameters.values() if p.kind not in (p.VAR_KEYWORD,p.VAR_POSITIONAL)]
        except (ValueError,TypeError): sig=None; params=[]
        signals=selected.get(name,[])
        signal=signals[0] if signals else None
        spec=(SIGNALS|FILTERS).get(signal,{})
        entries.append({'name':name,'category':category,'available_as_callable':callable(f),'signature':str(sig) if sig else None,'required_inputs':[p['name'] for p in params if p['name'] in inputs], 'parameters': [p for p in params if p['name'] not in inputs], 'timeframes':['5m','15m','1h','4h','1d'], 'warmup':{'minimum_bars':spec.get('warmup_min_bars'),'status':'rule-specific lower bound; actual first valid output governs' if signal else 'unverified; derive from implementation and selected parameters'},'signal_rule':spec.get('rule'), 'implemented_signal_variants': [{'name':n, **(SIGNALS|FILTERS)[n]} for n in signals], 'screening_status':'implemented' if signal else 'not_screened','exclusion_reason':None if signal else ('helper or output transform; no justified trading rule' if category in ('math','performance') else 'not yet tested; mapping output to a directional signal requires an explicit causal rule')})
patterns=importlib.import_module('pandas_ta_classic.candles.cdl_pattern')
patterns_list=[{'name':p,'native_available':p in patterns._NATIVE_PATTERNS,'available':(p in patterns._NATIVE_PATTERNS or ta.Imports.get('talib',False)), 'signal_status':'not screened; pattern polarity alone is not evidence of predictive edge'} for p in patterns.ALL_PATTERNS]
result={'library':'pandas-ta-classic','version':ta.__version__,'git_revision':'9c1e0ca16f1decf2cb39823f0e229e17d97bafb2','optional_dependencies':ta.Imports,'categories':ta.Category,'indicators':entries,'candlestick_patterns':patterns_list,'notes':['Catalog reflects runtime introspection, not independent evidence of trading performance.','Unverified warm-up periods are not assumed; no rule is applied to unimplemented studies.','All signals use completed bars and must be delayed before execution.']}
Path('lab/indicator_catalog.json').write_text(json.dumps(result,indent=2)+'\n')
print('catalog',len(entries),'indicators;',len(patterns_list),'candlestick patterns; version',ta.__version__)
