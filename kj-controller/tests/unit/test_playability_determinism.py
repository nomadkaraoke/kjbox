"""Tests for the deterministic playability fixes (June 2026): recoverable
decoder warnings no longer fail a file, decode timeouts scale with duration,
and CD+G validation accepts non-mp3 audio. See
docs/archive/2026-06-30-playability-checker-reliability-design.md."""
import os
import zipfile

import playability as pl


def test_parse_decode_ignores_recoverable_warnings():
    # ffmpeg's cdgraphics "tile is out of range" is logged at error level but is
    # recoverable (valid commercial CD+G discs trigger it). rc == 0 => playable.
    stderr = ("[cdgraphics @ 0x1] tile is out of range\n"
              "[mp3float @ 0x2] some error happened but decode finished\n")
    d = pl.parse_decode(0, stderr)
    assert d["ok"] is True
    assert d["decode_errors"] >= 1  # still recorded as a diagnostic


def test_parse_decode_fails_on_nonzero_rc():
    d = pl.parse_decode(1, "moov atom not found\n")
    assert d["ok"] is False
    assert d["error"]


def test_decode_video_omits_xerror_and_uses_timeout(monkeypatch):
    chk = pl.PlayabilityChecker(config={})
    seen = {}

    def fake_run(cmd, timeout):
        seen["cmd"], seen["timeout"] = cmd, timeout
        return 0, "", ""

    monkeypatch.setattr(chk, "_run", fake_run)
    chk.decode_video("/x/a.mp4", timeout=321)
    assert "-xerror" not in seen["cmd"]
    assert seen["timeout"] == 321


def test_decode_file_omits_xerror(monkeypatch):
    chk = pl.PlayabilityChecker(config={})
    seen = {}

    def fake_run(cmd, timeout):
        seen["cmd"] = cmd
        return 0, "", ""

    monkeypatch.setattr(chk, "_run", fake_run)
    chk.decode_file("/x/a.cdg")
    assert "-xerror" not in seen["cmd"]


def test_decode_timeout_scales_with_duration():
    assert pl._decode_timeout(9999, "quick") == pl.QUICK_DECODE_TIMEOUT
    # deep: floor for short, scaled for long, floor for unknown duration
    assert pl._decode_timeout(5, "deep") == pl.MIN_DEEP_DECODE_TIMEOUT
    assert pl._decode_timeout(1000, "deep") == int(1000 * pl.DECODE_TIMEOUT_FACTOR)
    assert pl._decode_timeout(None, "deep") == pl.MIN_DEEP_DECODE_TIMEOUT


def test_check_cdg_accepts_non_mp3_audio(tmp_path, monkeypatch):
    # A commercial disc may ship .m4a/.wav instead of .mp3. The checker must not
    # report "no audio" for it.
    zp = os.path.join(str(tmp_path), "song.zip")
    with zipfile.ZipFile(zp, "w") as zf:
        zf.writestr("song.cdg", b"\x00" * 96)
        zf.writestr("song.m4a", b"fake-aac-audio")

    chk = pl.PlayabilityChecker(config={})
    monkeypatch.setattr(chk, "decode_file", lambda p, **k: {"ok": True})
    monkeypatch.setattr(chk, "probe_integrity", lambda p: {"duration": 123.0})
    res = chk.check_cdg(zp)
    chk._cleanup_cdg()

    assert res["has_audio"] is True
    assert res["extracted_audio"].endswith(".m4a")
    assert res["ok"] is True


def test_check_cdg_corrupt_zip_is_not_a_valid_zip(tmp_path):
    bad = os.path.join(str(tmp_path), "bad.zip")
    with open(bad, "wb") as fh:
        fh.write(b"this is not a zip at all")
    chk = pl.PlayabilityChecker(config={})
    res = chk.check_cdg(bad)
    chk._cleanup_cdg()
    assert res["ok"] is False
    assert res["error"] == "not a valid zip"
