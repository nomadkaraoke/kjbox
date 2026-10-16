"""Shared fixtures: the real 2026-10-08 night (Night Log + 23 Camera Uploads filenames)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from nightclips import kjbox, match
from nightclips.clips import Clip, clip_id, parse_clip_time

FIXTURES = Path(__file__).parent / "fixtures"
NIGHT = "2026-10-08"


@pytest.fixture
def night_log() -> dict:
    """Real ``/history/night?night=2026-10-08`` response."""
    return json.loads((FIXTURES / "night_2026-10-08.json").read_text())


@pytest.fixture
def clip_names() -> list[str]:
    return [l.strip() for l in (FIXTURES / "clips_2026-10-08.txt").read_text().splitlines() if l.strip()]


def make_clips(names, local=True) -> list[Clip]:
    return [
        Clip(id=clip_id(n), filename=n, path=f"/fake/{n}", start=parse_clip_time(n), local=local, duration=20.0)
        for n in names
    ]


def synth_consent(songs: list[dict]) -> list[dict]:
    """Night-media-shaped songs with a deterministic mix of yes / no / unknown consent.

    Performers are numbered in order of first appearance; ``n % 5``: 0-2 yes, 3 no, 4 unknown.
    Every yes performer with an even number gets an Instagram handle. One extra rule
    simulates a per-song "no" override: Mija said no to "Versace On The Floor" only.
    """
    order: dict[str, int] = {}
    for s in songs:
        for p in s["performers"]:
            order.setdefault(p["name"], len(order))
    out = []
    for s in songs:
        perfs = []
        for p in s["performers"]:
            n = order[p["name"]]
            consent = ["yes", "yes", "yes", "no", "unknown"][n % 5]
            reason = "night_default" if consent != "unknown" else "unknown"
            if p["name"] == "Mija" and "Versace" in s["song"]:
                consent, reason = "no", "song_override_singer"
            handle = (p["name"].lower().replace(" ", "").replace(".", "") + "_sings") if consent == "yes" and n % 2 == 0 else None
            perfs.append({"name": p["name"], "consent": consent, "reason": reason, "instagram": handle})
        out.append({**s, "performers": perfs, "postable": all(p["consent"] == "yes" for p in perfs)})
    return out


@pytest.fixture
def songs_unknown(night_log) -> list[dict]:
    """Fallback-shaped songs (everyone unknown), indexed."""
    return match.index_songs(kjbox.songs_from_night_log(night_log))


@pytest.fixture
def songs_media(night_log) -> list[dict]:
    """Night-media-shaped songs with synthesized consent, indexed."""
    return match.index_songs(synth_consent(kjbox.songs_from_night_log(night_log)))


@pytest.fixture
def night_media_payload(night_log) -> dict:
    return {"night": NIGHT, "consent_source": "legacy", "songs": synth_consent(kjbox.songs_from_night_log(night_log))}


@pytest.fixture
def night_media_1015() -> dict:
    """Real 2026-10-15 night with ``started_at`` (play start) and ``sung_at`` (Done time).

    Songs from ``/history/night-media`` with consent blanked; ``started_at`` from the
    kjbox play_events. Five songs were marked Done without being played (no start).
    """
    return json.loads((FIXTURES / "night_media_2026-10-15.json").read_text())


@pytest.fixture
def clip_names_1015() -> list[str]:
    return [l.strip() for l in (FIXTURES / "clips_2026-10-15.txt").read_text().splitlines() if l.strip()]
