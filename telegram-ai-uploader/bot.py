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
from aiogram.types import Message, CallbackQuery, BufferedInputFile, InlineKeyboardMarkup, InlineKeyboardButton

from config import (
    BOT_TOKEN, ADMIN_TELEGRAM_IDS, is_admin,
    HAS_AI, WEBSITE_URL, GITHUB_TOKEN,
)
from ai_parser import parse_with_ai, REQUIRED_KEYS
from parser import parse_car_text
from media_storage import (
    now_id, now_iso, photo_filename, video_filename,
    save_bytes, read_bytes, remove_temp, temp_path,
    video_too_large, human_size, extract_video_poster, extract_video_poster_smart,
)
from github_client import publish_car, upload_binary_file
from instagram_fetcher import fetch_instagram
from keyboards import main_kb, edit_kb, confirm_kb, EDIT_FIELDS
from marketing import generate_pitch, generate_pitch_ai, generate_whatsapp_share
from image_generator import generate_ad_image_with_template, pick_different_template, TEMPLATES
from config import IMAGES_FOLDER
from batch_session import BatchSession, BatchMedia, BatchText, parse_batch_text_message

import re as _re
INSTAGRAM_URL_RE = _re.compile(r"https?://(?:www\.)?(?:instagram\.com|instagr\.am)/[^\s]+", _re.IGNORECASE)

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
        # generated marketing pitch (user can regenerate)
        self.pitch: str = ""
        # generated ad poster bytes (user can regenerate)
        self.poster: bytes | None = None
        self.poster_filename: str = ""
        # last template used (so 🎨 Regenerate picks a different one)
        self.poster_template: str | None = None

    def cleanup_files(self):
        for p in self.photos:
            remove_temp(p["name"])
        for v in self.videos:
            remove_temp(v["name"])
        self.photos.clear()
        self.videos.clear()


drafts: dict[int, Draft] = defaultdict(Draft)
batch_sessions: dict[int, BatchSession] = {}


def get_draft(uid: int) -> Draft:
    if uid not in drafts:
        drafts[uid] = Draft()
    return drafts[uid]


def reset_draft(uid: int):
    if uid in drafts:
        drafts[uid].cleanup_files()
    drafts[uid] = Draft()


def get_batch(uid: int) -> BatchSession | None:
    return batch_sessions.get(uid)


def is_in_batch(uid: int) -> bool:
    return uid in batch_sessions


def reset_batch(uid: int):
    if uid in batch_sessions:
        batch_sessions[uid].cleanup_files()
        for d in batch_sessions[uid].drafts:
            try:
                d.cleanup_files()
            except Exception:
                pass
        del batch_sessions[uid]


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
    # Marketing pitch preview — shows how the description will look on the site
    if d.get("title") and d.get("price"):
        try:
            pitch = generate_pitch(d)
            lines.append("")
            lines.append("━━━━━━━━━━━━━━━━━━")
            lines.append("📣 <b>Рекламный текст (для сайта + WhatsApp):</b>")
            lines.append("")
            lines.append(pitch)
        except Exception:
            pass
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
        "<b>Tilek Auto AI Uploader 🚗</b>\n\n"
        "<b>Порядок добавления машины:</b>\n"
        "1️⃣ Сначала пришли <b>фото машины</b> (можно несколько)\n"
        "2️⃣ Потом <b>видео</b> машины\n"
        "3️⃣ Потом <b>описание</b> (марка, модель, год, цена, WhatsApp)\n"
        "4️⃣ Нажми ✅ Publish — машина появится на сайте\n\n"
        "💎 <b>Совет про качество фото:</b>\n"
        "В Telegram отправляй фото как <b>Файл</b> (📎 → Файл) — не как «Фото». "
        "Так оно сохранится в оригинальном качестве без сжатия Telegram.\n\n"
        f"{ai_note}\n{gh_note}\n\n"
        "Команды: /new · /preview · /batch — несколько машин одним пакетом · /help",
    )


@dp.message(Command("help"))
async def cmd_help(message: Message):
    if not is_admin(message.from_user.id):
        return
    await message.answer(
        "<b>Как добавить машину:</b>\n\n"
        "1️⃣ Пришли <b>фото</b> машины (1 или больше)\n"
        "   💎 Для лучшего качества → 📎 → <b>Файл</b> (не «Фото»)\n"
        "2️⃣ Пришли <b>видео</b> машины (до 20 МБ)\n"
        "3️⃣ Пришли <b>описание</b>, например:\n"
        "<code>Toyota Camry 2022\n2.5 Hybrid\nЦена 22000$\nWhatsApp 0551234567</code>\n"
        "4️⃣ Нажми ✅ <b>Publish</b>\n\n"
        "Команды: /new /preview /cancel"
    )


