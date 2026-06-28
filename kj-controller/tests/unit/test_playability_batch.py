"""Unit tests for the library batch runner (walker, manifest, JSONL)."""
import json
import os

import playability_batch as pb


def test_iter_media_files_filters_extensions(tmp_path):
    (tmp_path / "a.mp4").write_bytes(b"x")
    (tmp_path / "b.zip").write_bytes(b"x")
    (tmp_path / "c.txt").write_bytes(b"x")
    sub = tmp_path / "sub"; sub.mkdir()
    (sub / "d.mkv").write_bytes(b"x")
    found = sorted(os.path.basename(p) for p in pb.iter_media_files([str(tmp_path)], pb.DEFAULT_EXTS))
    assert found == ["a.mp4", "b.zip", "d.mkv"]


def test_manifest_skip_unchanged(tmp_path):
    f = tmp_path / "a.mp4"; f.write_bytes(b"hello")
    jsonl = tmp_path / "results.jsonl"
    pb.append_jsonl(str(jsonl), {"path": str(f), "size": 5, "mtime": os.stat(f).st_mtime})
    manifest = pb.load_manifest(str(jsonl))
    assert pb.is_unchanged(str(f), manifest) is True
    f.write_bytes(b"changed-size")
    manifest2 = pb.load_manifest(str(jsonl))
    assert pb.is_unchanged(str(f), manifest2) is False


def _fake_xvfb(mocker, display=":99"):
    """Stub playability_batch.XvfbDisplay so run_batch never launches a real
    off-screen X server in unit tests."""
    cls = mocker.patch("playability_batch.XvfbDisplay")
    cls.return_value.__enter__.return_value.display = display
    cls.return_value.__exit__.return_value = False
    return cls


def test_run_batch_streams_and_skips(tmp_path, mocker):
    (tmp_path / "a.mp4").write_bytes(b"x")
    (tmp_path / "b.mp4").write_bytes(b"x")
    jsonl = tmp_path / "out.jsonl"
    _fake_xvfb(mocker)

    class FakeChecker:
        def __init__(self): self.calls = 0
        def check(self, path, **kw):
            self.calls += 1
            from playability import PlayabilityResult
            r = PlayabilityResult(path=path, kind="video",
                                  size=os.stat(path).st_size, mtime=os.stat(path).st_mtime)
            r.verdict = {"overall_ok": True}
            return r

    chk = FakeChecker()
    n1 = pb.run_batch(chk, [str(tmp_path)], str(jsonl), throttle=0.0, log=lambda *a: None)
    assert n1 == 2 and chk.calls == 2
    # Second run skips both unchanged files.
    n2 = pb.run_batch(chk, [str(tmp_path)], str(jsonl), throttle=0.0, log=lambda *a: None)
    assert n2 == 0 and chk.calls == 2
    lines = [json.loads(l) for l in open(jsonl)]
    assert len(lines) == 2


def test_run_batch_wraps_in_xvfb_and_passes_display(tmp_path, mocker):
    (tmp_path / "a.mp4").write_bytes(b"x")
    jsonl = tmp_path / "out.jsonl"
    xvfb = _fake_xvfb(mocker, display=":99")

    class FakeChecker:
        def __init__(self): self.seen = []
        def check(self, path, **kw):
            self.seen.append(kw.get("display"))
            from playability import PlayabilityResult
            r = PlayabilityResult(path=path, kind="video",
                                  size=os.stat(path).st_size, mtime=os.stat(path).st_mtime)
            r.verdict = {"overall_ok": True}
            return r

    chk = FakeChecker()
    pb.run_batch(chk, [str(tmp_path)], str(jsonl), throttle=0.0, log=lambda *a: None)
    # One shared Xvfb started for the whole run, and its display threaded into check().
    xvfb.assert_called_once()
    xvfb.return_value.__enter__.assert_called_once()
    assert chk.seen == [":99"]


def _row(path, vlc, mpv, kind="video", overall=None):
    return {
        "path": path, "kind": kind,
        "integrity": {"vcodec": "h264", "acodec": "aac"},
        "renderers": {"vlc": {"frame_nonblank": vlc}, "mpv": {"frame_nonblank": mpv}},
        "verdict": {"overall_ok": overall if overall is not None else (vlc and mpv),
                    "vlc_playable": vlc, "mpv_playable": mpv, "reasons": []},
    }


def test_aggregate_buckets(tmp_path):
    jsonl = tmp_path / "r.jsonl"
    for r in [_row("/a.mp4", True, True), _row("/b.mp4", False, True),
              _row("/c.mp4", True, False), _row("/d.mp4", False, False)]:
        pb.append_jsonl(str(jsonl), r)
    agg = pb.aggregate(str(jsonl))
    assert agg["total"] == 4
    assert "/a.mp4" in agg["ok"]
    assert "/b.mp4" in agg["mpv_not_vlc"]
    assert "/c.mp4" in agg["vlc_not_mpv"]
    assert "/d.mp4" in agg["unplayable"]


def test_write_reports_emits_files(tmp_path):
    jsonl = tmp_path / "r.jsonl"
    pb.append_jsonl(str(jsonl), _row("/a.mp4", True, False))
    csv_p, md_p = tmp_path / "out.csv", tmp_path / "out.md"
    agg = pb.write_reports(str(jsonl), str(csv_p), str(md_p))
    assert csv_p.exists() and md_p.exists()
    head = open(csv_p).readline()
    assert "path" in head and "vlc" in head and "mpv" in head
    assert "/a.mp4" in open(md_p).read()
    assert agg["total"] == 1


def test_arg_parser_defaults_and_overrides():
    p = pb.build_arg_parser()
    ns = p.parse_args([])
    assert ns.throttle >= 0.0
    assert ns.depth in ("deep", "quick")
    ns2 = p.parse_args(["--roots", "/x", "/y", "--throttle", "0.5", "--limit", "3", "--depth", "quick"])
    assert ns2.roots == ["/x", "/y"] and ns2.throttle == 0.5 and ns2.limit == 3 and ns2.depth == "quick"
