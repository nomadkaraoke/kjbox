"""quick_lyrics: add lyrics to a quick version gen rendered without any."""

import shutil
import subprocess
import threading
from unittest.mock import MagicMock

import pytest

import quick_lyrics as ql

HAVE_FFMPEG = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


class TestCleanTitle:
    @pytest.mark.parametrize("raw,clean", [
        ("My Body Isn't Ready (Orchestral Version Live From Sound City)", "My Body Isn't Ready"),
        ("Creep (Acoustic) [Remastered 2009]", "Creep"),
        ("Song - Live at Wembley", "Song"),
        ("Song - 2011 Remaster", "Song"),
        ("Rock - Paper - Scissors", "Rock - Paper - Scissors"),
        ("(Intro)", "(Intro)"),
        ("", ""),
    ])
    def test_strips_decorations(self, raw, clean):
        assert ql.clean_title(raw) == clean

    def test_default_query(self):
        assert ql.default_query("Sombr", "My Body Isn't Ready (Live)") == "Sombr My Body Isn't Ready"
        assert ql.default_query("", "Title") == "Title"


class TestArtistTitleFromPath:
    def test_gen_quick_upload_name(self):
        p = "/opt/nomad/downloads/upload/Sombr - My Body Isn't Ready (Orchestral) [up-965c1e2e01a1].mp4"
        assert ql.artist_title_from_path(p) == ("Sombr", "My Body Isn't Ready (Orchestral)")

    def test_quick_version_suffix_and_no_artist(self):
        assert ql.artist_title_from_path("/x/A - B (Quick Version) [up-1].mp4") == ("A", "B")
        assert ql.artist_title_from_path("/x/Just A Title.mp4") == ("", "Just A Title")


class TestParseLyrics:
    def test_lrc_is_synced(self):
        lyr = ql.parse_lyrics("[00:01.00] one\r\n[00:03.50] two\n")
        assert lyr.kind == "synced"
        assert lyr.timed == [(1.0, "one"), (3.5, "two")]
        assert lyr.plain == "one\ntwo"

    def test_plain(self):
        lyr = ql.parse_lyrics("one\n\ntwo")
        assert lyr.kind == "plain" and lyr.timed is None

    @pytest.mark.parametrize("text", ["", "   \n ", "[00:01.00]"])
    def test_empty_rejected(self, text):
        with pytest.raises(ValueError):
            ql.parse_lyrics(text)

    def test_too_long_rejected(self):
        with pytest.raises(ValueError):
            ql.parse_lyrics("x" * (ql.MAX_LYRICS_CHARS + 1))

    def test_has_timing(self):
        assert ql.has_timing("[01:02.03] x") and not ql.has_timing("plain")


def _session(payload, status=200):
    s = MagicMock()
    s.headers = {}
    resp = MagicMock(status_code=status)
    resp.json.return_value = payload
    if status >= 400:
        import requests
        resp.raise_for_status.side_effect = requests.HTTPError(str(status))
    s.get.return_value = resp
    return s


class TestSearchLrclib:
    def test_synced_first_dedup_and_drops_empty(self):
        s = _session([
            {"id": 1, "artistName": "A", "trackName": "T", "duration": 200, "plainLyrics": "p1\np2\np3"},
            {"id": 2, "artistName": "A", "trackName": "T", "duration": 201,
             "syncedLyrics": "[00:01.00] s1\n[00:02.00] s2", "plainLyrics": "s1\ns2"},
            {"id": 2, "artistName": "dupe"},
            {"id": 3, "artistName": "A", "trackName": "Instrumental"},
            "junk",
        ])
        out = ql.search_lrclib("A T", session=s)
        assert [r["id"] for r in out] == [2, 1]
        assert out[0]["synced"] and out[0]["lyrics"].startswith("[00:01.00]")
        assert out[0]["preview"] == "s1 / s2"
        assert out[1]["preview"] == "p1 / p2" and not out[1]["synced"]
        assert s.get.call_args.kwargs["params"] == {"q": "A T"}
        assert "nomadkaraoke" in s.headers["User-Agent"]

    def test_empty_query_skips_request(self):
        s = _session([])
        assert ql.search_lrclib("  ", session=s) == []
        s.get.assert_not_called()

    def test_http_error_raises(self):
        import requests
        with pytest.raises(requests.RequestException):
            ql.search_lrclib("x", session=_session([], status=503))

    def test_caps_results(self):
        hits = [{"id": i, "plainLyrics": f"l{i}"} for i in range(40)]
        assert len(ql.search_lrclib("x", session=_session(hits))) == ql.MAX_RESULTS


