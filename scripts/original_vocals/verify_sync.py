#!/usr/bin/env python3
"""Phase 2 of the original-vocals guide: verify that a harvested original mix
aligns with its released karaoke video, and emit a padded copy aligned to the
video timeline.

The released video is: [title-card intro (nominally silent)] + [instrumental] +
[tail]. The instrumental is stem-separated from the original, so the original and
the video's audio share the same non-vocal content and cross-correlate sharply.
We therefore *measure* the lead-in offset rather than assuming a 5 s intro.

Per track we compute three independent signals and combine them into a verdict:

  1. offset      cross-correlation lag of original vs. the video's audio (seconds)
  2. intro       is the first `intro` seconds of the video near-silent?
  3. duration    does video_dur ~= original_dur + intro + tail?

A track is `confirmed` when the correlation peak is sharp AND the measured offset
agrees with the silence/duration expectation; otherwise `needs-review` (which also
catches a wrong phase-1 file pick, since a mismatched recording won't correlate).

The math (`best_lag`, `normalized_peak`, `rms_db`) is pure and unit-tested; ffmpeg
/ ffprobe handle audio decode + duration + producing the padded output.

Usage:
  verify_sync.py --audio-dir /opt/nomad/downloads/NOMAD-audio \
                 --video-dir /opt/nomad/downloads/NOMAD-720p \
                 --out-dir   /opt/nomad/downloads/NOMAD-audio-synced \
                 [--intro 5] [--tail 5] [--emit] [--only NOMAD-0900]
"""
from __future__ import annotations

import argparse
import csv
import glob
import os
import re
import subprocess
import sys
from dataclasses import dataclass, asdict

import numpy as np

ANALYSIS_SR = 8000          # mono resample rate for correlation (ms-accurate, fast)
DEFAULT_INTRO = 5.0
DEFAULT_TAIL = 5.0
SILENCE_DB = -45.0          # first-intro-seconds considered "silent" below this RMS dBFS
OFFSET_TOL = 0.35           # s: measured offset vs. expected intro agreement
DURATION_TOL = 0.75         # s: |video_dur - (orig_dur + intro + tail)|
PEAK_MIN = 0.30             # normalised correlation peak floor for "sharp"
_BRAND_RE = re.compile(r"(NOMAD-\d+)", re.IGNORECASE)


# ---------------------------------------------------------------------------
# Pure signal math (unit-tested; no I/O)
# ---------------------------------------------------------------------------

def rms_db(samples: np.ndarray) -> float:
    """RMS level of a float signal in dBFS (0 dB = full scale 1.0)."""
    if samples.size == 0:
        return -np.inf
    rms = float(np.sqrt(np.mean(np.square(samples.astype(np.float64)))))
    if rms <= 1e-9:
        return -np.inf
    return 20.0 * np.log10(rms)


def best_lag(reference: np.ndarray, signal: np.ndarray, max_lag: int) -> tuple[int, float]:
    """Return (lag, normalised_peak) maximising cross-correlation of `signal`
    against `reference`, searching lags in [0, max_lag]. A positive lag means
    `signal` starts `lag` samples *after* `reference` (i.e. reference is padded /
    delayed relative to signal — the title-card lead-in).

    normalised_peak is in [0, 1]: the peak correlation divided by the geometric
    mean of the two energies over the overlap, so it reflects waveform similarity
    independent of amplitude.
    """
    ref = reference.astype(np.float64)
    sig = signal.astype(np.float64)
    ref -= ref.mean()
    sig -= sig.mean()
    n = min(ref.size, sig.size)
    if n == 0:
        return 0, 0.0
    max_lag = int(max(0, min(max_lag, n - 1)))
    best_l, best_score = 0, -1.0
    sig_energy_full = float(np.dot(sig, sig)) or 1e-12
    for lag in range(0, max_lag + 1):
        m = min(ref.size - lag, sig.size)
        if m <= ANALYSIS_SR // 4:            # need >~0.25s overlap to be meaningful
            break
        r = ref[lag:lag + m]
        s = sig[:m]
        num = float(np.dot(r, s))
        den = np.sqrt((float(np.dot(r, r)) or 1e-12) * (float(np.dot(s, s)) or 1e-12))
        score = num / den
        if score > best_score:
            best_score, best_l = score, lag
    return best_l, max(0.0, best_score)


# ---------------------------------------------------------------------------
# ffmpeg / ffprobe I/O
# ---------------------------------------------------------------------------

def probe_duration(path: str) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=nokey=1:noprint_wrappers=1", path],
        capture_output=True, text=True, check=True).stdout.strip()
    try:
        return float(out)
    except ValueError:
        return 0.0


def decode_mono(path: str, sr: int = ANALYSIS_SR, start: float = 0.0,
                dur: float | None = None) -> np.ndarray:
    """Decode a media file's audio to a mono float32 numpy array at `sr`."""
    cmd = ["ffmpeg", "-v", "error"]
    if start:
        cmd += ["-ss", f"{start:.3f}"]
    cmd += ["-i", path]
    if dur is not None:
        cmd += ["-t", f"{dur:.3f}"]
    cmd += ["-ac", "1", "-ar", str(sr), "-f", "f32le", "-"]
    raw = subprocess.run(cmd, capture_output=True, check=True).stdout
    return np.frombuffer(raw, dtype="<f4").copy()


