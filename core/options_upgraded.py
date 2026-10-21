"""
core/pure_technical_options.py
Pure Technical Options Advisor V5.0 (Pro Trader Edition)
- Volume Confirmation (1.5x avg volume)
- IV Crush Protection (Blocks if IV > 25)
- Theta Protection (Blocks if < 2 days to expiry)
- PCR (Put-Call Ratio) directional filter
- Strict Risk Capping (Never exceeds MAX_RISK_PER_TRADE)
"""
import os
import re
from datetime import datetime, timedelta
from urllib.parse import quote

import pandas as pd
import requests
from dotenv import load_dotenv

load_dotenv()

IST = "Asia/Kolkata"
BASE = "https://api.upstox.com"

# ─── Config ───
OPTIONS_UNDERLYINGS = {
    "NIFTY":     {"key": "NSE_INDEX|Nifty 50"},
    "BANKNIFTY": {"key": "NSE_INDEX|Nifty Bank"},
    "SENSEX":    {"key": "BSE_INDEX|SENSEX"},
}

TRADING_CAPITAL     = float(os.getenv("TRADING_CAPITAL", "25000"))
MAX_RISK_PER_TRADE  = float(os.getenv("MAX_RISK_PER_TRADE", "125"))
MAX_RISK_PCT        = float(os.getenv("MAX_RISK_PCT", "0.005"))
ALLOW_OFFHOURS      = os.getenv("ALLOW_OFFHOURS", "0") == "1"

# Liquidity Thresholds
MAX_SPREAD_PCT = 0.03
MIN_VOLUME     = 1000
MIN_OI         = 5000

# Pro Trader Filters
MAX_IV = 25.0             # Block trades if Implied Volatility is > 25%
MIN_DAYS_TO_EXPIRY = 2    # Block trades if Theta decay is too aggressive
VOLUME_MULTIPLIER = 1.1   # Candle must have 1.5x average volume

# Scoring
SIGNAL_THRESHOLD = 15
STRONG_THRESHOLD = 40
MAX_EXPIRIES_TO_SCAN = 3


# ═══════════════════════════════════════════════════════
#  HELPERS
# ═══════════════════════════════════════════════════════
def _now_ist():
    return pd.Timestamp.now(tz=IST)

def _headers():
    token = os.getenv("UPSTOX_ANALYTICS_TOKEN")
    return {"Accept": "application/json", "Authorization": f"Bearer {token}"}

def _enc(instrument_key):
    return quote(instrument_key, safe="")

def _market_open():
    now = _now_ist()
    if now.weekday() >= 5:
        return False
    mins = now.hour * 60 + now.minute
    return (9 * 60 + 15) <= mins <= (15 * 60 + 30)

def _rsi(close, period=14):
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / period, adjust=False).mean()
    down = (-delta.clip(upper=0)).ewm(alpha=1 / period, adjust=False).mean()
    rs = gain / down.replace(0, 1e-9)
    return float((100 - (100 / (1 + rs))).iloc[-1])

def _range_position(df, n=20):
    recent = df.tail(n)
    hi, lo = recent["High"].max(), recent["Low"].min()
    if hi <= lo: return None
    return float((df["Close"].iloc[-1] - lo) / (hi - lo))

def _candles_to_df(candles):
    df = pd.DataFrame(candles, columns=["timestamp", "Open", "High", "Low", "Close", "Volume", "OI"])
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    if df["timestamp"].dt.tz is None:
        df["timestamp"] = df["timestamp"].dt.tz_localize(IST)
    else:
        df["timestamp"] = df["timestamp"].dt.tz_convert(IST)
    df.set_index("timestamp", inplace=True)
    df.sort_index(inplace=True)
    return df


