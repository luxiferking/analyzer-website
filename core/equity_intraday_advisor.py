"""
core/equity_intraday_advisor.py
Equity Intraday Advisor V5.1 (Volume Breakout Logic Added)
"""
import os
import time
import json
import gzip
import io
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from urllib.parse import quote
from zoneinfo import ZoneInfo

import pandas as pd
import requests
from dotenv import load_dotenv

load_dotenv()

BASE = "https://api.upstox.com"
IST = ZoneInfo("Asia/Kolkata")


def _envf(name, default):
    return float(os.getenv(name, default))


# ─── Config (all overridable from .env) ───
TRADING_CAPITAL = _envf("TRADING_CAPITAL", "25000")
MAX_RISK_PCT = _envf("EQ_MAX_RISK_PCT", "0.02")
MAX_RISK_PER_TRADE = _envf("EQ_MAX_RISK_PER_TRADE", os.getenv("MAX_RISK_PER_TRADE", "500"))
MAX_POSITION_PCT = _envf("EQ_MAX_POSITION_PCT", "1.0")   
MAX_STOCK_PRICE = _envf("EQ_MAX_STOCK_PRICE", "6000") 

BUY_THRESHOLD = 25
SELL_THRESHOLD = -25

MIN_SL_PCT = _envf("EQ_MIN_SL_PCT", "0.006")
MAX_SL_PCT = _envf("EQ_MAX_SL_PCT", "0.020")
TARGET_R = _envf("EQ_TARGET_R", "2.5")
MIN_NET_PROFIT = _envf("EQ_MIN_NET_PROFIT", "80")
MIN_RR_NET = _envf("EQ_MIN_RR_NET", "1.2")

MIN_REL_VOL = _envf("EQ_MIN_REL_VOL", "0.5")
RSI_BLOCK_BUY = int(_envf("EQ_RSI_BLOCK_BUY", "75"))
RSI_BLOCK_SELL = int(_envf("EQ_RSI_BLOCK_SELL", "25"))
REGIME_BLOCK = int(_envf("EQ_REGIME_BLOCK", "30"))

REQUIRE_REGIME = os.getenv("EQ_REQUIRE_REGIME", "0") == "1" 

MAX_GAP_PCT = _envf("EQ_MAX_GAP_PCT", "0.03")
MAX_PER_SECTOR = int(_envf("EQ_MAX_PER_SECTOR", "2"))
MAX_WORKERS = int(_envf("EQ_MAX_WORKERS", "8"))

MIN_TURNOVER_CR = _envf("EQ_MIN_TURNOVER_CR", "2.0") 

ALLOW_OFFHOURS = os.getenv("EQ_ALLOW_OFFHOURS", os.getenv("ALLOW_OFFHOURS", "0")) == "1"
FETCH_DAYS_5M = int(_envf("EQ_FETCH_DAYS_5M", "14"))

BROKERAGE_FLAT = _envf("EQ_BROKERAGE_FLAT", "20")
BROKERAGE_PCT = _envf("EQ_BROKERAGE_PCT", "0.001")
SLIPPAGE_PCT = _envf("EQ_SLIPPAGE_PCT", "0.0005")

MARKET_OPEN = (9, 15)
SAFE_START = (9, 30)
SAFE_END = (14, 30) # 2:30 PM (Late entries avoid karne ke liye)
MARKET_CLOSE = (15, 30)
MAX_HOLD_MINUTES = 45
NIFTY_KEY = "NSE_INDEX|Nifty 50"

# ═══════════════════════════════════════════════════════
#  STOCK UNIVERSE (Dynamic F&O Downloader)
# ═══════════════════════════════════════════════════════
INSTRUMENTS_URL = "https://assets.upstox.com/market-quote/instruments/exchange/NSE.json.gz"
INSTRUMENTS_CACHE = "data/NSE_instruments.json"

