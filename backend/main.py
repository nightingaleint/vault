"""
Nightingale Vault — Probability Engine v2.0
Data source: Twelve Data API (https://twelvedata.com)
- Free tier: 800 API calls/day, no credit card
- Supports: stocks, ETFs, forex, crypto
- Sign up at twelvedata.com → get API key → add to Render env vars

Render environment variables needed:
  TWELVEDATA_API_KEY      → from twelvedata.com (free)
  STRIPE_WEBHOOK_SECRET   → from Stripe dashboard
  RESEND_API_KEY          → from resend.com (free)
  FROM_EMAIL              → your sending email
  SITE_URL                → https://nightingalevault.com
"""

from fastapi import FastAPI, HTTPException, Request, Header
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
import httpx
import os, secrets, string, json
from datetime import datetime, date, timedelta

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)

# ── CONFIG ────────────────────────────────────────────────────────────────────
TWELVEDATA_KEY  = os.getenv("TWELVEDATA_API_KEY", "")
STRIPE_SECRET   = os.getenv("STRIPE_WEBHOOK_SECRET", "")
RESEND_KEY      = os.getenv("RESEND_API_KEY", "")
FROM_EMAIL      = os.getenv("FROM_EMAIL", "vault@nightingalevault.com")
SITE_URL        = os.getenv("SITE_URL", "https://nightingalevault.com")

# ── ACCESS CODES ──────────────────────────────────────────────────────────────
CODES: dict = {
    "NIGHTINGALE": {"type": "demo",  "active": True, "email": "", "session": ""},
    "VAULT2025":   {"type": "admin", "active": True, "email": "", "session": ""},
}

# ── HELPERS ───────────────────────────────────────────────────────────────────
def make_code() -> str:
    c = string.ascii_uppercase + string.digits
    r = "".join(secrets.choice(c) for _ in range(12))
    return f"NV-{r[:4]}-{r[4:8]}-{r[8:12]}"

def days_to_outputsize(days: int) -> int:
    # Twelve Data returns N most recent data points
    # Add buffer for weekends/holidays
    return {30: 45, 60: 90, 90: 130}.get(days, 130)

async def fetch_twelvedata(ticker: str, days: int) -> list[dict]:
    """
    Fetch daily OHLCV from Twelve Data.
    Returns list of {date, close} dicts sorted oldest → newest.
    """
    if not TWELVEDATA_KEY:
        raise HTTPException(500, "TWELVEDATA_API_KEY not set on server. Add it in Render environment variables.")

    # If custom date range, fetch up to 2 years of data so we can filter
    outputsize = 500 if date_from and date_to else days_to_outputsize(days)
    url = "https://api.twelvedata.com/time_series"
    params = {
        "symbol":     ticker,
        "interval":   "1day",
        "outputsize": outputsize,
        "apikey":     TWELVEDATA_KEY,
        "format":     "JSON",
        "order":      "ASC",   # oldest first
    }

    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.get(url, params=params)

    if resp.status_code != 200:
        raise HTTPException(502, f"Twelve Data returned HTTP {resp.status_code}")

    data = resp.json()

    # Check for API-level errors
    if data.get("status") == "error":
        msg = data.get("message", "Unknown error from data provider")
        if "not found" in msg.lower() or "invalid" in msg.lower():
            raise HTTPException(404, f"Symbol '{ticker}' not found. Check the ticker — e.g. AAPL, MSFT, BTC/USD, SPY.")
        raise HTTPException(502, f"Data provider error: {msg}")

    values = data.get("values")
    if not values:
        raise HTTPException(404, f"No price data returned for '{ticker}'.")

    # Parse and trim to requested number of trading days
    parsed = []
    for v in values:
        try:
            parsed.append({
                "date":  datetime.strptime(v["datetime"], "%Y-%m-%d").strftime("%d %b '%y"),
                "close": float(v["close"]),
            })
        except (KeyError, ValueError):
            continue

    # Keep only the last `days` worth of actual trading days
    return parsed[-days:] if len(parsed) > days else parsed


