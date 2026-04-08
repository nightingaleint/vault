# Nightingale Vault — Deployment Guide
## Your setup: Render at vault-qr1s.onrender.com

---

## What you have after this update

| File | Purpose |
|---|---|
| `index.html` | Frontend — already wired to vault-qr1s.onrender.com |
| `main.py` | Backend — FastAPI + yfinance + Stripe webhook + email |
| `requirements.txt` | Python dependencies |
| `render.yaml` | Render auto-config |

---

## Step 1 — Push the backend to Render

You already have vault-qr1s.onrender.com. Push main.py and requirements.txt to the GitHub repo connected to that service.

```
git add main.py requirements.txt render.yaml
git commit -m "Nightingale v1.1 — lifetime access + Stripe"
git push
```

Render auto-redeploys. Verify it's live:
  https://vault-qr1s.onrender.com/health
Expected: {"status":"live","version":"1.1.0"}

---

## Step 2 — Set environment variables in Render

Render Dashboard → vault-qr1s → Environment → Add:

  STRIPE_WEBHOOK_SECRET   whsec_xxxx          (from Step 3)
  RESEND_API_KEY          re_xxxx             (from resend.com)
  FROM_EMAIL              vault@yourdomain.com
  SITE_URL                https://nightingalevault.com

---

## Step 3 — Stripe setup (15 min)

CREATE PAYMENT LINK:
1. Stripe Dashboard → Payment Links → Create
2. Set your price (one-time, e.g. £29)
3. After payment → Redirect to custom URL:
   https://vault-qr1s.onrender.com/success?session_id={CHECKOUT_SESSION_ID}
   (Stripe fills in the session ID automatically — this is safe)
4. Copy the payment link → paste into index.html where it says buy.stripe.com/YOUR_LINK

ADD WEBHOOK:
1. Stripe → Developers → Webhooks → Add endpoint
2. URL: https://vault-qr1s.onrender.com/stripe-webhook
3. Event: checkout.session.completed
4. Copy the Signing secret → paste into Render as STRIPE_WEBHOOK_SECRET

---

## Step 4 — Resend email setup (5 min)

1. Sign up free at https://resend.com (3,000 emails/month free)
2. Add your domain → verify DNS records
3. Create API key → paste into Render as RESEND_API_KEY

---

## What happens when someone pays

  Customer pays via Stripe
       ↓
  Stripe fires webhook → backend generates NV-XXXX-XXXX code
       ↓
  Code stored with active=True (lifetime — never expires)
       ↓ (both happen simultaneously)
  Email sent via Resend          Customer sees code on /success page
       ↓
  Customer enters code → Learn to Trade → permanently unlocked

---

## Test it

Stripe test card: 4242 4242 4242 4242 (any future date, any CVC)

---

## Before going live

- [ ] /health returns live
- [ ] All 4 env vars set in Render
- [ ] Stripe Payment Link with correct success URL
- [ ] Webhook pointing to /stripe-webhook with signing secret
- [ ] Resend domain verified
- [ ] Test purchase → code arrives by email AND on screen
- [ ] Code unlocks Learn to Trade on site
- [ ] Replace allow_origins=["*"] with your real domain in main.py
- [ ] Migrate VALID_CODES to Supabase (free) before launch so codes
      survive Render restarts and redeployments