def load_stock_universe():
    if not os.path.exists("data"):
        os.makedirs("data")

    if not os.path.exists(INSTRUMENTS_CACHE):
        print("📥 Fetching complete instrument list from Upstox (First run only)...")
        try:
            r = requests.get(INSTRUMENTS_URL, timeout=30)
            if r.status_code == 200:
                with gzip.GzipFile(fileobj=io.BytesIO(r.content)) as f:
                    data = f.read()
                with open(INSTRUMENTS_CACHE, 'wb') as f:
                    f.write(data)
            else:
                print("❌ Failed to download instruments.")
                return {}
        except Exception as e:
            print(f"❌ Error downloading instruments: {e}")
            return {}

    with open(INSTRUMENTS_CACHE, 'r', encoding='utf-8') as f:
        instruments = json.load(f)

    fo_underlyings = set()
    for inst in instruments:
        if inst.get("segment") == "NSE_FO" and inst.get("instrument_type") == "FUT":
            fo_underlyings.add(inst.get("underlying_symbol"))

    universe = {}
    for inst in instruments:
        if inst.get("segment") == "NSE_EQ" and inst.get("instrument_type") == "EQ":
            sym = inst.get("trading_symbol")
            key = inst.get("instrument_key")
            if sym in fo_underlyings and sym and key:
                universe[sym] = key
                
    print(f"✅ Loaded {len(universe)} F&O stocks for intraday scanning.")
    return universe

STOCK_UNIVERSE = load_stock_universe()
if not STOCK_UNIVERSE:
    STOCK_UNIVERSE = {"SBIN": "NSE_EQ|INE062A01020", "ITC": "NSE_EQ|INE154A01025"}

SECTORS = {
    "SBIN": "BANK", "ICICIBANK": "BANK", "AXISBANK": "BANK", "PNB": "BANK",
    "BANKBARODA": "BANK", "CANBK": "BANK", "IDFCFIRSTB": "BANK", "FEDERALBNK": "BANK",
    "TATASTEEL": "METALS", "JSWSTEEL": "METALS", "HINDALCO": "METALS", "COALINDIA": "METALS",
    "ONGC": "OIL_GAS", "IOC": "OIL_GAS", "BPCL": "OIL_GAS", "GAIL": "OIL_GAS",
    "NTPC": "POWER", "TATAPOWER": "POWER", "ADANIPOWER": "POWER", "IEX": "POWER",
    "BEL": "DEFENCE", "IRFC": "RAIL", "RVNL": "RAIL", "ITC": "FMCG",
}

# ═══════════════════════════════════════════════════════
#  HELPERS & NEW BREAKOUT LOGIC
# ═══════════════════════════════════════════════════════
def _now_ist():
    return pd.Timestamp.now(tz="Asia/Kolkata")

def _headers():
    token = os.getenv("UPSTOX_ANALYTICS_TOKEN")
    return {"Accept": "application/json", "Authorization": f"Bearer {token}"}

def _enc(instrument_key):
    return quote(instrument_key, safe="")

def _get(url, timeout=15, retries=3):
    for attempt in range(retries):
        try:
            r = requests.get(url, headers=_headers(), timeout=timeout)
            if r.status_code == 429:
                time.sleep(1.5 * (attempt + 1))
                continue
            return r
        except requests.RequestException as e:
            if attempt == retries - 1:
                return None
            time.sleep(1.0)
    return None

def _is_trading_time():
    now = datetime.now(IST)
    t = (now.hour, now.minute)
    w = now.weekday()
    stamp = now.strftime("%I:%M %p")

    if w >= 5: return False, "Weekend — market closed"
    if t < MARKET_OPEN: return False, f"Pre-market (now {stamp})"
    if t < SAFE_START: return False, f"Opening volatility (now {stamp})"
    if SAFE_END < t <= MARKET_CLOSE: return False, f"Closing time (now {stamp})"
    if t > MARKET_CLOSE: return False, f"Market closed (now {stamp})"
    return True, f"Safe trading window (now {stamp})"

def _minutes_to_close():
    now = datetime.now(IST)
    close = now.replace(hour=MARKET_CLOSE[0], minute=MARKET_CLOSE[1], second=0, microsecond=0)
    return (close - now).total_seconds() / 60

