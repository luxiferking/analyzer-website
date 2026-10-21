"""
core/trader_brain.py
Makes Jarvis think like a smart trader, not a calculator.

Instead of "score +5 = no trade", thinks:
- What's the market narrative?
- What scenarios are possible?
- What would a smart trader do?
"""
from datetime import datetime


def build_trader_view(
    symbol: str,
    spot_price: float,
    signals: dict,
    regime: dict,
    levels: dict,
    atm_data: dict = None,
) -> dict:
    """
    Build a trader-style reasoning about the market.
    
    Args:
        signals: dict of {SMC, Patterns, PCR, FII/DII, Global, News} 
                 each with {"score": -100 to +100, "raw": "..."}
        regime: output of detect_regime()
        levels: {"support": [], "resistance": [], "bsl": float, "ssl": float}
    
    Returns:
        {
            "narrative": str,
            "bull_case": [list],
            "bear_case": [list],
            "neutral_case": [list],
            "scenarios": [list of scenario dicts],
            "verdict": str,
            "intuition": str,
            "action_plan": [list],
        }
    """
    # ─── Extract signals ───
    smc = signals.get("SMC", {}).get("score", 0)
    patterns = signals.get("Patterns", {}).get("score", 0)
    pcr = signals.get("PCR", {}).get("score", 0)
    fii = signals.get("FII/DII", {}).get("score", 0)
    global_s = signals.get("Global", {}).get("score", 0)
    news = signals.get("News", {}).get("score", 0)

    # ─── Build Bull/Bear/Neutral cases ───
    bull_case = []
    bear_case = []
    neutral_case = []

    if smc >= 30:
        bull_case.append("SMC structure bullish")
    elif smc <= -30:
        bear_case.append("SMC structure weak (BOS breakdown, downtrend)")

    if patterns >= 30:
        bull_case.append("Candlestick patterns bullish")
    elif patterns <= -30:
        bear_case.append("Bearish candlestick patterns dominant")

    if pcr >= 30:
        bull_case.append("PCR shows contrarian bullish setup")
    elif pcr <= -30:
        bear_case.append("PCR extreme — overbought risk")

    if fii >= 30:
        bull_case.append("FII/DII net buyers")
    elif fii <= -30:
        bear_case.append("FII/DII selling")

    if global_s >= 30:
        bull_case.append("Global markets supportive")
    elif global_s <= -30:
        bear_case.append("Global markets weak")

    if news >= 30:
        bull_case.append("News sentiment positive")
    elif news <= -30:
        bear_case.append("News sentiment negative")

    if abs(smc) < 30: neutral_case.append("SMC neutral")
    if abs(patterns) < 30: neutral_case.append("Patterns mixed")
    if abs(pcr) < 30: neutral_case.append("PCR balanced")
    if abs(fii) < 30: neutral_case.append("FII/DII quiet")
    if abs(global_s) < 30: neutral_case.append("Global neutral")
    if abs(news) < 30: neutral_case.append("News mixed")

    # ─── Narrative ───
    regime_name = regime.get("regime", "UNKNOWN")
    bull_count = len(bull_case)
    bear_count = len(bear_case)

    if regime_name == "CHOPPY":
        narrative = ("Market is choppy — no clear direction. "
                     "Candles overlapping, wicks long. "
                     "Smart money is waiting for a trigger.")
    elif regime_name == "RANGING":
        narrative = (f"Market is range-bound. "
                     f"{bull_count} bullish signals vs {bear_count} bearish. "
                     f"Neither side has conviction.")
    elif "TRENDING" in regime_name:
        direction = "up" if "UP" in regime_name else "down"
        narrative = (f"Clear {direction}trend in place. "
                     f"Momentum favours {direction}side.")
    elif regime_name == "VOLATILE":
        narrative = "Volatility elevated — big swings both ways. Reduce size."
    else:
        narrative = (f"Market undecided. "
                     f"{bull_count} bull signals vs {bear_count} bear signals.")

    # ─── Scenario Planning ───
    scenarios = _build_scenarios(spot_price, signals, regime, levels, atm_data)

    # ─── Verdict ───
    verdict, intuition = _form_verdict(
        signals, regime, bull_count, bear_count, scenarios
    )

    # ─── Action Plan ───
    action_plan = _build_action_plan(levels, scenarios, regime)

    return {
        "narrative": narrative,
        "bull_case": bull_case,
        "bear_case": bear_case,
        "neutral_case": neutral_case,
        "scenarios": scenarios,
        "verdict": verdict,
        "intuition": intuition,
        "action_plan": action_plan,
    }


