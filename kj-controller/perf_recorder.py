"""PerfRecorder — persist labeled perf sessions to disk for before/after analysis.

While recording, each 1 Hz sample the PerfSampler produces is appended as one JSON
line to a session file (``<dir>/<YYYYmmdd-HHMMSS>-<label>.jsonl``). Sessions can be
listed, summarised (aggregate stats over the whole capture) and downloaded. This
is the mechanism for well-informed before/after decisions — e.g. Phase-2
compositing-fix validation or a CDG-vs-h264 comparison — instead of eyeballing the
live panel.

Never raises on the record path (called from the sampler thread); a bad write is
dropped, not propagated.
"""
from __future__ import annotations

import json
import os
import re
import threading
import time

_LABEL_STRIP = re.compile(r'[^A-Za-z0-9_-]+')
# Session ids are self-generated: date-time-label. Validated on lookup so a
# client-supplied id can never traverse outside the recordings dir.
_ID_RE = re.compile(r'^\d{8}-\d{6}-[A-Za-z0-9_-]+$')


def sanitize_label(label):
    label = (label or "session").strip().replace(" ", "-")
    label = _LABEL_STRIP.sub("", label)[:48]
    return label or "session"


def summarize_file(path):
    """Aggregate a session JSONL into a compact stats dict. None on read error."""
    import statistics
    samples = []
    try:
        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    d = json.loads(line)
                except ValueError:
                    continue
                if d.get("_meta"):
                    continue
                samples.append(d)
    except OSError:
        return None

    sid = os.path.basename(path)[:-6] if path.endswith(".jsonl") else os.path.basename(path)
    if not samples:
        return {"id": sid, "samples": 0}

    def rnd(x, n=1):
        return round(x, n) if isinstance(x, (int, float)) else None

    def drop(s, key):
        return (s.get("drops_delta") or {}).get(key) or 0

    ts = [s["t"] for s in samples if isinstance(s.get("t"), (int, float))]
    duration = (max(ts) - min(ts)) if len(ts) >= 2 else 0.0
    playing = [s for s in samples if s.get("playing")]

    rf = [s["render_fps"] for s in playing if isinstance(s.get("render_fps"), (int, float))]
    vo_real = sum(drop(s, "vo") for s in samples if s.get("drops_meaningful"))
    vo_total = sum(drop(s, "vo") for s in samples)
    dec = sum(drop(s, "decoder") for s in samples)
    lost = sum(drop(s, "vlc_lost") for s in samples)

    fps_ok = fps_playing = 0
    for s in playing:
        t, r = s.get("fps_target"), s.get("render_fps")
        if isinstance(t, (int, float)) and isinstance(r, (int, float)) and t > 0:
            fps_playing += 1
            if r >= 0.92 * t:
                fps_ok += 1

    gpu_busy = [(s.get("gpu") or {}).get("busy_pct") for s in samples]
    gpu_busy = [v for v in gpu_busy if isinstance(v, (int, float))]

    cpu_avg = {}
    for k in ("mpv", "Xorg", "x11vnc", "overlay_engine", "xfwm4", "vlc", "flask_app", "sampler"):
        vs = [(s.get("cpu") or {}).get(k) for s in samples]
        vs = [v for v in vs if isinstance(v, (int, float))]
        if vs:
            cpu_avg[k] = round(statistics.mean(vs), 1)

    temps = [s["temp_c"] for s in samples if isinstance(s.get("temp_c"), (int, float))]
    health = {"green": 0, "amber": 0, "red": 0}
    for s in samples:
        if s.get("health") in health:
            health[s["health"]] += 1

    engines = sorted({s.get("engine") for s in playing if s.get("engine")})
    hwdecs = sorted({(s.get("video") or {}).get("hwdec") for s in playing
                     if (s.get("video") or {}).get("hwdec")})

    return {
        "id": sid,
        "samples": len(samples),
        "duration_s": round(duration, 1),
        "engines": engines,
        "hwdec": hwdecs,
        "render_fps": {"min": rnd(min(rf)) if rf else None,
                       "avg": rnd(statistics.mean(rf)) if rf else None},
        "fps_ok_pct": round(100 * fps_ok / fps_playing, 1) if fps_playing else None,
        "drops": {"vo_real": vo_real, "vo_total": vo_total, "decoder": dec, "vlc_lost": lost},
        "drops_real_per_min": round(60 * vo_real / duration, 1) if duration > 0 else None,
        "gpu_busy": {"avg": rnd(statistics.mean(gpu_busy)) if gpu_busy else None,
                     "max": rnd(max(gpu_busy)) if gpu_busy else None},
        "cpu_avg": cpu_avg,
        "temp_max": rnd(max(temps)) if temps else None,
        "health": health,
    }


