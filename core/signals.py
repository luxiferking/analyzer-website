"""
core/signals.py  (V2.0)

Fixes over V1
-------------
 1. Triple counting removed. Factors are now separate: trend state, fresh
    cross EVENTS (EMA + MACD), trend strength (ADX/DI), RSI momentum.
 2. Signals are EVENT based: a BUY/SELL needs a fresh cross in the last N bars
    (no more "BUY every day of an uptrend"). `trigger_time` + SignalDeduper
    stop the bot from entering the same setup again and again.
 3. ADX is a real gate (adx_min) instead of only a score booster.
 4. RSI exhaustion guard (no BUY above 75 / SELL below 25). RSI is no longer
    mean-reversion and momentum at the same time.
 5. Wilder RSI / ATR / ADX everywhere (one smoothing method, matches TA-Lib).
 6. Indicators are ALWAYS recomputed (no trusting stale columns). Input df is
    never modified. MACD_Signal KeyError fixed.
 7. Input validation: column names, duplicates, sort order, NaN / bad OHLC rows,
    min bars (120), stale-data check, incomplete last candle trimmed.
 8. NaN checks on BOTH latest and previous row; divide-by-zero safe
    (flat prices, zero ATR).
 9. Always returns the same dict shape with a `reason`, so the bot can tell
    "no setup" from "bad data". Never raises.
10. ATR based stop_loss / target in the result, plus volume ratio, score,
    components, signal_id and trigger_time for logging / backtest.
11. `mtf_checker` is now actually used (veto hook).
12. All thresholds live in PARAMS. `mode="legacy"` keeps the OLD scoring
    rules (on the fixed indicators) so you can A/B backtest old vs new.
13. Speed: only the last `max_bars` rows are used for indicators.

Backward compatible: ensemble_signals(df, mtf_checker=None) -> (signal, confidence)
Full detail:          analyze_signal(df, ...) -> dict

NOT handled (needs your decision / data):
 - Intraday session resets of indicators (indicators run continuously across days).
 - Entry price: signal is built on the CLOSE of the last completed bar, so the
   realistic fill is the NEXT bar's open. Backtest it that way.
 - "confidence" is a score magnitude, NOT a win probability.
"""
from datetime import datetime

import numpy as np
import pandas as pd
import pytz

VERSION = "2.0"
IST = pytz.timezone("Asia/Kolkata")
REQ = ["Open", "High", "Low", "Close"]

PARAMS = {
    "min_bars": 120,           # EMA50 / ADX need ~100+ bars to converge
    "max_bars": 300,           # only last N bars are used (speed)
    "cross_lookback": 3,       # a cross counts as a fresh trigger for N bars
    "adx_min": 25,             # below this: no trend -> no trend signal
    "adx_strong": 25,
    "rsi_exhaust_hi": 75,      # no new BUY above this
    "rsi_exhaust_lo": 25,      # no new SELL below this
    "signal_threshold": 50,    # v2 score needed
    "legacy_threshold": 30,    # old threshold (mode="legacy")
    "atr_stop_mult": 1.5,
    "atr_target_mult": 3.0,
    "max_stale_days": 5,       # daily bars: last bar older than this = stale
    "min_vol_ratio": None,     # e.g. 1.0 to require volume >= 20-bar average
}


# ============================================================
#  RESULT SHAPE
# ============================================================
def _neutral(reason, mode="v2", error=None, **extra):
    res = {
        "signal": "NEUTRAL", "confidence": 0.0, "score": 0.0,
        "reason": reason, "error": error, "mode": mode, "version": VERSION,
        "symbol": None, "bar_time": None, "trigger_time": None, "signal_id": None,
        "close": None, "atr": None, "stop_loss": None, "target": None,
        "vol_ratio": None, "components": {}, "indicators": {},
    }
    res.update(extra)
    return res


# ============================================================
#  TIME HELPERS
# ============================================================
def _to_ist(ts):
    ts = pd.Timestamp(ts)
    return ts.tz_localize(IST) if ts.tzinfo is None else ts.tz_convert(IST)


def _session_open(now):
    return now.weekday() < 5 and (9, 15) <= (now.hour, now.minute) < (15, 30)


def _bar_step(df):
    if not isinstance(df.index, pd.DatetimeIndex) or len(df) < 3:
        return None
    step = df.index.to_series().diff().dropna().median()
    return None if pd.isna(step) else step


