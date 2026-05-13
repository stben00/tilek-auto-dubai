"""Local temp media storage + filename helpers."""
import os
import shutil
import subprocess
import tempfile
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


def extract_video_poster(video_path: Path | str, timestamp_pct: float = 0.30) -> bytes | None:
    """
    Extract a high-quality frame from the given video file using ffmpeg.
    - Picks a frame at `timestamp_pct` of the video duration (default 30%)
    - Outputs 1280px wide JPEG with mild brightness/contrast boost for dark scenes
    - Returns the JPEG bytes, or None if ffmpeg is missing or extraction fails.
    """
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        return None
    video_path = str(video_path)
    if not os.path.exists(video_path):
        return None
    # 1. Probe duration
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", video_path],
            capture_output=True, text=True, timeout=20,
        )
        duration = float(out.stdout.strip()) if out.stdout.strip() else 0.0
    except Exception:
        duration = 0.0
    # Clamp seek time: avoid first 0.3s (frame may be black/transition)
    seek = max(0.3, duration * timestamp_pct) if duration > 0 else 0.5

    # 2. Extract frame with quality enhancements
    out_path = tempfile.mktemp(suffix=".jpg")
    try:
        result = subprocess.run(
            [
                "ffmpeg", "-y",
                "-ss", f"{seek:.2f}",
                "-i", video_path,
                "-frames:v", "1",
                "-vf", "scale=1280:-2:flags=lanczos,eq=brightness=0.04:contrast=1.15:saturation=1.10",
                "-q:v", "2",  # 2 = best JPEG quality in ffmpeg's scale 2..31
                out_path,
            ],
            capture_output=True, timeout=30,
        )
        if result.returncode != 0 or not os.path.exists(out_path):
            return None
        with open(out_path, "rb") as f:
            return f.read()
    except Exception:
        return None
    finally:
        try:
            if os.path.exists(out_path):
                os.remove(out_path)
        except OSError:
            pass
