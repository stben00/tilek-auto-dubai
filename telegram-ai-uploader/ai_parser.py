"""AI-based parser using Anthropic or OpenAI. Falls back to regex parser."""
import json
import logging
import re
from config import ANTHROPIC_API_KEY, OPENAI_API_KEY, HAS_AI
from parser import parse_car_text

log = logging.getLogger(__name__)

AI_TIMEOUT_SECONDS = 20.0

SYSTEM_PROMPT = """You are a strict JSON extractor for a used-car dealership in Dubai.
Convert messy Instagram/WhatsApp/Telegram car descriptions (Russian, English, Kyrgyz, mixed) into ONE JSON object with EXACTLY these keys:

{
  "title": "",
  "brand": "",
  "model": "",
  "year": "",
  "engine": "",
  "fuel": "",
  "bodyType": "",
  "price": "",
  "mileage": "",
  "location": "",
  "description": "",
  "whatsapp": "",
  "instagramUrl": "",
  "videoUrl": "",
  "status": "available"
}

Rules:
- brand in English (Toyota, Lexus, Mercedes, BMW, Audi, Nissan, Hyundai, Kia, Honda, Mitsubishi, Mazda, Land Rover, Range Rover, Jeep, Volvo, MAN, Scania, DAF, Ford, Chevrolet, Porsche, Tesla, Infiniti, Cadillac, GMC, Subaru)
- model as commonly written (Land Cruiser, Highlander, RX350, LX570, Wrangler, ...)
- year 4-digit string
- price formatted like "$15 500" (dollar sign, space thousands)
- fuel Russian: Бензин / Дизель / Гибрид / Электро / Газ
- bodyType Russian: Внедорожник / Кроссовер / Седан / Пикап / Грузовик / Минивэн / Купе / Хэтчбек
- engine concise like "2.4 Turbo 4WD" or "3.5 V6"
- mileage like "45 000 км" (or "" if unknown)
- location like "Dubai / UAE"
- whatsapp digits only, with + if international (e.g. "+971551234567" or "0551333360")
- instagramUrl: any instagram.com or reel link found
- videoUrl: youtube/tiktok/direct video link found (NOT instagram - that goes to instagramUrl)
- description: short clean human description in original language (max 300 chars)
- title like "Toyota Highlander 2021"
- If a field is unknown, use empty string ""
- Output ONLY the JSON object, no markdown, no commentary."""


def _extract_json(text: str) -> dict | None:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if m:
            try:
                return json.loads(m.group(0))
            except json.JSONDecodeError:
                return None
    return None


async def _anthropic_parse(text: str) -> dict | None:
    try:
        from anthropic import AsyncAnthropic
        client = AsyncAnthropic(api_key=ANTHROPIC_API_KEY, timeout=AI_TIMEOUT_SECONDS)
        resp = await client.messages.create(
            model="claude-sonnet-4-5",
            max_tokens=1024,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": text}],
        )
        content = resp.content[0].text if resp.content else ""
        parsed = _extract_json(content)
        if parsed is None:
            log.warning("Anthropic returned non-JSON content (len=%d)", len(content))
        return parsed
    except Exception as e:
        log.warning("Anthropic call failed: %s", e)
        return None


async def _openai_parse(text: str) -> dict | None:
    try:
        from openai import AsyncOpenAI
        client = AsyncOpenAI(api_key=OPENAI_API_KEY, timeout=AI_TIMEOUT_SECONDS)
        resp = await client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": text},
            ],
            response_format={"type": "json_object"},
            temperature=0.1,
        )
        content = resp.choices[0].message.content or ""
        parsed = _extract_json(content)
        if parsed is None:
            log.warning("OpenAI returned non-JSON content (len=%d)", len(content))
        return parsed
    except Exception as e:
        log.warning("OpenAI call failed: %s", e)
        return None


REQUIRED_KEYS = [
    "title", "brand", "model", "year", "engine", "fuel", "bodyType", "price",
    "mileage", "location", "description", "whatsapp", "instagramUrl", "videoUrl", "status",
]


def _normalize(data: dict) -> dict:
    out = {k: "" for k in REQUIRED_KEYS}
    out["status"] = "available"
    for k in REQUIRED_KEYS:
        v = data.get(k, "")
        if v is None:
            v = ""
        out[k] = str(v).strip()
    if not out["status"]:
        out["status"] = "available"
    return out


def _merge(ai: dict, regex: dict) -> dict:
    """Fill empty AI fields with regex parser results."""
    out = dict(ai)
    for k in REQUIRED_KEYS:
        if not out.get(k):
            out[k] = regex.get(k, "")
    if not out.get("title") and (out.get("brand") or out.get("model")):
        parts = [p for p in [out.get("brand"), out.get("model"), out.get("year")] if p]
        out["title"] = " ".join(parts)
    return out


async def parse_with_ai(text: str) -> dict:
    """Parse car text with AI if available, fallback to regex. Always returns a complete dict."""
    regex_result = parse_car_text(text)
    if not HAS_AI:
        return regex_result
    ai_raw = None
    if ANTHROPIC_API_KEY:
        ai_raw = await _anthropic_parse(text)
    if ai_raw is None and OPENAI_API_KEY:
        ai_raw = await _openai_parse(text)
    if ai_raw is None:
        log.warning("AI parsing failed for input (len=%d), falling back to regex", len(text))
        return regex_result
    return _merge(_normalize(ai_raw), regex_result)
