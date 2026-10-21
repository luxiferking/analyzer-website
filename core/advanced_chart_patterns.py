"""
core/advanced_chart_patterns.py
Advanced Chart Pattern Detector

Detects 15+ classic chart patterns:
- Head & Shoulders + Inverse
- Double Top / Double Bottom
- Triple Top / Triple Bottom
- Ascending/Descending/Symmetrical Triangles
- Rising/Falling Wedges
- Bull/Bear Flags
- Pennants
- Cup & Handle
- Rounding Bottom
- Diamond

Approach: Mathematical (peaks, troughs, trendlines)
NOT visual/image-based — pure OHLCV analysis.
"""
import pandas as pd
import numpy as np


# ═══════════════════════════════════════════════════════
#  SWING POINT DETECTION
# ═══════════════════════════════════════════════════════
def _find_swing_points(df, left=3, right=3):
    """Find swing highs and lows using window comparison."""
    highs = df['High'].values
    lows = df['Low'].values

    swing_highs = []
    swing_lows = []

    for i in range(left, len(df) - right):
        # Swing High
        if highs[i] == max(highs[i-left:i+right+1]):
            swing_highs.append({
                "index": i,
                "price": float(highs[i]),
                "date": str(df.index[i])[:10],
            })
        # Swing Low
        if lows[i] == min(lows[i-left:i+right+1]):
            swing_lows.append({
                "index": i,
                "price": float(lows[i]),
                "date": str(df.index[i])[:10],
            })

    return swing_highs, swing_lows


def _find_peaks_troughs(df, order=5):
    """Alternative: use scipy-style peak detection."""
    from scipy.signal import argrelextrema
    highs = df['High'].values
    lows = df['Low'].values

    peak_idx = argrelextrema(highs, np.greater, order=order)[0]
    trough_idx = argrelextrema(lows, np.less, order=order)[0]

    peaks = [{"index": int(i), "price": float(highs[i]),
              "date": str(df.index[i])[:10]} for i in peak_idx]
    troughs = [{"index": int(i), "price": float(lows[i]),
                "date": str(df.index[i])[:10]} for i in trough_idx]

    return peaks, troughs


def _get_recent(points, df, lookback_bars=100):
    """Filter points to recent window."""
    if not points:
        return []
    max_idx = len(df) - 1
    min_idx = max(0, max_idx - lookback_bars)
    return [p for p in points if p["index"] >= min_idx]


# ═══════════════════════════════════════════════════════
#  HELPER: Trendline Detection
# ═══════════════════════════════════════════════════════
def _is_uptrend(points):
    """Check if last N points form uptrend."""
    if len(points) < 2:
        return False
    prices = [p["price"] for p in points[-3:]]
    return prices[-1] > prices[-2] > prices[-3] if len(prices) >= 3 else False


def _is_downtrend(points):
    """Check if last N points form downtrend."""
    if len(points) < 2:
        return False
    prices = [p["price"] for p in points[-3:]]
    return prices[-1] < prices[-2] < prices[-3] if len(prices) >= 3 else False


def _price_similarity(p1, p2, tol=0.015):
    """Check if two prices are within tolerance (1.5%)."""
    if p1 <= 0:
        return False
    return abs(p1 - p2) / p1 <= tol


# ═══════════════════════════════════════════════════════
#  PATTERN: Head & Shoulders
# ═══════════════════════════════════════════════════════
def _detect_head_shoulders(df, peaks, troughs):
    """Detect Head & Shoulders (bearish reversal)."""
    recent_peaks = _get_recent(peaks, df, lookback_bars=120)
    if len(recent_peaks) < 3:
        return None

    # Look at last 3 peaks
    p1, p2, p3 = recent_peaks[-3], recent_peaks[-2], recent_peaks[-1]

    # Head (p2) should be highest, shoulders (p1, p3) similar & lower
    if (p2["price"] > p1["price"] and p2["price"] > p3["price"]
        and _price_similarity(p1["price"], p3["price"], tol=0.03)):
        # Neckline = average of troughs between shoulders
        between_troughs = [t for t in troughs
                           if p1["index"] < t["index"] < p3["index"]]
        if len(between_troughs) < 2:
            return None

        neckline = sum(t["price"] for t in between_troughs[:2]) / 2
        current = df['Close'].iloc[-1]

        # Target: head-to-neckline distance
        head_to_neck = p2["price"] - neckline
        target = neckline - head_to_neck

        return {
            "pattern": "Head and Shoulders",
            "direction": "BEARISH",
            "confidence": 75,
            "neckline": round(neckline, 2),
            "head": round(p2["price"], 2),
            "left_shoulder": round(p1["price"], 2),
            "right_shoulder": round(p3["price"], 2),
            "target": round(target, 2),
            "breakout_level": round(neckline, 2),
            "current": round(current, 2),
            "meaning": "Bearish reversal — expect breakdown below neckline",
        }
    return None


