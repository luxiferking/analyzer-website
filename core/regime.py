# core/regime.py
import pandas as pd


def detect_regime(df, lookback=50):
    """
    Detect market regime: TRENDING / RANGING / VOLATILE.
    Returns: "TRENDING_UP", "TRENDING_DOWN", "RANGING", "VOLATILE", or "UNKNOWN"
    """
    if df is None or len(df) < lookback:
        return "UNKNOWN"

    window = df.tail(lookback).copy()

    # ATR-based volatility check
    hl = window['High'] - window['Low']
    hc = (window['High'] - window['Close'].shift()).abs()
    lc = (window['Low'] - window['Close'].shift()).abs()
    atr = pd.concat([hl, hc, lc], axis=1).max(axis=1).rolling(14).mean()

    latest_atr = atr.iloc[-1]
    avg_atr = atr.mean()
    if pd.isna(latest_atr) or pd.isna(avg_atr) or avg_atr == 0:
        return "UNKNOWN"

    # Volatility spike check
    if latest_atr > 1.5 * avg_atr:
        return "VOLATILE"

    # Trend strength via ADX
    high, low, close = window['High'], window['Low'], window['Close']
    tr = pd.concat([high - low,
                    (high - close.shift()).abs(),
                    (low - close.shift()).abs()], axis=1).max(axis=1)
    up_move = high - high.shift()
    down_move = low.shift() - low
    plus_dm = pd.Series(0.0, index=window.index)
    minus_dm = pd.Series(0.0, index=window.index)
    plus_dm[(up_move > down_move) & (up_move > 0)] = up_move
    minus_dm[(down_move > up_move) & (down_move > 0)] = down_move
    atr_s = tr.ewm(alpha=1/14, adjust=False).mean()
    plus_di = 100 * (plus_dm.ewm(alpha=1/14, adjust=False).mean() / atr_s)
    minus_di = 100 * (minus_dm.ewm(alpha=1/14, adjust=False).mean() / atr_s)
    dx = 100 * ((plus_di - minus_di).abs() / (plus_di + minus_di))
    adx = dx.ewm(alpha=1/14, adjust=False).mean()

    latest_adx = adx.iloc[-1]
    if pd.isna(latest_adx):
        return "UNKNOWN"

    # Trend direction via EMA
    ema_20 = window['Close'].ewm(span=20, adjust=False).mean().iloc[-1]
    ema_50 = window['Close'].ewm(span=50, adjust=False).mean().iloc[-1]
    close_now = window['Close'].iloc[-1]

    if latest_adx > 25:
        if close_now > ema_20 > ema_50:
            return "TRENDING_UP"
        elif close_now < ema_20 < ema_50:
            return "TRENDING_DOWN"

    if latest_adx < 20:
        return "RANGING"

    return "UNKNOWN"