def _rsi(close, period=14):
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / period, adjust=False).mean()
    down = (-delta.clip(upper=0)).ewm(alpha=1 / period, adjust=False).mean()
    rs = gain / down.replace(0, 1e-9)
    return float((100 - (100 / (1 + rs))).iloc[-1])

def _atr(df, period=14):
    high, low, close = df["High"], df["Low"], df["Close"]
    tr = pd.concat([high - low, (high - close.shift()).abs(), (low - close.shift()).abs()], axis=1).max(axis=1)
    val = tr.rolling(period).mean().iloc[-1]
    if pd.isna(val) or val <= 0: raise ValueError("ATR unavailable")
    return float(val)

def _get_avg_daily_turnover(df5, days=5):
    df = df5.copy()
    df["Turnover"] = df["Close"] * df["Volume"]
    daily_turnover = df.groupby(df.index.date)["Turnover"].sum()
    if len(daily_turnover) > 1:
        last_date = df5.index[-1].date()
        full_days = daily_turnover[daily_turnover.index < last_date].tail(days)
        if len(full_days) > 0: return float(full_days.mean() / 1e7)
    return 0.0

def _parse_candles(resp):
    if resp is None or resp.status_code != 200: return None
    try:
        candles = resp.json().get("data", {}).get("candles", [])
        if not candles: return None
        rows = [(list(c) + [0] * 7)[:7] for c in candles]
        df = pd.DataFrame(rows, columns=["timestamp", "Open", "High", "Low", "Close", "Volume", "OI"])
        df["timestamp"] = pd.to_datetime(df["timestamp"])
        if df["timestamp"].dt.tz is None:
            df["timestamp"] = df["timestamp"].dt.tz_localize("Asia/Kolkata")
        else:
            df["timestamp"] = df["timestamp"].dt.tz_convert("Asia/Kolkata")
        df.set_index("timestamp", inplace=True)
        df.sort_index(inplace=True)
        return df
    except Exception:
        return None

def _fetch_candles(instrument_key, interval=5, days=FETCH_DAYS_5M):
    now = _now_ist()
    to_date = now.strftime("%Y-%m-%d")
    from_date = (now - pd.Timedelta(days=days)).strftime("%Y-%m-%d")
    key = _enc(instrument_key)

    frames = []
    hist = _parse_candles(_get(f"{BASE}/v3/historical-candle/{key}/minutes/{interval}/{to_date}/{from_date}"))
    if hist is not None: frames.append(hist)
    intra = _parse_candles(_get(f"{BASE}/v3/historical-candle/intraday/{key}/minutes/{interval}"))
    if intra is not None: frames.append(intra)
    if not frames: return None

    df = pd.concat(frames)
    df = df[~df.index.duplicated(keep="last")].sort_index()
    if df.index[-1] + pd.Timedelta(minutes=interval) > now:
        df = df.iloc[:-1]
    return df if len(df) else None

def _fetch_price_chunk(keys):
    prices = {}
    joined = ",".join(_enc(k) for k in keys)
    r = _get(f"{BASE}/v3/market-quote/quotes?instrument_key={joined}", timeout=20)
    if r is None or r.status_code != 200: return prices
    try:
        data = r.json()
        if data.get("status") != "success": return prices
        for resp_key, q in data.get("data", {}).items():
            if not q: continue
            price = q.get("last_price") or q.get("ohlc", {}).get("close") or 0
            if price and price > 0:
                token = q.get("instrument_token")
                if token: prices[token] = float(price)
                prices[resp_key] = float(price)
    except Exception: pass
    return prices

def fetch_bulk_live_prices(keys, chunk_size=50):
    prices = {}
    for i in range(0, len(keys), chunk_size):
        prices.update(_fetch_price_chunk(keys[i:i + chunk_size]))
    return prices

def _lookup_price(prices, key):
    return prices.get(key) or prices.get(key.replace("|", ":"))

