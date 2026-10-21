"""
core/options_analyst.py
Options Analysis Engine — Complete Integration

✅ Live spot + option premiums
✅ Expiry selection
✅ PCR contrarian
✅ Capital-aware trade
✅ SMC + Patterns + Consensus
✅ Regime detection
✅ Trader's view (narrative + scenarios)
✅ Final verdict (specific strike/entry/target/SL)
"""
import os
import json
import math
import re
import requests
import pandas as pd
from datetime import datetime, timedelta
from dotenv import load_dotenv

from core.options_strategies import bear_put_spread, bull_call_spread, iron_condor
from core.consensus_score import compute_consensus, format_consensus
from core.regime_detector import detect_regime, format_regime
from core.trader_brain import build_trader_view, format_trader_view

load_dotenv()


# ============================================================
#  PATTERN DETECTOR IMPORT
# ============================================================
try:
    from core.pattern_detector import get_pattern_summary
    _PATTERN_OK = True
except Exception:
    _PATTERN_OK = False

    def get_pattern_summary(_):
        return {"patterns": [], "overall_bias": "NEUTRAL", "bull_score": 0, "bear_score": 0}


# ============================================================
#  SMC IMPORT
# ============================================================
try:
    from core.smc_signal import run_smc_analysis
    _SMC_OK = True
except Exception:
    _SMC_OK = False

    def run_smc_analysis(*a, **k):
        return {"status": "error", "message": "SMC not available"}


# ============================================================
#  CAPITAL CONFIG
# ============================================================
TRADING_CAPITAL = float(os.getenv("TRADING_CAPITAL", "10000"))
MAX_RISK_PER_TRADE = float(os.getenv("MAX_RISK_PER_TRADE", "1000"))


OPTIONS_UNDERLYINGS = {
    "NIFTY":       {"key": "NSE_INDEX|Nifty 50",       "lot": 25,  "step": 50},
    "BANKNIFTY":   {"key": "NSE_INDEX|Nifty Bank",     "lot": 15,  "step": 100},
    "FINNIFTY":    {"key": "NSE_INDEX|Nifty Fin Service", "lot": 40, "step": 50},
    "MIDCPNIFTY":  {"key": "NSE_INDEX|NIFTY MID SELECT","lot": 75, "step": 25},
    "RELIANCE":    {"key": "NSE_EQ|INE002A01018",      "lot": 250, "step": 20},
    "TCS":         {"key": "NSE_EQ|INE467B01029",      "lot": 175, "step": 50},
    "HDFCBANK":    {"key": "NSE_EQ|INE040A01034",      "lot": 550, "step": 20},
    "INFY":        {"key": "NSE_EQ|INE009A01021",      "lot": 400, "step": 20},
    "ICICIBANK":   {"key": "NSE_EQ|INE090A01021",      "lot": 700, "step": 10},
    "SBIN":        {"key": "NSE_EQ|INE062A01020",      "lot": 1500,"step": 10},
    "TATAMOTORS":  {"key": "NSE_EQ|INE155A01022",      "lot": 550, "step": 5},
    "ADANIENT":    {"key": "NSE_EQ|INE423A01024",      "lot": 300, "step": 50},
}


_INDEX_YF_MAP = {
    "NIFTY":      "^NSEI",
    "BANKNIFTY":  "^NSEBANK",
    "FINNIFTY":   "^CNXFIN",
    "MIDCPNIFTY": "^NSEMDCP50",
}


def _get_yf_ticker(symbol: str) -> str:
    s = (symbol or "").upper().strip()
    if s in _INDEX_YF_MAP:
        return _INDEX_YF_MAP[s]
    return f"{s}.NS"


# ============================================================
#  LIVE PRICE HELPERS
# ============================================================
def get_live_price(instrument_key):
    access_token = os.getenv("UPSTOX_ANALYTICS_TOKEN")
    url = f"https://api.upstox.com/v3/market-quote/quotes?instrument_key={instrument_key}"
    headers = {"Accept": "application/json", "Authorization": f"Bearer {access_token}"}
    try:
        r = requests.get(url, headers=headers, timeout=10)
        if r.status_code == 200:
            data = r.json()
            if data.get("status") == "success":
                quote = data["data"].get(instrument_key)
                if quote:
                    return quote.get("ohlc", {}).get("close", 0) or quote.get("last_price", 0)
    except Exception:
        pass
    return None


def get_live_spot(instrument_key):
    return get_live_price(instrument_key)


def get_historical_candles(instrument_key, interval="15", days=15):
    access_token = os.getenv("UPSTOX_ANALYTICS_TOKEN")
    today = pd.Timestamp.now().strftime('%Y-%m-%d')
    from_date = (pd.Timestamp.now() - pd.Timedelta(days=days)).strftime('%Y-%m-%d')
    url = (f"https://api.upstox.com/v3/historical-candle/"
           f"{instrument_key}/minutes/{interval}/{today}/{from_date}")
    headers = {"Accept": "application/json", "Authorization": f"Bearer {access_token}"}
    try:
        response = requests.get(url, headers=headers, timeout=15)
        response.raise_for_status()
        data = response.json()
        candles = data.get("data", {}).get("candles", [])
        if not candles:
            return None
        df = pd.DataFrame(candles,
                          columns=['timestamp', 'Open', 'High', 'Low', 'Close', 'Volume', 'OI'])
        df['timestamp'] = pd.to_datetime(df['timestamp'])
        df.set_index('timestamp', inplace=True)
        df.sort_index(inplace=True)
        return df
    except Exception:
        return None


