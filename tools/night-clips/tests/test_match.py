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


# --- 2026-10-15: sung_at is the Done time (song END); started_at is the play start ---

EXPECTED_1015 = {
    # Filmed during Lulu K's Ana Ng (played 22:13:31), right after Wonderwall was Done
    # at 22:13:13 — matching on sung_at tagged it Ashlee A & Jude.
    "2026-10-15 22.14.47.mp4": ("Lulu K", "Ana Ng - They Might Be Giants"),
    "2026-10-15 21.48.34.mp4": ("Ashlee A", "Rock Me - One Direction"),
    "2026-10-15 23.06.37.mp4": ("Ana", "Bulletproof - La Roux"),
    # Medicina De Amor was on stage; three Juan G songs were ticked Done unplayed at 02:02.
    "2026-10-16 02.05.56.mp4": ("Juan G", "Medicina De Amor - Raulin Rodriguez"),
}


def test_1015_clips_match_song_playing_not_song_just_finished(night_media_1015, clip_names_1015):
    songs = match.index_songs(night_media_1015["songs"])
    assert len(songs) == len(night_media_1015["songs"]) - 5  # unplayed Done entries dropped
    clips = make_clips(clip_names_1015)
    res = match.match_clips(clips, songs)
    for c in clips:
        s = songs[res[c.id]["song_idx"]]
        assert parse_ts(s["started_at"]) <= c.start, c.filename
        assert "after_song_end" not in res[c.id]["flags"], c.filename
    for name, (singer, song) in EXPECTED_1015.items():
        s = songs[res[clip_id(name)]["song_idx"]]
        assert (s["singer"], s["song"]) == (singer, song), name


def test_sung_at_only_falls_back_to_old_behaviour(songs_unknown):
    # Old kjbox / reconstructed nights: no started_at anywhere -> nothing dropped.
    assert match.played_songs(songs_unknown) == songs_unknown
    assert all(s["start"] == s["sung_at"] for s in songs_unknown)


def test_after_song_end_flag():
    starts = _starts((21, 0, 0), (21, 4, 0))
    ends = [dt.datetime(2026, 10, 8, 21, 3, 50), dt.datetime(2026, 10, 8, 21, 6, 0)]
    i, flags = match.match_time(dt.datetime(2026, 10, 8, 21, 6, 31), starts, ends)
    assert i == 1 and flags == ["after_song_end"]
    i, flags = match.match_time(dt.datetime(2026, 10, 8, 21, 6, 30), starts, ends)
    assert flags == []
