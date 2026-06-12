"""
Divebar Karaoke catalog client.

Calls the Divebar Lookup API (Cloud Function) to search the indexed
Divebar Google Drive catalog and look up KN cross-references.
"""

import logging
import requests

logger = logging.getLogger(__name__)

# Default timeout for API calls
_TIMEOUT = 10


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
        return _group_results(data.get("results", []))

    except requests.Timeout:
        logger.warning("Divebar search timed out")
        return []
    except requests.RequestException as e:
        logger.error("Divebar search failed: %s", e)
        return []


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
