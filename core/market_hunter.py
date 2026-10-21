import os
import gzip
import json
import requests
import pandas as pd
from datetime import datetime
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")


NIFTY_100 = [
    "RELIANCE", "TCS", "HDFCBANK", "ICICIBANK", "INFY", "HINDUNILVR", "ITC",
    "SBIN", "BHARTIARTL", "KOTAKBANK", "LT", "AXISBANK", "BAJFINANCE",
    "ASIANPAINT", "MARUTI", "SUNPHARMA", "TITAN", "ULTRACEMCO", "WIPRO",
    "NESTLEIND", "TATASTEEL", "POWERGRID", "NTPC", "M&M", "HCLTECH", "TECHM",
    "ADANIENT", "ADANIPORTS", "COALINDIA", "ONGC", "BPCL", "IOC", "GRASIM",
    "JSWSTEEL", "INDUSINDBK", "DRREDDY", "CIPLA", "DIVISLAB", "APOLLOHOSP",
    "BRITANNIA", "EICHERMOT", "HEROMOTOCO", "BAJAJ-AUTO", "BAJAJFINSV",
    "SBILIFE", "HDFCLIFE", "TATACONSUM", "SIEMENS", "DLF", "GODREJCP",
    "PIDILITIND", "HAVELLS", "DABUR", "MARICO", "TATAPOWER", "JINDALSTEL",
    "VEDL", "HINDALCO", "AMBUJACEM", "SHREECEM", "GAIL", "BANKBARODA",
    "PNB", "CANBK", "IDFCFIRSTB", "FEDERALBNK", "BANDHANBNK", "AUBANK",
    "MUTHOOTFIN", "CHOLAFIN", "MOTHERSON", "TVSMOTOR", "BALKRISIND",
    "COLPAL", "UBL", "VBL", "TORNTPHARM", "LUPIN", "ALKEM", "BIOCON",
    "AUROPHARMA", "MPHASIS", "PERSISTENT", "COFORGE", "TATAELXSI",
    "IRCTC", "INDIGO", "BEL", "HAL", "ABB", "SRF", "BOSCHLTD",
]


INSTRUMENT_FILE = "data/upstox_instruments.json"
INSTRUMENT_URL = "https://assets.upstox.com/market-quote/instruments/exchange/complete.json.gz"


def load_instrument_map():
    os.makedirs("data", exist_ok=True)
    if not os.path.exists(INSTRUMENT_FILE):
        print("  Downloading Upstox instrument list...")
        try:
            r = requests.get(INSTRUMENT_URL, timeout=120)
            r.raise_for_status()
            instruments = json.loads(gzip.decompress(r.content).decode('utf-8'))
            symbol_map = {}
            for inst in instruments:
                if inst.get("segment") == "NSE_EQ" and inst.get("instrument_type") == "EQ":
                    sym = inst.get("trading_symbol", "").upper()
                    key = inst.get("instrument_key", "")
                    if sym and key:
                        symbol_map[sym] = key
            with open(INSTRUMENT_FILE, "w") as f:
                json.dump(symbol_map, f)
            return symbol_map
        except Exception as e:
            print(f"  ERROR: {e}")
            return {}
    with open(INSTRUMENT_FILE, "r") as f:
        return json.load(f)


# ==========================================
# KEY FIX: Response keys come as "NSE_EQ:SYMBOL"
# ==========================================
def _extract_symbol_from_response_key(key):
    """Upstox returns keys as 'NSE_EQ:RELIANCE' or 'NSE_INDEX:Nifty 50'."""
    if ":" in key:
        return key.split(":")[-1].strip().upper()
    return key.upper()


