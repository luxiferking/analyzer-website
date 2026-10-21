"""
core/pure_technical_options.py
Pure Technical Options Advisor V4.1 (Holiday & Stale Data Fix)
- Chart + Candlestick only (5m is a bonus, NOT a penalty)
- Multi-underlying, multi-expiry scan (NIFTY / BANKNIFTY / SENSEX)
- Returns the single BEST trade with highest profit potential
- 🚨 FIXED: Blocks trades if data is stale (e.g., market holiday)
"""
import os
import re
from datetime import datetime
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

# Scoring
SIGNAL_THRESHOLD = 20        # lowered from 25 — trades now fire
STRONG_THRESHOLD = 40

# How many expiries per underlying to scan (nearest N)
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
    if hi <= lo:
        return None
    return float((df["Close"].iloc[-1] - lo) / (hi - lo))


def _candles_to_df(candles):
    df = pd.DataFrame(candles, columns=["timestamp", "Open", "High", "Low",
                                        "Close", "Volume", "OI"])
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
        if candles:
            frames.append(_candles_to_df(candles))
    except Exception as e:
        print(f"   ⚠️ Historical fetch failed ({interval}): {e}")

    try:
        if interval == "day":
            iurl = f"{BASE}/v3/historical-candle/intraday/{key}/days/1"
        else:
            iurl = f"{BASE}/v3/historical-candle/intraday/{key}/minutes/{interval}"
        r = requests.get(iurl, headers=_headers(), timeout=15)
        if r.status_code == 200:
            candles = r.json().get("data", {}).get("candles", [])
            if candles:
                frames.append(_candles_to_df(candles))
    except Exception as e:
        print(f"   ⚠️ Intraday fetch failed ({interval}): {e}")

    if not frames:
        return None

    df = pd.concat(frames)
    df = df[~df.index.duplicated(keep="last")].sort_index()

    if interval == "day":
        if df.index[-1].date() == now.date() and not (
            now.hour > 15 or (now.hour == 15 and now.minute >= 30)
        ):
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
        if r.status_code != 200:
            return None
        data = r.json()
        if data.get("status") != "success":
            return None
        quotes = data.get("data", {})
        if not quotes:
            return None

        q = (quotes.get(instrument_key)
             or quotes.get(instrument_key.replace("|", ":")))
        if q is None:
            for v in quotes.values():
                if v.get("instrument_token") == instrument_key:
                    q = v
                    break
        if q is None and len(quotes) == 1:
            q = next(iter(quotes.values()))
        if q is None:
            return None

        price = q.get("last_price") or q.get("ohlc", {}).get("close")
        return float(price) if price else None
    except Exception as e:
        print(f"   ⚠️ Live price failed: {e}")
        return None


# ═══════════════════════════════════════════════════════
#  CANDLESTICK ANALYSIS
# ═══════════════════════════════════════════════════════
def _analyze_candlesticks(df):
    try:
        from core.pattern_detector import detect_candlestick_patterns
        patterns = detect_candlestick_patterns(df, lookback=5)
    except Exception as e:
        print(f"   ⚠️ Pattern detector failed: {e}")
        return 0, 0, []

    bull = 0
    bear = 0
    top = []

    for p in patterns[:10]:
        weight = p.get("strength", 1.0)
        if p.get("is_latest"):
            weight *= 1.5

        if p["direction"] == "BULLISH":
            bull += weight
            top.append(f"🟢 {p['pattern']} (str {p.get('strength', 1.0)})")
        elif p["direction"] == "BEARISH":
            bear += weight
            top.append(f"🔴 {p['pattern']} (str {p.get('strength', 1.0)})")

    return round(bull, 2), round(bear, 2), top[:6]