# ============================================================
#  DUAL TREND DETECTION
# ============================================================
def get_nifty_trend_dual():
    result = {"intraday": "UNKNOWN", "daily": "UNKNOWN", "combined": "UNKNOWN"}

    try:
        df = get_historical_candles("NSE_INDEX|Nifty 50", "15", days=10)
        if df is not None and len(df) >= 21:
            df['EMA_21'] = df['Close'].ewm(span=21, adjust=False).mean()
            result["intraday"] = "BULLISH" if df.iloc[-1]['Close'] > df.iloc[-1]['EMA_21'] else "BEARISH"
    except Exception as e:
        print(f"   ⚠️ Intraday trend fetch failed: {e}")

    try:
        df_daily = _get_upstox_daily_candles("NSE_INDEX|Nifty 50", days=90)
        if df_daily is not None and len(df_daily) >= 50:
            df_daily['EMA_20'] = df_daily['Close'].ewm(span=20, adjust=False).mean()
            df_daily['EMA_50'] = df_daily['Close'].ewm(span=50, adjust=False).mean()
            last = df_daily.iloc[-1]
            if last['Close'] > last['EMA_20'] > last['EMA_50']:
                result["daily"] = "BULLISH"
            elif last['Close'] < last['EMA_20'] < last['EMA_50']:
                result["daily"] = "BEARISH"
            else:
                result["daily"] = "NEUTRAL"
    except Exception as e:
        print(f"   ⚠️ Daily trend fetch failed: {e}")
        result["daily"] = result["intraday"]

    if result["intraday"] == result["daily"] and result["intraday"] != "UNKNOWN":
        result["combined"] = result["intraday"]
    elif result["daily"] != "UNKNOWN" and result["daily"] != "NEUTRAL":
        result["combined"] = result["daily"]
    else:
        result["combined"] = result["intraday"]

    return result


def _get_upstox_daily_candles(instrument_key: str, days: int = 90):
    access_token = os.getenv("UPSTOX_ANALYTICS_TOKEN")
    today = pd.Timestamp.now().strftime('%Y-%m-%d')
    from_date = (pd.Timestamp.now() - pd.Timedelta(days=days)).strftime('%Y-%m-%d')
    url = (f"https://api.upstox.com/v3/historical-candle/"
           f"{instrument_key}/days/1/{today}/{from_date}")
    headers = {"Accept": "application/json", "Authorization": f"Bearer {access_token}"}
    try:
        response = requests.get(url, headers=headers, timeout=15)
        response.raise_for_status()
        data = response.json()
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
        print(f"   ⚠️ Upstox daily fetch error: {e}")
        return None


# ============================================================
#  EXPIRY
# ============================================================
def fetch_expiry_dates(instrument_key):
    access_token = os.getenv("UPSTOX_ANALYTICS_TOKEN")
    url = f"https://api.upstox.com/v2/option/contract?instrument_key={instrument_key}"
    headers = {"Accept": "application/json", "Authorization": f"Bearer {access_token}"}
    try:
        r = requests.get(url, headers=headers, timeout=15)
        if r.status_code != 200:
            return []
        data = r.json()
        if data.get("status") != "success":
            return []
        expiries = list(set(item.get("expiry") for item in data.get("data", [])))
        expiries.sort()
        return expiries
    except Exception:
        return []


def _select_expiry(expiries, expiry_hint=None):
    if not expiries:
        return None

    today = datetime.now().date()

    if expiry_hint:
        hint = str(expiry_hint).lower().strip()

        if "month" in hint:
            month_expiries = []
            for e in expiries:
                try:
                    d = datetime.strptime(e, "%Y-%m-%d").date()
                    if d.month == today.month and d.year == today.year:
                        month_expiries.append(e)
                except Exception:
                    continue
            if month_expiries:
                return month_expiries[-1]

        if "week" in hint or "wk" in hint:
            return expiries[0]

        match = re.search(r'(\d{1,2})\s*([a-z]{3})', hint)
        if not match:
            match = re.search(r'([a-z]{3})\s*(\d{1,2})', hint)
            if match:
                month_str, day_str = match.group(1), match.group(2)
            else:
                month_str, day_str = None, None
        else:
            day_str, month_str = match.group(1), match.group(2)

        if month_str and day_str:
            months = {"jan":1,"feb":2,"mar":3,"apr":4,"may":5,"jun":6,
                      "jul":7,"aug":8,"sep":9,"oct":10,"nov":11,"dec":12}
            month = months.get(month_str)
            day = int(day_str)
            if month:
                for e in expiries:
                    try:
                        d = datetime.strptime(e, "%Y-%m-%d").date()
                        if d.day == day and d.month == month:
                            return e
                    except Exception:
                        continue

    for e in expiries:
        try:
            d = datetime.strptime(e, "%Y-%m-%d").date()
            if (d - today).days >= 3:
                return e
        except Exception:
            continue

    return expiries[0]


def fetch_option_chain(instrument_key, expiry_date):
    access_token = os.getenv("UPSTOX_ANALYTICS_TOKEN")
    url = (f"https://api.upstox.com/v2/option/chain"
           f"?instrument_key={instrument_key}&expiry_date={expiry_date}")
    headers = {"Accept": "application/json", "Authorization": f"Bearer {access_token}"}
    try:
        r = requests.get(url, headers=headers, timeout=20)
        if r.status_code != 200:
            return None
        data = r.json()
        if data.get("status") != "success":
            return None
        return data.get("data", [])
    except Exception:
        return None


# ============================================================
#  CHAIN ANALYSIS
# ============================================================
def analyze_chain(chain_data, spot_price):
    if not chain_data:
        return None

    total_call_oi = 0
    total_put_oi = 0
    oi_by_strike = {}

    for item in chain_data:
        strike = item.get("strike_price", 0)
        ce = item.get("call_options", {})
        pe = item.get("put_options", {})
        ce_oi = ce.get("market_data", {}).get("oi", 0) or 0
        pe_oi = pe.get("market_data", {}).get("oi", 0) or 0
        total_call_oi += ce_oi
        total_put_oi += pe_oi
        oi_by_strike[strike] = {"ce": ce_oi, "pe": pe_oi}

    if not oi_by_strike:
        return None

    pcr = total_put_oi / total_call_oi if total_call_oi > 0 else 0

    max_pain_strike = None
    min_loss = float("inf")
    strikes = sorted(oi_by_strike.keys())
    for test_strike in strikes:
        writer_loss = 0
        for K, oi in oi_by_strike.items():
            if test_strike > K:
                writer_loss += oi["ce"] * (test_strike - K)
            if test_strike < K:
                writer_loss += oi["pe"] * (K - test_strike)
        if writer_loss < min_loss:
            min_loss = writer_loss
            max_pain_strike = test_strike

    support_strike = max(oi_by_strike.keys(), key=lambda k: oi_by_strike[k]["pe"])
    resistance_strike = max(oi_by_strike.keys(), key=lambda k: oi_by_strike[k]["ce"])
    atm_strike = min(oi_by_strike.keys(), key=lambda k: abs(k - spot_price))

    return {
        "pcr": round(pcr, 3),
        "max_pain": max_pain_strike,
        "support": support_strike,
        "resistance": resistance_strike,
        "atm": atm_strike,
        "total_call_oi": total_call_oi,
        "total_put_oi": total_put_oi,
    }


