"""Karaoke Nerds search — backed by OUR OWN catalog copies, not a live scrape.

Historically this module scraped karaokenerds.com/Search on every query (including
the public, singer-facing search on sing.nomadkaraoke.com). KaraokeNerds now rate-
limits (429) aggressively, and scraping their site per singer keystroke was the main
driver. This now queries our own copies of BOTH KN catalogs through the Divebar
Cloud Function (``divebar.kn_search``, one HTTP call / one BigQuery job), refreshed
daily by the one authorized `kn-data-sync` export job. Nothing here touches
karaokenerds.com.

Community rows (`karaokenerds_community`) are the free, web-playable tracks and
carry a YouTube URL. Full-catalog rows (`karaokenerds_raw`) list EVERY release KN
knows about — including commercial disc brands with no web version — and merge in
as ``is_community=False`` tracks with ``youtube_url=None`` (the same shape the old
scrape produced for them), so a song that only exists on commercial discs still
shows up instead of "No results found".

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


def search(query, config=None, mirror=None):
    """Search our KaraokeNerds catalog copies (community + full).

    Returns a list of song dicts, each with title, artist, and a tracks list.
    Community/web tracks come first per song (``is_community=True`` with a
    playable ``youtube_url``); commercial disc releases from the full catalog
    follow (``is_community=False``, ``youtube_url=None``). The catalogs store
    brand *codes*; the human ``brand_name`` is resolved from them for display,
    and version ranking resolves the canonical brand from ``brand_code`` +
    ``is_community``.

    When a fresh local catalog ``mirror`` is available the search runs
    entirely on-box (<50ms, offline-capable); otherwise it falls back to the
    Divebar Cloud Function (one BigQuery job, ~1.5s, TTL-cached).
    """
    data = None
    if mirror is not None:
        try:
            if mirror.is_usable():
                data = mirror.kn_search(query)
        except Exception as e:  # noqa: BLE001 — mirror trouble -> remote path
            log_message(f"Catalog mirror KN search error: {e}", config)
            data = None
    if data is None:
        try:
            data = divebar.kn_search(query, config=config)
        except Exception as e:  # noqa: BLE001 — best-effort; never break search
            log_message(f"Karaoke Nerds search error: {e}", config)
            return []

    songs = _group_results(data.get("community") or [])
    _merge_full_catalog(songs, data.get("full") or [])
    return songs


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


def _merge_full_catalog(songs, full_rows):
    """Fold full-catalog rows into the community-grouped ``songs`` in place.

    Full rows are ``{artist, title, brands}`` where ``brands`` is KN's
    comma-separated brand-code list for that song — it includes the community
    codes too, so any code already present on the song (as a playable community
    track) is skipped. The remaining codes are appended as commercial disc
    releases: ``is_community=False`` and ``youtube_url=None`` (nothing to
    download — they only exist on physical/commercial media).
    """
    by_key = {(s["artist"].lower(), s["title"].lower()): s for s in songs}
    for r in full_rows:
        artist = (r.get("artist") or "").strip()
        title = (r.get("title") or "").strip()
        if not title:
            continue

        key = (artist.lower(), title.lower())
        song = by_key.get(key)
        if song is None:
            song = {"title": title, "artist": artist, "tracks": []}
            by_key[key] = song
            songs.append(song)

        have = {(t.get("brand_code") or "").upper() for t in song["tracks"]}
        for code in (r.get("brands") or "").split(","):
            code = code.strip()
            if not code or code.upper() in have:
                continue
            have.add(code.upper())
            song["tracks"].append({
                "brand_name": version_priority.display_name_for(code),
                "brand_code": code,
                "youtube_url": None,
                "is_community": False,
            })


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
