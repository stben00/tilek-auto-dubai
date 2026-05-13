"""Regex fallback parser for car data from messy text."""
import re

BRAND_MAP = {
    # English (canonical)
    "toyota": "Toyota", "lexus": "Lexus", "mercedes": "Mercedes", "mercedes-benz": "Mercedes",
    "bmw": "BMW", "audi": "Audi", "nissan": "Nissan", "hyundai": "Hyundai", "kia": "Kia",
    "honda": "Honda", "mitsubishi": "Mitsubishi", "mazda": "Mazda", "land rover": "Land Rover",
    "range rover": "Range Rover", "jeep": "Jeep", "volvo": "Volvo", "man": "MAN",
    "scania": "Scania", "daf": "DAF", "ford": "Ford", "chevrolet": "Chevrolet",
    "porsche": "Porsche", "tesla": "Tesla", "infiniti": "Infiniti", "cadillac": "Cadillac",
    "gmc": "GMC", "dodge": "Dodge", "subaru": "Subaru", "suzuki": "Suzuki",
    # Common English typos / variants
    "hundai": "Hyundai", "hunday": "Hyundai", "huynday": "Hyundai", "hyunday": "Hyundai",
    "huyndai": "Hyundai", "hyundia": "Hyundai", "tayota": "Toyota", "toyoda": "Toyota",
    "merc": "Mercedes", "mers": "Mercedes", "benz": "Mercedes",
    "rangerover": "Range Rover", "landrover": "Land Rover",
    # Russian
    "тойота": "Toyota", "тоета": "Toyota",
    "лексус": "Lexus", "лехус": "Lexus",
    "мерседес": "Mercedes", "мерс": "Mercedes",
    "бмв": "BMW",
    "ауди": "Audi", "аудю": "Audi",
    "ниссан": "Nissan",
    "хонда": "Honda",
    "хюндай": "Hyundai", "хундай": "Hyundai", "хёндай": "Hyundai", "хендай": "Hyundai",
    "хундэй": "Hyundai", "хюндэй": "Hyundai",
    "киа": "Kia", "кия": "Kia",
    "митсубиси": "Mitsubishi", "митсубиши": "Mitsubishi", "мицубиси": "Mitsubishi",
    "мазда": "Mazda",
    "вольво": "Volvo",
    "ман": "MAN", "скания": "Scania",
    "форд": "Ford",
    "шевроле": "Chevrolet", "шевролет": "Chevrolet",
    "порше": "Porsche", "поршэ": "Porsche",
    "тесла": "Tesla",
    "ленд ровер": "Land Rover", "лендровер": "Land Rover",
    "рендж ровер": "Range Rover", "ренж ровер": "Range Rover", "рейндж ровер": "Range Rover",
    "ренджровер": "Range Rover", "рейнджровер": "Range Rover",
    "джип": "Jeep",
    "инфинити": "Infiniti",
    "кадиллак": "Cadillac",
    "гмс": "GMC",
    "субару": "Subaru",
    "сузуки": "Suzuki",
}

MODEL_HINTS = [
    "Land Cruiser", "Highlander", "Camry", "Corolla", "Prado", "RAV4", "Hilux", "Tundra",
    "RX350", "RX450", "LX570", "LX600", "GX460", "ES350", "NX300", "LS500",
    "Wrangler", "Grand Cherokee", "Cherokee", "Compass",
    "Range Rover", "Discovery", "Defender", "Velar", "Evoque", "Sport",
    "X5", "X6", "X7", "M5", "M3", "528", "535", "740",
    "S-Class", "E-Class", "C-Class", "GLE", "GLS", "G63", "G500", "AMG",
    "A4", "A6", "A8", "Q7", "Q8", "Q5",
    "Patrol", "Pathfinder", "X-Trail", "Murano", "Altima",
    "Sonata", "Tucson", "Santa Fe", "Palisade", "Elantra",
    "Sportage", "Sorento", "K5", "Telluride",
    "Pilot", "CR-V", "Accord", "Civic", "Odyssey",
    "Pajero", "Outlander", "Eclipse", "L200",
    "CX-5", "CX-9", "Mazda6", "Mazda3",
    "Cayenne", "Macan", "Panamera", "911",
    "Model S", "Model 3", "Model X", "Model Y",
    "F-150", "Mustang", "Explorer", "Escape",
    "Silverado", "Tahoe", "Suburban", "Camaro",
    "TGX", "TGS", "TGM",
    "QX60", "QX80", "Q50",
    "Escalade",
]