def _find_premium(chain, strike, opt_type):
    for item in chain:
        if item.get("strike_price") == strike:
            if opt_type == "CE":
                return item.get("call_options", {}).get("market_data", {}).get("ltp")
            else:
                return item.get("put_options", {}).get("market_data", {}).get("ltp")
    return None


# ============================================================
#  EXPIRY DAY CHECK
# ============================================================
def check_expiry_day(nearest_expiry):
    try:
        expiry_date = datetime.strptime(nearest_expiry, "%Y-%m-%d").date()
        today = datetime.now().date()
        days_to_expiry = (expiry_date - today).days

        if days_to_expiry <= 0:
            return True, "🚨 TODAY IS EXPIRY — No new positions. BLOCKED."
        elif days_to_expiry == 1:
            return False, "⚠️ 1 day to expiry — Theta HIGH. Tight SL, small size."
        elif days_to_expiry == 2:
            return False, "⚠️ 2 days to expiry — Theta accelerating. Tight SL."
        elif days_to_expiry <= 4:
            return False, f"⚠️ {days_to_expiry} days to expiry — Monitor theta."
        else:
            return False, f"✅ {days_to_expiry} days to expiry — Safe entry."
    except Exception:
        return False, ""


def _days_to_expiry(nearest_expiry):
    try:
        exp = datetime.strptime(nearest_expiry, "%Y-%m-%d").date()
        return (exp - datetime.now().date()).days
    except Exception:
        return 999


# ============================================================
#  PATTERN HELPERS
# ============================================================
def _get_pattern_data(symbol):
    pat_bias = "NEUTRAL"
    pat_bull = 0
    pat_bear = 0
    pattern_str = "None detected"

    if not _PATTERN_OK:
        return pat_bias, pat_bull, pat_bear, pattern_str

    try:
        yf_ticker = _get_yf_ticker(symbol)
        pat = get_pattern_summary(yf_ticker)
        pat_list = (pat.get("patterns") or [])[:5]
        pat_bias = pat.get("overall_bias", "NEUTRAL")
        pat_bull = pat.get("bull_score", 0)
        pat_bear = pat.get("bear_score", 0)
        if pat_list:
            pattern_str = ", ".join(
                f"{p.get('pattern', '?')}({p.get('direction', '?')[:1]}"
                f"{p.get('strength', 0):.1f})"
                for p in pat_list
            )
    except Exception:
        pass

    return pat_bias, pat_bull, pat_bear, pattern_str


def _iv_status(avg_iv):
    if avg_iv <= 0:
        return "UNKNOWN", ""
    if avg_iv > 30:
        return (f"HIGH ({avg_iv:.1f})", "IV crush risk — premium inflated. Avoid buying.")
    if avg_iv > 25:
        return (f"ELEVATED ({avg_iv:.1f})", "IV elevated — premium costly. Reduce size.")
    if avg_iv < 12:
        return (f"LOW ({avg_iv:.1f})", "IV low — options cheap, but market expects small move.")
    return (f"NORMAL ({avg_iv:.1f})", "")


