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
DEFAULT_INTRO = 5.0         # nominal title-card length; used only for the silence probe window
SILENCE_DB = -60.0          # intro RMS below this dBFS counts as silent (real intros ~ -91 dB)
PEAK_MIN = 0.30             # normalised correlation peak floor for a trustworthy offset
OFFSET_SANE = (2.0, 15.0)   # s: plausible title-card lead-in range
SILENCE_LATE_TOL = 1.0      # s: correlation offset shouldn't land >this beyond the audio onset
#
# Sync model (validated against real releases 2026-07-07): every NOMAD video is
# [silent title card ~5s] + [instrumental time-aligned to the original] + [outro].
# The intro offset is ~5s but NOT exactly uniform (quiet song intros push the
# silence boundary out), and the OUTRO length is era-dependent (early era ~0s,
# web-platform era ~5s). So we do NOT assume 5s or check total duration; we
# measure the true offset by cross-correlation and corroborate with the audio
# onset (silencedetect). Padding uses the measured correlation offset.
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

    normalised_peak is in [0, 1]: the correlation at each lag divided by the
    geometric mean of the two energies over the overlap window, so it reflects
    waveform similarity independent of amplitude.

    Implementation: the correlation numerators for all lags come from one FFT
    (O(N log N)); per-lag overlap energies come from prefix sums (O(1) each). This
    keeps the full-catalog run tractable — a naive per-lag dot-product loop is
    O(max_lag * N) and would take ~minutes per track over a 20 s search window.
    """
    ref = reference.astype(np.float64)
    sig = signal.astype(np.float64)
    n_ref, n_sig = ref.size, sig.size
    if n_ref == 0 or n_sig == 0:
        return 0, 0.0
    ref -= ref.mean()
    sig -= sig.mean()
    max_lag = int(max(0, min(max_lag, n_ref - 1)))
    min_overlap = ANALYSIS_SR // 4           # need >~0.25s overlap to be meaningful

    # corr[lag] = sum_t ref[t+lag] * sig[t], for lag in [0, n_ref-1], via FFT.
    n_fft = 1 << (int(n_ref + n_sig - 1)).bit_length()
    corr = np.fft.irfft(np.fft.rfft(ref, n_fft) * np.conj(np.fft.rfft(sig, n_fft)), n_fft)

    # prefix sums of squared energy for O(1) per-lag overlap normalisation
    ref_e = np.concatenate(([0.0], np.cumsum(ref * ref)))
    sig_e = np.concatenate(([0.0], np.cumsum(sig * sig)))

    best_l, best_score = 0, -1.0
    for lag in range(0, max_lag + 1):
        m = min(n_ref - lag, n_sig)
        if m <= min_overlap:
            break
        e_ref = ref_e[lag + m] - ref_e[lag]
        e_sig = sig_e[m]
        den = np.sqrt((e_ref or 1e-12) * (e_sig or 1e-12))
        score = corr[lag] / den
        if score > best_score:
            best_score, best_l = score, lag
    return best_l, max(0.0, float(best_score))


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
    offset_s: float          # measured correlation lag = the pad to apply
    peak: float              # normalised correlation peak (offset confidence)
    onset_s: float           # audio onset from silencedetect (corroboration; -1 = none)
    intro_silent: bool
    intro_db: float
    video_dur: float
    audio_dur: float
    verdict: str
    reasons: str


def audio_onset(video: str, search_s: float = 20.0) -> float:
    """Seconds of leading silence in the video (where the instrumental starts),
    via silencedetect. Corroborates the correlation offset. Returns -1 if no
    leading silence region is found. NB: silencedetect logs at INFO level, so we
    must NOT pass -v error."""
    out = subprocess.run(
        ["ffmpeg", "-nostats", "-i", video, "-t", f"{search_s:.0f}",
         "-af", f"silencedetect=noise={SILENCE_DB:.0f}dB:d=0.5", "-f", "null", "-"],
        capture_output=True, text=True).stderr
    starts = re.findall(r"silence_start:\s*([-\d.]+)", out)
    ends = re.findall(r"silence_end:\s*([-\d.]+)", out)
    if ends and starts and float(starts[0]) < 0.3:
        return float(ends[0])
    return -1.0


def verify_pair(video: str, audio: str, intro: float = DEFAULT_INTRO,
                search_s: float = 20.0) -> SyncResult:
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
    onset = audio_onset(video, search_s)

    reasons = []
    if peak < PEAK_MIN:
        reasons.append(f"weak correlation peak {peak:.2f}<{PEAK_MIN}")
    if not intro_silent:
        reasons.append(f"intro not silent ({intro_db:.0f}dB)")
    if not (OFFSET_SANE[0] <= offset <= OFFSET_SANE[1]):
        reasons.append(f"offset {offset:.2f}s outside {OFFSET_SANE[0]}-{OFFSET_SANE[1]}s")
    # Corroboration: the correlation offset should not land meaningfully AFTER the
    # audio onset (a quiet song intro can push onset later than offset — that's
    # fine; the reverse means the two methods disagree).
    if onset >= 0 and offset > onset + SILENCE_LATE_TOL:
        reasons.append(f"offset {offset:.2f}s later than audio onset {onset:.2f}s")

    verdict = "confirmed" if not reasons else "needs-review"
    return SyncResult(brand, os.path.basename(video), os.path.basename(audio),
                      round(offset, 3), round(peak, 3), round(onset, 3),
                      intro_silent, round(intro_db, 1), round(v_dur, 3),
                      round(a_dur, 3), verdict, "; ".join(reasons))


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
    ap.add_argument("--intro", type=float, default=DEFAULT_INTRO,
                    help="title-card length for the silence probe window (offset is measured, not assumed)")
    ap.add_argument("--emit", action="store_true", help="write padded audio for confirmed tracks")
    ap.add_argument("--only", default=None, help="restrict to one brand code, e.g. NOMAD-0900")
    ap.add_argument("--report", default="sync_report.csv")
    args = ap.parse_args(argv)

    if args.emit and not args.out_dir:
        ap.error("--emit requires --out-dir")

    audio_idx = _index_by_brand(glob.glob(os.path.join(args.audio_dir, "*")))
    video_idx = _index_by_brand(glob.glob(os.path.join(args.video_dir, "*")))
    brands = sorted(set(audio_idx) & set(video_idx), key=lambda b: int(_BRAND_RE.search(b).group(0).split("-")[1]))
    if args.only:
        brands = [b for b in brands if b.upper() == args.only.upper()]

    if args.emit and args.out_dir:
        os.makedirs(args.out_dir, exist_ok=True)

    results = []
    errored = False
    for b in brands:
        try:
            r = verify_pair(video_idx[b], audio_idx[b], args.intro)
        except (subprocess.CalledProcessError, OSError, ValueError) as e:
            errored = True
            print(f"{b}: ERROR {e}", file=sys.stderr)
            results.append(SyncResult(b, os.path.basename(video_idx[b]),
                                      os.path.basename(audio_idx[b]), 0.0, 0.0, -1.0,
                                      False, 0.0, 0.0, 0.0, "error", str(e)))
            continue
        results.append(r)
        print(f"{b}: {r.verdict:12s} offset={r.offset_s:.2f}s peak={r.peak:.2f} {r.reasons}")
        if args.emit and args.out_dir and r.verdict == "confirmed":
            try:
                out = os.path.join(args.out_dir, os.path.basename(audio_idx[b]).rsplit(".", 1)[0] + ".flac")
                emit_padded(audio_idx[b], out, r.offset_s, r.video_dur)
            except (subprocess.CalledProcessError, OSError) as e:
                errored = True
                print(f"{b}: EMIT ERROR {e}", file=sys.stderr)

    with open(args.report, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(asdict(results[0]).keys()) if results else ["brand_code"])
        w.writeheader()
        for r in results:
            w.writerow(asdict(r))
    confirmed = sum(1 for r in results if r.verdict == "confirmed")
    print(f"\n{confirmed}/{len(results)} confirmed. Report: {args.report}")
    return 1 if errored else 0


if __name__ == "__main__":
    sys.exit(main())