# ═══════════════════════════════════════════════════════
#  CHART ANALYSIS (15m)  — trend + structure + momentum
# ═══════════════════════════════════════════════════════
def _analyze_chart(df):
    if df is None or len(df) < 30:
        return 0, []

    close = df["Close"]
    observations = []
    score = 0

    ema9  = close.ewm(span=9,  adjust=False).mean().iloc[-1]
    ema21 = close.ewm(span=21, adjust=False).mean().iloc[-1]
    ema50 = close.ewm(span=50, adjust=False).mean().iloc[-1]
    current = close.iloc[-1]

    if current > ema9 > ema21 > ema50:
        score += 40
        observations.append("📈 Perfect bullish EMA stack (9>21>50)")
    elif current < ema9 < ema21 < ema50:
        score -= 40
        observations.append("📉 Perfect bearish EMA stack (9<21<50)")
    elif current > ema21:
        score += 15
        observations.append("↑ Price above EMA21")
    elif current < ema21:
        score -= 15
        observations.append("↓ Price below EMA21")

    recent = df.tail(20)
    hh = recent["High"].iloc[-1] > recent["High"].iloc[-5]
    hl = recent["Low"].iloc[-1]  > recent["Low"].iloc[-5]
    lh = recent["High"].iloc[-1] < recent["High"].iloc[-5]
    ll = recent["Low"].iloc[-1]  < recent["Low"].iloc[-5]

    if hh and hl:
        score += 25
        observations.append("📊 Higher Highs + Higher Lows (uptrend)")
    elif lh and ll:
        score -= 25
        observations.append("📊 Lower Highs + Lower Lows (downtrend)")

    pos = _range_position(df, 20)
    if pos is not None:
        if pos < 0.15:
            score += 25
            observations.append(f"📍 Near range LOW ({pos*100:.0f}%) — strong bounce")
        elif pos < 0.25:
            score += 15
            observations.append(f"📍 Near range LOW ({pos*100:.0f}%)")
        elif pos > 0.9:
            score -= 40
            observations.append(f"🚨 Near range HIGH ({pos*100:.0f}%) — EXTREME resistance")
        elif pos > 0.8:
            score -= 25
            observations.append(f"📍 Near range HIGH ({pos*100:.0f}%) — resistance")

    try:
        rsi_val = _rsi(close)
        if rsi_val < 25:
            score += 25
            observations.append(f"🔥 RSI {rsi_val:.1f} — EXTREME Oversold")
        elif rsi_val < 30:
            score += 15
            observations.append(f"📊 RSI {rsi_val:.1f} — Oversold")
        elif rsi_val > 78:
            score -= 55
            observations.append(f"🚨 RSI {rsi_val:.1f} — EXTREME Overbought")
        elif rsi_val > 72:
            score -= 40
            observations.append(f"🚨 RSI {rsi_val:.1f} — Strong Overbought")
        elif rsi_val > 68:
            score -= 20
            observations.append(f"📊 RSI {rsi_val:.1f} — Overbought")
        else:
            observations.append(f"📊 RSI {rsi_val:.1f} — Neutral")
    except Exception:
        pass

    last_5_close = close.tail(5)
    momentum = (last_5_close.iloc[-1] - last_5_close.iloc[0]) / last_5_close.iloc[0] * 100
    if momentum > 0.5:
        score += 12
        observations.append(f"⚡ Strong bullish momentum (+{momentum:.2f}%)")
    elif momentum > 0.2:
        score += 6
        observations.append(f"⚡ Mild bullish momentum (+{momentum:.2f}%)")
    elif momentum < -0.5:
        score -= 12
        observations.append(f"⚡ Strong bearish momentum ({momentum:.2f}%)")
    elif momentum < -0.2:
        score -= 6
        observations.append(f"⚡ Mild bearish momentum ({momentum:.2f}%)")

    return max(-100, min(100, score)), observations


# ═══════════════════════════════════════════════════════
#  5 MIN CONFIRMATION — BONUS ONLY, NEVER A PENALTY
# ═══════════════════════════════════════════════════════
def _analyze_5min(df_5m):
    if df_5m is None or len(df_5m) < 10:
        return 0, ["⚠️ 5m data insufficient"], "UNKNOWN"

    close = df_5m["Close"]
    obs = []
    raw = 0

    last = df_5m.iloc[-1]
    if last["Close"] > last["Open"]:
        body_pct = (last["Close"] - last["Open"]) / last["Open"] * 100
        raw += 20 if body_pct > 0.15 else 10
        obs.append(f"🟢 {'Strong' if body_pct > 0.15 else 'Mild'} 5m bullish candle (+{body_pct:.2f}%)")
    else:
        body_pct = (last["Open"] - last["Close"]) / last["Open"] * 100
        raw -= 20 if body_pct > 0.15 else 10
        obs.append(f"🔴 {'Strong' if body_pct > 0.15 else 'Mild'} 5m bearish candle (-{body_pct:.2f}%)")

    last_5 = close.tail(5)
    momentum = (last_5.iloc[-1] - last_5.iloc[0]) / last_5.iloc[0] * 100
    if momentum > 0.3:
        raw += 25
        obs.append(f"⚡ Strong 5m bullish momentum (+{momentum:.2f}%)")
    elif momentum > 0.1:
        raw += 12
        obs.append(f"⚡ Mild 5m bullish momentum (+{momentum:.2f}%)")
    elif momentum < -0.3:
        raw -= 25
        obs.append(f"⚡ Strong 5m bearish momentum ({momentum:.2f}%)")
    elif momentum < -0.1:
        raw -= 12
        obs.append(f"⚡ Mild 5m bearish momentum ({momentum:.2f}%)")
    else:
        obs.append(f"➡️ 5m flat momentum ({momentum:.2f}%)")

    ema9  = close.ewm(span=9,  adjust=False).mean().iloc[-1]
    ema21 = close.ewm(span=21, adjust=False).mean().iloc[-1]
    cur = close.iloc[-1]
    if cur > ema9 > ema21:
        raw += 20
        obs.append("📈 5m EMA stack bullish (9>21)")
    elif cur < ema9 < ema21:
        raw -= 20
        obs.append("📉 5m EMA stack bearish (9<21)")

    raw = max(-100, min(100, raw))

    if raw >= 30:
        verdict = "BULLISH"
    elif raw <= -30:
        verdict = "BEARISH"
    else:
        verdict = "NEUTRAL"

    return round(raw, 1), obs, verdict


