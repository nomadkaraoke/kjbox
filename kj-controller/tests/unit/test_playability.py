"""Unit tests for playability parsers, classification, result schema."""
import json
import os
import zipfile

import playability as pl


def _fake_xvfb(mocker, module="playability_render", display=":99"):
    """Patch <module>.XvfbDisplay with a context manager yielding `.display`,
    so check()/run_batch never launch a real off-screen X server in unit tests."""
    cls = mocker.patch(f"{module}.XvfbDisplay")
    cls.return_value.__enter__.return_value.display = display
    cls.return_value.__exit__.return_value = False
    return cls


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


def test_compute_verdict_render_is_diagnostic_only():
    # Render frame-capture is recorded for the VLC-vs-mpv matrix but NEVER gates
    # the verdict (it proved ~90% false-positive). A file that passes integrity
    # + decode is OK even if a headless renderer captured no frame.
    res = pl.PlayabilityResult(path="/x/a.mp4", kind="video")
    res.integrity = {"ok": True, "has_video": True}
    res.decode = {"ok": True}
    res.renderers = {"vlc": {"frame_nonblank": False}, "mpv": {"frame_nonblank": True}}
    v = pl.compute_verdict("video", res, ("vlc", "mpv"))
    # matrix diagnostics still recorded ...
    assert v["mpv_playable"] is True and v["vlc_playable"] is False
    # ... but the verdict rests purely on deterministic integrity + decode
    assert v["overall_ok"] is True
    assert v["reasons"] == []


def test_compute_verdict_truncated_no_render_needed():
    res = pl.PlayabilityResult(path="/x/a.mp4", kind="video")
    res.integrity = {"ok": False, "has_video": False, "error": "moov atom not found (truncated/incomplete file)"}
    v = pl.compute_verdict("video", res, ("vlc", "mpv"))
    assert v["overall_ok"] is False
    assert any("moov" in r for r in v["reasons"])


def test_check_video_short_circuits_on_bad_integrity(mocker):
    chk = pl.PlayabilityChecker(config={})
    _fake_xvfb(mocker)
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
    _fake_xvfb(mocker)
    mocker.patch.object(chk, "probe_integrity",
                        return_value={"ok": True, "has_video": True, "duration": 100.0, "error": None})
    mocker.patch.object(chk, "decode_video", return_value={"ok": True, "decode_errors": 0, "error": None})
    mocker.patch("playability_render.render_check",
                 return_value={"frame_captured": True, "frame_nonblank": True, "frame_varies": True, "error": None, "elapsed_s": 1.0})
    res = chk.check(str(f), renderers=("vlc", "mpv"), short_circuit=False)
    assert res.verdict["overall_ok"] is True
    assert set(res.renderers) == {"vlc", "mpv"}


# ---- C1: Xvfb wiring in check() -------------------------------------------

def test_check_starts_xvfb_when_display_none_and_vlc_needed(mocker, tmp_path):
    f = tmp_path / "a.mp4"; f.write_bytes(b"x")
    chk = pl.PlayabilityChecker(config={})
    xvfb = _fake_xvfb(mocker, display=":99")
    mocker.patch.object(chk, "probe_integrity",
                        return_value={"ok": True, "has_video": True, "duration": 100.0, "error": None})
    mocker.patch.object(chk, "decode_video", return_value={"ok": True, "decode_errors": 0, "error": None})
    rc = mocker.patch("playability_render.render_check",
                      return_value={"frame_nonblank": True, "error": None})
    chk.check(str(f), renderers=("vlc", "mpv"), short_circuit=False)
    # Xvfb was started (context-managed) for the duration of the call.
    xvfb.assert_called_once()
    xvfb.return_value.__enter__.assert_called_once()
    # Every render_check got the started display.
    assert rc.call_args_list, "render_check should have been called"
    for call in rc.call_args_list:
        assert call.kwargs["display"] == ":99"


def test_check_does_not_start_xvfb_for_mpv_only(mocker, tmp_path):
    f = tmp_path / "a.mp4"; f.write_bytes(b"x")
    chk = pl.PlayabilityChecker(config={})
    xvfb = _fake_xvfb(mocker)
    mocker.patch.object(chk, "probe_integrity",
                        return_value={"ok": True, "has_video": True, "duration": 100.0, "error": None})
    mocker.patch.object(chk, "decode_video", return_value={"ok": True, "decode_errors": 0, "error": None})
    rc = mocker.patch("playability_render.render_check",
                      return_value={"frame_nonblank": True, "error": None})
    chk.check(str(f), renderers=("mpv",), short_circuit=False)
    xvfb.assert_not_called()
    # mpv still rendered, just with display left as None.
    assert rc.call_args_list and rc.call_args_list[0].kwargs["display"] is None


