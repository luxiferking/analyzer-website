import os
import json
import time
from datetime import datetime
from pathlib import Path
import yfinance as yf
import requests


# ==========================================
# STOCK UNIVERSE — Nifty 50 Core Holdings
# ==========================================
UNIVERSE = [
    {"symbol": "RELIANCE",   "yf": "RELIANCE.NS"},
    {"symbol": "TCS",        "yf": "TCS.NS"},
    {"symbol": "HDFCBANK",   "yf": "HDFCBANK.NS"},
    {"symbol": "ICICIBANK",  "yf": "ICICIBANK.NS"},
    {"symbol": "INFY",       "yf": "INFY.NS"},
    {"symbol": "HINDUNILVR", "yf": "HINDUNILVR.NS"},
    {"symbol": "ITC",        "yf": "ITC.NS"},
    {"symbol": "SBIN",       "yf": "SBIN.NS"},
    {"symbol": "BHARTIARTL", "yf": "BHARTIARTL.NS"},
    {"symbol": "KOTAKBANK",  "yf": "KOTAKBANK.NS"},
    {"symbol": "LT",         "yf": "LT.NS"},
    {"symbol": "AXISBANK",   "yf": "AXISBANK.NS"},
    {"symbol": "BAJFINANCE", "yf": "BAJFINANCE.NS"},
    {"symbol": "ASIANPAINT", "yf": "ASIANPAINT.NS"},
    {"symbol": "MARUTI",     "yf": "MARUTI.NS"},
    {"symbol": "SUNPHARMA",  "yf": "SUNPHARMA.NS"},
    {"symbol": "TITAN",      "yf": "TITAN.NS"},
    {"symbol": "ULTRACEMCO", "yf": "ULTRACEMCO.NS"},
    {"symbol": "WIPRO",      "yf": "WIPRO.NS"},
    {"symbol": "NESTLEIND",  "yf": "NESTLEIND.NS"},
    {"symbol": "TATASTEEL",  "yf": "TATASTEEL.NS"},
    {"symbol": "POWERGRID",  "yf": "POWERGRID.NS"},
    {"symbol": "NTPC",       "yf": "NTPC.NS"},
    {"symbol": "M&M",        "yf": "M&M.NS"},
    {"symbol": "HCLTECH",    "yf": "HCLTECH.NS"},
    {"symbol": "TECHM",      "yf": "TECHM.NS"},
    {"symbol": "ADANIENT",   "yf": "ADANIENT.NS"},
    {"symbol": "ADANIPORTS", "yf": "ADANIPORTS.NS"},
    {"symbol": "COALINDIA",  "yf": "COALINDIA.NS"},
    {"symbol": "ONGC",       "yf": "ONGC.NS"},
    {"symbol": "BPCL",       "yf": "BPCL.NS"},
    {"symbol": "IOC",        "yf": "IOC.NS"},
    {"symbol": "GRASIM",     "yf": "GRASIM.NS"},
    {"symbol": "JSWSTEEL",   "yf": "JSWSTEEL.NS"},
    {"symbol": "INDUSINDBK", "yf": "INDUSINDBK.NS"},
    {"symbol": "DRREDDY",    "yf": "DRREDDY.NS"},
    {"symbol": "CIPLA",      "yf": "CIPLA.NS"},
    {"symbol": "DIVISLAB",   "yf": "DIVISLAB.NS"},
    {"symbol": "APOLLOHOSP", "yf": "APOLLOHOSP.NS"},
    {"symbol": "BRITANNIA",  "yf": "BRITANNIA.NS"},
    {"symbol": "EICHERMOT",  "yf": "EICHERMOT.NS"},
    {"symbol": "HEROMOTOCO", "yf": "HEROMOTOCO.NS"},
    {"symbol": "BAJAJ-AUTO", "yf": "BAJAJ-AUTO.NS"},
    {"symbol": "BAJAJFINSV", "yf": "BAJAJFINSV.NS"},
    {"symbol": "SBILIFE",    "yf": "SBILIFE.NS"},
    {"symbol": "HDFCLIFE",   "yf": "HDFCLIFE.NS"},
    {"symbol": "TATACONSUM", "yf": "TATACONSUM.NS"},
    {"symbol": "SIEMENS",    "yf": "SIEMENS.NS"},
]


