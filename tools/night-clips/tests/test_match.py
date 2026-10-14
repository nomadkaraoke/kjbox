import datetime as dt

from nightclips import match
from nightclips.clips import clip_id, parse_ts

from .conftest import make_clips

# (clip filename, singer, song) spot checks verified by hand against the Night Log.
EXPECTED = {
    "2026-10-08 21.45.56.mp4": ("Mars W", "Girls Just Wanna Have Fun - Cyndi Lauper"),
    "2026-10-08 22.13.56.mp4": ("Shylo R.", "Gethsemane - Sleep Token"),
    "2026-10-08 23.28.19.mp4": ("Katie B", "I'm Low On Gas And You Need A Jacket - Pierce The Veil"),
    "2026-10-08 23.47.17.mp4": ("Bailey & Andrés & miggy", "Ode To Sleep - Twenty One Pilots"),
    "2026-10-09 01.34.10.mp4": ("Mija", "Versace On The Floor - Bruno Mars"),
    "2026-10-09 01.48.37.mp4": ("Mija", "Make You Feel My Love - Adele"),
    "2026-10-09 01.52.33.mp4": ("Emerson", "Rihanna & Mikky Ekko (Duet) - Stay"),
}


def test_all_23_real_clips_match_song_in_progress(clip_names, songs_unknown):
    assert len(clip_names) == 23
    clips = make_clips(clip_names)
    res = match.match_clips(clips, songs_unknown)
    starts = [parse_ts(s["sung_at"]) for s in songs_unknown]
    for c in clips:
        m = res[c.id]
        assert m["song_idx"] is not None, c.filename
        # Brute force: greatest sung_at <= clip start.
        expected = max(i for i, t in enumerate(starts) if t <= c.start)
        assert m["song_idx"] == expected, c.filename
        # ...and the next song (if any) started after the clip.
        if expected + 1 < len(starts):
            assert starts[expected + 1] > c.start
        assert 44 <= m["offset_s"] <= 253, (c.filename, m["offset_s"])
        assert m["flags"] == [], (c.filename, m["flags"])
    for name, (singer, song) in EXPECTED.items():
        s = songs_unknown[res[clip_id(name)]["song_idx"]]
        assert (s["singer"], s["song"]) == (singer, song), name


def test_songs_sorted_and_keyed(songs_unknown):
    assert [s["idx"] for s in songs_unknown] == list(range(len(songs_unknown)))
    assert all(a["sung_at"] <= b["sung_at"] for a, b in zip(songs_unknown, songs_unknown[1:]))
    assert len({s["key"] for s in songs_unknown}) == len(songs_unknown)


def _starts(*hms):
    return [dt.datetime(2026, 10, 8, *t) for t in hms]


def test_near_change_flag():
    starts = _starts((21, 0, 0), (21, 4, 0))
    i, flags = match.match_time(dt.datetime(2026, 10, 8, 21, 4, 15), starts)
    assert i == 1 and flags == ["near_change"]
    i, flags = match.match_time(dt.datetime(2026, 10, 8, 21, 4, 16), starts)
    assert i == 1 and flags == []


def test_stale_song_flag():
    starts = _starts((21, 0, 0), (21, 4, 0))
    i, flags = match.match_time(dt.datetime(2026, 10, 8, 21, 12, 1), starts)
    assert i == 1 and flags == ["stale_song"]
    i, flags = match.match_time(dt.datetime(2026, 10, 8, 21, 12, 0), starts)
    assert flags == []


def test_before_first_song_is_unassigned():
    starts = _starts((21, 0, 0))
    assert match.match_time(dt.datetime(2026, 10, 8, 20, 55), starts) == (None, [])


def test_exactly_at_song_start_matches_that_song():
    starts = _starts((21, 0, 0), (21, 4, 0))
    i, flags = match.match_time(dt.datetime(2026, 10, 8, 21, 4, 0), starts)
    assert i == 1 and flags == ["near_change"]


def test_candidates_are_adjacent_songs():
    assert match.candidate_songs(10, 5) == [3, 4, 5, 6, 7]
    assert match.candidate_songs(10, 0) == [0, 1, 2]
    assert match.candidate_songs(10, 9) == [7, 8, 9]
    assert match.candidate_songs(10, None) == [0, 1, 2]
    assert match.candidate_songs(0, None) == []
