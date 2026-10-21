# core/swing_strategy.py
import pandas as pd


def swing_signal(df, i):
    """
    Swing trading signal at bar i.
    Uses EMA_20/50 crossover + RSI + ATR-based SL/Target.
    Returns {'signal', 'entry', 'stop_loss', 'target'} or None.
    """
    if i < 60 or df is None:
        return None

    window = df.iloc[:i+1].copy()

    # Compute needed indicators
    if 'EMA_20' not in window.columns:
        window['EMA_20'] = window['Close'].ewm(span=20, adjust=False).mean()
    if 'EMA_50' not in window.columns:
        window['EMA_50'] = window['Close'].ewm(span=50, adjust=False).mean()
    if 'RSI' not in window.columns:
        delta = window['Close'].diff()
        gain = delta.where(delta > 0, 0).rolling(14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
        rs = gain / loss
        window['RSI'] = 100 - (100 / (1 + rs))
    if 'ATR' not in window.columns:
        hl = window['High'] - window['Low']
        hc = (window['High'] - window['Close'].shift()).abs()
        lc = (window['Low'] - window['Close'].shift()).abs()
        window['ATR'] = pd.concat([hl, hc, lc], axis=1).max(axis=1).rolling(14).mean()

    latest = window.iloc[-1]
    prev = window.iloc[-2]

    atr = latest['ATR']
    if pd.isna(atr) or atr <= 0:
        return None
    if pd.isna(latest['EMA_20']) or pd.isna(latest['EMA_50']) or pd.isna(latest['RSI']):
        return None

    close = float(latest['Close'])

    # BUY: EMA_20 crosses above EMA_50 + RSI > 55
    if (latest['EMA_20'] > latest['EMA_50'] and prev['EMA_20'] <= prev['EMA_50']
            and latest['RSI'] > 55):
        return {
            'signal':    'BUY',
            'entry':     close,
            'stop_loss': close - (2.0 * atr),
            'target':    close + (4.0 * atr),
        }

    # SELL: EMA_20 crosses below EMA_50 + RSI < 45
    if (latest['EMA_20'] < latest['EMA_50'] and prev['EMA_20'] >= prev['EMA_50']
            and latest['RSI'] < 45):
        return {
            'signal':    'SELL',
            'entry':     close,
            'stop_loss': close + (2.0 * atr),
            'target':    close - (4.0 * atr),
        }

    return None