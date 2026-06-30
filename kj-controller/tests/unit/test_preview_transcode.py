import os

import preview_transcode as pt


def test_ensure_hls_returns_existing_playlist_without_launch(tmp_path, monkeypatch):
    dest = tmp_path / "t"
    dest.mkdir()
    (dest / "index.m3u8").write_text("#EXTM3U")
    launched = {"n": 0}

    def fake_popen(*a, **k):
        launched["n"] += 1
        raise AssertionError("should not launch when playlist exists")

    monkeypatch.setattr(pt.subprocess, "Popen", fake_popen)
    m = pt.TranscodeManager({})
    out = m.ensure_hls(str(tmp_path / "src.mkv"), str(dest), mark_done=lambda: None)
    assert out == str(dest / "index.m3u8") and launched["n"] == 0


def test_ensure_hls_launches_and_waits_for_playlist(tmp_path, monkeypatch):
    dest = tmp_path / "t2"

    class FakeProc:
        def __init__(self):
            self.returncode = None

        def poll(self):
            return None

        def wait(self):
            return 0

        def kill(self):
            pass

    def fake_popen(cmd, **k):
        os.makedirs(dest, exist_ok=True)
        with open(os.path.join(dest, "index.m3u8"), "w") as fh:
            fh.write("#EXTM3U")
        return FakeProc()

    monkeypatch.setattr(pt.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(pt.shutil, "which", lambda name: "/usr/bin/" + name)
    m = pt.TranscodeManager({})
    out = m.ensure_hls(str(tmp_path / "src.mkv"), str(dest), mark_done=lambda: None)
    assert out.endswith("index.m3u8")


def test_ensure_hls_raises_when_ffmpeg_dies(tmp_path, monkeypatch):
    dest = tmp_path / "t3"

    class DeadProc:
        def __init__(self):
            self.returncode = 1

        def poll(self):
            return 1

        def wait(self):
            return 1

        def kill(self):
            pass

    monkeypatch.setattr(pt.subprocess, "Popen", lambda cmd, **k: DeadProc())
    monkeypatch.setattr(pt.shutil, "which", lambda name: "/usr/bin/" + name)
    m = pt.TranscodeManager({})
    try:
        m.ensure_hls(str(tmp_path / "src.mkv"), str(dest), mark_done=lambda: None)
        assert False, "expected TranscodeError"
    except pt.TranscodeError:
        pass


def test_ensure_hls_missing_ffmpeg_raises(tmp_path, monkeypatch):
    monkeypatch.setattr(pt.shutil, "which", lambda name: None)
    m = pt.TranscodeManager({})
    try:
        m.ensure_hls(str(tmp_path / "src.mkv"), str(tmp_path / "d"), mark_done=lambda: None)
        assert False, "expected TranscodeError"
    except pt.TranscodeError:
        pass
