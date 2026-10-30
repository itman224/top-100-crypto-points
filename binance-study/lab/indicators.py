"""Causal, close-available indicator signals. Shift one bar before open execution.

Inputs must be sorted, contiguous completed bars. Segment at missing bars before
calling this module: an observation on the far side of a gap must not inherit
an indicator state from before that gap. Non-finite warm-up outputs are NaN.
"""
from __future__ import annotations
import numpy as np
import pandas as pd
import pandas_ta_classic as ta

SIGNALS = {
    'rsi_reversal_14': {'category':'momentum','warmup_min_bars':15,'rule':'RSI(14) < 30: long; RSI(14) > 70: short; otherwise neutral'},
    'ema_trend_20_50': {'category':'trend','warmup_min_bars':50,'rule':'EMA(20) > EMA(50): long; EMA(20) < EMA(50): short'},
    'macd_momentum_12_26_9': {'category':'momentum','warmup_min_bars':34,'rule':'MACD histogram > 0: long; < 0: short'},
    'bb_reversal_20_2': {'category':'volatility','warmup_min_bars':20,'rule':'close < lower Bollinger band: long; close > upper band: short'},
    'donchian_breakout_20': {'category':'breakout','warmup_min_bars':21,'rule':'close > prior 20-bar high: long; close < prior 20-bar low: short'},
    'rsi_reversal_7': {'category':'momentum','warmup_min_bars':8,'rule':'RSI(7) < 30: long; > 70: short'},
    'rsi_reversal_21': {'category':'momentum','warmup_min_bars':22,'rule':'RSI(21) < 30: long; > 70: short'},
    'ema_trend_10_30': {'category':'trend','warmup_min_bars':30,'rule':'EMA(10) > EMA(30): long; < : short'},
    'roc_momentum_10': {'category':'momentum','warmup_min_bars':11,'rule':'ROC(10) > 0: long; < 0: short'},
    'cci_reversal_20': {'category':'momentum','warmup_min_bars':20,'rule':'CCI(20) < -100: long; > 100: short'},
    'stoch_reversal_14': {'category':'momentum','warmup_min_bars':16,'rule':'stochastic %K(14,3) < 20: long; > 80: short'},
    'willr_reversal_14': {'category':'momentum','warmup_min_bars':14,'rule':'Williams %R(14) < -80: long; > -20: short'},
    'mfi_reversal_14': {'category':'volume_momentum','warmup_min_bars':15,'rule':'MFI(14) < 20: long; > 80: short'},
    'obv_trend_10': {'category':'volume_trend','warmup_min_bars':11,'rule':'OBV above its 10-bar lag: long; below: short'},
}
FILTERS = {
    'trend_strength_14': {'category':'regime_filter','warmup_min_bars':27,'rule':'ADX(14) >= 25: enabled; otherwise disabled'},
    'volume_surge_20': {'category':'volume_filter','warmup_min_bars':21,'rule':'volume > 1.5 times preceding 20-bar mean: enabled; otherwise disabled'},
}

def _direction(index,valid,long,short):
    out = pd.Series(np.nan,index=index,dtype=float)
    valid = valid.fillna(False)
    out.loc[valid] = 0.0
    out.loc[valid & long.fillna(False)] = 1.0
    out.loc[valid & short.fillna(False)] = -1.0
    return out

