"""Attribute each clip to the song being performed when recording started.

Rule: clip start ``t`` -> the song with the greatest ``sung_at <= t`` (its successor,
if any, necessarily started after ``t``). Flag ``check_singer`` when the attribution
is shaky:

* ``near_change`` — ``t`` is within 15s after a song change (the phone may have been
  raised for the previous singer, or the next singer was slow to start);
* ``stale_song`` — the matched song started more than 8 minutes before ``t`` (songs
  rarely run that long, so a gap/break or an unlogged song is likely).

Clips before the first song are ``unassigned``.
"""

from __future__ import annotations

import bisect
import datetime as dt

from .clips import parse_ts

NEAR_CHANGE = dt.timedelta(seconds=15)
STALE_SONG = dt.timedelta(minutes=8)
ADJACENT = 2  # songs either side offered in the reassign dropdown


def song_key(song: dict) -> str:
    """Stable per-night key: entry ids are recycled across nights, never within one sung_at."""
    return f"{song.get('entry_id')}@{song['sung_at']}"


def index_songs(songs: list[dict]) -> list[dict]:
    """Sort by sung_at and add ``idx``/``key``/``start`` fields (returns new dicts)."""
    out = []
    for i, s in enumerate(sorted(songs, key=lambda s: s["sung_at"])):
        out.append({**s, "idx": i, "key": song_key(s)})
    return out


def match_time(t: dt.datetime, starts: list[dt.datetime]) -> tuple[int | None, list[str]]:
    """Index of the song in progress at ``t`` plus flag reasons."""
    i = bisect.bisect_right(starts, t) - 1
    if i < 0:
        return None, []
    flags = []
    since = t - starts[i]
    if since <= NEAR_CHANGE:
        flags.append("near_change")
    if since > STALE_SONG:
        flags.append("stale_song")
    return i, flags


def match_clips(clips: list, songs: list[dict]) -> dict[str, dict]:
    """``{clip_id: {song_idx, auto_song_idx, flags, offset_s, candidates}}`` for indexed songs."""
    starts = [parse_ts(s["sung_at"]) for s in songs]
    result = {}
    for c in clips:
        i, flags = match_time(c.start, starts)
        result[c.id] = {
            "song_idx": i,
            "auto_song_idx": i,
            "flags": flags,
            "offset_s": None if i is None else int((c.start - starts[i]).total_seconds()),
            "candidates": candidate_songs(len(songs), i),
        }
    return result


def candidate_songs(n: int, i: int | None) -> list[int]:
    """Song indices offered for manual reassignment: the match plus neighbours."""
    if n == 0:
        return []
    if i is None:
        # Before the first song: offer the first few.
        return list(range(0, min(n, ADJACENT + 1)))
    lo, hi = max(0, i - ADJACENT), min(n - 1, i + ADJACENT)
    return list(range(lo, hi + 1))
