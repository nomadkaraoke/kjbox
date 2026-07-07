"""Candidate enumeration: list the viable original-audio files in a messy
Tracks-Organized folder, dropping only unambiguous derived artifacts (existing
separation stems, karaoke renders).

Reuses `classify.AudioFile`, `classify.is_excluded`, `classify.AUDIO_EXT`.

The oracle will self-eliminate CDG-backfill instrumentals (which have names like
"Artist - Title.mp3" but measure near-silent vocals), so we KEEP these here —
they're fed through to the oracle self-judging step.
"""
from __future__ import annotations

import os
from classify import AudioFile, is_excluded, AUDIO_EXT


def filter_candidates(files: list[AudioFile]) -> list[AudioFile]:
    return [f for f in files if f.ext in AUDIO_EXT and not is_excluded(f.name.lower())]


def enumerate_candidates(folder: str) -> list[AudioFile]:
    out: list[AudioFile] = []
    for root, _dirs, names in os.walk(folder):
        for name in names:
            ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
            if ext not in AUDIO_EXT:
                continue

            path = os.path.join(root, name)
            try:
                size = os.path.getsize(path)
            except OSError:
                size = 0
            out.append(AudioFile(size=size, path=path, name=name, ext=ext))
    return filter_candidates(out)
