"""
core/order_blocks.py
Detects Bullish and Bearish Order Blocks.
OB = last opposite candle before a strong impulse move.
"""
import pandas as pd
import numpy as np
import yfinance as yf


def _detect_order_blocks(df, lookback=50, min_move_pct=1.2, body_mult=0.7):
    """
    Order Block = last opposite candle before a strong move.
    - Bullish OB: last red candle before strong bullish impulse
    - Bearish OB: last green candle before strong bearish impulse
    """
    if df is None or len(df) < 15:
        return []

    obs = []
    recent = df.tail(lookback + 5)
    avg_body = (recent['Close'] - recent['Open']).abs().mean()

    for i in range(2, len(recent) - 4):
        candle = recent.iloc[i]
        nxt = recent.iloc[i + 1:i + 5]  # Next 4 candles

        body = abs(candle['Close'] - candle['Open'])

        # Bullish OB: red candle → strong up move
        if candle['Close'] < candle['Open'] and body > avg_body * body_mult:
            move_up = (nxt['High'].max() - candle['High']) / candle['High'] * 100
            if move_up > min_move_pct:
                obs.append({
                    "type": "BULLISH_OB",
                    "top": round(float(candle['High']), 2),
                    "bottom": round(float(candle['Low']), 2),
                    "mid": round((float(candle['High']) + float(candle['Low'])) / 2, 2),
                    "date": str(recent.index[i])[:10],
                    "strength_pct": round(move_up, 2),
                    "meaning": "Last red candle before bullish impulse",
                })

        # Bearish OB: green candle → strong down move
        if candle['Close'] > candle['Open'] and body > avg_body * body_mult:
            move_down = (candle['Low'] - nxt['Low'].min()) / candle['Low'] * 100
            if move_down > min_move_pct:
                obs.append({
                    "type": "BEARISH_OB",
                    "top": round(float(candle['High']), 2),
                    "bottom": round(float(candle['Low']), 2),
                    "mid": round((float(candle['High']) + float(candle['Low'])) / 2, 2),
                    "date": str(recent.index[i])[:10],
                    "strength_pct": round(move_down, 2),
                    "meaning": "Last green candle before bearish impulse",
                })

    # Keep only the most recent, non-overlapping OBs
    obs.sort(key=lambda x: x['date'], reverse=True)

    # Deduplicate overlapping zones
    final = []
    for ob in obs:
        overlap = False
        for f in final:
            # Check if zones overlap by more than 50%
            top = min(ob['top'], f['top'])
            bottom = max(ob['bottom'], f['bottom'])
            if top > bottom:
                ob_range = ob['top'] - ob['bottom']
                overlap_range = top - bottom
                if overlap_range / ob_range > 0.5:
                    overlap = True
                    break
        if not overlap:
            final.append(ob)
        if len(final) >= 5:
            break

    return final


def check_price_in_zone(current_price, obs, ob_type=None):
    """Check if current price is inside any OB zone."""
    for ob in obs:
        if ob_type and ob['type'] != ob_type:
            continue
        if ob['bottom'] <= current_price <= ob['top']:
            return ob
    return None


def analyze_order_blocks(ticker_or_df, period="3mo", interval="1d"):
    """Full OB analysis."""
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
    obs = _detect_order_blocks(df)

    # Find nearest OB above and below current price
    bull_obs = [o for o in obs if o['type'] == "BULLISH_OB"]
    bear_obs = [o for o in obs if o['type'] == "BEARISH_OB"]

    nearest_bull_ob = None
    if bull_obs:
        bull_obs.sort(key=lambda x: abs(x['mid'] - current_price))
        nearest_bull_ob = bull_obs[0]

    nearest_bear_ob = None
    if bear_obs:
        bear_obs.sort(key=lambda x: abs(x['mid'] - current_price))
        nearest_bear_ob = bear_obs[0]

    # Check if price is IN any zone
    in_bull_zone = check_price_in_zone(current_price, obs, "BULLISH_OB")
    in_bear_zone = check_price_in_zone(current_price, obs, "BEARISH_OB")

    return {
        "status": "success",
        "current_price": round(current_price, 2),
        "all_obs": obs,
        "nearest_bull_ob": nearest_bull_ob,
        "nearest_bear_ob": nearest_bear_ob,
        "price_in_bull_zone": in_bull_zone,
        "price_in_bear_zone": in_bear_zone,
    }


def format_ob_report(data):
    """Format for Jarvis."""
    if not data or data.get("status") != "success":
        return "⚠️ Order block analysis unavailable."

    lines = []
    lines.append(f"\n📦 ORDER BLOCKS")
    lines.append(f"   Current: ₹{data['current_price']}")

    if data.get("price_in_bull_zone"):
        ob = data['price_in_bull_zone']
        lines.append(f"\n   🟢 INSIDE Bullish OB: ₹{ob['bottom']} – ₹{ob['top']}")
        lines.append(f"      → High-probability BUY zone")

    if data.get("price_in_bear_zone"):
        ob = data['price_in_bear_zone']
        lines.append(f"\n   🔴 INSIDE Bearish OB: ₹{ob['bottom']} – ₹{ob['top']}")
        lines.append(f"      → High-probability SELL zone")

    if data.get("nearest_bull_ob") and not data.get("price_in_bull_zone"):
        ob = data['nearest_bull_ob']
        lines.append(f"\n   🟢 Nearest Bullish OB: ₹{ob['bottom']} – ₹{ob['top']}")
        lines.append(f"      (from {ob['date']}, strength {ob['strength_pct']}%)")

    if data.get("nearest_bear_ob") and not data.get("price_in_bear_zone"):
        ob = data['nearest_bear_ob']
        lines.append(f"\n   🔴 Nearest Bearish OB: ₹{ob['bottom']} – ₹{ob['top']}")
        lines.append(f"      (from {ob['date']}, strength {ob['strength_pct']}%)")

    return "\n".join(lines)


if __name__ == "__main__":
    import sys
    ticker = sys.argv[1] if len(sys.argv) > 1 else "RELIANCE.NS"
    print(f"\n{'=' * 60}\n  Testing Order Blocks — {ticker}\n{'=' * 60}")
    result = analyze_order_blocks(ticker)
    print(format_ob_report(result))