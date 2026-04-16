import os

# ── Telegram ──────────────────────────────────────────────────────────────────
TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
BOT_USERNAME = os.environ.get("BOT_USERNAME", "spufa_bot")   # e.g. "spufa_bot" (no @)

# ── Admin ─────────────────────────────────────────────────────────────────────
# Your personal Telegram user_id — get it by messaging @userinfobot
ADMIN_TELEGRAM_ID = int(os.environ.get("ADMIN_TELEGRAM_ID", "0"))

# ── OxaPay ───────────────────────────────────────────────────────────────────
OXAPAY_MERCHANT_KEY  = os.environ["OXAPAY_MERCHANT_KEY"]
OXAPAY_PAYOUT_KEY    = os.environ.get("OXAPAY_PAYOUT_KEY", "")   # for sending payouts
OXAPAY_WEBHOOK_SECRET = os.environ.get("OXAPAY_WEBHOOK_SECRET", "")
OXAPAY_BASE_URL      = "https://api.oxapay.com"

# ── Webhook / server ──────────────────────────────────────────────────────────
# Railway sets PORT automatically; WEBHOOK_PORT is a fallback for local use
WEBHOOK_PORT = int(os.environ.get("PORT", os.environ.get("WEBHOOK_PORT", "5001")))

# Priority: WEBHOOK_BASE_URL (manual) > RAILWAY_PUBLIC_DOMAIN > localhost
_webhook_base = os.environ.get("WEBHOOK_BASE_URL", "")
if not _webhook_base:
    _railway_domain = os.environ.get("RAILWAY_PUBLIC_DOMAIN", "")
    if _railway_domain:
        _webhook_base = f"https://{_railway_domain}"
    else:
        _webhook_base = f"http://localhost:{WEBHOOK_PORT}"

WEBHOOK_BASE_URL     = _webhook_base
OXAPAY_CALLBACK_URL  = f"{WEBHOOK_BASE_URL}/webhook/oxapay"

# ── Coins / pricing ───────────────────────────────────────────────────────────
SC_PRICE_USD  = 10        # USD per top-up
SC_AMOUNT     = 100       # coins per top-up

# ── Referral ──────────────────────────────────────────────────────────────────
REFERRAL_PERCENT     = 0.25           # 25 % of each referred payment
MIN_WITHDRAWAL_USD   = 5.0            # minimum USD to request a withdrawal
