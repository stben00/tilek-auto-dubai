# Tilek Auto AI Uploader

Telegram bot that takes messy Instagram/WhatsApp/Telegram messages (link + caption + photos + video) and publishes a clean car card to `cars.json` in this GitHub repo.

No Instagram scraping. You send the link, caption, photos and video manually — the bot parses, previews, and on confirmation pushes to GitHub.

---

## 1. Create Telegram bot

1. Open Telegram → search `@BotFather`
2. Send `/newbot` → give it a name and a username ending in `bot`
3. Copy the **token** — that's `BOT_TOKEN`

## 2. Get your Telegram ID

1. Open Telegram → search `@userinfobot` (or `@getmyid_bot`)
2. Send any message → it replies with your numeric `id`
3. That number is `ADMIN_TELEGRAM_IDS` (comma-separated if multiple)

## 3. Create GitHub Personal Access Token

1. Go to https://github.com/settings/tokens
2. **Generate new token** → **Fine-grained** (recommended)
   - Repository access: **Only select repositories** → `stben00/tilek-auto-dubai`
   - Repository permissions:
     - **Contents: Read and write**
     - **Metadata: Read-only** (auto)
3. Click **Generate** → copy the token → that's `GITHUB_TOKEN`

(Classic token also works — needs scope `repo`.)

## 4. (Optional) AI API key

Without an AI key the bot uses a built-in regex parser — it works, but AI parsing is more forgiving with messy text.

- Anthropic: https://console.anthropic.com → API Keys → `ANTHROPIC_API_KEY`
- OpenAI: https://platform.openai.com/api-keys → `OPENAI_API_KEY`

If both are set, Anthropic is used.

## 5. Configure `.env`

```bash
cd telegram-ai-uploader
cp .env.example .env
```

Open `.env` and fill at minimum:

```
BOT_TOKEN=123456:ABC...
ADMIN_TELEGRAM_IDS=123456789
GITHUB_TOKEN=ghp_...
ANTHROPIC_API_KEY=          # optional
```

## 6. Install + run

```bash
cd telegram-ai-uploader
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python bot.py
```

You should see `Bot starting. Admins: {...}`.

## 7. Use the bot

In Telegram, open your bot and send `/start`. Then send (in any order):

1. Instagram or Reel link (e.g. `https://www.instagram.com/reel/DYxyz/`)
2. Caption pasted from Instagram/WhatsApp
3. Photos (single or album — multiple at once)
4. A video file (≤ 20 MB) or a video link (YouTube/TikTok/direct .mp4)

The bot answers with a **preview card** and buttons:

- ✅ Publish — uploads photos to `assets/cars/`, video to `assets/videos/`, prepends the car to `cars.json`, commits to GitHub
- ✏️ Edit — pick any field and rewrite it
- 🖼 / 🎥 / 🔗 — add more media or links
- 🧹 Clear draft / ❌ Cancel

After publish you'll get a link to the live site.

## 8. Where data lands

- `cars.json` at repo root — JSON array, newest car first
- `assets/cars/car_YYYYMMDD_HHMMSS_N.jpg`
- `assets/videos/car_YYYYMMDD_HHMMSS_video.mp4`

GitHub Pages picks up the changes within ~1 minute.

## 9. Video size limit

GitHub's REST API limit per file is ~100 MB but anything over 20 MB is slow and clutters the repo. Default cap is **20 MB** (`MAX_VIDEO_MB` in `.env`). Larger videos: send them to YouTube/TikTok and paste the link — it'll be saved as `videoUrl`.

## 10. Deploy 24/7 later

The bot polls Telegram and works fine locally. To leave it always-on:

- **Railway**: connect this folder as a service, set the same env vars, start command `python bot.py`
- **Render**: Background Worker, same setup
- **VPS**: `tmux` + `python bot.py`, or systemd unit

No webhooks needed — long polling works everywhere.

---

## Troubleshooting

| Problem | Fix |
|---|---|
| `BOT_TOKEN is empty` | Fill `.env`, then re-run |
| `Access denied.` in Telegram | Your user ID is not in `ADMIN_TELEGRAM_IDS` |
| `GitHub PUT … 404` | Token doesn't have write access to the repo |
| `GitHub PUT … 422` | Branch name in `.env` doesn't match the actual branch |
| AI returns nothing | Network/quota — regex parser kicks in automatically |
| Video upload fails | File > 20 MB — send a link instead |

## Files

```
telegram-ai-uploader/
├── bot.py              # entrypoint, aiogram handlers
├── config.py           # .env loader
├── parser.py           # regex fallback parser
├── ai_parser.py        # AI parser (Anthropic / OpenAI) + regex merge
├── github_client.py    # GitHub REST API client
├── media_storage.py    # local temp file helpers
├── keyboards.py        # inline keyboards
├── requirements.txt
├── .env.example
└── data/temp/          # downloaded photos/videos before publish
```
