"""Marketing pitch generator — hype-style sales copy for car listings.

Two modes:
  generate_pitch(car)         — sync template fallback (always works)
  generate_pitch_ai(car)      — async, uses OpenAI/Anthropic for fresh text;
                                falls back to template if AI fails.

Both return a multiline Russian marketing pitch like:
  🔥 ШОК ЦЕНА! TOYOTA RAV4 2022 ✅ Универсальный — идеально для семьи ...
"""
import os
import random
from datetime import datetime

from config import OPENAI_API_KEY, ANTHROPIC_API_KEY

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

# Year claim — only states the year itself, no fabricated quality claims.
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
        return "🆕 Свежий год выпуска"
    if age <= 3:
        return "✨ Молодой год выпуска"
    return None


# Mileage line — quotes the provided value verbatim. Never claims condition.
def _mileage_phrase(mileage: str) -> str | None:
    if not mileage:
        return None
    digits = "".join(c for c in mileage if c.isdigit())
    if not digits:
        return None
    return f"📊 Пробег: {mileage}"


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


# ---------------------------------------------------------------------------
# AI-powered pitch generation (uses OpenAI/Anthropic). Falls back to templates.
# ---------------------------------------------------------------------------

AI_PITCH_SYSTEM = """Ты — копирайтер автодилера в Дубае. Пишешь рекламные посты для Telegram/WhatsApp на русском.

🚫 ГЛАВНОЕ ПРАВИЛО — НИКОГДА НЕ ВЫДУМЫВАЙ ФАКТЫ.
Используй только те поля, которые я тебе передал. Если поля нет — НЕ упоминай его вообще.

❌ ЗАПРЕЩЕНО (примеры лжи, которые делал предыдущий бот):
- "практически новая" / "почти как новая" / "малый пробег" — если пробег не указан
- "экономия на топливе" / "минимум расхода" — если расход не указан
- "топ комплектация" / "максимальная комплектация" — если комплектация не указана
- "идеальное состояние" / "без аварий" / "родная краска" — если состояние не указано
- "VIP салон" / "белая кожа" — если интерьер не описан
- любые конкретные обещания, которых нет в исходных данных

✅ РАЗРЕШЕНО:
- ОБЩИЕ маркетинговые фразы про бренд/класс ("надёжный SUV", "премиум-седан", "городской хэтчбек") — это не утверждение о КОНКРЕТНОЙ машине
- ТОЧНЫЕ цитаты переданных полей ("Двигатель: 3.0 Twin Turbo" если так передано)
- Эмодзи 🔥⚡✅💰💎⏰ (≤ 8 на пост)
- Призыв к действию

ФОРМАТ ВЫВОДА:
1. Хук-строка: "🔥 ШОК ЦЕНА!" / "⚡ УСПЕЙ!" / "💎 ЭКСКЛЮЗИВ" — на выбор
2. Пустая строка
3. <b>МАРКА МОДЕЛЬ ГОД</b> в HTML
4. Пустая строка
5. 2-3 строки ✅ — общие фразы про класс машины (НЕ конкретные факты)
6. 🔧 Двигатель: <как в данных> — ТОЛЬКО если поле есть
7. ⛽ Топливо: <как в данных> — ТОЛЬКО если поле есть
8. 📊 Пробег: <как в данных> — ТОЛЬКО если поле есть
9. 📍 Локация — ТОЛЬКО если поле передано (не пиши Dubai/UAE если не передано)
10. Пустая строка
11. 💰 Цена — ТОЛЬКО если передана
12. Пустая строка
13. Призыв к действию

❗ Если поле в данных пустое — пропускай строку полностью. Лучше короткий честный пост, чем длинный с враньём.

Без кавычек вокруг ответа. Без markdown ``` ```. Только готовый HTML-текст для Telegram."""


async def _generate_pitch_openai(car: dict) -> str | None:
    if not OPENAI_API_KEY:
        return None
    try:
        from openai import AsyncOpenAI
        client = AsyncOpenAI(api_key=OPENAI_API_KEY)
        prompt = _format_car_for_ai(car)
        resp = await client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": AI_PITCH_SYSTEM},
                {"role": "user", "content": prompt},
            ],
            temperature=0.85,  # creative variation between regens
            max_tokens=500,
        )
        text = (resp.choices[0].message.content or "").strip()
        return text or None
    except Exception as e:
        print(f"[marketing] OpenAI pitch failed: {e}")
        return None


async def _generate_pitch_anthropic(car: dict) -> str | None:
    if not ANTHROPIC_API_KEY:
        return None
    try:
        from anthropic import AsyncAnthropic
        client = AsyncAnthropic(api_key=ANTHROPIC_API_KEY)
        prompt = _format_car_for_ai(car)
        resp = await client.messages.create(
            model="claude-haiku-4-5",
            max_tokens=500,
            temperature=0.85,
            system=AI_PITCH_SYSTEM,
            messages=[{"role": "user", "content": prompt}],
        )
        text = resp.content[0].text.strip() if resp.content else ""
        return text or None
    except Exception as e:
        print(f"[marketing] Anthropic pitch failed: {e}")
        return None


def _format_car_for_ai(car: dict) -> str:
    lines = ["Машина для рекламного поста:"]
    fields = [
        ("Бренд", "brand"),
        ("Модель", "model"),
        ("Год", "year"),
        ("Двигатель", "engine"),
        ("Топливо", "fuel"),
        ("Кузов", "bodyType"),
        ("Цена", "price"),
        ("Пробег", "mileage"),
        ("Локация", "location"),
    ]
    for label, key in fields:
        val = car.get(key)
        if val:
            lines.append(f"{label}: {val}")
    return "\n".join(lines)


async def generate_pitch_ai(car: dict) -> str:
    """
    Generate marketing pitch using AI (Anthropic preferred, OpenAI fallback,
    template fallback if both fail or no keys set).
    """
    # Try Anthropic first if available
    if ANTHROPIC_API_KEY:
        text = await _generate_pitch_anthropic(car)
        if text:
            return text
    # Then OpenAI
    if OPENAI_API_KEY:
        text = await _generate_pitch_openai(car)
        if text:
            return text
    # Fallback: deterministic template
    return generate_pitch(car)
