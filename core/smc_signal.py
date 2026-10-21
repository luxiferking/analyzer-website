"""
core/smc_signal.py
Smart Money Concepts Confluence Engine.
Combines liquidity, structure, order blocks, and FVGs
into a single actionable signal.
"""
import pandas as pd
import yfinance as yf

from core.liquidity_analyzer import analyze_liquidity
from core.market_structure import analyze_market_structure
from core.order_blocks import analyze_order_blocks
from core.fair_value_gaps import analyze_fvgs


def _fetch_df(ticker, period="3mo", interval="1d"):
    """Fetch OHLCV via Upstox (replaces yfinance for reliability)."""
    import os
    import pandas as pd
    import requests as _req

    # Map yf ticker → Upstox instrument key
    upstox_map = {
        "^NSEI":     "NSE_INDEX|Nifty 50",
        "^NSEBANK":  "NSE_INDEX|Nifty Bank",
        "^CNXFIN":   "NSE_INDEX|Nifty Fin Service",
        "^NSEMDCP50":"NSE_INDEX|NIFTY MID SELECT",
        "^BSESN":    "BSE_INDEX|SENSEX",
    }

    # If it's an index, use Upstox
    instrument_key = upstox_map.get(ticker)

    # If not an index, convert stock ticker (RELIANCE.NS → NSE_EQ|INE002A01018 is complex)
    # For stocks, keep yfinance as fallback, but with timeout
    if not instrument_key:
        # Stock: try yfinance with a hard timeout
        import signal
        try:
            import yfinance as yf

            def _to(signum, frame):
                raise TimeoutError("yfinance timeout")

            old = signal.signal(signal.SIGALRM, _to)
            signal.alarm(8)
            try:
                df = yf.Ticker(ticker).history(period=period, interval=interval)
                if df.empty:
                    return None
                return df
            finally:
                signal.alarm(0)
                signal.signal(signal.SIGALRM, old)
        except Exception as e:
            print(f"   ⚠️ yfinance failed for {ticker}: {e}")
            return None

    # Upstox fetch
    access_token = os.getenv("UPSTOX_ANALYTICS_TOKEN")
    today = pd.Timestamp.now().strftime('%Y-%m-%d')
    from_date = (pd.Timestamp.now() - pd.Timedelta(days=90)).strftime('%Y-%m-%d')

    # Use daily candles for index
    url = (f"https://api.upstox.com/v3/historical-candle/"
           f"{instrument_key}/days/1/{today}/{from_date}")
    headers = {"Accept": "application/json",
               "Authorization": f"Bearer {access_token}"}
    try:
        r = _req.get(url, headers=headers, timeout=15)
        r.raise_for_status()
        data = r.json()
        candles = data.get("data", {}).get("candles", [])
        if not candles:
            return None
        df = pd.DataFrame(candles,
                          columns=['timestamp', 'Open', 'High', 'Low',
                                   'Close', 'Volume', 'OI'])
        df['timestamp'] = pd.to_datetime(df['timestamp'])
        df.set_index('timestamp', inplace=True)
        df.sort_index(inplace=True)
        return df
    except Exception as e:
        print(f"   ⚠️ Upstox SMC fetch failed for {instrument_key}: {e}")
        return None


