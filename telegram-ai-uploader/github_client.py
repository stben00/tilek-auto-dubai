"""GitHub REST API client for committing car data + media to data/site-data.json."""
import base64
import json
import httpx
from config import (
    GITHUB_TOKEN, GITHUB_OWNER, GITHUB_REPO, GITHUB_BRANCH,
    CARS_JSON_PATH, IMAGES_FOLDER, VIDEOS_FOLDER,
)

API_BASE = "https://api.github.com"
SITE_DATA_PATH = "data/site-data.json"


def _headers():
    return {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "X-GitHub-Api-Version": "2022-11-28",
    }


async def _get_file(client: httpx.AsyncClient, path: str):
    """Returns (content_bytes, sha) or (None, None) if file does not exist."""
    url = f"{API_BASE}/repos/{GITHUB_OWNER}/{GITHUB_REPO}/contents/{path}"
    r = await client.get(url, headers=_headers(), params={"ref": GITHUB_BRANCH})
    if r.status_code == 404:
        return None, None
    r.raise_for_status()
    data = r.json()
    if data.get("encoding") == "base64" and data.get("content"):
        content = base64.b64decode(data["content"])
    else:
        dl = data.get("download_url")
        if dl:
            rr = await client.get(dl)
            rr.raise_for_status()
            content = rr.content
        else:
            content = b""
    return content, data.get("sha")


async def _put_file(client: httpx.AsyncClient, path: str, content_bytes: bytes, message: str, sha: str | None):
    url = f"{API_BASE}/repos/{GITHUB_OWNER}/{GITHUB_REPO}/contents/{path}"
    payload = {
        "message": message,
        "content": base64.b64encode(content_bytes).decode("ascii"),
        "branch": GITHUB_BRANCH,
    }
    if sha:
        payload["sha"] = sha
    r = await client.put(url, headers=_headers(), json=payload)
    if r.status_code not in (200, 201):
        raise RuntimeError(f"GitHub PUT {path} failed: {r.status_code} {r.text}")
    return r.json()


def _empty_site_data() -> dict:
    return {"cars": [], "videos": [], "reviews": [], "faq": [], "content": {}}


async def read_site_data() -> dict:
    """Read data/site-data.json. Returns empty scaffold if missing/invalid."""
    async with httpx.AsyncClient(timeout=30) as client:
        content, _sha = await _get_file(client, SITE_DATA_PATH)
        if not content:
            return _empty_site_data()
        try:
            data = json.loads(content.decode("utf-8"))
            if not isinstance(data, dict):
                return _empty_site_data()
            for key in ("cars", "videos", "reviews", "faq"):
                if not isinstance(data.get(key), list):
                    data[key] = []
            if not isinstance(data.get("content"), dict):
                data["content"] = {}
            return data
        except json.JSONDecodeError:
            return _empty_site_data()


async def write_site_data(data: dict, message: str):
    async with httpx.AsyncClient(timeout=60) as client:
        _existing, sha = await _get_file(client, SITE_DATA_PATH)
        body = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
        return await _put_file(client, SITE_DATA_PATH, body, message, sha)


async def upload_binary_file(path: str, data: bytes, message: str):
    async with httpx.AsyncClient(timeout=120) as client:
        _existing, sha = await _get_file(client, path)
        return await _put_file(client, path, data, message, sha)


# ----- Legacy cars.json helpers (kept for backwards compatibility) -----

async def read_cars_json() -> list:
    async with httpx.AsyncClient(timeout=30) as client:
        content, _sha = await _get_file(client, CARS_JSON_PATH)
        if not content:
            return []
        try:
            data = json.loads(content.decode("utf-8"))
            if isinstance(data, list):
                return data
            if isinstance(data, dict) and isinstance(data.get("cars"), list):
                return data["cars"]
            return []
        except json.JSONDecodeError:
            return []


async def write_cars_json(cars: list, message: str):
    async with httpx.AsyncClient(timeout=60) as client:
        _existing, sha = await _get_file(client, CARS_JSON_PATH)
        body = json.dumps(cars, ensure_ascii=False, indent=2).encode("utf-8")
        return await _put_file(client, CARS_JSON_PATH, body, message, sha)