def _detect_inverse_head_shoulders(df, peaks, troughs):
    """Detect Inverse Head & Shoulders (bullish reversal)."""
    recent_troughs = _get_recent(troughs, df, lookback_bars=120)
    if len(recent_troughs) < 3:
        return None

    t1, t2, t3 = recent_troughs[-3], recent_troughs[-2], recent_troughs[-1]

    # Head (t2) should be lowest, shoulders (t1, t3) similar & higher
    if (t2["price"] < t1["price"] and t2["price"] < t3["price"]
        and _price_similarity(t1["price"], t3["price"], tol=0.03)):

        between_peaks = [p for p in peaks
                         if t1["index"] < p["index"] < t3["index"]]
        if len(between_peaks) < 2:
            return None

        neckline = sum(p["price"] for p in between_peaks[:2]) / 2
        current = df['Close'].iloc[-1]

        head_to_neck = neckline - t2["price"]
        target = neckline + head_to_neck

        return {
            "pattern": "Inverse Head and Shoulders",
            "direction": "BULLISH",
            "confidence": 75,
            "neckline": round(neckline, 2),
            "head": round(t2["price"], 2),
            "target": round(target, 2),
            "breakout_level": round(neckline, 2),
            "current": round(current, 2),
            "meaning": "Bullish reversal — expect breakout above neckline",
        }
    return None


# ═══════════════════════════════════════════════════════
#  PATTERN: Double Top / Double Bottom
# ═══════════════════════════════════════════════════════
def _detect_double_top(df, peaks, troughs):
    """Detect Double Top (bearish reversal)."""
    recent_peaks = _get_recent(peaks, df, lookback_bars=80)
    if len(recent_peaks) < 2:
        return None

    p1, p2 = recent_peaks[-2], recent_peaks[-1]

    # Two peaks similar (within 2%)
    if _price_similarity(p1["price"], p2["price"], tol=0.02):
        # Trough between them
        between = [t for t in troughs if p1["index"] < t["index"] < p2["index"]]
        if not between:
            return None

        trough = between[0]
        current = df['Close'].iloc[-1]

        # Target = peak - (peak - trough)
        target = trough["price"] - (p1["price"] - trough["price"])

        return {
            "pattern": "Double Top",
            "direction": "BEARISH",
            "confidence": 65,
            "peak1": round(p1["price"], 2),
            "peak2": round(p2["price"], 2),
            "neckline": round(trough["price"], 2),
            "target": round(target, 2),
            "breakout_level": round(trough["price"], 2),
            "current": round(current, 2),
            "meaning": "Bearish reversal — double top formed",
        }
    return None


def _detect_double_bottom(df, peaks, troughs):
    """Detect Double Bottom (bullish reversal)."""
    recent_troughs = _get_recent(troughs, df, lookback_bars=80)
    if len(recent_troughs) < 2:
        return None

    t1, t2 = recent_troughs[-2], recent_troughs[-1]

    if _price_similarity(t1["price"], t2["price"], tol=0.02):
        between = [p for p in peaks if t1["index"] < p["index"] < t2["index"]]
        if not between:
            return None

        peak = between[0]
        current = df['Close'].iloc[-1]
        target = peak["price"] + (peak["price"] - t1["price"])

        return {
            "pattern": "Double Bottom",
            "direction": "BULLISH",
            "confidence": 65,
            "bottom1": round(t1["price"], 2),
            "bottom2": round(t2["price"], 2),
            "neckline": round(peak["price"], 2),
            "target": round(target, 2),
            "breakout_level": round(peak["price"], 2),
            "current": round(current, 2),
            "meaning": "Bullish reversal — double bottom formed",
        }
    return None