def fetch_live_quotes(symbols, instrument_map):
    """Fetch live quotes. Works even on holidays (returns last close)."""
    access_token = os.getenv("UPSTOX_ANALYTICS_TOKEN")
    if not access_token:
        return {}, "No token"

    keys = []
    for sym in symbols:
        key = instrument_map.get(sym)
        if key:
            keys.append(key)

    if not keys:
        return {}, "No valid keys"

    symbols_set = set(symbols)
    url = "https://api.upstox.com/v3/market-quote/quotes"
    headers = {"Accept": "application/json", "Authorization": f"Bearer {access_token}"}

    all_quotes = {}
    chunk_size = 450
    for i in range(0, len(keys), chunk_size):
        chunk = keys[i:i+chunk_size]
        params = {"instrument_key": ",".join(chunk)}
        try:
            r = requests.get(url, headers=headers, params=params, timeout=30)
            print(f"    Live batch {i//chunk_size+1}: HTTP {r.status_code}")
            if r.status_code != 200:
                continue
            data = r.json()
            quotes_data = data.get("data", {})
            print(f"    Returned {len(quotes_data)} objects")

            for resp_key, q in quotes_data.items():
                # Extract symbol from "NSE_EQ:RELIANCE" format
                sym = _extract_symbol_from_response_key(resp_key)
                if sym not in symbols_set:
                    continue
                ohlc = q.get("ohlc", {}) or {}
                all_quotes[sym] = {
                    "symbol": sym,
                    "open": float(ohlc.get("open", 0) or 0),
                    "high": float(ohlc.get("high", 0) or 0),
                    "low": float(ohlc.get("low", 0) or 0),
                    "price": float(ohlc.get("close", 0) or 0),
                    "volume": int(q.get("volume", 0) or ohlc.get("volume", 0) or 0),
                }
        except Exception as e:
            print(f"    Batch error: {str(e)[:80]}")

    return all_quotes, None


def get_nifty_change():
    access_token = os.getenv("UPSTOX_ANALYTICS_TOKEN")
    url = "https://api.upstox.com/v3/market-quote/quotes"
    headers = {"Accept": "application/json", "Authorization": f"Bearer {access_token}"}
    params = {"instrument_key": "NSE_INDEX|Nifty 50"}
    try:
        r = requests.get(url, headers=headers, params=params, timeout=15)
        data = r.json()
        if data.get("status") != "success":
            return 0.0
        for key, q in data.get("data", {}).items():
            ohlc = q.get("ohlc", {})
            o = float(ohlc.get("open", 0) or 0)
            c = float(ohlc.get("close", 0) or 0)
            if o > 0:
                return ((c - o) / o) * 100
    except Exception:
        pass
    return 0.0


def get_avg_volume_cache(symbols, instrument_map, cache_file="data/avg_volumes.json"):
    today_str = datetime.now(IST).strftime("%Y-%m-%d")
    os.makedirs("data", exist_ok=True)

    if os.path.exists(cache_file):
        try:
            with open(cache_file, "r") as f:
                cache = json.load(f)
            if cache.get("date") == today_str and len(cache.get("volumes", {})) > 50:
                print(f"  Using cached volumes ({len(cache.get('volumes', {}))} symbols)")
                return cache.get("volumes", {})
        except Exception:
            pass

    print("  Fetching fresh 20-day avg volumes...")
    access_token = os.getenv("UPSTOX_ANALYTICS_TOKEN")
    volumes = {}
    from_date = (pd.Timestamp.now() - pd.Timedelta(days=40)).strftime('%Y-%m-%d')
    today = pd.Timestamp.now().strftime('%Y-%m-%d')

    for i, sym in enumerate(symbols):
        key = instrument_map.get(sym)
        if not key:
            continue
        url = f"https://api.upstox.com/v3/historical-candle/{key}/days/1/{today}/{from_date}"
        try:
            r = requests.get(url, headers={"Accept": "application/json",
                                           "Authorization": f"Bearer {access_token}"}, timeout=10)
            if r.status_code != 200:
                continue
            data = r.json()
            candles = data.get("data", {}).get("candles", [])
            if len(candles) < 5:
                continue
            vols = [c[5] for c in candles[-20:]]
            volumes[sym] = int(sum(vols) / len(vols))
        except Exception:
            continue

    with open(cache_file, "w") as f:
        json.dump({"date": today_str, "volumes": volumes}, f)

    print(f"  Fetched avg volume for {len(volumes)} symbols")
    return volumes


