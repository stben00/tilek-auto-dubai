"""Marketing pitch generator — hype-style sales copy for car listings.

Takes a parsed car dict and produces an attention-grabbing description
in the style of "ТАКИЕ МАШИНЫ НЕ ЗАДЕРЖИВАЮТСЯ! УСПЕЙ ПЕРВЫМ! 🔥"
"""
import random
from datetime import datetime

# Headline hooks (top line)
HOOKS = [
    "🔥 ШОК ЦЕНА!",
    "⚡ ОТ ВЕРНЫХ РУК!",
    "🚨 ТОЛЬКО СЕГОДНЯ!",
    "💥 МЕГА ВЫГОДА!",
    "⭐ ЛУЧШЕЕ ПРЕДЛОЖЕНИЕ ДНЯ!",
    "🎯 ЭТО ТО ЧТО ВЫ ИСКАЛИ!",
    "🏆 ЭКСКЛЮЗИВНЫЙ ВАРИАНТ!",
    "💎 РЕДКИЙ ЭКЗЕМПЛЯР!",
]

# Closing urgency lines
URGENCY = [
    "Такие машины НЕ задерживаются — успей первым! 🔥",
    "Этот вариант разлетится за день — пиши сейчас! 📲",
    "На рынке таких единицы — забронируй пока есть! ⚡",
    "Один звонок — и машина твоя! 💪",
    "Дубай ждёт — успей оформить сегодня! ✈️",
    "Завтра уже может быть продана! ⏰",
    "Доставка под ключ — звони сейчас! 🚚",
]

# Body-type personality
BODY_VIBES = {
    "Внедорожник": ["НАДЁЖНЫЙ", "МОЩНЫЙ", "ГОТОВ К ЛЮБЫМ ДОРОГАМ"],
    "Кроссовер": ["УНИВЕРСАЛЬНЫЙ", "СТИЛЬНЫЙ", "ИДЕАЛЬНЫЙ ДЛЯ СЕМЬИ"],
    "Седан": ["ЭЛЕГАНТНЫЙ", "КОМФОРТНЫЙ", "БИЗНЕС-КЛАСС"],
    "Купе": ["СПОРТИВНЫЙ", "АГРЕССИВНЫЙ", "ХАРИЗМАТИЧНЫЙ"],
    "Пикап": ["МОЩНЫЙ", "БРУТАЛЬНЫЙ", "БЕЗ ГРАНИЦ"],
    "Минивэн": ["ПРОСТОРНЫЙ", "СЕМЕЙНЫЙ", "ВСЁ ВЛЕЗАЕТ"],
    "Хэтчбек": ["КОМПАКТНЫЙ", "ШУСТРЫЙ", "ГОРОДСКОЙ"],
}

# Fuel-type benefits
FUEL_BENEFITS = {
    "Гибрид": "⚡ ГИБРИД — экономия каждый день, минимум на бензин 💚",
    "Электро": "🔌 ЭЛЕКТРОМОБИЛЬ — НОЛЬ на топливо, тихий ход, будущее уже здесь",
    "Дизель": "💪 ДИЗЕЛЬ — мощь и низкий расход на трассе",
    "Бензин": "⛽ Бензин — проверенная классика, заправляется везде",
    "Газ": "🟢 ГАЗ — самое экономичное топливо",
}

# Year-quality phrase
def _year_phrase(year: str) -> str | None:
    if not year:
        return None
    try:
        y = int(year)
    except ValueError:
        return None
    current = datetime.now().year
    age = current - y
    if age <= 1:
        return "🆕 ПРАКТИЧЕСКИ НОВАЯ — свежий год"
    if age <= 3:
        return "✨ СВЕЖАЯ — отличное состояние"
    if age <= 5:
        return "✅ Молодая машина, прошла мало"
    return None


# Mileage phrase — extract number
def _mileage_phrase(mileage: str) -> str | None:
    if not mileage:
        return None
    digits = "".join(c for c in mileage if c.isdigit())
    if not digits:
        return None
    try:
        km = int(digits)
    except ValueError:
        return None
    if km < 30000:
        return f"📉 ПРОБЕГ ВСЕГО {mileage} — почти как новая!"
    if km < 70000:
        return f"📊 Пробег {mileage} — отличный для своих лет"
    if km < 150000:
        return f"📏 Пробег {mileage} — обкатанная и надёжная"
    return None