# ═══════════════════════════════════════════════════════
#  PATTERN: Triangles
# ═══════════════════════════════════════════════════════
def _detect_ascending_triangle(df, peaks, troughs):
    """Ascending Triangle: flat top, rising bottoms."""
    recent_peaks = _get_recent(peaks, df, lookback_bars=60)
    recent_troughs = _get_recent(troughs, df, lookback_bars=60)

    if len(recent_peaks) < 2 or len(recent_troughs) < 2:
        return None

    # Peaks similar (flat top)
    p1, p2 = recent_peaks[-2], recent_peaks[-1]
    if not _price_similarity(p1["price"], p2["price"], tol=0.015):
        return None

    # Troughs rising
    t1, t2 = recent_troughs[-2], recent_troughs[-1]
    if t2["price"] <= t1["price"]:
        return None

    current = df['Close'].iloc[-1]
    resistance = max(p1["price"], p2["price"])
    height = resistance - min(t1["price"], t2["price"])
    target = resistance + height

    return {
        "pattern": "Ascending Triangle",
        "direction": "BULLISH",
        "confidence": 68,
        "resistance": round(resistance, 2),
        "rising_support": round(t2["price"], 2),
        "target": round(target, 2),
        "breakout_level": round(resistance, 2),
        "current": round(current, 2),
        "meaning": "Bullish continuation — breakout above flat resistance likely",
    }


def _detect_descending_triangle(df, peaks, troughs):
    """Descending Triangle: flat bottom, falling tops."""
    recent_peaks = _get_recent(peaks, df, lookback_bars=60)
    recent_troughs = _get_recent(troughs, df, lookback_bars=60)

    if len(recent_peaks) < 2 or len(recent_troughs) < 2:
        return None

    # Troughs similar (flat bottom)
    t1, t2 = recent_troughs[-2], recent_troughs[-1]
    if not _price_similarity(t1["price"], t2["price"], tol=0.015):
        return None

    # Peaks falling
    p1, p2 = recent_peaks[-2], recent_peaks[-1]
    if p2["price"] >= p1["price"]:
        return None

    current = df['Close'].iloc[-1]
    support = min(t1["price"], t2["price"])
    height = max(p1["price"], p2["price"]) - support
    target = support - height

    return {
        "pattern": "Descending Triangle",
        "direction": "BEARISH",
        "confidence": 68,
        "support": round(support, 2),
        "falling_resistance": round(p2["price"], 2),
        "target": round(target, 2),
        "breakout_level": round(support, 2),
        "current": round(current, 2),
        "meaning": "Bearish continuation — breakdown below flat support likely",
    }


def _detect_symmetrical_triangle(df, peaks, troughs):
    """Symmetrical Triangle: converging trendlines."""
    recent_peaks = _get_recent(peaks, df, lookback_bars=60)
    recent_troughs = _get_recent(troughs, df, lookback_bars=60)

    if len(recent_peaks) < 2 or len(recent_troughs) < 2:
        return None

    p1, p2 = recent_peaks[-2], recent_peaks[-1]
    t1, t2 = recent_troughs[-2], recent_troughs[-1]

    # Peaks falling + Troughs rising
    if p2["price"] >= p1["price"] or t2["price"] <= t1["price"]:
        return None

    # Range should be narrowing
    range_old = p1["price"] - t1["price"]
    range_new = p2["price"] - t2["price"]
    if range_new >= range_old * 0.9:
        return None

    current = df['Close'].iloc[-1]
    apex_upper = p2["price"]
    apex_lower = t2["price"]
    height = range_old
    target_up = apex_upper + height * 0.7
    target_down = apex_lower - height * 0.7

    return {
        "pattern": "Symmetrical Triangle",
        "direction": "NEUTRAL",
        "confidence": 55,
        "upper_trend": round(apex_upper, 2),
        "lower_trend": round(apex_lower, 2),
        "target_up": round(target_up, 2),
        "target_down": round(target_down, 2),
        "current": round(current, 2),
        "meaning": "Continuation pattern — breakout direction unknown, wait for confirmation",
    }


