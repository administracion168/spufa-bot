import asyncio
import io
import logging
import os
import sys
import time

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

import config
import database
import payments
import processor

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    level=logging.INFO,
    stream=sys.stdout,
)
logger = logging.getLogger(__name__)

# ── States ────────────────────────────────────────────────────────────────────
STATE_IDLE             = "idle"
STATE_WAITING_FILE     = "waiting_file"
STATE_WAITING_VARIANTS = "waiting_variants"
STATE_WAITING_WALLET   = "waiting_wallet"   # withdrawal: waiting for wallet address

_user_states: dict = {}


def _get_state(uid: int) -> dict:
    return _user_states.get(uid, {"state": STATE_IDLE})


def _set_state(uid: int, state: str, **kw):
    _user_states[uid] = {"state": state, **kw}


# ── Keyboards ─────────────────────────────────────────────────────────────────

def _kb_main() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🪙 Balance",    callback_data="balance"),
            InlineKeyboardButton("➕ Top Up",     callback_data="topup"),
        ],
        [InlineKeyboardButton("🎨 Spoof Content", callback_data="spoof")],
        [InlineKeyboardButton("🔗 Referidos & Ganancias", callback_data="referidos")],
    ])


def _kb_back() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data="back")]])


def _kb_home() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("🎨 Spoof Again", callback_data="spoof"),
        InlineKeyboardButton("🏠 Home",        callback_data="home"),
    ]])


def _kb_variants() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("1 variant",  callback_data="v_1"),
            InlineKeyboardButton("5 variants", callback_data="v_5"),
            InlineKeyboardButton("10 variants", callback_data="v_10"),
        ],
        [
            InlineKeyboardButton("20 variants", callback_data="v_20"),
            InlineKeyboardButton("30 variants", callback_data="v_30"),
        ],
    ])


def _kb_referidos(has_earnings: bool, has_pending: bool) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton("📋 Copiar mi link de invitación", callback_data="ref_copy_link")]]
    if has_earnings and not has_pending:
        rows.append([InlineKeyboardButton("💸 Solicitar retiro", callback_data="ref_withdraw")])
    rows.append([InlineKeyboardButton("⬅️ Back", callback_data="back")])
    return InlineKeyboardMarkup(rows)


# Network / currency options for withdrawal
WITHDRAWAL_NETWORKS = [
    ("USDT TRC-20", "TRX",  "USDT"),
    ("USDT ERC-20", "ETH",  "USDT"),
    ("BTC",         "BTC",  "BTC"),
    ("ETH",         "ETH",  "ETH"),
]


def _kb_networks() -> InlineKeyboardMarkup:
    rows = []
    for label, network, currency in WITHDRAWAL_NETWORKS:
        rows.append([InlineKeyboardButton(label, callback_data=f"net_{network}_{currency}")])
    rows.append([InlineKeyboardButton("❌ Cancelar", callback_data="referidos")])
    return InlineKeyboardMarkup(rows)


# ── Text helpers ──────────────────────────────────────────────────────────────

def _welcome_text(balance: int) -> str:
    return (
        "✨ *Welcome to Spufa\\!*\n\n"
        "Your content uniquifier — bypass duplicate detection on any platform\\.\n\n"
        f"💰 Balance: *{balance} Spufa Coins*"
    )


def _referidos_text(user_id: int) -> str:
    stats = database.get_referral_stats(user_id)
    link  = f"https://t.me/{config.BOT_USERNAME}?start=ref_{user_id}"

    total_usd     = stats["total_earned_cents"] / 100
    available_usd = stats["available_cents"] / 100
    paid_usd      = stats["paid_out_cents"] / 100
    min_wd        = config.MIN_WITHDRAWAL_USD

    pending_line = ""
    if stats["pending_withdrawal"]:
        pw_usd = stats["pending_withdrawal"]["amount_cents"] / 100
        pending_line = f"\n⏳ Retiro pendiente: *${pw_usd:.2f}*"

    available_line = (
        f"💵 Disponible para retirar: *${available_usd:.2f}*"
        if available_usd >= min_wd
        else f"💵 Disponible: *${available_usd:.2f}* _(mínimo ${min_wd:.0f})_"
    )

    return (
        "🔗 *Tu programa de referidos*\n\n"
        f"👥 Personas invitadas: *{stats['referred_count']}*\n"
        f"💰 Total ganado: *${total_usd:.2f}*\n"
        f"{available_line}\n"
        f"✅ Ya cobrado: *${paid_usd:.2f}*"
        f"{pending_line}\n\n"
        f"📤 *Tu link personal:*\n`{link}`\n\n"
        "_Cada vez que alguien pague usando tu link, recibes el 25% en USD real\\._"
    )