# ============================================================
#  AI RECOMMENDATION
# ============================================================
def get_ai_options_recommendation(symbol, spot, analysis, atm_data, nearest_expiry):
    from core.ai_client import ask_groq

    trends = get_nifty_trend_dual()
    nifty_intraday = trends["intraday"]
    nifty_daily = trends["daily"]
    nifty_combined = trends["combined"]

    smc_bias = "UNKNOWN"
    smc_score = 0
    smc_signal = "UNKNOWN"
    smc_bull_score = 0
    smc_bear_score = 0
    if _SMC_OK:
        try:
            smc = run_smc_analysis(_get_yf_ticker(symbol))
            if smc.get("status") == "success":
                smc_bias = smc.get("bias", "UNKNOWN")
                smc_bull_score = smc.get("bull_score", 0)
                smc_bear_score = smc.get("bear_score", 0)
                smc_score = smc_bull_score - smc_bear_score
                smc_signal = smc.get("signal", "UNKNOWN")
        except Exception:
            pass

    fii_bias = "NEUTRAL"
    try:
        from core.fii_dii import get_fii_dii_data, get_fii_dii_bias
        fii_data = get_fii_dii_data()
        fii_bias = get_fii_dii_bias(fii_data)
    except Exception:
        pass

    global_bias = "NEUTRAL"
    try:
        from core.global_cues import fetch_global_cues, get_market_bias
        gdata = fetch_global_cues()
        global_bias, _ = get_market_bias(gdata)
    except Exception:
        pass

    news_sentiment = "NEUTRAL"
    news_score = 0
    news_summary = "No news"
    news_impact = "MEDIUM"
    try:
        from core.news_analyst import analyze_news
        news_data = analyze_news(symbol)
        news_sentiment = news_data.get("sentiment", {}).get("sentiment", "NEUTRAL")
        news_score = news_data.get("sentiment", {}).get("score", 0)
        news_summary = news_data.get("sentiment", {}).get("summary", "")
        news_impact = news_data.get("sentiment", {}).get("intraday_impact", "MEDIUM")
    except Exception:
        pass

    expiry_risky, expiry_msg = check_expiry_day(nearest_expiry)
    days_to_exp = _days_to_expiry(nearest_expiry)

    pcr = analysis.get('pcr', 0)
    if pcr < 0.7:
        pcr_bias = "Overbought → CONTRARIAN BEARISH"
    elif pcr > 1.2:
        pcr_bias = "Oversold → CONTRARIAN BULLISH"
    elif pcr > 1.0:
        pcr_bias = "Mildly oversold → Mildly Bullish"
    else:
        pcr_bias = "Neutral"

    ce_iv = atm_data.get('ce_iv') or 0
    pe_iv = atm_data.get('pe_iv') or 0
    avg_iv = (ce_iv + pe_iv) / 2 if (ce_iv and pe_iv) else 0
    iv_status_str, iv_warning = _iv_status(avg_iv)

    pat_bias, pat_bull, pat_bear, pattern_str = _get_pattern_data(symbol)
    pat_diff = abs(pat_bull - pat_bear)

    consensus = compute_consensus(
        smc_bull=smc_bull_score,
        smc_bear=smc_bear_score,
        pat_bull=pat_bull,
        pat_bear=pat_bear,
        pcr=pcr,
        fii_bias=fii_bias,
        global_bias=global_bias,
        news_sentiment=news_sentiment,
        news_score=news_score,
    )

    consensus_warning = ""
    if not consensus["threshold_met"]:
        consensus_warning = (
            f"\n⚠️ NOTE: Consensus score {consensus['final_score']:+.1f} is below typical "
            f"threshold (|score| ≥ 50). This is a WEAK setup. "
            f"If you recommend a trade, use SMALLER size and tighter SL."
        )

    atm = analysis.get('atm', 0)
    ce_ltp = atm_data.get('ce_ltp', 'N/A')
    pe_ltp = atm_data.get('pe_ltp', 'N/A')

    prompt = f"""You are a Senior Options Trader at an Indian prop desk.

Analyze this options setup for {symbol}:

SPOT PRICE: Rs. {spot:.2f}

=== WEIGHTED CONSENSUS SCORE ===
- Final Score: {consensus['final_score']:+.1f} / 100
- Classification: {consensus['classification']}
- Suggested Direction: {consensus['direction']}
- Signal Strength: {consensus['status']}
{consensus_warning}

=== NIFTY TREND ===
- Intraday (15m): {nifty_intraday}
- Daily:          {nifty_daily}
- Combined:       {nifty_combined}

=== SMART MONEY (SMC) ===
- Signal: {smc_signal}
- Bias:   {smc_bias}
- Score:  {smc_score}

=== EXPIRY ===
- Expiry: {nearest_expiry}
- Days to expiry: {days_to_exp}
- Status: {expiry_msg}

=== NEWS ===
- Sentiment: {news_sentiment} (score {news_score}, impact: {news_impact})
- Summary: {news_summary}

=== CANDLESTICK PATTERNS ===
- Patterns: {pattern_str}
- Bias: {pat_bias} (Bull {pat_bull} / Bear {pat_bear})

=== OPTION CHAIN ===
- PCR: {pcr} → {pcr_bias}
- Max Pain: {analysis['max_pain']}
- Support (PE OI): {analysis['support']}
- Resistance (CE OI): {analysis['resistance']}
- ATM Strike: {atm}

=== ATM PREMIUMS ===
- ATM CE LTP: Rs. {ce_ltp} | IV: {ce_iv}
- ATM PE LTP: Rs. {pe_ltp} | IV: {pe_iv}

=== DECISION LOGIC ===
1. CALL requires NIFTY combined BULLISH/NEUTRAL
2. PUT requires NIFTY combined BEARISH/NEUTRAL
3. Use SMC as tie-breaker when trend & patterns conflict
4. Use consensus score as primary guide

CONFIDENCE:
- 8-10: All aligned (score > 40)
- 6-7: Trend + SMC aligned
- 4-5: Partial alignment (weak signal)
- 1-3: Conflict (prefer NO TRADE)

*** CRITICAL — ALWAYS FILL SPECIFIC VALUES ***
Even if you recommend NO TRADE, you MUST provide:
- strike: specific strike (use ATM if unsure)
- entry: specific entry premium
- target: specific target premium
- stop_loss: specific SL premium

NEVER return 0 or null for these fields.

If NO TRADE, give plan values (what you'd buy IF triggered).
If WEAK signal (score 20-40), give smaller-size plan.
If STRONG signal (score > 40), give full-size plan.

Return ONLY valid JSON:
{{
  "action": "BUY CALL" | "BUY PUT" | "NO TRADE",
  "strike": number,
  "entry": number,
  "target": number,
  "stop_loss": number,
  "confidence": number,
  "reason": "one-line reason",
  "risks": "one-line risks",
  "wait_for": "trigger level if NO TRADE, else empty"
}}"""

    raw = ask_groq(prompt, max_tokens=4000)
    if not raw:
        return None

    text = raw.strip()
    if text.startswith("```"):
        parts = text.split("```")
        if len(parts) > 1:
            text = parts[1]
            if text.startswith("json"):
                text = text[4:]
    text = text.strip()
    s, e = text.find("{"), text.rfind("}")
    if s == -1 or e == -1:
        return None
    try:
        rec = json.loads(text[s:e+1])
    except Exception:
        return None

    # Post-filters (only for expiry — no more consensus block)
    action = rec.get("action", "NO TRADE")

    if expiry_risky and action != "NO TRADE":
        rec["action"] = "NO TRADE"
        rec["reason"] = f"Auto-filtered: {expiry_msg}"
        rec["consensus"] = consensus
        return rec

    rec["consensus"] = consensus
    return rec


