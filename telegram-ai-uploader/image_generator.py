"""Ad-poster generator for car listings.

Two backends, picked via POSTER_MODE env var:
  - "ai"     → OpenAI gpt-image-1 only (fails loudly if API broken)
  - "local"  → Pillow templates only (no paid API)
  - "auto"   → AI first, Pillow fallback if AI fails (default)

Pillow fallback uses 5 templates with smart selection based on car attributes:
- aggressive_black_yellow → SUV / Land Cruiser / RAV4 / default
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


def _enhance_photo(img: Image.Image) -> Image.Image:
    """
    Light, lossless-ish enhancement before placing photo on the poster.
    - Auto-contrast to fix flat washed-out frames from video
    - Slight saturation boost so paint colour pops
    - Mild unsharp mask for crisper edges (helps low-res Telegram frames)
    Wrapped in try/except so a corrupt image still renders.
    """
    try:
        img = ImageOps.autocontrast(img, cutoff=2)
    except Exception:
        pass
    try:
        img = ImageEnhance.Color(img).enhance(1.12)
    except Exception:
        pass
    try:
        img = img.filter(ImageFilter.UnsharpMask(radius=1.2, percent=120, threshold=3))
    except Exception:
        pass
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


def _draw_spec_row(draw, x: int, y: int, label: str, value: str, font_label, font_value, accent: tuple):
    """A spec row: thick yellow vertical accent bar + label (grey) + value (white)."""
    bar_w = 5
    bar_h = 48
    draw.rectangle([x, y + 6, x + bar_w, y + 6 + bar_h], fill=accent)
    label_x = x + bar_w + 18
    draw.text((label_x, y + 6), label, font=font_label, fill=(200, 200, 200))
    draw.text((label_x, y + 30), value, font=font_value, fill=(255, 255, 255))


def _photo_background(photo: Optional[Image.Image], W: int, H: int) -> Image.Image:
    """Photo as full-bleed background with cinematic enhancement + dark gradient masks."""
    if photo is None:
        bg = _gradient(W, H, (20, 20, 22), (5, 5, 7))
    else:
        bg = _cover_resize(_enhance_photo(photo), W, H)
        # Boost contrast and saturation a notch beyond _enhance_photo
        try:
            bg = ImageEnhance.Contrast(bg).enhance(1.08)
            bg = ImageEnhance.Color(bg).enhance(1.06)
        except Exception:
            pass

    # Top-left + top-right dark gradient masks so overlays are readable
    overlay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay, "RGBA")
    # Left vertical gradient (for headline+pill+bullets)
    for x in range(W // 2):
        alpha = int(220 * (1 - x / (W // 2)))
        draw.line([(x, 0), (x, H)], fill=(0, 0, 0, alpha))
    # Right top gradient (for spec panel area) — softer
    for y in range(H // 2):
        alpha = int(120 * (1 - y / (H // 2)))
        draw.line([(W // 2, y), (W, y)], fill=(0, 0, 0, alpha))
    # Bottom gradient for CTA legibility
    for y in range(H // 3):
        alpha = int(160 * (y / (H // 3)))
        draw.line([(0, H - H // 3 + y), (W, H - H // 3 + y)], fill=(0, 0, 0, alpha))

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

    # Year | Engine subline
    sub_parts = [p for p in [year, engine] if p]
    if sub_parts:
        sub_text = " | ".join(sub_parts)
        sub_font = _load_font("bold", 60)
        _shadow_text(draw, (40, 50 + headline_font.size + 6), sub_text, sub_font, accent, offset=2)

    # СТАРТ pill
    pill_text = f"СТАРТ: {price_pretty}"
    pill_font = _load_font("bold", 32)
    pw = _text_w(draw, pill_text, pill_font)
    pill_y = 50 + headline_font.size + 80
    pill_box = (40, pill_y, 40 + pw + 60, pill_y + pill_font.size + 28)
    _rounded_rect(draw, pill_box, radius=14, fill=accent)
    draw.text((40 + 30, pill_y + 13), pill_text, font=pill_font, fill=(15, 15, 15))

    # ===== Left: bullet list =====
    bullet_font = _load_font("bold", 24)
    bullets = [
        "Премиальный комфорт",
        f"Динамичный мотор{(' ' + engine) if engine else ''}",
        "Идеален для города и трассы",
        "Стильный, динамичный, надёжный",
    ]
    bullet_y = pill_y + pill_font.size + 70
    for line in bullets:
        line = _truncate(draw, line, bullet_font, int(W * 0.5))
        _draw_check_bullet(draw, 40, bullet_y, line, bullet_font, accent)
        bullet_y += 50

    # ===== Top-right: spec panel =====
    panel_x = int(W * 0.58)
    panel_y = 50
    panel_w = W - panel_x - 40
    panel_pad = 24

    specs = []
    if year:
        specs.append(("Год", year))
    if engine:
        specs.append(("Объём", engine))
    if fuel:
        specs.append(("Топливо", fuel))
    specs.append(("Коробка", "Автомат"))
    specs.append(("Привод", "Задний"))
    if body_type:
        specs.append(("Кузов", body_type))

    row_h = 64
    panel_h = panel_pad * 2 + row_h * len(specs)
    _rounded_rect(draw, (panel_x, panel_y, panel_x + panel_w, panel_y + panel_h),
                  radius=22, fill=(15, 15, 18, 215))

    label_font = _load_font("regular", 18)
    value_font = _load_font("bold", 24)
    for i, (label, value) in enumerate(specs):
        _draw_spec_row(draw, panel_x + panel_pad, panel_y + panel_pad + i * row_h,
                       label, value, label_font, value_font, accent)

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

    # ===== Bottom-left: ВИДЕО ПО ЗАПРОСУ CTA =====
    cta_y = H - 200
    cta_pill_text = "ВИДЕО ПО ЗАПРОСУ"
    cta_pill_font = _load_font("bold", 26)
    cw = _text_w(draw, cta_pill_text, cta_pill_font) + 50
    cta_pill_box = (40, cta_y, 40 + cw, cta_y + cta_pill_font.size + 22)
    _rounded_rect(draw, cta_pill_box, radius=14, fill=accent)
    draw.text((40 + 25, cta_y + 11), cta_pill_text, font=cta_pill_font, fill=(15, 15, 15))

    cta_sub_font = _load_font("regular", 22)
    draw.text((40, cta_y + cta_pill_font.size + 38),
              "Напишите — отправим", font=cta_sub_font, fill=(235, 235, 235))
    draw.text((40, cta_y + cta_pill_font.size + 66),
              "подробное видео автомобиля", font=cta_sub_font, fill=(235, 235, 235))

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
AI_TIMEOUT_SECONDS = 90.0
AI_QUALITY = os.getenv("OPENAI_IMAGE_QUALITY", "medium")  # low / medium / high
AI_SIZE = os.getenv("OPENAI_IMAGE_SIZE", "1024x1536")     # vertical phone format


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

    if mode in ("ai", "auto"):
        ai_bytes = await _generate_with_gpt_image(car, main_photo_path)
        if ai_bytes:
            return ai_bytes, AI_POSTER_TEMPLATE
        if mode == "ai":
            log.warning("POSTER_MODE=ai but AI generation failed; returning nothing")
            return None, None
        log.info("AI poster generation failed; falling back to local Pillow template")

    # mode == "local" OR auto-fallback
    try:
        data, used = generate_local_poster(car, main_photo_path, template_name=template_name)
        return data, used
    except Exception as e:
        log.warning("Local poster generation failed: %s", e)
        return None, None
