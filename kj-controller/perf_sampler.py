"""PerfSampler: lightweight 1 Hz performance sampler for the KJ Controller.

A daemon thread samples playback health (the active engine), GPU state (i915
sysfs), per-process CPU (``/proc`` jiffy deltas), VNC client connections, package
temperature and the overlay engine's self-reported FPS into an in-memory ring
buffer (~5 min). The UI reads it via ``GET /perf/stream`` only when the panel is
open, so idle cost is just the 1 Hz sampler itself.

Design rules:
- **Never raise.** Every collector degrades to ``None`` on any error (missing
  tool, absent sysfs path, non-Linux dev box). A broken source must not take
  down the sampler or the request thread.
- **Cheap.** ``/proc`` + sysfs reads are sub-millisecond; the engine perf is one
  batched IPC round-trip; nothing writes to disk.

Path constants are module-level so tests can point them at fixtures.
"""
from __future__ import annotations

import glob
import json
import os
import subprocess
import threading
import time
from collections import deque

# --- Tunables / paths (monkeypatchable in tests) ---
PROC_ROOT = "/proc"
DRM_GT_GLOB = "/sys/class/drm/card*/gt/gt0"
THERMAL_GLOB = "/sys/class/thermal/thermal_zone*"
OVERLAY_PERF_FILE = "/tmp/kj-overlay-perf.json"
GPU_CLOCK_HELPER = "/opt/nomad/kjbox/kj-controller/set-gpu-clock.sh"

VNC_PORT = 5900
RING_SIZE = 300              # 5 min @ 1 Hz
SAMPLE_INTERVAL_S = 1.0
OVERLAY_STALE_S = 4.0        # overlay perf file older than this = engine not updating

try:
    CLK_TCK = os.sysconf("SC_CLK_TCK")
except (ValueError, AttributeError, OSError):
    CLK_TCK = 100

# Processes tracked by CPU%. comm match (exact) or cmdline substring match.
_PROC_COMM = {"mpv", "Xorg", "xfwm4", "x11vnc", "vlc", "websockify"}
_PROC_CMDLINE = {"overlay_engine": "overlay_engine.py", "flask_app": "app.py"}


# ---------------------------------------------------------------------------
# Collectors — each returns a plain value/dict or None, never raises.
# ---------------------------------------------------------------------------

def read_gpu():
    """Raw i915 GT sysfs: act/max/min freq (MHz) + rc6 residency (ms). None if absent."""
    paths = sorted(glob.glob(DRM_GT_GLOB))
    if not paths:
        return None
    base = paths[0]

    def _int(name):
        try:
            with open(os.path.join(base, name)) as f:
                return int(f.read().strip())
        except (OSError, ValueError):
            return None

    act = _int("rps_act_freq_mhz")
    mx = _int("rps_max_freq_mhz")
    if act is None and mx is None:
        return None
    return {
        "act_mhz": act,
        "max_mhz": mx,
        "min_mhz": _int("rps_min_freq_mhz"),
        "rc6_ms": _int("rc6_residency_ms"),
    }


def read_proc_cpu_raw(sampler_tid=None):
    """Aggregate (utime+stime) jiffies per tracked label. None on total failure.

    Sums across all matching PIDs for a label. If ``sampler_tid`` is given, adds
    a ``sampler`` label from that thread's own stat (to surface the monitor's cost).
    """
    out = {}
    stats = glob.glob(os.path.join(PROC_ROOT, "[0-9]*", "stat"))
    if not stats:
        return None
    for stat in stats:
        try:
            with open(stat) as f:
                data = f.read()
        except OSError:
            continue
        try:
            rp = data.rindex(")")
            comm = data[data.index("(") + 1:rp]
            rest = data[rp + 2:].split()
            jiffies = int(rest[11]) + int(rest[12])  # utime + stime
        except (ValueError, IndexError):
            continue
        label = None
        if comm in _PROC_COMM:
            label = comm
        else:
            pid = os.path.basename(os.path.dirname(stat))
            try:
                with open(os.path.join(PROC_ROOT, pid, "cmdline")) as f:
                    cmd = f.read().replace("\x00", " ")
            except OSError:
                cmd = ""
            for lbl, needle in _PROC_CMDLINE.items():
                if needle in cmd:
                    label = lbl
                    break
        if label:
            out[label] = out.get(label, 0) + jiffies

    if sampler_tid is not None:
        try:
            with open(os.path.join(PROC_ROOT, "self", "task", str(sampler_tid), "stat")) as f:
                data = f.read()
            rp = data.rindex(")")
            rest = data[rp + 2:].split()
            out["sampler"] = int(rest[11]) + int(rest[12])
        except (OSError, ValueError, IndexError):
            pass
    return out