def _detect_video_type(url: str) -> str:
    if not url:
        return ""
    ul = url.lower()
    if ul.startswith("assets/videos/") or ul.endswith((".mp4", ".mov", ".webm", ".m4v")):
        return "local"
    if "youtube.com" in ul or "youtu.be" in ul:
        return "youtube"
    if "instagram.com" in ul or "instagr.am" in ul:
        return "instagram"
    return "external"


def _to_site_car(car: dict) -> dict:
    """Map bot's car dict to site-compatible schema with all video fields."""
    video_url = car.get("videoFile") or car.get("videoUrl") or ""
    video_type = _detect_video_type(video_url)
    main_image = car.get("mainImage") or (car.get("images")[0] if car.get("images") else "")
    return {
        "id": car.get("id"),
        "title": car.get("title", ""),
        "brand": car.get("brand", ""),
        "model": car.get("model", ""),
        "year": car.get("year", ""),
        "engine": car.get("engine", ""),
        "fuel": car.get("fuel", ""),
        "bodyType": car.get("bodyType", ""),
        "body": "",
        "budget": "",
        "tag": "reliable",
        "price": car.get("price", ""),
        "mileage": car.get("mileage", ""),
        "location": car.get("location", "Dubai / UAE"),
        "description": car.get("description", ""),
        "whatsapp": car.get("whatsapp", ""),
        "instagramUrl": car.get("instagramUrl", ""),
        "image": main_image,
        "mainImage": main_image,
        "images": car.get("images", []),
        "videoUrl": video_url,
        "videoFile": car.get("videoFile", ""),
        "videoType": video_type,
        "videoPoster": main_image,
        "videoTitle": f"Видео-обзор {car.get('title','')}".strip() if video_url else "",
        "videoDuration": car.get("videoDuration", ""),
        "status": car.get("status", "available"),
        "source": "telegram_ai_uploader",
        "createdAt": car.get("createdAt", ""),
    }


def _to_site_video(car: dict) -> dict | None:
    """Build a separate video-section entry from the car if it has video media."""
    video_url = car.get("videoFile") or car.get("videoUrl") or ""
    if not video_url:
        return None
    main_image = car.get("mainImage") or (car.get("images")[0] if car.get("images") else "")
    return {
        "id": car.get("id"),
        "carId": car.get("id"),
        "title": f"{car.get('title','')} — видео-обзор".strip(),
        "url": video_url,
        "thumb": main_image,
        "type": _detect_video_type(video_url),
        "views": "новое",
        "instagramUrl": car.get("instagramUrl", ""),
    }


async def publish_car(car: dict, photo_files: list[tuple[str, bytes]], video_files: list[tuple[str, bytes]]):
    """
    Uploads media to assets/cars + assets/videos, then prepends car (and a matching
    video entry, if media present) to data/site-data.json.
    Also keeps cars.json updated for legacy consumers.
    """
    image_paths = []
    video_paths = []

    async with httpx.AsyncClient(timeout=180) as client:
        for fname, data in photo_files:
            path = f"{IMAGES_FOLDER}/{fname}"
            _existing, sha = await _get_file(client, path)
            await _put_file(client, path, data, f"Upload photo {fname}", sha)
            image_paths.append(path)

        for fname, data in video_files:
            path = f"{VIDEOS_FOLDER}/{fname}"
            _existing, sha = await _get_file(client, path)
            await _put_file(client, path, data, f"Upload video {fname}", sha)
            video_paths.append(path)

    car["images"] = image_paths
    if image_paths:
        car["mainImage"] = image_paths[0]
    if video_paths:
        car["videoFile"] = video_paths[0]

    # Update data/site-data.json (primary source for the site)
    site = await read_site_data()
    site_car = _to_site_car(car)
    site["cars"].insert(0, site_car)
    video_entry = _to_site_video(car)
    if video_entry:
        site["videos"].insert(0, video_entry)
    title = car.get("title") or car.get("id") or "car"
    await write_site_data(site, f"Add car: {title}")

    # Also keep cars.json updated for legacy fallback
    try:
        cars = await read_cars_json()
        cars.insert(0, car)
        await write_cars_json(cars, f"Add car (legacy): {title}")
    except Exception as e:
        print(f"[publish_car] cars.json legacy update skipped: {e}")

    return car