def _resample(df5, minutes):
    agg = {"Open": "first", "High": "max", "Low": "min", "Close": "last", "Volume": "sum"}
    out = df5.resample(f"{minutes}min", offset="15min").agg(agg).dropna(subset=["Open"])
    if len(out):
        last_end_5m = df5.index[-1] + pd.Timedelta(minutes=5)
        if out.index[-1] + pd.Timedelta(minutes=minutes) > last_end_5m:
            out = out.iloc[:-1]
    return out

def _analyze_candles(df):
    from core.pattern_detector import detect_candlestick_patterns
    try:
        patterns = detect_candlestick_patterns(df, lookback=5)
    except:
        return 0, 0, []
    bull, bear, top = 0, 0, []
    for p in patterns[:8]:
        w = p.get("strength", 1.0) * (1.5 if p.get("is_latest") else 1)
        if p["direction"] == "BULLISH": bull += w; top.append(f"🟢 {p['pattern']}")
        elif p["direction"] == "BEARISH": bear += w; top.append(f"🔴 {p['pattern']}")
    return round(bull, 2), round(bear, 2), top[:4]

def _analyze_chart(df, use_vwap=False):
    metrics = {"rsi": None}
    if df is None or len(df) < 30: raise ValueError("not enough bars")
    close = df["Close"]
    score = 0
    ema9 = close.ewm(span=9, adjust=False).mean().iloc[-1]
    ema21 = close.ewm(span=21, adjust=False).mean().iloc[-1]
    current = close.iloc[-1]

    if current > ema9 > ema21: score += 30
    elif current < ema9 < ema21: score -= 30

    if use_vwap:
        last_date = df.index[-1].date()
        day = df[df.index.date == last_date]
        vol = day["Volume"]
        if vol.sum() > 0:
            tp = (day["High"] + day["Low"] + day["Close"]) / 3
            vwap = float((tp * vol).sum() / vol.sum())
            if current > vwap: score += 15
            else: score -= 15

    rsi = _rsi(close)
    metrics["rsi"] = rsi
    if rsi < 25: score += 25
    elif rsi < 30: score += 15
    elif rsi > 75: score -= 25
    elif rsi > 70: score -= 15
    return max(-100, min(100, score)), [], metrics

def _analyze_volume(df5):
    last_date = df5.index[-1].date()
    today = df5[df5.index.date == last_date].tail(3)
    prior = df5[df5.index.date < last_date]
    if len(today) < 2 or len({d for d in prior.index.date}) < 2: raise ValueError("vol data")
    avg_by_time = prior.groupby(prior.index.time)["Volume"].mean()
    expected = avg_by_time.reindex([t.time() for t in today.index])
    if expected.isna().any() or (expected <= 0).any(): raise ValueError("vol base missing")
    
    rel_vol = float((today["Volume"].values / expected.values).mean())
    direction = 1 if today["Close"].iloc[-1] > today["Open"].iloc[0] else -1
    mag = 25 if rel_vol >= 2.0 else 18 if rel_vol >= 1.5 else 8 if rel_vol >= 1.0 else 0
    return direction * mag, [], rel_vol

def _check_volume_breakout(df5):
    """
    BREAKOUT LOGIC: Checks if the last closed candle broke out of a 
    15-candle range (approx 75 mins) with high volume.
    """
    if len(df5) < 20: return 0, []
    
    # Look at the 15 candles BEFORE the last closed candle to find the range
    lookback = 15
    recent_range = df5.iloc[-lookback-1:-1] 
    
    prev_high = recent_range["High"].max()
    prev_low = recent_range["Low"].min()
    avg_vol = recent_range["Volume"].mean()
    
    last_candle = df5.iloc[-1]
    last_close = last_candle["Close"]
    last_open = last_candle["Open"]
    last_vol = last_candle["Volume"]
    
    score = 0
    obs = []
    
    # Bullish Breakout with Volume
    if last_close > prev_high and last_vol > (avg_vol * 1.5) and last_close > last_open:
        score += 40
        obs.append(f"🚀 VOLUME BREAKOUT (Vol: {last_vol/avg_vol:.1f}x)")
    # Bearish Breakdown with Volume
    elif last_close < prev_low and last_vol > (avg_vol * 1.5) and last_close < last_open:
        score -= 40
        obs.append(f"🔻 VOLUME BREAKDOWN (Vol: {last_vol/avg_vol:.1f}x)")
        
    return score, obs

