# Spufa Bot 🪄

Telegram bot that re-uniquifies images and videos (spoofing) to bypass duplicate content detection.

## Features

- 🖼️ Image processing: brightness, saturation, contrast, rotation, flip, crop, EXIF replacement, noise bytes
- 🎬 Video → GIF conversion with palette optimization
- 🪙 Coin economy (1 SC per job, any variant count)
- 💳 Crypto payments via OxaPay ($10 = 100 SC)
- 📦 ZIP delivery for 5–30 variants

---

## Deploy on Railway

### 1. Create a Railway project

Go to [railway.app](https://railway.app) → New Project → Deploy from GitHub (or upload this folder).

### 2. Set environment variables

In Railway → your service → Variables, add:

| Variable | Value |
|---|---|
| `TELEGRAM_BOT_TOKEN` | Your token from @BotFather |
| `OXAPAY_MERCHANT_KEY` | Your OxaPay merchant key |
| `OXAPAY_WEBHOOK_SECRET` | Your webhook secret |
| `WEBHOOK_BASE_URL` | `https://your-app.up.railway.app` (set after first deploy) |

> ⚠️ Do NOT set `PORT` — Railway sets it automatically.

### 3. First deploy

Railway will detect Python automatically (via `runtime.txt` and `Procfile`).  
`ffmpeg` is installed automatically via `nixpacks.toml`.

### 4. Set the OxaPay webhook URL

After your first deploy, copy your Railway public URL (e.g. `https://spufa.up.railway.app`) and:

1. Go to your [OxaPay dashboard](https://oxapay.com) → Merchant settings
2. Set **Callback URL** to: `https://your-app.up.railway.app/webhook/oxapay`
3. Set **Webhook Secret** to the same value as `OXAPAY_WEBHOOK_SECRET`
4. Add `WEBHOOK_BASE_URL=https://your-app.up.railway.app` to Railway variables
5. Redeploy (Railway auto-redeploys on variable changes)

---

## Run locally

```bash
cp .env.example .env
# Fill in .env values

pip install -r requirements.txt
python main.py
```

> You'll also need `ffmpeg` installed: `brew install ffmpeg` (Mac) or `apt install ffmpeg` (Linux).

---

## File structure

```
spufa-bot/
├── main.py          # Bot entry point & all handlers
├── processor.py     # Image/video processing logic
├── database.py      # SQLite operations (spufa.db)
├── payments.py      # OxaPay API + Flask webhook server
├── config.py        # Environment variable loader
├── requirements.txt
├── Procfile         # Railway/Heroku process definition
├── runtime.txt      # Python version pin
├── railway.toml     # Railway configuration
├── nixpacks.toml    # ffmpeg + Python install instructions
└── .env.example     # Template for environment variables
```

## Database

SQLite file `spufa.db` is created automatically on first run.  
On Railway, this lives in the container's filesystem (ephemeral). For persistence across deploys, mount a Railway volume at `/app/spufa.db` or switch to PostgreSQL.
