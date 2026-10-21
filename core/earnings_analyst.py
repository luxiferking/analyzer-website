"""
core/earnings_analyst.py
Earnings filter — blocks trades around earnings dates.

WHY THIS MATTERS:
- IV crush: options premium 40-60% crash after earnings
- Gap risk: stock can gap 3-5% either direction
- Unpredictable: good result can still trigger sell-off
- Historical volatility: RELIANCE shows -32% to +38% surprises

Cache: 1-hour TTL per ticker (yfinance calls are slow)
"""
import os
from datetime import datetime, date, timedelta

try:
    import yfinance as yf
    import pandas as pd
    _YF_OK = True
except Exception:
    _YF_OK = False


# ---- In-memory cache (per session) ----
_cache = {}
_CACHE_TTL = 3600  # 1 hour


def _cache_get(key):
    if key in _cache:
        val, ts = _cache[key]
        if (datetime.now() - ts).total_seconds() < _CACHE_TTL:
            return val
    return None


def _cache_set(key, value):
    _cache[key] = (value, datetime.now())


def _to_yf_ticker(symbol: str) -> str:
    """RELIANCE → RELIANCE.NS"""
    s = (symbol or "").upper().strip()
    if s.endswith(".NS") or s.endswith(".BO"):
        return s
    return f"{s}.NS"


# ============================================================
#  NEXT EARNINGS DATE
# ============================================================
def get_next_earnings_date(ticker: str):
    """
    Returns next earnings date as `date` object, or None.
    Picks the earliest future date if multiple are returned.
    """
    if not _YF_OK:
        return None

    key = f"next_earn_{ticker.upper()}"
    cached = _cache_get(key)
    if cached is not None:
        return cached

    result = None
    try:
        t = yf.Ticker(_to_yf_ticker(ticker))
        cal = t.calendar or {}
        earn_dates = cal.get("Earnings Date") or []

        today = date.today()

        # Normalize to list
        if isinstance(earn_dates, date) and not isinstance(earn_dates, datetime):
            dates_list = [earn_dates]
        elif isinstance(earn_dates, list):
            dates_list = [d for d in earn_dates if isinstance(d, (date, datetime))]
        else:
            dates_list = []

        # Convert datetime → date
        clean_dates = []
        for d in dates_list:
            if isinstance(d, datetime):
                clean_dates.append(d.date())
            else:
                clean_dates.append(d)

        # Prefer future dates
        future = [d for d in clean_dates if d >= today]
        if future:
            result = min(future)
        elif clean_dates:
            result = max(clean_dates)  # last known
    except Exception:
        pass

    _cache_set(key, result)
    return result


def days_to_earnings(ticker: str):
    """Days until next earnings. Returns int or None."""
    d = get_next_earnings_date(ticker)
    if d is None:
        return None
    try:
        return (d - date.today()).days
    except Exception:
        return None


# ============================================================
#  BLACKOUT WINDOW
# ============================================================
def is_earnings_blackout(ticker: str, days_before: int = 3, days_after: int = 1):
    """
    Returns (is_blackout: bool, reason: str).

    Rule:
    - 0 to -days_after days past earnings → blackout (post-earnings vol)
    - 1 to days_before days ahead → blackout (pre-earnings)
    - Otherwise safe
    """
    d = get_next_earnings_date(ticker)
    if d is None:
        return False, "Earnings date unknown"

    try:
        days = (d - date.today()).days
    except Exception:
        return False, "Date parsing error"

    if days < 0:
        if abs(days) <= days_after:
            return True, f"Earnings {abs(days)} day(s) ago — post-earnings volatility"
        return False, "No upcoming earnings"

    if days == 0:
        return True, "🚨 EARNINGS TODAY — extreme volatility, avoid new positions"
    if days == 1:
        return True, "🚨 EARNINGS TOMORROW — IV crush imminent, avoid options"
    if days <= days_before:
        return True, f"⚠️ Earnings in {days} days — blackout window active"

    return False, f"Safe — earnings in {days} days"


# ============================================================
#  EARNINGS HISTORY
# ============================================================
def get_earnings_history(ticker: str, n: int = 4):
    """
    Return last N completed quarters' surprise %.
    Returns list of dicts: [{date, eps_estimate, eps_reported, surprise_pct}, ...]
    """
    if not _YF_OK:
        return []

    key = f"earn_hist_{ticker.upper()}_{n}"
    cached = _cache_get(key)
    if cached is not None:
        return cached

    result = []
    try:
        t = yf.Ticker(_to_yf_ticker(ticker))
        df = t.earnings_dates
        if df is not None and not df.empty:
            # Keep only rows with reported EPS (past earnings)
            completed = df[df["Reported EPS"].notna()].head(n)
            for idx, row in completed.iterrows():
                try:
                    d = idx.date() if hasattr(idx, "date") else idx
                    result.append({
                        "date": d,
                        "eps_estimate": float(row["EPS Estimate"]) if pd.notna(row["EPS Estimate"]) else None,
                        "eps_reported": float(row["Reported EPS"]) if pd.notna(row["Reported EPS"]) else None,
                        "surprise_pct": float(row["Surprise(%)"]) if pd.notna(row["Surprise(%)"]) else None,
                    })
                except Exception:
                    continue
    except Exception:
        pass

    _cache_set(key, result)
    return result


# ============================================================
#  VOLATILITY SCORE (0-10)
# ============================================================
def get_earnings_impact_score(ticker: str) -> float:
    """
    0-10 score based on absolute earnings surprise history.
    Higher = more dangerous.
    Mapping: 0% avg → 0, 20%+ avg → 10
    """
    hist = get_earnings_history(ticker, n=8)
    if not hist:
        return 5.0

    surprises = [abs(h["surprise_pct"]) for h in hist if h["surprise_pct"] is not None]
    if not surprises:
        return 5.0

    avg_abs = sum(surprises) / len(surprises)
    return round(min(10.0, avg_abs / 2.0), 1)


# ============================================================
#  FORMATTER
# ============================================================
def format_earnings_warning(ticker: str) -> str:
    """Multi-line display string for Jarvis output."""
    d = get_next_earnings_date(ticker)
    if d is None:
        return ""

    days = (d - date.today()).days
    blackout, reason = is_earnings_blackout(ticker)
    impact = get_earnings_impact_score(ticker)

    lines = [
        f"📅 EARNINGS: {d.strftime('%b %d, %Y')} ({days} days away)",
        f"   Status: {reason}",
        f"   Historical Volatility: {impact}/10",
    ]

    hist = get_earnings_history(ticker, n=4)
    if hist:
        lines.append("   Last 4 quarters (surprise %):")
        for h in hist:
            s = f"{h['surprise_pct']:+.1f}%" if h['surprise_pct'] is not None else "N/A"
            lines.append(f"     • {h['date']}: {s}")

    return "\n".join(lines)


# ============================================================
#  BATCH PRELOAD (for screener performance)
# ============================================================
def preload_earnings(tickers):
    """Preload earnings cache for a list of tickers (best-effort)."""
    for t in tickers:
        try:
            get_next_earnings_date(t)
            get_earnings_history(t, n=4)
        except Exception:
            continue