"""Tilek Auto AI Uploader — Telegram bot."""
import asyncio
import logging
import os
import sys
from collections import defaultdict
from html import escape

from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import CommandStart, Command
from aiogram.types import Message, CallbackQuery, BufferedInputFile

from config import (
    BOT_TOKEN, ADMIN_TELEGRAM_IDS, is_admin,
    HAS_AI, WEBSITE_URL, GITHUB_TOKEN,
)
from ai_parser import parse_with_ai, REQUIRED_KEYS
from parser import parse_car_text
from media_storage import (
    now_id, now_iso, photo_filename, video_filename,
    save_bytes, read_bytes, remove_temp,
    video_too_large, human_size,
)
from github_client import publish_car
from keyboards import main_kb, edit_kb, confirm_kb, EDIT_FIELDS

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s — %(message)s")
log = logging.getLogger("bot")

if not BOT_TOKEN:
    print("ERROR: BOT_TOKEN is empty. Fill .env first.")
    sys.exit(1)
if not ADMIN_TELEGRAM_IDS:
    print("ERROR: ADMIN_TELEGRAM_IDS is empty. Fill .env first.")
    sys.exit(1)


# ---------- Draft state ----------

class Draft:
    def __init__(self):
        self.car_id = now_id()
        self.data = {k: "" for k in REQUIRED_KEYS}
        self.data["status"] = "available"
        # photos: list of {"name": str, "size": int}
        self.photos: list[dict] = []
        # videos: list of {"name": str, "size": int}
        self.videos: list[dict] = []
        self.raw_texts: list[str] = []
        # editing field: when set, next text message will overwrite this field
        self.editing_field: str | None = None
        # mode: collecting media after button press
        self.awaiting: str | None = None  # "photo" | "video" | "insta" | None

    def cleanup_files(self):
        for p in self.photos:
            remove_temp(p["name"])
        for v in self.videos:
            remove_temp(v["name"])
        self.photos.clear()
        self.videos.clear()


drafts: dict[int, Draft] = defaultdict(Draft)


def get_draft(uid: int) -> Draft:
    if uid not in drafts:
        drafts[uid] = Draft()
    return drafts[uid]


def reset_draft(uid: int):
    if uid in drafts:
        drafts[uid].cleanup_files()
    drafts[uid] = Draft()


# ---------- Helpers ----------

def merge_parsed(draft: Draft, parsed: dict):
    """Merge new parsed fields into draft, but don't overwrite manual edits with empty."""
    for k in REQUIRED_KEYS:
        new_val = parsed.get(k, "")
        if new_val and not draft.data.get(k):
            draft.data[k] = new_val
    # Re-derive title if empty
    if not draft.data.get("title"):
        parts = [p for p in [draft.data.get("brand"), draft.data.get("model"), draft.data.get("year")] if p]
        if parts:
            draft.data["title"] = " ".join(parts)


def preview_text(draft: Draft) -> str:
    d = draft.data
    lines = ["🚗 <b>Detected car:</b>", ""]
    rows = [
        ("Title", d.get("title")),
        ("Brand", d.get("brand")),
        ("Model", d.get("model")),
        ("Year", d.get("year")),
        ("Engine", d.get("engine")),
        ("Fuel", d.get("fuel")),
        ("Body", d.get("bodyType")),
        ("Price", d.get("price")),
        ("Mileage", d.get("mileage")),
        ("Location", d.get("location")),
        ("WhatsApp", d.get("whatsapp")),
        ("Instagram", "saved" if d.get("instagramUrl") else "—"),
        ("Video link", d.get("videoUrl") or "—"),
    ]
    for label, value in rows:
        lines.append(f"<b>{label}:</b> {escape(value) if value else '—'}")
    lines.append(f"<b>Photos:</b> {len(draft.photos)}")
    lines.append(f"<b>Video files:</b> {len(draft.videos)}")
    lines.append(f"<b>Status:</b> {escape(d.get('status') or 'available')}")
    if d.get("description"):
        lines.append("")
        lines.append("<b>Description:</b>")
        desc = d["description"]
        if len(desc) > 350:
            desc = desc[:347] + "..."
        lines.append(escape(desc))
    if d.get("instagramUrl"):
        lines.append("")
        lines.append(f"🔗 {escape(d['instagramUrl'])}")
    return "\n".join(lines)