def hunt(quotes, avg_volumes, nifty_change, min_price=50, min_volume=100000):
    candidates = []
    for sym, q in quotes.items():
        if q['price'] < min_price or q['open'] <= 0:
            continue
        if q['volume'] < min_volume:
            continue
        change_pct = ((q['price'] - q['open']) / q['open']) * 100
        if abs(change_pct) < 1.0:
            continue
        avg_vol = avg_volumes.get(sym, 0)
        if avg_vol <= 0:
            continue
        vol_ratio = q['volume'] / avg_vol
        rs = change_pct - nifty_change
        day_range_pct = ((q['high'] - q['low']) / q['open']) * 100 if q['open'] > 0 else 0

        score = 0
        if vol_ratio >= 3.0: score += 35
        elif vol_ratio >= 2.0: score += 25
        elif vol_ratio >= 1.5: score += 15
        elif vol_ratio >= 1.2: score += 8

        ac = abs(change_pct)
        if ac >= 4.0: score += 30
        elif ac >= 3.0: score += 24
        elif ac >= 2.0: score += 18
        elif ac >= 1.5: score += 12
        elif ac >= 1.0: score += 6

        ars = abs(rs)
        if ars >= 3.0: score += 25
        elif ars >= 2.0: score += 18
        elif ars >= 1.0: score += 10
        elif ars >= 0.5: score += 5

        if day_range_pct >= 3.0: score += 10
        elif day_range_pct >= 2.0: score += 6
        elif day_range_pct >= 1.5: score += 3

        candidates.append({
            "symbol": sym,
            "open": q['open'],
            "price": q['price'],
            "high": q['high'],
            "low": q['low'],
            "volume": q['volume'],
            "change_pct": round(change_pct, 2),
            "range_pct": round(day_range_pct, 2),
            "vol_ratio": round(vol_ratio, 2),
            "relative_strength": round(rs, 2),
            "score": score,
        })

    candidates.sort(key=lambda x: x['score'], reverse=True)
    return candidates


def generate_trade_plan(c, capital=100000, risk_pct=0.01):
    price = c['price']
    range_size = c['high'] - c['low']
    if range_size <= 0:
        return None
    direction = "BUY" if c['change_pct'] > 0 else "SELL"

    if direction == "BUY":
        entry = price
        stop_loss = entry - (0.6 * range_size)
        risk = entry - stop_loss
        target = entry + (2 * risk)
    else:
        entry = price
        stop_loss = entry + (0.6 * range_size)
        risk = stop_loss - entry
        target = entry - (2 * risk)

    if risk <= 0:
        return None
    size = int((capital * risk_pct) / risk)
    if size < 1:
        return None

    return {
        "direction": direction,
        "entry": round(entry, 2),
        "stop_loss": round(stop_loss, 2),
        "target": round(target, 2),
        "position_size": size,
        "risk_reward": "1:2",
        "potential_profit": round(size * risk * 2, 0),
        "potential_loss": round(size * risk, 0),
    }


def print_top_picks(candidates, top_n=5, capital=100000):
    print("\n" + "=" * 95)
    print("  JARVIS MARKET HUNTER — TOP PICKS")
    print("=" * 95)
    if not candidates:
        print("  No explosive setups detected.")
        print("  Market may be quiet, closed, or no stock met criteria.")
        print("=" * 95)
        return

    print(f"\n{'#':<4} {'Symbol':<12} {'Price':>10} {'Chg%':>8} {'Vol x':>8} {'RS':>8} {'Score':>7}")
    print("-" * 95)
    for i, c in enumerate(candidates[:top_n], 1):
        print(f"{i:<4} {c['symbol']:<12} "
              f"Rs.{c['price']:>8.2f} "
              f"{c['change_pct']:>+7.2f}% "
              f"{c['vol_ratio']:>7.2f}x "
              f"{c['relative_strength']:>+7.2f} "
              f"{c['score']:>6}")
    print("=" * 95)

    print(f"\n  DETAILED TRADE PLANS (Capital: Rs.{capital:,} | Risk: 1%)\n")
    for i, c in enumerate(candidates[:3], 1):
        plan = generate_trade_plan(c, capital)
        if not plan:
            continue
        print(f"\n  #{i} — {c['symbol']}  (Score: {c['score']}/100)")
        print(f"  ────────────────────────────────────────────────────────")
        print(f"  Direction:     {plan['direction']}")
        print(f"  Entry:         Rs. {plan['entry']}")
        print(f"  Stop Loss:     Rs. {plan['stop_loss']}")
        print(f"  Target:        Rs. {plan['target']}")
        print(f"  Position Size: {plan['position_size']} shares")
        print(f"  Max Profit:    Rs. {plan['potential_profit']:,.0f}")
        print(f"  Max Loss:      Rs. {plan['potential_loss']:,.0f}")
        print(f"  Volume:        {c['vol_ratio']:.2f}x 20-day avg")
        print(f"  vs Nifty:      {c['relative_strength']:+.2f}%")