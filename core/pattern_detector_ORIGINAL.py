"""
core/pattern_detector.py
Advanced Pattern Detection using TA-Lib (V5.0)

V5 changes over V4:
  #1  Near Support/Resistance and S/R levels use PRIOR bars only (today excluded),
      so "Near Resistance" no longer fires together with "52W Breakout" (and
      "Near Support" no longer fires together with "52W Breakdown").
  #2  _trim_incomplete_candle is safe for non-DatetimeIndex frames and only
      runs for NSE/BSE data (controlled by `trim_open_candle`).
  #3  Candlestick strength is capped at 1.0 so a single +/-200 pattern
      (Engulfing, Hikkake...) can't dominate chart patterns (0.4-0.95).

NOTE: weights (0.3 gate, 1.2 volume boost, 0.15 threshold, 20% decay,
      MIN_EVIDENCE) are still untuned guesses. Backtest before live use.
"""
import pandas as pd
import numpy as np
import talib
import yfinance as yf
from datetime import datetime
import pytz

# ─── DIRECTION OVERRIDES ───
FIXED_DIRECTION = {
    "Doji": "NEUTRAL", "Long-Legged Doji": "NEUTRAL",
    "Rickshaw Man": "NEUTRAL", "High Wave": "NEUTRAL",
    "Spinning Top": "NEUTRAL",
    "Gravestone Doji": "BEARISH",
    "Dragonfly Doji": "BULLISH", "Takuri": "BULLISH",
}

# ─── DEDUPLICATION FAMILIES ───
FAMILIES = {
    "doji": {"Doji", "Long-Legged Doji", "Rickshaw Man", "High Wave", "Spinning Top", "Dragonfly Doji", "Gravestone Doji", "Takuri"},
    "star": {"Morning Star", "Morning Doji Star", "Evening Star", "Evening Doji Star", "Abandoned Baby"},
    "engulf": {"Engulfing", "3 Outside"},
    "harami": {"Harami", "Harami Cross", "3 Inside"},
    "kicking": {"Kicking", "Kicking by Length"},
    "hikkake": {"Hikkake", "Modified Hikkake"},
    "soldiers": {"3 White Soldiers", "3 Black Crows", "Advance Block"},
    "methods": {"Rising/Falling 3 Methods", "Mat Hold", "Tasuki Gap"},
}

# ─── REVERSAL PATTERNS (For Trend Gating) ───
REVERSAL = {
    "Hammer", "Inverted Hammer", "Hanging Man", "Shooting Star", "Engulfing",
    "Harami", "Harami Cross", "Morning Star", "Evening Star", "Morning Doji Star",
    "Evening Doji Star", "Abandoned Baby", "3 Inside", "3 Outside", "Piercing",
    "Dark Cloud Cover", "Doji Star", "Tristar", "Dragonfly Doji", "Gravestone Doji",
    "Takuri", "Belt Hold"
}

MAX_CANDLE_STRENGTH = 1.0   # FIX #3
SR_LOOKBACK = 20


def _dedupe_patterns(patterns):
    best = {}
    for p in patterns:
        fam = next((f for f, names in FAMILIES.items() if p["pattern"] in names), p["pattern"])
        key = (fam, p["direction"])
        if key not in best or p["strength"] > best[key]["strength"]:
            best[key] = p
    return list(best.values())


def _norm_adv(ap):
    """Normalize advanced patterns to match basic pattern shape."""
    return {
        "pattern": ap["pattern"], "direction": ap["direction"],
        "strength": round(ap.get("confidence", 60) / 100, 2),
        "source": "advanced_chart", "bars_ago": 0, "is_latest": True,
        "breakout_level": ap.get("breakout_level"), "target": ap.get("target"),
    }


def _empty(ticker, err):
    """Fail-safe return shape to prevent KeyErrors in bot."""
    return {
        "ticker": ticker, "patterns": [], "advanced_patterns": [],
        "bull_score": 0, "bear_score": 0, "normalized_score": 0,
        "total_score": 0, "advanced_score": 0, "overall_bias": "NEUTRAL",
        "total_detected": 0, "support": None, "resistance": None,
        "pivot": None, "error": err
    }


# ============================================================
#  FIX #2: SAFE INCOMPLETE-CANDLE TRIM
# ============================================================
def _is_nse_ticker(ticker):
    t = str(ticker).upper()
    return t.endswith(".NS") or t.endswith(".BO") or t.startswith("^NSE") or t in ("^NSEI", "^BSESN")