@dp.message(Command("upload"))
async def cmd_upload(message: Message):
    if not is_admin(message.from_user.id):
        return
    reset_draft(message.from_user.id)
    await message.answer(
        "📤 <b>Готов принять пост из WhatsApp.</b>\n\n"
        "Перешли мне сообщение из WhatsApp с машиной (текст + фото + видео в любом порядке).\n"
        "Я сам распознаю данные, сделаю продающий текст и рекламный постер.\n\n"
        "💎 Для лучшего качества фото отправляй как <b>📎 → Файл</b> (не «Фото»)"
    )


async def _build_and_send_poster(target: Message | CallbackQuery, draft: Draft, regenerate: bool = False):
    """Generate ad poster from car data + main photo and send to user."""
    if not draft.data.get("title") and not draft.data.get("brand"):
        return
    main_photo_path = None
    if draft.photos:
        main_photo_path = temp_path(draft.photos[0]["name"])
    msg = target.message if isinstance(target, CallbackQuery) else target
    # Pick template: regenerate → different one, first time → smart auto
    if regenerate:
        template_name = pick_different_template(draft.data, draft.poster_template)
        await msg.answer(f"🎨 Новый постер · шаблон: <code>{template_name}</code>")
    else:
        template_name = None  # let generator auto-select
    try:
        poster_bytes, used = await generate_ad_image_with_template(draft.data, main_photo_path, template_name=template_name)
        if not poster_bytes:
            await msg.answer("⚠️ Не получилось сгенерировать постер.")
            return
        draft.poster = poster_bytes
        draft.poster_filename = f"{draft.car_id}_poster.jpg"
        draft.poster_template = used or template_name
        save_bytes(draft.poster_filename, poster_bytes)
        caption_tpl = f" · шаблон: <code>{draft.poster_template}</code>" if draft.poster_template else ""
        await msg.answer_photo(
            BufferedInputFile(poster_bytes, filename=draft.poster_filename),
            caption=f"🎨 <b>Рекламный постер</b>{caption_tpl}\nДобавится в карточку машины на сайте.",
        )
    except Exception as e:
        log.exception("Poster generation failed")
        await msg.answer(f"⚠️ Ошибка постера: <code>{escape(str(e))}</code>")


@dp.message(Command("new"))
async def cmd_new(message: Message):
    if not is_admin(message.from_user.id):
        return
    reset_draft(message.from_user.id)
    await message.answer(
        "🆕 <b>Новый черновик.</b>\n\n"
        "1️⃣ Пришли <b>фото</b> машины (📎 → Файл = лучшее качество)\n"
        "2️⃣ Потом <b>видео</b>\n"
        "3️⃣ Потом <b>описание</b> (марка, год, цена, WhatsApp)"
    )


# ============ BATCH UPLOAD MODE ============

def batch_collecting_media_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Media Done", callback_data="batch_media_done")],
        [InlineKeyboardButton(text="🧹 Clear Batch", callback_data="batch_clear"),
         InlineKeyboardButton(text="❌ Cancel", callback_data="batch_cancel")],
    ])