def run_smc_analysis(ticker, period="3mo", interval="1d"):
    """
    Run full SMC confluence analysis.
    Returns detailed signal with confidence score.
    """
    df = _fetch_df(ticker, period, interval)
    if df is None or len(df) < 30:
        return {"status": "error", "message": "Insufficient data"}

    # Run all analyses
    liquidity = analyze_liquidity(df)
    structure = analyze_market_structure(df)
    obs = analyze_order_blocks(df)
    fvgs = analyze_fvgs(df)

    if any(r.get("status") != "success" for r in [liquidity, structure, obs, fvgs]):
        return {"status": "error", "message": "One or more analyses failed"}

    current_price = liquidity['current_price']

    # ═══════════════════════════════════════════════════
    # BULLISH CONFLUENCE CHECKS
    # ═══════════════════════════════════════════════════
    bull_checks = {
        "bullish_sweep": any(s['type'] == "BULLISH_SWEEP" for s in liquidity.get('sweeps', [])),
        "uptrend_structure": structure.get('structure') == "UPTREND",
        "bullish_choch": structure.get('choch', {}).get('type') == "BULLISH_CHOCH" if structure.get('choch') else False,
        "price_in_bull_ob": obs.get('price_in_bull_zone') is not None,
        "price_in_bull_fvg": fvgs.get('price_in_bull_fvg') is not None,
        "near_bull_ob": obs.get('nearest_bull_ob') is not None,
        "near_bull_fvg": fvgs.get('nearest_bull_fvg') is not None,
    }

    # ═══════════════════════════════════════════════════
    # BEARISH CONFLUENCE CHECKS
    # ═══════════════════════════════════════════════════
    bear_checks = {
        "bearish_sweep": any(s['type'] == "BEARISH_SWEEP" for s in liquidity.get('sweeps', [])),
        "downtrend_structure": structure.get('structure') == "DOWNTREND",
        "bearish_choch": structure.get('choch', {}).get('type') == "BEARISH_CHOCH" if structure.get('choch') else False,
        "price_in_bear_ob": obs.get('price_in_bear_zone') is not None,
        "price_in_bear_fvg": fvgs.get('price_in_bear_fvg') is not None,
        "near_bear_ob": obs.get('nearest_bear_ob') is not None,
        "near_bear_fvg": fvgs.get('nearest_bear_fvg') is not None,
    }

    bull_score = sum(1 for v in bull_checks.values() if v)
    bear_score = sum(1 for v in bear_checks.values() if v)

    # Determine signal
    if bull_score >= 5 and bull_score > bear_score:
        signal = "STRONG BUY"
        bias = "BULLISH"
        confidence = min(bull_score * 15, 95)
    elif bull_score >= 3 and bull_score > bear_score:
        signal = "WEAK BUY"
        bias = "BULLISH"
        confidence = bull_score * 12
    elif bear_score >= 5 and bear_score > bull_score:
        signal = "STRONG SELL"
        bias = "BEARISH"
        confidence = min(bear_score * 15, 95)
    elif bear_score >= 3 and bear_score > bull_score:
        signal = "WEAK SELL"
        bias = "BEARISH"
        confidence = bear_score * 12
    else:
        signal = "NO TRADE"
        bias = "NEUTRAL"
        confidence = 0

    # Calculate entry/SL/target using nearest zones
    entry, stop_loss, take_profit = None, None, None

    if "BUY" in signal:
        if obs.get('nearest_bull_ob'):
            entry = obs['nearest_bull_ob']['mid']
            stop_loss = obs['nearest_bull_ob']['bottom'] * 0.995  # Just below OB
        elif fvgs.get('nearest_bull_fvg'):
            entry = fvgs['nearest_bull_fvg']['mid']
            stop_loss = fvgs['nearest_bull_fvg']['bottom'] * 0.995

        if liquidity.get('nearest_bsl'):
            take_profit = liquidity['nearest_bsl']['price']

    elif "SELL" in signal:
        if obs.get('nearest_bear_ob'):
            entry = obs['nearest_bear_ob']['mid']
            stop_loss = obs['nearest_bear_ob']['top'] * 1.005
        elif fvgs.get('nearest_bear_fvg'):
            entry = fvgs['nearest_bear_fvg']['mid']
            stop_loss = fvgs['nearest_bear_fvg']['top'] * 1.005

        if liquidity.get('nearest_ssl'):
            take_profit = liquidity['nearest_ssl']['price']

    return {
        "status": "success",
        "ticker": ticker,
        "current_price": current_price,
        "signal": signal,
        "bias": bias,
        "confidence": confidence,
        "bull_score": bull_score,
        "bear_score": bear_score,
        "bull_checks": bull_checks,
        "bear_checks": bear_checks,
        "entry": round(entry, 2) if entry else None,
        "stop_loss": round(stop_loss, 2) if stop_loss else None,
        "take_profit": round(take_profit, 2) if take_profit else None,
        "liquidity": liquidity,
        "structure": structure,
        "order_blocks": obs,
        "fvgs": fvgs,
    }