# ============================================================
#  CAPITAL-AWARE TRADE
# ============================================================
def get_capital_aware_trade(symbol, chain, spot_price, atm_strike, ai_rec, info, nearest_expiry):
    if not ai_rec:
        return None

    action = ai_rec.get("action", "NO TRADE")
    lot_size = info.get("lot", 25)
    step = info.get("step", 50)

    if action == "NO TRADE":
        return {
            "strategy": "NO TRADE",
            "outlook": "NO TRADE",
            "legs": [],
            "cost": 0,
            "note": ai_rec.get("reason", "AI recommends no trade"),
            "affordable": True,
            "is_ai_trade": False,
        }

    ai_strike = ai_rec.get("strike")
    option_type = "CE" if action == "BUY CALL" else "PE"

    target_strike = atm_strike
    if ai_strike and isinstance(ai_strike, (int, float)) and ai_strike > 0:
        target_strike = ai_strike

    actual_price = _find_premium(chain, target_strike, option_type)
    if not actual_price or actual_price <= 0:
        actual_price = _find_premium(chain, atm_strike, option_type)
        target_strike = atm_strike

    if not actual_price:
        return {
            "strategy": "NO TRADE",
            "outlook": "Data issue",
            "legs": [],
            "cost": 0,
            "note": "Premium data not available",
            "affordable": False,
            "is_ai_trade": False,
        }

    total_cost = actual_price * lot_size
    capital_used_pct = (total_cost / TRADING_CAPITAL) * 100

    entry_premium = ai_rec.get("entry") or actual_price
    target_premium = ai_rec.get("target") or round(entry_premium * 1.7, 2)
    sl_premium = ai_rec.get("stop_loss") or round(entry_premium * 0.7, 2)

    profit_at_target = (target_premium - entry_premium) * lot_size
    loss_at_sl = (entry_premium - sl_premium) * lot_size

    actual_risk = abs(loss_at_sl)
    risk_used_pct = (actual_risk / MAX_RISK_PER_TRADE) * 100 if MAX_RISK_PER_TRADE > 0 else 0

    capital_enough = total_cost <= TRADING_CAPITAL

    if not capital_enough:
        affordable = False
        status = f"❌ INSUFFICIENT CAPITAL (need Rs. {total_cost:.0f}, have Rs. {TRADING_CAPITAL:.0f})"
        status_emoji = "red"
    elif actual_risk <= MAX_RISK_PER_TRADE:
        affordable = True
        status = f"✅ WITHIN risk limit (SL-based risk: Rs. {actual_risk:.0f} / Rs. {MAX_RISK_PER_TRADE:.0f})"
        status_emoji = "green"
    elif actual_risk <= MAX_RISK_PER_TRADE * 1.5:
        affordable = True
        status = f"⚠️ SLIGHTLY EXCEEDS risk limit (SL risk: Rs. {actual_risk:.0f} vs limit Rs. {MAX_RISK_PER_TRADE:.0f})"
        status_emoji = "yellow"
    else:
        affordable = True
        status = f"⚠️ EXCEEDS risk limit {risk_used_pct:.0f}% (SL risk: Rs. {actual_risk:.0f} vs limit Rs. {MAX_RISK_PER_TRADE:.0f})"
        status_emoji = "yellow"

    return {
        "strategy": f"Naked BUY {option_type}",
        "outlook": action,
        "legs": [{
            "action": "BUY",
            "strike": target_strike,
            "type": option_type,
            "price": round(actual_price, 2),
        }],
        "lot_size": lot_size,
        "entry_premium": round(entry_premium, 2),
        "target_premium": round(target_premium, 2),
        "sl_premium": round(sl_premium, 2),
        "cost": round(total_cost, 2),
        "profit_at_target": round(profit_at_target, 2),
        "loss_at_sl": round(loss_at_sl, 2),
        "capital_used_pct": round(capital_used_pct, 2),
        "affordable": affordable,
        "status": status,
        "status_emoji": status_emoji,
        "is_ai_trade": True,
    }