# Common typos / variants → canonical model
MODEL_TYPOS = {
    "tukson": "Tucson", "tuskon": "Tucson", "tucsan": "Tucson",
    "тусан": "Tucson", "тукcон": "Tucson", "туксон": "Tucson", "тусон": "Tucson",
    "хайландер": "Highlander", "хайлендер": "Highlander", "хайлайндер": "Highlander",
    "ленд крузер": "Land Cruiser", "лэнд крузер": "Land Cruiser", "ландкрузер": "Land Cruiser",
    "крузер": "Land Cruiser",
    "прадо": "Prado", "прада": "Prado",
    "камри": "Camry", "королла": "Corolla", "коралла": "Corolla",
    "рав4": "RAV4", "рав 4": "RAV4",
    "хайлюкс": "Hilux", "хилюкс": "Hilux",
    "вранглер": "Wrangler", "ренглер": "Wrangler", "рангелер": "Wrangler",
    "патрол": "Patrol", "патруль": "Patrol",
    "соната": "Sonata", "санта фе": "Santa Fe", "санта-фе": "Santa Fe",
    "паджеро": "Pajero", "поджеро": "Pajero",
    "кайен": "Cayenne", "кайенн": "Cayenne", "каен": "Cayenne",
    "макан": "Macan", "панамера": "Panamera",
    "мустанг": "Mustang",
    "акорд": "Accord", "аккорд": "Accord", "сивик": "Civic", "цивик": "Civic",
    "тахо": "Tahoe", "субурбан": "Suburban",
    "эскалейд": "Escalade", "эскалад": "Escalade",
    "пилот": "Pilot",
    "соренто": "Sorento", "спортейдж": "Sportage", "спортэйдж": "Sportage",
}

SUV_MODELS = {
    "land cruiser", "highlander", "prado", "rav4", "rx350", "rx450", "lx570", "lx600",
    "gx460", "wrangler", "grand cherokee", "cherokee", "compass", "range rover",
    "discovery", "defender", "velar", "evoque", "x5", "x6", "x7", "gle", "gls", "g63",
    "g500", "q7", "q8", "q5", "patrol", "pathfinder", "x-trail", "tucson", "santa fe",
    "palisade", "sportage", "sorento", "telluride", "pilot", "cr-v", "pajero",
    "outlander", "cx-5", "cx-9", "cayenne", "macan", "model x", "model y",
    "explorer", "escape", "tahoe", "suburban", "qx60", "qx80", "escalade",
}

FUEL_MAP = {
    "бензин": "Бензин", "petrol": "Бензин", "gasoline": "Бензин", "gas": "Бензин",
    "дизель": "Дизель", "diesel": "Дизель",
    "гибрид": "Гибрид", "hybrid": "Гибрид",
    "электро": "Электро", "electric": "Электро", "ev": "Электро",
    "газ": "Газ", "lpg": "Газ", "cng": "Газ",
}

LOCATION_MAP = {
    "dubai": "Dubai / UAE", "дубай": "Dubai / UAE", "дубаи": "Dubai / UAE",
    "uae": "Dubai / UAE", "оаэ": "Dubai / UAE",
    "sharjah": "Sharjah / UAE", "шарджа": "Sharjah / UAE",
    "abu dhabi": "Abu Dhabi / UAE", "абу даби": "Abu Dhabi / UAE", "абу-даби": "Abu Dhabi / UAE",
    "ajman": "Ajman / UAE", "аджман": "Ajman / UAE",
}


def _detect_brand(text_low: str):
    sorted_brands = sorted(BRAND_MAP.keys(), key=len, reverse=True)
    for key in sorted_brands:
        if re.search(rf"\b{re.escape(key)}\b", text_low):
            return BRAND_MAP[key]
    return ""