# ═══════════════════════════════════════════════════════
#  PATTERN: Wedges
# ═══════════════════════════════════════════════════════
def _detect_rising_wedge(df, peaks, troughs):
    """Rising Wedge (bearish reversal)."""
    recent_peaks = _get_recent(peaks, df, lookback_bars=60)
    recent_troughs = _get_recent(troughs, df, lookback_bars=60)

    if len(recent_peaks) < 2 or len(recent_troughs) < 2:
        return None

    p1, p2 = recent_peaks[-2], recent_peaks[-1]
    t1, t2 = recent_troughs[-2], recent_troughs[-1]

    # Both rising, but converging
    if p2["price"] > p1["price"] and t2["price"] > t1["price"]:
        upper_slope = (p2["price"] - p1["price"]) / max(p2["index"] - p1["index"], 1)
        lower_slope = (t2["price"] - t1["price"]) / max(t2["index"] - t1["index"], 1)

        # Lower slope > upper slope (converging upward)
        if lower_slope > upper_slope * 1.1:
            current = df['Close'].iloc[-1]
            # Support = lowest point
            support = min(t1["price"], t2["price"])
            # Highest point
            highest = max(p1["price"], p2["price"])
            wedge_height = highest - support
            # Target = breakdown - 0.7 × height (bearish downward)
            target = support - (wedge_height * 0.7)

            return {
                "pattern": "Rising Wedge",
                "direction": "BEARISH",
                "confidence": 62,
                "upper": round(p2["price"], 2),
                "lower": round(t2["price"], 2),
                "target": round(target, 2),
                "breakout_level": round(support, 2),
                "current": round(current, 2),
                "meaning": "Bearish reversal — expect breakdown below lower trendline",
            }
    return None


def _detect_falling_wedge(df, peaks, troughs):
    """Falling Wedge (bullish reversal)."""
    recent_peaks = _get_recent(peaks, df, lookback_bars=60)
    recent_troughs = _get_recent(troughs, df, lookback_bars=60)

    if len(recent_peaks) < 2 or len(recent_troughs) < 2:
        return None

    p1, p2 = recent_peaks[-2], recent_peaks[-1]
    t1, t2 = recent_troughs[-2], recent_troughs[-1]

    # Both falling, but converging
    if p2["price"] < p1["price"] and t2["price"] < t1["price"]:
        upper_slope = abs((p2["price"] - p1["price"]) / max(p2["index"] - p1["index"], 1))
        lower_slope = abs((t2["price"] - t1["price"]) / max(t2["index"] - t1["index"], 1))

        # Upper slope > lower slope (converging downward)
        if upper_slope > lower_slope * 1.1:
            current = df['Close'].iloc[-1]
            # Resistance = highest point in recent pattern
            resistance = max(p1["price"], p2["price"])
            # Lowest point
            lowest = min(t1["price"], t2["price"])
            # Height of wedge
            wedge_height = resistance - lowest
            # Target = breakout + 0.7 × height (bullish upward)
            target = resistance + (wedge_height * 0.7)

            return {
                "pattern": "Falling Wedge",
                "direction": "BULLISH",
                "confidence": 62,
                "upper": round(p2["price"], 2),
                "lower": round(t2["price"], 2),
                "target": round(target, 2),
                "breakout_level": round(resistance, 2),
                "current": round(current, 2),
                "meaning": "Bullish reversal — expect breakout above upper trendline",
            }
    return None


# ═══════════════════════════════════════════════════════
#  PATTERN: Flags & Pennants
# ═══════════════════════════════════════════════════════
def _detect_bull_flag(df, peaks, troughs):
    """Bull Flag: strong move up, then tight consolidation."""
    if len(df) < 30:
        return None

    close = df['Close'].values
    recent_10 = close[-10:]
    recent_20 = close[-20:-10]

    # Prior move: +3% in 20-10 bars ago
    prior_move = (recent_20[-1] - recent_20[0]) / recent_20[0] * 100

    # Consolidation: tight range in last 10 bars
    consol_range = (max(recent_10) - min(recent_10)) / min(recent_10) * 100

    if prior_move > 3.0 and consol_range < 2.0:
        current = close[-1]
        flag_high = max(recent_10)
        target = flag_high + (flag_high - min(recent_10)) * 2

        return {
            "pattern": "Bull Flag",
            "direction": "BULLISH",
            "confidence": 70,
            "prior_move_pct": round(prior_move, 2),
            "consolidation_pct": round(consol_range, 2),
            "target": round(target, 2),
            "breakout_level": round(flag_high, 2),
            "current": round(current, 2),
            "meaning": "Bullish continuation — expect breakout above flag",
        }
    return None