def _trim_incomplete_candle(df):
    """
    Remove today's candle if the NSE session (till 15:30 IST) is still open.
    Safe no-op when the index isn't a DatetimeIndex or on any error.
    """
    try:
        if df is None or df.empty or not isinstance(df.index, pd.DatetimeIndex):
            return df

        now_ist = datetime.now(pytz.timezone('Asia/Kolkata'))
        market_closed = (now_ist.hour, now_ist.minute) >= (15, 30)
        if market_closed:
            return df

        last = df.index[-1]
        last_date = last.date() if hasattr(last, "date") else None
        if last_date == now_ist.date():
            return df.iloc[:-1]
        return df
    except Exception as e:
        print(f"[Trim Warning] {e}")
        return df


# ============================================================
#  CANDLESTICK PATTERNS
# ============================================================
def detect_candlestick_patterns(df, lookback=5):
    if df is None or len(df) < 50:
        return []

    try:
        o = df['Open'].values.astype(float)
        h = df['High'].values.astype(float)
        l = df['Low'].values.astype(float)
        c = df['Close'].values.astype(float)
        v = df['Volume'].values.astype(float) if 'Volume' in df.columns else np.zeros_like(c)

        pattern_functions = {
            "Doji": "CDLDOJI", "Dragonfly Doji": "CDLDRAGONFLYDOJI",
            "Gravestone Doji": "CDLGRAVESTONEDOJI", "Long-Legged Doji": "CDLLONGLEGGEDDOJI",
            "Hammer": "CDLHAMMER", "Inverted Hammer": "CDLINVERTEDHAMMER",
            "Hanging Man": "CDLHANGINGMAN", "Shooting Star": "CDLSHOOTINGSTAR",
            "Marubozu": "CDLMARUBOZU", "Spinning Top": "CDLSPINNINGTOP",
            "High Wave": "CDLHIGHWAVE", "Closing Marubozu": "CDLCLOSINGMARUBOZU",
            "Belt Hold": "CDLBELTHOLD", "Engulfing": "CDLENGULFING",
            "Harami": "CDLHARAMI", "Harami Cross": "CDLHARAMICROSS",
            "Dark Cloud Cover": "CDLDARKCLOUDCOVER", "Piercing": "CDLPIERCING",
            "Doji Star": "CDLDOJISTAR", "Kicking": "CDLKICKING",
            "Kicking by Length": "CDLKICKINGBYLENGTH", "Morning Star": "CDLMORNINGSTAR",
            "Evening Star": "CDLEVENINGSTAR", "Morning Doji Star": "CDLMORNINGDOJISTAR",
            "Evening Doji Star": "CDLEVENINGDOJISTAR", "3 White Soldiers": "CDL3WHITESOLDIERS",
            "3 Black Crows": "CDL3BLACKCROWS", "3 Inside": "CDL3INSIDE",
            "3 Outside": "CDL3OUTSIDE", "Abandoned Baby": "CDLABANDONEDBABY",
            "Tristar": "CDLTRISTAR", "2 Crows": "CDL2CROWS", "Advance Block": "CDLADVANCEBLOCK",
            "Rising/Falling 3 Methods": "CDLRISEFALL3METHODS", "Mat Hold": "CDLMATHOLD",
            "Tasuki Gap": "CDLTASUKIGAP", "Hikkake": "CDLHIKKAKE",
            "Modified Hikkake": "CDLHIKKAKEMOD", "Ladder Bottom": "CDLLADDERBOTTOM",
            "Rickshaw Man": "CDLRICKSHAWMAN", "Takuri": "CDLTAKURI",
            "3 Line Strike": "CDL3LINESTRIKE", "Unique 3 River": "CDLUNIQUE3RIVER",
            "Upside Gap 2 Crows": "CDLUPSIDEGAP2CROWS", "Gap Side by Side": "CDLGAPSIDESIDEWHITE",
            "Matching Low": "CDLMATCHINGLOW", "Homing Pigeon": "CDLHOMINGPIGEON",
            "Separating Lines": "CDLSEPARATINGLINES", "In Neck": "CDLINNECK",
            "On Neck": "CDLONNECK", "Thrusting": "CDLTHRUSTING",
            "Counterattack": "CDLCOUNTERATTACK", "Concealing Baby Swallow": "CDLCONCEALBABYSWALL",
            "Stalled Pattern": "CDLSTALLEDPATTERN", "3 Stars in South": "CDL3STARSINSOUTH"
        }

        detected = []

        # Pre-calculate series for gating
        ema20_s = pd.Series(c).ewm(span=20, adjust=False).mean().values
        rsi_s = talib.RSI(c, timeperiod=14)

        # Average volume excluding today
        avg_vol = pd.Series(v).rolling(20).mean().shift(1).iloc[-1]
        is_high_vol = v[-1] > (avg_vol * 1.5) if pd.notna(avg_vol) and avg_vol > 0 else False

        for name, func_name in pattern_functions.items():
            try:
                func = getattr(talib, func_name, None)
                if func is None:
                    continue

                result = func(o, h, l, c)
                if result is None or len(result) < 1:
                    continue

                recent = result[-lookback:] if len(result) >= lookback else result

                for offset in range(len(recent) - 1, -1, -1):
                    val = recent[offset]
                    if val != 0:
                        bars_ago = len(recent) - offset - 1
                        is_latest = (bars_ago == 0)

                        direction = FIXED_DIRECTION.get(name, "BULLISH" if val > 0 else "BEARISH")
                        if direction == "NEUTRAL":
                            break

                        # FIX #3: cap strength at 1.0 (TA-Lib can return +/-200)
                        strength = min(abs(val) / 100.0, MAX_CANDLE_STRENGTH)

                        # Trend gating at the pattern's own bar
                        i = len(c) - 1 - bars_ago
                        if name in REVERSAL and not pd.isna(rsi_s[i]):
                            if direction == "BULLISH" and (c[i] > ema20_s[i] or rsi_s[i] > 40):
                                strength *= 0.3
                            elif direction == "BEARISH" and (c[i] < ema20_s[i] or rsi_s[i] < 60):
                                strength *= 0.3

                        # Volume multiplier only on latest bar
                        if is_latest and is_high_vol:
                            strength *= 1.2

                        # Stale signal decay
                        if bars_ago > 0:
                            strength *= (1.0 - 0.20 * bars_ago)

                        if strength > 0.1:
                            detected.append({
                                "pattern": name, "direction": direction,
                                "strength": round(strength, 2), "source": "candlestick",
                                "bars_ago": bars_ago, "is_latest": is_latest,
                            })
                        break
            except Exception:
                continue

        deduped = _dedupe_patterns(detected)
        deduped.sort(key=lambda x: (not x['is_latest'], -x['strength']))
        return deduped

    except Exception as e:
        print(f"[Pattern Error] {e}")
        return []


