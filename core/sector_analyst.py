# core/sector_analyst.py
import yfinance as yf

# Nifty ke main sector indices
SECTOR_TICKERS = {
    "Nifty IT": "^CNXIT",
    "Nifty Bank": "^NSEBANK",
    "Nifty Auto": "^CNXAUTO",
    "Nifty Pharma": "^CNXPHARMA",
    "Nifty FMCG": "^CNXFMCG",
    "Nifty Metal": "^CNXMETAL",
    "Nifty Energy": "^CNXENERGY",
    "Nifty Realty": "^CNXREALTY",
    "Nifty Infra": "^CNXINFRA",
}

def get_sector_performance():
    """Sabhi sectors ka aaj ka performance nikaalo."""
    sector_data = {}
    try:
        for name, ticker in SECTOR_TICKERS.items():
            data = yf.Ticker(ticker).history(period="1d")
            if not data.empty:
                open_price = data['Open'].iloc[0]
                current_price = data['Close'].iloc[-1]
                change_pct = ((current_price - open_price) / open_price) * 100
                sector_data[name] = round(change_pct, 2)
            else:
                sector_data[name] = 0.0
    except Exception as e:
        print(f"[Sector Error] {e}")
    return sector_data

def get_top_sectors(sector_data, top_n=3):
    """Sabse zyada perform karne wale sectors nikaalo."""
    if not sector_data:
        return [], []
    
    sorted_sectors = sorted(sector_data.items(), key=lambda x: x[1], reverse=True)
    gainers = [f"{k} (+{v}%)" for k, v in sorted_sectors[:top_n] if v > 0]
    losers = [f"{k} ({v}%)" for k, v in sorted_sectors[-top_n:] if v < 0]
    return gainers, losers