"""Unit tests for perf_sampler — collectors, delta math, health, and the sampler.

Collectors read Linux ``/proc`` and sysfs; tests point the module's path
constants at fixtures so they run anywhere (incl. the macOS dev box), and also
assert graceful degradation to ``None`` when a source is absent.
"""
import json
import os

import pytest

import perf_sampler as ps


# --- read_gpu ---

def test_read_gpu_parses_sysfs(tmp_path, monkeypatch):
    gt = tmp_path / "card1" / "gt" / "gt0"
    gt.mkdir(parents=True)
    (gt / "rps_act_freq_mhz").write_text("1000\n")
    (gt / "rps_max_freq_mhz").write_text("1200\n")
    (gt / "rps_min_freq_mhz").write_text("300\n")
    (gt / "rc6_residency_ms").write_text("500\n")
    monkeypatch.setattr(ps, "DRM_GT_GLOB", str(tmp_path / "card*" / "gt" / "gt0"))
    g = ps.read_gpu()
    assert g == {"act_mhz": 1000, "max_mhz": 1200, "min_mhz": 300, "rc6_ms": 500}


def test_read_gpu_absent_returns_none(tmp_path, monkeypatch):
    monkeypatch.setattr(ps, "DRM_GT_GLOB", str(tmp_path / "nope" / "*"))
    assert ps.read_gpu() is None


# --- read_proc_cpu_raw ---

def _write_proc(root, pid, comm, utime, stime, cmdline=""):
    d = root / str(pid)
    d.mkdir(parents=True)
    fields = ["S", "1", "1", "1", "0", "-1", "0", "0", "0", "0", "0",
              str(utime), str(stime)] + ["0"] * 20
    (d / "stat").write_text(f"{pid} ({comm}) " + " ".join(fields) + "\n")
    (d / "cmdline").write_text(cmdline.replace(" ", "\x00"))


def test_read_proc_cpu_raw_by_comm_and_cmdline(tmp_path, monkeypatch):
    proc = tmp_path / "proc"
    _write_proc(proc, 100, "mpv", 700, 50)
    _write_proc(proc, 101, "Xorg", 200, 20)
    _write_proc(proc, 102, "python3", 30, 5, cmdline="python3 /opt/nomad/kjbox/desktop/overlay_engine.py")
    _write_proc(proc, 103, "python3", 10, 2, cmdline="python3 /opt/nomad/kjbox/kj-controller/app.py")
    _write_proc(proc, 104, "unrelated", 999, 999)
    monkeypatch.setattr(ps, "PROC_ROOT", str(proc))
    out = ps.read_proc_cpu_raw()
    assert out["mpv"] == 750
    assert out["Xorg"] == 220
    assert out["overlay_engine"] == 35
    assert out["flask_app"] == 12
    assert "unrelated" not in out


def test_read_proc_cpu_raw_absent_returns_none(tmp_path, monkeypatch):
    monkeypatch.setattr(ps, "PROC_ROOT", str(tmp_path / "nope"))
    assert ps.read_proc_cpu_raw() is None


# --- read_vnc_connections ---

def test_read_vnc_connections_counts_established(tmp_path, monkeypatch):
    net = tmp_path / "proc" / "net"
    net.mkdir(parents=True)
    # 170C == 5900. State 01 = ESTABLISHED, 0A = LISTEN.
    net.joinpath("tcp").write_text(
        "  sl  local_address rem_address   st ...\n"
        "   0: 0100007F:170C 0100007F:C001 01 x\n"   # established client
        "   1: 00000000:170C 00000000:0000 0A x\n"   # listener (ignored)
        "   2: 0100007F:1F90 0100007F:C002 01 x\n"   # different port (ignored)
    )
    net.joinpath("tcp6").write_text("  sl  local_address ...\n")
    monkeypatch.setattr(ps, "PROC_ROOT", str(tmp_path / "proc"))
    assert ps.read_vnc_connections() == 1


def test_read_vnc_connections_absent_returns_none(tmp_path, monkeypatch):
    monkeypatch.setattr(ps, "PROC_ROOT", str(tmp_path / "nope"))
    assert ps.read_vnc_connections() is None


# --- read_temp_c ---

def test_read_temp_c_takes_max(tmp_path, monkeypatch):
    for i, milli in enumerate((68000, 72000, 27800)):
        z = tmp_path / f"thermal_zone{i}"
        z.mkdir()
        (z / "temp").write_text(str(milli))
    monkeypatch.setattr(ps, "THERMAL_GLOB", str(tmp_path / "thermal_zone*"))
    assert ps.read_temp_c() == 72.0


def test_read_temp_c_absent_returns_none(tmp_path, monkeypatch):
    monkeypatch.setattr(ps, "THERMAL_GLOB", str(tmp_path / "nope*"))
    assert ps.read_temp_c() is None


# --- read_overlay_perf ---