def test_check_does_not_start_xvfb_for_audio_kind(mocker, tmp_path):
    f = tmp_path / "a.mp3"; f.write_bytes(b"x")
    chk = pl.PlayabilityChecker(config={})
    xvfb = _fake_xvfb(mocker)
    mocker.patch.object(chk, "probe_integrity",
                        return_value={"ok": True, "has_audio": True, "duration": 100.0, "error": None})
    chk.check(str(f), renderers=("vlc", "mpv"), short_circuit=False)
    xvfb.assert_not_called()


def test_check_uses_provided_display_without_starting_xvfb(mocker, tmp_path):
    f = tmp_path / "a.mp4"; f.write_bytes(b"x")
    chk = pl.PlayabilityChecker(config={})
    xvfb = _fake_xvfb(mocker)
    mocker.patch.object(chk, "probe_integrity",
                        return_value={"ok": True, "has_video": True, "duration": 100.0, "error": None})
    mocker.patch.object(chk, "decode_video", return_value={"ok": True, "decode_errors": 0, "error": None})
    rc = mocker.patch("playability_render.render_check",
                      return_value={"frame_nonblank": True, "error": None})
    chk.check(str(f), renderers=("vlc", "mpv"), short_circuit=False, display=":55")
    xvfb.assert_not_called()
    for call in rc.call_args_list:
        assert call.kwargs["display"] == ":55"


# ---- I3: CDG mpv gets the .cdg, vlc gets the .mp3 -------------------------

def test_check_cdg_exposes_extracted_cdg(tmp_path, mocker):
    chk = pl.PlayabilityChecker(config={})
    zp = _make_cdg_zip(tmp_path, with_cdg=True, with_audio=True)
    mocker.patch.object(chk, "_run", return_value=(0, "", ""))
    try:
        r = chk.check_cdg(zp)
        assert r["extracted_cdg"] and r["extracted_cdg"].endswith(".cdg")
        assert os.path.dirname(r["extracted_cdg"]) == os.path.dirname(r["extracted_audio"])
    finally:
        chk._cleanup_cdg()


def test_check_cdg_feeds_mpv_the_cdg_and_vlc_the_mp3(tmp_path, mocker):
    chk = pl.PlayabilityChecker(config={})
    zp = _make_cdg_zip(tmp_path, with_cdg=True, with_audio=True)
    _fake_xvfb(mocker)
    mocker.patch.object(chk, "_run", return_value=(0, "", ""))  # decode_file calls
    rc = mocker.patch("playability_render.render_check",
                      return_value={"frame_nonblank": True, "error": None})
    chk.check(zp, renderers=("vlc", "mpv"), short_circuit=False)
    # Map renderer -> input path that render_check was handed.
    src_by_renderer = {call.args[2]: call.args[1] for call in rc.call_args_list}
    assert set(src_by_renderer) == {"vlc", "mpv"}
    assert src_by_renderer["vlc"].endswith(".mp3")
    assert src_by_renderer["mpv"].endswith(".cdg")
    # mpv is ALSO handed the audio track (mirrors production loadfile+audio-add):
    # a bare .cdg has no timeline so mpv can't seek mid-file. VLC needs no
    # external audio (it auto-discovers the sibling .cdg from the .mp3).
    audio_by_renderer = {call.args[2]: call.kwargs.get("audio_file") for call in rc.call_args_list}
    assert audio_by_renderer["mpv"] and audio_by_renderer["mpv"].endswith(".mp3")
    assert audio_by_renderer["vlc"] is None


def test_compute_verdict_cdg_mpv_recorded_but_not_required():
    res = pl.PlayabilityResult(path="/x/a.zip", kind="cdg_zip")
    res.cdg = {"ok": True}
    res.renderers = {"vlc": {"frame_nonblank": True},
                     "mpv": {"frame_nonblank": False, "error": "mpv exited 2"}}
    v = pl.compute_verdict("cdg_zip", res, ("vlc", "mpv"))
    assert v["overall_ok"] is True          # base_ok (cdg decode) gates; render is diagnostic only
    assert v["vlc_playable"] is True
    assert v["mpv_playable"] is False        # still recorded for the matrix
    assert not any("mpv" in r for r in v["reasons"])


def test_check_records_per_stage_timings(tmp_path, mocker):
    f = tmp_path / "a.mp4"
    f.write_bytes(b"x")
    chk = pl.PlayabilityChecker(config={})
    _fake_xvfb(mocker)
    mocker.patch.object(chk, "probe_integrity",
                        return_value={"ok": True, "has_video": True, "duration": 100.0, "error": None})
    mocker.patch.object(chk, "decode_video",
                        return_value={"ok": True, "decode_errors": 0, "error": None})
    mocker.patch("playability_render.render_check",
                 return_value={"frame_nonblank": True, "elapsed_s": 1.0, "error": None})
    res = chk.check(str(f), renderers=("vlc", "mpv"))
    for key in ("integrity", "decode", "render_vlc", "render_mpv", "total"):
        assert key in res.timings, f"missing timing stage: {key}"
    assert res.timings["render_vlc"] == 1.0