async def send_email(to: str, code: str, name: str = "Trader"):
    if not RESEND_KEY:
        print(f"[NO EMAIL KEY] Code for {to}: {code}")
        return
    html = f"""<div style="background:#020305;color:#d8e8f4;font-family:monospace;padding:40px;max-width:520px;margin:0 auto">
<div style="color:#d4af37;font-size:1.1rem;letter-spacing:.1em;margin-bottom:4px">NIGHTINGALE VAULT</div>
<div style="color:#8a9ab8;font-size:.72rem;letter-spacing:.15em;margin-bottom:28px">PRIVATE INTELLIGENCE PLATFORM</div>
<p style="font-size:.88rem;line-height:1.9;margin-bottom:24px">Hi {name},<br>Your purchase is confirmed. Here is your lifetime access code.</p>
<div style="background:#0c1018;border:1px solid #b8960c;border-radius:6px;padding:24px;text-align:center;margin-bottom:24px">
<div style="color:#8a9ab8;font-size:.58rem;letter-spacing:.22em;text-transform:uppercase;margin-bottom:10px">Lifetime Access Code</div>
<div style="color:#d4af37;font-size:1.8rem;letter-spacing:.22em;font-weight:bold">{code}</div>
<div style="color:#8a9ab8;font-size:.6rem;margin-top:8px">This code never expires</div>
</div>
<div style="color:#8a9ab8;font-size:.74rem;line-height:2">
<b style="color:#d8e8f4">How to use it:</b><br>
1. Go to <a href="{SITE_URL}" style="color:#90cce8">{SITE_URL}</a><br>
2. Click Learn to Trade in the navigation<br>
3. Paste your code and click Unlock<br><br>
Works on any device. No expiry. No renewal.<br>
Lost this email? Just reply and we will resend it.<br><br>
— Nightingale Vault
</div></div>"""
    async with httpx.AsyncClient() as client:
        r = await client.post(
            "https://api.resend.com/emails",
            headers={"Authorization": f"Bearer {RESEND_KEY}", "Content-Type": "application/json"},
            json={"from": FROM_EMAIL, "to": [to],
                  "subject": "Your Nightingale Vault Lifetime Access Code", "html": html},
            timeout=10,
        )
        print(f"[EMAIL] {r.status_code} → {to}")


# ── ROUTES ────────────────────────────────────────────────────────────────────

@app.get("/")
def root():
    return {
        "service":   "Nightingale Probability Engine",
        "status":    "live",
        "version":   "2.0.0",
        "data":      "Twelve Data API",
        "endpoints": ["/health", "/analyze", "/validate-code", "/stripe-webhook", "/success"],
    }

@app.get("/health")
def health():
    return {
        "status":     "live",
        "version":    "2.0.0",
        "data_source": "Twelve Data",
        "api_key_set": bool(TWELVEDATA_KEY),
        "codes":      len(CODES),
    }