def _quick_mtf(df5):
    details = {}
    for name, minutes in (("15m", 15), ("1h", 60)):
        try:
            sc, _, _ = _analyze_chart(_resample(df5, minutes))
        except Exception:
            details[name] = "NO_DATA"; continue
        details[name] = "BULLISH" if sc > 15 else "BEARISH" if sc < -15 else "NEUTRAL"

    if "NO_DATA" in details.values(): raise ValueError("MTF data insufficient")
    bulls = sum(1 for v in details.values() if v == "BULLISH")
    bears = sum(1 for v in details.values() if v == "BEARISH")

    if bulls == 2: return 25, ["🔥 MTF BULLISH (2/2)"], details
    if bears == 2: return -25, ["🔥 MTF BEARISH (2/2)"], details
    return 0, ["Timeframes mixed"], details

def get_market_regime():
    try:
        df = _fetch_candles(NIFTY_KEY, 5, days=5)
        if df is None or len(df) < 30: return None
        score, _, _ = _analyze_chart(df)
        label = "BULLISH" if score >= REGIME_BLOCK else "BEARISH" if score <= -REGIME_BLOCK else "NEUTRAL"
        return {"score": score, "label": label}
    except Exception: return None

def _score_decision(cb, cbe, chart_score, vol_score, mtf_score, mtf_details):
    candle_total = cb + cbe
    candle_score = ((cb - cbe) / max(candle_total, 2)) * 100 if candle_total > 0 else 0
    
    # Chart score now includes the Volume Breakout bonus!
    final = (candle_score * 0.30 + chart_score * 0.35 + vol_score * 0.15 + mtf_score * 0.20)

    if final >= BUY_THRESHOLD:
        action, strength = "BUY", "STRONG" if final >= 40 else "MODERATE"
    elif final <= SELL_THRESHOLD:
        action, strength = "SELL", "STRONG" if final <= -40 else "MODERATE"
    else:
        action, strength = "WAIT", "NO_EDGE"

    if mtf_details and set(mtf_details.values()) == {"BULLISH", "BEARISH"}:
        if action in ("BUY", "SELL"): action, strength = "WAIT", "MTF_CONFLICT"
    
    return {"action": action, "strength": strength, "score": round(final, 1), "candle_score": round(candle_score, 1)}

def _build_levels(action, entry, atr):
    sl_dist = max(1.5 * atr, entry * MIN_SL_PCT)
    if sl_dist > entry * MAX_SL_PCT: return None, None, f"Required SL {sl_dist/entry*100:.2f}% > max"
    tgt_dist = sl_dist * TARGET_R
    if action == "BUY":
        sl, target = entry - sl_dist, entry + tgt_dist
        if not (sl < entry < target): return None, None, "Invalid geometry"
    else:
        sl, target = entry + sl_dist, entry - tgt_dist
        if not (target < entry < sl): return None, None, "Invalid geometry"
    return round(sl, 2), round(target, 2), None

def _trade_costs(direction, entry, exit_price, qty):
    if direction == "BUY": buy_val, sell_val = entry * qty, exit_price * qty
    else: sell_val, buy_val = entry * qty, exit_price * qty
    brokerage = (min(BROKERAGE_FLAT, BROKERAGE_PCT * buy_val) + min(BROKERAGE_FLAT, BROKERAGE_PCT * sell_val))
    stt = 0.00025 * sell_val
    exch = 0.0000297 * (buy_val + sell_val)
    sebi = 0.000001 * (buy_val + sell_val)
    stamp = 0.00003 * buy_val
    gst = 0.18 * (brokerage + exch + sebi)
    slippage = SLIPPAGE_PCT * (buy_val + sell_val)
    return brokerage + stt + exch + sebi + stamp + gst + slippage

