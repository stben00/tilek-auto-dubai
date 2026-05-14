"""Ad-poster generator for car listings.

Two modes:
1. AI generation via OpenAI Images API (DALL-E 3) — used if OPENAI_API_KEY set
   AND ENABLE_AI_IMAGE=true. Architecture is wired but the actual call is a
   thin wrapper that can be enabled at any time.
2. Local PIL-based poster — always available, produces a vertical 1080x1350
   poster with the car photo on top + brand/model/year/engine/mileage/price
   text blocks + random hype banners. Style: dark background, gold accents,
   red SHOCK badge, white headlines.

Returns JPEG bytes ready to upload to GitHub / send via Telegram.
"""
import io
import os
import random
from pathlib import Path
from typing import Optional

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from config import OPENAI_API_KEY

# ---- Font discovery ----------------------------------------------------------

_FONT_CACHE: dict[str, str] = {}

CANDIDATE_FONTS = {
    "bold": [
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
        "/System/Library/Fonts/Supplemental/Arial Black.ttf",
        "/System/Library/Fonts/Helvetica.ttc",
        "/Library/Fonts/Arial Bold.ttf",
        "C:/Windows/Fonts/arialbd.ttf",
    ],
    "narrow": [
        "/System/Library/Fonts/Supplemental/Arial Narrow Bold.ttf",
        "/System/Library/Fonts/Supplemental/Impact.ttf",
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    ],
    "regular": [
        "/System/Library/Fonts/Supplemental/Arial.ttf",
        "/System/Library/Fonts/Helvetica.ttc",
        "/Library/Fonts/Arial.ttf",
    ],
}


def _find_font(kind: str) -> Optional[str]:
    if kind in _FONT_CACHE:
        return _FONT_CACHE[kind]
    for path in CANDIDATE_FONTS.get(kind, []):
        if os.path.exists(path):
            _FONT_CACHE[kind] = path
            return path
    return None


def _load_font(kind: str, size: int) -> ImageFont.FreeTypeFont:
    path = _find_font(kind) or _find_font("bold") or _find_font("regular")
    if path:
        try:
            return ImageFont.truetype(path, size)
        except Exception:
            pass
    return ImageFont.load_default()


# ---- Helpers -----------------------------------------------------------------

HOOK_BADGES = [
    ("ШОК ЦЕНА!", (220, 30, 35)),
    ("УСПЕЙ ПЕРВЫМ!", (220, 50, 35)),
    ("ЛУЧШЕЕ ПРЕДЛОЖЕНИЕ", (212, 175, 55)),
    ("ТОЛЬКО СЕГОДНЯ", (220, 30, 35)),
    ("MEGA SALE", (220, 50, 35)),
    ("HOT DEAL", (220, 30, 35)),
    ("ХИТ ПРОДАЖ", (212, 175, 55)),
]

BOTTOM_TAGLINES = [
    "ТАКИЕ МАШИНЫ НА РЫНКЕ НЕ ЗАДЕРЖИВАЮТСЯ!",
    "ЗАВТРА МОЖЕТ БЫТЬ ПРОДАНА!",
    "ОДИН ЗВОНОК — И ОНА ТВОЯ!",
    "ЛУЧШАЯ ЦЕНА В ДУБАЕ!",
    "ПОД КЛЮЧ ИЗ ДУБАЯ!",
]


def _cover_resize(img: Image.Image, w: int, h: int) -> Image.Image:
    """Resize image to cover (w,h) keeping aspect ratio, then center-crop."""
    src_w, src_h = img.size
    scale = max(w / src_w, h / src_h)
    new_w, new_h = int(src_w * scale), int(src_h * scale)
    img = img.resize((new_w, new_h), Image.Resampling.LANCZOS)
    left = (new_w - w) // 2
    top = (new_h - h) // 2
    return img.crop((left, top, left + w, top + h))