@app.get("/analyze")
async def analyze(ticker: str = "AAPL", days: int = 90, date_from: str = "", date_to: str = ""):
    ticker = ticker.upper().strip()

    # Twelve Data uses BTC/USD not BTC-USD for crypto
    # Support both formats
    ticker_td = ticker.replace("-", "/")

    if days not in (30, 60, 90):
        days = 90

    try:
        prices = await fetch_twelvedata(ticker_td, days)

        # Filter by custom date range if provided
        if date_from and date_to:
            try:
                from datetime import datetime as dt
                df_start = dt.strptime(date_from, "%Y-%m-%d")
                df_end   = dt.strptime(date_to,   "%Y-%m-%d")
                # Re-parse dates for comparison (prices have "dd Mon 'yy" format)
                def parse_price_date(d):
                    try: return dt.strptime(d, "%d %b '%y")
                    except: return None
                prices = [p for p in prices if parse_price_date(p["date"]) and df_start <= parse_price_date(p["date"]) <= df_end]
            except Exception:
                pass  # if date parsing fails, use all data

        if len(prices) < 5:
            raise HTTPException(404, f"Not enough data for '{ticker}'. Only {len(prices)} days returned.")

        closes = [p["close"] for p in prices]
        dates  = [p["date"]  for p in prices]

        # Daily returns
        returns = []
        for i in range(1, len(closes)):
            pct = ((closes[i] - closes[i-1]) / closes[i-1]) * 100
            returns.append(round(pct, 3))

        win_days  = sum(1 for r in returns if r > 0)
        loss_days = sum(1 for r in returns if r < 0)
        flat_days = sum(1 for r in returns if r == 0)
        total     = len(returns)
        win_rate  = round((win_days / total) * 100, 1) if total else 0

        start_price   = closes[0]
        current_price = closes[-1]
        total_return  = round(((current_price - start_price) / start_price) * 100, 2)
        avg_move      = round(sum(abs(r) for r in returns) / total, 3) if total else 0
        max_drop      = round(min(returns), 3) if returns else 0
        best_day      = round(max(returns), 3) if returns else 0
        volatility    = round((sum((r - (sum(returns)/total))**2 for r in returns) / total) ** 0.5, 3) if total else 0

        price_series  = [{"date": dates[i], "price": closes[i]} for i in range(len(closes))]
        return_series = [{"date": dates[i+1], "return": returns[i], "green": returns[i] > 0} for i in range(len(returns))]

        return {
            "ticker":         ticker,
            "days":           days,
            "win_rate":       win_rate,
            "win_days":       win_days,
            "loss_days":      loss_days,
            "flat_days":      flat_days,
            "total_days":     total,
            "current_price":  round(current_price, 4),
            "start_price":    round(start_price, 4),
            "total_return":   total_return,
            "avg_daily_move": avg_move,
            "max_drop":       max_drop,
            "best_day":       best_day,
            "volatility":     volatility,
            "price_series":   price_series,
            "return_series":  return_series,
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, str(e))


@app.post("/validate-code")
async def validate_code(request: Request):
    body = await request.json()
    code = body.get("code", "").strip().upper()
    if not code:
        return {"valid": False, "reason": "No code provided"}
    entry = CODES.get(code)
    if not entry or not entry.get("active"):
        return {"valid": False, "reason": "Code not recognised"}
    return {"valid": True, "type": entry["type"], "message": "Lifetime access granted"}


@app.post("/stripe-webhook")
async def stripe_webhook(request: Request, stripe_signature: str = Header(None)):
    payload = await request.body()
    if STRIPE_SECRET:
        try:
            import stripe as sl
            event = sl.Webhook.construct_event(payload, stripe_signature, STRIPE_SECRET)
        except Exception as e:
            raise HTTPException(400, f"Bad signature: {e}")
    else:
        event = json.loads(payload)
        print("[STRIPE] No secret set — skipping verification")

    if event.get("type") == "checkout.session.completed":
        s     = event["data"]["object"]
        email = s.get("customer_details", {}).get("email") or s.get("customer_email", "")
        name  = s.get("customer_details", {}).get("name") or "Trader"
        sid   = s.get("id", "")
        if email:
            code = make_code()
            CODES[code] = {
                "type": "paid", "active": True,
                "email": email,
                "purchased_at": datetime.utcnow().isoformat(),
                "session": sid,
            }
            print(f"[STRIPE] Issued {code} → {email}")
            await send_email(email, code, name)

    return {"received": True}


