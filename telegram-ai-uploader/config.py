import os
from dotenv import load_dotenv
from pathlib import Path

load_dotenv()

BASE_DIR = Path(__file__).parent
TEMP_DIR = BASE_DIR / "data" / "temp"
TEMP_DIR.mkdir(parents=True, exist_ok=True)

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
ADMIN_TELEGRAM_IDS = {
    int(x) for x in os.getenv("ADMIN_TELEGRAM_IDS", "").replace(" ", "").split(",") if x.strip().isdigit()
}

GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "").strip()
GITHUB_OWNER = os.getenv("GITHUB_OWNER", "stben00").strip()
GITHUB_REPO = os.getenv("GITHUB_REPO", "tilek-auto-dubai").strip()
GITHUB_BRANCH = os.getenv("GITHUB_BRANCH", "main").strip()
CARS_JSON_PATH = os.getenv("CARS_JSON_PATH", "cars.json").strip()
IMAGES_FOLDER = os.getenv("IMAGES_FOLDER", "assets/cars").strip().strip("/")
VIDEOS_FOLDER = os.getenv("VIDEOS_FOLDER", "assets/videos").strip().strip("/")

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "").strip()

WEBSITE_URL = os.getenv("WEBSITE_URL", "https://stben00.github.io/tilek-auto-dubai/").strip()
MAX_VIDEO_MB = int(os.getenv("MAX_VIDEO_MB", "20"))

HAS_AI = bool(OPENAI_API_KEY or ANTHROPIC_API_KEY)


def is_admin(user_id: int) -> bool:
    if not ADMIN_TELEGRAM_IDS:
        return False
    return user_id in ADMIN_TELEGRAM_IDS
