"""Local temp media storage + filename helpers."""
import os
from datetime import datetime, timezone
from pathlib import Path
from config import TEMP_DIR, MAX_VIDEO_MB


def now_id() -> str:
    return datetime.now(timezone.utc).strftime("car_%Y%m%d_%H%M%S")


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def photo_filename(car_id: str, index: int, ext: str = "jpg") -> str:
    return f"{car_id}_{index}.{ext.lstrip('.').lower()}"


def video_filename(car_id: str, ext: str = "mp4") -> str:
    return f"{car_id}_video.{ext.lstrip('.').lower()}"


def temp_path(name: str) -> Path:
    return Path(TEMP_DIR) / name


def save_bytes(name: str, data: bytes) -> Path:
    p = temp_path(name)
    with open(p, "wb") as f:
        f.write(data)
    return p


def read_bytes(name: str) -> bytes:
    with open(temp_path(name), "rb") as f:
        return f.read()


def remove_temp(name: str):
    try:
        os.remove(temp_path(name))
    except FileNotFoundError:
        pass


def video_too_large(size_bytes: int) -> bool:
    return size_bytes > MAX_VIDEO_MB * 1024 * 1024


def human_size(size_bytes: int) -> str:
    mb = size_bytes / (1024 * 1024)
    if mb >= 1:
        return f"{mb:.1f} MB"
    kb = size_bytes / 1024
    return f"{kb:.0f} KB"