def _detect_model(text: str):
    text_low = text.lower()
    # First: typo dictionary
    for k in sorted(MODEL_TYPOS.keys(), key=len, reverse=True):
        if re.search(rf"\b{re.escape(k)}\b", text_low):
            return MODEL_TYPOS[k]
    # Then: canonical models
    for m in sorted(MODEL_HINTS, key=len, reverse=True):
        if re.search(rf"\b{re.escape(m)}\b", text, re.IGNORECASE):
            return m
    return ""


def _detect_year(text: str):
    m = re.search(r"\b(19[9]\d|20[0-3]\d)\b", text)
    return m.group(1) if m else ""


def _detect_price(text: str):
    patterns = [
        r"(?:цена|баасы|price|prc)[\s:]*([\d][\d\s'.,]*)\s*(?:\$|usd|долл)",
        r"\$\s*([\d][\d\s'.,]*)",
        r"([\d][\d\s'.,]*)\s*\$",
        r"([\d][\d\s'.,]*)\s*usd",
    ]
    for p in patterns:
        m = re.search(p, text, re.IGNORECASE)
        if m:
            raw = re.sub(r"[^\d]", "", m.group(1))
            if raw and 500 <= int(raw) <= 10_000_000:
                n = int(raw)
                return f"${n:,}".replace(",", " ")
    return ""


def _detect_fuel(text_low: str):
    for k, v in FUEL_MAP.items():
        if re.search(rf"\b{re.escape(k)}\b", text_low):
            return v
    return ""


def _detect_engine(text: str):
    parts = []
    m = re.search(r"(?:объ[её]м|мотор|engine|двигатель)[\s:]*([\d.]+)", text, re.IGNORECASE)
    if m:
        parts.append(m.group(1))
    else:
        m = re.search(r"\b([1-9]\.\d)\b(?!\d)", text)
        if m:
            parts.append(m.group(1))
    if re.search(r"\bturbo\b|турбо", text, re.IGNORECASE):
        parts.append("Turbo")
    if re.search(r"\bv6\b", text, re.IGNORECASE):
        parts.append("V6")
    if re.search(r"\bv8\b", text, re.IGNORECASE):
        parts.append("V8")
    if re.search(r"\bv12\b", text, re.IGNORECASE):
        parts.append("V12")
    if re.search(r"\b4wd\b|\bawd\b|полный\s*привод", text, re.IGNORECASE):
        parts.append("4WD")
    elif re.search(r"\b2wd\b|\bfwd\b|\brwd\b", text, re.IGNORECASE):
        m = re.search(r"\b(2wd|fwd|rwd)\b", text, re.IGNORECASE)
        if m:
            parts.append(m.group(1).upper())
    return " ".join(parts)


def _detect_body(text: str, model: str):
    text_low = text.lower()
    body_map = {
        "внедорожник": "Внедорожник", "suv": "Внедорожник",
        "кроссовер": "Кроссовер", "crossover": "Кроссовер",
        "седан": "Седан", "sedan": "Седан",
        "пикап": "Пикап", "pickup": "Пикап",
        "грузовик": "Грузовик", "truck": "Грузовик",
        "минивэн": "Минивэн", "минивен": "Минивэн", "minivan": "Минивэн", "van": "Минивэн",
        "автобус": "Автобус", "bus": "Автобус",
        "купе": "Купе", "coupe": "Купе",
        "хэтчбек": "Хэтчбек", "hatchback": "Хэтчбек",
    }
    for k, v in body_map.items():
        if re.search(rf"\b{re.escape(k)}\b", text_low):
            return v
    if model and model.lower() in SUV_MODELS:
        return "Внедорожник"
    return ""


