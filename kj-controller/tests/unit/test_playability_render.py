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
