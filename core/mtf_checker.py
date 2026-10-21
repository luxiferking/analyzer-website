# core/mtf_checker.py
import pandas as pd


def _trend_of(df):
    """Return 'BULL' or 'BEAR' for a single timeframe."""
    if df is None or len(df) < 25:
        return None
    ema9 = df['Close'].ewm(span=9, adjust=False).mean().iloc[-1]
    ema21 = df['Close'].ewm(span=21, adjust=False).mean().iloc[-1]
    return "BULL" if ema9 > ema21 else "BEAR"


def check_multi_timeframe(df_5m, df_15m=None, df_1h=None, df_daily=None):
    """
    Check trend alignment across timeframes.
    Returns: (signal, confidence)
    """
    results = []
    for df in [df_5m, df_15m, df_1h, df_daily]:
        t = _trend_of(df)
        if t:
            results.append(t)

    if not results:
        return "NO_TRADE", 0

    bull_count = results.count("BULL")
    total = len(results)

    if total == 4 and bull_count == 4:
        return "STRONG_BUY", 100
    elif total == 4 and bull_count == 3:
        return "BUY", 75
    elif total == 4 and bull_count == 1:
        return "WEAK", 25
    elif total == 4 and bull_count == 0:
        return "STRONG_SELL", 100
    elif total >= 3 and bull_count == total:
        return "BUY", 80
    elif total >= 3 and bull_count == 0:
        return "SELL", 80
    else:
        return "NO_TRADE", 0