# ==========================================
# STEP 1: Fetch Fundamentals
# ==========================================
def fetch_fundamentals(yf_ticker, retries=2):
    """Fetch fundamental + technical data via yfinance."""
    for attempt in range(retries):
        try:
            ticker = yf.Ticker(yf_ticker)
            info = ticker.info
            if not info or info.get("regularMarketPrice") is None:
                time.sleep(0.5)
                continue

            price = info.get("currentPrice") or info.get("regularMarketPrice")
            if not price:
                return None

            high_52 = info.get("fiftyTwoWeekHigh")
            low_52 = info.get("fiftyTwoWeekLow")

            price_position = 0
            if high_52 and low_52 and high_52 != low_52:
                price_position = ((price - low_52) / (high_52 - low_52)) * 100

            hist = ticker.history(period="6mo")
            ret_3m = 0
            ret_6m = 0
            if hist is not None and len(hist) >= 60:
                old_price = hist['Close'].iloc[-60]
                ret_3m = ((price - old_price) / old_price) * 100
            if hist is not None and len(hist) >= 120:
                old_price = hist['Close'].iloc[-120]
                ret_6m = ((price - old_price) / old_price) * 100

            return {
                "symbol": yf_ticker.replace(".NS", ""),
                "price": round(price, 2),
                "market_cap_cr": round(info.get("marketCap", 0) / 1e7, 0) if info.get("marketCap") else None,
                "pe_ratio": info.get("trailingPE"),
                "forward_pe": info.get("forwardPE"),
                "price_to_book": info.get("priceToBook"),
                "roe": info.get("returnOnEquity"),
                "debt_to_equity": info.get("debtToEquity"),
                "profit_margin": info.get("profitMargins"),
                "operating_margin": info.get("operatingMargins"),
                "revenue_growth": info.get("revenueGrowth"),
                "earnings_growth": info.get("earningsGrowth"),
                "dividend_yield": info.get("dividendYield"),
                "beta": info.get("beta"),
                "fifty_two_high": high_52,
                "fifty_two_low": low_52,
                "price_position_52w_pct": round(price_position, 1),
                "return_3m_pct": round(ret_3m, 1),
                "return_6m_pct": round(ret_6m, 1),
                "sector": info.get("sector", "Unknown"),
            }
        except Exception:
            time.sleep(1)
    return None


def fetch_news(yf_ticker, limit=3):
    """Fetch recent news headlines."""
    try:
        ticker = yf.Ticker(yf_ticker)
        news = ticker.news
        if not news:
            return []
        headlines = []
        for item in news[:limit]:
            title = item.get("title") or item.get("headline")
            if title:
                headlines.append(title)
        return headlines
    except Exception:
        return []


# ==========================================
# STEP 2: AI Scoring via Groq
# ==========================================
def ask_groq_json(prompt):
    """Send prompt to Groq, expect JSON response.
    Using the 8B model for much higher rate limits on free tier.
    """
    api_key = os.getenv("GROQ_API_KEY")
    model = "openai/gpt-oss-20b"  # Current fast model with high rate limits

    url = "https://api.groq.com/openai/v1/chat/completions"
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.1,
        "response_format": {"type": "json_object"},
        "max_tokens": 2000,
    }

    try:
        response = requests.post(url, headers=headers, json=payload, timeout=120)
        response.raise_for_status()
        return response.json()["choices"][0]["message"]["content"]
    except requests.exceptions.HTTPError as e:
        if e.response.status_code == 429:
            print(f"    Rate limit hit (429) - will retry after sleep")
        else:
            print(f"    HTTP {e.response.status_code}: {e.response.text[:150]}")
        return None
    except Exception as e:
        print(f"    Groq error: {str(e)[:100]}")
        return None


def score_batch_with_ai(batch, max_retries=5):
    """Score a batch of stocks using Groq AI with retry logic."""
    prompt = f"""You are a Senior Equity Research Analyst at an Indian investment firm.

Analyze the following {len(batch)} stocks for SWING TRADING (holding 2-6 weeks).

For each stock, evaluate:
1. QUALITY (weight 60%): valuation (P/E, forward P/E, P/B), profitability (ROE, margins), balance sheet (debt/equity), growth (revenue, earnings)
2. MOMENTUM (weight 40%): price position in 52-week range, 3-month and 6-month returns, trend direction

Scoring guide:
- quality_score: 1-10 (1=terrible, 10=excellent)
- momentum_score: 1-10 (1=strong downtrend, 10=strong uptrend)
- overall_score: 1-10 (weighted average, rounded)
- verdict: "STRONG BUY" (>=8), "BUY" (7-8), "WATCH" (5-7), "AVOID" (<5)
- reason: ONE clear sentence (max 15 words)

STOCKS DATA:
{json.dumps(batch, indent=2, default=str)}

Return ONLY this JSON structure (no other text):
{{
  "rankings": [
    {{"symbol": "RELIANCE", "quality_score": 8, "momentum_score": 7, "overall_score": 8, "verdict": "STRONG BUY", "reason": "Strong ROE and positive trend"}}
  ]
}}
"""

    for attempt in range(max_retries):
        try:
            response_text = ask_groq_json(prompt)
            if not response_text:
                # Backoff: 15s, 20s, 25s, 30s, 35s
                wait_time = 15 + (attempt * 5)
                print(f"    Retry {attempt+1}/{max_retries} - sleeping {wait_time}s...")
                time.sleep(wait_time)
                continue

            response_text = response_text.strip()
            if response_text.startswith("```"):
                response_text = response_text.split("```")[1]
                if response_text.startswith("json"):
                    response_text = response_text[4:]

            data = json.loads(response_text)
            return data.get("rankings", [])

        except json.JSONDecodeError as e:
            print(f"    JSON parse error (attempt {attempt+1}/{max_retries}): {str(e)[:60]}")
            time.sleep(5)
        except Exception as e:
            print(f"    Error (attempt {attempt+1}/{max_retries}): {str(e)[:60]}")
            time.sleep(5)

    return []