# ---------------------------------------------------------------------------
# Verification
# ---------------------------------------------------------------------------

@dataclass
class SyncResult:
    brand_code: str
    video: str
    audio: str
    offset_s: float
    peak: float
    intro_silent: bool
    intro_db: float
    video_dur: float
    audio_dur: float
    expected_video_dur: float
    verdict: str
    reasons: str


def verify_pair(video: str, audio: str, intro: float = DEFAULT_INTRO,
                tail: float = DEFAULT_TAIL, search_s: float = 20.0) -> SyncResult:
    brand_m = _BRAND_RE.search(os.path.basename(video)) or _BRAND_RE.search(os.path.basename(audio))
    brand = brand_m.group(1).upper() if brand_m else "?"

    v_dur = probe_duration(video)
    a_dur = probe_duration(audio)

    # Correlate the original against the first (intro + song head) of the video.
    head = decode_mono(video, dur=min(v_dur, search_s + 60))
    orig_head = decode_mono(audio, dur=min(a_dur, 60))
    lag, peak = best_lag(head, orig_head, max_lag=int(search_s * ANALYSIS_SR))
    offset = lag / ANALYSIS_SR

    intro_samples = decode_mono(video, dur=intro)
    intro_db = rms_db(intro_samples)
    intro_silent = intro_db < SILENCE_DB

    expected = a_dur + intro + tail

    reasons = []
    if peak < PEAK_MIN:
        reasons.append(f"weak correlation peak {peak:.2f}<{PEAK_MIN}")
    if abs(offset - intro) > OFFSET_TOL:
        reasons.append(f"offset {offset:.2f}s != intro {intro:.0f}s")
    if not intro_silent:
        reasons.append(f"intro not silent ({intro_db:.0f}dB)")
    if abs(v_dur - expected) > DURATION_TOL:
        reasons.append(f"duration {v_dur:.2f}s != expected {expected:.2f}s")

    verdict = "confirmed" if not reasons else "needs-review"
    return SyncResult(brand, os.path.basename(video), os.path.basename(audio),
                      round(offset, 3), round(peak, 3), intro_silent,
                      round(intro_db, 1), round(v_dur, 3), round(a_dur, 3),
                      round(expected, 3), verdict, "; ".join(reasons))


def emit_padded(audio: str, out_path: str, offset_s: float, target_dur: float) -> None:
    """Write `silence[offset] + audio`, trimmed/padded to target_dur, so it lines
    up sample-accurately with the video timeline."""
    delay_ms = int(round(offset_s * 1000))
    af = (f"adelay={delay_ms}:all=1,apad,atrim=0:{target_dur:.3f},"
          "asetpts=N/SR/TB")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", audio, "-af", af,
                    "-c:a", "flac", out_path], check=True)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _index_by_brand(paths):
    idx = {}
    for p in paths:
        m = _BRAND_RE.search(os.path.basename(p))
        if m:
            idx.setdefault(m.group(1).upper(), p)
    return idx


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--audio-dir", required=True)
    ap.add_argument("--video-dir", required=True)
    ap.add_argument("--out-dir", default=None, help="where padded audio is written (with --emit)")
    ap.add_argument("--intro", type=float, default=DEFAULT_INTRO)
    ap.add_argument("--tail", type=float, default=DEFAULT_TAIL)
    ap.add_argument("--emit", action="store_true", help="write padded audio for confirmed tracks")
    ap.add_argument("--only", default=None, help="restrict to one brand code, e.g. NOMAD-0900")
    ap.add_argument("--report", default="sync_report.csv")
    args = ap.parse_args(argv)

    audio_idx = _index_by_brand(glob.glob(os.path.join(args.audio_dir, "*")))
    video_idx = _index_by_brand(glob.glob(os.path.join(args.video_dir, "*")))
    brands = sorted(set(audio_idx) & set(video_idx), key=lambda b: int(_BRAND_RE.search(b).group(0).split("-")[1]))
    if args.only:
        brands = [b for b in brands if b.upper() == args.only.upper()]

    if args.emit and args.out_dir:
        os.makedirs(args.out_dir, exist_ok=True)

    results = []
    for b in brands:
        try:
            r = verify_pair(video_idx[b], audio_idx[b], args.intro, args.tail)
        except subprocess.CalledProcessError as e:
            print(f"{b}: ERROR {e}", file=sys.stderr)
            continue
        results.append(r)
        print(f"{b}: {r.verdict:12s} offset={r.offset_s:.2f}s peak={r.peak:.2f} {r.reasons}")
        if args.emit and args.out_dir and r.verdict == "confirmed":
            out = os.path.join(args.out_dir, os.path.basename(audio_idx[b]).rsplit(".", 1)[0] + ".flac")
            emit_padded(audio_idx[b], out, r.offset_s, r.video_dur)

    with open(args.report, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(asdict(results[0]).keys()) if results else ["brand_code"])
        w.writeheader()
        for r in results:
            w.writerow(asdict(r))
    confirmed = sum(1 for r in results if r.verdict == "confirmed")
    print(f"\n{confirmed}/{len(results)} confirmed. Report: {args.report}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