class PerfRecorder:
    """Owns the current recording session; the sampler feeds it each tick."""

    def __init__(self, recordings_dir):
        self._dir = recordings_dir
        self._lock = threading.Lock()
        self._fh = None
        self._active = False
        self._label = None
        self._id = None
        self._started = None
        self._count = 0

    def _status_locked(self):
        return {
            "recording": self._active,
            "id": self._id,
            "label": self._label,
            "started": self._started,
            "elapsed": (time.time() - self._started) if (self._active and self._started) else None,
            "sample_count": self._count,
            "dir": self._dir,
        }

    def status(self):
        with self._lock:
            return self._status_locked()

    def start(self, label):
        with self._lock:
            if self._active:
                return self._status_locked()
            label = sanitize_label(label)
            try:
                os.makedirs(self._dir, exist_ok=True)
                stamp = time.strftime("%Y%m%d-%H%M%S")
                sid = f"{stamp}-{label}"
                path = os.path.join(self._dir, sid + ".jsonl")
                n = 1
                while os.path.exists(path):
                    sid = f"{stamp}-{label}-{n}"
                    path = os.path.join(self._dir, sid + ".jsonl")
                    n += 1
                self._fh = open(path, "a")
                self._fh.write(json.dumps(
                    {"_meta": True, "label": label, "id": sid, "started": time.time()}) + "\n")
                self._fh.flush()
            except OSError as e:
                return {"recording": False, "error": str(e)}
            self._active = True
            self._label = label
            self._id = sid
            self._started = time.time()
            self._count = 0
            return self._status_locked()

    def record(self, sample):
        """Append one sample. Called from the sampler thread; never raises."""
        if not self._active:
            return
        with self._lock:
            if not self._active or self._fh is None:
                return
            try:
                self._fh.write(json.dumps(sample) + "\n")
                self._fh.flush()
                self._count += 1
            except OSError:
                pass

    def stop(self):
        with self._lock:
            st = self._status_locked()
            if self._fh is not None:
                try:
                    self._fh.close()
                except OSError:
                    pass
            self._fh = None
            self._active = False
            st["recording"] = False
            return st

    def list(self):
        try:
            files = [f for f in os.listdir(self._dir) if f.endswith(".jsonl")]
        except OSError:
            return []
        out = []
        for f in sorted(files, reverse=True):
            sid = f[:-6]
            path = os.path.join(self._dir, f)
            entry = {"id": sid, "label": sid, "started": None, "size": None,
                     "active": (sid == self._id and self._active)}
            try:
                entry["size"] = os.path.getsize(path)
                with open(path) as fh:
                    head = fh.readline()
                try:
                    h = json.loads(head)
                    if h.get("_meta"):
                        entry["label"] = h.get("label", sid)
                        entry["started"] = h.get("started")
                except ValueError:
                    pass
            except OSError:
                pass
            out.append(entry)
        return out

    def path_for(self, session_id):
        """Resolve a session id to its file path, or None. Traversal-safe."""
        if not session_id or not _ID_RE.match(session_id):
            return None
        path = os.path.join(self._dir, session_id + ".jsonl")
        if os.path.dirname(os.path.abspath(path)) != os.path.abspath(self._dir):
            return None
        return path if os.path.isfile(path) else None

    def summary(self, session_id):
        path = self.path_for(session_id)
        if not path:
            return None
        return summarize_file(path)