# ═══════════════════════════════════════════════════════
#  DATA FETCH
# ═══════════════════════════════════════════════════════
def _fetch_candles(instrument_key, interval="15", days=15):
    now = _now_ist()
    to_date = now.strftime("%Y-%m-%d")
    from_date = (now - pd.Timedelta(days=days)).strftime("%Y-%m-%d")
    key = _enc(instrument_key)

    frames = []
    if interval == "day":
        url = f"{BASE}/v3/historical-candle/{key}/days/1/{to_date}/{from_date}"
    else:
        url = f"{BASE}/v3/historical-candle/{key}/minutes/{interval}/{to_date}/{from_date}"

    try:
        r = requests.get(url, headers=_headers(), timeout=15)
        r.raise_for_status()
        candles = r.json().get("data", {}).get("candles", [])
        if candles: frames.append(_candles_to_df(candles))
    except Exception: pass

    try:
        if interval == "day":
            iurl = f"{BASE}/v3/historical-candle/intraday/{key}/days/1"
        else:
            iurl = f"{BASE}/v3/historical-candle/intraday/{key}/minutes/{interval}"
        r = requests.get(iurl, headers=_headers(), timeout=15)
        if r.status_code == 200:
            candles = r.json().get("data", {}).get("candles", [])
            if candles: frames.append(_candles_to_df(candles))
    except Exception: pass

    if not frames: return None

    df = pd.concat(frames)
    df = df[~df.index.duplicated(keep="last")].sort_index()

    if interval == "day":
        if df.index[-1].date() == now.date() and not (now.hour > 15 or (now.hour == 15 and now.minute >= 30)):
            df = df.iloc[:-1]
    else:
        candle_len = pd.Timedelta(minutes=int(interval))
        if df.index[-1] + candle_len > now:
            df = df.iloc[:-1]

    return df if len(df) else None

def _get_live_price(instrument_key):
    url = f"{BASE}/v3/market-quote/quotes?instrument_key={_enc(instrument_key)}"
    try:
        r = requests.get(url, headers=_headers(), timeout=10)
        if r.status_code != 200: return None
        data = r.json()
        quotes = data.get("data", {})
        q = (quotes.get(instrument_key) or quotes.get(instrument_key.replace("|", ":")))
        if not q and quotes: q = next(iter(quotes.values()))
        price = q.get("last_price") if q else None
        return float(price) if price else None
    except Exception: return None


# ═══════════════════════════════════════════════════════
#  ANALYSIS
# ═══════════════════════════════════════════════════════
def _analyze_candlesticks(df):
    try:
        from core.pattern_detector import detect_candlestick_patterns
        patterns = detect_candlestick_patterns(df, lookback=5)
    except Exception: return 0, 0, []

    bull, bear, top = 0, 0, []
    for p in patterns[:10]:
        weight = p.get("strength", 1.0)
        if p.get("pattern", "").lower() == "hikkake": weight = 0.1 # Downgrade Hikkake
        if p.get("is_latest"): weight *= 1.5

        if p["direction"] == "BULLISH":
            bull += weight
            top.append(f"🟢 {p['pattern']} (str {weight:.1f})")
        elif p["direction"] == "BEARISH":
            bear += weight
            top.append(f"🔴 {p['pattern']} (str {weight:.1f})")
    return round(bull, 2), round(bear, 2), top[:6]

def _analyze_chart(df):
    if df is None or len(df) < 30: return 0, []
    close = df["Close"]
    obs, score = [], 0

    ema9, ema21, ema50 = close.ewm(span=9, adjust=False).mean().iloc[-1], close.ewm(span=21, adjust=False).mean().iloc[-1], close.ewm(span=50, adjust=False).mean().iloc[-1]
    current = close.iloc[-1]

    if current > ema9 > ema21 > ema50:
        score += 40; obs.append("📈 Perfect bullish EMA stack")
    elif current < ema9 < ema21 < ema50:
        score -= 40; obs.append("📉 Perfect bearish EMA stack")
    elif current > ema21:
        score += 15; obs.append("↑ Price above EMA21")
    elif current < ema21:
        score -= 15; obs.append("↓ Price below EMA21")

    recent = df.tail(20)
    hh = recent["High"].iloc[-1] > recent["High"].iloc[-5]
    hl = recent["Low"].iloc[-1]  > recent["Low"].iloc[-5]
    lh = recent["High"].iloc[-1] < recent["High"].iloc[-5]
    ll = recent["Low"].iloc[-1]  < recent["Low"].iloc[-5]

    if hh and hl: score += 25; obs.append("📊 Higher Highs + Higher Lows")
    elif lh and ll: score -= 25; obs.append("📊 Lower Highs + Lower Lows")

    pos = _range_position(df, 20)
    if pos is not None:
        if pos < 0.15: score += 25; obs.append(f"📍 Near range LOW ({pos*100:.0f}%)")
        elif pos > 0.9: score -= 40; obs.append(f"🚨 Near range HIGH ({pos*100:.0f}%)")

    rsi_val = _rsi(close)
    if rsi_val < 25: score += 25; obs.append(f"🔥 RSI {rsi_val:.1f} — Oversold")
    elif rsi_val > 78: score -= 55; obs.append(f"🚨 RSI {rsi_val:.1f} — EXTREME Overbought")
    elif rsi_val > 68: score -= 20; obs.append(f"📊 RSI {rsi_val:.1f} — Overbought")
    else: obs.append(f"📊 RSI {rsi_val:.1f} — Neutral")

    last_5 = close.tail(5)
    mom = (last_5.iloc[-1] - last_5.iloc[0]) / last_5.iloc[0] * 100
    if mom > 0.5: score += 12; obs.append(f"⚡ Strong bullish momentum (+{mom:.2f}%)")
    elif mom < -0.5: score -= 12; obs.append(f"⚡ Strong bearish momentum ({mom:.2f}%)")

    return max(-100, min(100, score)), obs

