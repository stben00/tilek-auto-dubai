"""Ad-poster generator for car listings.

Three AI backends + Pillow fallback, controlled by POSTER_MODE + AI_PROVIDER env vars:

  POSTER_MODE:
    "ai"    → AI only (fails loudly if all AI fails)
    "local" → Pillow templates only (no paid API)
    "auto"  → AI first, Pillow fallback (default)

  AI_PROVIDER (controls which AI engine to try for posters):
    "gemini" → Gemini only (Flash image-gen → Imagen 3 fallback)
    "openai" → OpenAI gpt-image-1 only
    "auto"   → Gemini first → OpenAI fallback (default)

Pillow templates (local fallback):
- premium_dubai          → default, full-bleed photo + overlay layout
- aggressive_black_yellow → SUV / Land Cruiser / RAV4
- red_price_blast        → cheap price (< $15k)
- luxury_dark_gold       → BMW / Mercedes / Lexus / Porsche / Audi
- clean_white_premium    → Toyota / Honda / mid-tier sedans
- hybrid_green_energy    → fuel = Гибрид / Электро

Output: vertical 1080x1350 (or POSTER_SIZE from env) JPEG bytes.
"""
import base64
import io
import logging
import os
import random
from pathlib import Path
from typing import Optional

from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont, ImageOps

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration (env-tunable)
# ---------------------------------------------------------------------------

def _parse_size(s: str, default=(1080, 1350)) -> tuple[int, int]:
    try:
        a, b = s.lower().split("x")
        return int(a), int(b)
    except Exception:
        return default


POSTER_W, POSTER_H = _parse_size(os.getenv("POSTER_SIZE", "1080x1350"))

# ---------------------------------------------------------------------------
# Font discovery
# ---------------------------------------------------------------------------

_FONT_CACHE: dict[str, str] = {}

CANDIDATES = {
    "bold": [
        # Linux (Fly.io container) — DejaVu + Noto, both ship with Cyrillic support
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf",
        # macOS (local dev)
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
        "/System/Library/Fonts/Supplemental/Arial Black.ttf",
        "/System/Library/Fonts/Helvetica.ttc",
        "/Library/Fonts/Arial Bold.ttf",
        # Windows
        "C:/Windows/Fonts/arialbd.ttf",
    ],
    "narrow": [
        # Linux
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSansCondensed-Bold.ttf",
        "/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf",
        # macOS
        "/System/Library/Fonts/Supplemental/Impact.ttf",
        "/System/Library/Fonts/Supplemental/Arial Narrow Bold.ttf",
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    ],
    "regular": [
        # Linux
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf",
        # macOS
        "/System/Library/Fonts/Supplemental/Arial.ttf",
        "/System/Library/Fonts/Helvetica.ttc",
    ],
}


def _find_font(kind: str) -> Optional[str]:
    if kind in _FONT_CACHE:
        return _FONT_CACHE[kind]
    for p in CANDIDATES.get(kind, []):
        if os.path.exists(p):
            _FONT_CACHE[kind] = p
            return p
    return None


def _load_font(kind: str, size: int) -> ImageFont.ImageFont:
    path = _find_font(kind) or _find_font("bold") or _find_font("regular")
    if path:
        try:
            return ImageFont.truetype(path, size)
        except Exception:
            pass
    return ImageFont.load_default()


# ---------------------------------------------------------------------------
# Drawing helpers
# ---------------------------------------------------------------------------