# ============================================================
#  BASIC CHART PATTERNS
# ============================================================
def detect_chart_patterns(df):
    if df is None or len(df) < 50:
        return []

    try:
        close = df['Close']
        detected = []
        current = close.iloc[-1]

        # FIX #1: range from PRIOR bars only (today excluded)
        prior = df.iloc[-(SR_LOOKBACK + 1):-1]
        resistance = prior['High'].max()
        support = prior['Low'].min()

        if resistance > support:
            position = (current - support) / (resistance - support)
            # position < 0 or > 1 means breakdown / breakout -> not "near" S/R
            if 0 <= position < 0.15:
                detected.append({"pattern": "Near Support", "direction": "BULLISH", "strength": 0.7, "source": "chart", "bars_ago": 0, "is_latest": True})
            elif 0.85 < position <= 1.0:
                detected.append({"pattern": "Near Resistance", "direction": "BEARISH", "strength": 0.7, "source": "chart", "bars_ago": 0, "is_latest": True})

        ema_20 = close.ewm(span=20, adjust=False).mean()
        ema_50 = close.ewm(span=50, adjust=False).mean()

        # Trend state, low weight so it doesn't permanently tilt the score
        if close.iloc[-1] > ema_20.iloc[-1] > ema_50.iloc[-1]:
            detected.append({"pattern": "Uptrend Channel", "direction": "BULLISH", "strength": 0.4, "source": "chart", "bars_ago": 0, "is_latest": True})
        elif close.iloc[-1] < ema_20.iloc[-1] < ema_50.iloc[-1]:
            detected.append({"pattern": "Downtrend Channel", "direction": "BEARISH", "strength": 0.4, "source": "chart", "bars_ago": 0, "is_latest": True})

        # 52W Breakout / Breakdown (prior bars only, strict comparison)
        if len(df) >= 252:
            prior_52w = df.iloc[-252:-1]
            if current > prior_52w['High'].max():
                detected.append({"pattern": "52W Breakout", "direction": "BULLISH", "strength": 0.9, "source": "chart", "bars_ago": 0, "is_latest": True})
            if current < prior_52w['Low'].min():
                detected.append({"pattern": "52W Breakdown", "direction": "BEARISH", "strength": 0.9, "source": "chart", "bars_ago": 0, "is_latest": True})

        if len(df) >= 200:
            sma50 = close.rolling(50).mean()
            sma200 = close.rolling(200).mean()
            if pd.notna(sma50.iloc[-1]) and pd.notna(sma200.iloc[-1]):
                if sma50.iloc[-1] > sma200.iloc[-1] and sma50.iloc[-2] <= sma200.iloc[-2]:
                    detected.append({"pattern": "Golden Cross", "direction": "BULLISH", "strength": 0.95, "source": "chart", "bars_ago": 0, "is_latest": True})
                elif sma50.iloc[-1] < sma200.iloc[-1] and sma50.iloc[-2] >= sma200.iloc[-2]:
                    detected.append({"pattern": "Death Cross", "direction": "BEARISH", "strength": 0.95, "source": "chart", "bars_ago": 0, "is_latest": True})

        return detected
    except Exception as e:
        print(f"[Chart Pattern Error] {e}")
        return []


