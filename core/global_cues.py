"""
core/global_cues.py
Global markets (Dow, Nasdaq, USD/INR, Crude, Gold, VIX) — yfinance-free version.

Fixes:
✅ Direct Yahoo Finance API (no yfinance overhead)
✅ 5-minute cache (avoid rate limits)
✅ Per-ticker timeout (no hang)
✅ Graceful fallback (NEUTRAL if all fail)
✅ Reduced fetch list (only critical indicators)
"""
import os
import time
import requests

# ─── Cache ───
_CACHE = {}
_CACHE_TTL = 300  # 5 minutes


# ─── Only critical tickers ───
GLOBAL_TICKERS = {
    "Dow Jones": "^DJI",
    "Nasdaq":    "^IXIC",
    "USD/INR":   "USDINR=X",
    "Crude Oil": "CL=F",
    "Gold":      "GC=F",
    "VIX":       "^VIX",
}


_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
}


def _get_cached(key):
    entry = _CACHE.get(key)
    if entry and (time.time() - entry["ts"]) < _CACHE_TTL:
        return entry["data"]
    return None


def _set_cached(key, data):
    _CACHE[key] = {"ts": time.time(), "data": data}


def _fetch_yahoo(ticker: str):
    """Direct Yahoo Finance API call with timeout."""
    url = (
        f"https://query1.finance.yahoo.com/v8/finance/chart/"
        f"{ticker}?interval=1d&range=5d"
    )
    try:
        r = requests.get(url, headers=_HEADERS, timeout=8)
        if r.status_code != 200:
            return None
        data = r.json()

        result = data.get("chart", {}).get("result")
        if not result or not result[0]:
            return None

        closes = result[0].get("indicators", {}).get("quote", [{}])[0].get("close", [])
        # Filter out None values
        closes = [c for c in closes if c is not None]
        if len(closes) < 2:
            return None

        last = float(closes[-1])
        prev = float(closes[-2])
        change_pct = ((last - prev) / prev) * 100

        return {
            "price": round(last, 2),
            "change": round(change_pct, 2),
        }
    except Exception:
        return None


def fetch_global_cues():
    """Fetch all global indicators with caching + graceful failures."""
    cache_key = "global_cues"
    cached = _get_cached(cache_key)
    if cached:
        return cached

    results = {}
    success_count = 0

    for name, ticker in GLOBAL_TICKERS.items():
        data = _fetch_yahoo(ticker)
        if data:
            results[name] = data
            success_count += 1
        else:
            results[name] = {"price": None, "change": None}

    # Cache even partial results (better than repeated failing fetches)
    if success_count > 0:
        _set_cached(cache_key, results)

    return results


def get_market_bias(global_data):
    """
    Global data se overall bias nikaalo.
    Gracefully returns NEUTRAL if data unavailable.
    """
    if not global_data:
        return "NEUTRAL", 0

    # Check if we have any data at all
    has_data = any(
        global_data.get(n, {}).get("change") is not None
        for n in ["Dow Jones", "Nasdaq", "VIX", "USD/INR"]
    )
    if not has_data:
        return "NEUTRAL", 0

    score = 0

    # US markets (weight: 2 each)
    for name in ["Dow Jones", "Nasdaq"]:
        change = global_data.get(name, {}).get("change")
        if change is not None:
            if change > 0.5:
                score += 2
            elif change > 0:
                score += 1
            elif change < -0.5:
                score -= 2
            elif change < 0:
                score -= 1

    # USD/INR (weak rupee = negative)
    usdinr = global_data.get("USD/INR", {}).get("change")
    if usdinr is not None:
        if usdinr > 0.3:
            score -= 1
        elif usdinr < -0.3:
            score += 1

    # Crude (higher = negative for India)
    crude = global_data.get("Crude Oil", {}).get("change")
    if crude is not None:
        if crude > 2:
            score -= 1
        elif crude < -2:
            score += 1

    # VIX (fear index)
    vix_price = global_data.get("VIX", {}).get("price")
    if vix_price is not None:
        if vix_price > 20:
            score -= 2
        elif vix_price < 13:
            score += 1

    if score >= 3:
        return "BULLISH", score
    elif score <= -3:
        return "BEARISH", score
    else:
        return "NEUTRAL", score


# ─── Standalone test ───
if __name__ == "__main__":
    print("\n" + "=" * 60)
    print("  Testing Global Cues Fetch")
    print("=" * 60 + "\n")

    data = fetch_global_cues()
    for name, values in data.items():
        price = values.get("price")
        change = values.get("change")
        if price is not None and change is not None:
            arrow = "🟢" if change > 0 else "🔴" if change < 0 else "⚪"
            print(f"  {arrow} {name:<12}  {price:>10.2f}  ({change:+.2f}%)")
        else:
            print(f"  ⚠️  {name:<12}  (unavailable)")

    bias, score = get_market_bias(data)
    print(f"\n  📊 Overall Bias: {bias}  (Score: {score:+d})")
    print("=" * 60 + "\n")