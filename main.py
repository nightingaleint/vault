"""
Nightingale Vault — Probability Engine
Backend: FastAPI + yfinance
Live on Render: https://vault-qr1s.onrender.com

Endpoints:
  GET  /health                           → uptime check
  GET  /analyze?ticker=BTC-USD&days=90  → win rate, chart data, daily returns
  POST /validate-code                    → gate check (lifetime — no expiry)
  POST /stripe-webhook                   → payment success → generate code → email it
  GET  /success?session_id=xxx           → post-payment page showing code on screen

GDPR:
  Stripe handles all payment data. We never see card details.
  The only personal data stored here is the customer email from Stripe,
  used solely to deliver the access code. No marketing. No third-party sharing.
"""

from fastapi import FastAPI, HTTPException, Request, Header
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
import yfinance as yf
import pandas as pd
import os
import secrets
import string
import json
from datetime import datetime

app = FastAPI(title="Nightingale Probability Engine", version="1.1.0")

# ── CORS ──────────────────────────────────────────────────────────────────────
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],   # tighten to your domain in production
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)

# ── CODE STORE ────────────────────────────────────────────────────────────────
# { "CODE": { "type": "paid"|"demo"|"admin", "active": True,
#             "email": "...", "purchased_at": "ISO", "stripe_session": "..." } }
# active=True = lifetime access. Never set to False after a paid purchase.
# In-memory for MVP. Upgrade to Supabase (free) when you need persistence.
VALID_CODES: dict = {
    "NIGHTINGALE": {"type": "demo",  "active": True, "email": "", "purchased_at": "2025-01-01", "stripe_session": ""},
    "VAULT2025":   {"type": "admin", "active": True, "email": "", "purchased_at": "2025-01-01", "stripe_session": ""},
}

# ── ENV VARS — set in Render Dashboard → Environment ─────────────────────────
STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET", "")
RESEND_API_KEY        = os.getenv("RESEND_API_KEY", "")
FROM_EMAIL            = os.getenv("FROM_EMAIL", "vault@nightingalevault.com")
SITE_URL              = os.getenv("SITE_URL", "https://nightingalevault.com")


# ─────────────────────────────────────────────────────────────────────────────
#  HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def generate_code() -> str:
    chars = string.ascii_uppercase + string.digits
    raw   = "".join(secrets.choice(chars) for _ in range(12))
    return f"NV-{raw[:4]}-{raw[4:8]}-{raw[8:12]}"


async def send_access_email(to: str, code: str, name: str = "Trader"):
    if not RESEND_API_KEY:
        print(f"[EMAIL SKIPPED] No RESEND_API_KEY. Code for {to}: {code}")
        return

    import httpx

    html = f"""
    <!DOCTYPE html><html><body style="margin:0;padding:0;background:#020305;font-family:monospace">
    <div style="max-width:520px;margin:40px auto;padding:40px;background:#060810;border:1px solid #2a3548;border-radius:8px">
      <div style="color:#d4af37;font-size:1.1rem;letter-spacing:.12em;margin-bottom:4px">NIGHTINGALE VAULT</div>
      <div style="color:#8a9ab8;font-size:.72rem;letter-spacing:.18em;margin-bottom:30px">PRIVATE INTELLIGENCE PLATFORM</div>
      <div style="color:#d8e8f4;font-size:.88rem;line-height:1.9;margin-bottom:26px">
        Hi {name},<br><br>
        Your purchase is confirmed. Here is your <strong>lifetime access code</strong>
        for the Nightingale Vault Strategy Analyser.
      </div>
      <div style="background:#0c1018;border:1px solid #b8960c;border-radius:6px;padding:26px;text-align:center;margin-bottom:26px">
        <div style="color:#8a9ab8;font-size:.58rem;letter-spacing:.25em;text-transform:uppercase;margin-bottom:12px">Lifetime Access Code</div>
        <div style="color:#d4af37;font-size:1.8rem;letter-spacing:.25em;font-weight:bold">{code}</div>
        <div style="color:#8a9ab8;font-size:.6rem;margin-top:10px">This code never expires</div>
      </div>
      <div style="color:#8a9ab8;font-size:.74rem;line-height:2.1">
        <strong style="color:#d8e8f4">How to use it:</strong><br>
        1. Go to <a href="{SITE_URL}" style="color:#90cce8">{SITE_URL}</a><br>
        2. Click <strong>Learn to Trade</strong> in the navigation<br>
        3. Paste your code and click Unlock<br><br>
        Works on any device, any browser. No renewal. No expiry.<br>
        Lost this email? Just reply and we will resend it.<br><br>
        — Nightingale Vault
      </div>
    </div>
    </body></html>
    """

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            "https://api.resend.com/emails",
            headers={"Authorization": f"Bearer {RESEND_API_KEY}", "Content-Type": "application/json"},
            json={"from": FROM_EMAIL, "to": [to],
                  "subject": "Your Nightingale Vault Lifetime Access Code", "html": html},
            timeout=10
        )
        if resp.status_code not in (200, 201, 202):
            print(f"[EMAIL ERROR] {resp.status_code}: {resp.text}")
        else:
            print(f"[EMAIL SENT] {code} → {to}")