def compute_signals(df: pd.DataFrame) -> pd.DataFrame:
    """Return contemporaneous close-available decisions; caller shifts for trading."""
    required=['open','high','low','close','volume']
    if any(x not in df.columns for x in required):
        raise ValueError('OHLCV columns open, high, low, close, volume required')
    if not df.index.is_monotonic_increasing or not df.index.is_unique:
        raise ValueError('bar index must be sorted and unique')
    close=df['close'].astype(float); high=df['high'].astype(float); low=df['low'].astype(float); vol=df['volume'].astype(float)
    idx=df.index; out=pd.DataFrame(index=idx)
    rsi=ta.rsi(close,length=14,talib=False)
    e20=ta.ema(close,length=20,talib=False); e50=ta.ema(close,length=50,talib=False)
    macd=ta.macd(close,fast=12,slow=26,signal=9,talib=False)
    bb=ta.bbands(close,length=20,std=2,talib=False)
    adx=ta.adx(high,low,close,length=14,lensig=14)
    # Short segments lack enough observations for the library to return an output.
    if rsi is None: rsi=pd.Series(np.nan,index=idx)
    if e20 is None: e20=pd.Series(np.nan,index=idx)
    if e50 is None: e50=pd.Series(np.nan,index=idx)
    hist=macd['MACDh_12_26_9'] if macd is not None else pd.Series(np.nan,index=idx)
    lower=bb['BBL_20_2.0'] if bb is not None else pd.Series(np.nan,index=idx)
    upper=bb['BBU_20_2.0'] if bb is not None else pd.Series(np.nan,index=idx)
    adx14=adx['ADX_14'] if adx is not None else pd.Series(np.nan,index=idx)
    hi=high.shift(1).rolling(20,min_periods=20).max(); lo=low.shift(1).rolling(20,min_periods=20).min()
    vm=vol.shift(1).rolling(20,min_periods=20).mean()
    out['rsi_reversal_14']=_direction(idx,rsi.notna(),rsi<30,rsi>70)
    out['ema_trend_20_50']=_direction(idx,e20.notna() & e50.notna(),e20>e50,e20<e50)
    out['macd_momentum_12_26_9']=_direction(idx,hist.notna(),hist>0,hist<0)
    out['bb_reversal_20_2']=_direction(idx,lower.notna() & upper.notna(),close<lower,close>upper)
    out['donchian_breakout_20']=_direction(idx,hi.notna() & lo.notna(),close>hi,close<lo)
    out['trend_strength_14']=_direction(idx,adx14.notna(),adx14>=25,pd.Series(False,index=idx))
    out['volume_surge_20']=_direction(idx,vm.notna(),vol>1.5*vm,pd.Series(False,index=idx))
    def safe(s):
        return s if s is not None else pd.Series(np.nan,index=idx)
    for length in (7,21):
        val=safe(ta.rsi(close,length=length,talib=False))
        out[f'rsi_reversal_{length}']=_direction(idx,val.notna(),val<30,val>70)
    e10=safe(ta.ema(close,length=10,talib=False)); e30=safe(ta.ema(close,length=30,talib=False))
    out['ema_trend_10_30']=_direction(idx,e10.notna() & e30.notna(),e10>e30,e10<e30)
    roc=safe(ta.roc(close,length=10,talib=False))
    out['roc_momentum_10']=_direction(idx,roc.notna(),roc>0,roc<0)
    cci=safe(ta.cci(high,low,close,length=20,talib=False))
    out['cci_reversal_20']=_direction(idx,cci.notna(),cci< -100,cci>100)
    stoch=ta.stoch(high,low,close,k=14,d=3,smooth_k=3,talib=False)
    stochk=stoch['STOCHk_14_3_3'] if stoch is not None else pd.Series(np.nan,index=idx)
    out['stoch_reversal_14']=_direction(idx,stochk.notna(),stochk<20,stochk>80)
    will=safe(ta.willr(high,low,close,length=14,talib=False))
    out['willr_reversal_14']=_direction(idx,will.notna(),will< -80,will> -20)
    mfi=safe(ta.mfi(high,low,close,vol,length=14,talib=False))
    out['mfi_reversal_14']=_direction(idx,mfi.notna(),mfi<20,mfi>80)
    obv=safe(ta.obv(close,vol,talib=False)); previous=obv.shift(10)
    out['obv_trend_10']=_direction(idx,obv.notna() & previous.notna(),obv>previous,obv<previous)
    return out