# ============================================================
#  FINAL VERDICT BUILDER
# ============================================================
def build_final_verdict(symbol, spot, analysis, atm_data, chain, ai_rec, regime, trader_view, info):
    """Synthesize everything into ONE clear verdict."""
    verdict = {
        "action": "WAIT",
        "confidence": 0,
        "strike": None,
        "option_type": None,
        "entry": None,
        "target": None,
        "stop_loss": None,
        "lot_size": info.get("lot", 25),
        "total_cost": None,
        "profit_at_target": None,
        "loss_at_sl": None,
        "reasoning": "",
        "if_then": None,
        "urgency": "wait",
    }

    consensus = (ai_rec or {}).get("consensus", {}) or {}
    score = consensus.get("final_score", 0)
    ai_action = (ai_rec or {}).get("action", "NO TRADE")
    ai_confidence = (ai_rec or {}).get("confidence", 0)

    regime_name = (regime or {}).get("regime", "UNKNOWN")
    regime_tradeable = (regime or {}).get("tradeable", True)

    atm_strike = analysis.get("atm", 0)
    step = info.get("step", 50)
    lot_size = info.get("lot", 25)

    ce_ltp = atm_data.get("ce_ltp") or 0
    pe_ltp = atm_data.get("pe_ltp") or 0

    # ─── CASE 1: AI gave specific trade ───
    if ai_action in ("BUY CALL", "BUY PUT") and ai_confidence >= 6:
        opt_type = "CE" if ai_action == "BUY CALL" else "PE"
        strike = ai_rec.get("strike") or atm_strike
        entry = ai_rec.get("entry") or _find_premium(chain, strike, opt_type) or (ce_ltp if opt_type == "CE" else pe_ltp)
        target = ai_rec.get("target") or round(entry * 1.7, 2)
        sl = ai_rec.get("stop_loss") or round(entry * 0.7, 2)

        urgency = "immediate" if abs(score) >= 40 else "wait_for_level"

        verdict.update({
            "action": ai_action,
            "confidence": min(95, int(abs(score) * 1.2 + ai_confidence * 3)),
            "strike": strike,
            "option_type": opt_type,
            "entry": round(entry, 2),
            "target": round(target, 2),
            "stop_loss": round(sl, 2),
            "total_cost": round(entry * lot_size, 2),
            "profit_at_target": round((target - entry) * lot_size, 2),
            "loss_at_sl": round((entry - sl) * lot_size, 2),
            "reasoning": ai_rec.get("reason", f"Consensus {score:+.1f}"),
            "urgency": urgency,
        })
        return verdict

    # ─── CASE 2: WAIT — but show IF-THEN plan ───
    scenarios = (trader_view or {}).get("scenarios", []) if trader_view else []

    bull_trigger = None
    bear_trigger = None
    bull_trade = None
    bear_trade = None

    for s in scenarios:
        if s.get("direction") == "UP" and not bull_trigger:
            bull_trigger = s.get("entry_level")
            if bull_trigger:
                ep = (ce_ltp or 100) * 1.15
                bull_trade = {
                    "strike": atm_strike + step,
                    "type": "CE",
                    "entry_premium": round(ep, 2),
                    "target_premium": round(ep * 1.7, 2),
                    "sl_premium": round(ep * 0.7, 2),
                    "cost": round(ep * lot_size, 2),
                    "profit": round((ep * 1.7 - ep) * lot_size, 2),
                    "loss": round((ep - ep * 0.7) * lot_size, 2),
                }
        if s.get("direction") == "DOWN" and not bear_trigger:
            bear_trigger = s.get("entry_level")
            if bear_trigger:
                ep = (pe_ltp or 100) * 1.15
                bear_trade = {
                    "strike": atm_strike - step,
                    "type": "PE",
                    "entry_premium": round(ep, 2),
                    "target_premium": round(ep * 1.7, 2),
                    "sl_premium": round(ep * 0.7, 2),
                    "cost": round(ep * lot_size, 2),
                    "profit": round((ep * 1.7 - ep) * lot_size, 2),
                    "loss": round((ep - ep * 0.7) * lot_size, 2),
                }

    # Fallback to ATM levels if no scenarios
    if not bull_trigger:
        bull_trigger = analysis.get("resistance", spot * 1.005)
    if not bear_trigger:
        bear_trigger = analysis.get("support", spot * 0.995)

    if not bull_trade:
        ep = (ce_ltp or 100) * 1.15
        bull_trade = {
            "strike": atm_strike + step, "type": "CE",
            "entry_premium": round(ep, 2),
            "target_premium": round(ep * 1.7, 2),
            "sl_premium": round(ep * 0.7, 2),
            "cost": round(ep * lot_size, 2),
            "profit": round((ep * 1.7 - ep) * lot_size, 2),
            "loss": round((ep - ep * 0.7) * lot_size, 2),
        }
    if not bear_trade:
        ep = (pe_ltp or 100) * 1.15
        bear_trade = {
            "strike": atm_strike - step, "type": "PE",
            "entry_premium": round(ep, 2),
            "target_premium": round(ep * 1.7, 2),
            "sl_premium": round(ep * 0.7, 2),
            "cost": round(ep * lot_size, 2),
            "profit": round((ep * 1.7 - ep) * lot_size, 2),
            "loss": round((ep - ep * 0.7) * lot_size, 2),
        }

    verdict.update({
        "action": "WAIT",
        "confidence": 0,
        "reasoning": f"Score {score:+.1f}. {regime_name} regime. Wait for breakout.",
        "if_then": {
            "bull_trigger": bull_trigger,
            "bear_trigger": bear_trigger,
            "bull_trade": bull_trade,
            "bear_trade": bear_trade,
        },
        "urgency": "wait_for_level",
    })
    return verdict


def format_final_verdict(v):
    """Format final verdict output."""
    lines = []
    lines.append("")
    lines.append("╔" + "═" * 66 + "╗")
    lines.append("║  🎯 FINAL VERDICT — WHAT TO DO RIGHT NOW" + " " * 25 + "║")
    lines.append("╚" + "═" * 66 + "╝")

    if v.get("urgency") == "immediate" and v.get("option_type"):
        emoji = "🟢" if "CALL" in v["action"] else "🔴"
        lines.append(f"\n{emoji} ACTION: {v['action']}  (Confidence: {v['confidence']}%)")
        lines.append("")
        lines.append(f"   📍 BUY {v.get('strike', '?')} {v.get('option_type', '?')}")
        lines.append(f"   Entry:      ₹{v.get('entry', '?')}")
        lines.append(f"   Target:     ₹{v.get('target', '?')}")
        lines.append(f"   Stop Loss:  ₹{v.get('stop_loss', '?')}")
        lines.append("")
        lines.append(f"   Lot Size:   {v.get('lot_size', 0)} qty")
        lines.append(f"   Total Cost: ₹{v.get('total_cost', 0)}")
        lines.append(f"   Max Profit: ₹+{v.get('profit_at_target', 0)}")
        lines.append(f"   Max Loss:   ₹-{v.get('loss_at_sl', 0)}")
        lines.append("")
        lines.append(f"   💡 {v.get('reasoning', '')}")

    else:
        lines.append(f"\n⏸️  ACTION: WAIT")
        lines.append(f"   {v.get('reasoning', '')}")

        if_then = v.get("if_then", {}) or {}
        lines.append("")
        lines.append("   ────── TRADE PLAN (IF-THEN) ──────")

        bt = if_then.get("bull_trade")
        if bt and if_then.get("bull_trigger"):
            lines.append(f"\n   🟢 IF NIFTY closes ABOVE ₹{if_then['bull_trigger']:.0f}")
            lines.append(f"      → BUY {bt['strike']} {bt['type']}")
            lines.append(f"      Entry:  ₹{bt['entry_premium']}  |  Target: ₹{bt['target_premium']}  |  SL: ₹{bt['sl_premium']}")
            lines.append(f"      Cost:   ₹{bt['cost']}  |  Profit: ₹+{bt['profit']}  |  Risk: ₹-{bt['loss']}")

        bt = if_then.get("bear_trade")
        if bt and if_then.get("bear_trigger"):
            lines.append(f"\n   🔴 IF NIFTY closes BELOW ₹{if_then['bear_trigger']:.0f}")
            lines.append(f"      → BUY {bt['strike']} {bt['type']}")
            lines.append(f"      Entry:  ₹{bt['entry_premium']}  |  Target: ₹{bt['target_premium']}  |  SL: ₹{bt['sl_premium']}")
            lines.append(f"      Cost:   ₹{bt['cost']}  |  Profit: ₹+{bt['profit']}  |  Risk: ₹-{bt['loss']}")

        lines.append("\n   🛑 OTHERWISE: No trade. Stay out.")

    lines.append("")
    lines.append("╚" + "═" * 66 + "╝")
    return "\n".join(lines)