async def show_preview(target: Message | CallbackQuery, draft: Draft):
    text = preview_text(draft)
    if isinstance(target, CallbackQuery):
        try:
            await target.message.edit_text(text, reply_markup=main_kb())
        except Exception:
            await target.message.answer(text, reply_markup=main_kb())
    else:
        await target.answer(text, reply_markup=main_kb())


# ---------- Bot setup ----------

bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()


@dp.message(CommandStart())
async def cmd_start(message: Message):
    if not is_admin(message.from_user.id):
        await message.answer("Access denied.")
        return
    reset_draft(message.from_user.id)
    ai_note = "🤖 AI parsing: ON" if HAS_AI else "⚙️ AI parsing: OFF (regex only)"
    gh_note = "📦 GitHub: configured" if GITHUB_TOKEN else "⚠️ GitHub token missing — publish disabled"
    await message.answer(
        "<b>Tilek Auto AI Uploader 🚗</b>\n"
        "Send me Instagram link, caption, photos or video.\n"
        "I will prepare the car card automatically.\n\n"
        f"{ai_note}\n{gh_note}\n\n"
        "Commands: /new to start fresh, /preview to see current draft, /help",
    )


@dp.message(Command("help"))
async def cmd_help(message: Message):
    if not is_admin(message.from_user.id):
        return
    await message.answer(
        "<b>How to use:</b>\n"
        "1. Send Instagram/Reel link\n"
        "2. Send caption text\n"
        "3. Send photos (album OK)\n"
        "4. Send video file OR video link\n"
        "5. Tap ✅ Publish to add the car to the site\n\n"
        "Commands: /new /preview /cancel"
    )


@dp.message(Command("new"))
async def cmd_new(message: Message):
    if not is_admin(message.from_user.id):
        return
    reset_draft(message.from_user.id)
    await message.answer("🆕 New draft started. Send link/caption/photos/video.")


@dp.message(Command("cancel"))
async def cmd_cancel(message: Message):
    if not is_admin(message.from_user.id):
        return
    reset_draft(message.from_user.id)
    await message.answer("Draft cleared.")


@dp.message(Command("preview"))
async def cmd_preview(message: Message):
    if not is_admin(message.from_user.id):
        return
    draft = get_draft(message.from_user.id)
    await show_preview(message, draft)


# ---------- Photo / Video handlers ----------

@dp.message(F.photo)
async def on_photo(message: Message):
    if not is_admin(message.from_user.id):
        return
    draft = get_draft(message.from_user.id)
    # take largest photo
    photo = message.photo[-1]
    file = await bot.get_file(photo.file_id)
    buf = await bot.download_file(file.file_path)
    data = buf.read()
    idx = len(draft.photos) + 1
    fname = photo_filename(draft.car_id, idx, "jpg")
    save_bytes(fname, data)
    draft.photos.append({"name": fname, "size": len(data)})

    # If caption present, parse it too
    if message.caption:
        draft.raw_texts.append(message.caption)
        parsed = await parse_with_ai(message.caption)
        merge_parsed(draft, parsed)

    await message.answer(f"📸 Photo {idx} saved ({human_size(len(data))}). Total: {len(draft.photos)}")