# ═══════════════════════════════════════════════════════
#  DECISION — pure chart + candles
# ═══════════════════════════════════════════════════════
def _make_decision(candle_bull, candle_bear, chart_score, df):
    candle_total = candle_bull + candle_bear
    candle_score = 0
    if candle_total > 0:
        candle_score = ((candle_bull - candle_bear) / max(candle_total, 2)) * 100

    final_score = (candle_score * 0.5) + (chart_score * 0.5)

    if final_score >= STRONG_THRESHOLD:
        action, strength = "BUY CALL", "STRONG"
    elif final_score >= SIGNAL_THRESHOLD:
        action, strength = "BUY CALL", "MODERATE"
    elif final_score <= -STRONG_THRESHOLD:
        action, strength = "BUY PUT", "STRONG"
    elif final_score <= -SIGNAL_THRESHOLD:
        action, strength = "BUY PUT", "MODERATE"
    else:
        action, strength = "WAIT", "NO_EDGE"

    try:
        high, low, close = df["High"], df["Low"], df["Close"]
        tr = pd.concat([
            high - low,
            (high - close.shift()).abs(),
            (low - close.shift()).abs(),
        ], axis=1).max(axis=1)
        atr = tr.rolling(14).mean().iloc[-1]
        if pd.isna(atr):
            atr = close.iloc[-1] * 0.005
    except Exception:
        atr = df["Close"].iloc[-1] * 0.005

    current = df["Close"].iloc[-1]

    if action == "BUY CALL":
        entry_spot  = current
        sl_spot     = current - (1.5 * atr)
        target_spot = current + (3.0 * atr)
    elif action == "BUY PUT":
        entry_spot  = current
        sl_spot     = current + (1.5 * atr)
        target_spot = current - (3.0 * atr)
    else:
        entry_spot = sl_spot = target_spot = None

    return {
        "action": action,
        "strength": strength,
        "score": round(final_score, 1),
        "candle_score": round(candle_score, 1),
        "chart_score": round(chart_score, 1),
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
        if r.status_code != 200:
            return []
        data = r.json()
        if data.get("status") != "success":
            return []
        return data.get("data", [])
    except Exception:
        return []


def _expiries_from_contracts(contracts):
    return sorted({c.get("expiry") for c in contracts if c.get("expiry")})


def _lot_size_for_expiry(contracts, expiry):
    for c in contracts:
        if c.get("expiry") == expiry and c.get("lot_size"):
            return int(c["lot_size"])
    return None


def _select_expiry(expiries, expiry_hint=None):
    if not expiries:
        return None

    today = _now_ist().date()

    if expiry_hint:
        hint = str(expiry_hint).lower().strip()
        # exact YYYY-MM-DD
        if re.match(r"\d{4}-\d{2}-\d{2}", hint):
            for e in expiries:
                if e == hint:
                    return e
        months = {"jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
                  "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12}
        m = re.search(r"(\d{1,2})\s*([a-z]{3})", hint)
        if m:
            day, mon = int(m.group(1)), months.get(m.group(2))
            if mon:
                for e in expiries:
                    d = datetime.strptime(e, "%Y-%m-%d").date()
                    if d.day == day and d.month == mon:
                        return e
        if "month" in hint:
            for e in expiries:
                d = datetime.strptime(e, "%Y-%m-%d").date()
                if d.month == today.month and d.year == today.year:
                    return e

    for e in expiries:
        d = datetime.strptime(e, "%Y-%m-%d").date()
        if (d - today).days >= 3:
            return e
    return expiries[0]


def _fetch_chain(instrument_key, expiry):
    url = (f"{BASE}/v2/option/chain"
           f"?instrument_key={_enc(instrument_key)}&expiry_date={expiry}")
    try:
        r = requests.get(url, headers=_headers(), timeout=20)
        if r.status_code != 200:
            return None
        data = r.json()
        if data.get("status") != "success":
            return None
        return data.get("data", [])
    except Exception:
        return None


def _find_atm_and_premium(chain, spot, opt_type):
    strikes = [i.get("strike_price") for i in chain if i.get("strike_price")]
    if not strikes:
        return None, None, None, None, None, None

    atm = min(strikes, key=lambda k: abs(k - spot))

    for item in chain:
        if item.get("strike_price") == atm:
            side = "call_options" if opt_type == "CE" else "put_options"
            md = item.get(side, {}).get("market_data", {})
            return (atm,
                    md.get("ltp"),
                    md.get("bid_price"),
                    md.get("ask_price"),
                    md.get("volume"),
                    md.get("oi"))
    return atm, None, None, None, None, None


# ═══════════════════════════════════════════════════════
#  COMBINED SCORE — 5m gives ONLY A BONUS
# ═══════════════════════════════════════════════════════
def _compare_directional_setups(decision, df_15m, daily_bias,
                                 score_5m=0.0, verdict_5m="NEUTRAL"):
    base = float(decision.get("score", 0))
    call_score = base
    put_score  = -base

    # 🎁 5m BONUS ONLY — no penalty
    if verdict_5m == "BULLISH":
        call_score += 8          # bonus for confirmation
    elif verdict_5m == "BEARISH":
        put_score  += 8

    rsi_val = _rsi(df_15m["Close"])
    pos     = _range_position(df_15m, 20)

    # Daily bias gives a small alignment bonus
    if daily_bias == "BEARISH":
        put_score  += 8
    elif daily_bias == "BULLISH":
        call_score += 8

    # Extreme RSI/range — only dampens the counter-side, never kills both
    if not pd.isna(rsi_val):
        if rsi_val > 72:
            call_score -= 10
        elif rsi_val < 28:
            put_score -= 10
    if pos is not None:
        if pos > 0.85:
            call_score -= 8
        elif pos < 0.15:
            put_score -= 8

    call_score = round(max(-100, min(100, call_score)), 1)
    put_score  = round(max(-100, min(100, put_score)), 1)

    if max(call_score, put_score) < SIGNAL_THRESHOLD:
        action, score, strength = "WAIT", max(call_score, put_score), "NO_EDGE"
    elif call_score >= put_score:
        action, score = "BUY CALL", call_score
        strength = "STRONG" if score >= STRONG_THRESHOLD else "MODERATE"
    else:
        action, score = "BUY PUT", put_score
        strength = "STRONG" if score >= STRONG_THRESHOLD else "MODERATE"

    decision["action"]   = action
    decision["score"]    = score
    decision["strength"] = strength

    current = float(df_15m["Close"].iloc[-1])
    atr = float(decision.get("atr") or current * 0.005)
    if action == "BUY CALL":
        decision["entry_spot"]  = round(current, 2)
        decision["sl_spot"]     = round(current - 1.5 * atr, 2)
        decision["target_spot"] = round(current + 3.0 * atr, 2)
    elif action == "BUY PUT":
        decision["entry_spot"]  = round(current, 2)
        decision["sl_spot"]     = round(current + 1.5 * atr, 2)
        decision["target_spot"] = round(current - 3.0 * atr, 2)
    else:
        decision["entry_spot"] = decision["sl_spot"] = decision["target_spot"] = None

    decision["call_setup_score"] = call_score
    decision["put_setup_score"]  = put_score
    decision["setup_comparison"] = (
        "Chart + Candlesticks primary; 5m & daily bias = bonus only"
    )
    return decision


# ═══════════════════════════════════════════════════════
#  CORE: analyze a single underlying + expiry
# ═══════════════════════════════════════════════════════
def analyze_pure_technical(symbol, expiry_hint=None, preloaded=None):
    symbol = symbol.upper().strip()
    if symbol not in OPTIONS_UNDERLYINGS:
        return {"error": f"Symbol '{symbol}' not supported"}

    info = OPTIONS_UNDERLYINGS[symbol]
    instrument_key = info["key"]

    if preloaded:
        df_15m = preloaded.get("df_15m")
        df_5m  = preloaded.get("df_5m")
        df_daily = preloaded.get("df_daily")
        contracts = preloaded.get("contracts", [])
        spot = preloaded.get("spot")
    else:
        df_15m   = _fetch_candles(instrument_key, "15",  days=10)
        df_5m    = _fetch_candles(instrument_key, "5",   days=3)
        df_daily = _fetch_candles(instrument_key, "day", days=120)
        contracts = []
        spot = None

    if df_15m is None or len(df_15m) < 30:
        return {"error": "Insufficient 15m data"}

    # 🚨 NEW FIX: Stale Data / Holiday Check
    if not ALLOW_OFFHOURS:
        last_candle_date = df_15m.index[-1].date()
        today = _now_ist().date()
        if last_candle_date != today:
            return {"error": f"Stale data: Last 15m candle is from {last_candle_date}. Market closed or holiday?"}

    if spot is None:
        live_spot = _get_live_price(instrument_key)
        spot = live_spot if live_spot else float(df_15m["Close"].iloc[-1])

    candle_bull, candle_bear, patterns = _analyze_candlesticks(df_15m)
    chart_score, observations = _analyze_chart(df_15m)
    score_5m, obs_5m, verdict_5m = _analyze_5min(df_5m)

    daily_bias = "NEUTRAL"
    if df_daily is not None and len(df_daily) >= 50:
        d_close  = df_daily["Close"]
        d_ema20  = d_close.ewm(span=20, adjust=False).mean().iloc[-1]
        d_ema50  = d_close.ewm(span=50, adjust=False).mean().iloc[-1]
        d_last   = d_close.iloc[-1]
        if d_last > d_ema20 > d_ema50:
            daily_bias = "BULLISH"
        elif d_last < d_ema20 < d_ema50:
            daily_bias = "BEARISH"

    decision = _make_decision(candle_bull, candle_bear, chart_score, df_15m)
    decision = _compare_directional_setups(
        decision, df_15m, daily_bias,
        score_5m=score_5m, verdict_5m=verdict_5m,
    )

    block_reason = None
    if not ALLOW_OFFHOURS and not _market_open():
        block_reason = "Market closed — data is stale"

    if block_reason:
        decision["action"]   = "WAIT"
        decision["strength"] = "BLOCKED"
        decision["block_reason"] = block_reason

    option_plan = None
    risk_warning = None
    expiry = None

    if decision["action"] in ("BUY CALL", "BUY PUT"):
        if not contracts:
            contracts = _fetch_contracts(instrument_key)
        expiries = _expiries_from_contracts(contracts)
        expiry   = _select_expiry(expiries, expiry_hint) if expiries else None
        base_lot = _lot_size_for_expiry(contracts, expiry) if expiry else None
        chain    = _fetch_chain(instrument_key, expiry) if expiry else None

        if not expiry or not chain or not base_lot:
            decision["action"]   = "WAIT"
            decision["strength"] = "DATA_BLOCK"
            decision["block_reason"] = "Option expiry / chain / lot size unavailable"
        else:
            opt_type = "CE" if decision["action"] == "BUY CALL" else "PE"
            atm_strike, atm_ltp, opt_bid, opt_ask, opt_vol, opt_oi = \
                _find_atm_and_premium(chain, spot, opt_type)

            if not atm_ltp or atm_ltp <= 0:
                decision["action"]   = "WAIT"
                decision["strength"] = "DATA_BLOCK"
                decision["block_reason"] = "ATM premium unavailable"
            else:
                liquidity_block = None
                if not opt_bid or not opt_ask or opt_bid <= 0 or opt_ask <= 0:
                    liquidity_block = "Invalid Bid/Ask data (fail-closed)"
                else:
                    spread_pct = (opt_ask - opt_bid) / atm_ltp
                    if spread_pct > MAX_SPREAD_PCT:
                        liquidity_block = f"High Spread ({spread_pct*100:.1f}%) — Illiquid"
                    elif opt_vol is not None and opt_vol < MIN_VOLUME:
                        liquidity_block = f"Low Volume ({opt_vol})"
                    elif opt_oi is not None and opt_oi < MIN_OI:
                        liquidity_block = f"Low OI ({opt_oi})"

                if liquidity_block:
                    decision["action"]   = "WAIT"
                    decision["strength"] = "LIQUIDITY_BLOCK"
                    decision["block_reason"] = liquidity_block
                else:
                    atr_ratio = decision["atr"] / spot if spot > 0 else 0.005
                    sl_pct = max(0.15, min(0.22, 0.18 + (atr_ratio * 2)))
                    sl_premium = round(atm_ltp * (1 - sl_pct), 2)

                    target_mult = 1.60 if decision["strength"] == "STRONG" else 1.45
                    target_premium = round(atm_ltp * target_mult, 2)

                    risk_per_unit = atm_ltp - sl_premium

                    max_risk_amount = TRADING_CAPITAL * MAX_RISK_PCT
                    if MAX_RISK_PER_TRADE > 0:
                        max_risk_amount = min(max_risk_amount, MAX_RISK_PER_TRADE)

                    one_lot_risk = risk_per_unit * base_lot if risk_per_unit > 0 else float("inf")
                    one_lot_cost = atm_ltp * base_lot

                    max_lots_by_risk = (
                        int(max_risk_amount // one_lot_risk)
                        if one_lot_risk > 0 and one_lot_risk != float("inf") else 0
                    )
                    max_lots_by_capital = (
                        int(TRADING_CAPITAL // one_lot_cost) if one_lot_cost > 0 else 0
                    )

                    if max_lots_by_risk < 1 or max_lots_by_capital < 1:
                        reasons = []
                        if max_lots_by_risk < 1:
                            risk_warning = (
                                f"1 lot risk ₹{one_lot_risk:.0f} exceeds limit ₹{max_risk_amount:.0f}"
                            )
                            reasons.append(f"1-lot risk ₹{one_lot_risk:.0f} > ₹{max_risk_amount:.0f}")
                        if max_lots_by_capital < 1:
                            reasons.append("Capital insufficient for one lot")
                        decision["action"]   = "WAIT"
                        decision["strength"] = "RISK_BLOCK" if max_lots_by_risk < 1 else "CAPITAL_BLOCK"
                        decision["block_reason"] = "; ".join(reasons)
                    else:
                        lots = min(max_lots_by_risk, max_lots_by_capital)
                        qty  = lots * base_lot
                        total_cost = round(atm_ltp * qty, 2)
                        profit     = round((target_premium - atm_ltp) * qty, 2)
                        max_loss   = round((atm_ltp - sl_premium) * qty, 2)
                        rr_ratio   = profit / max_loss if max_loss > 0 else 0

                        option_plan = {
                            "strike": atm_strike,
                            "type": opt_type,
                            "entry": atm_ltp,
                            "target": target_premium,
                            "sl": sl_premium,
                            "lot": qty,
                            "lots": lots,
                            "base_lot": base_lot,
                            "cost": total_cost,
                            "profit": profit,
                            "loss": max_loss,
                            "expiry": expiry,
                            "rr_ratio": round(rr_ratio, 2),
                            "risk_pct": round((max_loss / TRADING_CAPITAL) * 100, 2)
                            if TRADING_CAPITAL > 0 else 0,
                        }

    return {
        "symbol": symbol,
        "spot": round(spot, 2),
        "decision": decision,
        "candle_bull": candle_bull,
        "candle_bear": candle_bear,
        "patterns": patterns,
        "chart_score": chart_score,
        "observations": observations,
        "daily_bias": daily_bias,
        "option_plan": option_plan,
        "risk_warning": risk_warning,
        "last_candle": str(df_15m.index[-1]),
        "last_candle_5m": str(df_5m.index[-1]) if df_5m is not None and len(df_5m) else None,
        "score_5m": score_5m,
        "obs_5m": obs_5m,
        "verdict_5m": verdict_5m,
    }


# ═══════════════════════════════════════════════════════
#  SCAN ALL UNDERLYINGS × ALL EXPIRIES → RETURN BEST
# ═══════════════════════════════════════════════════════
def analyze_best_trade(expiry_hint=None):
    """
    Scan NIFTY, BANKNIFTY, SENSEX across nearest N expiries.
    Return the candidate with highest profit potential.
    """
    candidates = []
    errors = set()

    for symbol in OPTIONS_UNDERLYINGS:
        key = OPTIONS_UNDERLYINGS[symbol]["key"]

        df_15m   = _fetch_candles(key, "15",  days=10)
        df_5m    = _fetch_candles(key, "5",   days=3)
        df_daily = _fetch_candles(key, "day", days=120)
        contracts = _fetch_contracts(key)
        live_spot = _get_live_price(key)
        spot = live_spot if live_spot else (
            float(df_15m["Close"].iloc[-1]) if df_15m is not None and len(df_15m) else None
        )

        if df_15m is None or spot is None:
            errors.add(f"{symbol}: Insufficient data")
            continue

        # 🚨 Pre-check for stale data to avoid looping expiries
        if not ALLOW_OFFHOURS:
            last_date = df_15m.index[-1].date()
            today = _now_ist().date()
            if last_date != today:
                errors.add(f"{symbol}: Stale data (Last candle: {last_date})")
                continue

        expiries = _expiries_from_contracts(contracts)

        # pick expiries to scan
        if expiry_hint:
            picked = [_select_expiry(expiries, expiry_hint)] if expiries else []
        else:
            # nearest N future expiries
            today = _now_ist().date()
            future = [e for e in expiries
                      if datetime.strptime(e, "%Y-%m-%d").date() >= today]
            picked = future[:MAX_EXPIRIES_TO_SCAN]
            if not picked and expiries:
                picked = expiries[:MAX_EXPIRIES_TO_SCAN]

        preloaded = {
            "df_15m": df_15m, "df_5m": df_5m, "df_daily": df_daily,
            "contracts": contracts, "spot": spot,
        }

        for exp in picked:
            r = analyze_pure_technical(symbol, expiry_hint=exp, preloaded=preloaded)
            if r.get("error"):
                errors.add(f"{symbol} ({exp}): {r['error']}")
                continue
            if r["decision"]["action"] == "WAIT":
                continue
            op = r.get("option_plan")
            if not op:
                continue

            score  = abs(r["decision"]["score"])
            rr     = op.get("rr_ratio", 0)
            profit = op.get("profit", 0)
            mult   = op.get("target", 0) / op.get("entry", 1) if op.get("entry") else 1

            # Composite profit-potential score:
            #   strong signal × good R:R × absolute profit × premium expansion
            potential = score * rr * (profit / 100) * mult
            candidates.append({
                "potential": round(potential, 2),
                "result": r,
            })

    if not candidates:
        if errors:
            print("⚠️  No trades found. Reasons:")
            for e in errors:
                print(f"   - {e}")
            print()
        return None

    candidates.sort(key=lambda x: x["potential"], reverse=True)
    best = candidates[0]["result"]
    best["_leaderboard"] = []
    for c in candidates[:5]:
        r = c["result"]
        d = r["decision"]
        op = r.get("option_plan") or {}
        best["_leaderboard"].append({
            "rank":         len(best["_leaderboard"]) + 1,
            "symbol":       r["symbol"],
            "action":       d["action"],
            "strength":     d["strength"],
            "score":        d["score"],
            "potential":    c["potential"],
            # full option plan
            "expiry":       op.get("expiry", "-"),
            "strike":       op.get("strike", "-"),
            "type":         op.get("type", "-"),
            "entry":        op.get("entry", "-"),
            "target":       op.get("target", "-"),
            "sl":           op.get("sl", "-"),
            "lots":         op.get("lots", "-"),
            "base_lot":     op.get("base_lot", "-"),
            "qty":          op.get("lot", "-"),
            "cost":         op.get("cost", "-"),
            "profit":       op.get("profit", 0),
            "loss":         op.get("loss", 0),
            "rr":           op.get("rr_ratio", 0),
            # spot levels
            "spot_entry":   d.get("entry_spot"),
            "spot_target":  d.get("target_spot"),
            "spot_sl":      d.get("sl_spot"),
        })
    return best


# ═══════════════════════════════════════════════════════
#  FORMATTER
# ═══════════════════════════════════════════════════════
def _wrap_text(text, width=59):
    words = text.split()
    lines = []
    current = ""
    for w in words:
        if len(current) + len(w) + 1 <= width:
            current += (w + " ") if current else w
        else:
            lines.append(current)
            current = w
    if current:
        lines.append(current)
    return lines


def format_result(r):
    if not r or r.get("error"):
        return f"❌ {r.get('error', 'Unknown error') if r else 'Unknown error'}"

    lines = []
    lines.append("\n" + "═" * 65)
    lines.append(f"  📊 PURE TECHNICAL OPTIONS ADVISOR — {r['symbol']}")
    lines.append("═" * 65)
    lines.append(f"\n  Spot: ₹{r['spot']}")
    lines.append(f"  Last closed 15m candle: {r.get('last_candle')}")
    if r.get("last_candle_5m"):
        lines.append(f"  Last closed 5m candle:  {r.get('last_candle_5m')}")
    lines.append(f"  Daily Bias: {r['daily_bias']}")

    lines.append(f"\n  🎨 CANDLESTICK PATTERNS")
    lines.append(f"     Bull Score: {r['candle_bull']}  |  Bear Score: {r['candle_bear']}")
    for p in r["patterns"]:
        lines.append(f"     {p}")

    lines.append(f"\n  📈 CHART ANALYSIS (Score: {r['chart_score']:+d})")
    for o in r["observations"]:
        lines.append(f"     {o}")

    if r.get("score_5m") is not None:
        lines.append(f"\n  ⚡ 5 MIN BONUS (Score: {r['score_5m']:+.1f})")
        lines.append(f"     Verdict: {r.get('verdict_5m', 'UNKNOWN')} (bonus only — never penalizes)")

    d = r["decision"]
    lines.append("\n" + "─" * 65)
    lines.append(f"  🧮 TECHNICAL SCORE")
    lines.append(f"     Candle Score:  {d['candle_score']:+.1f}")
    lines.append(f"     Chart Score:   {d['chart_score']:+.1f}")
    lines.append(f"     ─────────────────────")
    lines.append(f"     Selected Setup Score: {d['score']:+.1f}  ->  {d['action']} ({d['strength']})")
    if "call_setup_score" in d and "put_setup_score" in d:
        lines.append(f"     Bullish BUY CALL setup: {d['call_setup_score']:+.1f}")
        lines.append(f"     Bearish BUY PUT setup:  {d['put_setup_score']:+.1f}")

    if d.get("block_reason"):
        lines.append(f"\n  🚫 BLOCKED: {d['block_reason']}")
    if r.get("risk_warning"):
        lines.append(f"  ⚠️  {r['risk_warning']}")

    op = r.get("option_plan")
    if op and d["action"] != "WAIT":
        lines.append("\n" + "─" * 65)
        lines.append(f"  📋 OPTION PLAN")
        lines.append(f"     Expiry:     {op['expiry']}")
        lines.append(f"     Trade:      BUY {op['strike']:.0f} {op['type']}")
        lines.append(f"     Entry:      ₹{op['entry']}")
        target_pct = round((op["target"] / op["entry"] - 1) * 100, 1)
        sl_pct_disp = round((op["sl"] / op["entry"] - 1) * 100, 1)
        lines.append(f"     Target:     ₹{op['target']}    ({target_pct:+.1f}%)")
        lines.append(f"     Stop Loss:  ₹{op['sl']}    ({sl_pct_disp:+.1f}%)")
        lines.append(f"     ")
        lines.append(f"     Lot Size:   {op['lots']} lot × {op['base_lot']} = {op['lot']} qty")
        lines.append(f"     Cost:       ₹{op['cost']}")
        lines.append(f"     Profit:     ₹+{op['profit']}")
        lines.append(f"     Loss:       ₹-{op['loss']}")

        rr = op.get("rr_ratio", 0)
        lines.append("")
        if rr >= 2.0:
            lines.append(f"     ✅ R:R = 1:{rr:.2f}  (Excellent)")
        elif rr >= 1.5:
            lines.append(f"     ✅ R:R = 1:{rr:.2f}  (Good)")
        elif rr >= 1.0:
            lines.append(f"     🟡 R:R = 1:{rr:.2f}")
        else:
            lines.append(f"     🔴 R:R = 1:{rr:.2f}  (Poor)")

        lines.append(f"     📊 Risk: {op['risk_pct']}% of ₹{int(TRADING_CAPITAL)}")
        lines.append(f"     ⚠️  Costs/slippage NOT included")

    if d.get("entry_spot") and d["action"] != "WAIT":
        lines.append("\n" + "─" * 65)
        lines.append(f"  📍 SPOT LEVELS")
        lines.append(f"     Spot Entry:  ₹{d['entry_spot']}")
        lines.append(f"     Spot Target: ₹{d['target_spot']}")
        lines.append(f"     Spot SL:     ₹{d['sl_spot']}")

    lines.append("")
    lines.append("╔" + "═" * 63 + "╗")
    lines.append("║" + " " * 22 + "🎯 FINAL VERDICT" + " " * 25 + "║")
    lines.append("╠" + "═" * 63 + "╣")

    if d["action"] == "WAIT":
        lines.append("║" + " " * 63 + "║")
        if d.get("block_reason"):
            lines.append("║  🚫 TRADE BLOCKED" + " " * 45 + "║")
            for wl in _wrap_text(d["block_reason"], width=58):
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
    lines.append("")

    # Leaderboard (only when running the scan)
    if r.get("_leaderboard"):
        lines.append("═" * 65)
        lines.append("  🏆 TOP CANDIDATES SCANNED")
        lines.append("═" * 65)
        for i, c in enumerate(r["_leaderboard"], 1):
            lines.append(
                f"  {i}. {c['symbol']:<10} {c['action']:<10} "
                f"{c['strike']:<8} {c['expiry']}  "
                f"score={c['score']:+.1f}  profit=₹+{c['profit']}  "
                f"potential={c['potential']}"
            )
        lines.append("")

    return "\n".join(lines)


# ═══════════════════════════════════════════════════════
#  ENTRY POINT
# ═══════════════════════════════════════════════════════
if __name__ == "__main__":
    import sys
    # Usage:
    #   python -m core.pure_technical_options          -> scan ALL, show best
    #   python -m core.pure_technical_options NIFTY    -> single underlying
    #   python -m core.pure_technical_options NIFTY 2026-10-23  -> specific expiry
    if len(sys.argv) == 1:
        print("\n🔎 Scanning NIFTY + BANKNIFTY + SENSEX across all expiries...\n")
        best = analyze_best_trade()
        if best is None:
            print("❌ No valid trade found across any underlying/expiry.")
        else:
            print(format_result(best))
    else:
        symbol = sys.argv[1]
        expiry = sys.argv[2] if len(sys.argv) > 2 else None
        result = analyze_pure_technical(symbol, expiry_hint=expiry)
        print(format_result(result))