def read_vnc_connections():
    """Count ESTABLISHED TCP connections to the local VNC port. None on failure.

    Parses /proc/net/tcp{,6} directly (no subprocess). Local port is hex; an
    ESTABLISHED socket is state 01 (the listener is 0A and is excluded).
    """
    target = VNC_PORT
    total = 0
    seen_any = False
    for name in ("net/tcp", "net/tcp6"):
        path = os.path.join(PROC_ROOT, name)
        try:
            with open(path) as f:
                lines = f.readlines()[1:]
        except OSError:
            continue
        seen_any = True
        for line in lines:
            parts = line.split()
            if len(parts) < 4:
                continue
            local = parts[1]          # ADDR:PORT in hex
            state = parts[3]
            if state != "01":         # 01 = ESTABLISHED
                continue
            try:
                port = int(local.rsplit(":", 1)[1], 16)
            except (ValueError, IndexError):
                continue
            if port == target:
                total += 1
    if not seen_any:
        return None
    return total


def read_temp_c():
    """Max package/core temperature in °C from thermal sysfs. None if unavailable."""
    best = None
    for zone in glob.glob(THERMAL_GLOB):
        try:
            with open(os.path.join(zone, "temp")) as f:
                milli = int(f.read().strip())
        except (OSError, ValueError):
            continue
        c = milli / 1000.0
        if -50 < c < 150 and (best is None or c > best):
            best = c
    return best


def read_overlay_perf(now=None):
    """Overlay engine's self-reported perf (/tmp file). None if missing.

    Returns {fps, raster_ms, active}. ``active`` is False when the file is stale
    (engine stopped or wedged) even though the file still exists.
    """
    now = time.time() if now is None else now
    try:
        with open(OVERLAY_PERF_FILE) as f:
            d = json.load(f)
    except (OSError, ValueError):
        return None
    ts = d.get("ts")
    fresh = isinstance(ts, (int, float)) and (now - ts) < OVERLAY_STALE_S
    return {
        "fps": d.get("fps"),
        "raster_ms": d.get("raster_ms"),
        "active": bool(fresh),
    }


def read_compositor():
    """xfwm4 compositing on/off via xfconf-query. None if unreadable."""
    try:
        env = dict(os.environ)
        env.setdefault("DISPLAY", ":0")
        out = subprocess.run(
            ["xfconf-query", "-c", "xfwm4", "-p", "/general/use_compositing"],
            capture_output=True, text=True, timeout=3, env=env,
        )
        if out.returncode != 0:
            return None
        return out.stdout.strip().lower() == "true"
    except (OSError, subprocess.SubprocessError):
        return None


def gpu_busy_pct(rc6_delta_ms, wall_s):
    """Rough GPU busy% from rc6 residency delta (time NOT in the deepest idle)."""
    if rc6_delta_ms is None or wall_s <= 0:
        return None
    busy = 100.0 * (1.0 - (rc6_delta_ms / (wall_s * 1000.0)))
    return round(max(0.0, min(100.0, busy)), 1)


DISPLAY_FPS_DEFAULT = 60.0


