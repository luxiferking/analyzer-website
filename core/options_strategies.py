"""
core/options_strategies.py
Low-Risk Options Strategies:
✅ Bear Put Spread (bearish view)
✅ Bull Call Spread (bullish view)
✅ Iron Condor (range-bound market)

Ye strategies naked options se 3x safer hain — kyunki risk LIMITED hai.
"""
import os
import requests
from dotenv import load_dotenv

load_dotenv()


def get_chain_price(chain, strike, option_type):
    """Get LTP for a specific strike and option type from chain."""
    for item in chain:
        if item.get("strike_price") == strike:
            if option_type == "CE":
                return item.get("call_options", {}).get("market_data", {}).get("ltp")
            else:
                return item.get("put_options", {}).get("market_data", {}).get("ltp")
    return None


def get_chain_key(chain, strike, option_type):
    """Get instrument_key for a specific strike."""
    for item in chain:
        if item.get("strike_price") == strike:
            if option_type == "CE":
                return item.get("call_options", {}).get("instrument_key")
            else:
                return item.get("put_options", {}).get("instrument_key")
    return None


# ============================================================
#  STRATEGY 1: BEAR PUT SPREAD (Bearish)
# ============================================================
def bear_put_spread(chain, spot, atm_strike, step=50, width=2):
    """
    Bear Put Spread:
    - BUY ATM PUT (higher strike, higher premium)
    - SELL OTM PUT (lower strike, lower premium)

    Max loss = net premium paid
    Max profit = (strike diff) - net premium
    Best when: Mildly bearish
    """
    buy_strike = atm_strike
    sell_strike = atm_strike - (width * step)

    buy_price = get_chain_price(chain, buy_strike, "PE")
    sell_price = get_chain_price(chain, sell_strike, "PE")

    if not buy_price or not sell_price:
        return None

    net_cost = buy_price - sell_price
    if net_cost <= 0:
        return None

    max_profit = (width * step) - net_cost
    max_loss = net_cost
    breakeven = buy_strike - net_cost
    rr = max_profit / max_loss if max_loss > 0 else 0

    return {
        "strategy": "Bear Put Spread",
        "outlook": "Bearish (limited risk)",
        "legs": [
            {"action": "BUY",  "strike": buy_strike,  "type": "PE", "price": buy_price,  "key": get_chain_key(chain, buy_strike, "PE")},
            {"action": "SELL", "strike": sell_strike, "type": "PE", "price": sell_price, "key": get_chain_key(chain, sell_strike, "PE")},
        ],
        "net_cost": round(net_cost, 2),
        "max_profit": round(max_profit, 2),
        "max_loss": round(max_loss, 2),
        "breakeven": round(breakeven, 2),
        "risk_reward": round(rr, 2),
        "buy_strike": buy_strike,
        "sell_strike": sell_strike,
    }


# ============================================================
#  STRATEGY 2: BULL CALL SPREAD (Bullish)
# ============================================================
def bull_call_spread(chain, spot, atm_strike, step=50, width=2):
    """
    Bull Call Spread:
    - BUY ATM CALL (lower strike)
    - SELL OTM CALL (higher strike)

    Max loss = net premium paid
    Max profit = (strike diff) - net premium
    Best when: Mildly bullish
    """
    buy_strike = atm_strike
    sell_strike = atm_strike + (width * step)

    buy_price = get_chain_price(chain, buy_strike, "CE")
    sell_price = get_chain_price(chain, sell_strike, "CE")

    if not buy_price or not sell_price:
        return None

    net_cost = buy_price - sell_price
    if net_cost <= 0:
        return None

    max_profit = (width * step) - net_cost
    max_loss = net_cost
    breakeven = buy_strike + net_cost
    rr = max_profit / max_loss if max_loss > 0 else 0

    return {
        "strategy": "Bull Call Spread",
        "outlook": "Bullish (limited risk)",
        "legs": [
            {"action": "BUY",  "strike": buy_strike,  "type": "CE", "price": buy_price,  "key": get_chain_key(chain, buy_strike, "CE")},
            {"action": "SELL", "strike": sell_strike, "type": "CE", "price": sell_price, "key": get_chain_key(chain, sell_strike, "CE")},
        ],
        "net_cost": round(net_cost, 2),
        "max_profit": round(max_profit, 2),
        "max_loss": round(max_loss, 2),
        "breakeven": round(breakeven, 2),
        "risk_reward": round(rr, 2),
        "buy_strike": buy_strike,
        "sell_strike": sell_strike,
    }


# ============================================================
#  STRATEGY 3: IRON CONDOR (Range-bound)
# ============================================================
def iron_condor(chain, spot, atm_strike, step=50, width=2, wing=2):
    """
    Iron Condor:
    - SELL OTM CALL + BUY further OTM CALL (Call Spread)
    - SELL OTM PUT + BUY further OTM PUT (Put Spread)

    Net credit received (you get paid to open)
    Max profit = net credit
    Max loss = wing_width - net credit
    Best when: Market is range-bound / sideways
    """
    sell_ce_strike = atm_strike + (width * step)
    buy_ce_strike = atm_strike + ((width + wing) * step)
    sell_pe_strike = atm_strike - (width * step)
    buy_pe_strike = atm_strike - ((width + wing) * step)

    sell_ce_price = get_chain_price(chain, sell_ce_strike, "CE")
    buy_ce_price = get_chain_price(chain, buy_ce_strike, "CE")
    sell_pe_price = get_chain_price(chain, sell_pe_strike, "PE")
    buy_pe_price = get_chain_price(chain, buy_pe_strike, "PE")

    if not all([sell_ce_price, buy_ce_price, sell_pe_price, buy_pe_price]):
        return None

    net_credit = (sell_ce_price + sell_pe_price) - (buy_ce_price + buy_pe_price)
    if net_credit <= 0:
        return None

    wing_width = wing * step
    max_loss = wing_width - net_credit
    max_profit = net_credit
    upper_breakeven = sell_ce_strike + net_credit
    lower_breakeven = sell_pe_strike - net_credit
    rr = max_profit / max_loss if max_loss > 0 else 0

    return {
        "strategy": "Iron Condor",
        "outlook": "Range-bound (sideways market)",
        "legs": [
            {"action": "SELL", "strike": sell_ce_strike, "type": "CE", "price": sell_ce_price, "key": get_chain_key(chain, sell_ce_strike, "CE")},
            {"action": "BUY",  "strike": buy_ce_strike,  "type": "CE", "price": buy_ce_price,  "key": get_chain_key(chain, buy_ce_strike, "CE")},
            {"action": "SELL", "strike": sell_pe_strike, "type": "PE", "price": sell_pe_price, "key": get_chain_key(chain, sell_pe_strike, "PE")},
            {"action": "BUY",  "strike": buy_pe_strike,  "type": "PE", "price": buy_pe_price,  "key": get_chain_key(chain, buy_pe_strike, "PE")},
        ],
        "net_credit": round(net_credit, 2),
        "max_profit": round(max_profit, 2),
        "max_loss": round(max_loss, 2),
        "upper_breakeven": round(upper_breakeven, 2),
        "lower_breakeven": round(lower_breakeven, 2),
        "risk_reward": round(rr, 2),
        "sell_ce_strike": sell_ce_strike,
        "buy_ce_strike": buy_ce_strike,
        "sell_pe_strike": sell_pe_strike,
        "buy_pe_strike": buy_pe_strike,
    }