def batch_collecting_text_kb(can_create: bool = False) -> InlineKeyboardMarkup:
    rows = []
    if can_create:
        rows.append([InlineKeyboardButton(text="✅ Create Batch Drafts", callback_data="batch_create")])
    rows.append([
        InlineKeyboardButton(text="🧹 Clear Batch", callback_data="batch_clear"),
        InlineKeyboardButton(text="❌ Cancel", callback_data="batch_cancel"),
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def batch_review_kb(n: int) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text="✅ Publish ALL", callback_data="batch_publish_all"),
         InlineKeyboardButton(text="👁 Preview ALL", callback_data="batch_preview_all")],
        [InlineKeyboardButton(text="🎨 Regen ALL posters", callback_data="batch_regen_posters")],
    ]
    # Per-draft Edit buttons (up to 5 to avoid keyboard overflow)
    edit_row = []
    for i in range(1, min(n, 5) + 1):
        edit_row.append(InlineKeyboardButton(text=f"✏️ #{i}", callback_data=f"batch_edit:{i}"))
        if len(edit_row) == 5:
            rows.append(edit_row)
            edit_row = []
    if edit_row:
        rows.append(edit_row)
    rows.append([
        InlineKeyboardButton(text="🧹 Clear", callback_data="batch_clear"),
        InlineKeyboardButton(text="❌ Cancel", callback_data="batch_cancel"),
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


@dp.message(Command("batch"))
async def cmd_batch(message: Message):
    if not is_admin(message.from_user.id):
        return
    uid = message.from_user.id
    reset_batch(uid)
    batch_sessions[uid] = BatchSession(user_id=uid, status="collecting_media")
    await message.answer(
        "📦 <b>Batch Upload</b>\n\n"
        "Отправь <b>все видео/фото машин по очереди</b>. "
        "Я их сохраню в порядке отправки.\n\n"
        "Когда закончишь — нажми <b>✅ Media Done</b>, потом пришли тексты "
        "(можно в одном сообщении с разделителями <code>1)</code> <code>2)</code> <code>3)</code> "
        "или по одному).",
        reply_markup=batch_collecting_media_kb(),
    )


async def _batch_handle_photo(message: Message, batch: BatchSession):
    photo = message.photo[-1]
    file = await bot.get_file(photo.file_id)
    buf = await bot.download_file(file.file_path)
    data = buf.read()
    idx = len(batch.media) + 1
    fname = f"batch_{batch.user_id}_{idx}_photo.jpg"
    save_bytes(fname, data)
    batch.media.append(BatchMedia(index=idx, file_name=fname, file_size=len(data), media_type="photo"))
    await message.answer(
        f"📸 Фото #{idx} добавлено ({human_size(len(data))}). Всего: {idx}",
        reply_markup=batch_collecting_media_kb(),
    )


async def _batch_handle_document_image(message: Message, batch: BatchSession):
    file_obj = message.document
    size = file_obj.file_size or 0
    if size > 25 * 1024 * 1024:
        await message.answer(f"⚠️ Фото {human_size(size)} больше 25 МБ — пропускаю.")
        return
    try:
        file = await bot.get_file(file_obj.file_id)
    except Exception as e:
        await message.answer(f"⚠️ Не смог скачать: {e}")
        return
    buf = await bot.download_file(file.file_path)
    data = buf.read()
    idx = len(batch.media) + 1
    fname = f"batch_{batch.user_id}_{idx}_photo.jpg"
    save_bytes(fname, data)
    batch.media.append(BatchMedia(index=idx, file_name=fname, file_size=len(data), media_type="photo"))
    await message.answer(
        f"📸✨ Фото #{idx} HD добавлено ({human_size(len(data))}). Всего: {idx}",
        reply_markup=batch_collecting_media_kb(),
    )


async def _batch_handle_video(message: Message, batch: BatchSession):
    file_obj = message.video or message.document
    size = file_obj.file_size or 0
    if video_too_large(size):
        await message.answer(f"⚠️ Видео {human_size(size)} слишком большое — пропускаю.")
        return
    try:
        file = await bot.get_file(file_obj.file_id)
    except Exception as e:
        await message.answer(f"⚠️ Не смог скачать: {e}")
        return
    buf = await bot.download_file(file.file_path)
    data = buf.read()
    idx = len(batch.media) + 1
    ext = "mp4"
    if file.file_path and "." in file.file_path:
        ext = file.file_path.rsplit(".", 1)[-1].lower()[:5] or "mp4"
    fname = f"batch_{batch.user_id}_{idx}_video.{ext}"
    save_bytes(fname, data)

    # Pre-extract poster frame from video for later use.
    # Uses GPT-4o-mini Vision to pick the frame showing the FRONT/EXTERIOR of the car,
    # falling back to heuristic scoring (brightness/sharpness) on any error.
    thumb_name = None
    try:
        poster_bytes = await extract_video_poster_smart(temp_path(fname))
        if poster_bytes:
            thumb_name = f"batch_{batch.user_id}_{idx}_thumb.jpg"
            save_bytes(thumb_name, poster_bytes)
    except Exception as e:
        log.warning(f"Batch poster extract failed: {e}")

    batch.media.append(BatchMedia(
        index=idx,
        file_name=fname,
        file_size=len(data),
        media_type="video",
        caption=message.caption or "",
        video_thumb_name=thumb_name,
    ))
    extra = " · HD кадр сохранён" if thumb_name else ""
    await message.answer(
        f"🎥 Видео #{idx} добавлено ({human_size(len(data))}){extra}. Всего: {idx}",
        reply_markup=batch_collecting_media_kb(),
    )


async def _batch_add_text_items(message: Message, batch: BatchSession, raw_text: str):
    parts = parse_batch_text_message(raw_text)
    for p in parts:
        idx = len(batch.texts) + 1
        batch.texts.append(BatchText(index=idx, raw_text=p))

    media_n = batch.media_count()
    text_n = batch.text_count()
    note = ""
    if text_n > media_n:
        note = f"\n⚠️ Текстов ({text_n}) больше чем видео ({media_n}). Удали лишние через 🧹 Clear или жми Cancel."
    elif text_n < media_n:
        note = f"\n👉 Пришли ещё {media_n - text_n} текст(а), чтобы было по одному на каждое видео."

    await message.answer(
        f"📝 Принято <b>{len(parts)}</b> текст(а). Видео: {media_n} · Текстов: {text_n}{note}",
        reply_markup=batch_collecting_text_kb(can_create=batch.is_balanced()),
    )


@dp.callback_query(F.data == "batch_media_done")
async def cb_batch_media_done(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        return
    batch = get_batch(cb.from_user.id)
    if not batch:
        await cb.answer("Batch не запущен. Нажми /batch", show_alert=True)
        return
    if not batch.media:
        await cb.answer("Сначала пришли хотя бы одно видео/фото", show_alert=True)
        return
    batch.status = "collecting_texts"
    await cb.message.answer(
        f"📦 Принял <b>{batch.media_count()}</b> медиа.\n\n"
        "Теперь пришли <b>тексты машин</b>.\n"
        "Варианты:\n"
        "• <b>Одним сообщением</b> с разделителями <code>1)</code> <code>2)</code> <code>3)</code>\n"
        "• <b>По одному сообщению</b> на машину — я свяжу их в порядке отправки",
        reply_markup=batch_collecting_text_kb(),
    )
    await cb.answer()


@dp.callback_query(F.data == "batch_clear")
async def cb_batch_clear(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        return
    reset_batch(cb.from_user.id)
    await cb.message.answer("🧹 Batch очищен. Нажми /batch чтобы начать заново.")
    await cb.answer()


@dp.callback_query(F.data == "batch_cancel")
async def cb_batch_cancel(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        return
    reset_batch(cb.from_user.id)
    await cb.message.answer("❌ Batch отменён. Обычный режим восстановлен.")
    await cb.answer()


@dp.callback_query(F.data == "batch_create")
async def cb_batch_create(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        return
    batch = get_batch(cb.from_user.id)
    if not batch or not batch.is_balanced():
        await cb.answer("Кол-во видео и текстов не совпадает", show_alert=True)
        return
    await cb.message.answer("⏳ Создаю карточки и постеры... (это займёт ~30 сек на машину)")
    await _create_batch_drafts(cb.message, batch)
    await cb.answer()


async def _create_batch_drafts(message: Message, batch: BatchSession):
    """Pair media[i] with texts[i], run AI parse + pitch + poster for each."""
    batch.drafts = []
    for i, (media, text) in enumerate(zip(batch.media, batch.texts), start=1):
        d = Draft()
        d.car_id = f"car_{batch.user_id}_{int(__import__('time').time())}_{i}"
        # Copy media into draft's photo/video pools
        if media.media_type == "video":
            d.videos.append({"name": media.file_name, "size": media.file_size})
            if media.video_thumb_name:
                d.photos.append({"name": media.video_thumb_name, "size": 0})
        else:
            d.photos.append({"name": media.file_name, "size": media.file_size})

        # Parse car text via AI
        parsed = await parse_with_ai(text.raw_text)
        text.parsed_data = parsed
        merge_parsed(d, parsed)
        d.raw_texts.append(text.raw_text)

        # AI pitch
        try:
            d.pitch = await generate_pitch_ai(d.data)
            d.data["description"] = d.pitch
        except Exception as e:
            log.warning(f"Batch pitch #{i} failed: {e}")

        # Poster
        try:
            main_photo = temp_path(d.photos[0]["name"]) if d.photos else None
            pb, used_template = await generate_ad_image_with_template(d.data, main_photo)
            if pb:
                d.poster = pb
                d.poster_filename = f"{d.car_id}_poster.jpg"
                d.poster_template = used_template
                save_bytes(d.poster_filename, pb)
        except Exception as e:
            log.warning(f"Batch poster #{i} failed: {e}")

        batch.drafts.append(d)
        await message.answer(f"✅ Карточка #{i}: {d.data.get('title') or '(без названия)'}")

    batch.status = "review"
    await _show_batch_review(message, batch)


async def _show_batch_review(target: Message | CallbackQuery, batch: BatchSession):
    msg = target.message if isinstance(target, CallbackQuery) else target
    lines = [f"📦 <b>Batch готов</b> — {len(batch.drafts)} машин:"]
    for i, d in enumerate(batch.drafts, 1):
        title = d.data.get("title") or "(без названия)"
        price = d.data.get("price") or "—"
        has_video = "🎥" if d.videos else "·"
        has_pitch = "📣" if d.pitch else "·"
        has_poster = "🎨" if d.poster else "·"
        lines.append(f"<b>{i}.</b> {escape(title)} · {escape(price)} {has_video}{has_pitch}{has_poster}")
    await msg.answer("\n".join(lines), reply_markup=batch_review_kb(len(batch.drafts)))


@dp.callback_query(F.data == "batch_preview_all")
async def cb_batch_preview_all(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        return
    batch = get_batch(cb.from_user.id)
    if not batch or not batch.drafts:
        await cb.answer("Batch пуст", show_alert=True)
        return
    for i, d in enumerate(batch.drafts, 1):
        if d.poster:
            await cb.message.answer_photo(
                BufferedInputFile(d.poster, filename=d.poster_filename or f"poster_{i}.jpg"),
                caption=f"<b>#{i}. {escape(d.data.get('title') or '')}</b>\n{escape(d.data.get('price') or '')}",
            )
        else:
            await cb.message.answer(f"<b>#{i}. {escape(d.data.get('title') or '')}</b> (без постера)")
    await cb.answer()


@dp.callback_query(F.data == "batch_regen_posters")
async def cb_batch_regen_posters(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        return
    batch = get_batch(cb.from_user.id)
    if not batch or not batch.drafts:
        await cb.answer("Batch пуст", show_alert=True)
        return
    await cb.message.answer("🎨 Перегенерирую постеры...")
    for i, d in enumerate(batch.drafts, 1):
        try:
            main_photo = temp_path(d.photos[0]["name"]) if d.photos else None
            new_tpl = pick_different_template(d.data, d.poster_template)
            pb, used = await generate_ad_image_with_template(d.data, main_photo, template_name=new_tpl)
            if pb:
                d.poster = pb
                d.poster_filename = f"{d.car_id}_poster.jpg"
                d.poster_template = used
                save_bytes(d.poster_filename, pb)
        except Exception as e:
            log.warning(f"Batch regen #{i} failed: {e}")
    await cb.message.answer("✅ Постеры обновлены")
    await _show_batch_review(cb, batch)
    await cb.answer()


@dp.callback_query(F.data == "batch_publish_all")
async def cb_batch_publish_all(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        return
    batch = get_batch(cb.from_user.id)
    if not batch or not batch.drafts:
        await cb.answer("Batch пуст", show_alert=True)
        return
    if not GITHUB_TOKEN:
        await cb.message.answer("⚠️ GITHUB_TOKEN не настроен — публикация недоступна.")
        await cb.answer()
        return
    await cb.message.answer(f"⏳ Публикую {len(batch.drafts)} машин на сайт...")
    ok = 0
    for i, d in enumerate(batch.drafts, 1):
        try:
            car = build_car_object(d)
            if d.pitch:
                car["description"] = d.pitch
            photo_files = [(p["name"], read_bytes(p["name"])) for p in d.photos]
            if d.poster and d.poster_filename:
                photo_files.append((d.poster_filename, d.poster))
            video_files = [(v["name"], read_bytes(v["name"])) for v in d.videos]
            await publish_car(car, photo_files, video_files)
            ok += 1
            await cb.message.answer(f"✅ #{i} опубликовано: <b>{escape(d.data.get('title') or '')}</b>")
        except Exception as e:
            log.exception(f"Batch publish #{i} failed")
            await cb.message.answer(f"❌ #{i}: <code>{escape(str(e))}</code>")
    await cb.message.answer(f"🎉 Готово: {ok}/{len(batch.drafts)} опубликованы\n🌐 {WEBSITE_URL}")
    reset_batch(cb.from_user.id)
    await cb.answer()


@dp.callback_query(F.data.startswith("batch_edit:"))
async def cb_batch_edit(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        return
    try:
        idx = int(cb.data.split(":", 1)[1])
    except ValueError:
        await cb.answer("Bad index", show_alert=True)
        return
    batch = get_batch(cb.from_user.id)
    if not batch or idx < 1 or idx > len(batch.drafts):
        await cb.answer("Нет такой машины", show_alert=True)
        return
    d = batch.drafts[idx - 1]
    # Move the draft into the normal single-draft slot so existing Edit flow works
    drafts[cb.from_user.id] = d
    await cb.message.answer(
        f"✏️ Редактирую #{idx}: <b>{escape(d.data.get('title') or '')}</b>\n"
        "Открыл карточку в обычном режиме. Используй ✏️ Edit / 🔁 Regenerate / 🎨 Regenerate.\n"
        "<i>Когда закончишь — нажми ✅ Publish (опубликует ТОЛЬКО эту машину) или возвращайся к /batch.</i>"
    )
    await show_preview(cb, d)
    await cb.answer()


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
    # Batch mode takes priority
    batch = get_batch(message.from_user.id)
    if batch and batch.status == "collecting_media":
        await _batch_handle_photo(message, batch)
        return
    if batch and batch.status == "collecting_texts":
        await message.answer("⚠️ Сейчас режим текстов. Жми 🧹 Clear Batch если хочешь начать заново.")
        return
    draft = get_draft(message.from_user.id)
    # Telegram compresses photos sent as "photo" to max ~1280px JPEG
    photo = message.photo[-1]
    file = await bot.get_file(photo.file_id)
    buf = await bot.download_file(file.file_path)
    data = buf.read()
    idx = len(draft.photos) + 1
    fname = photo_filename(draft.car_id, idx, "jpg")
    save_bytes(fname, data)
    draft.photos.append({"name": fname, "size": len(data)})

    if message.caption:
        draft.raw_texts.append(message.caption)
        parsed = await parse_with_ai(message.caption)
        merge_parsed(draft, parsed)

    hint = ""
    if len(draft.photos) == 1 and not draft.videos:
        hint = "\n👉 Теперь пришли <b>видео</b> машины (или ещё фото)"
        hint += "\n💎 Совет: для оригинального качества отправляй фото как 📎 → <b>Файл</b>"
    elif draft.videos and not draft.data.get("title"):
        hint = "\n👉 Теперь пришли <b>описание</b> (марка, год, цена, WhatsApp)"
    await message.answer(f"📸 Фото {idx} ({human_size(len(data))}), всего: {len(draft.photos)}{hint}")


@dp.message(F.video | F.document)
async def on_video(message: Message):
    if not is_admin(message.from_user.id):
        return
    file_obj = message.video or message.document
    if not file_obj:
        return
    mime = (getattr(file_obj, "mime_type", "") or "").lower()

    # Batch mode takes priority
    batch = get_batch(message.from_user.id)
    if batch and batch.status == "collecting_media":
        if message.document and mime.startswith("image/"):
            await _batch_handle_document_image(message, batch)
        elif message.video or mime.startswith("video/"):
            await _batch_handle_video(message, batch)
        else:
            await message.answer("⚠️ В batch принимаю только видео и фото.")
        return
    if batch and batch.status == "collecting_texts":
        await message.answer("⚠️ Сейчас режим текстов. Жми 🧹 Clear Batch для перезапуска.")
        return

    draft = get_draft(message.from_user.id)

    # If document is an IMAGE — save as uncompressed photo (original quality)
    if message.document and mime.startswith("image/"):
        size = file_obj.file_size or 0
        if size > 25 * 1024 * 1024:
            await message.answer(f"⚠️ Фото {human_size(size)} — больше 25 МБ. Сожми и пришли заново.")
            return
        try:
            file = await bot.get_file(file_obj.file_id)
        except Exception as e:
            await message.answer(f"⚠️ Не смог скачать фото: {e}")
            return
        buf = await bot.download_file(file.file_path)
        data = buf.read()
        ext = "jpg"
        if mime == "image/png":
            ext = "png"
        elif mime == "image/webp":
            ext = "webp"
        elif "/" in mime:
            sub = mime.split("/", 1)[1]
            if sub in ("jpeg", "jpg", "png", "webp"):
                ext = "jpg" if sub == "jpeg" else sub
        idx = len(draft.photos) + 1
        fname = photo_filename(draft.car_id, idx, ext)
        save_bytes(fname, data)
        draft.photos.append({"name": fname, "size": len(data)})
        if message.caption:
            draft.raw_texts.append(message.caption)
            parsed = await parse_with_ai(message.caption)
            merge_parsed(draft, parsed)
        hint = ""
        if len(draft.photos) == 1 and not draft.videos:
            hint = "\n👉 Теперь пришли <b>видео</b> машины"
        elif draft.videos and not draft.data.get("title"):
            hint = "\n👉 Теперь пришли <b>описание</b>"
        await message.answer(
            f"📸✨ Фото в HD-качестве сохранено ({human_size(len(data))}, без сжатия). Всего: {len(draft.photos)}{hint}"
        )
        return

    # If document is not a video and not an image → reject
    if message.document and not mime.startswith("video/"):
        await message.answer("⚠️ Это не видео и не изображение. Пришли фото, видео или PDF/MP4.")
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

    # Auto-save high-quality video frame as a photo if user hasn't sent any photos yet.
    # 1) Try ffmpeg to grab a HD frame from 30% of the video (best quality, brightness-corrected)
    # 2) Fallback to Telegram's auto thumbnail (low-res but always works)
    thumb_added = False
    poster_source = ""
    if not draft.photos:
        # Try ffmpeg first
        try:
            poster_bytes = await extract_video_poster_smart(temp_path(fname))
            if poster_bytes:
                tidx = len(draft.photos) + 1
                tfname = photo_filename(draft.car_id, tidx, "jpg")
                save_bytes(tfname, poster_bytes)
                draft.photos.append({"name": tfname, "size": len(poster_bytes)})
                thumb_added = True
                poster_source = "HD кадр из видео"
        except Exception as e:
            log.warning(f"ffmpeg poster extraction failed: {e}")
        # Fallback to Telegram thumbnail
        if not thumb_added:
            thumb = getattr(file_obj, "thumbnail", None) or getattr(file_obj, "thumb", None)
            if thumb:
                try:
                    tfile = await bot.get_file(thumb.file_id)
                    tbuf = await bot.download_file(tfile.file_path)
                    tdata = tbuf.read()
                    tidx = len(draft.photos) + 1
                    tfname = photo_filename(draft.car_id, tidx, "jpg")
                    save_bytes(tfname, tdata)
                    draft.photos.append({"name": tfname, "size": len(tdata)})
                    thumb_added = True
                    poster_source = "превью из Telegram"
                except Exception as e:
                    log.warning(f"Could not save video thumbnail: {e}")

    if message.caption:
        draft.raw_texts.append(message.caption)
        parsed = await parse_with_ai(message.caption)
        merge_parsed(draft, parsed)

    extra = f"\n🖼 {poster_source} сохранён как фото-обложка" if thumb_added else ""
    next_hint = ""
    if not draft.data.get("title"):
        next_hint = (
            "\n\n👉 Теперь пришли <b>описание</b> машины, например:\n"
            "<code>Toyota Camry 2022\n2.5 Hybrid\nЦена 22000$\nWhatsApp 0551234567</code>"
        )
    await message.answer(
        f"🎥 Видео сохранено ({human_size(len(data))}). Всего видео: {len(draft.videos)}{extra}{next_hint}"
    )


# ---------- Text handler ----------

@dp.message(F.text)
async def on_text(message: Message):
    if not is_admin(message.from_user.id):
        await message.answer("Access denied.")
        return
    text = message.text.strip()

    # Batch mode takes priority
    batch = get_batch(message.from_user.id)
    if batch and batch.status == "collecting_media":
        await message.answer(
            "👀 Сейчас режим сбора <b>медиа</b>. Сначала пришли все видео/фото, "
            "потом нажми <b>✅ Media Done</b>.",
            reply_markup=batch_collecting_media_kb(),
        )
        return
    if batch and batch.status == "collecting_texts":
        await _batch_add_text_items(message, batch, text)
        return

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

    # Auto-fetch Instagram if URL is present
    insta_match = INSTAGRAM_URL_RE.search(text)
    if insta_match:
        insta_url = insta_match.group(0).rstrip(").,;")
        draft.data["instagramUrl"] = insta_url
        await message.answer("📥 Тяну фото/видео/подпись из Instagram... (может занять до 30 сек)")
        fetched = await fetch_instagram(insta_url)
        if fetched.get("ok"):
            count_photos = 0
            for raw in fetched.get("photos", []):
                idx = len(draft.photos) + 1
                fname = photo_filename(draft.car_id, idx, "jpg")
                save_bytes(fname, raw)
                draft.photos.append({"name": fname, "size": len(raw)})
                count_photos += 1
            video_added = False
            if fetched.get("video"):
                vdata = fetched["video"]
                if not video_too_large(len(vdata)):
                    fname = video_filename(draft.car_id, "mp4")
                    save_bytes(fname, vdata)
                    draft.videos.append({"name": fname, "size": len(vdata)})
                    video_added = True
            # If no photos but we got a video thumbnail, save it as a photo
            if count_photos == 0 and fetched.get("video_thumb"):
                idx = len(draft.photos) + 1
                fname = photo_filename(draft.car_id, idx, "jpg")
                save_bytes(fname, fetched["video_thumb"])
                draft.photos.append({"name": fname, "size": len(fetched["video_thumb"])})
                count_photos += 1
            caption = fetched.get("caption", "")
            if caption:
                draft.raw_texts.append(caption)
                parsed = await parse_with_ai(caption)
                merge_parsed(draft, parsed)
            await message.answer(
                f"✅ Instagram OK: фото {count_photos}, видео {1 if video_added else 0}, "
                f"подпись {'есть' if caption else 'нет'}"
            )
        else:
            err = fetched.get("error") or "unknown"
            await message.answer(
                f"⚠️ Не смог скачать из Instagram: {err}\n"
                "Пришли фото/видео/подпись вручную."
            )
        # Also parse the remaining text (around the URL)
        rest = INSTAGRAM_URL_RE.sub(" ", text).strip()
        if rest:
            draft.raw_texts.append(rest)
            parsed = await parse_with_ai(rest)
            merge_parsed(draft, parsed)
        await show_preview(message, draft)
        return

    # Default: parse as car text
    draft.raw_texts.append(text)
    parsed = await parse_with_ai(text)
    merge_parsed(draft, parsed)
    # Auto-generate AI pitch once we have brand+model+price (only once per draft)
    if (draft.data.get("title") or draft.data.get("brand")) and not draft.pitch:
        try:
            draft.pitch = await generate_pitch_ai(draft.data)
            draft.data["description"] = draft.pitch
        except Exception as e:
            log.warning(f"Auto AI pitch failed: {e}")
    await show_preview(message, draft)
    # Auto-generate poster once we have enough data
    if (draft.data.get("title") or draft.data.get("brand")) and draft.photos and not draft.poster:
        await _build_and_send_poster(message, draft)


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


@dp.callback_query(F.data == "regen_text")
async def cb_regen_text(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        return
    draft = get_draft(cb.from_user.id)
    if not draft.data.get("title") and not draft.data.get("brand"):
        await cb.answer("Сначала пришли описание машины.", show_alert=True)
        return
    await cb.message.answer("⏳ Генерирую новый рекламный текст через AI...")
    try:
        draft.pitch = await generate_pitch_ai(draft.data)
        draft.data["description"] = draft.pitch
        await cb.message.answer(f"📣 <b>Новый рекламный текст:</b>\n\n{draft.pitch}")
    except Exception as e:
        await cb.message.answer(f"⚠️ Ошибка: <code>{escape(str(e))}</code>")
    await cb.answer()


@dp.callback_query(F.data == "regen_image")
async def cb_regen_image(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        return
    draft = get_draft(cb.from_user.id)
    if not draft.data.get("title") and not draft.data.get("brand"):
        await cb.answer("Сначала пришли описание машины.", show_alert=True)
        return
    await _build_and_send_poster(cb, draft, regenerate=True)
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
    await cb.message.answer("⏳ Загружаю на GitHub...")
    try:
        car = build_car_object(draft)
        # Auto-generate marketing pitch via AI (with template fallback inside generate_pitch_ai)
        # Prefer cached draft.pitch if user already saw/edited it; else generate now.
        if draft.pitch and len(draft.pitch) > 40:
            car["description"] = draft.pitch
        elif not car.get("description") or len(car.get("description", "")) < 40:
            try:
                car["description"] = await generate_pitch_ai(car)
            except Exception as e:
                log.warning(f"AI pitch generation failed: {e}")
                try:
                    car["description"] = generate_pitch(car)
                except Exception:
                    pass
        photo_files = [(p["name"], read_bytes(p["name"])) for p in draft.photos]
        # Generate poster if not yet generated (e.g. user skipped preview)
        if not draft.poster:
            try:
                main_p = temp_path(draft.photos[0]["name"]) if draft.photos else None
                pb, used = await generate_ad_image_with_template(car, main_p)
                if pb:
                    draft.poster = pb
                    draft.poster_filename = f"{draft.car_id}_poster.jpg"
                    draft.poster_template = used
                    save_bytes(draft.poster_filename, pb)
            except Exception as e:
                log.warning(f"Poster fallback failed: {e}")
        # Add poster as an extra car photo (appended so original photos stay first)
        if draft.poster and draft.poster_filename:
            photo_files.append((draft.poster_filename, draft.poster))
        video_files = [(v["name"], read_bytes(v["name"])) for v in draft.videos]
        result = await publish_car(car, photo_files, video_files)
        await cb.message.answer(
            f"✅ <b>Опубликовано:</b> {escape(result.get('title') or '')}\n"
            f"Фото: {len(result.get('images', []))} · Видео: {1 if result.get('videoFile') else 0}\n"
            f"🌐 {WEBSITE_URL}"
        )
        # Generate WhatsApp share text
        try:
            wa_text = generate_whatsapp_share(result, site_url=WEBSITE_URL)
            await cb.message.answer(
                "📋 <b>Готовый текст для WhatsApp — скопируй и вставь в группу:</b>\n\n"
                f"<code>{escape(wa_text)}</code>"
            )
        except Exception as e:
            log.warning(f"WhatsApp share generation failed: {e}")
        reset_draft(cb.from_user.id)
    except Exception as e:
        log.exception("Publish failed")
        await cb.message.answer(f"❌ Ошибка публикации: <code>{escape(str(e))}</code>")
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

    # Start the small HTTP API used by the admin panel (skip if disabled or
    # ADMIN_API_SECRET missing — endpoint refuses requests anyway in that case).
    api_runner = None
    if os.getenv("ADMIN_API_ENABLED", "true").strip().lower() in ("1", "true", "yes"):
        try:
            from admin_api import start_server
            port = int(os.getenv("PORT", "8080"))
            api_runner = await start_server(port=port)
        except Exception as e:
            log.warning("Admin API failed to start: %s", e)

    try:
        await dp.start_polling(bot)
    finally:
        if api_runner is not None:
            try:
                await api_runner.cleanup()
            except Exception:
                pass


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        log.info("Bot stopped")