# ============================================================
#  TRADER SECTION BUILDER (Regime + Trader View)
# ============================================================
def _build_trader_section(symbol, spot_price, ai_rec, analysis, atm_data, chain, info):
    """Build regime + trader view section. Returns (lines, trader_view)."""
    lines = []
    regime_data = None
    trader_view = None

    # Fetch historical daily data
    df_daily = None
    try:
        from core.backtest_equity import fetch_historical_data
        df_daily = fetch_historical_data("NSE_INDEX|Nifty 50", "day", days=100)
    except Exception as e:
        lines.append(f"\n⚠️ Historical fetch failed: {e}")
        return lines, None

    if df_daily is None or len(df_daily) < 30:
        return lines, None

    # Regime
    try:
        regime_data = detect_regime(df_daily)
        lines.append("")
        lines.append(format_regime(regime_data))
    except Exception as e:
        lines.append(f"\n⚠️ Regime detection failed: {e}")
        return lines, None

    # Trader View
    try:
        consensus = (ai_rec or {}).get("consensus", {}) or {}
        signals_raw = consensus.get("signals", {}) or {}

        signals = {}
        for key in ["SMC", "Patterns", "PCR", "FII/DII", "Global", "News"]:
            if key in signals_raw:
                signals[key] = {
                    "score": signals_raw[key].get("score", 0),
                    "raw": signals_raw[key].get("raw", ""),
                }

        # Levels
        levels = {"supports": [], "resistances": [], "bsl": None, "ssl": None}

        try:
            from core.liquidity_analyzer import analyze_liquidity
            liq = analyze_liquidity(df_daily)
            if liq.get("status") == "success":
                if liq.get("nearest_bsl"):
                    levels["bsl"] = liq["nearest_bsl"]["price"]
                if liq.get("nearest_ssl"):
                    levels["ssl"] = liq["nearest_ssl"]["price"]
        except Exception:
            pass

        try:
            from core.pattern_detector import detect_support_resistance
            sr = detect_support_resistance(df_daily)
            if sr.get("support"):
                levels["supports"].append({"price": sr["support"]})
            if sr.get("resistance"):
                levels["resistances"].append({"price": sr["resistance"]})
        except Exception:
            pass

        if not levels["supports"]:
            levels["supports"] = [{"price": round(spot_price * 0.995, 0)}]
        if not levels["resistances"]:
            levels["resistances"] = [{"price": round(spot_price * 1.005, 0)}]

        trader_view = build_trader_view(
            symbol=symbol,
            spot_price=spot_price,
            signals=signals,
            regime=regime_data,
            levels=levels,
            atm_data=atm_data,
        )
        lines.append(format_trader_view(trader_view))
    except Exception as e:
        lines.append(f"\n⚠️ Trader view failed: {e}")

    return lines, trader_view