# ============================================================
#  SUPPORT / RESISTANCE  (FIX #1: prior bars only)
# ============================================================
def detect_support_resistance(df, lookback=SR_LOOKBACK):
    """
    Key levels from the PRIOR `lookback` bars (today excluded), so levels
    are real reference levels for stop-loss/target and a breakout above
    `resistance` is actually detectable.
    """
    if df is None or len(df) < lookback + 1:
        return {}

    prior = df.iloc[-(lookback + 1):-1]
    try:
        hi = float(prior['High'].max())
        lo = float(prior['Low'].min())
        prev_close = float(prior['Close'].iloc[-1])
        return {
            "resistance": round(hi, 2),
            "support": round(lo, 2),
            # classic pivot from the previous completed bar
            "pivot": round((float(prior['High'].iloc[-1]) + float(prior['Low'].iloc[-1]) + prev_close) / 3, 2),
        }
    except Exception:
        return {}


# ============================================================
#  MAIN FUNCTION
# ============================================================
def get_pattern_summary(ticker_or_df, lookback=5, trim_open_candle=None):
    """
    trim_open_candle:
        None  -> auto: ticker string => only for NSE/BSE tickers (.NS/.BO);
                 DataFrame => True (bot is NSE-based; pass False for other markets
                 or if your df already contains only closed candles).
        True / False -> force on / off.
    """
    ticker = ticker_or_df if isinstance(ticker_or_df, str) else "custom_df"

    try:
        if isinstance(ticker_or_df, pd.DataFrame):
            df = ticker_or_df.copy()
            do_trim = True if trim_open_candle is None else trim_open_candle
        else:
            df = yf.Ticker(ticker).history(period="2y", auto_adjust=False)
            do_trim = _is_nse_ticker(ticker) if trim_open_candle is None else trim_open_candle

        if df is None or df.empty:
            return _empty(ticker, "No data")

        if do_trim:
            df = _trim_incomplete_candle(df)
            if df is None or df.empty:
                return _empty(ticker, "No data after trimming open candle")

        df.columns = [str(col).capitalize() for col in df.columns]
        df = df.dropna(subset=['Open', 'High', 'Low', 'Close'])

        candle_patterns = detect_candlestick_patterns(df, lookback=lookback)
        chart_patterns = detect_chart_patterns(df)
        sr_levels = detect_support_resistance(df)

        adv_patterns = []
        adv_score = 0
        try:
            from core.advanced_chart_patterns import detect_advanced_patterns, get_pattern_score
            adv_patterns = detect_advanced_patterns(df)
            adv_score, _ = get_pattern_score(adv_patterns)
        except Exception as e:
            print(f"[Advanced Pattern Error] {e}")

        all_basic_patterns = candle_patterns + chart_patterns

        bull_score = sum(p["strength"] for p in all_basic_patterns if p["direction"] == "BULLISH")
        bear_score = sum(p["strength"] for p in all_basic_patterns if p["direction"] == "BEARISH")

        # Advanced score counted ONCE (assumes get_pattern_score is on a -100..+100 scale)
        if adv_score > 0:
            bull_score += (adv_score / 100)
        elif adv_score < 0:
            bear_score += (abs(adv_score) / 100)

        total_score = bull_score + bear_score
        normalized_score = round((bull_score - bear_score) / (total_score + 1e-9), 2) if total_score > 0 else 0

        MIN_EVIDENCE = 1.0
        if total_score < MIN_EVIDENCE:
            overall = "NEUTRAL"
        elif normalized_score > 0.15:
            overall = "BULLISH"
        elif normalized_score < -0.15:
            overall = "BEARISH"
        else:
            overall = "NEUTRAL"

        display_patterns = all_basic_patterns + [_norm_adv(a) for a in adv_patterns]

        return {
            "ticker": ticker,
            "patterns": display_patterns,
            "advanced_patterns": adv_patterns,
            "advanced_score": adv_score,
            "bull_score": round(bull_score, 2),
            "bear_score": round(bear_score, 2),
            "normalized_score": normalized_score,
            "total_score": round(total_score, 2),
            "overall_bias": overall,
            "total_detected": len(display_patterns),
            "support": sr_levels.get("support"),
            "resistance": sr_levels.get("resistance"),
            "pivot": sr_levels.get("pivot"),
        }

    except Exception as e:
        return _empty(ticker, str(e))


