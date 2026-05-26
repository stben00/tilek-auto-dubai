"""Local temp media storage + filename helpers."""
import base64
import logging
import os
import shutil
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from config import TEMP_DIR, MAX_VIDEO_MB

log = logging.getLogger(__name__)


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
                # Extract at full HD (1920px wide). Lanczos for sharp scaling, mild
                # brightness/contrast/saturation lift so dim garage videos look better
                # before the Pillow enhancement pass.
                "-vf", "scale=1920:-2:flags=lanczos,eq=brightness=0.05:contrast=1.18:saturation=1.12",
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


def _extract_candidate_frames(video_path: str, candidates_pct: tuple) -> list[tuple[float, str]]:
    """Extract frames at the given percentages. Returns list of (heuristic_score, path)."""
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        return []
    if not os.path.exists(video_path):
        return []
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
        out_path = tempfile.mktemp(suffix=".jpg")
        if _extract_frame_at(video_path, 0.5, out_path):
            return [(_score_frame(out_path), out_path)]
        return []

    candidates: list[tuple[float, str]] = []
    for pct in candidates_pct:
        seek = max(0.3, duration * pct)
        out_path = tempfile.mktemp(suffix=".jpg")
        if _extract_frame_at(video_path, seek, out_path):
            candidates.append((_score_frame(out_path), out_path))
    return candidates


def _build_vision_prompt(n_frames: int, target_brand: str = "", target_model: str = "") -> str:
    """Build the shared prompt for the vision frame-picker (provider-agnostic)."""
    brand_hint = ""
    if target_brand:
        full_target = (target_brand + " " + target_model).strip()
        brand_hint = (
            f"🎯 BRAND MATCH — The seller is listing a {full_target}. The video was filmed in a "
            f"dealership lot with many other cars in view. You MUST pick a frame showing the "
            f"{target_brand} specifically — recognize its grille, badge, and overall body shape. "
            f"If multiple cars are visible in a frame, the {target_brand} must be the largest / "
            f"most centred one. NEVER pick a frame where a different brand is the main subject "
            f"(e.g. a BMW in a Honda listing). If no frame shows a {target_brand} clearly, return "
            f"the lowest-numbered frame.\n\n"
        )
    return (
        "You are picking the cover photo for a car-sale poster.\n\n"
        + brand_hint +
        "🚗 RULE #1 — IF ANY FRAME SHOWS THE FRONT OF THE CAR (grille + headlights + "
        "hood, even partially), YOU MUST PICK THAT FRAME. Don't compromise on this. "
        "Buyers decide in the first 2 seconds, and the front is what sells.\n\n"
        "📹 TIE-BREAKER — Frames are numbered in chronological order. Lower numbers "
        "come from EARLIER in the video. Sellers almost always start their clip pointed "
        "at the front of the car, so when two frames qualify equally under Rule #1, "
        "PREFER THE LOWER-NUMBERED FRAME.\n\n"
        "ABSOLUTE REJECTIONS — never choose a frame that primarily shows any of:\n"
        "  • paper documents, dealership sales sheets, receipts, invoices, VAT printouts\n"
        "  • VIN stickers, windshield price tags, auction lot papers, registration cards\n"
        "  • Arabic / English text close-ups, license-plate close-ups\n"
        "  • interior shots (dashboard, steering wheel, seats, gear stick)\n"
        "  • close-ups of wheels, badges, mirrors, engine bay\n"
        "  • motion-blur, very dark frames, partial views where less than half the car body is visible\n\n"
        "PREFERENCE ORDER (apply Rule #1 first, then break ties with this):\n"
        "  1. Sharp front view, car centered: full grille + both headlights + hood + bumper.\n"
        "  2. 3/4 front angle (front + side together).\n"
        "  3. Clean side profile of the whole car.\n"
        "  4. 3/4 rear angle only if no front/side exists.\n\n"
        "If MULTIPLE frames qualify under Rule #1, pick the one where the car is biggest "
        "and the lighting is best.\n\n"
        f"If NONE of the {n_frames} frames satisfy any preference, return the LEAST "
        f"BAD frame number — never return 0 or text.\n"
        f"Reply with ONLY a single digit between 1 and {n_frames}. No words, no punctuation."
    )


