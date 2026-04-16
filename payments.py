import hashlib
import hmac
import json
import logging
import queue
import threading
import time

import requests
from flask import Flask, request, jsonify

import config
import database

logger = logging.getLogger(__name__)

app = Flask(__name__)

# Thread-safe queue so the async Telegram bot can send messages from Flask callbacks
notification_queue: queue.Queue = queue.Queue()


# ── Payment creation ──────────────────────────────────────────────────────────

def create_payment(user_id: int) -> dict:
    order_id = f"spufa_{user_id}_{int(time.time())}"

    payload = {
        "merchant":    config.OXAPAY_MERCHANT_KEY,
        "amount":      config.SC_PRICE_USD,
        "currency":    "USD",
        "lifeTime":    30,
        "callbackUrl": config.OXAPAY_CALLBACK_URL,
        "returnUrl":   f"https://t.me/{config.BOT_USERNAME}",
        "description": f"Spufa Coins — {config.SC_AMOUNT} SC",
        "orderId":     order_id,
    }

    resp = requests.post(
        f"{config.OXAPAY_BASE_URL}/merchants/request",
        json=payload,
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json()

    if data.get("result") == 100:
        return {
            "success":  True,
            "pay_link": data["payLink"],
            "order_id": order_id,
            "track_id": data.get("trackId", ""),
        }
    return {
        "success": False,
        "error":   data.get("message", "Unknown error"),
    }


# ── Payout (referral withdrawals) ─────────────────────────────────────────────

def send_payout(wallet_address: str, network: str, currency: str, amount_usd: float) -> dict:
    """
    Send a payout via OxaPay Payout API v1.
    Returns {"success": True, "track_id": "..."} or {"success": False, "error": "..."}.
    Requires OXAPAY_PAYOUT_KEY to be set.
    """
    if not config.OXAPAY_PAYOUT_KEY:
        return {"success": False, "error": "OXAPAY_PAYOUT_KEY not configured"}

    headers = {
        "payout_api_key": config.OXAPAY_PAYOUT_KEY,
        "Content-Type":   "application/json",
    }
    payload = {
        "address":     wallet_address,
        "amount":      amount_usd,
        "currency":    currency,
        "network":     network,
        "description": "Spufa referral withdrawal",
    }

    try:
        resp = requests.post(
            f"{config.OXAPAY_BASE_URL}/v1/payout",
            headers=headers,
            json=payload,
            timeout=20,
        )
        resp.raise_for_status()
        data = resp.json()

        # v1 API returns status 200 with {"status": 200, "data": {...}}
        if resp.status_code == 200 and data.get("status") in (200, None):
            track_id = (data.get("data") or {}).get("track_id", data.get("trackId", ""))
            return {"success": True, "track_id": track_id}
        return {"success": False, "error": data.get("message", "Unknown error")}
    except Exception as e:
        return {"success": False, "error": str(e)}


# ── Webhook signature verification ────────────────────────────────────────────

def _verify_oxapay_signature(raw_body: bytes, received_hmac: str) -> bool:
    """Verify HMAC-SHA512 signature from OxaPay if a secret is configured."""
    if not config.OXAPAY_WEBHOOK_SECRET:
        return True   # no secret configured → skip verification
    expected = hmac.new(
        config.OXAPAY_WEBHOOK_SECRET.encode(),
        raw_body,
        hashlib.sha512,
    ).hexdigest()
    return hmac.compare_digest(expected, received_hmac)


# ── Flask webhook routes ───────────────────────────────────────────────────────

@app.route("/webhook/oxapay", methods=["POST"])
def oxapay_webhook():
    raw_body = request.get_data()
    received_hmac = request.headers.get("HMAC", "")

    if not _verify_oxapay_signature(raw_body, received_hmac):
        logger.warning("OxaPay webhook: invalid HMAC signature — request ignored")
        return jsonify({"error": "Invalid signature"}), 403

    data = json.loads(raw_body) if raw_body else {}

    status   = data.get("status", "")
    order_id = data.get("orderId", "")

    if status.lower() != "paid":
        return jsonify({"ok": True}), 200

    # Parse user_id from order_id (format: spufa_<user_id>_<timestamp>)
    try:
        parts   = order_id.split("_")
        user_id = int(parts[1])
    except (IndexError, ValueError):
        logger.warning(f"Invalid order_id in webhook: {order_id}")
        return jsonify({"error": "Invalid order ID"}), 400

    credited = database.credit_coins(user_id, config.SC_AMOUNT, order_id)
    if credited:
        notification_queue.put((
            user_id,
            f"✅ Payment confirmed! {config.SC_AMOUNT} Spufa Coins added to your wallet. 🪙",
        ))
        logger.info(f"Credited {config.SC_AMOUNT} SC to user {user_id} for order {order_id}")

        # ── Referral commission ───────────────────────────────────────────────
        user = database.get_user(user_id)
        referrer_id = user.get("referred_by") if user else None
        if referrer_id:
            commission_cents = int(config.SC_PRICE_USD * 100 * config.REFERRAL_PERCENT)
            database.add_referral_commission(referrer_id, commission_cents)
            commission_usd = commission_cents / 100
            notification_queue.put((
                referrer_id,
                f"🎉 One of your referrals just paid! "
                f"You earned *${commission_usd:.2f}* in referral commission.\n"
                f"Use /referidos to check your balance.",
            ))
            logger.info(
                f"Referral commission ${commission_usd:.2f} credited to user {referrer_id} "
                f"(referred {user_id})"
            )

    return jsonify({"ok": True}), 200


@app.route("/webhook/health", methods=["GET"])
def health():
    return jsonify({"status": "ok"}), 200


# ── Server startup ────────────────────────────────────────────────────────────

def start_webhook_server(port: int):
    thread = threading.Thread(
        target=lambda: app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False),
        daemon=True,
        name="flask-webhook",
    )
    thread.start()
    logger.info(f"Webhook server started on port {port}")
    return thread
