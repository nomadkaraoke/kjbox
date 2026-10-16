import datetime as dt
import subprocess

from nightclips import clips as C


def test_parse_clip_time_variants():
    assert C.parse_clip_time("2026-10-08 22.13.56.mp4") == dt.datetime(2026, 10, 8, 22, 13, 56)
    assert C.parse_clip_time("2026-10-08 22.13.56-1.MOV") == dt.datetime(2026, 10, 8, 22, 13, 56)
    assert C.parse_clip_time("2026-10-08 22.13.56.jpg") is None
    assert C.parse_clip_time("2026-10-08 22.13.56 edited.mp4") is None
    assert C.parse_clip_time("IMG_1234.mp4") is None
    assert C.parse_clip_time("2026-13-08 22.13.56.mp4") is None


def test_clip_ids_unique_and_url_safe():
    a, b, c = (C.clip_id(n) for n in ("2026-10-08 22.13.56.mp4", "2026-10-08 22.13.56-1.mp4", "2026-10-08 22.13.56.mov"))
    assert len({a, b, c}) == 3
    assert all(" " not in x and "/" not in x for x in (a, b, c))


def test_night_window_pads_ten_minutes(songs_unknown):
    start, end = C.night_window(songs_unknown)
    assert start == dt.datetime(2026, 10, 8, 20, 54, 32)
    assert end == dt.datetime(2026, 10, 9, 2, 30, 17)
    assert C.night_window([]) is None


def test_scan_clips_window_filter(tmp_path, clip_names, songs_unknown):
    names = clip_names + [
        "2026-10-08 20.54.31.mp4",  # 1s before window
        "2026-10-08 20.54.32.mov",  # exactly window start
        "2026-10-09 02.30.17-1.mp4",  # exactly window end
        "2026-10-09 02.30.18.mp4",  # 1s after
        "2026-10-08 22.00.00.jpg",  # photo
        "2026-10-01 22.00.00.mp4",  # last week
        "notes.txt",
    ]
    for n in names:
        (tmp_path / n).write_bytes(b"")
    start, end = C.night_window(songs_unknown)
    found = C.scan_clips(tmp_path, start, end)
    got = [c.filename for c in found]
    assert len(got) == 25
    assert "2026-10-08 20.54.32.mov" in got and "2026-10-09 02.30.17-1.mp4" in got
    assert "2026-10-08 20.54.31.mp4" not in got and "2026-10-09 02.30.18.mp4" not in got
    assert [c.start for c in found] == sorted(c.start for c in found)


class FakeRun:
    def __init__(self, stdout="", rc=0, side_effect=None):
        self.calls, self.stdout, self.rc, self.side_effect = [], stdout, rc, side_effect

    def __call__(self, cmd, **kw):
        self.calls.append(cmd)
        if self.side_effect:
            self.side_effect(cmd)
        return subprocess.CompletedProcess(cmd, self.rc, self.stdout, "")


def test_materialize_only_pending_and_reports_failures(tmp_path):
    local = tmp_path / "a.mp4"
    local.write_bytes(b"x")
    ok = tmp_path / "b.mp4"
    ok.write_bytes(b"")
    bad = tmp_path / "c.mp4"
    bad.write_bytes(b"")

    def fill(cmd):
        ok.write_bytes(b"data")

    run = FakeRun(stdout=f"FAIL    {bad}: network\nsummary: 1 ok, 1 failed\n", rc=1, side_effect=fill)
    failures = C.materialize([str(local), str(ok), str(bad)], runner=run)
    assert run.calls and str(local) not in run.calls[0]
    assert "-j" in run.calls[0] and "-q" in run.calls[0]
    assert set(failures) == {str(bad)}
    assert "network" in failures[str(bad)]


def test_materialize_nothing_to_do(tmp_path):
    f = tmp_path / "a.mp4"
    f.write_bytes(b"x")
    run = FakeRun()
    assert C.materialize([str(f)], runner=run) == {}
    assert run.calls == []


