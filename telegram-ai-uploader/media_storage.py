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


def _extract_frame_at(video_path: str, seek: float, out_path: str) -> bool:
    """Extract one frame at `seek` seconds. Returns True on success."""
    try:
        result = subprocess.run(
            [
                "ffmpeg", "-y",
                "-ss", f"{seek:.2f}",
                "-i", video_path,
                "-frames:v", "1",
                "-vf", "scale=1280:-2:flags=lanczos,eq=brightness=0.04:contrast=1.15:saturation=1.10",
                "-q:v", "2",
                out_path,
            ],
            capture_output=True, timeout=30,
        )
        return result.returncode == 0 and os.path.exists(out_path)
    except Exception:
        return False


def _score_frame(path: str) -> float:
    """
    Score a candidate frame for poster use.
    Higher = better. Looks for:
      - Brightness in mid range (not too dark, not blown out)
      - Sharpness (variance of grayscale)
      - File size (proxy for detail richness)
    Pure-PIL implementation, no OpenCV needed.
    """
    try:
        from PIL import Image, ImageStat
        with Image.open(path) as img:
            img = img.convert("L")  # grayscale
            stat = ImageStat.Stat(img)
            mean = stat.mean[0]      # 0..255
            stddev = stat.stddev[0]  # contrast/sharpness proxy
        # Penalize too-dark (<60) or too-bright (>200)
        if mean < 60:
            brightness_score = mean / 60.0
        elif mean > 200:
            brightness_score = (255 - mean) / 55.0
        else:
            brightness_score = 1.0
        # Reward higher stddev (more variance = more detail)
        sharpness_score = min(stddev / 60.0, 1.5)
        size_score = min(os.path.getsize(path) / 200_000, 1.0)
        return brightness_score * 1.0 + sharpness_score * 1.2 + size_score * 0.3
    except Exception:
        return 0.0


def extract_video_poster(video_path: Path | str, candidates_pct: tuple = (0.15, 0.30, 0.50, 0.70, 0.85)) -> bytes | None:
    """
    Extract the BEST frame from the given video.
    - Tries 5 timestamps across the video
    - Scores each by brightness + sharpness + detail
    - Returns the highest-scoring JPEG bytes
    """
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        return None
    video_path = str(video_path)
    if not os.path.exists(video_path):
        return None
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", video_path],
            capture_output=True, text=True, timeout=20,
        )
        duration = float(out.stdout.strip()) if out.stdout.strip() else 0.0
    except Exception:
        duration = 0.0

    if duration <= 0:
        # Single shot fallback
        out_path = tempfile.mktemp(suffix=".jpg")
        try:
            if _extract_frame_at(video_path, 0.5, out_path):
                with open(out_path, "rb") as f:
                    return f.read()
        finally:
            try: os.remove(out_path)
            except OSError: pass
        return None

    # Try multiple frames
    candidates = []
    for pct in candidates_pct:
        seek = max(0.3, duration * pct)
        out_path = tempfile.mktemp(suffix=".jpg")
        if _extract_frame_at(video_path, seek, out_path):
            score = _score_frame(out_path)
            candidates.append((score, out_path))

    if not candidates:
        return None

    # Pick best, cleanup the rest
    candidates.sort(reverse=True)
    best_score, best_path = candidates[0]
    try:
        with open(best_path, "rb") as f:
            data = f.read()
    except OSError:
        data = None
    for _score, p in candidates:
        try: os.remove(p)
        except OSError: pass
    return data