def _detect_mileage(text: str):
    patterns = [
        r"пробег[\s:]*([\d][\d\s.,]*)\s*к?м?",
        r"mileage[\s:]*([\d][\d\s.,]*)\s*km",
        r"([\d][\d\s.,]*)\s*(?:км|km)\b",
        r"пробег[\s:]*([\d.,]+)\s*к\b",
    ]
    for p in patterns:
        m = re.search(p, text, re.IGNORECASE)
        if m:
            raw = m.group(1)
            if re.search(r"\bк\b", m.group(0), re.IGNORECASE) and not re.search(r"км|km", m.group(0), re.IGNORECASE):
                num = re.sub(r"[^\d.]", "", raw)
                if num:
                    try:
                        return f"{int(float(num) * 1000):,}".replace(",", " ") + " км"
                    except ValueError:
                        pass
            num = re.sub(r"[^\d]", "", raw)
            if num and 1 <= int(num) <= 2_000_000:
                return f"{int(num):,}".replace(",", " ") + " км"
    m = re.search(r"(\d+)\s*k\s*km", text, re.IGNORECASE)
    if m:
        return f"{int(m.group(1)) * 1000:,}".replace(",", " ") + " км"
    return ""


def _detect_location(text_low: str):
    for k, v in LOCATION_MAP.items():
        if re.search(rf"\b{re.escape(k)}\b", text_low):
            return v
    return ""


def _detect_phone(text: str):
    patterns = [
        r"(?:ват|whatsapp|wa|вотсап|ватсап|тел|phone|номер|tel)[\s:.+]*((?:\+?\d[\d\s\-()]{7,})\d)",
        r"(\+971[\s\-]?\d[\d\s\-]{7,})",
        r"(05\d[\s\-]?\d{3}[\s\-]?\d{4})",
        r"(\+?\d{10,15})",
    ]
    for p in patterns:
        m = re.search(p, text, re.IGNORECASE)
        if m:
            phone = re.sub(r"[^\d+]", "", m.group(1))
            if 8 <= len(re.sub(r"\D", "", phone)) <= 15:
                return phone
    return ""


def _detect_links(text: str):
    urls = re.findall(r"https?://[^\s\)\]\,]+", text)
    instagram_url = ""
    video_url = ""
    for u in urls:
        ul = u.lower()
        if "instagram.com" in ul or "instagr.am" in ul:
            instagram_url = u
        elif "tiktok.com" in ul or "youtube.com" in ul or "youtu.be" in ul:
            video_url = u
        elif re.search(r"\.(mp4|mov|webm|m4v)(\?|$)", ul):
            video_url = u
    return instagram_url, video_url


def parse_car_text(text: str) -> dict:
    """Parse messy car description text into structured dict."""
    text = text or ""
    text_low = text.lower()

    brand = _detect_brand(text_low)
    model = _detect_model(text)
    year = _detect_year(text)
    price = _detect_price(text)
    fuel = _detect_fuel(text_low)
    engine = _detect_engine(text)
    body = _detect_body(text, model)
    mileage = _detect_mileage(text)
    location = _detect_location(text_low)
    phone = _detect_phone(text)
    insta, video = _detect_links(text)

    title_parts = [p for p in [brand, model, year] if p]
    title = " ".join(title_parts)
    # Fallback: if title is empty or just a year, use first line of the message
    if not title or title == year:
        first_line = next((ln.strip() for ln in text.splitlines() if ln.strip() and not ln.startswith("http")), "")
        if first_line and len(first_line) < 80:
            # Strip trailing year if same as detected
            clean = re.sub(r"\s+", " ", first_line).strip()
            if year and clean.endswith(year):
                pass
            elif year:
                clean = f"{clean} {year}"
            title = clean

    # description: original text minus URLs and obvious phone numbers, trimmed
    desc = re.sub(r"https?://\S+", "", text).strip()
    desc = re.sub(r"\s{2,}", " ", desc)
    if len(desc) > 600:
        desc = desc[:597] + "..."

    return {
        "title": title,
        "brand": brand,
        "model": model,
        "year": year,
        "engine": engine,
        "fuel": fuel,
        "bodyType": body,
        "price": price,
        "mileage": mileage,
        "location": location or "Dubai / UAE",
        "description": desc,
        "whatsapp": phone,
        "instagramUrl": insta,
        "videoUrl": video,
        "status": "available",
    }
