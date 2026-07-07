#!/usr/bin/env python3
"""Oracle pick logic: select the best candidate original vocal file by mean volume.

Primary signal: highest MEAN vocal volume. A real full mix separates loud
vocals; an instrumental separates near-silence. Filename is only a tiebreak
(format rank via classify.FMT_RANK, then size).

verdict: "confirmed" if at least one candidate >= floor_db; "no_source" if all below floor.
confidence: "none" if no_source; "low" if single candidate, runner-up within margin_db, or margin < margin_db;
            "high" only when margin >= margin_db.
"""
from __future__ import annotations

from dataclasses import dataclass

from classify import FMT_RANK

_TIE_EPS_DB = 1.5  # Candidates within this loudness are treated as tied; use format/size tiebreak.


@dataclass
class Candidate:
    """A single candidate original vocal file."""
    path: str
    name: str
    ext: str
    size: int
    mean_db: float | None


@dataclass
class PickResult:
    """Result of pick_winner()."""
    winner: Candidate | None
    winner_db: float | None
    runnerup_db: float | None
    margin_db: float | None
    confidence: str
    verdict: str


def _sort_key(c: Candidate):
    """Sort key: (db normalized to tie-epsilon bucket, format rank, size).
    Used with reverse=True, so larger values sort first (descending on all fields)."""
    db = c.mean_db if c.mean_db is not None else -999.0
    # Normalize db to tie-epsilon bucket so candidates within _TIE_EPS_DB are grouped.
    db_bucket = round(db / _TIE_EPS_DB) * _TIE_EPS_DB
    return (db_bucket, FMT_RANK.get(c.ext, 0), c.size)


def pick_winner(
    cands: list[Candidate], floor_db: float = -40.0, margin_db: float = 6.0
) -> PickResult:
    """Pick the best candidate by mean vocal volume.

    Args:
        cands: List of candidates with measured mean_db.
        floor_db: Minimum acceptable mean_db to consider a candidate "confirmed" (-40 dB default).
        margin_db: Minimum margin over runner-up for "high" confidence (6 dB default).

    Returns:
        PickResult with winner, confidence, and verdict.
    """
    # no_source iff NO candidate is at/above the floor. Use the LOUDEST value, not
    # the format-sorted winner (which can be slightly quieter within a tie-bucket).
    loudest_db = max((c.mean_db for c in cands if c.mean_db is not None), default=None)
    if loudest_db is None or loudest_db < floor_db:
        return PickResult(None, None, None, None, "none", "no_source")

    # Sort all candidates by (db bucket, format rank, size).
    # Descending on db and format rank, ascending on size.
    sorted_cands = sorted(cands, key=_sort_key, reverse=True)
    winner = sorted_cands[0]
    runner_up = sorted_cands[1] if len(sorted_cands) > 1 else None

    # Verdict is confirmed.
    winner_db = winner.mean_db
    runnerup_db = runner_up.mean_db if runner_up else None

    # Calculate margin and confidence.
    if runnerup_db is None:
        # Single candidate: confirmed but low confidence (no comparison).
        confidence = "low"
        margin = None
    else:
        margin = abs(winner_db - runnerup_db)
        # High confidence only when the winner clearly beats the runner-up.
        # A near-tie (small margin) is the MOST ambiguous case (possibly two real
        # originals, e.g. studio vs live) -> low, so a human verifies. _TIE_EPS_DB
        # affects only the sort tiebreak (which file wins), never confidence.
        confidence = "high" if margin >= margin_db else "low"

    return PickResult(
        winner=winner,
        winner_db=winner_db,
        runnerup_db=runnerup_db,
        margin_db=margin,
        confidence=confidence,
        verdict="confirmed",
    )