# Price hype
def _price_phrase(price: str) -> str | None:
    if not price:
        return None
    digits = "".join(c for c in price if c.isdigit())
    if not digits:
        return None
    try:
        p = int(digits)
    except ValueError:
        return None
    if p < 10000:
        return f"💰 ВСЕГО {price} — поверить не можем сами!"
    if p < 20000:
        return f"💵 {price} — золотая цена за такое авто!"
    if p < 40000:
        return f"💸 {price} — отличный вариант, торг возможен"
    return f"💎 Премиум-класс за {price}"


def generate_pitch(car: dict) -> str:
    """
    Generate hype-style marketing description for a car.
    `car` is a dict with title/brand/model/year/engine/fuel/bodyType/price/mileage/location/whatsapp.
    Returns a multiline Russian marketing pitch.
    """
    brand = car.get("brand", "").strip()
    model = car.get("model", "").strip()
    year = car.get("year", "").strip()
    engine = car.get("engine", "").strip()
    fuel = car.get("fuel", "").strip()
    body = car.get("bodyType", "").strip()
    price = car.get("price", "").strip()
    mileage = car.get("mileage", "").strip()
    location = car.get("location", "Dubai / UAE").strip()
    title = car.get("title") or f"{brand} {model} {year}".strip()

    parts = []

    # 1. Hook
    parts.append(random.choice(HOOKS))
    parts.append(f"<b>{title.upper()}</b>")
    parts.append("")

    # 2. Tagline from body type
    if body in BODY_VIBES:
        adjectives = random.sample(BODY_VIBES[body], k=min(2, len(BODY_VIBES[body])))
        parts.append("✅ " + " · ".join(adjectives))
    elif body:
        parts.append(f"✅ {body.upper()}")

    # 3. Year quality
    yp = _year_phrase(year)
    if yp:
        parts.append(yp)

    # 4. Engine + fuel
    eng_parts = []
    if engine:
        eng_parts.append(f"🔧 Двигатель: {engine}")
    if fuel:
        benefit = FUEL_BENEFITS.get(fuel)
        if benefit:
            eng_parts.append(benefit)
        else:
            eng_parts.append(f"⛽ Топливо: {fuel}")
    parts.extend(eng_parts)

    # 5. Mileage
    mp = _mileage_phrase(mileage)
    if mp:
        parts.append(mp)

    # 6. Location
    parts.append(f"📍 {location}")

    # 7. Price
    if price:
        parts.append("")
        pp = _price_phrase(price)
        parts.append(pp if pp else f"💰 ЦЕНА: {price}")

    # 8. Urgency closer
    parts.append("")
    parts.append(random.choice(URGENCY))

    return "\n".join(parts)


def generate_whatsapp_share(car: dict, site_url: str = "") -> str:
    """
    Plain-text version (no HTML) for copy-paste to WhatsApp groups.
    Same content as generate_pitch but with WhatsApp-friendly formatting.
    """
    brand = car.get("brand", "").strip()
    model = car.get("model", "").strip()
    year = car.get("year", "").strip()
    engine = car.get("engine", "").strip()
    fuel = car.get("fuel", "").strip()
    body = car.get("bodyType", "").strip()
    price = car.get("price", "").strip()
    mileage = car.get("mileage", "").strip()
    whatsapp = car.get("whatsapp", "").strip()
    title = car.get("title") or f"{brand} {model} {year}".strip()

    lines = []
    lines.append(random.choice(HOOKS))
    lines.append(f"*{title.upper()}*")  # WhatsApp uses *bold*
    lines.append("")

    if body in BODY_VIBES:
        adjectives = random.sample(BODY_VIBES[body], k=min(2, len(BODY_VIBES[body])))
        lines.append("✅ " + " · ".join(adjectives))

    yp = _year_phrase(year)
    if yp: lines.append(yp)

    if engine:
        lines.append(f"🔧 Двигатель: {engine}")
    if fuel:
        b = FUEL_BENEFITS.get(fuel)
        if b:
            lines.append(b)

    mp = _mileage_phrase(mileage)
    if mp: lines.append(mp)

    lines.append("📍 Dubai / UAE")

    if price:
        lines.append("")
        pp = _price_phrase(price)
        lines.append(pp if pp else f"💰 ЦЕНА: {price}")

    lines.append("")
    lines.append(random.choice(URGENCY))

    if whatsapp:
        lines.append(f"📲 WhatsApp: {whatsapp}")
    if site_url:
        lines.append(f"🌐 Полная карточка: {site_url}")

    return "\n".join(lines)
