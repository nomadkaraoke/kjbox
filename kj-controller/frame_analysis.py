"""Pure frame-analysis helpers playability render check.

Operates on PNG screenshots captured video renderer. No video/audio
I/O — just pixel math, fully unit-testable with synthetic PNGs.
"""
from __future__ import annotations

import os
from dataclasses import dataclass

from PIL import Image, ImageChops, ImageStat


@dataclass
class FrameStats:
    """Statistics for a single frame."""
    path: str
    exists: bool
    mean_luma: float
    spread: float
    is_blank: bool


def analyze_frame(path: str, blank_spread_threshold: float = 6.0) -> FrameStats:
    """Analyze a single PNG frame for content.

    Args:
        path: Path to PNG file.
        blank_spread_threshold: Spread value below which frame is considered blank.

    Returns:
        FrameStats with luminance, spread, and blank verdict.
    """
    if not os.path.isfile(path):
        return FrameStats(path=path, exists=False, mean_luma=0.0, spread=0.0, is_blank=True)

    try:
        with Image.open(path) as img:
            gray = img.convert("L")
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
    """True if two frames differ enough to indicate picture moving.

    Args:
        path_a: Path to first PNG.
        path_b: Path to second PNG.
        min_mean_abs_diff: Threshold for mean absolute difference in luma.

    Returns:
        True if frames differ significantly.
    """
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
    """Verdict over captured renderer frames.

    Checks whether video captured at least one non-blank frame,
    and whether frames show variation (motion).

    Args:
        frame_paths: List of PNG paths from renderer capture.

    Returns:
        Dict with keys:
            - frame_captured: any file exists
            - frame_nonblank: any non-blank content detected
            - frame_varies: detected motion across frames
            - frames: list of FrameStats for all paths
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
        "frames": stats,
    }