def _size_position(direction, entry, sl):
    risk_ps = abs(entry - sl)
    if risk_ps <= 0: return 0, "Invalid SL"
    max_risk = min(TRADING_CAPITAL * MAX_RISK_PCT, MAX_RISK_PER_TRADE if MAX_RISK_PER_TRADE > 0 else 1e18)
    max_by_capital = int(TRADING_CAPITAL * MAX_POSITION_PCT / entry)
    if max_by_capital < 1: return 0, f"Price ₹{entry:.0f} too high"
    
    shares = min(int(max_risk / risk_ps), max_by_capital)
    while shares >= 1:
        net_loss = shares * risk_ps + _trade_costs(direction, entry, sl, shares)
        if net_loss <= max_risk: return shares, None
        shares -= 1
    return 0, f"1 share net risk exceeds limit ₹{max_risk:.0f}"

# ═══════════════════════════════════════════════════════
#  ANALYZE STOCK
# ═══════════════════════════════════════════════════════
def analyze_stock(symbol, instrument_key, live_price=None, regime=None, allow_after_hours=None):
    def skip(reason): return {"symbol": symbol, "status": "SKIP", "action": "SKIP", "reason": reason}

    if allow_after_hours is None: allow_after_hours = ALLOW_OFFHOURS
    if not allow_after_hours:
        safe, why = _is_trading_time()
        if not safe: return skip(why)

    if not live_price or live_price <= 0: return skip("no live price")
    live = float(live_price)
    if live > MAX_STOCK_PRICE: return skip(f"price ₹{live:.0f} > max ₹{MAX_STOCK_PRICE:.0f}")

    try: df5 = _fetch_candles(instrument_key, 5, days=FETCH_DAYS_5M)
    except Exception: return skip("candle fetch error")
    if df5 is None or len(df5) < 60: return skip("insufficient 5m data")

    try:
        adtv = _get_avg_daily_turnover(df5)
        if adtv < MIN_TURNOVER_CR: return skip(f"low liquidity (ADTV ₹{adtv:.2f} Cr)")
    except Exception: return skip("turnover fail-closed")

    if not allow_after_hours:
        mins_left = _minutes_to_close()
        if mins_left < MAX_HOLD_MINUTES: return skip(f"only {mins_left:.0f} min to close")

    last_close = float(df5["Close"].iloc[-1])
    if abs(live / last_close - 1) > MAX_GAP_PCT: return skip(f"gap/stale")

    try:
        cb, cbe, patterns = _analyze_candles(df5)
        chart_score, _, metrics = _analyze_chart(df5, use_vwap=True)
        
        # 🔥 NEW LOGIC: Check for Volume Breakout
        breakout_score, breakout_obs = _check_volume_breakout(df5)
        chart_score += breakout_score # Boosting chart score with breakout
        
        vol_score, _, rel_vol = _analyze_volume(df5)
        mtf_score, _, mtf_details = _quick_mtf(df5)
        atr = _atr(df5)
    except Exception as e: return skip(f"indicator error: {e}")

    d = _score_decision(cb, cbe, chart_score, vol_score, mtf_score, mtf_details)
    action = d["action"]
    
    if action == "WAIT": return {"symbol": symbol, "status": "WAIT", "action": "WAIT", "score": d["score"], "reason": d["strength"]}
    if rel_vol < MIN_REL_VOL: return skip(f"low volume ({rel_vol:.1f}x of normal)")

    rsi = metrics["rsi"]
    if action == "BUY" and rsi > RSI_BLOCK_BUY: return skip(f"BUY blocked: RSI {rsi:.0f} > {RSI_BLOCK_BUY}")
    if action == "SELL" and rsi < RSI_BLOCK_SELL: return skip(f"SELL blocked: RSI {rsi:.0f} < {RSI_BLOCK_SELL}")

    if regime is not None and REQUIRE_REGIME:
        if action == "BUY" and regime["score"] <= -REGIME_BLOCK: return skip(f"BUY blocked: Nifty bearish ({regime['score']:+d})")
        if action == "SELL" and regime["score"] >= REGIME_BLOCK: return skip(f"SELL blocked: Nifty bullish ({regime['score']:+d})")

    entry = live
    sl, target, err = _build_levels(action, entry, atr)
    if err: return skip(err)

    shares, err = _size_position(action, entry, sl)
    if err: return skip(err)

    gross_profit = shares * abs(target - entry)
    gross_loss = shares * abs(entry - sl)
    net_profit = gross_profit - _trade_costs(action, entry, target, shares)
    net_loss = gross_loss + _trade_costs(action, entry, sl, shares)
    rr_net = net_profit / net_loss if net_loss > 0 else 0

    if net_profit < MIN_NET_PROFIT: return skip(f"net profit ₹{net_profit:.0f} < ₹{MIN_NET_PROFIT:.0f}")
    if rr_net < MIN_RR_NET: return skip(f"net R:R {rr_net:.2f} < {MIN_RR_NET}")

    return {
        "symbol": symbol, "status": "SIGNAL", "sector": SECTORS.get(symbol, "OTHER"),
        "spot": round(entry, 2), "action": action, "strength": d["strength"], "score": d["score"],
        "candle_score": d["candle_score"], "chart_score": round(chart_score, 1),
        "vol_score": round(vol_score, 1), "mtf_score": round(mtf_score, 1),
        "rel_vol": round(rel_vol, 2), "rsi": round(rsi, 1),
        "breakout_obs": breakout_obs, # Displaying the breakout signal
        "entry": round(entry, 2), "sl": sl, "target": target, "shares": shares,
        "cost": round(shares * entry, 2), "gross_profit": round(gross_profit, 2),
        "gross_loss": round(gross_loss, 2), "net_profit": round(net_profit, 2),
        "net_loss": round(net_loss, 2), "rr_net": round(rr_net, 2),
        "risk_pct": round(net_loss / TRADING_CAPITAL * 100, 2),
        "regime": regime["label"] if regime else "N/A"
    }

