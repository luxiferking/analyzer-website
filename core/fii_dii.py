# core/fii_dii.py
import requests

def get_fii_dii_data():
    """NSE se FII/DII ka aaj ka data nikaalo."""
    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "application/json",
        "Accept-Language": "en-US,en;q=0.9",
    })
    
    try:
        # NSE pehle cookies set karta hai
        session.get("https://www.nseindia.com", timeout=10)
        
        # Ab API hit karo
        url = "https://www.nseindia.com/api/fiidiiTradeReact"
        response = session.get(url, timeout=10)
        response.raise_for_status()
        data = response.json()
        
        result = {}
        for item in data:
            category = item.get("category", "")
            if "FII" in category.upper() or "FPI" in category.upper():
                result["FII_Net"] = float(item.get("netValue", 0))
            elif "DII" in category.upper():
                result["DII_Net"] = float(item.get("netValue", 0))
        
        return result
    except Exception as e:
        return {"FII_Net": 0, "DII_Net": 0, "Error": str(e)}

def get_fii_dii_bias(data):
    """FII/DII data ke basis pe market bias."""
    fii = data.get("FII_Net", 0)
    dii = data.get("DII_Net", 0)
    
    if fii > 0 and dii > 0: return "STRONGLY BULLISH"
    elif fii < 0 and dii < 0: return "STRONGLY BEARISH"
    elif fii > 0: return "BULLISH (FII Buying)"
    elif dii > 0: return "BULLISH (DII Buying)"
    else: return "BEARISH"