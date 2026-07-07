"""Tests for the phase-2 sync verifier.

Pure-math tests always run. The end-to-end tests generate audio with ffmpeg and
skip if ffmpeg is unavailable.

Run:  python -m pytest scripts/original_vocals/test_verify_sync.py -q
"""
import os
import shutil
import subprocess
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(__file__))
import verify_sync as V  # noqa: E402

HAVE_FFMPEG = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


# --- pure math -------------------------------------------------------------

def test_rms_db_silence_and_fullscale():
    assert V.rms_db(np.zeros(1000)) == -np.inf
    sine = np.sin(np.linspace(0, 200 * np.pi, 8000)).astype(np.float32)
    # RMS of a unit sine ~ 0.707 -> ~ -3 dBFS
    assert -4.0 < V.rms_db(sine) < -2.0


def test_best_lag_recovers_known_offset():
    rng = np.random.default_rng(1)
    base = rng.standard_normal(4000).astype(np.float32)
    lag_true = 800
    reference = np.concatenate([np.zeros(lag_true, np.float32), base])  # delayed
    signal = base
    lag, peak = V.best_lag(reference, signal, max_lag=2000)
    assert abs(lag - lag_true) <= 2
    assert peak > 0.95


def test_best_lag_realistic_scale_is_fast_and_correct():
    # ~25s reference @ 8kHz with a 40k-sample embed; a naive O(max_lag*N) loop
    # would be far too slow here — the FFT path returns near-instantly.
    import time
    rng = np.random.default_rng(4)
    base = rng.standard_normal(40000).astype(np.float32)
    lag_true = 30000
    reference = np.concatenate([np.zeros(lag_true, np.float32), base,
                                np.zeros(120000, np.float32)])
    t0 = time.time()
    lag, peak = V.best_lag(reference, base, max_lag=160000)
    assert time.time() - t0 < 5.0
    assert abs(lag - lag_true) <= 2
    assert peak > 0.95


def test_best_lag_zero_offset_identical():
    rng = np.random.default_rng(2)
    x = rng.standard_normal(4000).astype(np.float32)
    lag, peak = V.best_lag(x, x, max_lag=1000)
    assert lag == 0
    assert peak > 0.99


def test_best_lag_uncorrelated_low_peak():
    rng = np.random.default_rng(3)
    a = rng.standard_normal(4000).astype(np.float32)
    b = rng.standard_normal(4000).astype(np.float32)
    _, peak = V.best_lag(a, b, max_lag=1000)
    assert peak < 0.3


# --- ffmpeg-backed end-to-end ---------------------------------------------

def _ff(args):
    subprocess.run(["ffmpeg", "-v", "error", "-y", *args], check=True)


@pytest.mark.skipif(not HAVE_FFMPEG, reason="ffmpeg/ffprobe not installed")
def test_verify_pair_recovers_5s_intro(tmp_path):
    noise = str(tmp_path / "noise.wav")       # 20s aperiodic content == the "original"
    video = str(tmp_path / "NOMAD-0001 - A - B (video).wav")  # 5s silence + noise + 5s tail
    original = str(tmp_path / "NOMAD-0001 - A - B.wav")

    # aperiodic noise so correlation has a single sharp peak
    _ff(["-f", "lavfi", "-i", "anoisesrc=color=pink:seed=7:duration=20",
         "-ar", "8000", "-ac", "1", noise])
    shutil.copyfile(noise, original)
    # video audio = 5s silence, then the noise, then 5s silence tail
    _ff(["-i", noise, "-af", "adelay=5000:all=1,apad=pad_dur=5", video])

    r = V.verify_pair(video, original, intro=5.0, tail=5.0)
    assert abs(r.offset_s - 5.0) < 0.1, r
    assert r.peak > 0.8
    assert r.intro_silent
    assert r.verdict == "confirmed", r.reasons


@pytest.mark.skipif(not HAVE_FFMPEG, reason="ffmpeg/ffprobe not installed")
def test_emit_padded_matches_video_duration(tmp_path):
    original = str(tmp_path / "orig.wav")
    out = str(tmp_path / "padded.flac")
    _ff(["-f", "lavfi", "-i", "anoisesrc=color=white:seed=1:duration=10",
         "-ar", "8000", "-ac", "1", original])
    V.emit_padded(original, out, offset_s=5.0, target_dur=20.0)
    assert abs(V.probe_duration(out) - 20.0) < 0.15


@pytest.mark.skipif(not HAVE_FFMPEG, reason="ffmpeg/ffprobe not installed")
def test_wrong_recording_flagged_needs_review(tmp_path):
    # a completely different "original" should fail correlation -> needs-review
    video = str(tmp_path / "NOMAD-0002 - A - B (video).wav")
    wrong = str(tmp_path / "NOMAD-0002 - A - B.wav")
    _ff(["-f", "lavfi", "-i", "anoisesrc=color=pink:seed=11:duration=20",
         "-ar", "8000", "-ac", "1", str(tmp_path / "content.wav")])
    _ff(["-i", str(tmp_path / "content.wav"), "-af", "adelay=5000:all=1,apad=pad_dur=5", video])
    _ff(["-f", "lavfi", "-i", "anoisesrc=color=white:seed=99:duration=20",
         "-ar", "8000", "-ac", "1", wrong])
    r = V.verify_pair(video, wrong, intro=5.0, tail=5.0)
    assert r.verdict == "needs-review"
