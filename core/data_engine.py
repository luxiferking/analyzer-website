import os
import requests
import pandas as pd
from datetime import datetime
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")


def get_historical_candles(instrument_key: str, interval: str = "5", days: int = 30):
    """
    Fetches historical candles. Supports both intraday and daily data.
    - interval "5", "15", "60" -> minutes
    - interval "day" -> daily bars
    """
    access_token = os.getenv("UPSTOX_ANALYTICS_TOKEN")
    if not access_token:
        return None, "UPSTOX_ANALYTICS_TOKEN missing"

    # Determine unit and interval value
    if interval in ["day", "1D", "daily"]:
        unit = "days"
        interval_value = "1"
    else:
        unit = "minutes"
        interval_value = interval

    # Cap days for minute-level data (Upstox limit)
    if unit == "minutes" and days > 30:
        days = 30

    today = pd.Timestamp.now().strftime('%Y-%m-%d')
    from_date = (pd.Timestamp.now() - pd.Timedelta(days=days)).strftime('%Y-%m-%d')

    url = f"https://api.upstox.com/v3/historical-candle/{instrument_key}/{unit}/{interval_value}/{today}/{from_date}"
    headers = {"Accept": "application/json", "Authorization": f"Bearer {access_token}"}

    try:
        response = requests.get(url, headers=headers)
        if response.status_code != 200:
            return None, f"HTTP {response.status_code}: {response.text[:200]}"

        data = response.json()
        if data.get("status") != "success":
            return None, f"Upstox error: {data}"

        candles = data.get("data", {}).get("candles", [])
        if not candles:
            return None, "No candles returned"

        df = pd.DataFrame(candles, columns=['timestamp', 'Open', 'High', 'Low', 'Close', 'Volume', 'OI'])
        df['timestamp'] = pd.to_datetime(df['timestamp'])
        df.set_index('timestamp', inplace=True)
        df.sort_index(inplace=True)
        df = df[(df['High'] >= df['Low']) & (df['Open'] > 0) & (df['Close'] > 0)]
        df = df.dropna()
        # Don't require volume > 0 for daily data (some days have 0)
        return df, None
    except Exception as e:
        return None, f"Request exception: {e}"


def get_live_price(instrument_key: str):
    access_token = os.getenv("UPSTOX_ANALYTICS_TOKEN")
    url = f"https://api.upstox.com/v3/market-quote/quotes?instrument_key={instrument_key}"
    headers = {"Accept": "application/json", "Authorization": f"Bearer {access_token}"}
    try:
        response = requests.get(url, headers=headers)
        data = response.json()
        if data.get("status") != "success":
            return None
        quote = data["data"].get(instrument_key)
        return quote.get("ohlc", {}).get("close", 0) if quote else None
    except:
        return None


def is_market_live():
    now = datetime.now(IST)
    if now.weekday() >= 5:
        return False, "Weekend"
    market_open = now.replace(hour=9, minute=15, second=0, microsecond=0)
    market_close = now.replace(hour=15, minute=30, second=0, microsecond=0)
    if now < market_open:
        return False, "Pre-market"
    if now > market_close:
        return False, "Post-market"
    return True, "Live"