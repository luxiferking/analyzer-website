"""
core/liquidity_analyzer.py
Detects Buy-Side Liquidity (BSL), Sell-Side Liquidity (SSL),
Equal Highs/Lows, and Liquidity Sweeps.

Fixed:
- All prices rounded to 2 decimals
- Equal levels filtered to within 5% of current price
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
            swing_highs.append({
                "index": i,
                "price": float(highs[i]),
                "date": str(df.index[i])[:10],
            })
        if lows[i] == min(lows[i - left:i + right + 1]):
            swing_lows.append({
                "index": i,
                "price": float(lows[i]),
                "date": str(df.index[i])[:10],
            })

    return swing_highs, swing_lows


def _find_equal_levels(swings, tol_pct=0.15, current_price=None, max_distance_pct=5.0):
    """
    Find clusters of equal swing points (magnets).
    Only keeps clusters within `max_distance_pct` of current price.
    """
    if len(swings) < 2:
        return []

    # Filter swings near current price
    if current_price is not None:
        swings = [
            s for s in swings
            if abs(s['price'] - current_price) / current_price * 100 <= max_distance_pct
        ]

    if len(swings) < 2:
        return []

    sorted_s = sorted(swings, key=lambda x: x['price'])
    clusters = []
    i = 0
    while i < len(sorted_s) - 1:
        j = i + 1
        group = [sorted_s[i]]
        while j < len(sorted_s):
            diff = abs(sorted_s[j]['price'] - sorted_s[i]['price']) / sorted_s[i]['price'] * 100
            if diff <= tol_pct:
                group.append(sorted_s[j])
                j += 1
            else:
                break
        if len(group) >= 2:
            avg = sum(g['price'] for g in group) / len(group)
            clusters.append({
                "price": round(avg, 2),
                "count": len(group),
                "dates": [g['date'] for g in group],
            })
        i = j if j > i + 1 else i + 1

    return clusters


def _detect_sweeps(df, swing_highs, swing_lows, lookback=15):
    """Detect liquidity sweeps (stop hunts)."""
    if len(df) < lookback + 5:
        return []

    sweeps = []
    recent = df.tail(lookback)

    for idx, row in recent.iterrows():
        # Sweep above swing high (bearish sweep)
        for sh in swing_highs:
            if row['High'] > sh['price'] * 1.001 and row['Close'] < sh['price']:
                sweeps.append({
                    "type": "BEARISH_SWEEP",
                    "level": round(sh['price'], 2),
                    "date": str(idx)[:10],
                    "meaning": "Stop hunt above swing high — reversal likely",
                })
                break

        # Sweep below swing low (bullish sweep)
        for sl in swing_lows:
            if row['Low'] < sl['price'] * 0.999 and row['Close'] > sl['price']:
                sweeps.append({
                    "type": "BULLISH_SWEEP",
                    "level": round(sl['price'], 2),
                    "date": str(idx)[:10],
                    "meaning": "Stop hunt below swing low — reversal likely",
                })
                break

    return sweeps[-5:]  # Last 5 sweeps


def analyze_liquidity(ticker_or_df, period="3mo", interval="1d"):
    """
    Full liquidity analysis.
    Accepts ticker string OR DataFrame.
    """
    # Get data
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

    current_price = float(df['Close'].iloc[-1])

    # Detect swing points
    swing_highs, swing_lows = _find_swing_points(df, left=3, right=3)

    # BSL: above recent swing highs
    bsl = sorted(swing_highs, key=lambda x: x['index'], reverse=True)[:5]
    # SSL: below recent swing lows
    ssl = sorted(swing_lows, key=lambda x: x['index'], reverse=True)[:5]

    # Equal levels — only near current price
    equal_highs = _find_equal_levels(
        swing_highs, tol_pct=0.15,
        current_price=current_price, max_distance_pct=5.0
    )
    equal_lows = _find_equal_levels(
        swing_lows, tol_pct=0.15,
        current_price=current_price, max_distance_pct=5.0
    )

    # Sweeps
    sweeps = _detect_sweeps(df, swing_highs, swing_lows, lookback=15)

    # Find nearest BSL above current price
    bsl_above = [h for h in swing_highs if h['price'] > current_price]
    bsl_above.sort(key=lambda x: x['price'])
    nearest_bsl = bsl_above[0] if bsl_above else None

    # Find nearest SSL below current price
    ssl_below = [l for l in swing_lows if l['price'] < current_price]
    ssl_below.sort(key=lambda x: x['price'], reverse=True)
    nearest_ssl = ssl_below[0] if ssl_below else None

    return {
        "status": "success",
        "current_price": round(current_price, 2),
        "bsl": [{"price": round(h['price'], 2), "date": h['date']} for h in bsl],
        "ssl": [{"price": round(l['price'], 2), "date": l['date']} for l in ssl],
        "equal_highs": equal_highs,
        "equal_lows": equal_lows,
        "sweeps": sweeps,
        "nearest_bsl": {
            "price": round(nearest_bsl['price'], 2),
            "distance_pct": round((nearest_bsl['price'] - current_price) / current_price * 100, 2),
        } if nearest_bsl else None,
        "nearest_ssl": {
            "price": round(nearest_ssl['price'], 2),
            "distance_pct": round((current_price - nearest_ssl['price']) / current_price * 100, 2),
        } if nearest_ssl else None,
    }


def format_liquidity_report(data):
    """Format for Jarvis output."""
    if not data or data.get("status") != "success":
        return "⚠️ Liquidity analysis unavailable."

    lines = []
    lines.append(f"\n💧 LIQUIDITY ANALYSIS")
    lines.append(f"   Current: ₹{data['current_price']}")

    if data.get("nearest_bsl"):
        bsl = data['nearest_bsl']
        lines.append(f"\n🔴 Nearest BSL: ₹{bsl['price']} ({bsl['distance_pct']}% above)")
        lines.append(f"   → Shorts ka stop loss zone")

    if data.get("nearest_ssl"):
        ssl = data['nearest_ssl']
        lines.append(f"\n🟢 Nearest SSL: ₹{ssl['price']} ({ssl['distance_pct']}% below)")
        lines.append(f"   → Longs ka stop loss zone")

    if data.get("equal_highs"):
        lines.append(f"\n⚡ Equal Highs (Magnet):")
        for eh in data['equal_highs'][:2]:
            lines.append(f"   • ₹{eh['price']} (x{eh['count']})")

    if data.get("equal_lows"):
        lines.append(f"\n⚡ Equal Lows (Magnet):")
        for el in data['equal_lows'][:2]:
            lines.append(f"   • ₹{el['price']} (x{el['count']})")

    if data.get("sweeps"):
        lines.append(f"\n🎯 RECENT SWEEPS:")
        for s in data['sweeps']:
            emoji = "🔴" if s['type'] == "BEARISH_SWEEP" else "🟢"
            lines.append(f"   {emoji} ₹{s['level']} on {s['date']}")

    return "\n".join(lines)


# Standalone test
if __name__ == "__main__":
    import sys
    ticker = sys.argv[1] if len(sys.argv) > 1 else "RELIANCE.NS"
    print(f"\n{'=' * 60}\n  Testing Liquidity Analyzer — {ticker}\n{'=' * 60}")
    result = analyze_liquidity(ticker)
    print(format_liquidity_report(result))