def _trim_incomplete(df, intraday):
    """Drop the last bar if it is still forming."""
    if not isinstance(df.index, pd.DatetimeIndex) or len(df) < 3:
        return df
    try:
        now = datetime.now(IST)
        last = _to_ist(df.index[-1])
        if intraday:
            step = _bar_step(df)
            if step is not None and last + step > now:
                return df.iloc[:-1]
            return df
        if last.date() == now.date() and (now.hour, now.minute) < (15, 30):
            return df.iloc[:-1]
    except Exception:
        pass
    return df


def _is_stale(df, intraday, p):
    if not isinstance(df.index, pd.DatetimeIndex):
        return False
    try:
        now = datetime.now(IST)
        last = _to_ist(df.index[-1])
        if intraday:
            step = _bar_step(df)
            if step is None or not _session_open(now):
                return False
            session_start = now.replace(hour=9, minute=15, second=0, microsecond=0)
            return (now - max(last, session_start)) > 3 * step
        return (now - last).days > p["max_stale_days"]
    except Exception:
        return False


# ============================================================
#  VALIDATION
# ============================================================
def _validate(df, min_bars):
    if not isinstance(df, pd.DataFrame) or df.empty:
        return None, "no_data"
    d = df.copy()
    d.columns = [str(c).strip().capitalize() for c in d.columns]
    missing = [c for c in REQ if c not in d.columns]
    if missing:
        return None, "missing_columns:" + ",".join(missing)
    d = d[~d.index.duplicated(keep="last")].sort_index()
    for c in REQ + (["Volume"] if "Volume" in d.columns else []):
        d[c] = pd.to_numeric(d[c], errors="coerce")
    d = d.dropna(subset=REQ)
    d = d[(d["High"] >= d["Low"]) & (d["Close"] > 0)]
    if len(d) < min_bars:
        return None, "insufficient_data"
    return d, None


# ============================================================
#  INDICATORS (all Wilder where applicable)
# ============================================================
def _wilder(s, n=14):
    return s.ewm(alpha=1.0 / n, adjust=False, min_periods=n).mean()


def compute_indicators(df):
    """Always recomputes from OHLC. Returns a NEW DataFrame."""
    c, h, l = df["Close"], df["High"], df["Low"]
    out = pd.DataFrame(index=df.index)
    out["Close"] = c
    out["EMA_9"] = c.ewm(span=9, adjust=False).mean()
    out["EMA_21"] = c.ewm(span=21, adjust=False).mean()
    out["EMA_50"] = c.ewm(span=50, adjust=False).mean()

    # RSI (Wilder), flat-price safe
    delta = c.diff()
    gain = _wilder(delta.clip(lower=0))
    loss = _wilder((-delta).clip(lower=0))
    rs = gain / loss.replace(0, np.nan)
    rsi = 100 - 100 / (1 + rs)
    rsi[(loss == 0) & (gain > 0)] = 100.0
    rsi[(loss == 0) & (gain == 0)] = 50.0
    out["RSI"] = rsi

    # ATR (Wilder) - single definition used everywhere
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    atr = _wilder(tr)
    out["ATR"] = atr

    # ADX / DI (Wilder), divide-by-zero safe
    up = h.diff()
    down = -l.diff()
    plus_dm = up.where((up > down) & (up > 0), 0.0)
    minus_dm = down.where((down > up) & (down > 0), 0.0)
    atr_safe = atr.replace(0, np.nan)
    plus_di = 100 * _wilder(plus_dm) / atr_safe
    minus_di = 100 * _wilder(minus_dm) / atr_safe
    denom = (plus_di + minus_di).replace(0, np.nan)
    dx = 100 * (plus_di - minus_di).abs() / denom
    dx = dx.mask(atr.notna() & dx.isna(), 0.0)      # flat market -> no trend
    out["PLUS_DI"] = plus_di.mask(atr.notna() & plus_di.isna(), 0.0)
    out["MINUS_DI"] = minus_di.mask(atr.notna() & minus_di.isna(), 0.0)
    out["ADX"] = _wilder(dx)

    # MACD
    macd = c.ewm(span=12, adjust=False).mean() - c.ewm(span=26, adjust=False).mean()
    out["MACD"] = macd
    out["MACD_Signal"] = macd.ewm(span=9, adjust=False).mean()
    out["MACD_Hist"] = out["MACD"] - out["MACD_Signal"]
    return out


INDICATOR_COLS = ["EMA_9", "EMA_21", "EMA_50", "RSI", "ATR", "ADX",
                  "PLUS_DI", "MINUS_DI", "MACD", "MACD_Signal"]


