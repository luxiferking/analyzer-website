"""
core/regime_detector.py
Analyzes the market regime (Trending, Ranging, Volatile) to guide the bot.
"""
import pandas as pd
import numpy as np

def detect_regime(df):
    """
    Detects market regime based on ADX (Trend), ATR (Volatility), and MAs.
    """
    if df is None or len(df) < 50:
        return {
            "regime": "UNKNOWN",
            "confidence": 0,
            "tradeable": False,
            "preferred_style": "avoid",
            "characteristics": ["Insufficient data"],
            "metrics": {}
        }

    try:
        # Ensure correct column capitalization
        df.columns = [str(c).capitalize() for c in df.columns]
        
        close = df['Close']
        high = df['High']
        low = df['Low']
        volume = df['Volume'] if 'Volume' in df.columns else pd.Series(0, index=df.index)

        # 1. Calculate ATR (Average True Range) for Volatility
        tr = pd.concat([
            high - low,
            (high - close.shift()).abs(),
            (low - close.shift()).abs()
        ], axis=1).max(axis=1)
        atr = tr.ewm(alpha=1/14, adjust=False).mean()
        current_atr = atr.iloc[-1]
        avg_atr = atr.tail(50).mean()
        volatility_pct = (current_atr / close.iloc[-1]) * 100

        # 2. Calculate ADX (Trend Strength)
        up_move = high - high.shift()
        down_move = low.shift() - low
        plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
        minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)
        
        atr_safe = atr.replace(0, np.nan)
        plus_di = 100 * (pd.Series(plus_dm, index=df.index).ewm(alpha=1/14, adjust=False).mean() / atr_safe)
        minus_di = 100 * (pd.Series(minus_dm, index=df.index).ewm(alpha=1/14, adjust=False).mean() / atr_safe)
        
        dx = 100 * (abs(plus_di - minus_di) / (plus_di + minus_di).replace(0, np.nan))
        adx = dx.ewm(alpha=1/14, adjust=False).mean()
        current_adx = adx.iloc[-1]

        # 3. Trend Direction (SMA 50 vs Price)
        sma50 = close.rolling(50).mean().iloc[-1]
        current_price = close.iloc[-1]

        # 4. Determine Regime based on ADX and Volatility
        regime = "UNKNOWN"
        tradeable = True
        preferred_style = "intraday"
        characteristics = []
        confidence = 50

        if current_adx >= 25:
            # Strong Trend
            if current_price > sma50:
                regime = "STRONG_BULL"
                characteristics.append("Strong Uptrend (ADX > 25)")
                preferred_style = "trend_following_buy"
                confidence = min(90, 50 + current_adx)
            else:
                regime = "STRONG_BEAR"
                characteristics.append("Strong Downtrend (ADX > 25)")
                preferred_style = "trend_following_sell"
                confidence = min(90, 50 + current_adx)
        elif current_adx < 20:
            # Weak Trend / Ranging
            regime = "CHOPPY"
            characteristics.append("Sideways / Ranging (ADX < 20)")
            preferred_style = "avoid_or_mean_reversion"
            tradeable = False # Bot should avoid trading breakout strategies here
            confidence = 40
        else:
            # Moderate Trend
            regime = "MODERATE_TREND"
            characteristics.append("Moderate Trend (ADX 20-25)")
            preferred_style = "intraday_breakout"
            confidence = 60

        # Check extreme volatility
        if current_atr > (avg_atr * 1.5):
            characteristics.append("⚠️ High Volatility Detected")
            tradeable = False # Avoid trading during extreme volatility (e.g., news events)

        return {
            "regime": regime,
            "confidence": round(confidence, 1),
            "tradeable": tradeable,
            "preferred_style": preferred_style,
            "characteristics": characteristics,
            "metrics": {
                "ADX": round(current_adx, 2),
                "ATR": round(current_atr, 2),
                "Volatility %": round(volatility_pct, 2),
                "Price vs SMA50": "ABOVE" if current_price > sma50 else "BELOW"
            }
        }

    except Exception as e:
        return {
            "regime": "ERROR",
            "confidence": 0,
            "tradeable": False,
            "preferred_style": "avoid",
            "characteristics": [f"Error: {str(e)}"],
            "metrics": {}
        }

def format_regime(result):
    """Formats the regime result for pretty printing."""
    if result.get("regime") == "ERROR":
        return f"❌ Error detecting regime: {result['characteristics'][0]}"

    lines = []
    lines.append("=" * 50)
    lines.append(f"🌍 MARKET REGIME: {result['regime']} (Confidence: {result['confidence']}%)")
    lines.append("=" * 50)
    
    trade_status = "✅ YES" if result['tradeable'] else "❌ NO (Avoid Trading)"
    lines.append(f"Tradeable: {trade_status}")
    lines.append(f"Best Style: {result['preferred_style']}")
    lines.append("\nCharacteristics:")
    for char in result['characteristics']:
        lines.append(f"  • {char}")
        
    if result.get('metrics'):
        lines.append("\nMetrics:")
        for k, v in result['metrics'].items():
            lines.append(f"  • {k}: {v}")
            
    lines.append("=" * 50)
    return "\n".join(lines)

# ─── Standalone Test ───
if __name__ == "__main__":
    import yfinance as yf
    import warnings
    warnings.filterwarnings('ignore') # Ignore yfinance warnings
    
    print("Testing Regime Detector...")
    print("Fetching Nifty 50 (^NSEI) data...")
    
    # Download daily data for testing
    df = yf.download("^NSEI", period="6mo", interval="1d", progress=False)
    
    if df is not None and not df.empty:
        # Flatten multi-index columns if yfinance returns them
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
            
        result = detect_regime(df)
        print(format_regime(result))
    else:
        print("❌ Failed to fetch data from yfinance.")