def _build_scenarios(spot, signals, regime, levels, atm_data):
    """Generate scenario tree (if/then)."""
    scenarios = []

    # Get key levels
    supports = levels.get("supports", [])
    resistances = levels.get("resistances", [])
    ssl = levels.get("ssl")
    bsl = levels.get("bsl")

    nearest_resistance = resistances[0]["price"] if resistances else spot * 1.005
    nearest_support = supports[0]["price"] if supports else spot * 0.995

    # ─── Bull scenario (always possible) ───
    regime_name = regime.get("regime", "")
    bull_prob = 25  # baseline
    if regime_name == "TRENDING_UP": bull_prob = 60
    elif regime_name == "TRENDING_DOWN": bull_prob = 10
    elif regime_name == "RANGING": bull_prob = 25
    elif regime_name == "CHOPPY": bull_prob = 20

    if True:  # Always add bull scenario

   # ─── Bear scenario (always possible) ───
     bear_prob = 25  # baseline
    if regime_name == "TRENDING_DOWN": bear_prob = 60
    elif regime_name == "TRENDING_UP": bear_prob = 10
    elif regime_name == "RANGING": bear_prob = 25
    elif regime_name == "CHOPPY": bear_prob = 20

    if True:  # Always add bear scenario

        scenarios.append({
            "name": "Bull Breakout",
            "direction": "UP",
            "probability": bull_prob,
            "trigger": f"Close above ₹{nearest_resistance:.0f} with volume",
            "action": "BUY CALL",
            "entry_level": nearest_resistance,
            "target": nearest_resistance + (nearest_resistance - nearest_support) * 0.5,
            "sl": nearest_support + (nearest_resistance - nearest_support) * 0.1,
            "reasoning": "Bullish breakout above resistance with momentum",
        })

    # ─── Bear scenario ───
    if regime.get("regime") in ["TRENDING_DOWN", "RANGING", "NEUTRAL", "CHOPPY"]:
        bear_prob = 30
        if regime.get("regime") == "TRENDING_DOWN": bear_prob = 60
        elif "UP" in regime.get("regime", ""): bear_prob = 15

        scenarios.append({
            "name": "Bear Breakdown",
            "direction": "DOWN",
            "probability": bear_prob,
            "trigger": f"Close below ₹{nearest_support:.0f} with volume",
            "action": "BUY PUT",
            "entry_level": nearest_support,
            "target": nearest_support - (nearest_resistance - nearest_support) * 0.5,
            "sl": nearest_resistance - (nearest_resistance - nearest_support) * 0.1,
            "reasoning": "Support breakdown with follow-through",
        })

    # ─── Range scenario ───
    if regime.get("regime") in ["RANGING", "CHOPPY"]:
        range_prob = 40 if regime.get("regime") == "RANGING" else 50
        scenarios.append({
            "name": "Range Continues",
            "direction": "SIDEWAYS",
            "probability": range_prob,
            "trigger": f"Price respects both ₹{nearest_support:.0f} and ₹{nearest_resistance:.0f}",
            "action": "NO TRADE",
            "entry_level": None,
            "target": None,
            "sl": None,
            "reasoning": "Range-bound — wait for breakout",
        })

    # Normalize probabilities
    total = sum(s["probability"] for s in scenarios)
    if total > 0:
        for s in scenarios:
            s["probability"] = round(s["probability"] / total * 100)

    scenarios.sort(key=lambda x: -x["probability"])
    return scenarios


def _form_verdict(signals, regime, bull_count, bear_count, scenarios):
    """Human-like verdict and intuition."""
    regime_name = regime.get("regime", "UNKNOWN")

    # Choppy — always wait
    if regime_name == "CHOPPY":
        return (
            "NO TRADE — Choppy market",
            "Charts look messy. Wicks everywhere, no clean impulse. "
            "Better to wait for clarity than get chopped up."
        )

    # Trending
    if "TRENDING_UP" in regime_name and bull_count >= 3:
        return (
            "BUY CALL — Trend is your friend",
            "Clean uptrend, bulls have control. Ride the momentum with tight SL."
        )
    if "TRENDING_DOWN" in regime_name and bear_count >= 3:
        return (
            "BUY PUT — Trend is your friend",
            "Clean downtrend, bears in control. Ride the momentum with tight SL."
        )

    # Ranging — fade extremes
    if regime_name == "RANGING":
        return (
            "NO TRADE — Wait for breakout",
            "Range-bound market. Neither side has conviction. "
            "Smart money waits for a break. So should you."
        )

    # Volatile — reduce size
    if regime_name == "VOLATILE":
        return (
            "SMALL SIZE only",
            "Big swings — either direction. Reduce position size significantly."
        )

    # Balanced
    if abs(bull_count - bear_count) <= 1:
        return (
            "NO TRADE — Balanced market",
            "Bull and bear cases equal. No edge. "
            "Missing a trade is better than losing one."
        )

    # Slight edge
    if bull_count > bear_count + 1:
        return (
            "WEAK BUY — Small edge",
            "Mild bullish tilt. Consider small CALL if breakout confirms."
        )
    if bear_count > bull_count + 1:
        return (
            "WEAK SELL — Small edge",
            "Mild bearish tilt. Consider small PUT if breakdown confirms."
        )

    return ("NO TRADE", "No clear edge.")


