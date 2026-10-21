# core/risk_engine.py

def calculate_position_size(capital, entry, stop_loss, risk_pct=0.01):
    """
    Calculates the number of shares to buy based on risk percentage.
    Default risk: 1% of capital per trade.
    """
    risk_per_share = abs(entry - stop_loss)
    if risk_per_share <= 0:
        return 0
    risk_amount = capital * risk_pct
    size = int(risk_amount / risk_per_share)
    return size


def build_trade_plan(current_price, atr, signal, capital=100000, rr_ratio=3.0):
    """
    Builds a complete trade plan: entry, stop-loss, target, position size.
    rr_ratio: Reward-to-Risk ratio. 2.0 = 1:2, 3.0 = 1:3
    """
    if atr is None or atr <= 0:
        return None

    if signal == "BUY":
        stop_loss = current_price - (1.5 * atr)
        target = current_price + (1.5 * atr * rr_ratio)
    elif signal == "SELL":
        stop_loss = current_price + (1.5 * atr)
        target = current_price - (1.5 * atr * rr_ratio)
    else:
        return None

    size = calculate_position_size(capital, current_price, stop_loss)

    return {
        "direction": signal,                       # <-- YEH FIX HAI
        "entry": round(current_price, 2),
        "stop_loss": round(stop_loss, 2),
        "target": round(target, 2),
        "position_size": size,
        "risk_reward": f"1:{rr_ratio:.0f}",
    }