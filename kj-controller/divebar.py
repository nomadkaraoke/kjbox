"""
Divebar Karaoke catalog client.

Calls the Divebar Lookup API (Cloud Function) to search the indexed
Divebar Google Drive catalog and look up KN cross-references.
"""

import copy
import logging
import threading
import time

import requests

logger = logging.getLogger(__name__)

# Default timeout for API calls
_TIMEOUT = 10

# In-process TTL cache for CF search results. Both the KJ link search and the
# public singer UI fire a search per debounced keystroke, and each search costs
# a ~1.5s BigQuery job server-side — so incremental typing ("queen", "queen b",
# "queen bo"...) and KJ/singer overlap re-run identical queries constantly.
# Only successful responses are cached (errors/timeouts stay uncached so a
# blip doesn't pin an empty result for the TTL). Callers mutate returned
# structures in-place (in_library flags, track['divebar'], version
# annotations), so hits return a deep copy, never the cached object.
_SEARCH_CACHE_TTL = 300  # seconds
_SEARCH_CACHE_MAX_ENTRIES = 256
_search_cache = {}  # key -> (expires_at_monotonic, value)
_search_cache_lock = threading.Lock()


def _cache_key(api_url, action, query, limit):
    # Case/whitespace-insensitive: the CF folds case (and accents) server-side,
    # so "Queen  Bohemian" and "queen bohemian" are the same remote query.
    # The endpoint is part of the key so a config reload that repoints
    # divebar_api_url can never serve the previous endpoint's results.
    return (api_url, action, " ".join((query or "").casefold().split()), limit)


def _cache_get(key):
    with _search_cache_lock:
        hit = _search_cache.get(key)
        if hit is None:
            return None
        expires_at, value = hit
        if expires_at < time.monotonic():
            del _search_cache[key]
            return None
    return copy.deepcopy(value)


def _cache_put(key, value):
    now = time.monotonic()
    with _search_cache_lock:
        if len(_search_cache) >= _SEARCH_CACHE_MAX_ENTRIES:
            expired = [k for k, (exp, _) in _search_cache.items() if exp < now]
            for k in expired:
                del _search_cache[k]
            while len(_search_cache) >= _SEARCH_CACHE_MAX_ENTRIES:
                # Python dicts iterate in insertion order — evict oldest first.
                del _search_cache[next(iter(_search_cache))]
        _search_cache[key] = (now + _SEARCH_CACHE_TTL, copy.deepcopy(value))


def clear_search_cache():
    """Drop all cached search results (tests; config/catalog changes)."""
    with _search_cache_lock:
        _search_cache.clear()


def _get_api_url(config):
    """Get the Divebar API URL from config."""
    return config.get("divebar_api_url", "").rstrip("/")


