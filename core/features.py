import pandas as pd

def add_vwap(df):
    df = df.copy()
    df['VWAP'] = (df['Volume'] * (df['High'] + df['Low'] + df['Close']) / 3).cumsum() / df['Volume'].cumsum()
    return df

def add_ema(df, spans=(9, 21)):
    df = df.copy()
    for span in spans:
        df[f'EMA_{span}'] = df['Close'].ewm(span=span, adjust=False).mean()
    return df

def add_atr(df, period=14):
    df = df.copy()
    hl = df['High'] - df['Low']
    hc = (df['High'] - df['Close'].shift()).abs()
    lc = (df['Low'] - df['Close'].shift()).abs()
    tr = pd.concat([hl, hc, lc], axis=1).max(axis=1)
    df['ATR'] = tr.rolling(period).mean()
    return df

def add_rsi(df, period=14):
    df = df.copy()
    delta = df['Close'].diff()
    gain = delta.where(delta > 0, 0).rolling(period).mean()
    loss = -delta.where(delta < 0, 0).rolling(period).mean()
    rs = gain / loss
    df['RSI'] = 100 - (100 / (1 + rs))
    return df

def add_macd(df, fast=12, slow=26, signal=9):
    df = df.copy()
    ema_fast = df['Close'].ewm(span=fast, adjust=False).mean()
    ema_slow = df['Close'].ewm(span=slow, adjust=False).mean()
    df['MACD'] = ema_fast - ema_slow
    df['MACD_Signal'] = df['MACD'].ewm(span=signal, adjust=False).mean()
    df['MACD_Hist'] = df['MACD'] - df['MACD_Signal']
    return df

def add_adx(df, period=14):
    df = df.copy()
    high, low, close = df['High'], df['Low'], df['Close']
    tr = pd.concat([high - low, (high - close.shift()).abs(), (low - close.shift()).abs()], axis=1).max(axis=1)
    up = high - high.shift()
    down = low.shift() - low
    plus_dm = pd.Series(0.0, index=df.index)
    minus_dm = pd.Series(0.0, index=df.index)
    plus_dm[(up > down) & (up > 0)] = up
    minus_dm[(down > up) & (down > 0)] = down
    atr = tr.ewm(alpha=1/period, adjust=False).mean()
    plus_di = 100 * (plus_dm.ewm(alpha=1/period, adjust=False).mean() / atr)
    minus_di = 100 * (minus_dm.ewm(alpha=1/period, adjust=False).mean() / atr)
    dx = 100 * ((plus_di - minus_di).abs() / (plus_di + minus_di))
    df['ADX'] = dx.ewm(alpha=1/period, adjust=False).mean()
    df['Plus_DI'] = plus_di
    df['Minus_DI'] = minus_di
    return df

def add_all_features(df):
    df = add_vwap(df)
    df = add_ema(df, spans=(9, 21, 50))
    df = add_atr(df)
    df = add_rsi(df)
    df = add_macd(df)
    df = add_adx(df)
    return df