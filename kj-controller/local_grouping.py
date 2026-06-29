"""Group local rotation-search results into KJ-meaningful sections.

Recognized-brand local files already sort into the Community / Commercial tiers
via ``version_priority`` (their brand resolves), so this module only has to home
the *unknown-brand* local files — the ones that used to dump into a single,
misleading "Unknown" section. Those are one of two things:

* **4TB-SSD library** files — a well-organised collection (e.g.
  ``Karaoke - Digital/Active`` and ``.../Dead`` subfolders). Grouped by that
  folder so the heading is meaningful.
* **YTDownloads** files — downloaded from YouTube. Split by whether the brand in
  the filename looks community-trusted (from our GCS mirror, a registered
  community brand, or a community brand seen elsewhere in the same search) vs an
  unverified independent upload.

``classify_local_file`` returns a ``{"key", "label", "sort"}`` group dict the
rotation-search dropdown uses to emit section headers and order rows.
"""

import os
import re

import version_priority

# Section sort offsets. The frontend assigns the brand tiers Best=0,
# Community=10, Commercial=20; every local-unknown group sits after Commercial,
# ordered by trust.
GROUP_SORT_LIBRARY = 30
GROUP_SORT_YT_COMMUNITY = 40
GROUP_SORT_YT_UNVERIFIED = 50

# Common mount prefixes stripped so a folder label reads cleanly.
_MOUNT_STRIP_RE = re.compile(r'^/(?:media/nomad|opt/nomad)/')
# Files pulled from our community GCS mirror are named "divebar__<brand> - ...".
_DIVEBAR_PREFIX_RE = re.compile(r'^divebar__(.+)$', re.IGNORECASE)


def path_in_download_folder(path, cfg):
    """Pure cfg-based check for whether ``path`` lives under the YTDownloads
    folder. Mirrors ``MediaIndex.is_in_download_folder`` for callers (and tests)
    that don't have a live MediaIndex to hand."""
    folder = os.path.realpath((cfg or {}).get("download_folder", "") or "")
    if not folder or not path:
        return False
    real = os.path.realpath(path)
    return real == folder or real.startswith(folder + os.sep)


def _library_folder_label(path, cfg):
    """Derive a section label from a 4TB-SSD file's folder."""
    folder = os.path.dirname(path or "")
    mount = (cfg or {}).get("external_media_mount") or ""
    if mount and folder.startswith(mount):
        folder = folder[len(mount):]
    folder = _MOUNT_STRIP_RE.sub("", folder)
    segs = [s for s in folder.strip("/").split("/") if s]
    # Prefer the well-known "Karaoke - Digital/<Active|Dead|...>" grouping the
    # KJ organises the library around.
    for i, s in enumerate(segs):
        if s.lower() == "karaoke - digital":
            if i + 1 < len(segs):
                return "Karaoke - Digital/" + segs[i + 1]
            return "Karaoke - Digital"
    # Fall back to the top collection folder (skip a short bare drive name).
    if len(segs) >= 2:
        return segs[0] if len(segs[0]) > 3 else "/".join(segs[:2])
    if segs:
        return segs[0]
    return "Library"


def _yt_brand_token(filename):
    """Best-effort (source, brand_token) from a YTDownloads filename.

    Returns ``("divebar", brand)`` for GCS-mirror files, ``(None, brand)`` for
    the ``VIDEOID__Brand__...`` pattern, or ``(None, None)`` when no brand can
    be extracted.
    """
    name = (filename or "").strip()
    m = _DIVEBAR_PREFIX_RE.match(name)
    if m:
        # "divebar__ESK - Artist - Title.ext" -> "ESK"
        return ("divebar", m.group(1).split(" - ")[0].strip())
    m = version_priority._YT_FILENAME_RE.match(name)
    if m:
        return (None, m.group(1).strip())
    return (None, None)


def _is_yt_community(filename, known_community_brands):
    source, token = _yt_brand_token(filename)
    if source == "divebar":
        return True  # came straight from our community GCS mirror
    if not token:
        return False
    _canon, cls = version_priority.resolve_brand(brand_name=token)
    if cls == "community":
        return True
    return token.upper() in (known_community_brands or set())


def classify_local_file(path, filename, cfg, *, is_download,
                        known_community_brands=None):
    """Return a ``{"key", "label", "sort"}`` group dict for an unknown-brand
    local result.

    ``is_download`` (computed by the caller via ``MediaIndex.is_in_download_folder``)
    distinguishes YTDownloads from the SSD library. ``known_community_brands`` is
    an upper-cased set of brand codes/names seen as community elsewhere in the
    same search, used to recognise independents not in the static registry.
    """
    if is_download:
        if _is_yt_community(filename, known_community_brands):
            return {"key": "yt:community",
                    "label": "From YouTube — community brand",
                    "sort": GROUP_SORT_YT_COMMUNITY}
        return {"key": "yt:unverified",
                "label": "From YouTube — unverified",
                "sort": GROUP_SORT_YT_UNVERIFIED}
    label = _library_folder_label(path, cfg)
    return {"key": "lib:" + label, "label": "Library — " + label,
            "sort": GROUP_SORT_LIBRARY}


def collect_community_brand_keys(kn_results, divebar_rows):
    """Upper-cased set of brand codes/names that appear as *community* anywhere
    in this search — used to cross-reference YTDownloads files whose brand is
    not in the static registry (e.g. an independent community channel)."""
    keys = set()
    for song in kn_results or []:
        for t in song.get("tracks") or []:
            if t.get("is_community") or t.get("priority_class") == "community":
                for v in (t.get("brand_code"), t.get("brand_name")):
                    if v:
                        keys.add(str(v).upper().strip())
    for row in divebar_rows or []:
        if row.get("priority_class") == "community":
            for v in (row.get("brand_code"), row.get("brand_name")):
                if v:
                    keys.add(str(v).upper().strip())
    return keys