def _gradient(w: int, h: int, top=(15, 15, 18), bottom=(0, 0, 0)) -> Image.Image:
    img = Image.new("RGB", (w, h), top)
    px = img.load()
    for y in range(h):
        t = y / max(1, h - 1)
        r = int(top[0] + (bottom[0] - top[0]) * t)
        g = int(top[1] + (bottom[1] - top[1]) * t)
        b = int(top[2] + (bottom[2] - top[2]) * t)
        for x in range(w):
            px[x, y] = (r, g, b)
    return img


def _draw_text_with_shadow(draw: ImageDraw.ImageDraw, xy, text, font, fill, shadow=(0, 0, 0, 160), offset=3):
    x, y = xy
    draw.text((x + offset, y + offset), text, font=font, fill=shadow)
    draw.text((x, y), text, font=font, fill=fill)


def _text_w(draw: ImageDraw.ImageDraw, text: str, font) -> int:
    try:
        bbox = draw.textbbox((0, 0), text, font=font)
        return bbox[2] - bbox[0]
    except Exception:
        return draw.textlength(text, font=font) if hasattr(draw, "textlength") else len(text) * (font.size // 2)


# ---- Public API --------------------------------------------------------------

def generate_ad_image_prompt(car: dict) -> str:
    """Build a text prompt for AI image generation (used when AI is enabled)."""
    title = car.get("title") or f"{car.get('brand','')} {car.get('model','')} {car.get('year','')}".strip()
    body = car.get("bodyType") or ""
    fuel = car.get("fuel") or ""
    price = car.get("price") or ""
    return (
        f"Professional Instagram automotive sale poster, vertical 9:16. "
        f"Hero: {title} {body}. "
        f"Dark cinematic background with gold and red accents, dramatic lighting, "
        f"premium car dealership style. Bold headline 'SHOCK PRICE'. "
        f"Large price tag '{price}'. {fuel} drivetrain accent. "
        f"Sharp, hyper-real, glossy, professional advertising photography, no people."
    )


async def _try_openai_image(car: dict) -> Optional[bytes]:
    """Optional: call OpenAI Images API. Returns JPEG bytes or None."""
    if not OPENAI_API_KEY:
        return None
    enabled = os.getenv("ENABLE_AI_IMAGE", "false").lower() in ("1", "true", "yes")
    if not enabled:
        return None
    try:
        from openai import AsyncOpenAI
        client = AsyncOpenAI(api_key=OPENAI_API_KEY)
        prompt = generate_ad_image_prompt(car)
        resp = await client.images.generate(
            model="dall-e-3",
            prompt=prompt,
            size="1024x1792",  # vertical
            quality="standard",
            n=1,
        )
        url = resp.data[0].url
        if not url:
            return None
        import httpx
        async with httpx.AsyncClient(timeout=60) as http:
            r = await http.get(url)
            r.raise_for_status()
            return r.content
    except Exception as e:
        print(f"[image_generator] OpenAI error: {e}")
        return None


def generate_local_poster(car: dict, main_photo_path: Optional[Path | str]) -> bytes:
    """
    Build a 1080x1350 (4:5) vertical ad poster using PIL.
    Top 62% is the car photo with gradient overlay. Bottom 38% is text panel.
    """
    W, H = 1080, 1350
    img = _gradient(W, H, top=(20, 20, 24), bottom=(0, 0, 0))

    # --- Top: car photo (cover, with darken overlay at bottom) ---
    photo_h = int(H * 0.62)
    if main_photo_path and Path(main_photo_path).exists():
        try:
            with Image.open(main_photo_path) as ph:
                ph = ph.convert("RGB")
                ph = _cover_resize(ph, W, photo_h)
                img.paste(ph, (0, 0))
        except Exception:
            pass

    draw = ImageDraw.Draw(img, "RGBA")

    # Bottom fade on photo for text legibility
    fade_h = 220
    for y in range(fade_h):
        t = y / fade_h
        alpha = int(255 * t * 0.7)
        draw.line([(0, photo_h - fade_h + y), (W, photo_h - fade_h + y)], fill=(0, 0, 0, alpha))

    # --- Top-left badge ---
    badge_text, badge_color = random.choice(HOOK_BADGES)
    badge_font = _load_font("narrow", 56)
    bw = _text_w(draw, badge_text, badge_font) + 50
    bh = 84
    bx, by = 40, 40
    # Rotated rectangle look via simple solid
    draw.rectangle([bx, by, bx + bw, by + bh], fill=badge_color + (255,))
    draw.text((bx + 25, by + 10), badge_text, font=badge_font, fill=(255, 255, 255))

    # --- Brand + Model headline (on photo, near bottom of photo area) ---
    brand = (car.get("brand") or "").upper()
    model = (car.get("model") or "").upper()
    year = (car.get("year") or "").strip()
    title_text = f"{brand} {model}".strip()
    if not title_text:
        title_text = (car.get("title") or "AUTO").upper()
    title_font = _load_font("narrow", 110)
    year_font = _load_font("bold", 70)

    # Position title near the photo bottom
    title_y = photo_h - 200
    _draw_text_with_shadow(draw, (40, title_y), title_text, title_font, (255, 255, 255))
    if year:
        _draw_text_with_shadow(draw, (40, title_y + 110), year, year_font, (212, 175, 55))

    # --- Bottom panel: info blocks + price ---
    panel_y = photo_h + 20
    # Stats row: engine, mileage, fuel
    stat_font = _load_font("bold", 40)
    label_font = _load_font("regular", 26)
    items = []
    engine = car.get("engine") or ""
    if engine:
        items.append(("ДВИГАТЕЛЬ", engine))
    fuel = car.get("fuel") or ""
    if fuel:
        items.append(("ТОПЛИВО", fuel))
    mileage = car.get("mileage") or ""
    if mileage:
        items.append(("ПРОБЕГ", mileage))
    body = car.get("bodyType") or ""
    if body and len(items) < 3:
        items.append(("КУЗОВ", body))
    if not items:
        items.append(("СТРАНА", "Dubai / UAE"))

    n = len(items)
    col_w = (W - 80) // n
    for i, (label, value) in enumerate(items[:3]):
        cx = 40 + i * col_w + col_w // 2
        # Label
        lw = _text_w(draw, label, label_font)
        draw.text((cx - lw // 2, panel_y + 20), label, font=label_font, fill=(160, 160, 165))
        # Value (truncate if too long)
        val = value
        while _text_w(draw, val, stat_font) > col_w - 30 and len(val) > 4:
            val = val[:-2]
        if val != value:
            val += "…"
        vw = _text_w(draw, val, stat_font)
        draw.text((cx - vw // 2, panel_y + 60), val, font=stat_font, fill=(255, 255, 255))

    # Separator
    sep_y = panel_y + 160
    draw.line([(60, sep_y), (W - 60, sep_y)], fill=(212, 175, 55, 220), width=2)

    # --- Price (centered, huge) ---
    price = car.get("price") or "по запросу"
    price_font = _load_font("narrow", 140)
    pw = _text_w(draw, price, price_font)
    _draw_text_with_shadow(draw, ((W - pw) // 2, sep_y + 30), price, price_font, (212, 175, 55))

    # --- Bottom tagline ---
    tag_font = _load_font("bold", 36)
    tagline = random.choice(BOTTOM_TAGLINES)
    tw = _text_w(draw, tagline, tag_font)
    draw.text(((W - tw) // 2, H - 70), tagline, font=tag_font, fill=(220, 220, 220))

    # Save to bytes
    out = io.BytesIO()
    img.save(out, format="JPEG", quality=88, optimize=True)
    return out.getvalue()


async def generate_ad_image(car: dict, main_photo_path: Optional[Path | str]) -> Optional[bytes]:
    """
    Try AI image generation first (if enabled), fallback to local PIL poster.
    Returns JPEG bytes, or None if everything fails (very unlikely with PIL fallback).
    """
    # 1. Try OpenAI Images
    ai_bytes = await _try_openai_image(car)
    if ai_bytes:
        return ai_bytes
    # 2. Local PIL poster
    try:
        return generate_local_poster(car, main_photo_path)
    except Exception as e:
        print(f"[image_generator] PIL poster failed: {e}")
        return None