def _text_w(draw: ImageDraw.ImageDraw, text: str, font) -> int:
    try:
        bbox = draw.textbbox((0, 0), text, font=font)
        return bbox[2] - bbox[0]
    except Exception:
        return int(getattr(draw, "textlength", lambda *a, **k: len(text) * font.size // 2)(text, font=font))


def _shadow_text(draw, xy, text, font, fill, shadow=(0, 0, 0, 200), offset=3):
    x, y = xy
    draw.text((x + offset, y + offset), text, font=font, fill=shadow)
    draw.text((x, y), text, font=font, fill=fill)


def _gradient(w: int, h: int, top: tuple[int, int, int], bottom: tuple[int, int, int]) -> Image.Image:
    """Vertical gradient — efficient row-by-row."""
    base = Image.new("RGB", (w, h), top)
    draw = ImageDraw.Draw(base)
    for y in range(h):
        t = y / max(1, h - 1)
        r = int(top[0] + (bottom[0] - top[0]) * t)
        g = int(top[1] + (bottom[1] - top[1]) * t)
        b = int(top[2] + (bottom[2] - top[2]) * t)
        draw.line([(0, y), (w, y)], fill=(r, g, b))
    return base


def _cover_resize(img: Image.Image, w: int, h: int) -> Image.Image:
    src_w, src_h = img.size
    scale = max(w / src_w, h / src_h)
    nw, nh = int(src_w * scale), int(src_h * scale)
    img = img.resize((nw, nh), Image.Resampling.LANCZOS)
    left = (nw - w) // 2
    top = (nh - h) // 2
    return img.crop((left, top, left + w, top + h))


def _load_photo(path: Optional[Path | str]) -> Optional[Image.Image]:
    if not path:
        return None
    try:
        p = Path(path)
        if not p.exists():
            return None
        img = Image.open(p).convert("RGB")
        return _enhance_photo(img)
    except Exception:
        return None


def enhance_photo_bytes(data: bytes, max_side: int = 1600, jpeg_quality: int = 88) -> bytes:
    """
    Public helper used by bot.py to enhance a raw video frame BEFORE it's
    saved as the catalog _1.jpg. Same premium treatment as the poster
    background (adaptive brightness, shadow lift, contrast/saturation,
    unsharp mask), then resized to a sensible web max-side and re-encoded
    as a clean JPEG.

    Falls back to the original bytes on any error.
    """
    try:
        src = Image.open(io.BytesIO(data)).convert("RGB")
        out = _enhance_photo(src)
        w, h = out.size
        if max(w, h) > max_side:
            scale = max_side / max(w, h)
            out = out.resize((int(w * scale), int(h * scale)), Image.Resampling.LANCZOS)
        buf = io.BytesIO()
        out.save(buf, format="JPEG", quality=jpeg_quality, optimize=True)
        return buf.getvalue()
    except Exception:
        return data


def _enhance_photo(img: Image.Image) -> Image.Image:
    """
    Aggressive premium "studio" enhancement so dim Dubai garage video frames
    look like a lit dealership shot. Each step guarded so a corrupt image
    still renders.

    Pipeline:
      1. Auto-contrast (per-channel) — recovers detail + neutralises colour casts.
      2. ADAPTIVE brightness — measure mean luma; the darker the frame, the
         bigger the lift (up to ~1.5x for very dark garage clips).
      3. Shadow lift via a gentle gamma curve so the lower half of the car
         (bumper, wheels) stops being a black blob.
      4. Strong saturation — paint colour pops.
      5. Strong contrast — dramatic, glossy look.
      6. Large unsharp mask — crisp grille / badge / wheel edges at poster scale.
    """
    try:
        img = ImageOps.autocontrast(img, cutoff=1)
    except Exception:
        pass
    # Adaptive brightness based on mean luminance
    try:
        from PIL import ImageStat
        mean = ImageStat.Stat(img.convert("L")).mean[0]  # 0..255
        if mean < 70:
            factor = 1.55
        elif mean < 100:
            factor = 1.35
        elif mean < 130:
            factor = 1.18
        else:
            factor = 1.06
        img = ImageEnhance.Brightness(img).enhance(factor)
    except Exception:
        pass
    # Shadow lift (gamma < 1 brightens midtones/shadows without blowing highlights)
    try:
        gamma = 0.82
        lut = [min(255, int(((i / 255.0) ** gamma) * 255)) for i in range(256)] * 3
        img = img.convert("RGB").point(lut)
    except Exception:
        pass
    try:
        img = ImageEnhance.Color(img).enhance(1.28)
    except Exception:
        pass
    try:
        img = ImageEnhance.Contrast(img).enhance(1.22)
    except Exception:
        pass
    try:
        img = img.filter(ImageFilter.UnsharpMask(radius=2.0, percent=180, threshold=2))
    except Exception:
        pass
    return img


def _apply_spotlight(img: Image.Image, strength: int = 60) -> Image.Image:
    """Brighten the centre (where the car sits) with a soft radial 'studio light'.

    Builds a small radial mask, upsamples + blurs, then screen-blends a white
    plate through it. Cheap and gives the car a lit, premium pop.
    """
    try:
        w, h = img.size
        small = 96
        mask_small = Image.new("L", (small, small), 0)
        d = ImageDraw.Draw(mask_small)
        steps = 20
        # Centre the spotlight slightly below middle, on the car body
        cx, cy = small // 2, int(small * 0.6)
        for i in range(steps):
            t = i / (steps - 1)
            radius = int((small / 2) * (1 - t * 0.85))
            alpha = int(strength * (1 - t) ** 1.5)
            d.ellipse([cx - radius, cy - radius, cx + radius, cy + radius], fill=alpha)
        mask = mask_small.resize((w, h), Image.Resampling.LANCZOS).filter(ImageFilter.GaussianBlur(radius=70))
        light = Image.new("RGB", (w, h), (255, 255, 255))
        return _screen_blend(img, light, mask)
    except Exception:
        return img


def _screen_blend(base: Image.Image, top: Image.Image, mask: Image.Image) -> Image.Image:
    """Screen-blend `top` onto `base` through `mask` (mask 0=base, 255=full screen)."""
    try:
        from PIL import ImageChops
        screened = ImageChops.screen(base.convert("RGB"), top.convert("RGB"))
        return Image.composite(screened, base.convert("RGB"), mask)
    except Exception:
        return base


def _apply_vignette(img: Image.Image, strength: int = 130) -> Image.Image:
    """Soft radial darkening of the corners for cinematic look.

    Fast path: build a tiny 64x64 radial mask with concentric ellipses, then
    resize + blur it up to the photo dimensions. Avoids the per-pixel Python
    loop which is unusable on a 1080x1350 image.
    """
    try:
        w, h = img.size
        small = 96
        mask_small = Image.new("L", (small, small), 0)
        d = ImageDraw.Draw(mask_small)
        # 0 alpha at centre → up to `strength` at the corner ring
        steps = 24
        for i in range(steps):
            t = i / (steps - 1)
            alpha = int(strength * (t ** 1.4))
            radius = int((small / 2) * (1 - t * 0.92))
            cx = cy = small // 2
            d.ellipse([cx - radius, cy - radius, cx + radius, cy + radius],
                      fill=strength - alpha)
        mask = mask_small.resize((w, h), Image.Resampling.LANCZOS)
        mask = mask.filter(ImageFilter.GaussianBlur(radius=60))
        dark = Image.new("RGB", (w, h), (0, 0, 0))
        return Image.composite(dark, img, mask)
    except Exception:
        return img


def _diagonal_band(img: Image.Image, color: tuple[int, int, int, int], y_center: int, height: int = 70, angle: float = -8):
    """Draw a slanted color band across the image at vertical center y_center."""
    band = Image.new("RGBA", (img.width + 200, height), color)
    band = band.rotate(angle, expand=True, resample=Image.Resampling.BILINEAR)
    bx = (img.width - band.width) // 2
    by = y_center - band.height // 2
    img.paste(band, (bx, by), band)


def _badge(draw: ImageDraw.ImageDraw, xy: tuple[int, int], text: str, font, bg: tuple, fg=(255, 255, 255)):
    x, y = xy
    tw = _text_w(draw, text, font)
    pad_x, pad_y = 22, 10
    draw.rounded_rectangle(
        [x, y, x + tw + pad_x * 2, y + font.size + pad_y * 2],
        radius=8,
        fill=bg,
    )
    draw.text((x + pad_x, y + pad_y), text, font=font, fill=fg)
    return tw + pad_x * 2  # width used


def _truncate(draw, text: str, font, max_w: int) -> str:
    if _text_w(draw, text, font) <= max_w:
        return text
    while text and _text_w(draw, text + "…", font) > max_w:
        text = text[:-1]
    return text + "…" if text else ""


# ---------------------------------------------------------------------------
# Base template — all 5 templates customize this via a `style` dict.
# ---------------------------------------------------------------------------

def _stats_for(car: dict) -> list[tuple[str, str]]:
    items = []
    if car.get("engine"):
        items.append(("ДВИГАТЕЛЬ", str(car["engine"])))
    if car.get("fuel"):
        items.append(("ТОПЛИВО", str(car["fuel"])))
    if car.get("mileage"):
        items.append(("ПРОБЕГ", str(car["mileage"])))
    if not items:
        items.append(("СТРАНА", car.get("location") or "Dubai / UAE"))
    return items[:3]


def _draw_base_poster(car: dict, photo: Optional[Image.Image], style: dict) -> Image.Image:
    """
    Common layout shared by all templates. `style` controls the palette and accents:
      bg_top, bg_bottom: background gradient
      photo_h_ratio: how much vertical space the photo occupies (0.0-1.0)
      brand_color, year_color, price_color
      badge_text, badge_bg, badge_fg
      tagline_text, tagline_color
      accent_band_color: optional diagonal stripe behind the brand
      stat_label_color, stat_value_color
      separator_color
      tagline_text overridable; otherwise random pick
    """
    W, H = POSTER_W, POSTER_H
    img = _gradient(W, H, style["bg_top"], style["bg_bottom"])

    # Photo section (top)
    photo_h = int(H * style.get("photo_h_ratio", 0.62))
    if photo is not None:
        try:
            ph = _cover_resize(photo, W, photo_h)
            img.paste(ph, (0, 0))
        except Exception:
            pass
    else:
        # No-photo fallback: subtle inner shape
        ImageDraw.Draw(img).rectangle([60, 60, W - 60, photo_h - 60], outline=style["brand_color"] + (180,) if len(style["brand_color"]) == 3 else style["brand_color"], width=4)

    draw = ImageDraw.Draw(img, "RGBA")

    # Bottom-of-photo fade for legibility
    fade_h = 240
    for y in range(fade_h):
        t = y / fade_h
        alpha = int(220 * t)
        draw.line([(0, photo_h - fade_h + y), (W, photo_h - fade_h + y)], fill=(0, 0, 0, alpha))

    # Badge top-left
    badge_text = style["badge_text"]
    badge_font = _load_font("narrow", 56)
    _badge(draw, (40, 40), badge_text, badge_font, bg=style["badge_bg"], fg=style.get("badge_fg", (255, 255, 255)))

    # Optional accent band behind brand area
    if "accent_band_color" in style:
        _diagonal_band(img, style["accent_band_color"], photo_h - 150, height=70, angle=style.get("accent_band_angle", -8))
        draw = ImageDraw.Draw(img, "RGBA")  # rebind after paste

    # Brand + Model (huge, near photo bottom). Auto-shrink font instead of
    # truncating with "...", so the full name always fits.
    brand = (car.get("brand") or "").upper()
    model = (car.get("model") or "").upper()
    title_text = (brand + " " + model).strip() or (car.get("title") or "AUTO").upper()
    title_font = None
    for size in range(110, 60, -5):
        candidate = _load_font("narrow", size)
        if _text_w(draw, title_text, candidate) <= W - 80:
            title_font = candidate
            break
    if title_font is None:
        title_font = _load_font("narrow", 60)
        title_text = _truncate(draw, title_text, title_font, W - 80)
    _shadow_text(draw, (40, photo_h - 200), title_text, title_font, style["brand_color"])

    # Year (under title)
    year = str(car.get("year") or "").strip()
    if year:
        year_font = _load_font("bold", 72)
        _shadow_text(draw, (40, photo_h - 80), year, year_font, style["year_color"])

    # === Bottom info panel ===
    panel_y = photo_h + 30
    stat_font = _load_font("bold", 40)
    label_font = _load_font("regular", 26)
    stats = _stats_for(car)
    n = len(stats)
    col_w = (W - 80) // n
    for i, (label, value) in enumerate(stats):
        cx = 40 + i * col_w + col_w // 2
        lw = _text_w(draw, label, label_font)
        draw.text((cx - lw // 2, panel_y + 16), label, font=label_font, fill=style["stat_label_color"])
        val = _truncate(draw, value, stat_font, col_w - 20)
        vw = _text_w(draw, val, stat_font)
        draw.text((cx - vw // 2, panel_y + 56), val, font=stat_font, fill=style["stat_value_color"])

    # Separator
    sep_y = panel_y + 160
    draw.line([(60, sep_y), (W - 60, sep_y)], fill=style["separator_color"], width=2)

    # Price (huge, centered)
    price = car.get("price") or "по запросу"
    price_font = _load_font("narrow", 145)
    price = _truncate(draw, price, price_font, W - 80)
    pw = _text_w(draw, price, price_font)
    _shadow_text(draw, ((W - pw) // 2, sep_y + 25), price, price_font, style["price_color"])

    # Tagline
    tag_font = _load_font("bold", 36)
    tagline = style["tagline_text"]
    tw = _text_w(draw, tagline, tag_font)
    draw.text(((W - tw) // 2, H - 75), tagline, font=tag_font, fill=style.get("tagline_color", (220, 220, 220)))

    return img


# ---------------------------------------------------------------------------
# Templates
# ---------------------------------------------------------------------------

TAGLINES_HYPE = [
    "ТАКИЕ МАШИНЫ НЕ ЗАДЕРЖИВАЮТСЯ!",
    "ЗАВТРА МОЖЕТ БЫТЬ ПРОДАНА!",
    "ОДИН ЗВОНОК — И ОНА ТВОЯ!",
    "ПОД КЛЮЧ ИЗ ДУБАЯ!",
]


def template_aggressive_black_yellow(car: dict, photo: Optional[Image.Image]) -> Image.Image:
    return _draw_base_poster(car, photo, style={
        "bg_top": (15, 15, 18),
        "bg_bottom": (0, 0, 0),
        "photo_h_ratio": 0.62,
        "brand_color": (255, 215, 0),
        "year_color": (255, 255, 255),
        "price_color": (255, 215, 0),
        "badge_text": "ШОК ЦЕНА!",
        "badge_bg": (220, 30, 35),
        "accent_band_color": (255, 215, 0, 230),
        "accent_band_angle": -7,
        "stat_label_color": (170, 170, 175),
        "stat_value_color": (255, 255, 255),
        "separator_color": (255, 215, 0, 230),
        "tagline_text": random.choice(TAGLINES_HYPE),
        "tagline_color": (255, 235, 100),
    })


def template_red_price_blast(car: dict, photo: Optional[Image.Image]) -> Image.Image:
    return _draw_base_poster(car, photo, style={
        "bg_top": (40, 0, 0),
        "bg_bottom": (10, 0, 0),
        "photo_h_ratio": 0.58,
        "brand_color": (255, 255, 255),
        "year_color": (255, 200, 200),
        "price_color": (255, 60, 60),
        "badge_text": "HOT DEAL 🔥",
        "badge_bg": (255, 215, 0),
        "badge_fg": (10, 0, 0),
        "accent_band_color": (220, 30, 35, 230),
        "accent_band_angle": -6,
        "stat_label_color": (255, 170, 170),
        "stat_value_color": (255, 255, 255),
        "separator_color": (255, 60, 60, 200),
        "tagline_text": "СПЕЦЦЕНА — ТОЛЬКО СЕГОДНЯ!",
        "tagline_color": (255, 230, 230),
    })


def template_luxury_dark_gold(car: dict, photo: Optional[Image.Image]) -> Image.Image:
    return _draw_base_poster(car, photo, style={
        "bg_top": (18, 14, 8),
        "bg_bottom": (0, 0, 0),
        "photo_h_ratio": 0.64,
        "brand_color": (212, 175, 55),
        "year_color": (240, 220, 170),
        "price_color": (212, 175, 55),
        "badge_text": "EXCLUSIVE",
        "badge_bg": (212, 175, 55),
        "badge_fg": (15, 12, 6),
        "stat_label_color": (160, 145, 110),
        "stat_value_color": (240, 220, 170),
        "separator_color": (212, 175, 55, 200),
        "tagline_text": "PREMIUM ИЗ ДУБАЯ — ПОД КЛЮЧ",
        "tagline_color": (212, 175, 55),
    })


def template_clean_white_premium(car: dict, photo: Optional[Image.Image]) -> Image.Image:
    return _draw_base_poster(car, photo, style={
        "bg_top": (245, 245, 248),
        "bg_bottom": (220, 220, 225),
        "photo_h_ratio": 0.60,
        "brand_color": (20, 20, 25),
        "year_color": (180, 30, 30),
        "price_color": (20, 20, 25),
        "badge_text": "ХИТ ПРОДАЖ",
        "badge_bg": (20, 20, 25),
        "badge_fg": (255, 215, 0),
        "stat_label_color": (120, 120, 130),
        "stat_value_color": (20, 20, 25),
        "separator_color": (20, 20, 25, 180),
        "tagline_text": "НАДЁЖНО · ПРОВЕРЕНО · ВЫГОДНО",
        "tagline_color": (60, 60, 65),
    })


def template_hybrid_green_energy(car: dict, photo: Optional[Image.Image]) -> Image.Image:
    return _draw_base_poster(car, photo, style={
        "bg_top": (5, 22, 15),
        "bg_bottom": (0, 0, 0),
        "photo_h_ratio": 0.62,
        "brand_color": (140, 230, 130),
        "year_color": (255, 255, 255),
        "price_color": (140, 230, 130),
        "badge_text": "⚡ HYBRID",
        "badge_bg": (30, 140, 60),
        "accent_band_color": (60, 200, 100, 220),
        "accent_band_angle": -7,
        "stat_label_color": (140, 200, 160),
        "stat_value_color": (240, 255, 240),
        "separator_color": (60, 200, 100, 220),
        "tagline_text": "ЭКОНОМИЯ ТОПЛИВА КАЖДЫЙ ДЕНЬ 💚",
        "tagline_color": (180, 240, 180),
    })


TEMPLATES = {
    "aggressive_black_yellow": template_aggressive_black_yellow,
    "red_price_blast": template_red_price_blast,
    "luxury_dark_gold": template_luxury_dark_gold,
    "clean_white_premium": template_clean_white_premium,
    "hybrid_green_energy": template_hybrid_green_energy,
}

LUXURY_BRANDS = {"bmw", "mercedes", "lexus", "porsche", "audi", "range rover", "land rover", "infiniti", "cadillac"}
SUV_KEYWORDS = {"land cruiser", "rav4", "highlander", "prado", "patrol", "tucson", "santa fe", "wrangler", "x5", "x6", "x7", "gle", "gls", "q7", "q8", "cayenne", "macan", "tahoe", "suburban", "escalade", "explorer", "pajero", "outlander"}


def select_template(car: dict) -> str:
    """Smart template selection. Returns one of TEMPLATES keys."""
    brand = str(car.get("brand", "")).lower()
    model = str(car.get("model", "")).lower()
    body = str(car.get("bodyType", "")).lower()
    fuel = str(car.get("fuel", "")).lower()
    title_low = str(car.get("title", "")).lower()
    price_str = str(car.get("price", ""))
    digits = "".join(c for c in price_str if c.isdigit())
    price_num = int(digits) if digits else 0

    # 1. Hybrid / electric
    if fuel in ("гибрид", "электро") or "hybrid" in title_low or "electric" in title_low:
        return "hybrid_green_energy"
    # 2. Luxury brands
    if brand in LUXURY_BRANDS:
        return "luxury_dark_gold"
    # 3. Cheap price
    if 0 < price_num < 15000:
        return "red_price_blast"
    # 4. SUVs / Off-roaders
    if "внедорожник" in body or model in SUV_KEYWORDS or any(k in title_low for k in SUV_KEYWORDS):
        return "aggressive_black_yellow"
    # 5. Default: clean premium (sedans / hatchbacks / other)
    return "clean_white_premium"


def pick_different_template(car: dict, current: Optional[str]) -> Optional[str]:
    """
    For the 🎨 Regenerate button.

    In AI / auto mode, returns None so the regenerate flow asks gpt-image-1 for a fresh
    image rather than switching to a Pillow template. In local mode, cycles through
    Pillow templates so the user can preview different looks.
    """
    mode = _poster_mode()
    if mode in ("ai", "auto"):
        return None
    all_keys = list(TEMPLATES.keys())
    smart_choice = select_template(car)
    pool = [k for k in all_keys if k != current]
    if not pool:
        return smart_choice
    if current is None or current == AI_POSTER_TEMPLATE:
        return smart_choice
    return random.choice(pool)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def generate_local_poster(car: dict, main_photo_path: Optional[Path | str], template_name: Optional[str] = None) -> tuple[bytes, str]:
    """
    Build a poster locally with Pillow. Always succeeds (fallback to no-photo).
    Returns (jpeg_bytes, template_used).
    """
    photo = _load_photo(main_photo_path)
    if not template_name or template_name not in TEMPLATES:
        template_name = select_template(car)
    fn = TEMPLATES[template_name]
    try:
        img = fn(car, photo)
    except Exception as e:
        print(f"[poster_generator] template {template_name} failed: {e}; falling back")
        img = template_aggressive_black_yellow(car, photo)
        template_name = "aggressive_black_yellow"
    out = io.BytesIO()
    img.save(out, format="JPEG", quality=88, optimize=True)
    return out.getvalue(), template_name


# ---------------------------------------------------------------------------
# Premium Dubai-dealership Pillow template (photo-preserving)
# ---------------------------------------------------------------------------
#
# This is the new default. It puts the client's ORIGINAL photo on the canvas
# (lightly enhanced + dark gradient mask for legibility) and overlays the entire
# layout — headline, СТАРТ pill, bullet list, spec panel, ВЫГОДНОЕ ПРЕДЛОЖЕНИЕ
# badge, ВИДЕО ПО ЗАПРОСУ CTA — via Pillow with proper Cyrillic fonts.
#
# No AI image is generated, so the real car is preserved exactly. Colour
# palette adapts to the car category (luxury / sport / suv / city).

PREMIUM_DUBAI_TEMPLATE = "premium_dubai"

LUXURY_BRAND_SET = {
    "bmw", "mercedes", "mercedes-benz", "lexus", "porsche", "audi",
    "range rover", "land rover", "rolls-royce", "bentley", "maserati",
    "jaguar", "infiniti", "cadillac", "tesla", "genesis",
}
SPORT_KEYWORDS = {
    "m3", "m4", "m5", "m8", "amg", "rs", "type r", "type-r", "gtr",
    "supra", "wrx", "sti", "z4", "z3", "s2000", "gt-r", "r8", "huracan",
    "gallardo", "performance",
}
SUV_KEYWORD_SET = {
    "x5", "x6", "x7", "land cruiser", "rav4", "highlander", "prado",
    "patrol", "tucson", "santa fe", "wrangler", "explorer", "tahoe",
    "suburban", "escalade", "cayenne", "macan", "q7", "q8", "gle", "gls",
    "rx", "lx", "nx", "gx", "4runner", "pajero", "outlander", "x-trail",
    "f-150", "f150", "ram", "silverado",
}
SUV_BODY_KEYWORDS = {"внедорожник", "кроссовер", "suv", "crossover", "pickup", "пикап"}


def detect_car_category(car: dict) -> str:
    """Returns one of: 'sport', 'luxury', 'suv', 'city'."""
    brand = str(car.get("brand", "")).lower().strip()
    model = str(car.get("model", "")).lower().strip()
    title = str(car.get("title", "")).lower().strip()
    body = str(car.get("bodyType", "")).lower().strip()
    haystack = " ".join([brand, model, title])

    if any(kw in haystack for kw in SPORT_KEYWORDS):
        return "sport"
    if any(kw in haystack for kw in SUV_KEYWORD_SET) or any(b in body for b in SUV_BODY_KEYWORDS):
        return "suv"
    if brand in LUXURY_BRAND_SET:
        return "luxury"
    return "city"


# Category → (accent_rgb, accent_dark_rgb, headline_rgb, mood_label)
_CATEGORY_PALETTES = {
    "luxury": ((212, 175, 55),  (160, 130, 35),  (255, 255, 255), "PREMIUM"),
    "sport":  ((230, 60, 60),   (170, 30, 30),   (255, 255, 255), "SPORT"),
    "suv":    ((255, 200, 60),  (180, 130, 25),  (255, 255, 255), "POWER"),
    "city":   ((255, 215, 0),   (200, 160, 0),   (255, 255, 255), "DAILY"),
}


# Brand-specific or category-specific bullet copy. Picked at render time so each
# car gets its own mood instead of the four generic lines on every poster.
_BRAND_BULLETS = {
    "bmw":      ["Настоящий BMW характер",    "Динамика и точное управление",
                 "Премиум сборка и стиль",     "Выглядит дороже своей цены"],
    "mercedes": ["Эталон комфорта Mercedes",  "Тишина и плавный ход",
                 "Премиум сборка и материалы", "Машина для своих людей"],
    "lexus":    ["Премиум по-японски",         "Тихий, мягкий, безотказный",
                 "Салон бизнес-класса",        "Без поломок и сюрпризов"],
    "porsche":  ["Настоящий Porsche DNA",      "Управление с первой секунды",
                 "Премиум-салон, deep design", "Машина-инвестиция"],
    "audi":     ["Audi quattro — уверенность", "Premium салон и LED-оптика",
                 "Технологии Audi каждый день", "Держит цену на рынке"],
    "toyota":   ["Toyota — проверено временем", "Минимум поломок",
                 "Экономия на бензине и ТО",    "Надёжно и на каждый день"],
    "honda":    ["Honda — надёжно и практично", "Мотор без головной боли",
                 "Экономный расход",            "Просторный комфортный салон"],
    "hyundai":  ["Современная Hyundai",         "Богатая комплектация",
                 "Комфорт за разумные деньги",  "Топ-выбор для города и трассы"],
    "kia":      ["Современная Kia, всё что нужно", "Богатая комплектация",
                 "Просторный салон, экономный мотор", "Не подведёт в дороге"],
    "nissan":   ["Nissan — японское качество",  "Мотор без капризов",
                 "Просторный салон для семьи",  "Честная цена за технику"],
    "ford":     ["Ford — мощь и тяга",          "Готов к любым нагрузкам",
                 "Машина для работы и отдыха",  "Американский характер"],
    "dodge":    ["Американский характер",       "V-образный мотор",
                 "Машина с personality",        "Привезёт куда угодно"],
}

_CATEGORY_BULLETS = {
    "suv":    ["Уверенность на любой дороге", "Просторный салон для семьи",
                "Полный привод — не страшно",   "Внедорожный комфорт"],
    "sport":  ["Спортивная динамика",          "Точное управление",
                "Звук мотора, на который смотрят", "Для тех, кто любит ехать"],
    "luxury": ["Премиум сборка",                "Технологии удивляют",
                "Дорогой interior",             "Машина статуса"],
    "city":   ["Идеален для города",            "Экономный расход",
                "Лёгкая парковка",              "Надёжно на каждый день"],
}


def _bullets_for_category(category: str, brand: str = "", engine: str = "") -> list[str]:
    """Pick 4 bullets — brand-specific copy if we have it, else category-default."""
    if brand:
        b = _BRAND_BULLETS.get(brand.lower().strip())
        if b:
            out = list(b)
            # If we know the engine, inject it into the second bullet as a short prefix.
            # Keep total length tight so it doesn't truncate.
            if engine and len(engine) <= 16:
                out[1] = f"Мотор {engine} — надёжен"
            return out[:4]
    return _CATEGORY_BULLETS.get(category, _CATEGORY_BULLETS["city"])[:4]


def _rounded_rect(draw: ImageDraw.ImageDraw, xy, radius: int, fill, outline=None, width: int = 0):
    draw.rounded_rectangle(xy, radius=radius, fill=fill, outline=outline, width=width)


def _draw_check_glyph(draw, cx: int, cy: int, size: int, color):
    """Draw a check mark as two strokes — works on any font system."""
    half = size // 2
    draw.line([(cx - half, cy), (cx - 2, cy + half - 2)], fill=color, width=4)
    draw.line([(cx - 2, cy + half - 2), (cx + half, cy - half + 2)], fill=color, width=4)


def _draw_check_bullet(draw, x: int, y: int, text: str, font, accent: tuple, text_color=(245, 245, 245)):
    """Yellow rounded box with a stroke-drawn check mark + white text."""
    box = 30
    _rounded_rect(draw, [x, y, x + box, y + box], radius=6, fill=accent)
    _draw_check_glyph(draw, x + box // 2, y + box // 2, size=18, color=(15, 15, 15))
    draw.text((x + box + 14, y + 2), text, font=font, fill=text_color)


def _draw_mini_icon(draw, x: int, y: int, size: int, kind: str, color):
    """Draw a simple recognizable icon with Pillow primitives. No emoji fonts needed."""
    s = size
    w = max(2, s // 12)  # stroke width
    cx, cy = x + s // 2, y + s // 2
    if kind == "year":  # calendar
        draw.rounded_rectangle([x, y + s * 0.12, x + s, y + s], radius=s // 8, outline=color, width=w)
        draw.line([(x, y + s * 0.36), (x + s, y + s * 0.36)], fill=color, width=w)
        draw.line([(x + s * 0.28, y), (x + s * 0.28, y + s * 0.22)], fill=color, width=w)
        draw.line([(x + s * 0.72, y), (x + s * 0.72, y + s * 0.22)], fill=color, width=w)
    elif kind == "engine":  # gear
        r = s // 2 - w
        draw.ellipse([cx - r, cy - r, cx + r, cy + r], outline=color, width=w)
        draw.ellipse([cx - r // 3, cy - r // 3, cx + r // 3, cy + r // 3], outline=color, width=w)
        import math
        for ang in range(0, 360, 45):
            a = math.radians(ang)
            x1 = cx + int((r) * math.cos(a)); y1 = cy + int((r) * math.sin(a))
            x2 = cx + int((r + s * 0.16) * math.cos(a)); y2 = cy + int((r + s * 0.16) * math.sin(a))
            draw.line([(x1, y1), (x2, y2)], fill=color, width=w)
    elif kind == "fuel":  # fuel pump droplet
        draw.rounded_rectangle([x + s * 0.1, y, x + s * 0.6, y + s], radius=s // 10, outline=color, width=w)
        draw.line([(x + s * 0.6, y + s * 0.3), (x + s * 0.85, y + s * 0.3)], fill=color, width=w)
        draw.line([(x + s * 0.85, y + s * 0.3), (x + s * 0.85, y + s * 0.75)], fill=color, width=w)
        draw.line([(x + s * 0.2, y + s * 0.3), (x + s * 0.5, y + s * 0.3)], fill=color, width=w)
    elif kind == "gearbox":  # H-pattern
        draw.line([(x + s * 0.25, y), (x + s * 0.25, y + s)], fill=color, width=w)
        draw.line([(x + s * 0.75, y), (x + s * 0.75, y + s)], fill=color, width=w)
        draw.line([(x + s * 0.25, y + s * 0.5), (x + s * 0.75, y + s * 0.5)], fill=color, width=w)
        for px in (0.25, 0.75):
            draw.ellipse([x + s * px - w, y - w, x + s * px + w, y + w], fill=color)
    elif kind == "drive":  # steering wheel
        r = s // 2 - w
        draw.ellipse([cx - r, cy - r, cx + r, cy + r], outline=color, width=w)
        draw.ellipse([cx - r // 4, cy - r // 4, cx + r // 4, cy + r // 4], fill=color)
        draw.line([(cx, cy), (cx, cy - r)], fill=color, width=w)
        draw.line([(cx, cy), (cx - int(r * 0.87), cy + r // 2)], fill=color, width=w)
        draw.line([(cx, cy), (cx + int(r * 0.87), cy + r // 2)], fill=color, width=w)
    elif kind == "body":  # car silhouette
        draw.rounded_rectangle([x, y + s * 0.35, x + s, y + s * 0.7], radius=s // 8, outline=color, width=w)
        draw.arc([x + s * 0.1, y + s * 0.1, x + s * 0.9, y + s * 0.75], 180, 360, fill=color, width=w)
        draw.ellipse([x + s * 0.15, y + s * 0.62, x + s * 0.35, y + s * 0.82], outline=color, width=w)
        draw.ellipse([x + s * 0.65, y + s * 0.62, x + s * 0.85, y + s * 0.82], outline=color, width=w)


def _draw_spec_row(draw, x: int, y: int, label: str, value: str, font_label, font_value, accent: tuple, icon: str = ""):
    """A spec row: small yellow icon + label (grey, uppercase) + value (white bold)."""
    icon_size = 34
    if icon:
        _draw_mini_icon(draw, x, y + 8, icon_size, icon, accent)
        text_x = x + icon_size + 16
    else:
        bar_w = 5
        draw.rectangle([x, y + 6, x + bar_w, y + 6 + 48], fill=accent)
        text_x = x + bar_w + 18
    draw.text((text_x, y + 4), label.upper(), font=font_label, fill=(170, 170, 175))
    draw.text((text_x, y + 26), value, font=font_value, fill=(255, 255, 255))


def _photo_background(photo: Optional[Image.Image], W: int, H: int) -> Image.Image:
    """Photo as full-bleed background with cinematic enhancement + vignette + overlays."""
    if photo is None:
        bg = _gradient(W, H, (20, 20, 22), (5, 5, 7))
    else:
        bg = _cover_resize(_enhance_photo(photo), W, H)
        # Studio spotlight on the car centre, then cinematic corner vignette.
        bg = _apply_spotlight(bg, strength=70)
        bg = _apply_vignette(bg, strength=130)

    # Dark gradient masks for overlay legibility. Tuned to keep the car bright
    # in the center while darkening only the top-left / top-right / bottom edges.
    overlay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay, "RGBA")
    # Left vertical gradient (headline + pill + bullets sit here)
    left_w = int(W * 0.42)
    for x in range(left_w):
        alpha = int(170 * (1 - x / left_w))
        draw.line([(x, 0), (x, H)], fill=(0, 0, 0, alpha))
    # Top-right corner gradient (spec panel + badge area)
    for y in range(H // 2):
        alpha = int(90 * (1 - y / (H // 2)))
        draw.line([(int(W * 0.55), y), (W, y)], fill=(0, 0, 0, alpha))
    # Bottom gradient (CTA legibility) — short and soft
    bottom_h = int(H * 0.22)
    for y in range(bottom_h):
        alpha = int(130 * (y / bottom_h))
        draw.line([(0, H - bottom_h + y), (W, H - bottom_h + y)], fill=(0, 0, 0, alpha))

    bg = bg.convert("RGBA")
    bg.alpha_composite(overlay)
    return bg.convert("RGB")


def template_premium_dubai(car: dict, photo: Optional[Image.Image]) -> Image.Image:
    """
    Premium Dubai-dealership poster that PRESERVES the original car photo.
    Layout matches the user-approved BMW 330L reference.
    """
    W, H = POSTER_W, POSTER_H
    accent, accent_dark, headline_color, _mood = _CATEGORY_PALETTES.get(
        detect_car_category(car), _CATEGORY_PALETTES["city"]
    )

    img = _photo_background(photo, W, H)
    draw = ImageDraw.Draw(img, "RGBA")

    brand = str(car.get("brand", "") or "").upper().strip()
    model = str(car.get("model", "") or "").upper().strip()
    year = str(car.get("year", "") or "").strip()
    engine = str(car.get("engine", "") or "").strip()
    fuel = str(car.get("fuel", "Бензин") or "Бензин").strip().capitalize()
    body_type = str(car.get("bodyType", "") or "").strip().capitalize()
    price_raw = str(car.get("price", "по запросу") or "по запросу").strip()
    price_digits = "".join(c for c in price_raw if c.isdigit())
    price_pretty = f"${int(price_digits):,}".replace(",", " ") if price_digits else price_raw

    # ===== Top-left: brand headline =====
    title_text = (brand + " " + model).strip() or (car.get("title") or "AUTO").upper()
    headline_font = None
    for size in range(110, 56, -4):
        f = _load_font("narrow", size)
        if _text_w(draw, title_text, f) <= W * 0.55:
            headline_font = f
            break
    if headline_font is None:
        headline_font = _load_font("narrow", 56)
        title_text = _truncate(draw, title_text, headline_font, int(W * 0.55))
    _shadow_text(draw, (40, 50), title_text, headline_font, headline_color, offset=2)

    # Year | Engine subline — auto-shrink so it always fits the left half
    sub_parts = [p for p in [year, engine] if p]
    if sub_parts:
        sub_text = " | ".join(sub_parts)
        sub_font = None
        for size in range(60, 28, -3):
            f = _load_font("bold", size)
            if _text_w(draw, sub_text, f) <= W * 0.52:
                sub_font = f
                break
        if sub_font is None:
            sub_font = _load_font("bold", 28)
            sub_text = _truncate(draw, sub_text, sub_font, int(W * 0.52))
        _shadow_text(draw, (40, 50 + headline_font.size + 6), sub_text, sub_font, accent, offset=2)

    # Price block: yellow rounded box with small label "СТАРТОВАЯ ЦЕНА" + big price
    price_font = _load_font("narrow", 56)
    label_font = _load_font("bold", 20)
    pw = max(_text_w(draw, price_pretty, price_font), _text_w(draw, "СТАРТОВАЯ ЦЕНА", label_font))
    pill_y = 50 + headline_font.size + 80
    box_h = 22 + label_font.size + price_font.size + 18
    pill_box = (40, pill_y, 40 + pw + 56, pill_y + box_h)
    _rounded_rect(draw, pill_box, radius=16, fill=accent)
    draw.text((40 + 28, pill_y + 14), "СТАРТОВАЯ ЦЕНА", font=label_font, fill=(40, 30, 0))
    _shadow_text(draw, (40 + 28, pill_y + 14 + label_font.size + 2), price_pretty, price_font, (15, 15, 15), offset=1)

    # ===== Left: bullet list (dynamic by car category) =====
    bullets = _bullets_for_category(detect_car_category(car), brand=brand, engine=engine)
    # Auto-shrink: pick the largest font where ALL bullets fit in the available width.
    bullet_max_w = int(W * 0.52) - 50  # subtract the checkmark box width + padding
    bullet_font = _load_font("bold", 24)
    for size in range(24, 15, -1):
        f = _load_font("bold", size)
        if all(_text_w(draw, line, f) <= bullet_max_w for line in bullets):
            bullet_font = f
            break
    bullet_y = pill_box[3] + 36  # start below the price box
    for line in bullets:
        # Belt-and-suspenders truncation in case a single bullet is still too long.
        line = _truncate(draw, line, bullet_font, bullet_max_w)
        _draw_check_bullet(draw, 40, bullet_y, line, bullet_font, accent)
        bullet_y += bullet_font.size + 22

    # ===== Top-right: spec panel =====
    panel_x = int(W * 0.58)
    panel_y = 50
    panel_w = W - panel_x - 40
    panel_pad = 24

    specs = []
    if year:
        specs.append(("Год", year, "year"))
    if engine:
        specs.append(("Двигатель", engine, "engine"))
    if fuel:
        specs.append(("Топливо", fuel, "fuel"))
    specs.append(("Коробка", "Автомат", "gearbox"))
    specs.append(("Привод", "Задний", "drive"))
    if body_type:
        specs.append(("Кузов", body_type, "body"))

    row_h = 64
    panel_h = panel_pad * 2 + row_h * len(specs)
    _rounded_rect(draw, (panel_x, panel_y, panel_x + panel_w, panel_y + panel_h),
                  radius=22, fill=(15, 15, 18, 225))

    label_font = _load_font("regular", 17)
    value_font = _load_font("bold", 24)
    for i, (label, value, icon) in enumerate(specs):
        _draw_spec_row(draw, panel_x + panel_pad, panel_y + panel_pad + i * row_h,
                       label, value, label_font, value_font, accent, icon=icon)

    # ===== Middle-right: ВЫГОДНОЕ ПРЕДЛОЖЕНИЕ badge =====
    badge_y = panel_y + panel_h + 20
    badge_pad_x = 26
    badge_pad_y = 18
    badge_line1 = "ВЫГОДНОЕ ПРЕДЛОЖЕНИЕ!"
    badge_line2 = "ЛУЧШАЯ ЦЕНА НА РЫНКЕ"
    bf1 = _load_font("bold", 22)
    bf2 = _load_font("bold", 20)
    bw = max(_text_w(draw, badge_line1, bf1), _text_w(draw, badge_line2, bf2)) + badge_pad_x * 2
    badge_box = (panel_x + (panel_w - bw) // 2, badge_y,
                 panel_x + (panel_w + bw) // 2, badge_y + bf1.size + bf2.size + badge_pad_y * 2 + 4)
    _rounded_rect(draw, badge_box, radius=16, fill=(15, 15, 18, 230))
    draw.text((badge_box[0] + badge_pad_x, badge_box[1] + badge_pad_y),
              badge_line1, font=bf1, fill=accent)
    draw.text((badge_box[0] + badge_pad_x, badge_box[1] + badge_pad_y + bf1.size + 4),
              badge_line2, font=bf2, fill=(255, 255, 255))

    # ===== Bottom CTA bar: video pill (left) + WhatsApp button (right) =====
    cta_y = H - 130

    # Left: "ПОЛУЧИТЬ ВИДЕО" with a play triangle
    play_box = 44
    _rounded_rect(draw, (40, cta_y, 40 + play_box, cta_y + play_box), radius=10, fill=accent)
    draw.polygon([
        (40 + play_box * 0.36, cta_y + play_box * 0.3),
        (40 + play_box * 0.36, cta_y + play_box * 0.7),
        (40 + play_box * 0.70, cta_y + play_box * 0.5),
    ], fill=(15, 15, 15))
    vid_title_font = _load_font("bold", 22)
    vid_sub_font = _load_font("regular", 18)
    vtx = 40 + play_box + 16
    draw.text((vtx, cta_y - 2), "ПОЛУЧИТЬ ВИДЕО", font=vid_title_font, fill=(255, 255, 255))
    draw.text((vtx, cta_y + 26), "Напишите — отправим", font=vid_sub_font, fill=(210, 210, 210))
    draw.text((vtx, cta_y + 48), "полный обзор авто", font=vid_sub_font, fill=(210, 210, 210))

    # Right: green WhatsApp button
    wa_green = (37, 211, 102)
    wa_text = "WHATSAPP"
    wa_font = _load_font("bold", 24)
    wa_icon = 40
    wa_w = wa_icon + 20 + _text_w(draw, wa_text, wa_font) + 56
    wa_box = (W - 40 - wa_w, cta_y - 4, W - 40, cta_y + 52)
    _rounded_rect(draw, wa_box, radius=26, fill=wa_green)
    # WhatsApp glyph: white circle + handset
    icx, icy = wa_box[0] + 28, (wa_box[1] + wa_box[3]) // 2
    draw.ellipse([icx - 18, icy - 18, icx + 18, icy + 18], fill=(255, 255, 255))
    # simple handset curl
    draw.arc([icx - 9, icy - 9, icx + 9, icy + 9], 30, 300, fill=wa_green, width=5)
    draw.ellipse([icx - 3, icy + 4, icx + 5, icy + 12], fill=wa_green)
    draw.text((icx + 28, icy - wa_font.size // 2), wa_text, font=wa_font, fill=(255, 255, 255))

    return img


# Register the premium template in the same dispatcher so existing helpers
# (generate_local_poster, pick_different_template) treat it as a first-class
# option that wins over the legacy 5 templates.
TEMPLATES[PREMIUM_DUBAI_TEMPLATE] = template_premium_dubai


def select_template(car: dict) -> str:  # noqa: F811 — intentionally shadowing the older legacy selector
    """Always prefer the premium photo-preserving template."""
    return PREMIUM_DUBAI_TEMPLATE


# ---------------------------------------------------------------------------
# AI poster generation via OpenAI gpt-image-1
# ---------------------------------------------------------------------------

AI_POSTER_TEMPLATE = "ai_gpt_image"
AI_GEMINI_TEMPLATE = "ai_gemini"
AI_TIMEOUT_SECONDS = 90.0
AI_QUALITY = os.getenv("OPENAI_IMAGE_QUALITY", "medium")  # low / medium / high
AI_SIZE = os.getenv("OPENAI_IMAGE_SIZE", "1024x1536")     # vertical phone format

# Gemini models
GEMINI_FLASH_IMAGE_MODEL = "gemini-2.5-flash-image"
GEMINI_FLASH_IMAGE_FALLBACK = "gemini-3.1-flash-image-preview"
GEMINI_API_BASE = "https://generativelanguage.googleapis.com/v1beta"

# Style reference poster shown to Gemini Nano Banana so it matches our brand layout.
# Generated once from template_premium_dubai() — see scripts in repo README.
REFERENCE_POSTER_PATH = Path(__file__).parent / "assets" / "reference_poster.jpg"


def _load_reference_poster_b64() -> Optional[str]:
    try:
        if REFERENCE_POSTER_PATH.exists():
            with open(REFERENCE_POSTER_PATH, "rb") as f:
                return base64.b64encode(f.read()).decode()
    except Exception as e:
        log.warning("Could not load reference poster: %s", e)
    return None


def _build_ai_prompt(car: dict) -> str:
    """
    Premium Dubai-dealership "Срочно сатылат" poster prompt.

    The reference layout (matches the user's approved design):
      - Top-left:  brand+model headline, year/engine line, yellow "СТАРТ: $price БАШТАЛАТ" pill, bullet list
      - Top-right: black spec panel with year / engine / fuel / gearbox / drive / color rows
      - Middle:   "🔥 ВЫГОДНОЕ ПРЕДЛОЖЕНИЕ! ЛУЧШАЯ ЦЕНА НА РЫНКЕ" badge under spec panel
      - Bottom-left CTA: "📷 ВИДЕО ПО ЗАПРОСУ! Напишите — отправим подробное видео автомобиля"
      - Background: the user's car photo, cinematic luxury lighting, no body changes
    """
    brand = (car.get("brand") or "").upper().strip()
    model = (car.get("model") or "").upper().strip()
    year = str(car.get("year") or "").strip()
    title_line = (brand + " " + model).strip() or (car.get("title") or "AUTO").upper()
    engine = str(car.get("engine") or "").strip()
    fuel = str(car.get("fuel") or "Бензин").strip().capitalize()
    price = str(car.get("price") or "по запросу").strip().replace("$", "").strip()
    body_type = str(car.get("bodyType") or "").strip()
    # Two-line under-title (year | engine). Omit pieces that are missing.
    sub_parts = [p for p in [year, engine] if p]
    sub_line = " | ".join(sub_parts) if sub_parts else "—"

    # Spec rows for the right-hand panel. Each line is "Label: Value" with an emoji-style icon.
    spec_rows = []
    if year:
        spec_rows.append(f"📅  Год: {year}")
    if engine:
        spec_rows.append(f"🔧  Объём: {engine}")
    if fuel:
        spec_rows.append(f"⛽  Топливо: {fuel}")
    spec_rows.append("⚙️  Коробка: Автомат")
    spec_rows.append("🚗  Привод: задний")
    if body_type:
        spec_rows.append(f"🎨  Кузов: {body_type}")
    spec_block = "\n".join(spec_rows) if spec_rows else "—"

    return f"""# SYSTEM ROLE
You are a premium automotive marketing designer for a luxury Dubai car marketplace.
You are NOT generating a new AI car. You are converting the provided real car photo
into a PREMIUM AUTO DEALERSHIP POSTER.

# CRITICAL — KEEP THE ORIGINAL CAR
Preserve the provided car exactly: body, headlights, grille, bumper, wheels, color,
angle, all body details. Do NOT redraw, restyle or replace the vehicle. Only enhance
lighting / contrast / reflections around it.

# CANVAS
Vertical 2:3 advertising poster, like a premium Instagram ad for a Dubai dealership.

# LAYOUT (this matches the approved reference exactly)

TOP-LEFT BLOCK
  Large bold white headline: "{title_line}"
  Yellow accent line right under it: "{sub_line}"
  Yellow rounded pill with black bold text: "СТАРТ: ${price} БАШТАЛАТ"
  Below the pill, a column of 4–5 white bullet lines with small yellow check icons.
  Use ONLY these bullets (do not invent extras):
    ✔  Премиальный комфорт и технологии
    ✔  Динамичный мотор {engine if engine else ''}
    ✔  Идеален для города и трассы
    ✔  Стильный, динамичный, надёжный

TOP-RIGHT BLOCK
  A dark rounded panel containing the spec list, each row with a small yellow icon:
{chr(10).join("    " + line for line in spec_rows)}

MIDDLE-RIGHT BADGE (just below the spec panel)
  Dark rounded pill with a flame icon on the left, white text on two lines:
  "🔥 ВЫГОДНОЕ ПРЕДЛОЖЕНИЕ!"
  "ЛУЧШАЯ ЦЕНА НА РЫНКЕ"

BOTTOM-LEFT CTA (small)
  Yellow camera icon pill: "📷 ВИДЕО ПО ЗАПРОСУ!"
  Under it, two white lines: "Напишите — отправим" / "подробное видео автомобиля"

MAIN IMAGE
  The provided car photo, large, centered-right, the front/face of the car is the
  focal point. Cinematic Dubai-showroom lighting: warm sunset tones, deep black
  shadows, glossy reflections, soft glow on the headlights. The background can be
  the original surroundings but enhanced for premium feel — no random objects or
  text added.

# DESIGN STYLE
- luxury automotive poster, Dubai dealership style, black + #FFD700 gold aesthetic
- premium realistic reflections, cinematic lighting, deep shadows, soft glow
- bold modern sans-serif typography, perfectly aligned, no text glitches
- background never plain white

# TYPOGRAPHY RULES
- All Cyrillic text MUST be perfectly spelled. Do NOT garble or replace letters with
  Latin lookalikes. Do NOT invent extra labels.
- Render ONLY the text strings listed above.
- No watermark, no logo, no phone number, no website URL.

# NEGATIVE
- no cartoon, no fake car, no replaced body, no distorted grille, no fake wheels
- no AI artifacts, no broken reflections, no random background objects
- no extra Cyrillic labels (ПРЕМИУМ САЛОН, МУЛЬТИМЕДИА, КОМФОРТ, etc.)

# SALES PSYCHOLOGY
The poster must trigger urgency, a sense of a great deal, and the desire to call /
write the seller. It should look like a real, expensive Dubai dealership ad — NOT
like an AI image.
"""


async def _generate_with_gpt_image(car: dict, main_photo_path: Optional[Path | str]) -> Optional[bytes]:
    """Call OpenAI gpt-image-1 (images.edit if a reference photo is provided, else images.generate)."""
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        log.warning("OPENAI_API_KEY missing; skipping AI poster")
        return None
    try:
        from openai import AsyncOpenAI
    except ImportError:
        log.warning("openai package not installed")
        return None

    prompt = _build_ai_prompt(car)
    client = AsyncOpenAI(api_key=api_key, timeout=AI_TIMEOUT_SECONDS)

    try:
        if main_photo_path and Path(main_photo_path).exists():
            with open(main_photo_path, "rb") as f:
                resp = await client.images.edit(
                    model="gpt-image-1",
                    image=f,
                    prompt=prompt,
                    size=AI_SIZE,
                    quality=AI_QUALITY,
                )
        else:
            resp = await client.images.generate(
                model="gpt-image-1",
                prompt=prompt,
                size=AI_SIZE,
                quality=AI_QUALITY,
            )
    except Exception as e:
        log.warning("gpt-image-1 call failed: %s", e)
        return None

    try:
        data = resp.data[0]
        b64 = getattr(data, "b64_json", None)
        if b64:
            return base64.b64decode(b64)
        url = getattr(data, "url", None)
        if url:
            import httpx
            async with httpx.AsyncClient(timeout=30) as http:
                r = await http.get(url)
                r.raise_for_status()
                return r.content
        log.warning("gpt-image-1 returned no usable image data")
        return None
    except Exception as e:
        log.warning("gpt-image-1 response parsing failed: %s", e)
        return None


# ---------------------------------------------------------------------------
# Gemini image generation (Flash image-gen + Imagen 3 fallback)
# ---------------------------------------------------------------------------

def _build_gemini_poster_prompt(car: dict) -> str:
    """
    Compact, photo-preserving prompt tuned for gemini-2.5-flash-image (Nano Banana).
    Nano Banana is an *image-editing* model — it takes the supplied car photo and
    keeps the car identical, only adding the poster overlay around it.
    """
    brand = (car.get("brand") or "").upper().strip()
    model_name = (car.get("model") or "").upper().strip()
    year = str(car.get("year") or "").strip()
    engine = str(car.get("engine") or "").strip()
    fuel = str(car.get("fuel") or "").strip().capitalize()
    body_type = str(car.get("bodyType") or "").strip().capitalize()
    price_raw = str(car.get("price") or "по запросу").strip()
    price_digits = "".join(c for c in price_raw if c.isdigit())
    price_pretty = f"${int(price_digits):,}".replace(",", " ") if price_digits else price_raw
    title = (brand + " " + model_name).strip() or (car.get("title") or "AUTO").upper()
    sub = " | ".join(p for p in [year, engine] if p) or "Premium auto"

    spec_lines = []
    if year:
        spec_lines.append(f"  • Год: {year}")
    if engine:
        spec_lines.append(f"  • Двигатель: {engine}")
    if fuel:
        spec_lines.append(f"  • Топливо: {fuel}")
    spec_lines.append("  • Коробка: Автомат")
    spec_lines.append("  • Привод: Полный")
    if body_type:
        spec_lines.append(f"  • Кузов: {body_type}")
    spec_block = "\n".join(spec_lines)

    return (
        "TASK: Transform the supplied car photograph into a premium Dubai dealership "
        "vertical poster (portrait 2:3, 1080x1350). Keep the car IDENTICAL — same body, "
        "same headlights, same grille, same wheels, same color, same angle, same plate.\n\n"

        "BACKGROUND TREATMENT:\n"
        "CRITICAL — the car body MUST be BRIGHT, SHARP and FULLY VISIBLE. "
        "Boost exposure and lift shadows so the car paint, wheels and headlights are clearly seen. "
        "Apply studio-grade daylight lighting: crisp details, vivid colors, clean white highlights "
        "on the car body, soft bokeh only on the background (not the car). "
        "Add a dark gradient overlay ONLY at the very top 15% and very bottom 10% of the poster "
        "so text is readable — the central 75% (where the car sits) must stay bright and sharp. "
        "Do NOT darken the car itself under any circumstances.\n\n"

        "OVERLAY (placed over the dimmed gradient areas, never covering the car body):\n\n"

        "TOP-LEFT block:\n"
        f"  Big white bold sans-serif title: {title}\n"
        f"  Gold (#FFD700) thin line under it: {sub}\n"
        f"  Gold pill button with BLACK bold text: СТАРТОВАЯ ЦЕНА  {price_pretty}\n"
        "  Below the pill, four short white bullet lines with small gold check icons:\n"
        "    ✓ Премиум комплектация\n"
        "    ✓ Полный пакет опций\n"
        "    ✓ Под ключ в Бишкек\n"
        "    ✓ Лучшая цена в Дубае\n\n"

        "TOP-RIGHT panel (dark rounded rectangle, semi-transparent black #0F0F12 90%):\n"
        f"{spec_block}\n"
        "  Each row: small gold icon + thin gray label + white bold value.\n\n"

        "MIDDLE-RIGHT badge under the spec panel:\n"
        "  Dark pill with gold flame emoji and two white lines:\n"
        "    🔥 ВЫГОДНОЕ ПРЕДЛОЖЕНИЕ!\n"
        "    ЛУЧШАЯ ЦЕНА НА РЫНКЕ\n\n"

        "BOTTOM CTA bar:\n"
        "  LEFT: small gold play-triangle button + two white lines\n"
        "        ПОЛУЧИТЬ ВИДЕО / Напишите — отправим полный обзор\n"
        "  RIGHT: bright green (#25D366) rounded button with white WhatsApp glyph and bold white text WHATSAPP\n\n"

        "STYLE:\n"
        "• Luxury automotive ad, Dubai dealership aesthetic\n"
        "• Black + gold (#FFD700) palette only, plus the green WhatsApp accent\n"
        "• Bold modern sans-serif typography, perfectly aligned, no spelling errors\n"
        "• Every Cyrillic letter rendered correctly — no Latin lookalikes\n"
        "• No watermarks, no phone numbers, no extra text\n\n"

        "STRICT RULES:\n"
        "• Do NOT redraw, replace or restyle the car. Pixel-preserving edit only.\n"
        "• Do NOT invent extra bullet points or labels beyond those listed.\n"
        "• Do NOT add cartoon, anime, or illustration effects.\n"
        "• Output a single finished poster image."
    )


async def _gemini_flash_image_edit(api_key: str, car: dict, photo_path: Path | str, model: str = None) -> Optional[bytes]:
    """
    Use Gemini Flash image-generation model with the car photo as reference.
    Generates a premium poster while preserving the car's appearance.
    """
    try:
        import httpx as _httpx
    except ImportError:
        log.warning("httpx not installed; skipping Gemini Flash image gen")
        return None

    try:
        # Pre-enhance the photo so dark frames (night shots, garage clips) arrive
        # at Gemini already bright. This prevents Gemini from "preserving" a dark car.
        with open(photo_path, "rb") as f:
            raw_bytes = f.read()
        try:
            from PIL import Image as _PilImage
            import io as _io
            _src = _PilImage.open(_io.BytesIO(raw_bytes)).convert("RGB")
            _bright = _enhance_photo(_src)
            _buf = _io.BytesIO()
            _bright.save(_buf, format="JPEG", quality=90)
            image_b64 = base64.b64encode(_buf.getvalue()).decode()
            log.info("Gemini: pre-enhanced photo before sending (adaptive brightness applied)")
        except Exception as _e:
            log.warning("Gemini: photo pre-enhancement failed (%s), sending raw", _e)
            image_b64 = base64.b64encode(raw_bytes).decode()
    except Exception as e:
        log.warning("Could not read photo for Gemini: %s", e)
        return None

    model = model or GEMINI_FLASH_IMAGE_MODEL
    prompt = _build_gemini_poster_prompt(car)
    reference_b64 = _load_reference_poster_b64()
    url = f"{GEMINI_API_BASE}/models/{model}:generateContent?key={api_key}"

    # Multi-image input: [style reference] + [real car photo] + [text instructions]
    # Nano Banana sees the layout in image 1 and copies it onto the car in image 2.
    parts: list[dict] = []
    if reference_b64:
        parts.append({"text": "Image 1 — STYLE REFERENCE: copy this poster's exact layout, typography, colour palette, and composition. Do NOT copy the car shown in this reference."})
        parts.append({"inline_data": {"mime_type": "image/jpeg", "data": reference_b64}})
        parts.append({"text": "Image 2 — REAL CAR (use this exact car, keep it pixel-perfect):"})
        parts.append({"inline_data": {"mime_type": "image/jpeg", "data": image_b64}})
        parts.append({"text": prompt})
    else:
        parts.append({"text": prompt})
        parts.append({"inline_data": {"mime_type": "image/jpeg", "data": image_b64}})

    payload = {
        "contents": [{"parts": parts}],
        "generationConfig": {
            "responseModalities": ["IMAGE", "TEXT"],
        },
    }

    try:
        async with _httpx.AsyncClient(timeout=AI_TIMEOUT_SECONDS) as client:
            resp = await client.post(url, json=payload)
            if resp.status_code != 200:
                log.warning("Gemini Flash image-edit (%s) returned %s: %s", model, resp.status_code, resp.text[:300])
                return None
            data = resp.json()
    except Exception as e:
        log.warning("Gemini Flash image-edit (%s) request failed: %s", model, e)
        return None

    try:
        for part in data["candidates"][0]["content"]["parts"]:
            inline = part.get("inlineData") or part.get("inline_data")
            if inline and inline.get("data"):
                return base64.b64decode(inline["data"])
        log.warning("Gemini Flash image-edit (%s): no image in response parts", model)
        return None
    except Exception as e:
        log.warning("Gemini Flash image-edit (%s) response parse failed: %s", model, e)
        return None


async def _gemini_flash_text_to_image(api_key: str, car: dict, model: str = None) -> Optional[bytes]:
    """
    Text-to-image fallback using gemini-2.5-flash-image (no reference photo).
    Used when no car photo is available. Imagen 3/4 require paid Vertex AI billing,
    so we use the same Flash image model — works on free Google AI Studio tier.
    """
    try:
        import httpx as _httpx
    except ImportError:
        log.warning("httpx not installed; skipping Gemini text-to-image")
        return None

    model = model or GEMINI_FLASH_IMAGE_MODEL
    prompt = _build_gemini_poster_prompt(car)
    url = f"{GEMINI_API_BASE}/models/{model}:generateContent?key={api_key}"

    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"responseModalities": ["IMAGE", "TEXT"]},
    }

    try:
        async with _httpx.AsyncClient(timeout=AI_TIMEOUT_SECONDS) as client:
            resp = await client.post(url, json=payload)
            if resp.status_code != 200:
                log.warning("Gemini text-to-image (%s) returned %s: %s", model, resp.status_code, resp.text[:300])
                return None
            data = resp.json()
    except Exception as e:
        log.warning("Gemini text-to-image request failed: %s", e)
        return None

    try:
        for part in data["candidates"][0]["content"]["parts"]:
            inline = part.get("inlineData") or part.get("inline_data")
            if inline and inline.get("data"):
                return base64.b64decode(inline["data"])
        log.warning("Gemini text-to-image (%s): no image in response", model)
        return None
    except Exception as e:
        log.warning("Gemini text-to-image response parse failed: %s", e)
        return None


def _build_pollinations_prompt(car: dict) -> str:
    """Build an English prompt for Pollinations.ai (FLUX model)."""
    brand = (car.get("brand") or "").strip()
    model_name = (car.get("model") or "").strip()
    year = str(car.get("year") or "").strip()
    engine = str(car.get("engine") or "").strip()
    fuel = str(car.get("fuel") or "").strip()
    price_raw = str(car.get("price") or "").strip()
    price_digits = "".join(c for c in price_raw if c.isdigit())
    price_pretty = f"${int(price_digits):,}".replace(",", " ") if price_digits else ""

    car_desc = " ".join(p for p in [year, brand, model_name] if p) or "luxury car"
    specs = " ".join(p for p in [engine, fuel] if p)
    price_part = f", price tag {price_pretty}" if price_pretty else ""

    return (
        f"Professional luxury car dealership advertisement vertical poster, "
        f"{car_desc}{(', ' + specs) if specs else ''}{price_part}. "
        f"Dark premium black background, golden yellow accent colors, "
        f"Dubai automotive showroom aesthetic, dramatic studio lighting, "
        f"cinematic car photography, photorealistic, ultra high quality, "
        f"marketing poster design, bold typography overlay areas, "
        f"2:3 portrait format"
    )


async def _generate_with_pollinations(car: dict, main_photo_path: Optional[Path | str]) -> Optional[bytes]:
    """
    Completely FREE image generation via Pollinations.ai (FLUX model).
    No API key required. Falls back gracefully on any error.
    When a real car photo exists, composites the AI-generated background
    with the actual photo using Pillow so the real car stays visible.
    """
    try:
        import httpx as _httpx
    except ImportError:
        log.warning("httpx not installed; skipping Pollinations.ai")
        return None

    prompt = _build_pollinations_prompt(car)
    seed = random.randint(1, 999999)
    encoded = prompt.replace(" ", "%20").replace(",", "%2C").replace(":", "%3A")
    url = (
        f"https://image.pollinations.ai/prompt/{encoded}"
        f"?width={POSTER_W}&height={POSTER_H}&nologo=true&seed={seed}&model=flux"
    )

    log.info("Pollinations.ai: requesting free AI poster...")
    try:
        async with _httpx.AsyncClient(timeout=60.0, follow_redirects=True) as client:
            resp = await client.get(url)
            if resp.status_code != 200:
                log.warning("Pollinations.ai returned %s", resp.status_code)
                return None
            ai_bg_bytes = resp.content
            if len(ai_bg_bytes) < 5000:
                log.warning("Pollinations.ai returned suspiciously small image (%d bytes)", len(ai_bg_bytes))
                return None
    except Exception as e:
        log.warning("Pollinations.ai request failed: %s", e)
        return None

    # If user uploaded a real car photo, composite it over the AI background
    # so the actual car is clearly visible in the center
    has_photo = main_photo_path and Path(main_photo_path).exists()
    if has_photo:
        try:
            from PIL import Image as _PilImage, ImageFilter as _IFilter
            import io as _io

            ai_bg = _PilImage.open(_io.BytesIO(ai_bg_bytes)).convert("RGBA").resize((POSTER_W, POSTER_H))

            # Load and enhance the real car photo
            car_img = _PilImage.open(main_photo_path).convert("RGBA")
            car_img = _enhance_photo(car_img.convert("RGB")).convert("RGBA")

            # Scale car photo to fill 90% of poster width, keep aspect ratio
            cw, ch = car_img.size
            target_w = int(POSTER_W * 0.92)
            target_h = int(target_w * ch / cw)
            if target_h > int(POSTER_H * 0.65):
                target_h = int(POSTER_H * 0.65)
                target_w = int(target_h * cw / ch)
            car_img = car_img.resize((target_w, target_h), _PilImage.LANCZOS)

            # Center the car horizontally, place at vertical center
            x = (POSTER_W - target_w) // 2
            y = int(POSTER_H * 0.18)

            # Blur the AI background slightly so car stands out
            ai_bg = ai_bg.filter(_IFilter.GaussianBlur(radius=3))

            # Composite: paste car photo over blurred AI background
            composite = ai_bg.copy()
            composite.paste(car_img, (x, y), car_img)

            buf = _io.BytesIO()
            composite.convert("RGB").save(buf, format="JPEG", quality=90)
            result_bytes = buf.getvalue()
            log.info("Pollinations.ai: composited real car photo over AI background ✓")
            return result_bytes
        except Exception as e:
            log.warning("Pollinations.ai composite failed (%s); returning raw AI image", e)
            return ai_bg_bytes

    log.info("Pollinations.ai: generated poster (no real photo) ✓")
    return ai_bg_bytes


async def _generate_with_gemini(car: dict, main_photo_path: Optional[Path | str]) -> Optional[bytes]:
    """
    Try Gemini image generation in this order:
      1. gemini-2.5-flash-image (Nano Banana) image-edit with the car photo as reference — best quality
      2. gemini-3.1-flash-image-preview image-edit fallback (separate quota bucket)
      3. gemini-2.5-flash-image text-to-image (no reference) if no photo or all edits failed
    Returns JPEG/PNG bytes or None.
    """
    api_key = os.getenv("GEMINI_API_KEY", "").strip()
    if not api_key:
        log.warning("GEMINI_API_KEY missing; skipping Gemini poster generation")
        return None

    has_photo = main_photo_path and Path(main_photo_path).exists()

    if has_photo:
        log.info("Gemini: trying %s image-edit with reference photo", GEMINI_FLASH_IMAGE_MODEL)
        result = await _gemini_flash_image_edit(api_key, car, main_photo_path, model=GEMINI_FLASH_IMAGE_MODEL)
        if result:
            log.info("Gemini %s image-edit succeeded", GEMINI_FLASH_IMAGE_MODEL)
            return result

        log.info("Gemini: %s failed, trying %s", GEMINI_FLASH_IMAGE_MODEL, GEMINI_FLASH_IMAGE_FALLBACK)
        result = await _gemini_flash_image_edit(api_key, car, main_photo_path, model=GEMINI_FLASH_IMAGE_FALLBACK)
        if result:
            log.info("Gemini %s image-edit succeeded", GEMINI_FLASH_IMAGE_FALLBACK)
            return result

    log.info("Gemini: trying text-to-image (no reference photo)")
    result = await _gemini_flash_text_to_image(api_key, car, model=GEMINI_FLASH_IMAGE_MODEL)
    if result:
        log.info("Gemini text-to-image succeeded")
        return result
    result = await _gemini_flash_text_to_image(api_key, car, model=GEMINI_FLASH_IMAGE_FALLBACK)
    if result:
        log.info("Gemini text-to-image (fallback model) succeeded")
    return result


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

# Backwards-compatible name used by bot.py
def generate_ad_image_prompt(car: dict) -> str:
    """Returns the prompt that would be sent to gpt-image-1 for this car."""
    return _build_ai_prompt(car)


def _poster_mode() -> str:
    """Returns one of: 'ai', 'local', 'auto'. Defaults to 'auto'."""
    raw = os.getenv("POSTER_MODE", "auto").strip().lower()
    if raw in ("ai", "local", "auto"):
        return raw
    # Legacy ENABLE_LOCAL_POSTER toggle: false → ai, true/missing → auto
    legacy = os.getenv("ENABLE_LOCAL_POSTER", "").strip().lower()
    if legacy in ("0", "false", "no"):
        return "ai"
    return "auto"


async def generate_ad_image(car: dict, main_photo_path: Optional[Path | str], template_name: Optional[str] = None) -> Optional[bytes]:
    """Public entry point. Returns JPEG/PNG bytes or None if all backends fail."""
    data, _ = await generate_ad_image_with_template(car, main_photo_path, template_name=template_name)
    return data


async def generate_ad_image_with_template(
    car: dict,
    main_photo_path: Optional[Path | str],
    template_name: Optional[str] = None,
) -> tuple[Optional[bytes], Optional[str]]:
    """
    Generates a poster following POSTER_MODE.

    `template_name` is honored only for the Pillow backend. If template_name is one of the
    local template keys, we use Pillow directly (used by the 🎨 Regenerate button when the
    user wants to pick a specific template). Otherwise we follow POSTER_MODE.

    Returns (bytes, template_used) or (None, None) on total failure.
    """
    # Caller asked for a specific Pillow template → use Pillow directly
    if template_name and template_name in TEMPLATES:
        try:
            data, used = generate_local_poster(car, main_photo_path, template_name=template_name)
            return data, used
        except Exception as e:
            log.warning("Local poster generation failed: %s", e)
            return None, None

    mode = _poster_mode()
    provider = os.getenv("AI_PROVIDER", "auto").strip().lower()

    if mode in ("ai", "auto"):
        ai_bytes = None

        # Gemini path: provider is "gemini" or "auto"
        if provider in ("gemini", "auto"):
            ai_bytes = await _generate_with_gemini(car, main_photo_path)
            if ai_bytes:
                return ai_bytes, AI_GEMINI_TEMPLATE

        # OpenAI path: provider is "openai" or auto-fallback when Gemini failed
        if provider in ("openai", "auto"):
            ai_bytes = await _generate_with_gpt_image(car, main_photo_path)
            if ai_bytes:
                return ai_bytes, AI_POSTER_TEMPLATE

        # Pollinations.ai — FREE fallback, no API key needed
        if provider in ("gemini", "openai", "auto"):
            log.info("Trying Pollinations.ai (free, no API key)...")
            ai_bytes = await _generate_with_pollinations(car, main_photo_path)
            if ai_bytes:
                log.info("Pollinations.ai poster generated successfully ✓")
                return ai_bytes, "ai_pollinations"

        if mode == "ai":
            log.warning("POSTER_MODE=ai but all AI generation failed; returning nothing")
            return None, None
        log.info("All AI poster backends failed; falling back to local Pillow template")

    # mode == "local" OR auto-fallback
    try:
        data, used = generate_local_poster(car, main_photo_path, template_name=template_name)
        return data, used
    except Exception as e:
        log.warning("Local poster generation failed: %s", e)
        return None, None