def test_read_overlay_perf_fresh_is_active(tmp_path, monkeypatch):
    f = tmp_path / "overlay.json"
    f.write_text(json.dumps({"fps": 29.8, "raster_ms": 1.2, "ts": 1000.0}))
    monkeypatch.setattr(ps, "OVERLAY_PERF_FILE", str(f))
    r = ps.read_overlay_perf(now=1001.0)
    assert r == {"fps": 29.8, "raster_ms": 1.2, "active": True}


def test_read_overlay_perf_stale_is_inactive(tmp_path, monkeypatch):
    f = tmp_path / "overlay.json"
    f.write_text(json.dumps({"fps": 0, "raster_ms": 0, "ts": 1000.0}))
    monkeypatch.setattr(ps, "OVERLAY_PERF_FILE", str(f))
    r = ps.read_overlay_perf(now=1100.0)   # 100s old
    assert r["active"] is False


def test_read_overlay_perf_missing_returns_none(tmp_path, monkeypatch):
    monkeypatch.setattr(ps, "OVERLAY_PERF_FILE", str(tmp_path / "nope.json"))
    assert ps.read_overlay_perf() is None


# --- gpu_busy_pct ---

def test_gpu_busy_pct():
    assert ps.gpu_busy_pct(0, 1.0) == 100.0       # 0ms idle over 1s = 100% busy
    assert ps.gpu_busy_pct(1000, 1.0) == 0.0      # fully idle
    assert ps.gpu_busy_pct(500, 1.0) == 50.0
    assert ps.gpu_busy_pct(None, 1.0) is None
    assert ps.gpu_busy_pct(100, 0) is None


# --- compute_health ---

def _s(**kw):
    base = {"playing": True, "drops_delta": {"vo": 0, "vlc_lost": 0}, "temp_c": 60,
            "gpu": {"busy_pct": 50, "act_mhz": 1200, "max_mhz": 1200}}
    base.update(kw)
    return base


def test_health_green_when_healthy():
    assert ps.compute_health([_s() for _ in range(5)]) == "green"


def test_health_amber_on_single_drop():
    tail = [_s() for _ in range(4)] + [_s(drops_delta={"vo": 1, "vlc_lost": 0})]
    assert ps.compute_health(tail) == "amber"


def test_health_amber_on_gpu_starved():
    tail = [_s(gpu={"busy_pct": 99, "act_mhz": 1000, "max_mhz": 1200}) for _ in range(5)]
    assert ps.compute_health(tail) == "amber"


def test_health_red_on_sustained_drops():
    tail = [_s(drops_delta={"vo": 2, "vlc_lost": 0}) for _ in range(5)]
    assert ps.compute_health(tail) == "red"


def test_health_red_on_hot():
    assert ps.compute_health([_s(temp_c=97) for _ in range(5)]) == "red"


# --- PerfSampler (with a fake coordinator) ---

class _FakeCoord:
    def __init__(self, seq):
        self._seq = list(seq)
        self.i = 0

    def get_perf(self):
        v = self._seq[min(self.i, len(self._seq) - 1)]
        self.i += 1
        return v


def _isolate(monkeypatch, tmp_path):
    """Point every collector at absent paths so only the fake engine drives output."""
    monkeypatch.setattr(ps, "PROC_ROOT", str(tmp_path / "noproc"))
    monkeypatch.setattr(ps, "DRM_GT_GLOB", str(tmp_path / "nogpu" / "*"))
    monkeypatch.setattr(ps, "THERMAL_GLOB", str(tmp_path / "notherm*"))
    monkeypatch.setattr(ps, "OVERLAY_PERF_FILE", str(tmp_path / "noov.json"))
    monkeypatch.setattr(ps, "read_compositor", lambda: None)


def test_sampler_snapshot_shape_and_ondemand(tmp_path, monkeypatch):
    _isolate(monkeypatch, tmp_path)
    coord = _FakeCoord([{"engine": "mpv", "playing": True, "render_fps": 30.0,
                         "vo_drops": 0, "decoder_drops": 0, "delayed": 0, "vlc_lost": None}])
    s = ps.PerfSampler(coord)
    snap = s.snapshot()   # thread not started -> samples on demand
    assert snap["sample_interval_s"] == ps.SAMPLE_INTERVAL_S
    assert snap["now"]["engine"] == "mpv"
    assert snap["now"]["render_fps"] == 30.0
    assert snap["now"]["health"] in ("green", "amber", "red")
    assert isinstance(snap["samples"], list) and len(snap["samples"]) == 1


def test_sampler_computes_drop_delta_across_ticks(tmp_path, monkeypatch):
    _isolate(monkeypatch, tmp_path)
    coord = _FakeCoord([
        {"engine": "mpv", "playing": True, "render_fps": 30.0, "vo_drops": 5,
         "decoder_drops": 0, "delayed": 0, "vlc_lost": None},
        {"engine": "mpv", "playing": True, "render_fps": 30.0, "vo_drops": 7,
         "decoder_drops": 0, "delayed": 0, "vlc_lost": None},
    ])
    s = ps.PerfSampler(coord)
    s._build_sample()               # tick 1 primes prev
    with s._lock:
        s._ring.append(s._build_sample())  # tick 2
    assert s._ring[-1]["drops_delta"]["vo"] == 2


