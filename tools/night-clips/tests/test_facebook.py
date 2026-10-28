import json

import pytest

from nightclips import facebook as F
from nightclips.publish import StepFailed


def test_missing_files_exit_code_2(tmp_path):
    assert F.run_publisher([str(tmp_path / "nope.mp4")], "c", tmp_path) == 2


def test_empty_placeholder_files_are_rejected(tmp_path):
    f = tmp_path / "a.mp4"
    f.write_bytes(b"")  # a Dropbox online-only stub
    assert F.run_publisher([str(f)], "c", tmp_path) == 2


def test_caption_over_facebook_limit_fails_before_opening_anything():
    pub = F.FacebookPublisher(page=None, work_dir=".")
    with pytest.raises(StepFailed, match="too long"):
        pub.run(["x"], "a" * (F.FB_CAPTION_MAX + 1), None, False)


def test_main_reads_request_file(tmp_path, monkeypatch):
    req = tmp_path / "r.json"
    req.write_text(json.dumps({"files": ["a.mp4"], "caption": "hi", "location": "Somewhere"}))
    seen = {}
    monkeypatch.setattr(F, "run_publisher", lambda files, cap, wd, loc, auto: seen.update(
        files=files, cap=cap, loc=loc, auto=auto) or 0)
    assert F.main(["--request", str(req), "--work-dir", str(tmp_path), "--auto-share"]) == 0
    assert seen == {"files": ["a.mp4"], "cap": "hi", "loc": "Somewhere", "auto": True}
