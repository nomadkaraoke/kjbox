"""Unit tests for perf_recorder — session lifecycle, traversal safety, summary."""
import json

import perf_recorder as pr


def test_sanitize_label():
    assert pr.sanitize_label("CDG baseline!") == "CDG-baseline"
    assert pr.sanitize_label("") == "session"
    assert pr.sanitize_label("  ../etc/passwd  ") == "etcpasswd"
    assert len(pr.sanitize_label("x" * 100)) == 48


def test_start_record_stop_lifecycle(tmp_path):
    rec = pr.PerfRecorder(str(tmp_path))
    assert rec.status()["recording"] is False

    st = rec.start("cdg test")
    assert st["recording"] is True
    assert st["label"] == "cdg-test"
    sid = st["id"]

    rec.record({"t": 1.0, "playing": True, "render_fps": 58})
    rec.record({"t": 2.0, "playing": True, "render_fps": 59})
    mid = rec.status()
    assert mid["recording"] is True and mid["sample_count"] == 2

    end = rec.stop()
    assert end["recording"] is False and end["sample_count"] == 2

    # File exists with a meta header + 2 sample lines.
    path = rec.path_for(sid)
    assert path is not None
    lines = [l for l in open(path).read().splitlines() if l]
    assert len(lines) == 3
    assert json.loads(lines[0]).get("_meta") is True
    assert json.loads(lines[1])["render_fps"] == 58


def test_record_is_noop_when_not_recording(tmp_path):
    rec = pr.PerfRecorder(str(tmp_path))
    rec.record({"t": 1.0})   # must not raise or create anything
    assert rec.list() == []


def test_record_never_raises_on_unserializable_sample(tmp_path):
    rec = pr.PerfRecorder(str(tmp_path))
    rec.start("x")
    rec.record({"bad": object()})   # not JSON-serializable — must be dropped, not raised
    rec.record({"t": 1.0, "ok": True})
    end = rec.stop()
    assert end["sample_count"] == 1   # only the good one landed


def test_list_reports_sessions(tmp_path):
    rec = pr.PerfRecorder(str(tmp_path))
    rec.start("alpha")
    rec.record({"t": 1.0})
    rec.stop()
    listing = rec.list()
    assert len(listing) == 1
    assert listing[0]["label"] == "alpha"
    assert listing[0]["started"] is not None


def test_path_for_rejects_traversal(tmp_path):
    rec = pr.PerfRecorder(str(tmp_path))
    assert rec.path_for("../etc/passwd") is None
    assert rec.path_for("nonsense") is None
    assert rec.path_for("20260706-010203-x/../../y") is None
    assert rec.path_for("") is None


def test_summary_separates_real_from_benign_drops(tmp_path):
    # A CDG-shaped session: big vo-drops but NOT meaningful -> vo_real stays 0.
    path = tmp_path / "20260706-010203-cdg.jsonl"
    rows = [{"_meta": True, "label": "cdg", "id": "20260706-010203-cdg", "started": 0}]
    for i in range(10):
        rows.append({
            "t": float(i), "playing": True, "engine": "mpv",
            "render_fps": 58.0, "fps_target": 60,
            "video": {"container_fps": 300, "display_fps": 60, "hwdec": "no"},
            "drops_delta": {"vo": 9, "decoder": 0, "vlc_lost": 0},
            "drops_meaningful": False,
            "gpu": {"busy_pct": 45.0}, "cpu": {"mpv": 60.0, "Xorg": 24.0},
            "temp_c": 60.0, "health": "green",
        })
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n")

    s = pr.summarize_file(str(path))
    assert s["samples"] == 10
    assert s["duration_s"] == 9.0
    assert s["engines"] == ["mpv"]
    assert s["hwdec"] == ["no"]
    assert s["drops"]["vo_total"] == 90     # all vo drops
    assert s["drops"]["vo_real"] == 0       # none were meaningful (CDG decimation)
    assert s["drops"]["decoder"] == 0
    assert s["render_fps"]["avg"] == 58.0
    assert s["fps_ok_pct"] == 100.0         # 58 >= 0.92*60
    assert s["gpu_busy"]["avg"] == 45.0
    assert s["cpu_avg"]["mpv"] == 60.0
    assert s["health"] == {"green": 10, "amber": 0, "red": 0}


def test_summary_counts_meaningful_h264_drops(tmp_path):
    path = tmp_path / "20260706-010203-h264.jsonl"
    rows = [{"_meta": True, "label": "h264", "id": "20260706-010203-h264", "started": 0}]
    for i in range(6):
        rows.append({
            "t": float(i), "playing": True, "engine": "mpv",
            "render_fps": 28.0, "fps_target": 30,
            "video": {"container_fps": 30, "display_fps": 60},
            "drops_delta": {"vo": 2, "decoder": 0, "vlc_lost": 0},
            "drops_meaningful": True,
            "health": "amber",
        })
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    s = pr.summarize_file(str(path))
    assert s["drops"]["vo_real"] == 12
    assert s["drops_real_per_min"] is not None and s["drops_real_per_min"] > 0


def test_summarize_missing_file_returns_none():
    assert pr.summarize_file("/no/such/file.jsonl") is None
