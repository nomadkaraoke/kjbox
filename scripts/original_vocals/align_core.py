"""Pure alignment logic (no device/ffmpeg I/O) — unit-tested on the Mac."""
import csv
from dataclasses import dataclass, asdict, fields
import numpy as np


def first_vocal_onset(samples, sr, thresh_db=-45.0, win_s=0.05, min_run_s=0.15):
    """First time (s) the isolated-vocals guide has sustained energy >= thresh_db.
    0.0 if it starts immediately / no clear onset."""
    if samples.size == 0:
        return 0.0
    w = max(1, int(win_s * sr))
    need = max(1, int(round(min_run_s / win_s)))
    run = 0
    for i in range(0, samples.size - w, w):
        seg = samples[i:i + w].astype(np.float64)
        rms = float(np.sqrt(np.mean(seg * seg))) if seg.size else 0.0
        db = 20 * np.log10(rms) if rms > 1e-9 else -120.0
        if db >= thresh_db:
            run += 1
            if run >= need:
                return max(0.0, (i - (need - 1) * w) / sr)
        else:
            run = 0
    return 0.0


def variant_offsets(measured_s, steps_ms=(0, -100, 100, -200, 200)):
    out = []
    for s in steps_ms:
        v = round(measured_s + s / 1000.0, 3)
        if v >= 0:
            out.append(v)
    return out


def emit_af(offset_s, target_dur):
    delay_ms = int(round(offset_s * 1000))
    return (f"adelay={delay_ms}:all=1,apad,atrim=0:{target_dur:.3f},asetpts=N/SR/TB")


@dataclass
class OffsetRow:
    brand: str
    offset_s: float
    peak: float
    verdict: str
    video_dur: float
    audio_dur: float
    onset_s: float
    source: str      # measured | human
    status: str      # active | excluded


def parse_decision(value):
    v = (value or "").strip()
    if v.startswith("offset_ms="):
        try:
            return "offset", round(int(v.split("=", 1)[1]) / 1000.0, 3)
        except ValueError:
            return "invalid", None
    if v in ("confirm", "exclude", "needs-finer"):
        return v, None
    return "invalid", None


def apply_decision(row, kind, off_s):
    d = asdict(row)
    if kind == "exclude":
        d["status"] = "excluded"
    elif kind == "offset" and off_s is not None:
        d["offset_s"] = off_s
        d["source"] = "human"
        d["status"] = "active"
    elif kind == "confirm":
        d["status"] = "active"
        d["verdict"] = "confirmed"   # human agrees with the measured offset -> emit-eligible
    return OffsetRow(**d)


def write_offsets(path, rows):
    cols = [f.name for f in fields(OffsetRow)]
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for r in rows.values():
            w.writerow(asdict(r))


def read_offsets(path):
    out = {}
    with open(path, encoding="utf-8") as f:
        for d in csv.DictReader(f):
            out[d["brand"]] = OffsetRow(
                d["brand"], float(d["offset_s"]), float(d["peak"]), d["verdict"],
                float(d["video_dur"]), float(d["audio_dur"]), float(d["onset_s"]),
                d["source"], d["status"])
    return out
