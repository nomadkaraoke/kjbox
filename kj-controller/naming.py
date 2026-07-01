"""Pure source-classification, media_id derivation, and deterministic
best-effort artist/title parsing for downloaded/library media.

No I/O except content_hash(path). The LLM refinement layer (Phase 2) upgrades
low-confidence results; this module never calls the network.
"""

import hashlib
import os
import re

from utils import parse_youtube_filename, sanitize_filename_part
from catalog import parse_karaoke_filename

SOURCE_YOUTUBE = "youtube"
SOURCE_COMMUNITY = "community"
SOURCE_GEN = "gen"
SOURCE_MASTER = "master"
SOURCE_UPLOAD = "upload"

_MASTER_RE = re.compile(r"^NOMAD-(\d+)\b", re.IGNORECASE)
_GEN_RE = re.compile(r"(?:^|[^A-Za-z0-9])GEN-([0-9a-f]{4,})", re.IGNORECASE)
_YT_RE = re.compile(r"^[A-Za-z0-9_-]{11}__")
_MEDIA_ID_RE = re.compile(r"\[([a-z]+-[^\]]+)\]\.[^.]+$")

# Ordered noise patterns stripped from a title fragment (case-insensitive).
_NOISE_RES = [
    re.compile(r"\((?:final\s+)?karaoke[^)]*\)", re.IGNORECASE),
    re.compile(r"\[(?:final\s+)?karaoke[^\]]*\]", re.IGNORECASE),
    re.compile(r"\bkaraoke\b", re.IGNORECASE),
    re.compile(r"\b(?:official\s+video|lyrics?|instrumental|cover)\b", re.IGNORECASE),
    re.compile(r"_\s*karafun.*$", re.IGNORECASE),
]


def strip_karaoke_noise(text):
    """Remove karaoke-marker noise and collapse whitespace/leftover separators."""
    out = text or ""
    for rx in _NOISE_RES:
        out = rx.sub(" ", out)
    out = re.sub(r"\s+", " ", out).strip(" -_•|")
    return out.strip()


def classify_source(filename):
    """Best-effort source classification from the filename alone."""
    name = os.path.basename(filename or "")
    if _MASTER_RE.match(name):
        return SOURCE_MASTER
    if _GEN_RE.search(name):
        return SOURCE_GEN
    if _YT_RE.match(name):
        return SOURCE_YOUTUBE
    if name.startswith("divebar__"):
        return SOURCE_COMMUNITY
    return SOURCE_UPLOAD


def media_id_for(source, source_ref):
    prefix = {
        SOURCE_YOUTUBE: "yt",
        SOURCE_COMMUNITY: "db",
        SOURCE_GEN: "gen",
        SOURCE_MASTER: "nomad",
        SOURCE_UPLOAD: "up",
    }[source]
    return f"{prefix}-{source_ref}"


def extract_media_id(filename):
    """Return the embedded [media_id] token from a slug filename, or None."""
    m = _MEDIA_ID_RE.search(os.path.basename(filename or ""))
    return m.group(1) if m else None


def _hash8(text):
    return hashlib.sha1((text or "").encode("utf-8")).hexdigest()[:8]


def content_hash(path):
    """sha1 of file bytes, first 8 hex chars (stable id for keyless uploads)."""
    h = hashlib.sha1()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()[:8]


def build_slug_filename(artist, title, media_id, ext):
    """`<Artist> - <Title> [<media_id>]<ext>`, sanitized and <=255 bytes."""
    a = sanitize_filename_part(artist or "").strip()
    t = sanitize_filename_part(title or "").strip()
    stem = " - ".join(p for p in (a, t) if p) or "unknown"
    suffix = f" [{media_id}]{ext}"
    budget = 255 - len(suffix.encode("utf-8"))
    stem_b = stem.encode("utf-8")[:budget]
    stem = stem_b.decode("utf-8", "ignore").strip()
    return f"{stem}{suffix}"


def parse_identity(filename, channel=None):
    """Deterministic best-effort identity. Returns a dict:
    {source, source_ref, artist, title, confidence, needs_review, parse_method}.
    """
    name = os.path.basename(filename or "")
    source = classify_source(name)

    if source == SOURCE_MASTER:
        m = _MASTER_RE.match(name)
        disc = m.group(1)
        _disc_id, artist, title = parse_karaoke_filename(name)
        return {
            "source": source, "source_ref": disc,
            "artist": artist, "title": title,
            "confidence": 1.0, "needs_review": 0, "parse_method": "master",
        }

    if source == SOURCE_YOUTUBE:
        parsed = parse_youtube_filename(name)
        vid = parsed[0] if parsed else ""
        title_str = parsed[2] if parsed else os.path.splitext(name)[0]
        clean = strip_karaoke_noise(title_str.replace(" _ ", " - ").replace(" • ", " - "))
        _d, artist, title = parse_karaoke_filename(clean + ".x")
        if not artist and not title:
            artist, title = "", clean
        return {
            "source": source, "source_ref": vid,
            "artist": artist, "title": title,
            "confidence": 0.4, "needs_review": 1, "parse_method": "deterministic",
        }

    if source in (SOURCE_COMMUNITY, SOURCE_GEN):
        stem = os.path.splitext(name)[0]
        if stem.startswith("divebar__"):
            stem = stem[len("divebar__"):]
        _d, artist, title = parse_karaoke_filename(stem + ".x")
        if source == SOURCE_GEN:
            gm = _GEN_RE.search(name)
            ref = gm.group(1)[:8] if gm else _hash8(stem)
        else:
            brand = (_d or "DB").strip() or "DB"
            ref = f"{brand}-{_hash8(stem)}"
        return {
            "source": source, "source_ref": ref,
            "artist": artist, "title": strip_karaoke_noise(title),
            "confidence": 0.6, "needs_review": 1 if source == SOURCE_GEN else 0,
            "parse_method": "deterministic",
        }

    # upload / unknown — no natural key; caller supplies content hash as source_ref.
    stem = os.path.splitext(name)[0]
    clean = strip_karaoke_noise(stem)
    _d, artist, title = parse_karaoke_filename(clean + ".x")
    if not artist and not title:
        artist, title = "", clean
    return {
        "source": SOURCE_UPLOAD, "source_ref": None,
        "artist": artist, "title": title,
        "confidence": 0.3, "needs_review": 1, "parse_method": "deterministic",
    }