def format_smc_report(data):
    """Full formatted SMC report for Jarvis."""
    if not data or data.get("status") != "success":
        return f"⚠️ SMC analysis unavailable: {data.get('message', 'Unknown')}"

    lines = []
    lines.append(f"\n🎯 SMART MONEY ANALYSIS — {data['ticker']}")
    lines.append(f"   Current Price: ₹{data['current_price']}")
    lines.append("=" * 55)

    # Signal
    sig = data['signal']
    emoji = "🟢" if "BUY" in sig else "🔴" if "SELL" in sig else "🟡"
    lines.append(f"\n{emoji} SIGNAL: {sig}  (Confidence: {data['confidence']}%)")
    lines.append(f"   Bias: {data['bias']}  (Bull {data['bull_score']} / Bear {data['bear_score']})")

    # Entry/SL/Target
    if data.get('entry'):
        lines.append(f"\n💰 TRADE PLAN:")
        lines.append(f"   Entry:       ₹{data['entry']}")
        lines.append(f"   Stop Loss:   ₹{data['stop_loss']}")
        lines.append(f"   Take Profit: ₹{data['take_profit']}")
        if data['entry'] and data['stop_loss'] and data['take_profit']:
            risk = abs(data['entry'] - data['stop_loss'])
            reward = abs(data['take_profit'] - data['entry'])
            if risk > 0:
                lines.append(f"   R:R Ratio:   1:{round(reward / risk, 1)}")

    # Confluence checks
    lines.append(f"\n✅ BULLISH CHECKS ({data['bull_score']}/7):")
    for k, v in data['bull_checks'].items():
        mark = "✅" if v else "❌"
        label = k.replace("_", " ").title()
        lines.append(f"   {mark} {label}")

    lines.append(f"\n❌ BEARISH CHECKS ({data['bear_score']}/7):")
    for k, v in data['bear_checks'].items():
        mark = "✅" if v else "❌"
        label = k.replace("_", " ").title()
        lines.append(f"   {mark} {label}")

    # Add structure summary
    st = data['structure']
    lines.append(f"\n📐 Structure: {st['structure']}")
    if st.get('bos'):
        lines.append(f"   BOS: {st['bos']['type']} at ₹{st['bos']['broken_level']}")
    if st.get('choch'):
        lines.append(f"   CHoCH: {st['choch']['type']} at ₹{st['choch']['broken_level']}")

    # Liquidity summary
    if data['liquidity'].get('nearest_bsl'):
        lines.append(f"\n💧 Nearest BSL: ₹{data['liquidity']['nearest_bsl']['price']}")
    if data['liquidity'].get('nearest_ssl'):
        lines.append(f"💧 Nearest SSL: ₹{data['liquidity']['nearest_ssl']['price']}")

    # Zone status
    if data['order_blocks'].get('price_in_bull_zone'):
        lines.append(f"\n📦 🟢 Price IN Bullish OB zone")
    if data['order_blocks'].get('price_in_bear_zone'):
        lines.append(f"\n📦 🔴 Price IN Bearish OB zone")
    if data['fvgs'].get('price_in_bull_fvg'):
        lines.append(f"\n🌫️ 🟢 Price IN Bullish FVG")
    if data['fvgs'].get('price_in_bear_fvg'):
        lines.append(f"\n🌫️ 🔴 Price IN Bearish FVG")

    return "\n".join(lines)


if __name__ == "__main__":
    import sys
    ticker = sys.argv[1] if len(sys.argv) > 1 else "RELIANCE.NS"
    print(f"\n{'=' * 60}\n  Testing SMC Signal — {ticker}\n{'=' * 60}")
    result = run_smc_analysis(ticker)
    print(format_smc_report(result))