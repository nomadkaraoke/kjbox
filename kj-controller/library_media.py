# kj-controller/library_media.py
"""Canonical identity for external-library (SSD) files.

Design: docs/archive/2026-07-02-ssd-library-media-identity-design.md (rev 2).
Files stay in place on the SSD; identity is content-derived (lib-<sha1[:12]>)
so the KJ can rename/reorganise freely — after a move, the next touch
re-hashes to the same id and the row's file_path is refreshed. Rows
materialize lazily on touch; hashing never runs on a request thread (callers
use run_async) and never on the search hot path.
"""
import os
import threading

from catalog import parse_karaoke_filename
from naming import SOURCE_LIBRARY, content_hash, media_id_for


def is_library_path(path, config):
    """True when ``path`` lives under the configured external media mount."""
    mount = ((config or {}).get("external_media_mount") or "").rstrip("/")
    if not mount or not path:
        return False
    return path == mount or path.startswith(mount + "/")


def ensure_library_row(path, catalog, media_library):
    """Return the media_library row for an SSD file, creating it if needed.

    Fast path: an existing by-path row returns without touching the file.
    Otherwise hash the content; a row already existing under that id means
    the file moved — refresh file_path/ext only, preserving (possibly
    manually edited) identity. Brand-new ids get artist/title from the
    catalog, falling back to the deterministic filename parse with
    needs_review=1. Returns None when the file is absent/unreadable.
    """
    if media_library is None or not path:
        return None
    row = media_library.get_by_path(path)
    if row:
        return row
    if not os.path.isfile(path):
        return None
    try:
        digest = content_hash(path)
    except OSError:
        return None
    media_id = media_id_for(SOURCE_LIBRARY, digest)
    ext = os.path.splitext(path)[1].lower()
    if media_library.get(media_id) is not None:
        media_library.upsert_scanned(
            {"media_id": media_id, "file_path": path, "ext": ext})
        return media_library.get(media_id)
    basename = os.path.basename(path)
    try:
        cat_row = catalog.get_by_path(path) if catalog is not None else None
    except Exception:
        cat_row = None
    if cat_row and ((cat_row.get("artist") or "").strip()
                    or (cat_row.get("title") or "").strip()):
        artist = cat_row.get("artist") or ""
        title = cat_row.get("title") or ""
        parse_method, needs_review = "catalog", 0
    else:
        _disc, artist, title = parse_karaoke_filename(basename)
        parse_method, needs_review = "deterministic", 1
    media_library.upsert({
        "media_id": media_id,
        "source": SOURCE_LIBRARY,
        "source_ref": digest,
        "artist": artist,
        "title": title,
        "confidence": None,
        "parse_method": parse_method,
        "needs_review": needs_review,
        "raw_original_name": basename,
        "file_path": path,
        "ext": ext,
    })
    return media_library.get(media_id)


def ensure_library_row_for_app(app, path):
    """Thread-target convenience: resolve stores off the app object; never raises."""
    try:
        return ensure_library_row(
            path, getattr(app, "catalog", None), getattr(app, "media_library", None))
    except Exception:
        return None


def run_async(target, *args):
    """Run target(*args) on a daemon thread.

    Production never hashes multi-hundred-MB files on a request thread
    (design D3); tests monkeypatch this to run synchronously.
    """
    threading.Thread(target=target, args=args, daemon=True).start()
