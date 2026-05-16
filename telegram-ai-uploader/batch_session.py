"""Batch upload session — collect multiple videos/photos, then multiple texts,
pair them by order, create one Draft per pair.

State machine per user:
  IDLE             → no batch (normal single-upload mode)
  collecting_media → user is sending videos/photos
  collecting_texts → user clicked "Media Done", sending texts
  review           → drafts created, showing summary
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class BatchMedia:
    index: int
    file_name: str          # local filename in TEMP_DIR
    file_size: int
    media_type: str         # "video" or "photo"
    caption: str = ""
    video_thumb_name: Optional[str] = None  # extracted poster frame, if video


@dataclass
class BatchText:
    index: int
    raw_text: str
    parsed_data: Optional[dict] = None      # filled later via parse_with_ai


@dataclass
class BatchSession:
    user_id: int
    status: str = "collecting_media"  # collecting_media | collecting_texts | review
    media: list[BatchMedia] = field(default_factory=list)
    texts: list[BatchText] = field(default_factory=list)
    drafts: list = field(default_factory=list)  # list of Draft objects

    def cleanup_files(self):
        from media_storage import remove_temp
        for m in self.media:
            try:
                remove_temp(m.file_name)
                if m.video_thumb_name:
                    remove_temp(m.video_thumb_name)
            except Exception:
                pass

    def media_count(self) -> int:
        return len(self.media)

    def text_count(self) -> int:
        return len(self.texts)

    def is_balanced(self) -> bool:
        return self.media_count() == self.text_count() and self.media_count() > 0


# ---------------------------------------------------------------------------
# Text splitting
# ---------------------------------------------------------------------------

# Marker patterns at start of a line that begin a new car:
#   1)  1.  1:  1-  1—   (requires delimiter after digit)
#   #1                    (hash-prefix, no delimiter needed)
# Followed by whitespace + a non-digit so phone numbers like "5)55" or
# "971551234567" don't get matched.
_CAR_NUMBER_RE = re.compile(
    r"(?:\A|\n)\s*(?:#\s*\d{1,3}\s+|\d{1,3}\s*[\)\.:\-—–]\s+)(?=\S)",
    re.MULTILINE,
)
_SEPARATOR_RE = re.compile(r"\n\s*(?:---+|===+|\*\*\*+)\s*\n")


def parse_batch_text_message(raw_text: str) -> list[str]:
    """
    Split one message into one-or-more car descriptions.

    Recognised separators (in order of preference):
      1) "1) ...", "2. ...", "#3 ..." line-starts (most explicit)
      2) horizontal separators like "---" or "==="
      3) double blank lines between blocks

    Returns a non-empty list of trimmed car-text strings. If no separator is
    detected, returns the whole message as a single item.
    """
    text = (raw_text or "").strip()
    if not text:
        return []

    # Strategy 1: numbered markers "1)" "2." "#3"
    parts = _split_by_numbers(text)
    if len(parts) > 1:
        return [p.strip() for p in parts if p.strip()]

    # Strategy 2: horizontal separators
    parts = _SEPARATOR_RE.split(text)
    if len(parts) > 1:
        return [p.strip() for p in parts if p.strip()]

    # Strategy 3: double blank lines (only if at least 2 chunks AND each has a
    # plausible car indicator like a year or price — avoids splitting on
    # accidental blank lines inside a single description)
    blocks = re.split(r"\n\s*\n\s*\n+", text)
    if len(blocks) > 1 and all(_looks_like_car(b) for b in blocks):
        return [b.strip() for b in blocks if b.strip()]

    return [text]


def _split_by_numbers(text: str) -> list[str]:
    """Split text by leading "1)", "2.", "#3" markers."""
    matches = list(_CAR_NUMBER_RE.finditer(text))
    if len(matches) < 2:
        return [text]
    # Use the match.start() to slice — first chunk is anything before match[0]
    parts: list[str] = []
    first_chunk = text[: matches[0].start()].strip()
    if first_chunk:
        # If the text BEFORE the first "1)" contains real content, keep it
        # only when it looks like a heading. Otherwise discard.
        if len(first_chunk) > 40 and _looks_like_car(first_chunk):
            parts.append(first_chunk)
    for i, m in enumerate(matches):
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        chunk = text[start:end].strip()
        if chunk:
            parts.append(chunk)
    return parts


_PRICE_RE = re.compile(r"\$|\bUSD\b|долл|\d{4,6}\b")
_YEAR_RE = re.compile(r"\b(19[8-9]\d|20[0-3]\d)\b")


def _looks_like_car(block: str) -> bool:
    """Heuristic: does this text block plausibly describe a car?"""
    if not block or len(block) < 8:
        return False
    return bool(_YEAR_RE.search(block) or _PRICE_RE.search(block) or len(block) > 60)