def search(query, config=None, limit=50):
    """
    Search the Divebar catalog by artist/title.

    Returns list of dicts grouped by song:
    [
        {
            "artist": "Queen",
            "title": "Bohemian Rhapsody",
            "tracks": [
                {"file_id": "abc", "brand": "WTF Karaoke", "format": "mp4", "file_size": 45000000, ...},
                ...
            ]
        },
        ...
    ]
    """
    config = config or {}
    api_url = _get_api_url(config)
    if not api_url:
        logger.warning("divebar_api_url not configured")
        return []

    cache_key = _cache_key(api_url, "search", query, limit)
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached

    try:
        resp = requests.post(
            api_url,
            json={"action": "search", "query": query, "limit": limit},
            timeout=_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()

        if data.get("status") != "ok":
            logger.error("Divebar search error: %s", data.get("message"))
            return []

        # Group flat results by (artist, title) into songs with tracks
        grouped = _group_results(data.get("results", []))
        _cache_put(cache_key, grouped)
        return grouped

    except requests.Timeout:
        logger.warning("Divebar search timed out")
        return []
    except requests.RequestException as e:
        logger.error("Divebar search failed: %s", e)
        return []


def kn_community_search(query, config=None, limit=50):
    """Search our OWN KaraokeNerds community catalog via the Divebar Cloud Function.

    This replaces the old live scrape of karaokenerds.com. The Cloud Function
    reads `karaokenerds_community` (the free, directly-playable web/YouTube tracks
    populated daily by the authorized `kn-data-sync` export). Returns a flat list
    of ``{artist, title, brand, watch}`` rows (grouped into songs by the caller),
    or ``[]`` on any error/timeout (best-effort — never raises).
    """
    config = config or {}
    api_url = _get_api_url(config)
    if not api_url or not query:
        return []

    cache_key = _cache_key(api_url, "kn_community_search", query, limit)
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached

    try:
        resp = requests.post(
            api_url,
            json={"action": "kn_community_search", "query": query, "limit": limit},
            timeout=_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()

        if data.get("status") != "ok":
            logger.error("KN community search error: %s", data.get("message"))
            return []

        results = data.get("results", [])
        _cache_put(cache_key, results)
        return results

    except requests.Timeout:
        logger.warning("KN community search timed out")
        return []
    except requests.RequestException as e:
        logger.error("KN community search failed: %s", e)
        return []


def kn_search(query, config=None, limit=50):
    """Search BOTH KaraokeNerds catalogs via the Divebar Cloud Function.

    One HTTP call, one BigQuery job server-side (the CF UNIONs the two tables).
    ``community`` rows are the web-playable tracks (``{artist, title, brand,
    watch}``); ``full`` rows are the complete KN catalog (``{artist, title,
    brands}`` — comma-separated brand CODES, including commercial disc brands
    that have no web version and therefore no URL). Returns empty lists on any
    error/timeout (best-effort — never raises).
    """
    empty = {"community": [], "full": []}
    config = config or {}
    api_url = _get_api_url(config)
    if not api_url or not query:
        return empty

    cache_key = _cache_key(api_url, "kn_search", query, limit)
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached

    try:
        resp = requests.post(
            api_url,
            json={"action": "kn_search", "query": query, "limit": limit},
            timeout=_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()

        if data.get("status") != "ok":
            logger.error("KN search error: %s", data.get("message"))
            return empty

        result = {
            "community": data.get("community", []),
            "full": data.get("full", []),
        }
        _cache_put(cache_key, result)
        return result

    except requests.Timeout:
        logger.warning("KN search timed out")
        return empty
    except requests.RequestException as e:
        logger.error("KN search failed: %s", e)
        return empty


def lookup_kn_ids(kn_ids, config=None):
    """
    Look up which KN song IDs have Divebar versions.

    Returns dict: {kn_id: [{"file_id": ..., "brand": ..., "format": ..., ...}]}
    """
    config = config or {}
    api_url = _get_api_url(config)
    if not api_url or not kn_ids:
        return {}

    try:
        resp = requests.post(
            api_url,
            json={"action": "lookup", "kn_ids": kn_ids},
            timeout=_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()

        if data.get("status") != "ok":
            logger.error("Divebar lookup error: %s", data.get("message"))
            return {}

        return data.get("matches", {})

    except requests.Timeout:
        logger.warning("Divebar lookup timed out")
        return {}
    except requests.RequestException as e:
        logger.error("Divebar lookup failed: %s", e)
        return {}


def get_stats(config=None):
    """
    Get Divebar catalog statistics from the Cloud Function.

    Returns dict with catalog, gcs_mirror, formats, cross_reference, karaoke_nerds.
    """
    config = config or {}
    api_url = _get_api_url(config)
    if not api_url:
        return None

    try:
        resp = requests.post(
            api_url,
            json={"action": "stats"},
            timeout=_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()

        if data.get("status") != "ok":
            return None

        return data

    except requests.RequestException as e:
        logger.error("Divebar stats failed: %s", e)
        return None


def refresh(config=None):
    """Trigger an on-demand refresh of the Divebar pipeline.

    Calls the divebar-lookup ``refresh`` action, which force-runs the
    Drive→BigQuery index, Drive→GCS file sync, and xref-rebuild scheduler jobs
    so a track just published to the Nomad Drive shows up without waiting for
    the nightly runs.

    The action is token-gated (the endpoint is otherwise public), so a
    ``divebar_refresh_token`` must be configured. Returns the parsed response
    dict on success, or a ``{"status": "error", "message": ...}`` dict on
    failure (never raises).
    """
    config = config or {}
    api_url = _get_api_url(config)
    if not api_url:
        return {"status": "error", "message": "divebar_api_url not configured"}

    token = config.get("divebar_refresh_token")
    if not token:
        return {"status": "error", "message": "divebar_refresh_token not configured"}

    try:
        resp = requests.post(
            api_url,
            json={"action": "refresh", "token": token},
            # Force-running the scheduler jobs is quick (they run async), but
            # allow a little more headroom than a plain search.
            timeout=30,
        )
        if resp.status_code == 403:
            return {"status": "error", "message": "refresh token rejected (403)"}
        resp.raise_for_status()
        return resp.json()
    except requests.RequestException as e:
        logger.error("Divebar refresh failed: %s", e)
        return {"status": "error", "message": str(e)}


def get_download_url(file_id, config=None):
    """
    Get a download URL for a Divebar file.

    Returns direct Google Drive download URL string, or None on error.
    """
    config = config or {}
    api_url = _get_api_url(config)
    if not api_url or not file_id:
        return None

    try:
        resp = requests.post(
            api_url,
            json={"action": "download_url", "file_id": file_id},
            timeout=_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()

        if data.get("status") != "ok":
            return None

        return data.get("download_url")

    except requests.RequestException as e:
        logger.error("Divebar download URL failed: %s", e)
        return None


# Audio container formats a loose CDG track may be paired with. Sandell ships
# .mp3, but commercial discs sometimes carry .m4a/.wav/.ogg/etc.
_CDG_AUDIO_FORMATS = {"mp3", "m4a", "wav", "flac", "ogg", "opus", "aac", "mp2"}


def find_sibling_audio(cdg_file_id, artist, title, brand_code, config=None):
    """Resolve the audio track that belongs to a loose (un-zipped) CDG track.

    Some brands (e.g. Sandell Karaoke) store a CDG's graphics and its audio as
    two separate Drive files in the same folder rather than a single cdg+mp3
    ``.zip``. The divebar index therefore exposes them as two independent track
    rows, and downloading the ``cdg`` row alone yields a silent, useless file —
    so callers pair it with its audio before download.

    Re-runs the public ``search`` for the same song, locates the cdg row by
    ``cdg_file_id`` to read its ``drive_path``, then finds the audio track in the
    SAME brand whose filename (basename minus extension) matches the cdg's. The
    brand + basename match avoids pairing across brands, or across different
    versions of the same song within one brand.

    The caller's artist/title text may not match the index verbatim (e.g. a
    KaraokeNerds-normalized request stores "Jose Feliciano" while the divebar
    index has "José Feliciano", and the lookup service matches by plain
    substring), so a miss on the combined query falls back to title-only and
    then artist-only searches before giving up. Any query that surfaces both
    the cdg row and its brand+basename sibling is trustworthy — the match
    criteria themselves never loosen.

    Returns ``{"file_id": ..., "format": ...}`` for the sibling audio, or
    ``None`` when no companion audio exists (a genuinely orphaned CDG).
    """
    import os

    artist = (artist or "").strip()
    title = (title or "").strip()

    def _stem(p):
        return os.path.splitext(os.path.basename(p or ""))[0].lower().strip()

    queries = []
    for q in (f"{artist} {title}".strip(), title, artist):
        if q and q not in queries:
            queries.append(q)

    for query in queries:
        results = search(query, config=config) or []

        # Flatten the grouped songs into a single list of tracks.
        tracks = [t for song in results for t in song.get("tracks", [])]

        cdg = next((t for t in tracks if t.get("file_id") == cdg_file_id), None)
        if cdg is None:
            # The cdg row didn't come back in this search, so we can't read its
            # drive_path to confirm a basename match here. Try a looser query
            # rather than guess: pairing the wrong song's audio is worse than
            # pairing none.
            continue
        cdg_stem = _stem(cdg.get("drive_path"))
        if not cdg_stem:
            # No usable basename to match on — refuse rather than risk an
            # empty-stem match against another empty-stem track. A different
            # query would return the same row, so don't bother retrying.
            return None

        for t in tracks:
            if t.get("file_id") == cdg_file_id:
                continue
            if (t.get("format") or "").lower() not in _CDG_AUDIO_FORMATS:
                continue
            if brand_code and t.get("brand_code") != brand_code:
                continue
            # Require a basename match so we never pair the wrong audio when a
            # brand has several tracks for one song.
            if _stem(t.get("drive_path")) != cdg_stem:
                continue
            return {"file_id": t.get("file_id"),
                    "format": (t.get("format") or "").lower()}

    return None


def classify_download_url(url):
    """Classify a Divebar download URL as 'gcs' or 'drive'.

    The Divebar Cloud Function returns either a GCS mirror URL (fast,
    direct from the community mirror bucket) or a Google Drive URL
    (slower, original storage) depending on whether the track has been
    mirrored. The kjbox UI surfaces this so the KJ knows what to expect
    for the active download.

    Returns 'gcs', 'drive', or None for unrecognised hosts.
    """
    if not url:
        return None
    from urllib.parse import urlparse
    try:
        host = (urlparse(url).hostname or "").lower()
    except Exception:
        return None
    if host == "storage.googleapis.com" or host.endswith(".storage.googleapis.com"):
        return "gcs"
    if host == "drive.google.com" or host.endswith(".drive.google.com") \
            or host == "drive.usercontent.google.com" \
            or host.endswith(".googleusercontent.com"):
        return "drive"
    return None


def _group_results(results):
    """Group flat search results into songs with tracks."""
    songs = {}
    for r in results:
        artist = r.get("artist") or "Unknown"
        title = r.get("title") or r.get("filename", "Unknown")
        key = (artist.lower().strip(), title.lower().strip())

        if key not in songs:
            songs[key] = {
                "artist": artist,
                "title": title,
                "tracks": [],
            }

        songs[key]["tracks"].append({
            "file_id": r.get("file_id"),
            "brand": r.get("brand", "Unknown"),
            "brand_code": r.get("brand_code"),
            "format": r.get("format", "unknown"),
            "file_size": r.get("file_size"),
            "drive_path": r.get("drive_path"),
            "subfolder": r.get("subfolder", ""),
            "quality": r.get("quality", ""),
            "in_gcs": r.get("in_gcs", False),
        })

    return list(songs.values())


def _format_file_size(size_bytes):
    """Format file size in human-readable form."""
    if not size_bytes:
        return ""
    if size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.0f} KB"
    return f"{size_bytes / (1024 * 1024):.1f} MB"