def effective_target_fps(sample):
    """The fps mpv can actually SHOW: min(container, display refresh).

    A source can't display faster than the panel. For a CDG track the container
    declares ~300 fps (the CD subcode packet rate) while the display is 60 Hz, so
    the real target is 60, not 300.
    """
    v = sample.get("video") or {}
    container = v.get("container_fps")
    display = v.get("display_fps") or DISPLAY_FPS_DEFAULT
    if container and container > 0:
        return min(container, display)
    return display  # unknown container (e.g. VLC) → assume display-rate target


def drops_are_meaningful(sample):
    """Whether vo-drops indicate *visible* skipping.

    They don't when the source over-drives the display: a 300 fps CDG on a 60 Hz
    screen must drop ~240 fps of frames that were never visible. Only when the
    container rate is at/below the display rate does a dropped frame mean a
    viewer-visible skip. Unknown container (VLC) → treat as meaningful.
    """
    v = sample.get("video") or {}
    container = v.get("container_fps")
    display = v.get("display_fps") or DISPLAY_FPS_DEFAULT
    if not container or container <= 0:
        return True
    return container <= display * 1.1


def _fps_short(sample, factor):
    """True when a playing sample's render fps is below factor*target."""
    if not sample.get("playing"):
        return False
    target = effective_target_fps(sample)
    rf = sample.get("render_fps")
    return bool(target and isinstance(rf, (int, float)) and rf < factor * target)


def compute_health(samples):
    """Derive green/amber/red from the recent tail of samples.

    Signals that mean a *viewer-visible* problem: decoder drops (a frame that
    failed to decode — always real), meaningful vo-drops (source not over-driving
    the display), render fps falling short of the display-capped target, GPU
    pegged-but-not-boosting, and temperature. Benign high-fps decimation (CDG) is
    deliberately NOT counted.
    """
    tail = list(samples)[-5:]
    if not tail:
        return "green"
    now = tail[-1]
    temp = now.get("temp_c")
    if temp is not None and temp >= 95:
        return "red"

    dec_drops = sum(1 for s in tail if (s.get("drops_delta") or {}).get("decoder"))
    vo_drops = sum(
        1 for s in tail
        if drops_are_meaningful(s) and (
            (s.get("drops_delta") or {}).get("vo") or (s.get("drops_delta") or {}).get("vlc_lost"))
    )
    bad_short = sum(1 for s in tail if _fps_short(s, 0.75))
    if dec_drops >= 1 or vo_drops >= 3 or bad_short >= 3:
        return "red"

    gpu = now.get("gpu") or {}
    starved = (
        now.get("playing")
        and (gpu.get("busy_pct") or 0) >= 95
        and gpu.get("act_mhz") is not None and gpu.get("max_mhz") is not None
        and gpu["act_mhz"] < gpu["max_mhz"]
    )
    mild_short = any(_fps_short(s, 0.92) for s in tail)
    if (temp is not None and temp >= 85) or vo_drops >= 1 or starved or mild_short:
        return "amber"
    return "green"


def apply_toggle(control, on):
    """Perform an A/B toggle side effect. Returns (ok, new_state, message).

    control ∈ {overlay, compositor, gpu-clock}. Best-effort; never raises.
    """
    on = bool(on)
    try:
        if control == "overlay":
            action = "start" if on else "stop"
            r = subprocess.run(["sudo", "-n", "systemctl", action, "overlay-display"],
                               capture_output=True, text=True, timeout=10)
            return (r.returncode == 0, on, r.stderr.strip() or "ok")
        if control == "compositor":
            env = dict(os.environ)
            env.setdefault("DISPLAY", ":0")
            r = subprocess.run(
                ["xfconf-query", "-c", "xfwm4", "-p", "/general/use_compositing",
                 "-s", "true" if on else "false"],
                capture_output=True, text=True, timeout=5, env=env)
            return (r.returncode == 0, on, r.stderr.strip() or "ok")
        if control == "gpu-clock":
            arg = "pin" if on else "unpin"
            r = subprocess.run(["sudo", "-n", GPU_CLOCK_HELPER, arg],
                               capture_output=True, text=True, timeout=10)
            return (r.returncode == 0, on, r.stderr.strip() or "ok")
    except (OSError, subprocess.SubprocessError) as e:
        return (False, None, str(e))
    return (False, None, f"unknown control: {control}")


