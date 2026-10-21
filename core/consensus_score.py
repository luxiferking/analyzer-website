"""
core/consensus_score.py
Weighted consensus scoring engine.

Combines 6 signals into a single -100 to +100 score:
  • SMC (30%)       — chart structure, most precise
  • Patterns (20%)  — mathematical candlestick analysis
  • PCR (15%)       — option writers' positioning
  • FII/DII (15%)   — institutional money flow
  • Global (10%)    — US/Asian market context
  • News (10%)      — headlines (noisy, lowest weight)

Only trades when |score| >= 50.
"""

# ─── Signal Weights (sum = 100) ───
WEIGHTS = {
    "SMC":       30,
    "Patterns":  20,
    "PCR":       15,
    "FII/DII":   15,
    "Global":    10,
    "News":      10,
}

# ─── Thresholds ───
STRONG_THRESHOLD = 70   # |score| >= 70 → strong signal
WEAK_THRESHOLD   = 50   # |score| >= 50 → weak signal (allow)
                        # |score| < 50 → NO TRADE


def _smc_score(bull, bear):
    """SMC score: -100 to +100. Uses 7 as max checks."""
    total = bull + bear
    if total == 0:
        return 0, "No SMC signals"
    score = ((bull - bear) / 7.0) * 100
    score = max(-100, min(100, score))
    return score, f"Bull {bull} / Bear {bear}"


def _pattern_score(bull, bear):
    """Pattern score: -100 to +100."""
    total = bull + bear
    if total == 0:
        return 0, "No patterns detected"
    diff = bull - bear
    score = (diff / max(total, 3.0)) * 100
    score = max(-100, min(100, score))
    return score, f"Bull {bull:.1f} / Bear {bear:.1f}"


def _pcr_score(pcr):
    """PCR contrarian: high PCR = fear (bullish), low PCR = greed (bearish)."""
    if not pcr or pcr <= 0:
        return 0, "PCR unknown"
    if pcr >= 1.5:
        return 100, f"PCR {pcr} → extreme fear (bullish)"
    if pcr >= 1.2:
        return 80, f"PCR {pcr} → oversold (bullish)"
    if pcr >= 1.0:
        return 30, f"PCR {pcr} → mild bullish bias"
    if pcr >= 0.85:
        return -20, f"PCR {pcr} → mild bearish bias"
    if pcr >= 0.7:
        return -60, f"PCR {pcr} → overbought (bearish)"
    return -100, f"PCR {pcr} → extreme greed (bearish)"


def _fii_dii_score(fii_bias):
    b = (fii_bias or "").upper()
    if "STRONG" in b and "BUY" in b:
        return 100, b
    if "STRONG" in b and "SELL" in b:
        return -100, b
    if "BUY" in b or "BULLISH" in b:
        return 70, b
    if "SELL" in b or "BEARISH" in b:
        return -70, b
    return 0, b or "Neutral"


def _global_score(global_bias):
    b = (global_bias or "").upper()
    if "STRONG" in b and "BULL" in b:
        return 100, b
    if "STRONG" in b and "BEAR" in b:
        return -100, b
    if "BULL" in b:
        return 70, b
    if "BEAR" in b:
        return -70, b
    return 0, b or "Neutral"


def _news_score(news_sentiment, news_score):
    """News score: -100 to +100 (news_score is -10 to +10)."""
    s = int(news_score or 0) * 10
    s = max(-100, min(100, s))
    return s, f"{news_sentiment} ({news_score}/10)"


