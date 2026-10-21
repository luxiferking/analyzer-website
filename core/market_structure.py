"""
core/market_structure.py
Detects BOS (Break of Structure), CHoCH (Change of Character),
and HH/HL/LH/LL sequence.
"""
import pandas as pd
import numpy as np
import yfinance as yf


def _find_swing_points(df, left=3, right=3):
    """Find swing highs and lows."""
    highs = df['High'].values
    lows = df['Low'].values
    swing_highs = []
    swing_lows = []

    for i in range(left, len(df) - right):
        if highs[i] == max(highs[i - left:i + right + 1]):
            swing_highs.append({"index": i, "price": float(highs[i])})
        if lows[i] == min(lows[i - left:i + right + 1]):
            swing_lows.append({"index": i, "price": float(lows[i])})

    return swing_highs, swing_lows


def _classify_sequence(swing_highs, swing_lows):
    """Classify HH/HL/LH/LL sequence."""
    if len(swing_highs) < 2 or len(swing_lows) < 2:
        return "UNDEFINED", []

    # Sort by index (chronological)
    sh = sorted(swing_highs, key=lambda x: x['index'])[-3:]
    sl = sorted(swing_lows, key=lambda x: x['index'])[-3:]

    sequence = []

    # Classify highs
    for i in range(1, len(sh)):
        if sh[i]['price'] > sh[i-1]['price']:
            sequence.append("HH")
        elif sh[i]['price'] < sh[i-1]['price']:
            sequence.append("LH")

    # Classify lows
    for i in range(1, len(sl)):
        if sl[i]['price'] > sl[i-1]['price']:
            sequence.append("HL")
        elif sl[i]['price'] < sl[i-1]['price']:
            sequence.append("LL")

    # Determine structure
    hh_count = sequence.count("HH")
    hl_count = sequence.count("HL")
    lh_count = sequence.count("LH")
    ll_count = sequence.count("LL")

    if (hh_count + hl_count) > (lh_count + ll_count):
        return "UPTREND", sequence
    elif (lh_count + ll_count) > (hh_count + hl_count):
        return "DOWNTREND", sequence
    else:
        return "RANGING", sequence


def _detect_bos_choch(df, swing_highs, swing_lows, lookback=10):
    """
    Detect BOS (Break of Structure) and CHoCH (Change of Character).
    BOS = break in same direction as trend
    CHoCH = break in opposite direction (reversal)
    """
    if len(df) < lookback + 5 or len(swing_highs) < 2 or len(swing_lows) < 2:
        return None, None

    recent = df.tail(lookback)
    structure, sequence = _classify_sequence(swing_highs, swing_lows)

    # Get last swing points
    last_sh = sorted(swing_highs, key=lambda x: x['index'])[-1]
    last_sl = sorted(swing_lows, key=lambda x: x['index'])[-1]

    # Find previous significant swing high/low
    prev_sh = sorted(swing_highs, key=lambda x: x['index'])[-2] if len(swing_highs) >= 2 else None
    prev_sl = sorted(swing_lows, key=lambda x: x['index'])[-2] if len(swing_lows) >= 2 else None

    bos = None
    choch = None

    # Check for BOS/CHoCH in the recent candles
    recent_high = recent['High'].max()
    recent_low = recent['Low'].min()
    recent_close = df['Close'].iloc[-1]

    # Bullish break
    if prev_sh and recent_high > prev_sh['price']:
        if structure == "UPTREND":
            bos = {
                "type": "BULLISH_BOS",
                "broken_level": round(prev_sh['price'], 2),
                "current_price": round(recent_close, 2),
                "meaning": "Uptrend continuation — structure intact",
            }
        elif structure == "DOWNTREND":
            choch = {
                "type": "BULLISH_CHOCH",
                "broken_level": round(prev_sh['price'], 2),
                "current_price": round(recent_close, 2),
                "meaning": "Trend reversal — bearish to bullish",
            }

    # Bearish break
    if prev_sl and recent_low < prev_sl['price']:
        if structure == "DOWNTREND":
            bos = {
                "type": "BEARISH_BOS",
                "broken_level": round(prev_sl['price'], 2),
                "current_price": round(recent_close, 2),
                "meaning": "Downtrend continuation — structure intact",
            }
        elif structure == "UPTREND":
            choch = {
                "type": "BEARISH_CHOCH",
                "broken_level": round(prev_sl['price'], 2),
                "current_price": round(recent_close, 2),
                "meaning": "Trend reversal — bullish to bearish",
            }

    return bos, choch


def analyze_market_structure(ticker_or_df, period="3mo", interval="1d"):
    """Full market structure analysis."""
    if isinstance(ticker_or_df, str):
        try:
            df = yf.Ticker(ticker_or_df).history(period=period, interval=interval)
        except Exception as e:
            return {"status": "error", "message": str(e)}
    elif isinstance(ticker_or_df, pd.DataFrame):
        df = ticker_or_df
    else:
        return {"status": "error", "message": "Invalid input"}

    if df is None or df.empty or len(df) < 30:
        return {"status": "error", "message": "Not enough data"}

    swing_highs, swing_lows = _find_swing_points(df, left=3, right=3)
    structure, sequence = _classify_sequence(swing_highs, swing_lows)
    bos, choch = _detect_bos_choch(df, swing_highs, swing_lows)

    return {
        "status": "success",
        "structure": structure,
        "sequence": sequence,
        "bos": bos,
        "choch": choch,
        "last_swing_high": round(swing_highs[-1]['price'], 2) if swing_highs else None,
        "last_swing_low": round(swing_lows[-1]['price'], 2) if swing_lows else None,
    }


def format_structure_report(data):
    """Format for Jarvis output."""
    if not data or data.get("status") != "success":
        return "⚠️ Structure analysis unavailable."

    lines = []
    lines.append(f"\n📐 MARKET STRUCTURE")

    structure = data['structure']
    emoji = "🟢" if structure == "UPTREND" else "🔴" if structure == "DOWNTREND" else "🟡"
    lines.append(f"   {emoji} Trend: {structure}")
    lines.append(f"   Sequence: {' → '.join(data['sequence']) if data['sequence'] else 'N/A'}")

    if data.get("bos"):
        bos = data['bos']
        lines.append(f"\n   ✅ BOS: {bos['type']} at ₹{bos['broken_level']}")
        lines.append(f"      → {bos['meaning']}")

    if data.get("choch"):
        choch = data['choch']
        lines.append(f"\n   ⚠️ CHoCH: {choch['type']} at ₹{choch['broken_level']}")
        lines.append(f"      → {choch['meaning']}")

    if data.get("last_swing_high"):
        lines.append(f"\n   Last Swing High: ₹{data['last_swing_high']}")
        lines.append(f"   Last Swing Low:  ₹{data['last_swing_low']}")

    return "\n".join(lines)


if __name__ == "__main__":
    import sys
    ticker = sys.argv[1] if len(sys.argv) > 1 else "RELIANCE.NS"
    print(f"\n{'=' * 60}\n  Testing Market Structure — {ticker}\n{'=' * 60}")
    result = analyze_market_structure(ticker)
    print(format_structure_report(result))