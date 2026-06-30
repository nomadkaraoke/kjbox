"""Bare .cdg (graphics-only) classification + gate behaviour."""
import os

import playability as pl


def test_classify_cdg_bare():
    assert pl.classify_kind("/x/song.cdg") == "cdg_bare"
    assert pl.classify_kind("/x/song.CDG") == "cdg_bare"
    assert pl.classify_kind("/x/song.zip") == "cdg_zip"  # zip stays cdg_zip


def test_sibling_cdg_audio_same_stem(tmp_path):
    cdg = tmp_path / "ABBA - Dancing Queen.cdg"
    mp3 = tmp_path / "ABBA - Dancing Queen.mp3"
    cdg.write_bytes(b"\0" * 10)
    mp3.write_bytes(b"\0" * 10)
    assert pl.sibling_cdg_audio(str(cdg)) == str(mp3)


def test_sibling_cdg_audio_ignores_other_stem_and_nonaudio(tmp_path):
    cdg = tmp_path / "song.cdg"
    cdg.write_bytes(b"\0" * 10)
    (tmp_path / "other.mp3").write_bytes(b"\0")      # different stem
    (tmp_path / "song.txt").write_bytes(b"\0")       # same stem, not audio
    assert pl.sibling_cdg_audio(str(cdg)) is None


def test_sibling_cdg_audio_case_insensitive_ext(tmp_path):
    cdg = tmp_path / "x.cdg"
    cdg.write_bytes(b"\0")
    m4a = tmp_path / "x.M4A"
    m4a.write_bytes(b"\0")
    assert pl.sibling_cdg_audio(str(cdg)) == str(m4a)


def test_gate_rejects_bare_cdg_without_audio(tmp_path):
    cdg = tmp_path / "lonely.cdg"
    cdg.write_bytes(b"\0" * 100)
    checker = pl.PlayabilityChecker(config={})
    res = checker.check(str(cdg), renderers=(), depth="quick")
    assert res.verdict.get("overall_ok") is False
    assert any("no audio" in r.lower() for r in res.verdict.get("reasons") or [])


def test_gate_allows_bare_cdg_with_sibling_audio(tmp_path):
    cdg = tmp_path / "paired.cdg"
    mp3 = tmp_path / "paired.mp3"
    cdg.write_bytes(b"\0" * 100)
    mp3.write_bytes(b"\0" * 100)
    checker = pl.PlayabilityChecker(config={})
    res = checker.check(str(cdg), renderers=(), depth="quick")
    assert res.verdict.get("overall_ok") is True