# ============================================================
#  EVENT HELPERS
# ============================================================
def _cross_events(up_state):
    """Bool series -> (cross_up, cross_down) event series."""
    prev = up_state.shift(1, fill_value=bool(up_state.iloc[0]))
    return up_state & ~prev, (~up_state) & prev


def _last_true_time(ev, n):
    tail = ev.tail(n)
    hits = tail[tail]
    return hits.index[-1] if len(hits) else None


def _event(up_ev, dn_ev, n, weight):
    """Most recent cross within last n bars -> (score, time)."""
    t_up, t_dn = _last_true_time(up_ev, n), _last_true_time(dn_ev, n)
    if t_up is None and t_dn is None:
        return 0, None
    if t_dn is None or (t_up is not None and t_up >= t_dn):
        return weight, t_up
    return -weight, t_dn


# ============================================================
#  SCORING
# ============================================================
def _score_v2(ind, p):
    L = ind.iloc[-1]
    n = p["cross_lookback"]
    comps = {}

    # 1) trend STATE (max +/-30) - one factor, not three
    comps["trend"] = (15 if L["EMA_9"] > L["EMA_21"] else -15) + \
                     (15 if L["Close"] > L["EMA_50"] else -15)

    # 2) fresh EVENTS (max +/-45)
    e_up, e_dn = _cross_events(ind["EMA_9"] > ind["EMA_21"])
    m_up, m_dn = _cross_events(ind["MACD"] > ind["MACD_Signal"])
    ema_s, ema_t = _event(e_up, e_dn, n, 25)
    macd_s, macd_t = _event(m_up, m_dn, n, 20)
    comps["ema_cross"], comps["macd_cross"] = ema_s, macd_s

    # 3) trend STRENGTH via ADX + DI direction (max +/-15)
    di_dir = 1 if L["PLUS_DI"] > L["MINUS_DI"] else -1
    if L["ADX"] >= p["adx_strong"]:
        comps["strength"] = 15 * di_dir
    elif L["ADX"] >= p["adx_min"]:
        comps["strength"] = 8 * di_dir
    else:
        comps["strength"] = 0

    # 4) RSI momentum (max +/-10), NOT mean reversion
    comps["rsi_momentum"] = 10 if L["RSI"] > 55 else (-10 if L["RSI"] < 45 else 0)

    raw = sum(comps.values())

    # 5) exhaustion guard
    comps["rsi_guard"] = 0
    if raw > 0 and L["RSI"] > p["rsi_exhaust_hi"]:
        comps["rsi_guard"] = -25
    elif raw < 0 and L["RSI"] < p["rsi_exhaust_lo"]:
        comps["rsi_guard"] = 25
    score = raw + comps["rsi_guard"]

    times = {1: [t for s, t in ((ema_s, ema_t), (macd_s, macd_t)) if s > 0],
             -1: [t for s, t in ((ema_s, ema_t), (macd_s, macd_t)) if s < 0]}
    return score, comps, times


def _decide_v2(score, comps, times, ind, p):
    L = ind.iloc[-1]
    thr = p["signal_threshold"]
    if score >= thr:
        d = 1
    elif score <= -thr:
        d = -1
    else:
        return "NEUTRAL", "score_below_threshold", None
    if L["ADX"] < p["adx_min"]:
        return "NEUTRAL", "low_adx", None
    if comps["rsi_guard"] != 0:
        return "NEUTRAL", "rsi_exhausted", None
    if not times[d]:
        return "NEUTRAL", "no_fresh_trigger", None
    return ("BUY" if d == 1 else "SELL"), "ok", max(times[d])


def _score_legacy(ind):
    """Original V1 rules (for A/B backtests) on the fixed indicators."""
    L, P = ind.iloc[-1], ind.iloc[-2]
    if any(pd.isna(P[c]) for c in INDICATOR_COLS):
        return None, None
    comps, up = {}, L["EMA_9"] > L["EMA_21"]
    comps["ema"] = (25 if P["EMA_9"] <= P["EMA_21"] else 15) if up else \
                   (-25 if P["EMA_9"] >= P["EMA_21"] else -15)
    r = L["RSI"]
    comps["rsi"] = 20 if r < 30 else -20 if r > 70 else 5 if r > 60 else -5 if r < 40 else 0
    a = L["ADX"]
    comps["adx"] = (20 if up else -20) if a > 25 else (10 if up else -10) if a > 20 else 0
    mu = L["MACD"] > L["MACD_Signal"]
    comps["macd"] = (20 if P["MACD"] <= P["MACD_Signal"] else 10) if mu else \
                    (-20 if P["MACD"] >= P["MACD_Signal"] else -10)
    comps["price_ema50"] = 15 if L["Close"] > L["EMA_50"] else -15
    return sum(comps.values()), comps