def _parse_frame_choice(raw: str, n_frames: int) -> int | None:
    """Find the first digit in the model's response and convert to 0-based index."""
    for ch in (raw or "").strip():
        if ch.isdigit():
            idx = int(ch) - 1
            if 0 <= idx < n_frames:
                return idx
            break
    return None


async def _pick_frame_gemini(frame_paths: list[str], prompt: str) -> int | None:
    """Send the frames to Gemini 2.5 Flash Vision via REST. Returns 0-based index or None."""
    import httpx
    api_key = os.getenv("GEMINI_API_KEY", "").strip()
    if not api_key:
        return None

    parts = [{"text": prompt}]
    for path in frame_paths:
        try:
            with open(path, "rb") as f:
                b64 = base64.b64encode(f.read()).decode()
            parts.append({"inline_data": {"mime_type": "image/jpeg", "data": b64}})
        except OSError:
            return None

    url = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        f"gemini-2.5-flash:generateContent?key={api_key}"
    )
    payload = {
        "contents": [{"role": "user", "parts": parts}],
        "generationConfig": {"temperature": 0, "maxOutputTokens": 8},
    }
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            r = await client.post(url, json=payload)
            if r.status_code != 200:
                log.warning("Gemini Vision HTTP %d: %s", r.status_code, r.text[:200])
                return None
            data = r.json()
        raw = (
            data.get("candidates", [{}])[0]
                .get("content", {})
                .get("parts", [{}])[0]
                .get("text", "")
        )
        return _parse_frame_choice(raw, len(frame_paths))
    except Exception as e:
        log.warning("Gemini Vision frame picker failed: %s", e)
        return None


async def _pick_frame_openai(frame_paths: list[str], prompt: str) -> int | None:
    """OpenAI gpt-4o-mini fallback for the vision frame picker."""
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        return None
    try:
        from openai import AsyncOpenAI
    except ImportError:
        return None

    content: list[dict] = [{"type": "text", "text": prompt}]
    for path in frame_paths:
        try:
            with open(path, "rb") as f:
                b64 = base64.b64encode(f.read()).decode()
            content.append({
                "type": "image_url",
                "image_url": {"url": f"data:image/jpeg;base64,{b64}", "detail": "low"},
            })
        except OSError:
            return None

    client = AsyncOpenAI(api_key=api_key, timeout=30.0)
    try:
        resp = await client.chat.completions.create(
            model="gpt-4o-mini",
            max_tokens=4,
            temperature=0,
            messages=[{"role": "user", "content": content}],
        )
        return _parse_frame_choice(resp.choices[0].message.content or "", len(frame_paths))
    except Exception as e:
        log.warning("OpenAI Vision frame picker failed: %s", e)
        return None


async def _pick_best_exterior_frame_via_vision(frame_paths: list[str], target_brand: str = "", target_model: str = "") -> int | None:
    """
    Pick the frame that best shows the FRONT/EXTERIOR of the car.
    Tries the provider chain (AI_PROVIDER env var): gemini → openai. Both
    use the exact same prompt for consistent behaviour.
    """
    if not frame_paths:
        return None
    provider = os.getenv("AI_PROVIDER", "auto").strip().lower()
    prompt = _build_vision_prompt(len(frame_paths), target_brand, target_model)

    if provider == "gemini" or (provider == "auto" and os.getenv("GEMINI_API_KEY")):
        idx = await _pick_frame_gemini(frame_paths, prompt)
        if idx is not None:
            return idx
        if provider == "gemini":
            return None  # strict mode, don't fall back to OpenAI
    return await _pick_frame_openai(frame_paths, prompt)