# ── /start command ────────────────────────────────────────────────────────────

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user    = update.effective_user
    db_user = database.get_or_create_user(user.id, user.username)
    _set_state(user.id, STATE_IDLE)

    # Handle referral deep link: /start ref_<referrer_id>
    if ctx.args:
        arg = ctx.args[0]
        if arg.startswith("ref_"):
            try:
                referrer_id = int(arg[4:])
                if referrer_id != user.id:
                    was_set = database.set_referral(user.id, referrer_id)
                    if was_set:
                        logger.info(f"User {user.id} referred by {referrer_id}")
                        # notify the referrer
                        try:
                            await ctx.bot.send_message(
                                chat_id=referrer_id,
                                text=(
                                    f"🎉 ¡Alguien acaba de unirse usando tu link de invitación!\n"
                                    f"Ganarás el 25% de cada pago que hagan. 🔥"
                                ),
                            )
                        except Exception:
                            pass   # referrer might have blocked the bot
            except ValueError:
                pass

    await update.message.reply_text(
        _welcome_text(db_user["balance"]),
        reply_markup=_kb_main(),
        parse_mode="MarkdownV2",
    )


# ── /referidos command ────────────────────────────────────────────────────────

async def cmd_referidos(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user  = update.effective_user
    database.get_or_create_user(user.id, user.username)
    stats = database.get_referral_stats(user.id)

    available_usd = stats["available_cents"] / 100
    has_earnings  = available_usd >= config.MIN_WITHDRAWAL_USD
    has_pending   = stats["pending_withdrawal"] is not None

    await update.message.reply_text(
        _referidos_text(user.id),
        reply_markup=_kb_referidos(has_earnings, has_pending),
        parse_mode="MarkdownV2",
    )


# ── /admin command ────────────────────────────────────────────────────────────

async def cmd_admin(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if user.id != config.ADMIN_TELEGRAM_ID:
        return

    pending = database.get_pending_withdrawals()
    if not pending:
        await update.message.reply_text("✅ No hay retiros pendientes.")
        return

    for wd in pending:
        usd  = wd["amount_cents"] / 100
        who  = wd["username"] or str(wd["user_id"])
        text = (
            f"💸 *Retiro #{wd['id']}*\n"
            f"👤 @{who} (id: `{wd['user_id']}`)\n"
            f"💵 ${usd:.2f}\n"
            f"🪙 {wd['currency']} via {wd['network']}\n"
            f"📬 `{wd['wallet_address']}`"
        )
        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton("✅ Aprobar & Pagar", callback_data=f"admin_pay_{wd['id']}"),
            InlineKeyboardButton("❌ Rechazar",        callback_data=f"admin_reject_{wd['id']}"),
        ]])
        await update.message.reply_text(text, reply_markup=kb, parse_mode="Markdown")


# ── Callback router ───────────────────────────────────────────────────────────