# ============================================================
#  MAIN API
# ============================================================
def analyze_signal(df, mode="v2", symbol=None, intraday=False,
                   trim_open_candle=True, check_stale=True,
                   mtf_checker=None, params=None):
    """
    Returns a dict (always the same keys, never raises):
      signal ("BUY"/"SELL"/"NEUTRAL"), confidence, score, reason, error,
      bar_time, trigger_time, signal_id, close, atr, stop_loss, target,
      vol_ratio, components, indicators, mode, version, symbol

    mtf_checker: optional callable(result_dict) -> bool. Return False to veto
                 (e.g. higher-timeframe disagrees). Exceptions also veto.
    trim_open_candle: drop the still-forming last bar (set False if your df
                 already contains only closed bars).
    """
    p = dict(PARAMS)
    if params:
        p.update(params)
    try:
        d, err = _validate(df, p["min_bars"])
        if err:
            return _neutral(err, mode, symbol=symbol)

        if trim_open_candle:
            d = _trim_incomplete(d, intraday)
            if len(d) < p["min_bars"]:
                return _neutral("insufficient_data", mode, symbol=symbol)

        if check_stale and _is_stale(d, intraday, p):
            return _neutral("stale_data", mode, symbol=symbol, bar_time=str(d.index[-1]))

        d = d.tail(p["max_bars"])
        ind = compute_indicators(d)
        if ind.iloc[-2:][INDICATOR_COLS].isna().any().any():
            return _neutral("indicators_not_ready", mode, symbol=symbol,
                            bar_time=str(d.index[-1]))

        if mode == "legacy":
            score, comps = _score_legacy(ind)
            if score is None:
                return _neutral("indicators_not_ready", mode, symbol=symbol)
            sig = "BUY" if score > p["legacy_threshold"] else \
                  "SELL" if score < -p["legacy_threshold"] else "NEUTRAL"
            reason, trig = ("ok" if sig != "NEUTRAL" else "score_below_threshold"), None
        else:
            score, comps, times = _score_v2(ind, p)
            sig, reason, trig = _decide_v2(score, comps, times, ind, p)

        L = ind.iloc[-1]
        bar_time = str(ind.index[-1])
        close, atr = float(L["Close"]), float(L["ATR"])

        vol_ratio = None
        if "Volume" in d.columns and len(d) > 21:
            avg = d["Volume"].iloc[-21:-1].mean()
            if pd.notna(avg) and avg > 0:
                vol_ratio = round(float(d["Volume"].iloc[-1] / avg), 2)
        if sig != "NEUTRAL" and p["min_vol_ratio"] is not None and \
                (vol_ratio is None or vol_ratio < p["min_vol_ratio"]):
            sig, reason = "NEUTRAL", "low_volume"

        stop = target = None
        if sig == "BUY":
            stop, target = close - p["atr_stop_mult"] * atr, close + p["atr_target_mult"] * atr
        elif sig == "SELL":
            stop, target = close + p["atr_stop_mult"] * atr, close - p["atr_target_mult"] * atr

        res = _neutral(reason, mode, symbol=symbol)
        res.update({
            "signal": sig,
            "confidence": round(min(abs(score), 100.0), 1) if sig != "NEUTRAL" else 0.0,
            "score": round(float(score), 1),
            "bar_time": bar_time,
            "trigger_time": str(trig) if trig is not None else None,
            "close": round(close, 2), "atr": round(atr, 2),
            "stop_loss": round(stop, 2) if stop is not None else None,
            "target": round(target, 2) if target is not None else None,
            "vol_ratio": vol_ratio,
            "components": {k: float(v) for k, v in comps.items()},
            "indicators": {k: round(float(L[k]), 3) for k in INDICATOR_COLS},
        })

        if sig != "NEUTRAL" and mtf_checker is not None:
            try:
                ok = bool(mtf_checker(res))
            except Exception as e:
                ok, res["error"] = False, f"mtf_error: {e}"
            if not ok:
                res.update({"signal": "NEUTRAL", "confidence": 0.0, "reason": "mtf_veto",
                            "stop_loss": None, "target": None})

        if res["signal"] != "NEUTRAL":
            res["signal_id"] = f"{symbol or 'NA'}|{res['trigger_time'] or bar_time}|{res['signal']}"
        return res

    except Exception as e:                      # never crash a scan loop
        return _neutral("exception", mode, error=str(e), symbol=symbol)