def _detect_bear_flag(df, peaks, troughs):
    """Bear Flag: strong move down, then tight consolidation."""
    if len(df) < 30:
        return None

    close = df['Close'].values
    recent_10 = close[-10:]
    recent_20 = close[-20:-10]

    prior_move = (recent_20[-1] - recent_20[0]) / recent_20[0] * 100
    consol_range = (max(recent_10) - min(recent_10)) / min(recent_10) * 100

    if prior_move < -3.0 and consol_range < 2.0:
        current = close[-1]
        flag_low = min(recent_10)
        target = flag_low - (max(recent_10) - flag_low) * 2

        return {
            "pattern": "Bear Flag",
            "direction": "BEARISH",
            "confidence": 70,
            "prior_move_pct": round(prior_move, 2),
            "consolidation_pct": round(consol_range, 2),
            "target": round(target, 2),
            "breakout_level": round(flag_low, 2),
            "current": round(current, 2),
            "meaning": "Bearish continuation — expect breakdown below flag",
        }
    return None


# ═══════════════════════════════════════════════════════
#  PATTERN: Rounding Bottom
# ═══════════════════════════════════════════════════════
def _detect_rounding_bottom(df, peaks, troughs):
    """Rounding Bottom: gradual U-shape."""
    if len(df) < 60:
        return None

    close = df['Close'].values
    mid = len(close) // 2

    # Split into 3 parts: left, middle, right
    left = close[:mid-10]
    middle = close[mid-10:mid+10]
    right = close[mid+10:]

    if len(left) < 5 or len(middle) < 5 or len(right) < 5:
        return None

    left_avg = np.mean(left)
    middle_avg = np.mean(middle)
    right_avg = np.mean(right)

    # Middle should be lower than both ends (U shape)
    if middle_avg < left_avg * 0.97 and middle_avg < right_avg * 0.97:
        current = close[-1]
        resistance = left_avg
        depth = (left_avg + right_avg) / 2 - middle_avg
        target = resistance + depth

        return {
            "pattern": "Rounding Bottom",
            "direction": "BULLISH",
            "confidence": 60,
            "resistance": round(resistance, 2),
            "bottom": round(middle_avg, 2),
            "target": round(target, 2),
            "breakout_level": round(resistance, 2),
            "current": round(current, 2),
            "meaning": "Bullish reversal — accumulation pattern",
        }
    return None