def test_sampler_derives_vlc_fps_from_displayed_delta(tmp_path, monkeypatch):
    _isolate(monkeypatch, tmp_path)
    coord = _FakeCoord([
        {"engine": "vlc", "playing": True, "render_fps": None, "vlc_displayed": 100,
         "vlc_lost": 0, "vo_drops": None, "decoder_drops": None, "delayed": None},
        {"engine": "vlc", "playing": True, "render_fps": None, "vlc_displayed": 130,
         "vlc_lost": 0, "vo_drops": None, "decoder_drops": None, "delayed": None},
    ])
    s = ps.PerfSampler(coord, interval=1.0)
    s._build_sample()
    # force dt=1.0 by stamping prev's time exactly one interval back
    s._prev["t"] = s._prev["t"] - 0.0
    import time as _t
    monkeypatch.setattr(_t, "time", lambda: s._prev["t"] + 1.0)
    s2 = s._build_sample()
    assert s2["render_fps"] == 30.0   # (130-100)/1.0s


def test_sampler_ring_caps_at_maxlen(tmp_path, monkeypatch):
    _isolate(monkeypatch, tmp_path)
    coord = _FakeCoord([{"engine": None, "playing": False}])
    s = ps.PerfSampler(coord)
    for _ in range(ps.RING_SIZE + 25):
        with s._lock:
            s._ring.append(s._build_sample())
    assert len(s._ring) == ps.RING_SIZE


def test_sampler_never_raises_on_all_sources_absent(tmp_path, monkeypatch):
    _isolate(monkeypatch, tmp_path)

    class Boom:
        def get_perf(self):
            raise RuntimeError("engine down")

    s = ps.PerfSampler(Boom())
    snap = s.snapshot()   # must not raise even when the engine itself throws
    assert snap["now"]["playing"] is False


def test_apply_toggle_unknown_control():
    ok, state, msg = ps.apply_toggle("bogus", True)
    assert ok is False and "unknown control" in msg


# --- CDG-aware fps target / drop meaningfulness / health ---

def test_effective_target_fps_caps_at_display():
    assert ps.effective_target_fps({"video": {"container_fps": 300, "display_fps": 60}}) == 60
    assert ps.effective_target_fps({"video": {"container_fps": 30, "display_fps": 60}}) == 30
    assert ps.effective_target_fps({"video": {"container_fps": None, "display_fps": 50}}) == 50
    assert ps.effective_target_fps({"video": {}}) == ps.DISPLAY_FPS_DEFAULT


def test_drops_meaningful_false_for_cdg():
    # 300fps CDG on a 60Hz display: vo-drops are just decimation, not visible.
    assert ps.drops_are_meaningful({"video": {"container_fps": 300, "display_fps": 60}}) is False
    # 30fps h264 <= display: a dropped frame IS visible.
    assert ps.drops_are_meaningful({"video": {"container_fps": 30, "display_fps": 60}}) is True
    # unknown container (VLC) -> treat as meaningful.
    assert ps.drops_are_meaningful({"video": {}}) is True


def _cdg(vo_delta, render_fps=58.0):
    return {"playing": True, "render_fps": render_fps, "fps_target": 60,
            "video": {"container_fps": 300, "display_fps": 60},
            "drops_delta": {"vo": vo_delta, "decoder": 0, "vlc_lost": 0},
            "temp_c": 60, "gpu": {"busy_pct": 40, "act_mhz": 500, "max_mhz": 1200}}


def _h264(vo_delta, render_fps=30.0):
    return {"playing": True, "render_fps": render_fps, "fps_target": 30,
            "video": {"container_fps": 30, "display_fps": 60},
            "drops_delta": {"vo": vo_delta, "decoder": 0, "vlc_lost": 0},
            "temp_c": 60, "gpu": {"busy_pct": 40, "act_mhz": 1200, "max_mhz": 1200}}


def test_health_cdg_benign_drops_not_red():
    # Sustained vo-drops on a CDG track must NOT read as red (they're invisible).
    assert ps.compute_health([_cdg(9) for _ in range(5)]) == "green"


def test_health_h264_meaningful_drops_are_red():
    # The same drop rate on a 30fps h264 source IS a visible problem.
    assert ps.compute_health([_h264(2) for _ in range(5)]) == "red"


def test_health_red_on_fps_shortfall():
    # render fps collapsing below 75% of target = red regardless of drop bookkeeping.
    tail = [_h264(0, render_fps=18.0) for _ in range(5)]
    assert ps.compute_health(tail) == "red"


def test_health_decoder_drops_always_red():
    s = _cdg(0)
    s["drops_delta"]["decoder"] = 1   # a real decode failure, even on CDG
    assert ps.compute_health([_cdg(0), _cdg(0), s]) == "red"