async def pick_best_hero_photo(photo_paths: list[Path | str], target_brand: str = "", target_model: str = "") -> int:
    """
    Score multiple uploaded photos and return the index of the best hero shot.
    Reuses the same Gemini/OpenAI Vision picker as the video frame extractor.

    Falls back to heuristic scoring (brightness + sharpness) when Vision is
    unavailable. Returns 0 (first photo) if all picking strategies fail.
    """
    if not photo_paths:
        return 0
    if len(photo_paths) == 1:
        return 0

    use_vision = os.getenv("USE_VISION_FRAME_PICKER", "true").strip().lower() in ("1", "true", "yes")
    str_paths = [str(p) for p in photo_paths]

    if use_vision:
        try:
            chosen = await _pick_best_exterior_frame_via_vision(str_paths, target_brand=target_brand, target_model=target_model)
            if chosen is not None and 0 <= chosen < len(str_paths):
                log.info("Hero photo picker chose photo %d/%d via Vision", chosen + 1, len(str_paths))
                return chosen
        except Exception as e:
            log.warning("Hero photo Vision picker failed: %s", e)

    scored = [(_score_frame(p), i) for i, p in enumerate(str_paths)]
    scored.sort(reverse=True)
    if scored and scored[0][0] > 0:
        log.info("Hero photo picker chose photo %d/%d via heuristic", scored[0][1] + 1, len(str_paths))
        return scored[0][1]
    return 0


def extract_video_poster(video_path: Path | str, candidates_pct: tuple = (0.0, 0.10, 0.25, 0.40, 0.55, 0.70, 0.85, 0.95)) -> bytes | None:
    """
    Sync poster extraction — fallback when caller can't await.
    Picks the highest-scoring frame by heuristics (brightness + sharpness + detail).
    """
    video_path = str(video_path)
    candidates = _extract_candidate_frames(video_path, candidates_pct)
    if not candidates:
        return None
    candidates.sort(reverse=True)
    _best_score, best_path = candidates[0]
    try:
        with open(best_path, "rb") as f:
            data = f.read()
    except OSError:
        data = None
    for _score, p in candidates:
        try: os.remove(p)
        except OSError: pass
    return data


async def extract_video_poster_smart(
    video_path: Path | str,
    candidates_pct: tuple = (0.0, 0.04, 0.08, 0.12, 0.18, 0.25, 0.35, 0.50, 0.70, 0.90),
    target_brand: str = "",
    target_model: str = "",
) -> bytes | None:
    """
    Async poster extraction that uses GPT-4o-mini Vision to pick the frame
    that best shows the EXTERIOR ("face") of the car.

    Extracts 8 evenly-spaced frames including the very first (0%) and last (95%)
    of the video — sellers often deliberately position the front shot at the
    start of the clip, so the 0% frame is often pre-curated.

    Fallback order if Vision can't pick:
      1. The very first frame (most often the seller's "cover" shot)
      2. Highest brightness/sharpness scoring frame

    Toggle off by setting USE_VISION_FRAME_PICKER=false on the deployment.
    """
    use_vision = os.getenv("USE_VISION_FRAME_PICKER", "true").strip().lower() in ("1", "true", "yes")
    video_path = str(video_path)
    candidates = _extract_candidate_frames(video_path, candidates_pct)
    if not candidates:
        return None

    # Candidates come back in the same order as the input percentages, so
    # candidates[0] corresponds to the very first frame (0%).
    frame_paths = [p for _s, p in candidates]
    chosen_idx: int | None = None
    if use_vision and len(frame_paths) > 1:
        chosen_idx = await _pick_best_exterior_frame_via_vision(frame_paths, target_brand=target_brand, target_model=target_model)

    if chosen_idx is None:
        # First fallback: the 0% / cover frame. Sellers typically point the
        # camera at the front of the car for the first second.
        log.info("Vision picker undecided, defaulting to the cover (0%%) frame")
        best_path = frame_paths[0]
    else:
        best_path = frame_paths[chosen_idx]
        log.info("Vision picker chose frame %d/%d", chosen_idx + 1, len(frame_paths))

    try:
        with open(best_path, "rb") as f:
            data = f.read()
    except OSError:
        data = None
    for p in frame_paths:
        try: os.remove(p)
        except OSError: pass
    return data
