"""
core/fair_value_gaps.py
Detects Fair Value Gaps (imbalances) — 3-candle gaps.
"""
import pandas as pd
import numpy as np
import yfinance as yf


def _detect_fvgs(df, lookback=50, min_gap_pct=0.2):
    """
    FVG = 3-candle pattern where candle 1 and candle 3 don't overlap.
    Bullish FVG: candle 3 low > candle 1 high (gap up)
    Bearish FVG: candle 1 low > candle 3 high (gap down)
    """
    if df is None or len(df) < 5:
        return []

    fvgs = []
    recent = df.tail(lookback + 2)

    for i in range(len(recent) - 3):
        c1 = recent.iloc[i]
        c2 = recent.iloc[i + 1]
        c3 = recent.iloc[i + 2]

        # Bullish FVG
        if c3['Low'] > c1['High']:
            gap_size = (c3['Low'] - c1['High']) / c1['High'] * 100
            if gap_size >= min_gap_pct:
                fvgs.append({
                    "type": "BULLISH_FVG",
                    "top": round(float(c3['Low']), 2),
                    "bottom": round(float(c1['High']), 2),
                    "mid": round((float(c3['Low']) + float(c1['High'])) / 2, 2),
                    "date": str(recent.index[i + 1])[:10],
                    "size_pct": round(gap_size, 2),
                    "meaning": "Bullish imbalance — price may revisit to fill",
                })

        # Bearish FVG
        if c1['Low'] > c3['High']:
            gap_size = (c1['Low'] - c3['High']) / c3['High'] * 100
            if gap_size >= min_gap_pct:
                fvgs.append({
                    "type": "BEARISH_FVG",
                    "top": round(float(c1['Low']), 2),
                    "bottom": round(float(c3['High']), 2),
                    "mid": round((float(c1['Low']) + float(c3['High'])) / 2, 2),
                    "date": str(recent.index[i + 1])[:10],
                    "size_pct": round(gap_size, 2),
                    "meaning": "Bearish imbalance — price may revisit to fill",
                })

    # Keep only unfilled FVGs (where price hasn't come back into the zone)
    current_price = float(df['Close'].iloc[-1])
    unfilled = []
    for fvg in fvgs:
        # If price hasn't traded into the FVG zone since it formed
        fvg_date = fvg['date']
        try:
            after_fvg = df[df.index > pd.Timestamp(fvg_date)]
            if after_fvg.empty:
                unfilled.append(fvg)
                continue

            if fvg['type'] == "BULLISH_FVG":
                # Price must not have come down into the gap
                if after_fvg['Low'].min() > fvg['bottom']:
                    unfilled.append(fvg)
            else:
                # Price must not have come up into the gap
                if after_fvg['High'].max() < fvg['top']:
                    unfilled.append(fvg)
        except Exception:
            unfilled.append(fvg)

    # Return last 5 unfilled
    return unfilled[-5:]


def analyze_fvgs(ticker_or_df, period="3mo", interval="1d"):
    """Full FVG analysis."""
    if isinstance(ticker_or_df, str):
        try:
            df = yf.Ticker(ticker_or_df).history(period=period, interval=interval)
        except Exception as e:
            return {"status": "error", "message": str(e)}
    elif isinstance(ticker_or_df, pd.DataFrame):
        df = ticker_or_df
    else:
        return {"status": "error", "message": "Invalid input"}

    if df is None or df.empty or len(df) < 20:
        return {"status": "error", "message": "Not enough data"}

    current_price = float(df['Close'].iloc[-1])
    fvgs = _detect_fvgs(df)

    # Find nearest bull/bear FVG
    bull_fvgs = [f for f in fvgs if f['type'] == "BULLISH_FVG"]
    bear_fvgs = [f for f in fvgs if f['type'] == "BEARISH_FVG"]

    nearest_bull_fvg = None
    if bull_fvgs:
        bull_fvgs.sort(key=lambda x: abs(x['mid'] - current_price))
        nearest_bull_fvg = bull_fvgs[0]

    nearest_bear_fvg = None
    if bear_fvgs:
        bear_fvgs.sort(key=lambda x: abs(x['mid'] - current_price))
        nearest_bear_fvg = bear_fvgs[0]

    # Check if price is in any FVG
    in_bull_fvg = None
    in_bear_fvg = None
    for f in fvgs:
        if f['bottom'] <= current_price <= f['top']:
            if f['type'] == "BULLISH_FVG":
                in_bull_fvg = f
            else:
                in_bear_fvg = f

    return {
        "status": "success",
        "current_price": round(current_price, 2),
        "all_fvgs": fvgs,
        "nearest_bull_fvg": nearest_bull_fvg,
        "nearest_bear_fvg": nearest_bear_fvg,
        "price_in_bull_fvg": in_bull_fvg,
        "price_in_bear_fvg": in_bear_fvg,
    }


def format_fvg_report(data):
    """Format for Jarvis."""
    if not data or data.get("status") != "success":
        return "⚠️ FVG analysis unavailable."

    lines = []
    lines.append(f"\n🌫️ FAIR VALUE GAPS (Imbalances)")
    lines.append(f"   Current: ₹{data['current_price']}")

    if data.get("price_in_bull_fvg"):
        fvg = data['price_in_bull_fvg']
        lines.append(f"\n   🟢 INSIDE Bullish FVG: ₹{fvg['bottom']} – ₹{fvg['top']}")
        lines.append(f"      → Imbalance fill zone — potential BUY")

    if data.get("price_in_bear_fvg"):
        fvg = data['price_in_bear_fvg']
        lines.append(f"\n   🔴 INSIDE Bearish FVG: ₹{fvg['bottom']} – ₹{fvg['top']}")
        lines.append(f"      → Imbalance fill zone — potential SELL")

    if data.get("nearest_bull_fvg") and not data.get("price_in_bull_fvg"):
        fvg = data['nearest_bull_fvg']
        lines.append(f"\n   🟢 Nearest Bullish FVG: ₹{fvg['bottom']} – ₹{fvg['top']}")
        lines.append(f"      ({fvg['size_pct']}% gap from {fvg['date']})")

    if data.get("nearest_bear_fvg") and not data.get("price_in_bear_fvg"):
        fvg = data['nearest_bear_fvg']
        lines.append(f"\n   🔴 Nearest Bearish FVG: ₹{fvg['bottom']} – ₹{fvg['top']}")
        lines.append(f"      ({fvg['size_pct']}% gap from {fvg['date']})")

    return "\n".join(lines)


if __name__ == "__main__":
    import sys
    ticker = sys.argv[1] if len(sys.argv) > 1 else "RELIANCE.NS"
    print(f"\n{'=' * 60}\n  Testing FVGs — {ticker}\n{'=' * 60}")
    result = analyze_fvgs(ticker)
    print(format_fvg_report(result))