@app.get("/success", response_class=HTMLResponse)
async def success(session_id: str = ""):
    code = next(
        (c for c, m in CODES.items() if m.get("session") == session_id and m.get("type") == "paid"),
        None
    )
    if not code:
        return HTMLResponse("""<!DOCTYPE html><html><head><meta charset="UTF-8">
        <meta http-equiv="refresh" content="3"><title>Processing…</title>
        <style>body{background:#020305;color:#d8e8f4;font-family:monospace;display:flex;
        align-items:center;justify-content:center;min-height:100vh;margin:0;text-align:center}
        .sp{font-size:2rem;animation:s 2s linear infinite;display:inline-block;margin-bottom:16px}
        @keyframes s{to{transform:rotate(360deg)}}</style></head>
        <body><div><div class="sp">◈</div>
        <div style="color:#d4af37;letter-spacing:.1em;margin-bottom:8px">PROCESSING YOUR ACCESS</div>
        <div style="color:#8a9ab8;font-size:.78rem;line-height:1.9">Payment confirmed.<br>
        Generating your code — this page refreshes automatically.</div>
        </div></body></html>""")

    return HTMLResponse(f"""<!DOCTYPE html><html><head><meta charset="UTF-8">
    <title>Access Granted — Nightingale Vault</title>
    <link href="https://fonts.googleapis.com/css2?family=Cinzel:wght@700&family=Share+Tech+Mono&display=swap" rel="stylesheet">
    <style>*{{margin:0;padding:0;box-sizing:border-box}}
    body{{background:#020305;color:#d8e8f4;font-family:'Share Tech Mono',monospace;
         display:flex;align-items:center;justify-content:center;min-height:100vh;padding:24px}}
    .w{{max-width:480px;width:100%;text-align:center}}
    .brand{{font-family:'Cinzel',serif;color:#d4af37;font-size:1.1rem;letter-spacing:.12em;margin-bottom:4px}}
    .sub{{color:#8a9ab8;font-size:.6rem;letter-spacing:.2em;text-transform:uppercase;margin-bottom:32px}}
    h1{{font-family:'Cinzel',serif;font-size:1.3rem;color:#f0f6ff;margin-bottom:8px}}
    .desc{{color:#8a9ab8;font-size:.76rem;line-height:1.9;margin-bottom:26px}}
    .cb{{background:#0c1018;border:1px solid #b8960c;border-radius:8px;padding:24px;margin-bottom:22px}}
    .cl{{font-size:.56rem;letter-spacing:.24em;text-transform:uppercase;color:#8a9ab8;margin-bottom:10px}}
    .code{{font-size:1.8rem;letter-spacing:.22em;color:#d4af37;font-weight:bold;user-select:all}}
    .cp{{display:inline-block;margin-top:12px;padding:8px 18px;background:rgba(184,150,12,.15);
         border:1px solid rgba(212,175,55,.4);border-radius:4px;font-family:'Share Tech Mono',monospace;
         font-size:.65rem;letter-spacing:.1em;color:#d4af37;cursor:pointer}}
    .steps{{text-align:left;background:#060810;border:1px solid #1e2535;border-radius:6px;
            padding:16px 20px;margin-bottom:20px;font-size:.72rem;line-height:2.2;color:#8a9ab8}}
    .go{{display:inline-block;padding:12px 28px;background:rgba(184,150,12,.18);
         border:1px solid rgba(212,175,55,.5);border-radius:5px;font-family:'Share Tech Mono',monospace;
         font-size:.72rem;letter-spacing:.12em;text-transform:uppercase;color:#d4af37;text-decoration:none}}
    .note{{font-size:.58rem;color:#2a3548;margin-top:16px;line-height:1.8}}</style></head>
    <body><div class="w">
    <div class="brand">NIGHTINGALE VAULT</div>
    <div class="sub">Strategy Analyser · Lifetime Access</div>
    <div style="font-size:2rem;margin-bottom:14px;color:#3dbb78">✓</div>
    <h1>Payment Confirmed</h1>
    <div class="desc">Your lifetime access code is below.<br>We've also emailed it to you.</div>
    <div class="cb">
      <div class="cl">Lifetime Access Code</div>
      <div class="code">{code}</div>
      <button class="cp" onclick="navigator.clipboard.writeText('{code}').then(()=>{{this.textContent='✓ Copied';setTimeout(()=>this.textContent='Copy',2000)}})">Copy</button>
    </div>
    <div class="steps">
      <strong style="color:#d8e8f4">How to use it:</strong><br>
      1. Click the button below to open the Vault<br>
      2. Click <strong style="color:#d8e8f4">Learn to Trade</strong> in the top nav<br>
      3. Paste your code and click <strong style="color:#d8e8f4">Unlock</strong><br>
      4. You're in — permanently.
    </div>
    <a href="{SITE_URL}" class="go">→ Open Nightingale Vault</a>
    <div class="note">Never expires · Any device · Lost it? Check your email</div>
    </div></body></html>""")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000, reload=True)
