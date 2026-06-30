"""Logging and filename utility functions."""

import os
import re
import sys
import time


def media_type_label(name_or_ext):
    """Friendly file-type label for the UI, e.g. 'cdg-zip', 'cdg', 'mp4', 'mp3'.

    Accepts a filename, a full path, or a bare extension (with or without the dot).
    """
    if not name_or_ext:
        return "file"
    ext = name_or_ext if name_or_ext.startswith(".") else os.path.splitext(name_or_ext)[1]
    ext = ext.lower()
    if ext == ".zip":
        return "cdg-zip"
    if ext == ".cdg":
        return "cdg"
    return ext[1:] if ext else "file"


def log_message(message, config=None):
    """Appends a message to the log file and prints to stderr."""
    log_file = config.get('log_file') if config else None
    if log_file:
        try:
            with open(log_file, "a") as f:
                f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - {message}\n")
        except Exception:
            pass
    print(message, file=sys.stderr, flush=True)


def sanitize_filename_part(text):
    """Replace filesystem-unsafe chars and __ (our separator) with _. Truncate to 100 chars."""
    text = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', text)
    text = re.sub(r'_{2,}', '_', text)
    text = re.sub(r'\s+', ' ', text).strip()
    text = text[:100]
    return text


def build_divebar_filename(brand_code, artist, title, ext=".mp4"):
    """Build a human-readable filename for a Divebar download.

    Format: ``<brand_code | "DB"> - <artist> - <title><ext>``. Returns None
    when neither artist nor title is present so the caller can apply its
    own fallback (typically ``divebar-{file_id}.mp4``). The on-disk
    ``divebar__`` prefix is added separately by ``media.download_from_url``.
    """
    bc = (brand_code or "DB").strip() or "DB"
    artist_part = sanitize_filename_part(artist).strip() if artist else ""
    title_part = sanitize_filename_part(title).strip() if title else ""
    if not artist_part and not title_part:
        return None
    parts = [bc]
    if artist_part:
        parts.append(artist_part)
    if title_part:
        parts.append(title_part)
    return " - ".join(parts) + ext


def divebar_ext(url=None, fmt=None):
    """Pick the on-disk extension for a Divebar download.

    A Divebar file may be mp4/mkv/avi/… video, a CDG+MP3 ``.zip``, a bare
    ``.cdg`` or a bare ``.mp3``. The extension must be correct on disk because
    playability classification (and therefore the download gate) keys off it —
    a CDG zip written as ``.mp4`` is misclassified as video and rejected.

    Resolution order:
      1. The real extension in the (GCS) URL path — the most authoritative
         source; GCS mirror URLs encode the object's true filename.
      2. The catalog ``format`` (e.g. ``"zip"``) — the fallback for Drive URLs
         whose path carries no extension.
      3. ``.mp4`` default.

    Only ever returns a known media extension (``config.MEDIA_EXTENSIONS``).
    """
    from config import MEDIA_EXTENSIONS

    if url:
        from urllib.parse import urlparse, unquote
        try:
            path = unquote(urlparse(url).path)
        except (ValueError, TypeError):
            path = ""
        url_ext = os.path.splitext(path)[1].lower()
        if url_ext in MEDIA_EXTENSIONS:
            return url_ext

    if fmt:
        candidate = "." + str(fmt).strip().lower().lstrip(".")
        if candidate in MEDIA_EXTENSIONS:
            return candidate

    return ".mp4"


def parse_youtube_filename(filename):
    """Parse youtube_id, channel, title from filename like {id}__{channel}__{title}.ext.
    Returns (youtube_id, channel, title) or None if not in this format."""
    stem = os.path.splitext(filename)[0]
    parts = stem.split('__', 2)
    if len(parts) == 3 and len(parts[0]) == 11:
        return (parts[0], parts[1], parts[2])
    return None
