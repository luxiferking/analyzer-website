from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from core.options_upgraded import analyze_best_trade as scan_pro, format_result as format_pro
from core.pure_technical_options import analyze_best_trade as scan_pure, format_result as format_pure
import traceback

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://luxiferking.github.io"], 
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

def build_response(best_trade, formatter):
    d = best_trade["decision"]
    op = best_trade.get("option_plan", {})
    return {
        "status": "success",
        "symbol": best_trade["symbol"],
        "action": d["action"],
        "strength": d["strength"],
        "entry": op.get("entry"),
        "target": op.get("target"),
        "stop_loss": op.get("sl"),
        "terminal_output": formatter(best_trade)
    }

@app.get("/api/scan")
def scan_market_pro():
    try:
        best_trade = scan_pro()
        if best_trade is None:
            return {"status": "wait", "message": "No valid trade found. The market conditions don't meet the strict V5.0 filters."}
        return build_response(best_trade, format_pro)
    except Exception as e:
        return {"status": "error", "message": str(e)}

@app.get("/api/scan_pure")
def scan_market_pure():
    try:
        best_trade = scan_pure()
        if best_trade is None:
            return {"status": "wait", "message": "No valid trade found across any underlying/expiry."}
        return build_response(best_trade, format_pure)
    except Exception as e:
        return {"status": "error", "message": str(e)}