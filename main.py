from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import yfinance as yf
import pandas as pd

app = FastAPI()

# This allows your Cloudflare website to talk to this Python code
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], 
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/analyze")
def analyze(ticker: str = "BTC-USD"):
    try:
        # Fetch 30 days of data
        data = yf.download(ticker, period="1mo", interval="1d")
        if data.empty:
            return {"error": "Invalid Ticker"}

        # Calculate Probability
        data['Returns'] = data['Close'].pct_change()
        win_days = int(len(data[data['Returns'] > 0]))
        total_days = int(len(data.dropna()))
        win_rate = round((win_days / total_days) * 100, 1)

        # Get current price
        current_price = round(float(data['Close'].iloc[-1]), 2)

        return {
            "ticker": ticker,
            "win_rate": win_rate,
            "win_days": win_days,
            "total_days": total_days,
            "current_price": current_price
        }
    except Exception as e:
        return {"error": str(e)}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