@dp.message(F.video | F.document)
async def on_video(message: Message):
    if not is_admin(message.from_user.id):
        return
    draft = get_draft(message.from_user.id)
    file_obj = message.video or message.document
    if not file_obj:
        return
    # Reject if it's a document but not a video
    mime = (getattr(file_obj, "mime_type", "") or "").lower()
    if message.document and not mime.startswith("video/"):
        await message.answer("⚠️ This document is not a video. Send a video file or photo.")
        return

    size = file_obj.file_size or 0
    if video_too_large(size):
        await message.answer(
            f"⚠️ Video is {human_size(size)} — too large for GitHub upload.\n"
            "I'll keep it as reference only. Send a public video link instead "
            "(YouTube / TikTok / direct .mp4) and I'll save it as videoUrl."
        )
        return

    try:
        file = await bot.get_file(file_obj.file_id)
    except Exception as e:
        await message.answer(f"⚠️ Cannot fetch video: {e}\nTry sending a link instead.")
        return
    buf = await bot.download_file(file.file_path)
    data = buf.read()
    ext = "mp4"
    if file.file_path and "." in file.file_path:
        ext = file.file_path.rsplit(".", 1)[-1].lower()[:5] or "mp4"
    fname = video_filename(draft.car_id, ext)
    # If a video already exists, append index
    if any(v["name"] == fname for v in draft.videos):
        fname = f"{draft.car_id}_video_{len(draft.videos) + 1}.{ext}"
    save_bytes(fname, data)
    draft.videos.append({"name": fname, "size": len(data)})

    if message.caption:
        draft.raw_texts.append(message.caption)
        parsed = await parse_with_ai(message.caption)
        merge_parsed(draft, parsed)

    await message.answer(f"🎥 Video saved ({human_size(len(data))}). Total videos: {len(draft.videos)}")


# ---------- Text handler ----------

@dp.message(F.text)
async def on_text(message: Message):
    if not is_admin(message.from_user.id):
        await message.answer("Access denied.")
        return
    text = message.text.strip()
    draft = get_draft(message.from_user.id)

    # Editing a specific field
    if draft.editing_field:
        field = draft.editing_field
        draft.data[field] = text
        draft.editing_field = None
        await message.answer(f"✅ <b>{field}</b> updated.")
        await show_preview(message, draft)
        return

    # Awaiting specific input
    if draft.awaiting == "insta":
        draft.data["instagramUrl"] = text
        draft.awaiting = None
        await message.answer("🔗 Instagram link saved.")
        await show_preview(message, draft)
        return

    # Default: parse as car text
    draft.raw_texts.append(text)
    parsed = await parse_with_ai(text)
    merge_parsed(draft, parsed)
    await show_preview(message, draft)


# ---------- Callback handlers ----------