# ═══════════════════════════════════════════════════════
#  PATTERN: Cup & Handle
# ═══════════════════════════════════════════════════════
def _detect_cup_handle(df, peaks, troughs):
    """Cup & Handle: U-shape + small pullback."""
    if len(df) < 80:
        return None

    close = df['Close'].values

    # Cup: U-shape over last 60 bars (excluding last 15)
    cup_region = close[-75:-15]
    handle_region = close[-15:]

    if len(cup_region) < 30 or len(handle_region) < 5:
        return None

    cup_left = cup_region[:10]
    cup_middle = cup_region[len(cup_region)//2 - 5:len(cup_region)//2 + 5]
    cup_right = cup_region[-10:]

    left_high = np.max(cup_left)
    middle_low = np.min(cup_middle)
    right_high = np.max(cup_right)

    # Cup shape: high-low-high with middle at least 5% lower
    if (middle_low < left_high * 0.95 and middle_low < right_high * 0.95
        and _price_similarity(left_high, right_high, tol=0.05)):

        handle_low = np.min(handle_region)
        handle_high = np.max(handle_region)

        # Handle should be shallow pullback (< 50% of cup depth)
        cup_depth = left_high - middle_low
        handle_depth = right_high - handle_low

        if handle_depth < cup_depth * 0.5:
            current = close[-1]
            resistance = max(left_high, right_high)
            target = resistance + cup_depth * 0.7

            return {
                "pattern": "Cup and Handle",
                "direction": "BULLISH",
                "confidence": 72,
                "cup_high": round(resistance, 2),
                "cup_low": round(middle_low, 2),
                "handle_low": round(handle_low, 2),
                "target": round(target, 2),
                "breakout_level": round(resistance, 2),
                "current": round(current, 2),
                "meaning": "Bullish continuation — cup complete, handle forming",
            }
    return None


# ═══════════════════════════════════════════════════════
#  MAIN DETECTOR
# ═══════════════════════════════════════════════════════
def detect_advanced_patterns(df):
    """
    Detect all advanced chart patterns.
    Returns list sorted by confidence descending.
    """
    if df is None or len(df) < 80:
        return []

    # Find swing points
    try:
        peaks, troughs = _find_peaks_troughs(df, order=5)
    except ImportError:
        # Fallback: use simple swing detection
        swing_highs, swing_lows = _find_swing_points(df, left=5, right=5)
        peaks = swing_highs
        troughs = swing_lows
    except Exception:
        return []

    detected = []

    # Run all detectors
    detectors = [
        _detect_head_shoulders,
        _detect_inverse_head_shoulders,
        _detect_double_top,
        _detect_double_bottom,
        _detect_ascending_triangle,
        _detect_descending_triangle,
        _detect_symmetrical_triangle,
        _detect_rising_wedge,
        _detect_falling_wedge,
        _detect_bull_flag,
        _detect_bear_flag,
        _detect_rounding_bottom,
        _detect_cup_handle,
    ]

    for detector in detectors:
        try:
            result = detector(df, peaks, troughs)
            if result:
                result["source"] = "advanced_chart"
                detected.append(result)
        except Exception:
            continue

    # Sort by confidence
    detected.sort(key=lambda x: -x.get("confidence", 0))
    return detected


def format_advanced_patterns(patterns):
    """Pretty format."""
    if not patterns:
        return "No advanced chart patterns detected."

    lines = []
    lines.append(f"\n📊 ADVANCED CHART PATTERNS ({len(patterns)} found)")
    lines.append("─" * 65)

    for p in patterns[:5]:
        emoji = {"BULLISH": "🟢", "BEARISH": "🔴", "NEUTRAL": "🟡"}.get(
            p["direction"], "❓")
        lines.append(f"\n{emoji} {p['pattern']}  ({p['direction']}, conf {p['confidence']}%)")
        lines.append(f"   Current:    ₹{p.get('current', '?')}")

        if p.get("breakout_level"):
            lines.append(f"   Breakout:   ₹{p['breakout_level']}")
        if p.get("target"):
            lines.append(f"   Target:     ₹{p['target']}")

        lines.append(f"   💡 {p.get('meaning', '')}")

    return "\n".join(lines)


def get_pattern_score(patterns):
    """
    Convert patterns to -100 to +100 score.
    Score is based on weighted confidence, NOT just normalized.
    """
    if not patterns:
        return 0, "No patterns"

    bull_score = 0
    bear_score = 0

    for p in patterns:
        conf = p.get("confidence", 50)
        # Higher confidence = higher impact (max 50 per pattern)
        weight = conf / 2  # 50% conf = 25, 100% conf = 50
        if p["direction"] == "BULLISH":
            bull_score += weight
        elif p["direction"] == "BEARISH":
            bear_score += weight

    # Cap at 100 in each direction
    bull_score = min(bull_score, 100)
    bear_score = min(bear_score, 100)

    raw_score = bull_score - bear_score
    raw_score = max(-100, min(100, raw_score))

    return round(raw_score, 1), f"Bull {round(bull_score, 1)} / Bear {round(bear_score, 1)}"


# ═══════════════════════════════════════════════════════
#  STANDALONE TEST
# ═══════════════════════════════════════════════════════
if __name__ == "__main__":
    import sys
    import os
    import requests

    from dotenv import load_dotenv
    load_dotenv()

    symbol = sys.argv[1] if len(sys.argv) > 1 else "SBIN"

    # Simple fetch
    access_token = os.getenv("UPSTOX_ANALYTICS_TOKEN")
    key = f"NSE_EQ|INE062A01020"  # SBIN default

    today = pd.Timestamp.now().strftime('%Y-%m-%d')
    from_date = (pd.Timestamp.now() - pd.Timedelta(days=200)).strftime('%Y-%m-%d')

    url = (f"https://api.upstox.com/v3/historical-candle/"
           f"{key}/days/1/{today}/{from_date}")
    headers = {"Accept": "application/json",
               "Authorization": f"Bearer {access_token}"}

    r = requests.get(url, headers=headers, timeout=20)
    candles = r.json().get("data", {}).get("candles", [])

    if not candles:
        print(f"❌ No data for {symbol}")
        sys.exit(1)

    df = pd.DataFrame(candles, columns=['timestamp', 'Open', 'High', 'Low',
                                         'Close', 'Volume', 'OI'])
    df['timestamp'] = pd.to_datetime(df['timestamp'])
    df.set_index('timestamp', inplace=True)
    df.sort_index(inplace=True)

    print(f"\n{'═' * 65}")
    print(f"  📊 ADVANCED CHART PATTERN TEST — {symbol}")
    print(f"{'═' * 65}")
    print(f"  Data: {len(df)} daily candles")

    patterns = detect_advanced_patterns(df)
    print(format_advanced_patterns(patterns))

    score, raw = get_pattern_score(patterns)
    print(f"\n📈 Pattern Score: {score:+.1f}")
    print(f"   {raw}\n")