def ensemble_signals(df, mtf_checker=None, **kwargs):
    """Backward compatible wrapper -> (signal, confidence)."""
    r = analyze_signal(df, mtf_checker=mtf_checker, **kwargs)
    return r["signal"], r["confidence"]


# ============================================================
#  DUPLICATE-SIGNAL SUPPRESSION
# ============================================================
class SignalDeduper:
    """
    Use in the bot so one setup is acted on once.
        dd = SignalDeduper()
        res = analyze_signal(df, symbol="RELIANCE")
        if dd.is_new(res): place_order(...)
    In-memory: persist `seen` (DB/file) if the bot restarts during the day.
    """
    def __init__(self):
        self.seen = {}

    def is_new(self, result, symbol=None):
        if result.get("signal") == "NEUTRAL":
            return False
        key = (symbol or result.get("symbol"), result["signal"])
        stamp = result.get("trigger_time") or result.get("bar_time")
        if self.seen.get(key) == stamp:
            return False
        self.seen[key] = stamp
        return True


# ============================================================
#  SELF TEST   (python signals.py)
# ============================================================
def _make_df(n=400, kind="turn_up", seed=1):
    rng = np.random.default_rng(seed)
    end = pd.offsets.BDay().rollback(pd.Timestamp.now().normalize() - pd.Timedelta(days=1))
    idx = pd.bdate_range(end=end, periods=n)
    if kind == "flat":
        c = np.full(n, 100.0)
    elif kind == "turn_up":
        c = np.concatenate([100 - np.arange(n - 8) * 0.25, 100 - (n - 9) * 0.25 + np.arange(1, 9) * 2.0])
        c = c + rng.normal(0, 0.2, n)
    else:
        c = 100 + np.cumsum(rng.normal(0, 1, n))
    c = np.abs(c) + 5
    return pd.DataFrame({"Open": c, "High": c * 1.01, "Low": c * 0.99,
                         "Close": c, "Volume": rng.integers(1e5, 2e5, n)}, index=idx)


def _selftest():
    keys = set(_neutral("x").keys())
    ok = True

    def check(name, cond):
        nonlocal ok
        ok &= bool(cond)
        print(("PASS " if cond else "FAIL ") + name)

    df = _make_df(kind="turn_up")
    r = analyze_signal(df, symbol="TEST")
    print("  turn_up ->", r["signal"], r["reason"], "score", r["score"], "trigger", r["trigger_time"])
    check("result shape", set(r.keys()) == keys)
    check("legacy mode runs", set(analyze_signal(df, mode="legacy").keys()) == keys)
    check("input not mutated", list(df.columns) == ["Open", "High", "Low", "Close", "Volume"])
    check("lowercase columns ok", analyze_signal(df.rename(columns=str.lower))["reason"] != "missing_columns:Open")
    check("newest-first index handled", analyze_signal(df.iloc[::-1])["bar_time"] == r["bar_time"])
    check("too short", analyze_signal(df.tail(50))["reason"] == "insufficient_data")
    check("missing columns", analyze_signal(df[["Close"]])["reason"].startswith("missing_columns"))
    check("None input", analyze_signal(None)["reason"] == "no_data")
    check("flat price no crash", analyze_signal(_make_df(kind="flat"))["signal"] == "NEUTRAL")
    nan_df = df.copy()
    nan_df.iloc[-1, nan_df.columns.get_loc("Close")] = np.nan
    check("NaN last row no crash", analyze_signal(nan_df)["error"] is None)
    old = df.copy()
    old.index = old.index - pd.Timedelta(days=60)
    check("stale detected", analyze_signal(old)["reason"] == "stale_data")
    check("tuple wrapper", len(ensemble_signals(df)) == 2)

    veto = analyze_signal(df, mtf_checker=lambda res: False)
    check("mtf veto only affects signals", veto["signal"] == "NEUTRAL")

    fake = _neutral("ok")
    fake.update({"signal": "BUY", "trigger_time": "t1", "symbol": "A"})
    dd = SignalDeduper()
    check("dedupe first True", dd.is_new(fake))
    check("dedupe repeat False", not dd.is_new(fake))
    fake["trigger_time"] = "t2"
    check("dedupe new trigger True", dd.is_new(fake))
    print("\nALL PASSED" if ok else "\nSOME TESTS FAILED")


if __name__ == "__main__":
    _selftest()