# ==========================================
# STEP 3: Main Screener Pipeline
# ==========================================
def run_screener(output_dir="data", batch_size=5):
    """Full screener pipeline with rate-limit protection."""
    os.makedirs(output_dir, exist_ok=True)

    print(f"\nScreening {len(UNIVERSE)} stocks from Nifty 50...\n")

    # ---- Fetch all fundamentals ----
    all_data = []
    for i, stock in enumerate(UNIVERSE, 1):
        print(f"  [{i}/{len(UNIVERSE)}] Fetching {stock['symbol']}...")
        fundamentals = fetch_fundamentals(stock['yf'])
        if fundamentals:
            news = fetch_news(stock['yf'], limit=3)
            fundamentals['recent_news'] = news
            all_data.append(fundamentals)

    print(f"\n✓ Fetched data for {len(all_data)} stocks\n")

    # ---- Score in batches with delays ----
    all_rankings = []
    total_batches = (len(all_data) + batch_size - 1) // batch_size

    for i in range(0, len(all_data), batch_size):
        batch = all_data[i:i+batch_size]
        batch_num = i // batch_size + 1
        print(f"  Scoring batch {batch_num}/{total_batches} ({len(batch)} stocks)...")

        rankings = score_batch_with_ai(batch)
        all_rankings.extend(rankings)

        print(f"    → Got {len(rankings)} rankings from this batch")

        # 25s delay between batches (safe for Groq free tier)
        if batch_num < total_batches:
            print(f"    Sleeping 25s before next batch...")
            time.sleep(25)

    # ---- Sort by overall score ----
    all_rankings.sort(key=lambda x: x.get("overall_score", 0), reverse=True)

    # ---- Save ----
    today = datetime.now().strftime("%Y-%m-%d")
    output = {
        "date": today,
        "generated_at": datetime.now().isoformat(),
        "total_stocks_screened": len(all_data),
        "total_ranked": len(all_rankings),
        "rankings": all_rankings,
    }

    path = Path(output_dir) / f"watchlist_{today}.json"
    with open(path, "w") as f:
        json.dump(output, f, indent=2, default=str)

    latest = Path(output_dir) / "latest_watchlist.json"
    with open(latest, "w") as f:
        json.dump(output, f, indent=2, default=str)

    print(f"\n✓ Saved to {path}")
    print(f"✓ Total ranked: {len(all_rankings)} stocks")
    return all_rankings


# ==========================================
# STEP 4: Pretty Print
# ==========================================
def print_watchlist(rankings, top_n=15):
    """Pretty print the top candidates."""
    print("\n" + "=" * 85)
    print("  JARVIS WEEKLY SWING TRADING CANDIDATES")
    print("=" * 85)
    print(f"{'Rank':<5} {'Symbol':<13} {'Quality':<8} {'Momentum':<9} {'Overall':<8} {'Verdict':<13}")
    print("-" * 85)

    for i, r in enumerate(rankings[:top_n], 1):
        print(
            f"{i:<5} "
            f"{r.get('symbol', 'N/A'):<13} "
            f"{r.get('quality_score', 0):<8} "
            f"{r.get('momentum_score', 0):<9} "
            f"{r.get('overall_score', 0):<8} "
            f"{r.get('verdict', 'N/A'):<13}"
        )

    print("=" * 85)

    print("\nDETAILED ANALYSIS (Top 10):")
    print("-" * 85)
    for i, r in enumerate(rankings[:10], 1):
        print(f"\n{i}. {r.get('symbol')} — {r.get('verdict')} ({r.get('overall_score')}/10)")
        print(f"   {r.get('reason', '')}")