# ---------------------------------------------------------------------------
# The sampler
# ---------------------------------------------------------------------------

class PerfSampler:
    """1 Hz sampler + ring buffer. Attach to the app; call start() at runtime."""

    def __init__(self, coordinator, cfg=None, interval=SAMPLE_INTERVAL_S):
        from perf_recorder import PerfRecorder
        self._coord = coordinator
        self._cfg = cfg or {}
        self._interval = interval
        # Auto-pin the iGPU to its max clock while a song plays. Drops on 4K track
        # the GPU frequency stalling below its 1200MHz ceiling; pinning gives it
        # headroom. Best-effort + engine-agnostic; unpins when idle.
        self._auto_pin = self._cfg.get('auto_pin_gpu_during_playback', True)
        # Seeded False so an app that starts up mid-song (playing=True) pins on the
        # first tick instead of swallowing it as initialization.
        self._pin_prev_playing = False
        self._we_pinned = False
        rec_dir = self._cfg.get('perf_recordings_dir') or \
            os.path.expanduser('~/kjdata/perf_recordings')
        self.recorder = PerfRecorder(rec_dir)
        self._ring = deque(maxlen=RING_SIZE)
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = None
        self._tid = None
        self._prev = None                 # previous raw snapshot for deltas
        self._last_status_latency_ms = None

    # -- lifecycle --
    def start(self):
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="perf-sampler", daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        # Wait for the sampler thread to actually exit (unless we're calling from
        # within it) so stop() is synchronous and a restart can't race the old
        # daemon thread.
        if (self._thread is not None and self._thread.is_alive()
                and threading.current_thread() is not self._thread):
            self._thread.join(timeout=max(1.0, self._interval + 0.5))
        self._thread = None
        # Leave the GPU in its default (unpinned) state if we pinned it.
        if self._we_pinned:
            try:
                apply_toggle('gpu-clock', False)
            except Exception:
                pass
            self._we_pinned = False

    def _run(self):
        try:
            self._tid = threading.get_native_id()
        except (AttributeError, OSError):
            self._tid = None
        while not self._stop.is_set():
            try:
                s = self._build_sample()
                with self._lock:
                    self._ring.append(s)
                self.recorder.record(s)  # no-op unless a session is active
                self._maybe_autopin(bool(s.get("playing")))
            except Exception:
                pass  # a sampler must never die on a bad tick
            self._stop.wait(self._interval)

    def _maybe_autopin(self, playing):
        """Pin the iGPU to max on the play edge, unpin on the stop edge.

        No-op unless enabled; on a device without the sudo helper/entry the
        underlying toggle simply fails and we leave _we_pinned False.
        """
        if not self._auto_pin:
            return
        if playing == self._pin_prev_playing:
            return
        self._pin_prev_playing = playing
        ok, _, _ = apply_toggle('gpu-clock', playing)
        if ok:
            self._we_pinned = playing

    # -- external hooks --
    def record_status_latency(self, ms):
        self._last_status_latency_ms = ms

    def toggle(self, control, on):
        return apply_toggle(control, on)

    # -- sampling --
    def _build_sample(self):
        now = time.time()
        prev = self._prev
        dt = (now - prev["t"]) if prev else self._interval

        # engine perf (batched IPC / http)
        try:
            eng = self._coord.get_perf()
        except Exception:
            eng = {"playing": False}

        gpu_raw = read_gpu()
        cpu_raw = read_proc_cpu_raw(self._tid)
        vnc = read_vnc_connections()
        temp = read_temp_c()
        overlay = read_overlay_perf(now)
        compositor = read_compositor()

        # --- deltas ---
        cpu_pct = {}
        if cpu_raw and prev and prev.get("cpu") and dt > 0:
            for label, j in cpu_raw.items():
                if label in prev["cpu"]:
                    dj = j - prev["cpu"][label]
                    cpu_pct[label] = round(max(0.0, 100.0 * dj / CLK_TCK / dt), 1)

        busy = None
        if gpu_raw and prev and prev.get("gpu") and gpu_raw.get("rc6_ms") is not None \
                and prev["gpu"].get("rc6_ms") is not None:
            busy = gpu_busy_pct(gpu_raw["rc6_ms"] - prev["gpu"]["rc6_ms"], dt)

        drops = {
            "vo": eng.get("vo_drops"),
            "decoder": eng.get("decoder_drops"),
            "delayed": eng.get("delayed"),
            "vlc_lost": eng.get("vlc_lost"),
        }
        drops_delta = {"vo": 0, "decoder": 0, "delayed": 0, "vlc_lost": 0}
        render_fps = eng.get("render_fps")
        if prev and prev.get("eng"):
            pe = prev["eng"]
            for k, src in (("vo", "vo_drops"), ("decoder", "decoder_drops"),
                           ("delayed", "delayed"), ("vlc_lost", "vlc_lost")):
                a, b = pe.get(src), eng.get(src)
                if isinstance(a, (int, float)) and isinstance(b, (int, float)) and b >= a:
                    drops_delta[k] = b - a
            # VLC has no instantaneous fps; derive it from displayed-picture delta
            if render_fps is None:
                a, b = pe.get("vlc_displayed"), eng.get("vlc_displayed")
                if isinstance(a, (int, float)) and isinstance(b, (int, float)) and b >= a and dt > 0:
                    render_fps = round((b - a) / dt, 2)

        gpu = None
        if gpu_raw:
            pinned = (gpu_raw.get("min_mhz") is not None and gpu_raw.get("max_mhz") is not None
                      and gpu_raw["min_mhz"] >= gpu_raw["max_mhz"])
            gpu = {"busy_pct": busy, "act_mhz": gpu_raw.get("act_mhz"),
                   "max_mhz": gpu_raw.get("max_mhz"), "pinned": pinned}

        sample = {
            "t": now,
            "engine": eng.get("engine"),
            "playing": bool(eng.get("playing")),
            "video": {
                "codec": eng.get("codec"), "w": eng.get("width"), "h": eng.get("height"),
                "container_fps": eng.get("container_fps"), "hwdec": eng.get("hwdec"),
                "display_fps": eng.get("display_fps"),
            },
            "render_fps": render_fps,
            "drops": drops,
            "drops_delta": drops_delta,
            "gpu": gpu,
            "cpu": cpu_pct,
            "overlay": overlay,
            "vnc_connected": (vnc or 0) > 0 if vnc is not None else None,
            "compositor": compositor,
            "temp_c": temp,
            "status_latency_ms": self._last_status_latency_ms,
        }
        sample["fps_target"] = effective_target_fps(sample)
        sample["drops_meaningful"] = drops_are_meaningful(sample)
        sample["health"] = compute_health(list(self._ring) + [sample])

        # stash raw values needed for next tick's deltas
        self._prev = {"t": now, "cpu": cpu_raw or {}, "gpu": gpu_raw or {}, "eng": eng}
        return sample

    def snapshot(self):
        """Return the ring + latest sample. Samples on-demand if the thread is idle."""
        with self._lock:
            if not self._ring:
                # allow the endpoint to work in tests / before the thread warms up
                try:
                    self._ring.append(self._build_sample())
                except Exception:
                    pass
            samples = list(self._ring)
        now = samples[-1] if samples else None
        controls = {
            "overlay": bool((now or {}).get("cpu", {}).get("overlay_engine") is not None)
            if now else None,
            "compositor": (now or {}).get("compositor"),
            "gpu_pinned": bool(((now or {}).get("gpu") or {}).get("pinned")) if now else None,
        }
        return {
            "sample_interval_s": self._interval,
            "controls": controls,
            "now": now,
            "samples": samples,
        }