def test_ffmpeg_cmd_copies_audio_and_bounds_duration():
    cmd = ql._ffmpeg_cmd("/in.mp4", "/c.png", "0+1", "/out.mp4", 854, 12.5)
    assert cmd[cmd.index("-c:a") + 1] == "copy"
    assert "2:a:0" in cmd and cmd[cmd.index("-t", cmd.index("/in.mp4")) + 1] == "12.500"
    assert any("d=12.500" in c for c in cmd)


def test_render_busy(tmp_path):
    assert ql._render_lock.acquire(blocking=False)
    try:
        with pytest.raises(ql.RenderBusy):
            ql.rerender_quick_video(str(tmp_path / "x.mp4"), ql.parse_lyrics("a"), "A", "T")
    finally:
        ql._render_lock.release()


def test_missing_file(tmp_path):
    with pytest.raises(FileNotFoundError):
        ql.rerender_quick_video(str(tmp_path / "nope.mp4"), ql.parse_lyrics("a"), "A", "T")


@pytest.fixture
def quick_mp4(tmp_path):
    path = tmp_path / "upload" / "A - T [up-abc].mp4"
    path.parent.mkdir()
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y",
                    "-f", "lavfi", "-i", "color=c=black:s=320x180:r=24:d=6",
                    "-f", "lavfi", "-i", "sine=f=440:d=6",
                    "-c:v", "libx264", "-c:a", "aac", "-shortest", str(path)], check=True)
    path.chmod(0o600)
    return path


def _probe(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries",
                          "stream=codec_type,width,height:format=duration", "-of", "json", str(path)],
                         capture_output=True, text=True, check=True).stdout
    import json
    return json.loads(out)


@pytest.mark.skipif(not HAVE_FFMPEG, reason="ffmpeg not installed")
class TestRerender:
    def test_synced_in_place_keeps_audio_and_mode(self, quick_mp4):
        before = _probe(quick_mp4)
        res = ql.rerender_quick_video(str(quick_mp4), ql.parse_lyrics("[00:01.00] one\n[00:03.00] two"),
                                      "A", "T")
        assert res == {"lyrics_tier": "synced", "line_count": 2, "duration": res["duration"]}
        assert abs(res["duration"] - 6.0) < 0.2
        after = _probe(quick_mp4)
        vid = next(s for s in after["streams"] if s["codec_type"] == "video")
        assert (vid["width"], vid["height"]) == (854, 480)
        assert any(s["codec_type"] == "audio" for s in after["streams"])
        assert abs(float(after["format"]["duration"]) - float(before["format"]["duration"])) < 0.2
        assert (quick_mp4.stat().st_mode & 0o777) == 0o600
        assert [p.name for p in quick_mp4.parent.iterdir()] == [quick_mp4.name]  # no staging left

    def test_use_timing_false_gives_constant(self, quick_mp4):
        res = ql.rerender_quick_video(str(quick_mp4), ql.parse_lyrics("[00:01.00] one"), "A", "T",
                                      use_timing=False)
        assert res["lyrics_tier"] == "constant" and res["line_count"] == 1

    def test_ffmpeg_failure_leaves_original(self, quick_mp4, monkeypatch):
        original = quick_mp4.read_bytes()

        def boom(*a, **k):
            return subprocess.CompletedProcess(a, 1, "", "kaboom")
        monkeypatch.setattr(ql, "probe_duration", lambda p: 6.0)
        monkeypatch.setattr(ql.subprocess, "run", boom)
        with pytest.raises(RuntimeError, match="kaboom"):
            ql.rerender_quick_video(str(quick_mp4), ql.parse_lyrics("one"), "A", "T")
        assert quick_mp4.read_bytes() == original
        assert [p.name for p in quick_mp4.parent.iterdir()] == [quick_mp4.name]
        assert not ql._render_lock.locked()
