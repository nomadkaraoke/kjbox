"""Unit tests for render command builders + start-time selection."""
import playability_render as pr


def test_capture_start_mid_file_for_long():
    assert pr.capture_start(200.0) == 80.0  # 40%


def test_capture_start_zero_for_short_or_unknown():
    assert pr.capture_start(4.0) == 0.0
    assert pr.capture_start(None) == 0.0


def test_build_mpv_capture_cmd():
    cmd = pr.build_mpv_capture_cmd("/x/a.mp4", "/tmp/out", 30.0, frames=3, fps=1)
    assert cmd[0] == "mpv"
    assert "--no-config" in cmd and "--ao=null" in cmd
    assert "--vo=image" in cmd
    assert "--vo-image-outdir=/tmp/out" in cmd
    assert "--start=30.0" in cmd
    assert "--vf=fps=1" in cmd
    assert "--frames=3" in cmd
    assert cmd[-1] == "/x/a.mp4"


def test_build_vlc_capture_cmd_targets_xvfb_and_no_audio():
    cmd = pr.build_vlc_capture_cmd("/x/a.mp4", ":99", "/tmp/out", 30.0, window_s=3, scene_ratio=25)
    assert cmd[0] == "env" and "DISPLAY=:99" in cmd
    assert "cvlc" in cmd
    assert "--no-audio" in cmd
    assert "--video-filter=scene" in cmd
    assert "--scene-path=/tmp/out" in cmd
    assert "--start-time=30.0" in cmd
    assert "--stop-time=33.0" in cmd
    assert "vlc://quit" in cmd
    assert "/x/a.mp4" in cmd


import os
import shutil

import pytest

import playability_render as pr


def _tools_present(*names):
    return all(shutil.which(n) for n in names)


def test_render_check_mpv_judges_captured_frames(tmp_path, mocker):
    from PIL import Image

    def fake_run(cmd, timeout):
        # Simulate mpv writing two real (non-blank) frames into out_dir.
        out_dir = [a.split("=", 1)[1] for a in cmd if a.startswith("--vo-image-outdir=")][0]
        img = Image.new("L", (32, 24))
        img.putdata([(i * 9) % 256 for i in range(32 * 24)])
        img.save(os.path.join(out_dir, "00000001.png"))
        img.rotate(90).save(os.path.join(out_dir, "00000002.png"))
        return (0, "", "")

    res = pr.render_check(fake_run, "/x/a.mp4", "mpv", duration=100.0,
                          display=":99", tmp_root=str(tmp_path))
    assert res["frame_captured"] is True
    assert res["frame_nonblank"] is True
    assert res["error"] is None


def test_render_check_reports_no_frame(tmp_path):
    def fake_run(cmd, timeout):
        return (0, "", "")  # writes nothing

    res = pr.render_check(fake_run, "/x/a.mp4", "mpv", duration=100.0,
                          display=":99", tmp_root=str(tmp_path))
    assert res["frame_captured"] is False
    assert res["frame_nonblank"] is False


@pytest.mark.skipif(not _tools_present("Xvfb", "cvlc"), reason="Xvfb/cvlc not installed")
def test_xvfb_starts_and_stops():
    with pr.XvfbDisplay(display=":97") as d:
        assert os.path.exists("/tmp/.X11-unix/X97")
        assert d.display == ":97"
    # after exit the socket is gone
    assert not os.path.exists("/tmp/.X11-unix/X97")


def test_render_check_keep_dir_saves_representative_frame(tmp_path):
    from PIL import Image

    keep = tmp_path / "keep"

    def fake_run(cmd, timeout):
        out_dir = [a.split("=", 1)[1] for a in cmd if a.startswith("--vo-image-outdir=")][0]
        img = Image.new("L", (32, 24))
        img.putdata([(i * 9) % 256 for i in range(32 * 24)])  # real, non-blank
        img.save(os.path.join(out_dir, "00000001.png"))
        return (0, "", "")

    res = pr.render_check(fake_run, "/x/song.mp4", "mpv", duration=100.0, display=":99",
                          tmp_root=str(tmp_path), keep_dir=str(keep))
    assert res["saved_frame"] is not None
    assert os.path.isfile(res["saved_frame"])
    assert res["saved_frame"].endswith("song.mp4__mpv.png")


def test_render_check_no_keep_dir_leaves_saved_frame_none(tmp_path):
    def fake_run(cmd, timeout):
        return (0, "", "")

    res = pr.render_check(fake_run, "/x/song.mp4", "mpv", duration=100.0,
                          tmp_root=str(tmp_path))
    assert res["saved_frame"] is None
