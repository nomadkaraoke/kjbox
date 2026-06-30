import os

import preview_transcode as pt


def test_ensure_hls_discards_stale_partial_and_relaunches(tmp_path, monkeypatch):
    # A stale partial playlist (from a killed job, no .done) must NOT be reused —
    # ensure_hls always rebuilds fresh (the cache-hit decision is the caller's job).
    dest = tmp_path / "t"
    dest.mkdir()
    (dest / "index.m3u8").write_text("#EXTM3U:stale")
    (dest / "seg-0.ts").write_bytes(b"stale")
    launched = {"n": 0}

    class FakeProc:
        def poll(self):
            return None

        def wait(self):
            return 0

        def kill(self):
            pass

    def fake_popen(cmd, **k):
        launched["n"] += 1
        os.makedirs(dest, exist_ok=True)
        with open(os.path.join(dest, "index.m3u8"), "w") as fh:
            fh.write("#EXTM3U:fresh")
        return FakeProc()

    monkeypatch.setattr(pt.shutil, "which", lambda name: "/usr/bin/" + name)
    monkeypatch.setattr(pt.subprocess, "Popen", fake_popen)
    m = pt.TranscodeManager({})
    out = m.ensure_hls(str(tmp_path / "src.mkv"), str(dest), mark_done=lambda: None)
    assert out == str(dest / "index.m3u8") and launched["n"] == 1
    assert not os.path.exists(str(dest / "seg-0.ts"))  # stale segment cleared
    assert "fresh" in open(out).read()


def test_kill_active_removes_partial_dir(tmp_path, monkeypatch):
    import threading
    dest = tmp_path / "killme"

    class FakeProc:
        def __init__(self):
            self._ev = threading.Event()
            self._killed = False

        def poll(self):
            return -9 if self._killed else None  # "running" until killed

        def wait(self):
            self._ev.wait(5)
            return -9

        def kill(self):
            self._killed = True
            self._ev.set()

    def fake_popen(cmd, **k):
        os.makedirs(dest, exist_ok=True)
        with open(os.path.join(dest, "index.m3u8"), "w") as fh:
            fh.write("#EXTM3U")
        return FakeProc()

    monkeypatch.setattr(pt.shutil, "which", lambda name: "/usr/bin/" + name)
    monkeypatch.setattr(pt.subprocess, "Popen", fake_popen)
    m = pt.TranscodeManager({})
    m.ensure_hls(str(tmp_path / "src.mkv"), str(dest), mark_done=lambda: None)
    assert os.path.isdir(dest)
    m.kill_active()
    assert not os.path.isdir(dest)  # partial dir removed on kill


def test_self_failed_transcode_cleans_partial_dir(tmp_path, monkeypatch):
    # ffmpeg exiting non-zero on its own must not leak its partial dir.
    import time as _time
    dest = tmp_path / "failme"

    class FakeProc:
        def poll(self):
            return 1

        def wait(self):
            return 1

        def kill(self):
            pass

    def fake_popen(cmd, **k):
        os.makedirs(dest, exist_ok=True)
        with open(os.path.join(dest, "index.m3u8"), "w") as fh:
            fh.write("#EXTM3U")
        return FakeProc()

    monkeypatch.setattr(pt.shutil, "which", lambda name: "/usr/bin/" + name)
    monkeypatch.setattr(pt.subprocess, "Popen", fake_popen)
    m = pt.TranscodeManager({})
    try:
        m.ensure_hls(str(tmp_path / "src.mkv"), str(dest), mark_done=lambda: None)
    except pt.TranscodeError:
        pass
    for _ in range(50):  # let the watch thread run
        if not os.path.isdir(dest):
            break
        _time.sleep(0.05)
    assert not os.path.isdir(dest)


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
