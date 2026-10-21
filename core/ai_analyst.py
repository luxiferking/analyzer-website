import os
import json
import requests
from datetime import datetime


def ask_groq(prompt, model="openai/gpt-oss-20b", max_tokens=6000):
    """Send prompt to Groq. Higher max_tokens to accommodate reasoning models."""
    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        return None

    url = "https://api.groq.com/openai/v1/chat/completions"
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.1,
        "max_tokens": max_tokens,
    }
    try:
        r = requests.post(url, headers=headers, json=payload, timeout=180)
        if r.status_code != 200:
            return None
        data = r.json()
        content = data["choices"][0]["message"]["content"]
        if not content or not content.strip():
            return None
        return content
    except Exception:
        return None


def analyze_candidates(candidates, nifty_change, capital=100000, top_n=3):
    """Feed top candidates to AI for deep analysis and single best pick."""
    if not candidates:
        return None

    top = candidates[:top_n]

    # Build a COMPACT summary to reduce prompt length
    compact = []
    for c in top:
        compact.append(
            f"{c['symbol']}: price={c['price']}, chg={c['change_pct']}%, "
            f"vol={c['vol_ratio']}x, RS={c['relative_strength']}%, "
            f"range={c['range_pct']}%, score={c['score']}"
        )
    compact_text = "\n".join(compact)

    prompt = f"""You are an intraday trader. Nifty 50: {nifty_change:+.2f}%

Top {len(top)} candidates from our scanner:
{compact_text}

Pick the SINGLE BEST stock to trade today and give exact prices.
Rules:
- Only recommend if momentum + volume are strong
- Entry near current price
- Stop loss = 1.5% from entry
- Target = 2x the risk (1:2 R:R)
- Capital Rs.{capital:,}, risk 1% per trade

Return ONLY valid JSON, no markdown:
{{"recommendation":{{"action":"BUY","symbol":"MPHASIS","entry_price":2250.20,"stop_loss":2216.45,"target_price":2317.70,"confidence":8,"position_size":15,"reason":"Clear breakout on 2.7x volume with strong relative strength.","risks":"If Nifty reverses, momentum may fade."}},"other_picks":[{{"symbol":"SBILIFE","action":"BUY","confidence":7,"reason":"Strong volume surge"}}]}}"""

    response = ask_groq(prompt, max_tokens=6000)
    if not response:
        return {"error": "AI returned no content (reasoning tokens exceeded limit)"}

    # Clean response
    text = response.strip()
    if text.startswith("```"):
        parts = text.split("```")
        if len(parts) > 1:
            text = parts[1]
            if text.startswith("json"):
                text = text[4:]
    text = text.strip()

    # Find JSON boundaries
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1:
        return {"error": "No JSON found in response", "raw": text[:300]}

    json_str = text[start:end+1]

    try:
        return json.loads(json_str)
    except json.JSONDecodeError as e:
        return {"error": f"JSON parse failed: {str(e)[:80]}", "raw": json_str[:300]}


def print_ai_analysis(analysis):
    """Pretty print the AI recommendation."""
    if not analysis:
        print("\n  AI analysis unavailable.")
        return

    if "error" in analysis:
        print(f"\n  ⚠️ AI Error: {analysis['error']}")
        if "raw" in analysis:
            print(f"  Raw snippet: {analysis['raw'][:200]}")
        return

    rec = analysis.get("recommendation", {})
    if not rec:
        print("\n  ⚠️ AI returned no recommendation.")
        return

    action = rec.get("action", "NO TRADE")

    print("\n" + "=" * 80)
    print("  🎯 JARVIS AI FINAL RECOMMENDATION")
    print("=" * 80)

    if action == "NO TRADE":
        print(f"\n  ❌ NO TRADE TODAY")
        print(f"  Reason: {rec.get('reason', 'N/A')}")
        print("=" * 80)
        return

    print(f"\n  ✅ {action}: {rec.get('symbol', 'N/A')}")
    print(f"  ────────────────────────────────────────────────────────")
    print(f"  Entry Price:    Rs. {rec.get('entry_price', 0)}")
    print(f"  Stop Loss:      Rs. {rec.get('stop_loss', 0)}")
    print(f"  Target Price:   Rs. {rec.get('target_price', 0)}")
    print(f"  Confidence:     {rec.get('confidence', 0)}/10")
    print(f"  Position Size:  {rec.get('position_size', 0)} shares")
    print(f"  ────────────────────────────────────────────────────────")
    print(f"  WHY THIS STOCK:")
    print(f"  {rec.get('reason', '')}")
    print(f"  ────────────────────────────────────────────────────────")
    print(f"  RISKS TO WATCH:")
    print(f"  {rec.get('risks', '')}")
    print("=" * 80)

    others = analysis.get("other_picks", [])
    if others:
        print("\n  OTHER CANDIDATES:")
        for o in others:
            print(f"  • {o.get('symbol', 'N/A'):<12} "
                  f"{o.get('action', 'SKIP'):<6} "
                  f"Confidence {o.get('confidence', 0)}/10")
            print(f"    {o.get('reason', '')[:90]}")
        print("=" * 80)