async def on_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q    = update.callback_query
    await q.answer()
    user    = q.from_user
    db_user = database.get_or_create_user(user.id, user.username)
    data    = q.data

    # ── Navigation ────────────────────────────────────────────────────────────
    if data in ("home", "back"):
        _set_state(user.id, STATE_IDLE)
        await q.edit_message_text(
            _welcome_text(db_user["balance"]),
            reply_markup=_kb_main(),
            parse_mode="MarkdownV2",
        )

    # ── Balance ───────────────────────────────────────────────────────────────
    elif data == "balance":
        text = (
            "💼 *Your Spufa Wallet*\n\n"
            f"🪙 Balance: *{db_user['balance']} Spufa Coins*\n"
            f"📊 Total used: {db_user['total_used']} SC\n"
            f"📥 Total topped up: {db_user['total_topped_up']} SC"
        )
        await q.edit_message_text(text, reply_markup=_kb_back(), parse_mode="Markdown")

    # ── Top Up ────────────────────────────────────────────────────────────────
    elif data == "topup":
        await q.edit_message_text("⏳ Generating your payment link\\.\\.\\.", parse_mode="MarkdownV2")
        try:
            result = payments.create_payment(user.id)
            if result["success"]:
                kb = InlineKeyboardMarkup([
                    [InlineKeyboardButton("💳 Pay $10 → 100 SC", url=result["pay_link"])],
                    [InlineKeyboardButton("⬅️ Back", callback_data="back")],
                ])
                await q.edit_message_text(
                    "💳 *Top Up Your Wallet*\n\n"
                    "💰 Price: *$10 = 100 Spufa Coins*\n\n"
                    "Click below to pay with crypto via OxaPay\\.\n"
                    "Your coins will be added automatically once payment is confirmed\\! ✅",
                    reply_markup=kb,
                    parse_mode="MarkdownV2",
                )
            else:
                await q.edit_message_text(
                    "😕 Couldn't generate payment link\\. Please try again\\!",
                    reply_markup=_kb_back(),
                    parse_mode="MarkdownV2",
                )
        except Exception as e:
            logger.error(f"Payment creation error: {e}")
            await q.edit_message_text(
                "😕 Something went wrong\\. Please try again\\!",
                reply_markup=_kb_back(),
                parse_mode="MarkdownV2",
            )

    # ── Spoof ─────────────────────────────────────────────────────────────────
    elif data == "spoof":
        if db_user["balance"] <= 0:
            await q.edit_message_text(
                "❌ You have no Spufa Coins\\. Top up to get started\\!",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("➕ Top Up", callback_data="topup")]
                ]),
                parse_mode="MarkdownV2",
            )
            return
        _set_state(user.id, STATE_WAITING_FILE)
        await q.edit_message_text(
            "📤 *Send me your content\\!*\n\n"
            "Supported formats:\n"
            "🖼️ Images: JPG, PNG, WEBP, HEIC\n"
            "🎬 Videos: MP4, MOV, AVI, MKV, WEBM\n\n"
            "📏 Max file size: 50MB",
            parse_mode="MarkdownV2",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("❌ Cancel", callback_data="home")]
            ]),
        )

    # ── Variants ──────────────────────────────────────────────────────────────
    elif data.startswith("v_"):
        state = _get_state(user.id)
        if state["state"] != STATE_WAITING_VARIANTS:
            await q.edit_message_text(
                "😕 Session expired\\. Please start again\\!",
                reply_markup=_kb_main(),
                parse_mode="MarkdownV2",
            )
            return

        count      = int(data.split("_")[1])
        file_bytes = state.get("file_bytes")
        filename   = state.get("filename", "file.jpg")

        if not file_bytes:
            await q.edit_message_text(
                "😕 File not found\\. Please try again\\!",
                reply_markup=_kb_main(),
                parse_mode="MarkdownV2",
            )
            return

        database.deduct_coins(user.id, 1)
        db_user = database.get_user(user.id)
        _set_state(user.id, STATE_IDLE)

        is_vid      = processor.is_video(filename)
        progress_msg = await q.edit_message_text(
            f"⏳ Spufa is working her magic\\.\\.\\. \\(0/{count} done\\)",
            parse_mode="MarkdownV2",
        )

        try:
            if is_vid:
                gif_bytes = processor.process_video(file_bytes)
                await ctx.bot.send_document(
                    chat_id=user.id,
                    document=io.BytesIO(gif_bytes),
                    filename=f"spufa_{int(time.time())}.gif",
                )
                await progress_msg.edit_text(
                    f"✅ Done\\! Video converted to GIF\\.\n🪙 Remaining balance: {db_user['balance']} SC",
                    reply_markup=_kb_home(),
                    parse_mode="MarkdownV2",
                )
            else:
                variants  = []
                base_seed = int(time.time() * 1000)
                for i in range(count):
                    variants.append(processor.process_image(file_bytes, seed=base_seed + i))
                    step = max(1, count // 5)
                    if (i + 1) % step == 0 or i == count - 1:
                        await progress_msg.edit_text(
                            f"⏳ Spufa is working her magic\\.\\.\\. \\({i + 1}/{count} done\\)",
                            parse_mode="MarkdownV2",
                        )

                if count == 1:
                    await ctx.bot.send_document(
                        chat_id=user.id,
                        document=io.BytesIO(variants[0]),
                        filename=f"spufa_{int(time.time())}.jpg",
                    )
                else:
                    zip_data = processor.create_zip([
                        (f"spufa_{i + 1:03d}.jpg", v) for i, v in enumerate(variants)
                    ])
                    await ctx.bot.send_document(
                        chat_id=user.id,
                        document=io.BytesIO(zip_data),
                        filename=f"spufa_{int(time.time())}.zip",
                    )

                label = "variant" if count == 1 else "variants"
                await progress_msg.edit_text(
                    f"✅ Done\\! {count} {label} delivered\\.\n🪙 Remaining balance: {db_user['balance']} SC",
                    reply_markup=_kb_home(),
                    parse_mode="MarkdownV2",
                )
        except Exception as e:
            logger.error(f"Processing error for user {user.id}: {e}", exc_info=True)
            await progress_msg.edit_text(
                "😕 Something went wrong during processing\\. Please try again\\!",
                reply_markup=_kb_main(),
                parse_mode="MarkdownV2",
            )

    # ── Referidos panel ───────────────────────────────────────────────────────
    elif data == "referidos":
        stats         = database.get_referral_stats(user.id)
        available_usd = stats["available_cents"] / 100
        has_earnings  = available_usd >= config.MIN_WITHDRAWAL_USD
        has_pending   = stats["pending_withdrawal"] is not None
        await q.edit_message_text(
            _referidos_text(user.id),
            reply_markup=_kb_referidos(has_earnings, has_pending),
            parse_mode="MarkdownV2",
        )

    elif data == "ref_copy_link":
        link = f"https://t.me/{config.BOT_USERNAME}?start=ref_{user.id}"
        await q.edit_message_text(
            f"🔗 *Tu link de invitación:*\n\n`{link}`\n\n"
            "Compártelo con tus socios\\. Cada vez que paguen, recibes el 25% en USD real\\.",
            reply_markup=_kb_back(),
            parse_mode="MarkdownV2",
        )

    # ── Withdrawal flow ───────────────────────────────────────────────────────
    elif data == "ref_withdraw":
        stats         = database.get_referral_stats(user.id)
        available_usd = stats["available_cents"] / 100
        if available_usd < config.MIN_WITHDRAWAL_USD:
            await q.edit_message_text(
                f"❌ Necesitas al menos *${config.MIN_WITHDRAWAL_USD:.0f}* para retirar\\.\n"
                f"Tienes disponible: *${available_usd:.2f}*",
                reply_markup=_kb_back(),
                parse_mode="MarkdownV2",
            )
            return
        if stats["pending_withdrawal"]:
            await q.edit_message_text(
                "⏳ Ya tienes un retiro pendiente de aprobación\\.",
                reply_markup=_kb_back(),
                parse_mode="MarkdownV2",
            )
            return

        await q.edit_message_text(
            f"💸 *Solicitar retiro*\n\n"
            f"💵 Disponible: *${available_usd:.2f}*\n\n"
            "Elige la red / moneda en la que quieres recibir el pago:",
            reply_markup=_kb_networks(),
            parse_mode="MarkdownV2",
        )

    elif data.startswith("net_"):
        # net_<NETWORK>_<CURRENCY>
        parts    = data.split("_", 2)
        network  = parts[1]
        currency = parts[2]
        _set_state(user.id, STATE_WAITING_WALLET, network=network, currency=currency)
        await q.edit_message_text(
            f"📬 *Introduce tu dirección {currency} \\({network}\\):*\n\n"
            "Escríbela o pégala aquí abajo\\.",
            parse_mode="MarkdownV2",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("❌ Cancelar", callback_data="referidos")]
            ]),
        )

    # ── Admin callbacks ───────────────────────────────────────────────────────
    elif data.startswith("admin_pay_") or data.startswith("admin_reject_"):
        if user.id != config.ADMIN_TELEGRAM_ID:
            return

        action = "pay" if data.startswith("admin_pay_") else "reject"
        wd_id  = int(data.split("_")[-1])

        if action == "reject":
            database.update_withdrawal_status(wd_id, "rejected", admin_note="Rejected by admin")
            await q.edit_message_text(f"❌ Retiro #{wd_id} rechazado.")
            return

        # action == "pay"
        pending = database.get_pending_withdrawals()
        wd = next((w for w in pending if w["id"] == wd_id), None)
        if not wd:
            await q.edit_message_text("⚠️ Retiro no encontrado o ya procesado.")
            return

        amount_usd = wd["amount_cents"] / 100
        await q.edit_message_text(f"⏳ Enviando ${amount_usd:.2f} via OxaPay…")

        result = payments.send_payout(
            wallet_address=wd["wallet_address"],
            network=wd["network"],
            currency=wd["currency"],
            amount_usd=amount_usd,
        )

        if result["success"]:
            database.update_withdrawal_status(wd_id, "paid", oxapay_track_id=result["track_id"])
            # Notify the user
            try:
                await ctx.bot.send_message(
                    chat_id=wd["user_id"],
                    text=(
                        f"✅ ¡Tu retiro de *${amount_usd:.2f}* ha sido enviado!\n"
                        f"Track ID OxaPay: `{result['track_id']}`"
                    ),
                    parse_mode="Markdown",
                )
            except Exception:
                pass
            await q.edit_message_text(
                f"✅ Retiro #{wd_id} pagado. Track ID: `{result['track_id']}`",
                parse_mode="Markdown",
            )
        else:
            database.update_withdrawal_status(wd_id, "failed", admin_note=result["error"])
            await q.edit_message_text(
                f"❌ Error al pagar el retiro #{wd_id}:\n{result['error']}"
            )