# ============================================================
#  MAIN ENTRY
# ============================================================
def analyze_options(symbol_input, expiry_hint=None):
    symbol = symbol_input.upper().strip()
    if symbol not in OPTIONS_UNDERLYINGS:
        return (f"Options analysis for '{symbol}' available nahi hai.\n"
                f"Available: {', '.join(OPTIONS_UNDERLYINGS.keys())}")

    info = OPTIONS_UNDERLYINGS[symbol]
    instrument_key = info["key"]

    expiries = fetch_expiry_dates(instrument_key)
    if not expiries:
        return f"Expiry dates fetch nahi ho payi '{symbol}' ke liye."

    nearest_expiry = _select_expiry(expiries, expiry_hint)
    if not nearest_expiry:
        nearest_expiry = expiries[0]

    if expiry_hint:
        print(f"   📅 Expiry hint '{expiry_hint}' → selected {nearest_expiry}")

    chain = fetch_option_chain(instrument_key, nearest_expiry)
    if not chain:
        return f"Option chain fetch nahi ho payi '{symbol}' for {nearest_expiry}."

    live_spot = get_live_spot(instrument_key)
    chain_spot = chain[0].get("underlying_spot_price", 0) if chain else 0

    if live_spot and live_spot > 0:
        spot_price = live_spot
    else:
        spot_price = chain_spot if chain_spot > 0 else 0

    if spot_price <= 0:
        return f"Spot price fetch nahi ho paya '{symbol}' ke liye."

    analysis = analyze_chain(chain, spot_price)
    if not analysis:
        return f"Chain analysis fail ho gayi '{symbol}' ke liye."

    atm_strike = analysis["atm"]
    atm_data = {"ce_ltp": None, "pe_ltp": None, "ce_iv": None, "pe_iv": None}

    atm_entry = None
    for item in chain:
        if item.get("strike_price") == atm_strike:
            atm_entry = item
            break

    if atm_entry:
        ce = atm_entry.get("call_options", {})
        pe = atm_entry.get("put_options", {})
        atm_data["ce_ltp"] = ce.get("market_data", {}).get("ltp")
        atm_data["pe_ltp"] = pe.get("market_data", {}).get("ltp")
        atm_data["ce_iv"] = ce.get("option_greeks", {}).get("iv")
        atm_data["pe_iv"] = pe.get("option_greeks", {}).get("iv")

    ai_rec = get_ai_options_recommendation(symbol, spot_price, analysis, atm_data, nearest_expiry)
    expiry_risky, expiry_msg = check_expiry_day(nearest_expiry)
    days_to_exp = _days_to_expiry(nearest_expiry)

    pat_bias, pat_bull, pat_bear, pattern_str = _get_pattern_data(symbol)

    ce_iv_disp = atm_data.get('ce_iv') or 0
    pe_iv_disp = atm_data.get('pe_iv') or 0
    avg_iv_disp = (ce_iv_disp + pe_iv_disp) / 2 if (ce_iv_disp and pe_iv_disp) else 0
    iv_status_str, iv_warning_disp = _iv_status(avg_iv_disp)

    trade_suggestion = get_capital_aware_trade(
        symbol, chain, spot_price, atm_strike, ai_rec, info, nearest_expiry
    )

    trends = get_nifty_trend_dual()

    # ═══════════════════════════════════════════════════════
    #  BUILD OUTPUT
    # ═══════════════════════════════════════════════════════
    lines = []
    lines.append(f"📊 OPTIONS ANALYSIS — {symbol}")
    lines.append(f"Expiry: {nearest_expiry}  |  Spot: Rs. {spot_price:.2f}")
    if expiry_msg:
        lines.append(expiry_msg)
    lines.append(f"💰 Capital: Rs. {int(TRADING_CAPITAL)} | Max Risk/Trade: Rs. {int(MAX_RISK_PER_TRADE)}")
    lines.append("")
    lines.append(f"📈 NIFTY Trend:")
    lines.append(f"   Intraday (15m): {trends['intraday']}")
    lines.append(f"   Daily:          {trends['daily']}")
    lines.append(f"   Combined:       {trends['combined']}")
    lines.append("")
    lines.append(f"Put-Call Ratio (PCR):  {analysis['pcr']}")
    lines.append(f"Max Pain Strike:       {analysis['max_pain']}")
    lines.append(f"Support (PE OI):       {analysis['support']}")
    lines.append(f"Resistance (CE OI):    {analysis['resistance']}")
    lines.append(f"ATM Strike:            {atm_strike}")
    lines.append("")
    if atm_data['ce_ltp'] and atm_data['pe_ltp']:
        lines.append(f"ATM CE LTP: Rs. {atm_data['ce_ltp']}  |  IV: {atm_data['ce_iv']}")
        lines.append(f"ATM PE LTP: Rs. {atm_data['pe_ltp']}  |  IV: {atm_data['pe_iv']}")
        lines.append("")

    lines.append(f"🎨 Patterns ({symbol}): {pattern_str}")
    lines.append(f"🎨 Pattern Bias:       {pat_bias} (Bull {pat_bull} / Bear {pat_bear})")
    lines.append(f"📈 IV Status:          {iv_status_str}")
    if iv_warning_disp:
        lines.append(f"   ⚠️ {iv_warning_disp}")
    lines.append(f"⏳ Days to Expiry:     {days_to_exp}")
    lines.append("")

    # ─── Consensus score ───
    if ai_rec and ai_rec.get("consensus"):
        lines.append("")
        lines.append(format_consensus(ai_rec["consensus"]))
        lines.append("")

    # ─── AI recommendation ───
    if ai_rec:
        lines.append("🤖 JARVIS AI RECOMMENDATION:")
        lines.append(f"Action:     {ai_rec.get('action', 'N/A')}")
        lines.append(f"Strike:     {ai_rec.get('strike', 'N/A')}")
        lines.append(f"Entry:      Rs. {ai_rec.get('entry', 'N/A')}")
        lines.append(f"Target:     Rs. {ai_rec.get('target', 'N/A')}")
        lines.append(f"Stop Loss:  Rs. {ai_rec.get('stop_loss', 'N/A')}")
        lines.append(f"Confidence: {ai_rec.get('confidence', 'N/A')}/10")
        lines.append("")
        lines.append(f"Reason: {ai_rec.get('reason', '')}")
        lines.append(f"Risks:  {ai_rec.get('risks', '')}")

        if ai_rec.get("action") == "NO TRADE" and ai_rec.get("wait_for"):
            lines.append("")
            lines.append(f"⏳ WAIT FOR: {ai_rec['wait_for']}")
    else:
        lines.append("AI recommendation generate nahi ho payi.")

    # ─── Trader Section (Regime + Trader View) ───
    trader_lines, trader_view = _build_trader_section(
        symbol, spot_price, ai_rec, analysis, atm_data, chain, info
    )
    lines.extend(trader_lines)

    # ─── FINAL VERDICT ───
    try:
        # Get regime data from trader section (already fetched)
        regime_data = None
        try:
            from core.backtest_equity import fetch_historical_data
            df_d = fetch_historical_data("NSE_INDEX|Nifty 50", "day", days=100)
            if df_d is not None and len(df_d) > 30:
                regime_data = detect_regime(df_d)
        except Exception:
            pass

        verdict = build_final_verdict(
            symbol=symbol,
            spot=spot_price,
            analysis=analysis,
            atm_data=atm_data,
            chain=chain,
            ai_rec=ai_rec,
            regime=regime_data or {},
            trader_view=trader_view,
            info=info,
        )
        lines.append(format_final_verdict(verdict))
    except Exception as e:
        lines.append(f"\n⚠️ Final verdict failed: {e}")

    # ─── Trade Suggestion (AI gave trade only) ───
    if trade_suggestion and trade_suggestion.get("is_ai_trade"):
        lines.append("")
        lines.append("═" * 55)
        lines.append(f"🎯 CAPITAL CHECK: {trade_suggestion['strategy'].upper()}")
        lines.append("═" * 55)

        for leg in trade_suggestion["legs"]:
            lines.append(f"  BUY {symbol} {leg['strike']:.0f} {leg['type']} @ Rs. {leg['price']}")

        lines.append("")
        lines.append(f"  Lot Size:      {trade_suggestion['lot_size']} qty")
        lines.append(f"  Total Cost:    Rs. {trade_suggestion['cost']}")
        lines.append(f"  Max Profit:    Rs. +{trade_suggestion['profit_at_target']}")
        lines.append(f"  Max Loss:      Rs. -{trade_suggestion['loss_at_sl']}")
        lines.append(f"  Capital Used:  {trade_suggestion['capital_used_pct']}% of Rs. {int(TRADING_CAPITAL)}")
        lines.append(f"  {trade_suggestion['status']}")
        lines.append("═" * 55)

    lines.append("")
    lines.append("⚠️  Note: Options trading risky hai. Ye sirf analysis hai, trade decision aapka hai.")
    lines.append("⚠️  Paper trade karein pehle. Live trade apni risk par karein.")

    return "\n".join(lines)