# ═══════════════════════════════════════════════════════
#  SCAN AND RANK
# ═══════════════════════════════════════════════════════
def scan_and_rank(return_details=False):
    safe, reason = _is_trading_time()
    print(f"⏰ Time: {reason}")

    def _out(picks, skipped, waits, regime):
        if return_details: return {"picks": picks, "skipped": skipped, "waits": waits, "regime": regime}
        return picks

    if not safe and not ALLOW_OFFHOURS:
        print("   🔴 Outside safe trading window")
        return _out([], {}, 0, None)

    max_risk = min(TRADING_CAPITAL * MAX_RISK_PCT, MAX_RISK_PER_TRADE if MAX_RISK_PER_TRADE > 0 else 1e18)
    print(f"🔍 Scanning {len(STOCK_UNIVERSE)} stocks...")
    print(f"💰 Capital: ₹{int(TRADING_CAPITAL)} | Max risk/trade: ₹{int(max_risk)}\n")

    print("   📡 Step 1/3: bulk live prices...")
    keys = list(STOCK_UNIVERSE.values())
    prices = fetch_bulk_live_prices(keys)
    matched = sum(1 for k in keys if _lookup_price(prices, k))
    print(f"   ✅ Matched {matched}/{len(keys)} prices")

    print("   📡 Step 2/3: Nifty regime...")
    regime = get_market_regime()
    if regime: print(f"   ✅ Nifty: {regime['label']} ({regime['score']:+d})")
    else: print("   ⚠️ Nifty regime unavailable")

    print("   📡 Step 3/3: analyzing stocks...")
    signals, skipped, waits = [], {}, 0

    def _scan_one(item):
        sym, key = item
        try: return analyze_stock(sym, key, live_price=_lookup_price(prices, key), regime=regime)
        except Exception as e: return {"symbol": sym, "status": "SKIP", "reason": f"error: {e}"}

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        futures = [ex.submit(_scan_one, item) for item in STOCK_UNIVERSE.items()]
        done = 0
        for fut in as_completed(futures):
            done += 1
            if done % 25 == 0: print(f"   ⏳ {done}/{len(STOCK_UNIVERSE)}")
            r = fut.result()
            st = r.get("status")
            if st == "SIGNAL": signals.append(r)
            elif st == "WAIT": waits += 1
            else: skipped[r["symbol"]] = r.get("reason", "unknown")

    buys = sum(1 for s in signals if s["action"] == "BUY")
    sells = len(signals) - buys
    print(f"\n   📊 BUY: {buys} | SELL: {sells} | WAIT: {waits} | SKIPPED: {len(skipped)}")

    signals.sort(key=lambda x: -abs(x["score"]))
    picks, per_sector = [], {}
    for s in signals:
        sec = s["sector"]
        if sec != "OTHER" and per_sector.get(sec, 0) >= MAX_PER_SECTOR:
            skipped[s["symbol"]] = f"sector cap ({sec})"
            continue
        per_sector[sec] = per_sector.get(sec, 0) + 1
        picks.append(s)
        if len(picks) >= 10: break

    return _out(picks, skipped, waits, regime)