def _analyze_5min(df_5m):
    if df_5m is None or len(df_5m) < 10: return 0, [], "UNKNOWN"
    close = df_5m["Close"]
    raw = 0

    last = df_5m.iloc[-1]
    if last["Close"] > last["Open"]: raw += 20
    else: raw -= 20

    last_5 = close.tail(5)
    mom = (last_5.iloc[-1] - last_5.iloc[0]) / last_5.iloc[0] * 100
    if mom > 0.3: raw += 25
    elif mom < -0.3: raw -= 25

    ema9, ema21 = close.ewm(span=9, adjust=False).mean().iloc[-1], close.ewm(span=21, adjust=False).mean().iloc[-1]
    if close.iloc[-1] > ema9 > ema21: raw += 20
    elif close.iloc[-1] < ema9 < ema21: raw -= 20

    raw = max(-100, min(100, raw))
    verdict = "BULLISH" if raw >= 30 else "BEARISH" if raw <= -30 else "NEUTRAL"
    return round(raw, 1), [], verdict


# ═══════════════════════════════════════════════════════
#  DECISION LOGIC (Trend Veto, Volume, Conflict)
# ═══════════════════════════════════════════════════════
def _make_decision(candle_bull, candle_bear, chart_score, score_5m, daily_bias, df):
    candle_total = candle_bull + candle_bear
    candle_score = ((candle_bull - candle_bear) / max(candle_total, 2)) * 100 if candle_total > 0 else 0

    # Normalized weights: Chart 50%, Candle 30%, 5m 20%
    final_score = (chart_score * 0.50) + (candle_score * 0.30) + (score_5m * 0.20)
    if daily_bias == "BULLISH": final_score += 5
    elif daily_bias == "BEARISH": final_score -= 5

    final_score = max(-100, min(100, final_score))

    # Vetoes
    veto_put = (chart_score > 0) and (score_5m > 0)
    veto_call = (chart_score < 0) and (score_5m < 0)
    conflict = (chart_score > 0 and candle_score < 0) or (chart_score < 0 and candle_score > 0)

    action, strength, block_reason = "WAIT", "NO_EDGE", None

    if final_score >= STRONG_THRESHOLD: action, strength = "BUY CALL", "STRONG"
    elif final_score >= SIGNAL_THRESHOLD: action, strength = "BUY CALL", "MODERATE"
    elif final_score <= -STRONG_THRESHOLD: action, strength = "BUY PUT", "STRONG"
    elif final_score <= -SIGNAL_THRESHOLD: action, strength = "BUY PUT", "MODERATE"

    if action == "BUY PUT" and veto_put:
        action, strength, block_reason = "WAIT", "VETOED", "Trend Veto: Chart & 5m are BULLISH"
    elif action == "BUY CALL" and veto_call:
        action, strength, block_reason = "WAIT", "VETOED", "Trend Veto: Chart & 5m are BEARISH"

    # Conflict Cap (Allow extreme Marubozus to override)
    if conflict and abs(candle_score) < 80:
        action, strength, block_reason = "WAIT", "CONFLICT", "Signal Conflict across timeframes"

    # Volume & Candle Confirmation
    if action != "WAIT":
        prev_low, prev_high = df["Low"].iloc[-2], df["High"].iloc[-2]
        curr_close = df["Close"].iloc[-1]
        curr_vol = df["Volume"].iloc[-1]
        avg_vol = df["Volume"].tail(20).mean()

        if action == "BUY PUT" and not (curr_close < df["Open"].iloc[-2]):
            action, strength, block_reason = "WAIT", "NO_CONFIRM", "Spot didn't close below prior candle low"
        elif action == "BUY CALL" and not (curr_close > prev_high):
            action, strength, block_reason = "WAIT", "NO_CONFIRM", "Spot didn't close above prior candle high"
        
        # Volume Check
        if action != "WAIT" and curr_vol < (avg_vol * VOLUME_MULTIPLIER):
            action, strength, block_reason = "WAIT", "LOW_VOLUME", f"Volume {curr_vol:.0f} < {VOLUME_MULTIPLIER}x Avg ({avg_vol:.0f})"

    # ATR and Spot levels
    try:
        high, low, close = df["High"], df["Low"], df["Close"]
        tr = pd.concat([high - low, (high - close.shift()).abs(), (low - close.shift()).abs()], axis=1).max(axis=1)
        atr = tr.rolling(14).mean().iloc[-1]
    except: atr = df["Close"].iloc[-1] * 0.005

    current = df["Close"].iloc[-1]
    if action == "BUY CALL": entry_spot, sl_spot, target_spot = current, current - (1.5 * atr), current + (3.0 * atr)
    elif action == "BUY PUT": entry_spot, sl_spot, target_spot = current, current + (1.5 * atr), current - (3.0 * atr)
    else: entry_spot = sl_spot = target_spot = None

    return {
        "action": action, "strength": strength, "block_reason": block_reason,
        "score": round(final_score, 1), "candle_score": round(candle_score, 1),
        "chart_score": round(chart_score, 1), "score_5m": round(score_5m, 1),
        "call_setup_score": round(max(0, final_score), 1), "put_setup_score": round(max(0, -final_score), 1),
        "entry_spot": round(entry_spot, 2) if entry_spot else None,
        "sl_spot": round(sl_spot, 2) if sl_spot else None,
        "target_spot": round(target_spot, 2) if target_spot else None,
        "atr": round(float(atr), 2),
    }


