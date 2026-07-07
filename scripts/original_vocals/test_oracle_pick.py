#!/usr/bin/env python3
"""Tests for oracle_pick.py: winner selection by mean vocal volume."""

from oracle_pick import Candidate, pick_winner


def c(name, ext, db, size=1000):
    """Helper: create a Candidate."""
    return Candidate(path=f"F/{name}", name=name, ext=ext, size=size, mean_db=db)


def test_picks_loud_original_over_silent_instrumental():
    """Loud original (full mix) beats silent instrumental. High confidence (31.2 dB margin)."""
    r = pick_winner([
        c("Idlewild - Little Discourage.mp3", "mp3", -53.2),  # CDG instrumental (silent)
        c("01 Little Discourage.flac", "flac", -22.0),  # real original
    ])
    assert r.winner.name == "01 Little Discourage.flac"
    assert r.verdict == "confirmed"
    assert r.confidence == "high"  # 31 dB margin
    assert round(r.margin_db, 1) == 31.2


def test_all_dead_is_no_source():
    """All candidates below floor → no_source, confidence=none."""
    r = pick_winner([
        c("a.mp3", "mp3", -60.0),
        c("b.mp3", "mp3", -55.0),
    ])
    assert r.winner is None
    assert r.verdict == "no_source"
    assert r.confidence == "none"
    assert r.winner_db is None
    assert r.margin_db is None


def test_single_candidate_above_floor():
    """Single candidate above floor → confirmed, but confidence=low (no runner-up to compare)."""
    r = pick_winner([
        c("a.mp3", "mp3", -35.0),
    ])
    assert r.winner.name == "a.mp3"
    assert r.verdict == "confirmed"
    assert r.confidence == "low"
    assert r.winner_db == -35.0
    assert r.runnerup_db is None
    assert r.margin_db is None


def test_close_margin_low_confidence():
    """Two candidates within margin_db → confidence=low (needs human check)."""
    r = pick_winner([
        c("x.mp3", "mp3", -30.0),
        c("y.mp3", "mp3", -26.0),  # only 4 dB margin, threshold is 6 dB
    ])
    assert r.winner.name == "y.mp3"
    assert r.verdict == "confirmed"
    assert r.confidence == "low"
    assert r.winner_db == -26.0
    assert r.runnerup_db == -30.0
    assert r.margin_db == 4.0


def test_tie_uses_format_and_size():
    """Two candidates within tie-epsilon → format rank tiebreak, then size."""
    r = pick_winner([
        c("a.mp3", "mp3", -25.5, size=2000),    # mp3=rank 3, size 2000
        c("b.flac", "flac", -25.8, size=1000),  # flac=rank 6, size 1000, within 1.5dB
    ])
    # Both within 1.5 dB tie epsilon; flac (rank 6) beats mp3 (rank 3)
    assert r.winner.name == "b.flac"
    assert r.verdict == "confirmed"
    assert r.confidence == "high"
    assert round(r.margin_db, 1) == 0.3  # actual margin is 0.3 dB but treated as tie/tiebreak