def test_parse_probe_rotation_swaps_dimensions():
    data = {
        "format": {"duration": "20.533"},
        "streams": [
            {"codec_type": "audio"},
            {"codec_type": "video", "width": 1920, "height": 1080, "side_data_list": [{"rotation": -90}]},
        ],
    }
    assert C.parse_probe(data) == {"duration": 20.53, "width": 1080, "height": 1920}
    data2 = {"format": {}, "streams": [{"codec_type": "video", "width": 1080, "height": 1920, "duration": "5.0"}]}
    assert C.parse_probe(data2) == {"duration": 5.0, "width": 1080, "height": 1920}


def test_trim_leaves_short_clips_alone(tmp_path):
    def boom(*a, **k):
        raise AssertionError("ffmpeg must not run for a short clip")
    assert C.trim_for_instagram("/x/a.mp4", 59.0, tmp_path, runner=boom) == "/x/a.mp4"



def test_trim_probes_unknown_duration(tmp_path):
    import json

    import pytest
    calls = []

    def fake(cmd, **k):
        calls.append(cmd[0])
        if cmd[0] == "ffprobe":
            return subprocess.CompletedProcess(cmd, 0, json.dumps({"format": {"duration": dur}}), "")
        (tmp_path / "t" / "a.mp4").write_bytes(b"cut")
        return subprocess.CompletedProcess(cmd, 0, "", "")
    src = tmp_path / "a.mp4"
    src.write_bytes(b"x")
    dur = "30.0"
    assert C.trim_for_instagram(str(src), None, tmp_path / "t", runner=fake) == str(src)
    dur = "75.0"
    assert C.trim_for_instagram(str(src), None, tmp_path / "t", runner=fake) == str(tmp_path / "t" / "a.mp4")
    assert calls == ["ffprobe", "ffprobe", "ffmpeg"]
    dur = None
    (tmp_path / "t" / "a.mp4").unlink()
    with pytest.raises(RuntimeError, match="can't read the length"):
        C.trim_for_instagram(str(src), None, tmp_path / "t", runner=fake)


def test_trim_timeout_removes_partial_output(tmp_path):
    import pytest

    def hang(cmd, **k):
        (tmp_path / "t" / "a.mp4").write_bytes(b"partial")
        raise subprocess.TimeoutExpired(cmd, 300)
    src = tmp_path / "a.mp4"
    src.write_bytes(b"x")
    with pytest.raises(RuntimeError, match="trim failed"):
        C.trim_for_instagram(str(src), 70.0, tmp_path / "t", runner=hang)
    assert not (tmp_path / "t" / "a.mp4").exists()


def test_trim_long_clip_with_real_ffmpeg(tmp_path):
    import json
    import shutil

    import pytest
    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg not installed")
    src = tmp_path / "2026-10-16 00.58.10.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc=size=64x112:rate=10",
                    "-f", "lavfi", "-i", "sine", "-t", "63", "-c:v", "libx264", "-g", "10",
                    "-c:a", "aac", "-shortest", str(src)], check=True)
    out = C.trim_for_instagram(str(src), 63.0, tmp_path / "trimmed")
    assert out == str(tmp_path / "trimmed" / src.name)
    info = json.loads(subprocess.run(
        ["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams", out],
        capture_output=True, text=True, check=True).stdout)
    assert float(info["format"]["duration"]) < 60
    assert {s["codec_type"] for s in info["streams"]} == {"video", "audio"}
    # cached on the second call
    def boom(*a, **k):
        raise AssertionError("should reuse the trimmed copy")
    assert C.trim_for_instagram(str(src), 63.0, tmp_path / "trimmed", runner=boom) == out


def test_trim_failure_raises(tmp_path):
    import pytest

    def fail(*a, **k):
        return subprocess.CompletedProcess(a, 1, "", "boom")
    src = tmp_path / "a.mp4"
    src.write_bytes(b"x")
    with pytest.raises(RuntimeError, match="trim failed"):
        C.trim_for_instagram(str(src), 70.0, tmp_path / "t", runner=fail)
