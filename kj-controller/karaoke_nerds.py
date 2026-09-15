"""Karaoke Nerds search — backed by OUR OWN community catalog, not a live scrape.

Historically this module scraped karaokenerds.com/Search on every query (including
the public, singer-facing search on sing.nomadkaraoke.com). KaraokeNerds now rate-
limits (429) aggressively, and scraping their site per singer keystroke was the main
driver. This now queries our own `karaokenerds_community` table through the Divebar
Cloud Function (``divebar.kn_community_search``), which is refreshed daily by the one
authorized `kn-data-sync` export job. Nothing here touches karaokenerds.com.

The public ``search()`` return shape is unchanged, so ``routes.unified_search`` and
the ``/sing/search`` blueprint need no changes:

    [{"title", "artist", "tracks": [{"brand_name", "brand_code", "youtube_url",
                                     "is_community"}]}]
"""

import re

import divebar
import version_priority
from utils import log_message

# Extract a YouTube video id from any of KaraokeNerds' stored watch-URL forms
# (youtu.be/<id>, /watch?v=<id>, /embed/<id>, /shorts/<id>) so we emit the
# canonical youtube.com/watch?v=<id> the old scrape produced (stable media ids).
_YT_ID_RE = re.compile(
    r"(?:youtu\.be/|youtube\.com/(?:watch\?(?:[^&]*&)*v=|embed/|shorts/|v/))"
    r"([A-Za-z0-9_-]{11})"
)


def search(query, config=None):
    """Search our community karaoke catalog for web-playable tracks.

    Returns a list of song dicts, each with title, artist, and a tracks list.
    Every returned track is a community/web version (that is what the catalog
    holds), so ``is_community`` is always True. The catalog stores the brand
    *code*; the human ``brand_name`` is resolved from it for display, and version
    ranking resolves the canonical brand from ``brand_code`` + ``is_community``.
    """
    try:
        rows = divebar.kn_community_search(query, config=config)
    except Exception as e:  # noqa: BLE001 — best-effort; never break search
        log_message(f"Karaoke Nerds community search error: {e}", config)
        return []

    return _group_results(rows)


def _group_results(rows):
    """Group flat community rows into songs with a deduped tracks list.

    Rows are ``{artist, title, brand, watch}``. Songs are keyed by
    (artist, title) case-insensitively; tracks are deduped by (brand, youtube_url).
    """
    songs = {}
    for r in rows:
        artist = (r.get("artist") or "").strip()
        title = (r.get("title") or "").strip()
        if not title:
            continue
        brand_code = (r.get("brand") or "").strip()
        youtube_url = _normalize_youtube_url(r.get("watch") or "") or None

        key = (artist.lower(), title.lower())
        song = songs.get(key)
        if song is None:
            song = {"title": title, "artist": artist, "tracks": [], "_seen": set()}
            songs[key] = song

        dedup = (brand_code, youtube_url)
        if dedup in song["_seen"]:
            continue
        song["_seen"].add(dedup)
        song["tracks"].append({
            "brand_name": version_priority.display_name_for(brand_code),
            "brand_code": brand_code,
            "youtube_url": youtube_url,
            "is_community": True,
        })

    result = list(songs.values())
    for song in result:
        song.pop("_seen", None)
    return result


def _normalize_youtube_url(url):
    """Canonicalize a watch URL to youtube.com/watch?v=<id>.

    Returns the canonical URL, or the stripped original if no 11-char id parses
    (never fabricates), or "" when empty.
    """
    url = (url or "").strip()
    if not url:
        return ""
    m = _YT_ID_RE.search(url)
    if m:
        return f"https://www.youtube.com/watch?v={m.group(1)}"
    return url