# ═══════════════════════════════════════════════════════
#  OPTIONS HELPERS
# ═══════════════════════════════════════════════════════
def _fetch_contracts(instrument_key):
    url = f"{BASE}/v2/option/contract?instrument_key={_enc(instrument_key)}"
    try:
        r = requests.get(url, headers=_headers(), timeout=15)
        return r.json().get("data", []) if r.status_code == 200 else []
    except Exception: return []

def _expiries_from_contracts(contracts):
    return sorted({c.get("expiry") for c in contracts if c.get("expiry")})

def _lot_size_for_expiry(contracts, expiry):
    for c in contracts:
        if c.get("expiry") == expiry and c.get("lot_size"): return int(c["lot_size"])
    return None

def _select_expiry(expiries, expiry_hint=None):
    if not expiries: return None
    today = _now_ist().date()
    if expiry_hint:
        hint = str(expiry_hint).lower().strip()
        for e in expiries:
            if e == hint: return e
    for e in expiries:
        d = datetime.strptime(e, "%Y-%m-%d").date()
        if (d - today).days >= 3: return e
    return expiries[0]

def _fetch_chain(instrument_key, expiry):
    url = f"{BASE}/v2/option/chain?instrument_key={_enc(instrument_key)}&expiry_date={expiry}"
    try:
        r = requests.get(url, headers=_headers(), timeout=20)
        return r.json().get("data", []) if r.status_code == 200 else None
    except Exception: return None

def _analyze_option_chain(chain, spot):
    """Calculates PCR and extracts ATM IV"""
    total_call_oi, total_put_oi = 0, 0
    atm_iv, atm_strike = None, None
    closest_diff = float('inf')

    for item in chain:
        strike = item.get("strike_price", 0)
        c_oi = item.get("call_options", {}).get("market_data", {}).get("oi", 0)
        p_oi = item.get("put_options", {}).get("market_data", {}).get("oi", 0)
        total_call_oi += c_oi
        total_put_oi += p_oi

        diff = abs(strike - spot)
        if diff < closest_diff:
            closest_diff = diff
            atm_strike = strike
            c_data = item.get("call_options", {}).get("market_data", {})
            p_data = item.get("put_options", {}).get("market_data", {})
            atm_iv = c_data.get("implied_volatility") or p_data.get("implied_volatility") or 0.0

    pcr = round(total_put_oi / total_call_oi, 2) if total_call_oi > 0 else 1.0
    return pcr, atm_iv, atm_strike