# ============================================================
#  FORMATTER
# ============================================================
def format_patterns(summary):
    if not summary or summary.get("error"):
        return f"❌ Pattern detection failed: {summary.get('error') if summary else 'Unknown'}"

    lines = []
    lines.append(f"🎨 PATTERN ANALYSIS — {summary['ticker']}")
    lines.append("─" * 60)
    lines.append(f"Overall Bias: {summary['overall_bias']} (Normalized: {summary.get('normalized_score', 0)}, Total: {summary.get('total_score', 0)})")
    lines.append(f"Bull Score: {summary['bull_score']} | Bear Score: {summary['bear_score']}")

    if summary.get("support") is not None:
        lines.append(f"Support: ₹{summary['support']} | Resistance: ₹{summary['resistance']} | Pivot: ₹{summary.get('pivot')}")

    lines.append(f"Total Detected: {summary.get('total_detected', 0)}")

    adv = summary.get("advanced_patterns", [])
    if adv:
        lines.append("")
        lines.append(f"🎯 ADVANCED CHART PATTERNS ({len(adv)} found):")
        for ap in adv[:5]:
            emoji = {"BULLISH": "🟢", "BEARISH": "🔴", "NEUTRAL": "🟡"}.get(ap.get("direction"), "❓")
            lines.append(f"   {emoji} {ap['pattern']:<28} ({ap['direction']}, conf {ap.get('confidence', 0)}%)")
            if ap.get("breakout_level"):
                lines.append(f"      Breakout: ₹{ap['breakout_level']}  Target: ₹{ap.get('target', '?')}")

    patterns = summary.get("patterns", [])
    if patterns:
        lines.append("")
        lines.append("Detected Patterns:")
        for p in patterns:
            if p["direction"] == "BULLISH":
                emoji = "🟢"
            elif p["direction"] == "BEARISH":
                emoji = "🔴"
            else:
                emoji = "🟡"

            star = "⭐ " if p.get("is_latest") else "   "
            ago = p.get("bars_ago", 0)
            ago_str = "latest" if ago == 0 else f"{ago}bar ago"
            str_val = p.get('strength', 0)

            lines.append(f"  {star}{emoji} {p['pattern']:<25} ({p['direction']}, str {str_val}) — {ago_str}")
    else:
        lines.append("No significant patterns detected.")

    return "\n".join(lines)


# ============================================================
#  STANDALONE TEST
# ============================================================
if __name__ == "__main__":
    import sys

    tkr = sys.argv[1] if len(sys.argv) > 1 else "RELIANCE.NS"
    res = get_pattern_summary(tkr)
    print(format_patterns(res))
    print(f"\nBias: {res.get('overall_bias')} | Total score: {res.get('total_score')} | Advanced: {res.get('advanced_score')}")