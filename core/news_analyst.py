"""
core/news_analyst.py
Dual-source news fetch + AI sentiment analysis.

Sources:
  1. Google News RSS (primary — real-time, unlimited, free)
  2. NewsAPI.org (secondary — structured metadata, 100/day)

Improvements:
✅ Dual-source merge with deduplication
✅ Index symbol mapping (NIFTY → "NIFTY 50 index")
✅ Multi-tier source reliability (T1/T2/T3)
✅ Age-aware sentiment (fresh vs stale)
✅ Robust JSON parsing with retries
✅ Format function for pretty output
✅ 5-minute cache TTL
"""
import os
import re
import json
import time
import requests
import xml.etree.ElementTree as ET
from urllib.parse import quote
from email.utils import parsedate_to_datetime
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv

from core.ai_client import ask_groq

load_dotenv()


# ============================================================
#  CONFIG
# ============================================================
MAX_NEWS_AGE_HOURS = 48
FALLBACK_AGE_HOURS = 168  # 7 days

_CACHE = {}
_CACHE_TTL = 300  # 5 min


# Symbol → better search query
SYMBOL_QUERY_MAP = {
    "NIFTY": "NIFTY 50 index",
    "NIFTY50": "NIFTY 50 index",
    "BANKNIFTY": "Bank Nifty index",
    "FINNIFTY": "Fin Nifty index",
    "MIDCPNIFTY": "Nifty Midcap",
    "SENSEX": "Sensex BSE",
    "RELIANCE": "Reliance Industries",
    "TCS": "TCS Tata Consultancy",
    "HDFCBANK": "HDFC Bank",
    "INFY": "Infosys",
    "ICICIBANK": "ICICI Bank",
    "SBIN": "State Bank of India",
    "TATAMOTORS": "Tata Motors",
    "ADANIENT": "Adani Enterprises",
    "HINDUNILVR": "Hindustan Unilever",
    "ITC": "ITC Limited",
    "LT": "Larsen Toubro",
    "AXISBANK": "Axis Bank",
    "KOTAKBANK": "Kotak Mahindra Bank",
    "MARUTI": "Maruti Suzuki",
    "BAJFINANCE": "Bajaj Finance",
}

# Source reliability tiers
TIER1_SOURCES = {
    "reuters", "bloomberg", "cnbc", "cnbc-tv18", "cnbctv18",
    "economic times", "the economic times", "business standard",
    "mint", "livemint", "financial express",
}

TIER2_SOURCES = {
    "moneycontrol", "ndtv profit", "ndtv", "zeebiz", "bq prime",
    "business today", "forbes india", "the hindu", "the hindu businessline",
    "times of india", "hindustan times", "india today",
}

TIER3_SOURCES = {
    "the print", "the wire", "scroll", "news18", "republic",
    "firstpost", "opindia", "swarajya",
}


# ============================================================
#  HELPERS
# ============================================================
def _get_cached(key):
    entry = _CACHE.get(key)
    if entry and (time.time() - entry["ts"]) < _CACHE_TTL:
        return entry["data"]
    return None


def _set_cached(key, data):
    _CACHE[key] = {"ts": time.time(), "data": data}


def _build_query(symbol):
    s = (symbol or "").upper().strip()
    if s in SYMBOL_QUERY_MAP:
        return SYMBOL_QUERY_MAP[s]
    return f"{s} stock India"


