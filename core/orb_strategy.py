# core/orb_strategy.py
import pandas as pd


def detect_opening_ranges(df, or_minutes=30):
    """
    Detect Opening Range (High/Low) per day.
    Works with intraday data (has time component).
    For daily data, returns empty dict (ORB won't fire).
    """
    ranges = {}
    if df is None or len(df) == 0:
        return ranges

    # Detect if we have time-of-day granularity
    first_ts = df.index[0]
    if isinstance(first_ts, str):
        return ranges
    has_time = (first_ts.hour != 0) or (first_ts.minute != 0)
    if not has_time:
        return ranges  # daily data -> no ORB

    df_copy = df.copy()
    df_copy['_date'] = df_copy.index.date

    # Assume 5-min bars -> 6 bars for 30 min
    bars_in_or = max(1, or_minutes // 5)

    for date, group in df_copy.groupby('_date'):
        group = group.sort_index()
        or_bars = group.head(bars_in_or)
        if len(or_bars) > 0:
            ranges[date] = {
                'high': float(or_bars['High'].max()),
                'low':  float(or_bars['Low'].min()),
            }
    return ranges


def orb_signal(df, i, opening_ranges):
    """
    Check ORB breakout at bar i.
    Returns {'signal', 'entry', 'stop_loss', 'target'} or None.
    """
    if opening_ranges is None or i < 0 or i >= len(df):
        return None

    current_bar = df.iloc[i]
    timestamp = df.index[i]

    try:
        today = timestamp.date()
    except Exception:
        return None

    if today not in opening_ranges:
        return None

    or_range = opening_ranges[today]
    or_high = or_range['high']
    or_low = or_range['low']
    or_size = or_high - or_low

    if or_size <= 0:
        return None

    close = float(current_bar['Close'])

    # BUY breakout
    if close > or_high:
        return {
            'signal':    'BUY',
            'entry':     close,
            'stop_loss': or_high - (or_size * 0.3),
            'target':    close + (or_size * 1.5),
        }

    # SELL breakdown
    if close < or_low:
        return {
            'signal':    'SELL',
            'entry':     close,
            'stop_loss': or_low + (or_size * 0.3),
            'target':    close - (or_size * 1.5),
        }

    return None