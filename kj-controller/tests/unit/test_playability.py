"""Unit tests for playability parsers, classification, result schema."""
import json

import playability as pl


def test_classify_kind():
    assert pl.classify_kind("/x/song.mp4") == "video"
    assert pl.classify_kind("/x/song.MKV") == "video"
    assert pl.classify_kind("/x/song.zip") == "cdg_zip"
    assert pl.classify_kind("/x/song.mp3") == "audio"
    assert pl.classify_kind("/x/song.txt") == "unknown"


def test_parse_integrity_valid_video():
    stdout = json.dumps({
        "streams": [
            {"codec_type": "video", "codec_name": "h264"},
            {"codec_type": "audio", "codec_name": "aac"},
        ],
        "format": {"format_name": "mov,mp4,m4a,3gp,3g2,mj2", "duration": "212.5"},
    })
    r = pl.parse_integrity(0, stdout, "")
    assert r["ok"] is True
    assert r["has_video"] and r["has_audio"]
    assert r["vcodec"] == "h264" and r["acodec"] == "aac"
    assert r["duration"] == 212.5
    assert r["moov_ok"] is True
    assert r["error"] is None


def test_parse_integrity_truncated_moov():
    r = pl.parse_integrity(1, "", "[mov,mp4 @ 0x..] moov atom not found\n")
    assert r["ok"] is False
    assert r["moov_ok"] is False
    assert "moov atom not found" in r["error"]


def test_parse_integrity_no_video_stream():
    stdout = json.dumps({
        "streams": [{"codec_type": "audio", "codec_name": "mp3"}],
        "format": {"format_name": "mp3", "duration": "180.0"},
    })
    r = pl.parse_integrity(0, stdout, "")
    assert r["has_video"] is False and r["has_audio"] is True
    assert r["ok"] is True  # structurally valid; video requirement applied in verdict


def test_parse_integrity_zero_streams_clean_exit():
    import json as _json
    stdout = _json.dumps({"streams": [], "format": {"format_name": "data", "duration": "0"}})
    r = pl.parse_integrity(0, stdout, "")
    assert r["ok"] is False
    assert r["has_video"] is False and r["has_audio"] is False
    assert r["error"] == "no decodable streams"


def test_result_roundtrip():
    res = pl.PlayabilityResult(path="/x/a.mp4", kind="video", size=10, mtime=1.0)
    res.integrity = {"ok": True}
    d = res.to_dict()
    assert json.loads(json.dumps(d))["integrity"]["ok"] is True
    back = pl.PlayabilityResult.from_dict(d)
    assert back.path == "/x/a.mp4" and back.kind == "video"


def test_parse_decode_clean():
    r = pl.parse_decode(0, "")
    assert r["ok"] is True and r["decode_errors"] == 0


def test_parse_decode_errors():
    stderr = "[h264 @ 0x] error while decoding MB 1 2\n[h264 @ 0x] concealing errors\n"
    r = pl.parse_decode(1, stderr)
    assert r["ok"] is False
    assert r["decode_errors"] >= 1
    assert r["error"]


def test_probe_integrity_invokes_ffprobe(mocker):
    chk = pl.PlayabilityChecker(config={})
    fake = mocker.patch.object(chk, "_run", return_value=(0, '{"streams":[{"codec_type":"video","codec_name":"h264"}],"format":{"duration":"10.0"}}', ""))
    r = chk.probe_integrity("/x/a.mp4")
    assert r["ok"] is True and r["has_video"] is True
    cmd = fake.call_args[0][0]
    assert "ffprobe" in cmd and "/x/a.mp4" in cmd


def test_decode_video_builds_sampled_window(mocker):
    chk = pl.PlayabilityChecker(config={})
    fake = mocker.patch.object(chk, "_run", return_value=(0, "", ""))
    chk.decode_video("/x/a.mp4", start=30.0, length=5.0)
    cmd = fake.call_args[0][0]
    assert "ffmpeg" in cmd
    assert "-ss" in cmd and "30.0" in cmd
    assert "-t" in cmd and "5.0" in cmd
    assert "-f" in cmd and "null" in cmd