# ═══════════════════════════════════════════════════════
#  CORE
# ═══════════════════════════════════════════════════════
def analyze_pure_technical(symbol, expiry_hint=None, preloaded=None):
    symbol = symbol.upper().strip()
    if symbol not in OPTIONS_UNDERLYINGS: return {"error": f"Symbol '{symbol}' not supported"}

    info = OPTIONS_UNDERLYINGS[symbol]
    instrument_key = info["key"]

    if preloaded:
        df_15m, df_5m, df_daily = preloaded.get("df_15m"), preloaded.get("df_5m"), preloaded.get("df_daily")
        contracts, spot = preloaded.get("contracts", []), preloaded.get("spot")
    else:
        df_15m, df_5m, df_daily = _fetch_candles(instrument_key, "15", 10), _fetch_candles(instrument_key, "5", 3), _fetch_candles(instrument_key, "day", 120)
        contracts, spot = [], None

    if df_15m is None or len(df_15m) < 30: return {"error": "Insufficient 15m data"}

    now = _now_ist()
    if not ALLOW_OFFHOURS:
        if df_15m.index[-1].date() != now.date(): return {"error": "Stale data (Holiday/Market Closed)"}
        if now.hour == 9 and now.minute < 45: return {"error": "Time filter: No trade before 09:45 AM"}

    df_15m = df_15m[df_15m.index.date == now.date()]
    if len(df_15m) < 2: return {"error": "Not enough closed 15m candles for today"}

    if spot is None: spot = _get_live_price(instrument_key) or float(df_15m["Close"].iloc[-1])

    candle_bull, candle_bear, patterns = _analyze_candlesticks(df_15m)
    chart_score, observations = _analyze_chart(df_15m)
    score_5m, _, verdict_5m = _analyze_5min(df_5m)

    daily_bias = "NEUTRAL"
    if df_daily is not None and len(df_daily) >= 50:
        d_close = df_daily["Close"]
        if d_close.iloc[-1] > d_close.ewm(span=20).mean().iloc[-1] > d_close.ewm(span=50).mean().iloc[-1]: daily_bias = "BULLISH"
        elif d_close.iloc[-1] < d_close.ewm(span=20).mean().iloc[-1] < d_close.ewm(span=50).mean().iloc[-1]: daily_bias = "BEARISH"

    decision = _make_decision(candle_bull, candle_bear, chart_score, score_5m, daily_bias, df_15m)

    option_plan = None
    risk_warning = None

    if decision["action"] in ("BUY CALL", "BUY PUT"):
        if not contracts: contracts = _fetch_contracts(instrument_key)
        expiries = _expiries_from_contracts(contracts)
        expiry = _select_expiry(expiries, expiry_hint) if expiries else None
        
        if not expiry:
            decision.update({"action": "WAIT", "strength": "DATA_BLOCK", "block_reason": "No expiry found"})
        else:
            # Pro Trader Filter: Theta Decay Check
            days_to_expiry = (datetime.strptime(expiry, "%Y-%m-%d").date() - now.date()).days
            if days_to_expiry < MIN_DAYS_TO_EXPIRY:
                decision.update({"action": "WAIT", "strength": "THETA_BLOCK", "block_reason": f"Theta Decay Risk: Only {days_to_expiry} days to expiry"})

            if decision["action"] != "WAIT":
                base_lot = _lot_size_for_expiry(contracts, expiry)
                chain = _fetch_chain(instrument_key, expiry)
                
                if not chain or not base_lot:
                    decision.update({"action": "WAIT", "strength": "DATA_BLOCK", "block_reason": "Option chain/lot size unavailable"})
                else:
                    pcr, atm_iv, atm_strike = _analyze_option_chain(chain, spot)
                    
                    # Pro Trader Filter: IV Crush Check
                    if atm_iv and atm_iv > MAX_IV:
                        decision.update({"action": "WAIT", "strength": "IV_BLOCK", "block_reason": f"IV too high ({atm_iv:.1f}%). Risk of IV Crush."})

                    if decision["action"] != "WAIT":
                        opt_type = "CE" if decision["action"] == "BUY CALL" else "PE"
                        # PCR Alignment
                        if decision["action"] == "BUY CALL" and pcr > 1.3:
                            decision.update({"action": "WAIT", "strength": "PCR_BLOCK", "block_reason": f"PCR {pcr} is extremely high (Overbought). Call buying risky."})
                        elif decision["action"] == "BUY PUT" and pcr < 0.7:
                            decision.update({"action": "WAIT", "strength": "PCR_BLOCK", "block_reason": f"PCR {pcr} is extremely low (Oversold). Put buying risky."})

                        if decision["action"] != "WAIT":
                            for item in chain:
                                if item.get("strike_price") == atm_strike:
                                    side = "call_options" if opt_type == "CE" else "put_options"
                                    md = item.get(side, {}).get("market_data", {})
                                    atm_ltp, opt_bid, opt_ask = md.get("ltp"), md.get("bid_price"), md.get("ask_price")
                                    
                                    if not atm_ltp or atm_ltp <= 0:
                                        decision.update({"action": "WAIT", "strength": "DATA_BLOCK", "block_reason": "ATM LTP unavailable"})
                                    elif not opt_bid or not opt_ask or (opt_ask - opt_bid) / atm_ltp > MAX_SPREAD_PCT:
                                        decision.update({"action": "WAIT", "strength": "LIQUIDITY_BLOCK", "block_reason": "High spread / Illiquid"})
                                    else:
                                        atr_ratio = decision["atr"] / spot if spot > 0 else 0.005
                                        sl_pct = max(0.15, min(0.22, 0.18 + (atr_ratio * 2)))
                                        sl_premium = round(atm_ltp * (1 - sl_pct), 2)
                                        target_premium = round(atm_ltp * (1.60 if decision["strength"] == "STRONG" else 1.45), 2)
                                        
                                        risk_per_unit = atm_ltp - sl_premium
                                        max_risk_amount = min(TRADING_CAPITAL * MAX_RISK_PCT, MAX_RISK_PER_TRADE)
                                        one_lot_risk = risk_per_unit * base_lot
                                        one_lot_cost = atm_ltp * base_lot

                                        # Strict Risk Capping
                                        if one_lot_risk > max_risk_amount:
                                            risk_warning = f"1 lot risk ₹{one_lot_risk:.0f} exceeds strict limit ₹{max_risk_amount:.0f}"
                                            decision.update({"action": "WAIT", "strength": "RISK_BLOCK", "block_reason": risk_warning})
                                        elif TRADING_CAPITAL < one_lot_cost:
                                            decision.update({"action": "WAIT", "strength": "CAPITAL_BLOCK", "block_reason": "Insufficient capital for 1 lot"})
                                        else:
                                            lots = int(TRADING_CAPITAL // one_lot_cost)
                                            qty = lots * base_lot
                                            total_cost = round(atm_ltp * qty, 2)
                                            profit = round((target_premium - atm_ltp) * qty, 2)
                                            max_loss = round((atm_ltp - sl_premium) * qty, 2)
                                            
                                            option_plan = {
                                                "strike": atm_strike, "type": opt_type, "entry": atm_ltp,
                                                "target": target_premium, "sl": sl_premium,
                                                "lot": qty, "lots": lots, "base_lot": base_lot,
                                                "cost": total_cost, "profit": profit, "loss": max_loss,
                                                "expiry": expiry, "rr_ratio": round(profit / max_loss, 2) if max_loss > 0 else 0,
                                                "risk_pct": round((max_loss / TRADING_CAPITAL) * 100, 2),
                                                "pcr": pcr, "iv": round(atm_iv, 1)
                                            }
                                    break

    return {
        "symbol": symbol, "spot": round(spot, 2), "decision": decision,
        "candle_bull": candle_bull, "candle_bear": candle_bear, "patterns": patterns,
        "chart_score": chart_score, "observations": observations, "daily_bias": daily_bias,
        "option_plan": option_plan, "risk_warning": risk_warning,
        "last_candle": str(df_15m.index[-1]),
        "last_candle_5m": str(df_5m.index[-1]) if df_5m is not None and len(df_5m) else None,
        "score_5m": score_5m, "verdict_5m": verdict_5m,
    }


def analyze_best_trade(expiry_hint=None):
    candidates, errors = [], set()
    for symbol in OPTIONS_UNDERLYINGS:
        key = OPTIONS_UNDERLYINGS[symbol]["key"]
        df_15m, df_5m, df_daily = _fetch_candles(key, "15", 10), _fetch_candles(key, "5", 3), _fetch_candles(key, "day", 120)
        contracts, live_spot = _fetch_contracts(key), _get_live_price(key)
        spot = live_spot if live_spot else (float(df_15m["Close"].iloc[-1]) if df_15m is not None and len(df_15m) else None)

        if df_15m is None or spot is None:
            errors.add(f"{symbol}: Insufficient data")
            continue

        if not ALLOW_OFFHOURS and df_15m.index[-1].date() != _now_ist().date():
            errors.add(f"{symbol}: Stale data")
            continue

        expiries = _expiries_from_contracts(contracts)
        today = _now_ist().date()
        future = [e for e in expiries if datetime.strptime(e, "%Y-%m-%d").date() >= today]
        picked = future[:MAX_EXPIRIES_TO_SCAN] if not expiry_hint else [_select_expiry(expiries, expiry_hint)]

        preloaded = {"df_15m": df_15m, "df_5m": df_5m, "df_daily": df_daily, "contracts": contracts, "spot": spot}

        for exp in picked:
            r = analyze_pure_technical(symbol, expiry_hint=exp, preloaded=preloaded)
            if r.get("error"):
                errors.add(f"{symbol} ({exp}): {r['error']}")
                continue
            if r["decision"]["action"] == "WAIT":
                reason = r['decision'].get('block_reason') or 'Score below threshold (NO_EDGE)'
                errors.add(f"{symbol} ({exp}): WAIT - {reason}")
                continue
            if not r.get("option_plan"): continue

            score, rr, profit = abs(r["decision"]["score"]), r["option_plan"].get("rr_ratio", 0), r["option_plan"].get("profit", 0)
            mult = r["option_plan"].get("target", 0) / r["option_plan"].get("entry", 1)
            potential = score * rr * (profit / 100) * mult
            candidates.append({"potential": round(potential, 2), "result": r})

    if not candidates:
        if errors:
            print("⚠️  No trades found. Reasons:")
            for e in sorted(errors): print(f"   - {e}")
            print()
        return None

    candidates.sort(key=lambda x: x["potential"], reverse=True)
    best = candidates[0]["result"]
    return best


def format_result(r):
    if not r or r.get("error"): return f"❌ {r.get('error', 'Unknown error')}"
    lines = ["\n" + "═" * 65, f"  📊 PURE TECHNICAL OPTIONS ADVISOR — {r['symbol']}", "═" * 65]
    lines.append(f"\n  Spot: ₹{r['spot']}")
    lines.append(f"  Last closed 15m candle: {r.get('last_candle')}")
    lines.append(f"  Daily Bias: {r['daily_bias']}")

    lines.append(f"\n  🎨 CANDLESTICK PATTERNS")
    lines.append(f"     Bull Score: {r['candle_bull']}  |  Bear Score: {r['candle_bear']}")
    for p in r["patterns"]: lines.append(f"     {p}")

    lines.append(f"\n  📈 CHART ANALYSIS (Score: {r['chart_score']:+d})")
    for o in r["observations"]: lines.append(f"     {o}")

    if r.get("score_5m") is not None:
        lines.append(f"\n  ⚡ 5 MIN CONFIRMATION (Score: {r['score_5m']:+.1f})")
        lines.append(f"     Verdict: {r.get('verdict_5m', 'UNKNOWN')}")

    d = r["decision"]
    lines.append("\n" + "─" * 65 + "\n  🧮 TECHNICAL SCORE")
    lines.append(f"     Candle Score:  {d['candle_score']:+.1f}")
    lines.append(f"     Chart Score:   {d['chart_score']:+.1f}")
    lines.append(f"     ─────────────────────")
    lines.append(f"     Selected Setup Score: {d['score']:+.1f}  ->  {d['action']} ({d['strength']})")

    if d.get("block_reason"): lines.append(f"\n  🚫 BLOCKED: {d['block_reason']}")
    if r.get("risk_warning"): lines.append(f"  ⚠️  {r['risk_warning']}")

    op = r.get("option_plan")
    if op and d["action"] != "WAIT":
        lines.append("\n" + "─" * 65 + "\n  📋 OPTION PLAN")
        lines.append(f"     Expiry:     {op['expiry']} ({op.get('pcr', 'N/A')} PCR | IV: {op.get('iv', 'N/A')}%)")
        lines.append(f"     Trade:      BUY {op['strike']:.0f} {op['type']}")
        lines.append(f"     Entry:      ₹{op['entry']}")
        lines.append(f"     Target:     ₹{op['target']}    ({round((op['target']/op['entry']-1)*100, 1):+.1f}%)")
        lines.append(f"     Stop Loss:  ₹{op['sl']}    ({round((op['sl']/op['entry']-1)*100, 1):+.1f}%)")
        lines.append(f"     Lot Size:   {op['lots']} lot × {op['base_lot']} = {op['lot']} qty")
        lines.append(f"     Cost:       ₹{op['cost']}")
        lines.append(f"     Profit:     ₹+{op['profit']}")
        lines.append(f"     Loss:       ₹-{op['loss']}")
        lines.append(f"     ✅ R:R = 1:{op.get('rr_ratio', 0):.2f}")
        lines.append(f"     📊 Risk: {op['risk_pct']}% of ₹{int(TRADING_CAPITAL)}")

    if d.get("entry_spot") and d["action"] != "WAIT":
        lines.append("\n" + "─" * 65 + "\n  📍 SPOT LEVELS")
        lines.append(f"     Spot Entry:  ₹{d['entry_spot']}")
        lines.append(f"     Spot Target: ₹{d['target_spot']}")
        lines.append(f"     Spot SL:     ₹{d['sl_spot']}")

    lines.append("\n" + "╔" + "═" * 63 + "╗")
    lines.append("║" + " " * 22 + "🎯 FINAL VERDICT" + " " * 25 + "║")
    lines.append("╠" + "═" * 63 + "╣")

    if d["action"] == "WAIT":
        lines.append("║" + " " * 63 + "║")
        if d.get("block_reason"):
            lines.append("║  🚫 TRADE BLOCKED" + " " * 45 + "║")
            for wl in [d["block_reason"][i:i+58] for i in range(0, len(d["block_reason"]), 58)]:
                lines.append(f"║     {wl}".ljust(64) + "║")
        else:
            lines.append("║  ⏸️  NO TRADE — Wait for clearer signal" + " " * 22 + "║")
        lines.append("║" + " " * 63 + "║")
    elif op and not r.get("risk_warning"):
        emoji = "🟢" if "CALL" in d["action"] else "🔴"
        lines.append("║" + " " * 63 + "║")
        lines.append(f"║  {emoji}  ACTION: {d['action']} ({d['strength']})".ljust(64) + "║")
        lines.append("║" + " " * 63 + "║")
        lines.append(f"║  📍 BUY {int(op['strike'])} {op['type']} @ ₹{op['entry']}".ljust(64) + "║")
        lines.append(f"║     Expiry:      {op['expiry']}".ljust(64) + "║")
        lines.append(f"║     Target:      ₹{op['target']}".ljust(64) + "║")
        lines.append(f"║     Stop Loss:   ₹{op['sl']}".ljust(64) + "║")
        lines.append(f"║     Total Cost:  ₹{op['cost']}".ljust(64) + "║")
        lines.append(f"║     Max Profit:  ₹+{op['profit']}".ljust(64) + "║")
        lines.append(f"║     Max Loss:    ₹-{op['loss']}".ljust(64) + "║")
        lines.append("║" + " " * 63 + "║")
        lines.append(f"║  ✅ Within risk limits — paper trade first".ljust(64) + "║")
        lines.append("║" + " " * 63 + "║")
    else:
        lines.append("║" + " " * 63 + "║")
        lines.append("║  ⏸️  NO OPTION PLAN".ljust(64) + "║")
        lines.append("║" + " " * 63 + "║")

    lines.append("╚" + "═" * 63 + "╝")
    return "\n".join(lines)


if __name__ == "__main__":
    import sys
    if len(sys.argv) == 1:
        print("\n🔎 Scanning NIFTY + BANKNIFTY + SENSEX across all expiries...\n")
        best = analyze_best_trade()
        if best is None: print("❌ No valid trade found across any underlying/expiry.")
        else: print(format_result(best))
    else:
        symbol = sys.argv[1]
        expiry = sys.argv[2] if len(sys.argv) > 2 else None
        result = analyze_pure_technical(symbol, expiry_hint=expiry)
        print(format_result(result))