# ── File handler ──────────────────────────────────────────────────────────────

async def on_file(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user  = update.effective_user
    state = _get_state(user.id)
    msg   = update.message

    # ── Wallet address input (withdrawal flow) ────────────────────────────────
    if state["state"] == STATE_WAITING_WALLET:
        wallet_address = msg.text.strip() if msg.text else ""
        if not wallet_address:
            await msg.reply_text("❌ Dirección inválida. Inténtalo de nuevo o cancela.")
            return

        network  = state.get("network", "")
        currency = state.get("currency", "")

        stats         = database.get_referral_stats(user.id)
        available_usd = stats["available_cents"] / 100
        if available_usd < config.MIN_WITHDRAWAL_USD:
            _set_state(user.id, STATE_IDLE)
            await msg.reply_text(
                f"❌ Saldo insuficiente. Necesitas ${config.MIN_WITHDRAWAL_USD:.0f} mínimo.",
                reply_markup=_kb_main(),
            )
            return

        wd_id = database.create_withdrawal(
            user_id=user.id,
            amount_cents=stats["available_cents"],
            wallet_address=wallet_address,
            network=network,
            currency=currency,
        )
        _set_state(user.id, STATE_IDLE)

        await msg.reply_text(
            f"✅ *Solicitud de retiro enviada*\n\n"
            f"💵 Importe: *${available_usd:.2f}*\n"
            f"🪙 {currency} via {network}\n"
            f"📬 `{wallet_address}`\n\n"
            "El admin revisará tu solicitud y recibirás el pago en breve.",
            parse_mode="Markdown",
            reply_markup=_kb_main(),
        )

        # Notify admin
        if config.ADMIN_TELEGRAM_ID:
            try:
                await ctx.bot.send_message(
                    chat_id=config.ADMIN_TELEGRAM_ID,
                    text=(
                        f"💸 *Nuevo retiro #{wd_id}*\n"
                        f"👤 @{user.username or user.id} (id: `{user.id}`)\n"
                        f"💵 ${available_usd:.2f}\n"
                        f"🪙 {currency} via {network}\n"
                        f"📬 `{wallet_address}`\n\n"
                        "Usa /admin para gestionar los retiros."
                    ),
                    parse_mode="Markdown",
                )
            except Exception as e:
                logger.error(f"Failed to notify admin: {e}")
        return

    # ── Normal file upload (spoof flow) ───────────────────────────────────────
    if state["state"] != STATE_WAITING_FILE:
        return

    file_obj = None
    filename = "file.jpg"

    if msg.document:
        file_obj = msg.document
        filename = msg.document.file_name or "file.bin"
    elif msg.photo:
        file_obj = msg.photo[-1]
        filename = "photo.jpg"
    elif msg.video:
        file_obj = msg.video
        filename = msg.video.file_name or "video.mp4"

    if not file_obj:
        await msg.reply_text("😕 Couldn't read the file. Please try again!")
        return

    if not (processor.is_image(filename) or processor.is_video(filename)):
        await msg.reply_text(
            "❌ Unsupported format!\n\n"
            "🖼️ Images: JPG, PNG, WEBP, HEIC\n"
            "🎬 Videos: MP4, MOV, AVI, MKV, WEBM"
        )
        return

    if hasattr(file_obj, "file_size") and file_obj.file_size and file_obj.file_size > 50 * 1024 * 1024:
        await msg.reply_text("❌ File too large! Max size is 50MB.")
        return

    status_msg = await msg.reply_text("📥 Downloading your file...")
    try:
        tg_file    = await ctx.bot.get_file(file_obj.file_id)
        buf        = io.BytesIO()
        await tg_file.download_to_memory(buf)
        file_bytes = buf.getvalue()
    except Exception as e:
        logger.error(f"Download error: {e}")
        await status_msg.edit_text("😕 Failed to download file. Please try again!")
        return

    _set_state(user.id, STATE_WAITING_VARIANTS, file_bytes=file_bytes, filename=filename)
    await status_msg.edit_text(
        "🎛️ *How many variants do you want?*\n\nAll options cost just 1 🪙 Spufa Coin",
        reply_markup=_kb_variants(),
        parse_mode="Markdown",
    )


# ── Text message handler (catches wallet address input) ───────────────────────

async def on_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Route plain text messages — currently only used for wallet address input."""
    user  = update.effective_user
    state = _get_state(user.id)
    if state["state"] == STATE_WAITING_WALLET:
        await on_file(update, ctx)   # reuse the same handler logic


# ── Payment notification drain ────────────────────────────────────────────────

async def _drain_notifications_loop(bot):
    while True:
        while not payments.notification_queue.empty():
            try:
                user_id, text = payments.notification_queue.get_nowait()
                await bot.send_message(chat_id=user_id, text=text, parse_mode="Markdown")
            except Exception as e:
                logger.error(f"Failed to send payment notification: {e}")
        await asyncio.sleep(2)


async def _post_init(application: Application):
    asyncio.create_task(_drain_notifications_loop(application.bot))


# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    database.init_db()
    logger.info(f"Database initialized at: {database.DB_PATH}")

    payments.start_webhook_server(config.WEBHOOK_PORT)
    logger.info(f"OxaPay callback URL: {config.OXAPAY_CALLBACK_URL}")

    application = (
        Application.builder()
        .token(config.TELEGRAM_BOT_TOKEN)
        .post_init(_post_init)
        .build()
    )

    application.add_handler(CommandHandler("start",     cmd_start))
    application.add_handler(CommandHandler("referidos", cmd_referidos))
    application.add_handler(CommandHandler("admin",     cmd_admin))
    application.add_handler(CallbackQueryHandler(on_callback))
    application.add_handler(
        MessageHandler(filters.Document.ALL | filters.PHOTO | filters.VIDEO, on_file)
    )
    application.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, on_text)
    )

    logger.info("Spufa bot starting with long polling...")
    application.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)


if __name__ == "__main__":
    main()
