from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from core.options_upgraded import analyze_best_trade, format_result
import traceback

app = FastAPI()

# 🚨 FIXED CORS SETTINGS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://luxiferking.github.io"],  # Allows all origins
    allow_credentials=False, # MUST BE FALSE WHEN USING "*"
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/api/scan")
def scan_market():
    try:
         
        best_trade = analyze_best_trade()
        
        if best_trade is None:
            return {
                "status": "wait",
                "message": "No valid trade found. The market conditions don't meet the strict V5.0 filters."
            }
            
        d = best_trade["decision"]
        op = best_trade.get("option_plan", {})
        
        return {
            "status": "success",
            "symbol": best_trade["symbol"],
            "spot_price": best_trade["spot"],
            "action": d["action"],
            "strength": d["strength"],
            "score": d["score"],
            "strike": op.get("strike"),
            "option_type": op.get("type"),
            "entry": op.get("entry"),
            "target": op.get("target"),
            "stop_loss": op.get("sl"),
            "expiry": op.get("expiry"),
            "terminal_output": format_result(best_trade)
        }
        
    except Exception as e:
        return {
            "status": "error",
            "message": str(e),
            "traceback": traceback.format_exc()
        }