def _parse_pub_date(date_str):
    if not date_str:
        return None
    try:
        dt = parsedate_to_datetime(date_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


def _hours_old(dt):
    if dt is None:
        return None
    now = datetime.now(timezone.utc)
    delta = now - dt
    return round(delta.total_seconds() / 3600, 1)


def _source_tier(source_str):
    s = (source_str or "").lower().strip()
    for t1 in TIER1_SOURCES:
        if t1 in s:
            return 1
    for t2 in TIER2_SOURCES:
        if t2 in s:
            return 2
    for t3 in TIER3_SOURCES:
        if t3 in s:
            return 3
    return 0


# ============================================================
#  NEWS FETCH — DUAL SOURCE
# ============================================================
def fetch_news(query, limit=8, max_age_hours=MAX_NEWS_AGE_HOURS):
    """Dual-source news fetch: Google News RSS + NewsAPI."""
    all_news = []

    # ─── SOURCE 1: Google News RSS ───
    try:
        gnews_url = (f"https://news.google.com/rss/search?"
                     f"q={quote(query)}&hl=en-IN&gl=IN&ceid=IN:en")
        r = requests.get(gnews_url, timeout=10,
                         headers={"User-Agent": "Mozilla/5.0"})

        if r.status_code == 200:
            root = ET.fromstring(r.content)
            items = root.findall(".//item")

            for item in items:
                title_el = item.find("title")
                date_el = item.find("pubDate")
                source_el = item.find("source")

                title = title_el.text if title_el is not None else ""
                pub_date = date_el.text if date_el is not None else ""
                source = source_el.text if source_el is not None else ""

                if not title:
                    continue

                dt = _parse_pub_date(pub_date)
                hours_old = _hours_old(dt)
                tier = _source_tier(source)

                all_news.append({
                    "title": title,
                    "date": pub_date,
                    "source": source,
                    "hours_old": hours_old,
                    "tier": tier,
                    "is_reliable": tier in (1, 2),
                    "provider": "gnews",
                })
    except Exception as e:
        print(f"   [gnews fetch error] {e}")

    # ─── SOURCE 2: NewsAPI.org ───
    newsapi_key = os.getenv("NEWS_API_KEY")
    if newsapi_key:
        try:
            clean_q = query.replace(" stock India", "").replace(" index", "")
            na_params = {
                "q": clean_q,
                "language": "en",
                "sortBy": "publishedAt",
                "pageSize": min(limit, 10),
                "apiKey": newsapi_key,
            }
            r = requests.get("https://newsapi.org/v2/everything",
                             params=na_params, timeout=10)

            if r.status_code == 200:
                data = r.json()
                for art in data.get("articles", []):
                    title = art.get("title") or ""
                    source_name = (art.get("source") or {}).get("name") or ""
                    published = art.get("publishedAt") or ""

                    if not title or title == "[Removed]":
                        continue

                    try:
                        dt = datetime.fromisoformat(published.replace("Z", "+00:00"))
                        hours_old = _hours_old(dt)
                    except Exception:
                        hours_old = None

                    tier = _source_tier(source_name)

                    all_news.append({
                        "title": title,
                        "date": published,
                        "source": source_name,
                        "hours_old": hours_old,
                        "tier": tier,
                        "is_reliable": tier in (1, 2),
                        "provider": "newsapi",
                    })
            elif r.status_code == 429:
                print(f"   ⚠️  NewsAPI rate limit reached — using GNews only")
            elif r.status_code == 401:
                print(f"   ⚠️  NewsAPI key invalid")
        except Exception as e:
            print(f"   [newsapi fetch error] {e}")

    # ─── Merge + dedupe by title ───
    seen = set()
    unique_news = []
    for n in all_news:
        key = re.sub(r'[^\w\s]', '', n["title"].lower())[:60]
        if key not in seen:
            seen.add(key)
            unique_news.append(n)

    # ─── Recent filter ───
    recent = [n for n in unique_news if (n["hours_old"] or 999) <= max_age_hours]

    if not recent:
        print(f"   ⚠️  No news in last {max_age_hours}h, trying 7 days...")
        recent = [n for n in unique_news if (n["hours_old"] or 999) <= FALLBACK_AGE_HOURS]
        for n in recent:
            n["is_stale"] = True
    else:
        for n in recent:
            n["is_stale"] = False

    # ─── Sort: reliable first, then recent ───
    recent.sort(key=lambda x: (
        x.get("tier", 0) == 0,
        x.get("tier", 99),
        x.get("hours_old") or 999,
    ))

    return recent[:limit]


# ============================================================
#  SENTIMENT ANALYSIS
# ============================================================
def analyze_sentiment(news_items, symbol):
    if not news_items:
        return {
            "sentiment": "NEUTRAL",
            "score": 0,
            "summary": "Koi recent news nahi mili (last 48 hours mein).",
            "intraday_impact": "LOW",
        }

    lines = []
    for n in news_items:
        age = n.get("hours_old")
        age_str = f"{age:.1f}h ago" if age is not None else "unknown"
        tier = n.get("tier", 0)
        tier_str = f"T{tier}" if tier > 0 else "?"
        stale = " [STALE]" if n.get("is_stale") else ""
        prov = n.get("provider", "?")[:5]
        lines.append(f"- [{age_str}] [{tier_str}] [{prov}] ({n.get('source', 'unknown')}){stale} {n['title']}")

    headlines = "\n".join(lines)

    any_stale = any(n.get("is_stale") for n in news_items)
    stale_note = "\n⚠️ Some news are older than 48h — weight them LESS.\n" if any_stale else ""

    prompt = f"""You are a financial news analyst for INTRADAY trading in Indian markets.

Analyze sentiment for {symbol}.

HEADLINES (with age, source tier, provider):
{headlines}
{stale_note}

CRITICAL RULES:
1. Consider ONLY intraday-relevant news (impact within today)
2. Fresh news (< 24h) → high weight
3. Stale news (> 48h) → very low weight or ignore
4. STRUCTURAL news (long-term trends) → LOW intraday impact
5. EVENT news (RBI policy, earnings, big deals) → HIGH intraday impact
6. Tier 1 sources → highest trust, Tier 3 → lower trust
7. If news mixed or all stale → return NEUTRAL

SENTIMENT:
- POSITIVE: likely to push {symbol} UP today
- NEGATIVE: likely to push {symbol} DOWN today
- NEUTRAL: mixed, stale, or low impact

Return ONLY valid JSON (no markdown):
{{
  "sentiment": "POSITIVE" | "NEGATIVE" | "NEUTRAL",
  "score": -10 to +10,
  "summary": "one-line reason (mention recency & impact)",
  "intraday_impact": "HIGH" | "MEDIUM" | "LOW"
}}
"""

    raw = ask_groq(prompt)

    if not raw or "Error" in raw:
        return {
            "sentiment": "NEUTRAL",
            "score": 0,
            "summary": f"AI Error: {raw[:50] if raw else 'No response'}",
            "intraday_impact": "LOW",
        }

    match = re.search(r'\{[^{}]*\}', raw, re.DOTALL)
    if not match:
        match = re.search(r'\{.*\}', raw, re.DOTALL)

    if not match:
        return {
            "sentiment": "NEUTRAL",
            "score": 0,
            "summary": "JSON format fail.",
            "intraday_impact": "LOW",
        }

    try:
        result = json.loads(match.group(0))
        sentiment = str(result.get("sentiment", "NEUTRAL")).upper()
        if sentiment not in ("POSITIVE", "NEGATIVE", "NEUTRAL"):
            sentiment = "NEUTRAL"

        score = result.get("score", 0)
        try:
            score = int(score)
            score = max(-10, min(10, score))
        except (ValueError, TypeError):
            score = 0

        impact = str(result.get("intraday_impact", "MEDIUM")).upper()
        if impact not in ("HIGH", "MEDIUM", "LOW"):
            impact = "MEDIUM"

        return {
            "sentiment": sentiment,
            "score": score,
            "summary": result.get("summary", "No summary."),
            "intraday_impact": impact,
        }
    except Exception as e:
        return {
            "sentiment": "NEUTRAL",
            "score": 0,
            "summary": f"JSON parse fail: {e}",
            "intraday_impact": "LOW",
        }


# ============================================================
#  PUBLIC API
# ============================================================
def analyze_news(symbol, display_name=None):
    display = display_name or symbol
    symbol_upper = symbol.upper().strip()

    cache_key = f"news_{symbol_upper}"
    cached = _get_cached(cache_key)
    if cached:
        return cached

    query = _build_query(symbol_upper)
    news = fetch_news(query, limit=8)
    sentiment = analyze_sentiment(news, display)

    result = {
        "symbol": symbol,
        "news": news,
        "sentiment": sentiment,
    }

    _set_cached(cache_key, result)
    return result


# ============================================================
#  FORMATTER
# ============================================================
def format_news_report(news_data):
    if not news_data:
        return "⚠️ News data unavailable."

    sentiment = news_data.get("sentiment", {})
    label = sentiment.get("sentiment", "NEUTRAL")
    score = sentiment.get("score", 0)
    impact = sentiment.get("intraday_impact", "MEDIUM")
    summary = sentiment.get("summary", "")

    emoji = {"POSITIVE": "🟢", "NEGATIVE": "🔴", "NEUTRAL": "🟡"}.get(label, "⚪")

    lines = []
    lines.append(f"\n📰 NEWS CHECK: {emoji} {label} ({score:+d}) [Impact: {impact}]")
    lines.append(f"   {summary}")

    news_items = news_data.get("news", [])
    if news_items:
        lines.append("   ")
        lines.append("   Top headlines:")
        for n in news_items[:3]:
            age = n.get("hours_old")
            age_str = f"{age:.0f}h" if age is not None else "?"
            tier = n.get("tier", 0)
            tier_mark = "⭐" if tier == 1 else ("✓" if tier == 2 else "·")
            prov = n.get("provider", "")[:4]
            title = n.get("title", "")[:100]
            lines.append(f"   {tier_mark} [{age_str}] [{prov}] {title}")

    return "\n".join(lines)


# ============================================================
#  STANDALONE TEST
# ============================================================
if __name__ == "__main__":
    import sys
    symbol = sys.argv[1] if len(sys.argv) > 1 else "RELIANCE"

    print(f"\n{'=' * 60}\n  News Analysis — {symbol}\n{'=' * 60}")

    data = analyze_news(symbol)
    print(format_news_report(data))

    print(f"\n{'─' * 60}")
    print(f"  Total news: {len(data.get('news', []))}")

    # Show providers
    providers = {}
    for n in data.get("news", []):
        p = n.get("provider", "?")
        providers[p] = providers.get(p, 0) + 1
    print(f"  By provider: {providers}")
    print(f"{'─' * 60}\n")