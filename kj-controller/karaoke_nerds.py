"""Karaoke Nerds search integration — scrapes karaokenerds.com for web-only tracks."""

import re
from urllib.parse import urlencode

import requests
from bs4 import BeautifulSoup

from utils import log_message

SEARCH_URL = "https://karaokenerds.com/Search"
REQUEST_TIMEOUT = 8
USER_AGENT = "NomadKJ/1.0"


def search(query, config=None):
    """Search karaokenerds.com for web-only karaoke tracks.

    Returns a list of song dicts, each with title, artist, and tracks list.
    Tracks include brand info, YouTube URL, and community status.
    """
    params = urlencode({"query": query, "webFilter": "OnlyWeb"})
    url = f"{SEARCH_URL}?{params}"

    try:
        resp = requests.get(
            url,
            headers={"User-Agent": USER_AGENT},
            timeout=REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
    except Exception as e:
        log_message(f"Karaoke Nerds search error: {e}", config)
        return []

    return parse_results(resp.text, config)


def parse_results(html, config=None):
    """Parse karaokenerds.com search results HTML into structured data."""
    soup = BeautifulSoup(html, "html.parser")
    table = soup.find("table")
    if not table:
        return []

    tbody = table.find("tbody")
    if not tbody:
        return []

    songs = []
    rows = tbody.find_all("tr", recursive=False)

    i = 0
    while i < len(rows):
        row = rows[i]

        # Song rows have class "group"
        if "group" not in row.get("class", []):
            i += 1
            continue

        # Extract title and artist from the song row
        cells = row.find_all("td")
        if len(cells) < 3:
            i += 1
            continue

        title_link = cells[0].find("a")
        artist_link = cells[1].find("a")
        title = title_link.get_text(strip=True) if title_link else ""
        artist = artist_link.get_text(strip=True) if artist_link else ""

        # The next row should be the details row with tracks
        tracks = []
        if i + 1 < len(rows):
            details_row = rows[i + 1]
            if "details" in details_row.get("class", []):
                tracks = _parse_tracks(details_row)
                i += 2
            else:
                i += 1
        else:
            i += 1

        if title:
            songs.append({
                "title": title,
                "artist": artist,
                "tracks": tracks,
            })

    return songs


def _parse_tracks(details_row):
    """Parse track list items from a details row."""
    tracks = []
    for li in details_row.find_all("li", class_="track"):
        track = _parse_single_track(li)
        if track:
            tracks.append(track)
    return tracks


def _parse_single_track(li):
    """Parse a single track <li> element."""
    # Brand name: first <a> in the li
    brand_link = li.find("a")
    brand_name = brand_link.get_text(strip=True) if brand_link else ""

    # Brand code: text inside .badge span
    badge = li.find("span", class_="badge")
    brand_code = ""
    if badge:
        # Get text content, excluding child img text
        brand_code = badge.get_text(strip=True)

    # YouTube URL: link containing youtube.com
    youtube_url = None
    for a in li.find_all("a", href=True):
        href = a["href"]
        if "youtube.com" in href:
            youtube_url = _clean_youtube_url(href)
            break

    # Community: presence of img.check
    is_community = bool(li.find("img", class_="check"))

    if not youtube_url:
        return None

    return {
        "brand_name": brand_name,
        "brand_code": brand_code,
        "youtube_url": youtube_url,
        "is_community": is_community,
    }


def _clean_youtube_url(url):
    """Strip playlist params from YouTube URLs, keep just the video URL."""
    return re.sub(r"&list=[^&]*", "", url)
