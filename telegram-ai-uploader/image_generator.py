"""100% local ad-poster generator for car listings.

No OpenAI / no DALL-E / no paid APIs — uses only Pillow.

5 templates with smart selection based on car attributes:
- aggressive_black_yellow → SUV / Land Cruiser / RAV4 / default
- red_price_blast        → cheap price (< $15k)
- luxury_dark_gold       → BMW / Mercedes / Lexus / Porsche / Audi
- clean_white_premium    → Toyota / Honda / mid-tier sedans
- hybrid_green_energy    → fuel = Гибрид / Электро

Output: vertical 1080x1350 (or POSTER_SIZE from env) JPEG bytes.
"""
import io
import os
import random
from pathlib import Path
from typing import Optional

from PIL import Image, ImageDraw, ImageFilter, ImageFont

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
        return Image.open(p).convert("RGB")
    except Exception:
        return None


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

    # Brand + Model (huge, near photo bottom)
    brand = (car.get("brand") or "").upper()
    model = (car.get("model") or "").upper()
    title_text = (brand + " " + model).strip() or (car.get("title") or "AUTO").upper()
    title_font = _load_font("narrow", 110)
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


def pick_different_template(car: dict, current: Optional[str]) -> str:
    """For 🎨 Regenerate — picks a different template each time."""
    all_keys = list(TEMPLATES.keys())
    smart_choice = select_template(car)
    pool = [k for k in all_keys if k != current]
    if not pool:
        return smart_choice
    # Bias toward smart_choice on first generation, otherwise random
    if current is None:
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


# Backwards-compatible names used by bot.py
def generate_ad_image_prompt(car: dict) -> str:
    """Kept for API stability — used by the (disabled) AI image hook."""
    return f"Automotive Instagram poster for {car.get('title','car')}"


async def generate_ad_image(car: dict, main_photo_path: Optional[Path | str], template_name: Optional[str] = None) -> Optional[bytes]:
    """
    Public entry point. ALWAYS uses local poster generator (no paid API).
    `template_name` lets the caller force a specific template (used by 🎨 Regenerate).
    """
    enabled = os.getenv("ENABLE_LOCAL_POSTER", "true").lower() in ("1", "true", "yes")
    if not enabled:
        return None
    try:
        data, _used = generate_local_poster(car, main_photo_path, template_name=template_name)
        return data
    except Exception as e:
        print(f"[image_generator] generation failed: {e}")
        return None


async def generate_ad_image_with_template(car: dict, main_photo_path: Optional[Path | str], template_name: Optional[str] = None) -> tuple[Optional[bytes], Optional[str]]:
    """Variant that also returns the template name actually used."""
    enabled = os.getenv("ENABLE_LOCAL_POSTER", "true").lower() in ("1", "true", "yes")
    if not enabled:
        return None, None
    try:
        data, used = generate_local_poster(car, main_photo_path, template_name=template_name)
        return data, used
    except Exception as e:
        print(f"[image_generator] generation failed: {e}")
        return None, None