def _build_action_plan(levels, scenarios, regime):
    """Concrete action plan."""
    plan = []

    if regime.get("regime") == "CHOPPY":
        plan.append("⏸️  Stay out. Wait for choppiness to settle.")
        plan.append("👀 Watch for a strong impulse candle with volume")
        plan.append("⏰ Recheck in 1-2 hours")
        return plan

    if regime.get("regime") == "RANGING":
        supports = levels.get("supports", [])
        resistances = levels.get("resistances", [])

        if supports:
            plan.append(f"📉 Watch support at ₹{supports[0]['price']:.0f}")
        if resistances:
            plan.append(f"📈 Watch resistance at ₹{resistances[0]['price']:.0f}")

        plan.append("🎯 Enter ONLY on confirmed breakout (close + volume)")
        plan.append("⏰ Recheck every 30 min for range break")
        plan.append("🛑 Stay out if range continues")

    elif "TRENDING" in regime.get("regime", ""):
        plan.append("📈 Trade with trend, not against it")
        plan.append("🎯 Enter on pullbacks, not breakouts")
        plan.append("🛡️ Trail SL as price moves in your favour")
        plan.append("📊 Exit at next major S/R level")

    return plan


def format_trader_view(view: dict) -> str:
    """Pretty print trader's view."""
    lines = []
    lines.append("\n" + "═" * 65)
    lines.append("  🧠 JARVIS TRADER'S VIEW")
    lines.append("═" * 65)

    lines.append(f"\n📖 NARRATIVE:\n   {view['narrative']}")

    if view["bull_case"] or view["bear_case"]:
        lines.append(f"\n🎭 SIGNAL DEBATE:")
        if view["bull_case"]:
            lines.append(f"   🟢 BULLS SAY ({len(view['bull_case'])} signals):")
            for b in view["bull_case"]:
                lines.append(f"      • {b}")
        if view["bear_case"]:
            lines.append(f"   🔴 BEARS SAY ({len(view['bear_case'])} signals):")
            for b in view["bear_case"]:
                lines.append(f"      • {b}")
        if view["neutral_case"]:
            lines.append(f"   🟡 UNDECIDED ({len(view['neutral_case'])} signals):")
            for n in view["neutral_case"][:3]:
                lines.append(f"      • {n}")

    if view["scenarios"]:
        lines.append(f"\n📈 SCENARIO TREE:")
        for i, s in enumerate(view["scenarios"], 1):
            emoji = {"UP": "⬆️", "DOWN": "⬇️", "SIDEWAYS": "↔️"}.get(s["direction"], "❓")
            lines.append(f"\n   Scenario {chr(64+i)}: {s['name']} {emoji} ({s['probability']}%)")
            lines.append(f"      Trigger: {s['trigger']}")
            if s.get("action") and s["action"] != "NO TRADE":
                lines.append(f"      Action:  {s['action']}")
                lines.append(f"      Entry:   ₹{s['entry_level']:.0f}")
                lines.append(f"      Target:  ₹{s['target']:.0f}")
                lines.append(f"      SL:      ₹{s['sl']:.0f}")
            else:
                lines.append(f"      Action:  {s['action']}")

    lines.append(f"\n🎯 VERDICT:\n   {view['verdict']}")
    lines.append(f"\n💡 TRADER'S INTUITION:\n   \"{view['intuition']}\"")

    if view["action_plan"]:
        lines.append(f"\n📋 ACTION PLAN:")
        for a in view["action_plan"]:
            lines.append(f"   {a}")

    lines.append("\n" + "═" * 65)
    return "\n".join(lines)


if __name__ == "__main__":
    # Test with mock data
    mock_signals = {
        "SMC":       {"score": -29, "raw": "Bull 1 / Bear 3"},
        "Patterns":  {"score": -28, "raw": "Bull 1.7 / Bear 3.0"},
        "PCR":       {"score": +30, "raw": "PCR 1.04 mild bullish"},
        "FII/DII":   {"score": +70, "raw": "DII buying"},
        "Global":    {"score": 0,   "raw": "Neutral"},
        "News":      {"score": +50, "raw": "Positive"},
    }
    mock_regime = {
        "regime": "CHOPPY",
        "confidence": 75,
        "tradeable": False,
        "characteristics": ["Choppiness Index: 65 (high)", "Overlap: 80%"],
    }
    mock_levels = {
        "supports": [{"price": 22400}, {"price": 22300}],
        "resistances": [{"price": 22600}, {"price": 22700}],
        "bsl": 22600,
        "ssl": 22400,
    }

    view = build_trader_view("NIFTY", 22515, mock_signals, mock_regime, mock_levels)
    print(format_trader_view(view))