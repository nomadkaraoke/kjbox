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
from utils import log_message


def search(query, config=None):
    """Search our community karaoke catalog for web-playable tracks.

    Returns a list of song dicts, each with title, artist, and a tracks list.
    Every returned track is a community/web version (that is what the catalog
    holds), so ``is_community`` is always True. ``brand_code`` is unknown from
    this catalog (only the brand name is stored) and is left blank — version
    ranking resolves the canonical brand from ``brand_name`` + ``is_community``.
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
        brand_name = (r.get("brand") or "").strip()
        youtube_url = _clean_youtube_url(r.get("watch") or "") or None

        key = (artist.lower(), title.lower())
        song = songs.get(key)
        if song is None:
            song = {"title": title, "artist": artist, "tracks": [], "_seen": set()}
            songs[key] = song

        dedup = (brand_name, youtube_url)
        if dedup in song["_seen"]:
            continue
        song["_seen"].add(dedup)
        song["tracks"].append({
            "brand_name": brand_name,
            "brand_code": "",
            "youtube_url": youtube_url,
            "is_community": True,
        })

    result = list(songs.values())
    for song in result:
        song.pop("_seen", None)
    return result


def _clean_youtube_url(url):
    """Strip playlist params from YouTube URLs, keep just the video URL."""
    return re.sub(r"&list=[^&]*", "", url)