# ─────────────────────────────────────────────────────────────────────────────
#  ROUTES
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/health")
def health():
    return {"status": "live", "version": "1.1.0", "codes_loaded": len(VALID_CODES)}


@app.get("/analyze")
def analyze(ticker: str = "BTC-USD", days: int = 90):
    ticker = ticker.upper().strip()
    if days not in (30, 60, 90):
        days = 90
    period_map = {30: "1mo", 60: "2mo", 90: "3mo"}

    try:
        raw = yf.download(ticker, period=period_map[days], interval="1d",
                          auto_adjust=True, progress=False)
        if raw.empty:
            raise HTTPException(status_code=404,
                detail=f"No data for '{ticker}'. Try AAPL, BTC-USD, ETH-USD, SPY, QQQ.")

        if isinstance(raw.columns, pd.MultiIndex):
            raw.columns = raw.columns.get_level_values(0)

        df = raw[["Close"]].copy()
        df.columns = ["close"]
        df.dropna(inplace=True)
        df["return_pct"] = df["close"].pct_change() * 100
        ret = df["return_pct"].dropna()

        win_days   = int((ret > 0).sum())
        loss_days  = int((ret < 0).sum())
        flat_days  = int((ret == 0).sum())
        total_days = int(len(ret))
        win_rate   = round((win_days / total_days) * 100, 1) if total_days else 0
        cur_price  = round(float(df["close"].iloc[-1]), 4)
        start_price= round(float(df["close"].iloc[0]),  4)
        total_ret  = round(((cur_price - start_price) / start_price) * 100, 2)

        return {
            "ticker": ticker, "days": days,
            "win_rate": win_rate, "win_days": win_days, "loss_days": loss_days,
            "flat_days": flat_days, "total_days": total_days,
            "current_price": cur_price, "start_price": start_price,
            "total_return": total_ret,
            "avg_daily_move": round(float(ret.abs().mean()), 3),
            "max_drop":  round(float(ret.min()), 3),
            "best_day":  round(float(ret.max()), 3),
            "volatility": round(float(ret.std()), 3),
            "price_series":  [{"date": i.strftime("%d %b '%y"), "price": round(float(v), 4)}
                               for i, v in df["close"].items()],
            "return_series": [{"date": i.strftime("%d %b '%y"), "return": round(float(v), 3),
                                "green": float(v) > 0}
                               for i, v in ret.items()],
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/validate-code")
async def validate_code(request: Request):
    body = await request.json()
    code = body.get("code", "").strip().upper()
    if not code:
        return {"valid": False, "reason": "No code provided"}
    entry = VALID_CODES.get(code)
    if not entry or not entry.get("active", False):
        return {"valid": False, "reason": "Code not recognised"}
    # Lifetime: as long as active=True it always validates — no counter, no expiry
    return {"valid": True, "type": entry["type"], "message": "Lifetime access granted"}


@app.post("/stripe-webhook")
async def stripe_webhook(request: Request, stripe_signature: str = Header(None)):
    """
    Wire this URL in Stripe Dashboard → Developers → Webhooks:
      https://vault-qr1s.onrender.com/stripe-webhook
    Events: checkout.session.completed

    Also set your Payment Link success URL to:
      https://vault-qr1s.onrender.com/success?session_id={CHECKOUT_SESSION_ID}
    (Stripe auto-fills the session ID — this is safe, it's not the code itself)
    """
    payload = await request.body()

    if STRIPE_WEBHOOK_SECRET:
        try:
            import stripe as stripe_lib
            event = stripe_lib.Webhook.construct_event(
                payload, stripe_signature, STRIPE_WEBHOOK_SECRET)
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Signature invalid: {e}")
    else:
        event = json.loads(payload)
        print("[STRIPE] WARNING: No STRIPE_WEBHOOK_SECRET set.")

    if event.get("type") == "checkout.session.completed":
        session    = event["data"]["object"]
        email      = (session.get("customer_details", {}).get("email")
                      or session.get("customer_email", ""))
        name       = session.get("customer_details", {}).get("name") or "Trader"
        session_id = session.get("id", "")

        if email:
            code = generate_code()
            VALID_CODES[code] = {
                "type": "paid", "active": True,
                "email": email, "purchased_at": datetime.utcnow().isoformat(),
                "stripe_session": session_id,
            }
            print(f"[STRIPE] Issued: {code} → {email}")
            await send_access_email(email, code, name)   # Delivery 1: email

    return {"received": True}


@app.get("/success", response_class=HTMLResponse)
async def success_page(session_id: str = ""):
    """Delivery 2: show code on screen immediately after Stripe redirects here."""
    code = next(
        (c for c, m in VALID_CODES.items()
         if m.get("stripe_session") == session_id and m.get("type") == "paid"),
        None
    )

    if not code:
        # Webhook hasn't fired yet — auto-refresh every 3 seconds
        return HTMLResponse(content="""<!DOCTYPE html><html>
        <head><meta charset="UTF-8"><meta http-equiv="refresh" content="3">
        <title>Processing — Nightingale Vault</title>
        <style>body{background:#020305;color:#d8e8f4;font-family:monospace;display:flex;
        align-items:center;justify-content:center;min-height:100vh;margin:0;text-align:center}
        .s{font-size:2rem;animation:sp 2s linear infinite;display:inline-block;margin-bottom:20px}
        @keyframes sp{to{transform:rotate(360deg)}}</style></head>
        <body><div><div class="s">◈</div>
        <div style="color:#d4af37;letter-spacing:.1em;margin-bottom:10px">PROCESSING YOUR ACCESS</div>
        <div style="color:#8a9ab8;font-size:.8rem;line-height:1.9">Payment confirmed.<br>
        Generating your lifetime access code...<br>This page refreshes automatically.</div>
        </div></body></html>""")

    site = SITE_URL
    return HTMLResponse(content=f"""<!DOCTYPE html><html>
    <head><meta charset="UTF-8"><title>Access Granted — Nightingale Vault</title>
    <link href="https://fonts.googleapis.com/css2?family=Cinzel:wght@700&family=Share+Tech+Mono&display=swap" rel="stylesheet">
    <style>
    *{{margin:0;padding:0;box-sizing:border-box}}
    body{{background:#020305;color:#d8e8f4;font-family:'Share Tech Mono',monospace;
         display:flex;align-items:center;justify-content:center;min-height:100vh;padding:24px}}
    .wrap{{max-width:500px;width:100%;text-align:center}}
    .brand{{font-family:'Cinzel',serif;color:#d4af37;font-size:1.1rem;letter-spacing:.12em;margin-bottom:4px}}
    .sub{{color:#8a9ab8;font-size:.62rem;letter-spacing:.2em;text-transform:uppercase;margin-bottom:36px}}
    .tick{{font-size:2.4rem;margin-bottom:18px;color:#3dbb78}}
    h1{{font-family:'Cinzel',serif;font-size:1.3rem;color:#f0f6ff;margin-bottom:8px}}
    .desc{{color:#8a9ab8;font-size:.76rem;line-height:1.9;margin-bottom:28px}}
    .cbox{{background:#0c1018;border:1px solid #b8960c;border-radius:8px;padding:26px;margin-bottom:24px}}
    .cl{{font-size:.56rem;letter-spacing:.25em;text-transform:uppercase;color:#8a9ab8;margin-bottom:12px}}
    .code{{font-size:1.8rem;letter-spacing:.22em;color:#d4af37;font-weight:bold;user-select:all}}
    .cp{{display:inline-block;margin-top:14px;padding:8px 20px;background:rgba(184,150,12,.15);
         border:1px solid rgba(212,175,55,.4);border-radius:4px;font-family:'Share Tech Mono',monospace;
         font-size:.66rem;letter-spacing:.12em;color:#d4af37;cursor:pointer;transition:all .2s}}
    .cp:hover{{background:rgba(212,175,55,.28)}}
    .steps{{text-align:left;background:#060810;border:1px solid #1e2535;border-radius:6px;
            padding:18px 22px;margin-bottom:22px;font-size:.73rem;line-height:2.2;color:#8a9ab8}}
    .steps strong{{color:#d8e8f4}}
    .go{{display:inline-block;padding:13px 30px;background:linear-gradient(135deg,rgba(184,150,12,.2),rgba(212,175,55,.12));
         border:1px solid rgba(212,175,55,.5);border-radius:5px;font-family:'Share Tech Mono',monospace;
         font-size:.74rem;letter-spacing:.14em;text-transform:uppercase;color:#d4af37;text-decoration:none;transition:all .2s}}
    .go:hover{{background:rgba(212,175,55,.28)}}
    .note{{font-size:.6rem;color:#2a3548;margin-top:18px;line-height:1.8}}
    </style></head>
    <body><div class="wrap">
      <div class="brand">NIGHTINGALE VAULT</div>
      <div class="sub">Strategy Analyser · Lifetime Access</div>
      <div class="tick">✓</div>
      <h1>Payment Confirmed</h1>
      <div class="desc">Your lifetime access code is ready.<br>
      We've also emailed it to you as a backup.</div>
      <div class="cbox">
        <div class="cl">Your Lifetime Access Code</div>
        <div class="code" id="cd">{code}</div>
        <button class="cp" onclick="(()=>{{navigator.clipboard.writeText('{code}');this.textContent='✓ Copied';setTimeout(()=>this.textContent='Copy Code',2000)}}).call(this)">Copy Code</button>
      </div>
      <div class="steps">
        <strong>How to use it:</strong><br>
        1. Click the button below to open the Vault<br>
        2. Click <strong>Learn to Trade</strong> in the top nav<br>
        3. Paste your code and click <strong>Unlock</strong><br>
        4. You're in — permanently.
      </div>
      <a href="{site}" class="go">→ Open Nightingale Vault</a>
      <div class="note">This code never expires · Works on any device · Lost it? Check your email</div>
    </div></body></html>""")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000, reload=True)