# ═══════════════════════════════════════════════════════
#  FORMAT & MAIN
# ═══════════════════════════════════════════════════════
def format_results(results, skipped=None):
    if not results:
        msg = "\n⚠️  No high-quality signals found.\n"
        if skipped: msg += _format_skipped(skipped)
        return msg

    lines = ["\n" + "═" * 68, f"  📊 EQUITY INTRADAY — TOP PICKS  (Capital: ₹{int(TRADING_CAPITAL)})", "═" * 68]
    for i, r in enumerate(results, 1):
        emoji = "🟢" if r["action"] == "BUY" else "🔴"
        lines.append("")
        lines.append(f"  {emoji} #{i} {r['symbol']} [{r['sector']}] — {r['action']}  ({r['strength']}, score {r['score']:+.1f})")
        
        # Print the breakout indicator if it exists
        if r.get("breakout_obs"):
            lines.append(f"     🚨 {r['breakout_obs'][0]}")
            
        lines.append(f"     Live: ₹{r['spot']}   Nifty: {r['regime']}   RSI: {r['rsi']}   RelVol: {r['rel_vol']}x")
        lines.append("")
        lines.append(f"     Entry:      ₹{r['entry']}")
        lines.append(f"     Stop Loss:  ₹{r['sl']}   ({(r['sl']/r['entry']-1)*100:+.2f}%)")
        lines.append(f"     Target:     ₹{r['target']}   ({(r['target']/r['entry']-1)*100:+.2f}%)")
        lines.append("")
        lines.append(f"     Shares:     {r['shares']}   (cost ₹{r['cost']})")
        lines.append(f"     At target:  gross ₹+{r['gross_profit']}  →  NET ₹+{r['net_profit']}")
        lines.append(f"     At SL:      gross ₹-{r['gross_loss']}  →  NET ₹-{r['net_loss']}")
        lines.append(f"     📊 Net R:R = 1:{r['rr_net']:.2f}   |   Risk: {r['risk_pct']}% of capital")
        
    lines.append("\n" + "═" * 68)
    lines.append("  ⚠️  Net figures include estimated charges + slippage.")
    lines.append("  ⚠️  Not financial advice. Paper trade before going live.")
    lines.append("═" * 68 + "\n")
    out = "\n".join(lines)
    if skipped: out += _format_skipped(skipped)
    return out

def _format_skipped(skipped):
    lines = ["  ⏭️  SKIPPED (why):"]
    for sym, why in sorted(skipped.items()):
        lines.append(f"     {sym:<12} {why}")
    return "\n".join(lines) + "\n"

if __name__ == "__main__":
    import sys
    safe, reason = _is_trading_time()
    if not safe and not ALLOW_OFFHOURS:
        print(f"🔴 {reason}. Set ALLOW_OFFHOURS=1 in .env to test.")
        sys.exit(0)

    print(f"⏰ {reason}\n")
    details = scan_and_rank(return_details=True)
    print(format_results(details["picks"], skipped=details["skipped"]))