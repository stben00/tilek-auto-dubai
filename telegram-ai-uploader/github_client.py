"""GitHub REST API client for committing car data + media."""
import base64
import json
import httpx
from config import (
    GITHUB_TOKEN, GITHUB_OWNER, GITHUB_REPO, GITHUB_BRANCH,
    CARS_JSON_PATH, IMAGES_FOLDER, VIDEOS_FOLDER,
)

API_BASE = "https://api.github.com"


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
        # Large file: fetch from download_url
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


async def upload_binary_file(path: str, data: bytes, message: str):
    async with httpx.AsyncClient(timeout=120) as client:
        _existing, sha = await _get_file(client, path)
        return await _put_file(client, path, data, message, sha)


async def write_cars_json(cars: list, message: str):
    async with httpx.AsyncClient(timeout=60) as client:
        _existing, sha = await _get_file(client, CARS_JSON_PATH)
        body = json.dumps(cars, ensure_ascii=False, indent=2).encode("utf-8")
        return await _put_file(client, CARS_JSON_PATH, body, message, sha)


async def publish_car(car: dict, photo_files: list[tuple[str, bytes]], video_files: list[tuple[str, bytes]]):
    """
    photo_files: list of (filename, bytes)
    video_files: list of (filename, bytes)
    Uploads media, prepends car to cars.json, returns final car dict.
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

    cars = await read_cars_json()
    cars.insert(0, car)
    title = car.get("title") or car.get("id") or "car"
    await write_cars_json(cars, f"Add car: {title}")
    return car
