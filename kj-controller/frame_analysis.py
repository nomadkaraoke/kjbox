"""Pure frame-analysis helpers for the playability render check.

Operates on PNG screenshots captured from a video renderer. No video/audio
I/O — just pixel math, so it is fully unit-testable with synthetic PNGs.
"""
from __future__ import annotations

import os
from dataclasses import dataclass

from PIL import Image, ImageChops, ImageStat

# A frame whose luma standard deviation is below this is treated as "no real
# picture" (pure black, solid colour, or a flat title card with no detail).
# Deliberately lenient; calibrated against the real library before gating.
BLANK_SPREAD_THRESHOLD = 6.0


@dataclass
class FrameStats:
    path: str
    exists: bool
    mean_luma: float
    spread: float
    is_blank: bool


def analyze_frame(path: str, blank_spread_threshold: float = BLANK_SPREAD_THRESHOLD) -> FrameStats:
    if not path or not os.path.isfile(path) or os.path.getsize(path) == 0:
        return FrameStats(path=path, exists=False, mean_luma=0.0, spread=0.0, is_blank=True)
    try:
        with Image.open(path) as im:
            gray = im.convert("L")
            stat = ImageStat.Stat(gray)
            mean_luma = float(stat.mean[0])
            spread = float(stat.stddev[0])
    except OSError:
        return FrameStats(path=path, exists=False, mean_luma=0.0, spread=0.0, is_blank=True)
    return FrameStats(
        path=path, exists=True, mean_luma=mean_luma, spread=spread,
        is_blank=spread < blank_spread_threshold,
    )


def frames_differ(path_a: str, path_b: str, min_mean_abs_diff: float = 2.0) -> bool:
    """True if two frames differ enough to indicate the picture is moving."""
    if not (os.path.isfile(path_a) and os.path.isfile(path_b)):
        return False
    try:
        with Image.open(path_a) as a, Image.open(path_b) as b:
            ga, gb = a.convert("L"), b.convert("L")
            if ga.size != gb.size:
                gb = gb.resize(ga.size)
            mad = float(ImageStat.Stat(ImageChops.difference(ga, gb)).mean[0])
    except OSError:
        return False
    return mad >= min_mean_abs_diff


def judge_renderer_frames(frame_paths: list[str]) -> dict:
    """Verdict over the frames captured from one renderer.

    Produces video if at least one captured frame is a real (non-blank)
    image. Records whether frames vary (motion) as a secondary signal.
    """
    stats = [analyze_frame(p) for p in frame_paths]
    existing = [s.path for s in stats if s.exists]
    varies = False
    for i in range(len(existing)):
        for j in range(i + 1, len(existing)):
            if frames_differ(existing[i], existing[j]):
                varies = True
                break
        if varies:
            break
    return {
        "frame_captured": any(s.exists for s in stats),
        "frame_nonblank": any(s.exists and not s.is_blank for s in stats),
        "frame_varies": varies,
        "frames": [s.__dict__ for s in stats],
    }