def compute_consensus(
    smc_bull=0, smc_bear=0,
    pat_bull=0, pat_bear=0,
    pcr=0,
    fii_bias="NEUTRAL",
    global_bias="NEUTRAL",
    news_sentiment="NEUTRAL", news_score=0,
):
    """Compute weighted consensus score."""
    signals = {}

    s, raw = _smc_score(smc_bull, smc_bear)
    signals["SMC"] = {"score": s, "weight": WEIGHTS["SMC"], "raw": raw}

    s, raw = _pattern_score(pat_bull, pat_bear)
    signals["Patterns"] = {"score": s, "weight": WEIGHTS["Patterns"], "raw": raw}

    s, raw = _pcr_score(pcr)
    signals["PCR"] = {"score": s, "weight": WEIGHTS["PCR"], "raw": raw}

    s, raw = _fii_dii_score(fii_bias)
    signals["FII/DII"] = {"score": s, "weight": WEIGHTS["FII/DII"], "raw": raw}

    s, raw = _global_score(global_bias)
    signals["Global"] = {"score": s, "weight": WEIGHTS["Global"], "raw": raw}

    s, raw = _news_score(news_sentiment, news_score)
    signals["News"] = {"score": s, "weight": WEIGHTS["News"], "raw": raw}

    # Weighted sum
    total_weight = sum(v["weight"] for v in signals.values())
    weighted_sum = sum(v["score"] * v["weight"] for v in signals.values())
    final_score = weighted_sum / total_weight if total_weight > 0 else 0

    # Classify
    if final_score >= STRONG_THRESHOLD:
        classification, direction, emoji, status = "STRONG BULLISH", "BUY CALL", "🟢", "STRONG"
    elif final_score >= WEAK_THRESHOLD:
        classification, direction, emoji, status = "WEAK BULLISH", "BUY CALL", "🟡", "WEAK"
    elif final_score <= -STRONG_THRESHOLD:
        classification, direction, emoji, status = "STRONG BEARISH", "BUY PUT", "🔴", "STRONG"
    elif final_score <= -WEAK_THRESHOLD:
        classification, direction, emoji, status = "WEAK BEARISH", "BUY PUT", "🟡", "WEAK"
    else:
        classification, direction, emoji, status = "NEUTRAL / CONFLICT", "NO TRADE", "⚪", "NO_TRADE"

    return {
        "signals": signals,
        "final_score": round(final_score, 1),
        "classification": classification,
        "direction": direction,
        "emoji": emoji,
        "status": status,
        "threshold_met": abs(final_score) >= WEAK_THRESHOLD,
        "strong_signal": abs(final_score) >= STRONG_THRESHOLD,
    }


def format_consensus(cs):
    """Pretty format for display."""
    if not cs:
        return "⚠️ Consensus unavailable."

    lines = []
    lines.append("📊 WEIGHTED CONSENSUS SCORE")
    lines.append("─" * 62)
    lines.append(f"  {'Signal':<12} {'Score':>7}  {'Wt':>5}  {'Contrib':>9}")
    lines.append("─" * 62)

    for name, s in cs["signals"].items():
        score = s["score"]
        wt = s["weight"]
        contrib = score * wt / 100

        if score >= 25:
            emoji = "🟢"
        elif score <= -25:
            emoji = "🔴"
        else:
            emoji = "🟡"

        lines.append(f"  {emoji} {name:<10} {score:>+6.0f}  {wt:>4}%  {contrib:>+7.1f}")
        lines.append(f"       └ {s['raw']}")

    lines.append("─" * 62)
    lines.append(f"  {'NET SCORE':<18} {cs['final_score']:>+6.1f} / 100")
    lines.append(f"  {'Classification':<18} {cs['emoji']} {cs['classification']}")
    lines.append(f"  {'Direction':<18} {cs['direction']}")

    if not cs["threshold_met"]:
        lines.append("  ⚠️ Below threshold (need |score| ≥ 50) → NO TRADE")
    elif cs["strong_signal"]:
        lines.append("  ✅ Strong signal (|score| ≥ 70)")
    else:
        lines.append("  ⚠️ Weak signal — consider smaller size")

    lines.append("─" * 62)
    return "\n".join(lines)


# ─── Test ───
if __name__ == "__main__":
    test_cases = [
        {
            "name": "Strong Bearish (aligned)",
            "args": dict(smc_bull=1, smc_bear=5, pat_bull=1, pat_bear=3,
                         pcr=0.85, fii_bias="NET SELLING",
                         global_bias="BEARISH", news_sentiment="NEGATIVE", news_score=-5),
        },
        {
            "name": "Strong Bullish (aligned)",
            "args": dict(smc_bull=5, smc_bear=1, pat_bull=3, pat_bear=1,
                         pcr=1.3, fii_bias="NET BUYING",
                         global_bias="BULLISH", news_sentiment="POSITIVE", news_score=6),
        },
        {
            "name": "Mixed / Conflict",
            "args": dict(smc_bull=1, smc_bear=3, pat_bull=1.25, pat_bear=2.5,
                         pcr=0.856, fii_bias="NEUTRAL",
                         global_bias="BULLISH", news_sentiment="POSITIVE", news_score=7),
        },
    ]

    for tc in test_cases:
        print(f"\n{'=' * 62}")
        print(f"  Test: {tc['name']}")
        print(f"{'=' * 62}")
        cs = compute_consensus(**tc["args"])
        print(format_consensus(cs))