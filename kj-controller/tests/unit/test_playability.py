"""Unit tests for playability parsers, classification, result schema."""
import json
import os
import zipfile

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


def _make_cdg_zip(tmp_path, with_cdg=True, with_audio=True):
    zp = os.path.join(str(tmp_path), "song.zip")
    with zipfile.ZipFile(zp, "w") as zf:
        if with_audio:
            zf.writestr("song.mp3", b"ID3fakeaudio")
        if with_cdg:
            zf.writestr("song.cdg", b"\x00" * 96)  # 4 CDG packets (24 bytes each)
    return zp


def test_check_cdg_missing_cdg(tmp_path):
    chk = pl.PlayabilityChecker(config={})
    zp = _make_cdg_zip(tmp_path, with_cdg=False)
    r = chk.check_cdg(zp)
    assert r["zip_ok"] is True
    assert r["has_cdg"] is False
    assert r["ok"] is False
    assert "cdg" in r["error"].lower()


def test_check_cdg_decodes(tmp_path, mocker):
    chk = pl.PlayabilityChecker(config={})
    zp = _make_cdg_zip(tmp_path, with_cdg=True, with_audio=True)
    # Both ffmpeg decode calls succeed.
    mocker.patch.object(chk, "_run", return_value=(0, "", ""))
    r = chk.check_cdg(zp)
    assert r["zip_ok"] and r["has_cdg"] and r["has_audio"]
    assert r["cdg_decodes"] and r["audio_decodes"]
    assert r["ok"] is True
    assert r["extracted_audio"] and r["extracted_audio"].endswith(".mp3")


def test_check_cdg_bad_zip(tmp_path):
    chk = pl.PlayabilityChecker(config={})
    bad = os.path.join(str(tmp_path), "bad.zip")
    with open(bad, "wb") as f:
        f.write(b"not a zip")
    r = chk.check_cdg(bad)
    assert r["zip_ok"] is False and r["ok"] is False


def test_compute_verdict_video_all_pass():
    res = pl.PlayabilityResult(path="/x/a.mp4", kind="video")
    res.integrity = {"ok": True, "has_video": True}
    res.decode = {"ok": True}
    res.renderers = {"vlc": {"frame_nonblank": True}, "mpv": {"frame_nonblank": True}}
    v = pl.compute_verdict("video", res, ("vlc", "mpv"))
    assert v["overall_ok"] is True
    assert v["vlc_playable"] and v["mpv_playable"]
    assert v["reasons"] == []


def test_compute_verdict_video_mpv_only():
    res = pl.PlayabilityResult(path="/x/a.mp4", kind="video")
    res.integrity = {"ok": True, "has_video": True}
    res.decode = {"ok": True}
    res.renderers = {"vlc": {"frame_nonblank": False}, "mpv": {"frame_nonblank": True}}
    v = pl.compute_verdict("video", res, ("vlc", "mpv"))
    assert v["mpv_playable"] is True and v["vlc_playable"] is False
    assert v["overall_ok"] is False
    assert any("vlc" in r for r in v["reasons"])


def test_compute_verdict_truncated_no_render_needed():
    res = pl.PlayabilityResult(path="/x/a.mp4", kind="video")
    res.integrity = {"ok": False, "has_video": False, "error": "moov atom not found (truncated/incomplete file)"}
    v = pl.compute_verdict("video", res, ("vlc", "mpv"))
    assert v["overall_ok"] is False
    assert any("moov" in r for r in v["reasons"])


def test_check_video_short_circuits_on_bad_integrity(mocker):
    chk = pl.PlayabilityChecker(config={})
    mocker.patch.object(chk, "probe_integrity",
                        return_value={"ok": False, "has_video": False, "moov_ok": False,
                                      "error": "moov atom not found (truncated/incomplete file)", "duration": None})
    spy_decode = mocker.patch.object(chk, "decode_video")
    spy_render = mocker.patch("playability_render.render_check")
    res = chk.check("/x/a.mp4", short_circuit=True)
    assert res.verdict["overall_ok"] is False
    spy_decode.assert_not_called()
    spy_render.assert_not_called()


def test_check_video_runs_all_layers_in_batch_mode(mocker, tmp_path):
    f = tmp_path / "a.mp4"; f.write_bytes(b"x")
    chk = pl.PlayabilityChecker(config={})
    mocker.patch.object(chk, "probe_integrity",
                        return_value={"ok": True, "has_video": True, "duration": 100.0, "error": None})
    mocker.patch.object(chk, "decode_video", return_value={"ok": True, "decode_errors": 0, "error": None})
    mocker.patch("playability_render.render_check",
                 return_value={"frame_captured": True, "frame_nonblank": True, "frame_varies": True, "error": None, "elapsed_s": 1.0})
    res = chk.check(str(f), renderers=("vlc", "mpv"), short_circuit=False)
    assert res.verdict["overall_ok"] is True
    assert set(res.renderers) == {"vlc", "mpv"}
