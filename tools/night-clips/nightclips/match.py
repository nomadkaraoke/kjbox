"""Attribute each clip to the song being performed when recording started.

Rule: clip start ``t`` -> the song with the greatest start ``<= t`` (its successor,
if any, necessarily started after ``t``). A song's start is ``started_at`` (when the
kjbox began playing it). ``sung_at`` is when the KJ marked it Done, i.e. the song's
END on nights since the Night Log shipped, so it's only a start-time fallback for
older reconstructed nights (where it came from the play log). Flag ``check_singer``
when the attribution is shaky:

* ``near_change`` — ``t`` is within 15s after a song change (the phone may have been
  raised for the previous singer, or the next singer was slow to start);
* ``stale_song`` — the matched song started more than 8 minutes before ``t`` (songs
  rarely run that long, so a gap/break or an unlogged song is likely);
* ``after_song_end`` — ``t`` is more than 30s after the matched song was marked Done
  (something unlogged was on stage, e.g. an entry deleted after it played).

Clips before the first song are ``unassigned``.
"""

from __future__ import annotations

import bisect
import datetime as dt

from .clips import parse_ts

NEAR_CHANGE = dt.timedelta(seconds=15)
STALE_SONG = dt.timedelta(minutes=8)
AFTER_END = dt.timedelta(seconds=30)
ADJACENT = 2  # songs either side offered in the reassign dropdown


def song_key(song: dict) -> str:
    """Stable per-night key: entry ids are recycled across nights, never within one sung_at."""
    return f"{song.get('entry_id')}@{song['sung_at']}"


def song_start(song: dict) -> str:
    """When the song began: the play start, else ``sung_at`` (older kjbox / reconstructed nights)."""
    return song.get("started_at") or song["sung_at"]


def song_end(song: dict) -> str | None:
    """When the song was marked Done, if we know it separately from its start."""
    if song.get("started_at") and song.get("sung_at") and song["sung_at"] > song["started_at"]:
        return song["sung_at"]
    return None


def played_songs(songs: list[dict]) -> list[dict]:
    """Drop songs marked Done without ever playing, when the kjbox reports play starts.

    Their ``sung_at`` is just when the KJ ticked them off, so as a start-time fallback
    they would steal clips from the song actually on stage.
    """
    if any(s.get("started_at") for s in songs):
        return [s for s in songs if s.get("started_at")]
    return songs


def index_songs(songs: list[dict]) -> list[dict]:
    """Sort by start and add ``idx``/``key``/``start`` fields (returns new dicts)."""
    out = []
    for i, s in enumerate(sorted(played_songs(songs), key=song_start)):
        out.append({**s, "idx": i, "key": song_key(s), "start": song_start(s)})
    return out


def match_time(
    t: dt.datetime, starts: list[dt.datetime], ends: list[dt.datetime | None] | None = None
) -> tuple[int | None, list[str]]:
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
    end = ends[i] if ends else None
    if end is not None and t > end + AFTER_END:
        flags.append("after_song_end")
    return i, flags


def match_clips(clips: list, songs: list[dict]) -> dict[str, dict]:
    """``{clip_id: {song_idx, auto_song_idx, flags, offset_s, candidates}}`` for indexed songs."""
    starts = [parse_ts(song_start(s)) for s in songs]
    ends = [parse_ts(e) if (e := song_end(s)) else None for s in songs]
    result = {}
    for c in clips:
        i, flags = match_time(c.start, starts, ends)
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
