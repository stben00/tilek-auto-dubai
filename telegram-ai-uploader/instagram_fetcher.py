"""Best-effort Instagram public post / reel fetcher using yt-dlp.

Downloads media (photos or video) and extracts caption from public Instagram URLs.
May fail when Instagram updates their site — caller should fall back to manual mode.
"""
import asyncio
import os
import shutil
import tempfile
from pathlib import Path


def _is_instagram_url(url: str) -> bool:
    u = url.lower()
    return "instagram.com" in u or "instagr.am" in u


async def fetch_instagram(url: str) -> dict:
    """
    Returns:
      {
        "ok": bool,
        "caption": str,
        "photos": [bytes, ...],
        "video": bytes | None,
        "video_thumb": bytes | None,
        "error": str | "",
      }
    """
    result = {"ok": False, "caption": "", "photos": [], "video": None, "video_thumb": None, "error": ""}
    if not _is_instagram_url(url):
        result["error"] = "Not an Instagram URL"
        return result

    tmpdir = tempfile.mkdtemp(prefix="ig_")
    try:
        loop = asyncio.get_running_loop()
        info = await loop.run_in_executor(None, _ytdlp_download, url, tmpdir)
        if not info:
            result["error"] = "yt-dlp returned nothing (post may be private or Instagram changed)"
            return result

        caption = info.get("description") or info.get("title") or ""
        result["caption"] = caption.strip()

        # Collect downloaded files: yt-dlp saves to tmpdir
        files = sorted(Path(tmpdir).iterdir())
        photo_bytes = []
        video_bytes = None
        thumb_bytes = None

        for f in files:
            ext = f.suffix.lower()
            try:
                data = f.read_bytes()
            except OSError:
                continue
            if ext in (".jpg", ".jpeg", ".png", ".webp"):
                # yt-dlp downloads thumbnails as separate files; entries with the same
                # stem as the video file are thumbnails — keep one as video_thumb
                stem = f.stem
                # If a matching .mp4 exists, this jpg is the thumbnail
                video_match = any((Path(tmpdir) / (stem + e)).exists() for e in (".mp4", ".mov", ".webm"))
                if video_match and thumb_bytes is None:
                    thumb_bytes = data
                else:
                    photo_bytes.append(data)
            elif ext in (".mp4", ".mov", ".webm") and video_bytes is None:
                video_bytes = data

        result["photos"] = photo_bytes
        result["video"] = video_bytes
        result["video_thumb"] = thumb_bytes
        result["ok"] = True
        return result
    except Exception as e:
        result["error"] = f"{type(e).__name__}: {e}"
        return result
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def _ytdlp_download(url: str, outdir: str):
    """Synchronous yt-dlp call. Returns info dict."""
    try:
        from yt_dlp import YoutubeDL
    except ImportError:
        return None

    opts = {
        "outtmpl": os.path.join(outdir, "%(id)s.%(ext)s"),
        "writethumbnail": True,
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "noplaylist": False,  # carousels are playlists
        "ignoreerrors": True,
        "format": "best",
        "socket_timeout": 30,
        "retries": 2,
    }
    try:
        with YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)
            return info
    except Exception as e:
        print(f"[instagram_fetcher] yt-dlp error: {e}")
        return None
