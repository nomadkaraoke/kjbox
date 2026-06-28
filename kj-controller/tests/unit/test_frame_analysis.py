"""Unit tests frame_analysis (pure Pillow frame math)."""
import os

import pytest
from PIL import Image

import frame_analysis as fa


def _save(tmp_path, name, img):
    p = os.path.join(str(tmp_path), name)
    img.save(p)
    return p


def test_black_frame_is_blank(tmp_path):
    p = _save(tmp_path, "black.png", Image.new("RGB", (64, 48), (0, 0, 0)))
    s = fa.analyze_frame(p)
    assert s.exists is True
    assert s.is_blank is True
    assert s.spread < 1.0


def test_solid_colour_frame_is_blank(tmp_path):
    p = _save(tmp_path, "solid.png", Image.new("RGB", (64, 48), (40, 120, 200)))
    s = fa.analyze_frame(p)
    assert s.exists is True
    assert s.is_blank is True


def test_frames_differ_detects_motion(tmp_path):
    img1 = Image.new("RGB", (64, 48), (100, 100, 100))
    img2 = Image.new("RGB", (64, 48), (150, 150, 150))
    p1 = _save(tmp_path, "diff1.png", img1)
    p2 = _save(tmp_path, "diff2.png", img2)
    assert fa.frames_differ(p1, p2) is True


def test_frames_differ_ignores_identical(tmp_path):
    img = Image.new("RGB", (64, 48), (100, 100, 100))
    p1 = _save(tmp_path, "same1.png", img)
    p2 = _save(tmp_path, "same2.png", img)
    assert fa.frames_differ(p1, p2) is False


def test_judge_renderer_frames_real_video(tmp_path):
    img = Image.new("L", (64, 48))
    img.putdata([(i * 7) % 256 for i in range(64 * 48)])
    f1 = _save(tmp_path, "f1.png", img)
    img2 = img.rotate(90)
    f2 = _save(tmp_path, "f2.png", img2)
    v = fa.judge_renderer_frames([f1, f2])
    assert v["frame_captured"] is True
    assert v["frame_nonblank"] is True
    assert v["frame_varies"] is True


def test_judge_renderer_frames_all_black(tmp_path):
    f1 = _save(tmp_path, "b1.png", Image.new("RGB", (64, 48), (0, 0, 0)))
    f2 = _save(tmp_path, "b2.png", Image.new("RGB", (64, 48), (0, 0, 0)))
    v = fa.judge_renderer_frames([f1, f2])
    assert v["frame_captured"] is True
    assert v["frame_nonblank"] is False