@dp.callback_query(F.data == "preview")
async def cb_preview(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        await cb.answer("Access denied.", show_alert=True)
        return
    await show_preview(cb, get_draft(cb.from_user.id))
    await cb.answer()


@dp.callback_query(F.data == "clear")
async def cb_clear(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        return
    reset_draft(cb.from_user.id)
    await cb.message.answer("🧹 Draft cleared.")
    await cb.answer()


@dp.callback_query(F.data == "cancel")
async def cb_cancel(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        return
    reset_draft(cb.from_user.id)
    await cb.message.answer("❌ Cancelled.")
    await cb.answer()


@dp.callback_query(F.data == "add_photos")
async def cb_add_photos(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        return
    draft = get_draft(cb.from_user.id)
    draft.awaiting = "photo"
    await cb.message.answer("📸 Send photos (you can send multiple as an album).")
    await cb.answer()


@dp.callback_query(F.data == "add_video")
async def cb_add_video(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        return
    draft = get_draft(cb.from_user.id)
    draft.awaiting = "video"
    await cb.message.answer("🎥 Send a video file or a video URL.")
    await cb.answer()


@dp.callback_query(F.data == "add_insta")
async def cb_add_insta(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        return
    draft = get_draft(cb.from_user.id)
    draft.awaiting = "insta"
    await cb.message.answer("🔗 Send Instagram or Reels URL.")
    await cb.answer()


@dp.callback_query(F.data == "edit")
async def cb_edit(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        return
    await cb.message.edit_text("✏️ <b>Choose a field to edit:</b>", reply_markup=edit_kb())
    await cb.answer()


@dp.callback_query(F.data == "back")
async def cb_back(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        return
    await show_preview(cb, get_draft(cb.from_user.id))
    await cb.answer()


@dp.callback_query(F.data.startswith("editf:"))
async def cb_edit_field(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        return
    field = cb.data.split(":", 1)[1]
    if field not in {k for k, _ in EDIT_FIELDS}:
        await cb.answer("Unknown field.", show_alert=True)
        return
    draft = get_draft(cb.from_user.id)
    draft.editing_field = field
    current = draft.data.get(field, "") or "—"
    await cb.message.answer(
        f"Send new value for <b>{field}</b>.\nCurrent: <code>{escape(current)}</code>"
    )
    await cb.answer()


@dp.callback_query(F.data == "publish")
async def cb_publish(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        return
    draft = get_draft(cb.from_user.id)
    if not GITHUB_TOKEN:
        await cb.message.answer("⚠️ GITHUB_TOKEN missing. Fill .env and restart.")
        await cb.answer()
        return
    if not draft.data.get("title"):
        await cb.message.answer("⚠️ Title is empty. Add a brand/model/year first.")
        await cb.answer()
        return
    summary = (
        f"Publish this car?\n\n"
        f"<b>{escape(draft.data.get('title') or '')}</b>\n"
        f"Price: {escape(draft.data.get('price') or '—')}\n"
        f"Photos: {len(draft.photos)} | Videos: {len(draft.videos)}"
    )
    await cb.message.answer(summary, reply_markup=confirm_kb())
    await cb.answer()


@dp.callback_query(F.data == "confirm_publish")
async def cb_confirm_publish(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        return
    draft = get_draft(cb.from_user.id)
    await cb.message.answer("⏳ Uploading to GitHub...")
    try:
        car = build_car_object(draft)
        photo_files = [(p["name"], read_bytes(p["name"])) for p in draft.photos]
        video_files = [(v["name"], read_bytes(v["name"])) for v in draft.videos]
        result = await publish_car(car, photo_files, video_files)
        await cb.message.answer(
            f"✅ Published: <b>{escape(result.get('title') or '')}</b>\n"
            f"Photos: {len(result.get('images', []))}\n"
            f"🌐 {WEBSITE_URL}"
        )
        reset_draft(cb.from_user.id)
    except Exception as e:
        log.exception("Publish failed")
        await cb.message.answer(f"❌ Publish failed: <code>{escape(str(e))}</code>")
    await cb.answer()


def build_car_object(draft: Draft) -> dict:
    d = draft.data
    car = {
        "id": draft.car_id,
        "title": d.get("title", ""),
        "brand": d.get("brand", ""),
        "model": d.get("model", ""),
        "year": d.get("year", ""),
        "engine": d.get("engine", ""),
        "fuel": d.get("fuel", ""),
        "bodyType": d.get("bodyType", ""),
        "price": d.get("price", ""),
        "mileage": d.get("mileage", ""),
        "location": d.get("location", "") or "Dubai / UAE",
        "description": d.get("description", ""),
        "whatsapp": d.get("whatsapp", ""),
        "instagramUrl": d.get("instagramUrl", ""),
        "videoUrl": d.get("videoUrl", ""),
        "videoFile": "",
        "images": [],
        "mainImage": "",
        "status": d.get("status", "available") or "available",
        "source": "telegram_ai_uploader",
        "createdAt": now_iso(),
    }
    return car


async def main():
    log.info("Bot starting. Admins: %s | AI: %s", ADMIN_TELEGRAM_IDS, HAS_AI)
    await dp.start_polling(bot)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        log.info("Bot stopped")
