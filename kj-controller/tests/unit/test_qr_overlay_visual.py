"""Unit tests for QR overlay visual helpers (color blending, rounded mask).

These cover the math + branching; actual pygame rendering is verified by
manual smoke test on the device (see plan, Task 12).
"""

import sys
import os

# Engine code lives in desktop/ — import from there
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', '..', 'desktop'))

import pytest


class TestPremultiplyBg:
    def test_fully_opaque_returns_original_color(self):
        from overlay_types import _make_bg_color
        assert _make_bg_color('#102030', 1.0) == (16, 32, 48)

    def test_zero_opacity_returns_black(self):
        from overlay_types import _make_bg_color
        assert _make_bg_color('#FFFFFF', 0.0) == (0, 0, 0)

    def test_half_opacity_halves_components(self):
        from overlay_types import _make_bg_color
        # bg_opacity=0.5 on white → (127, 127, 127)
        r, g, b = _make_bg_color('#FFFFFF', 0.5)
        assert (r, g, b) == (127, 127, 127)


class TestBuildRoundedMask:
    def test_corner_radius_zero_returns_none(self):
        from overlay_types import _build_rounded_mask
        assert _build_rounded_mask(width=100, height=80, radius=0) is None

    def test_corner_radius_positive_returns_mask_with_correct_size(self):
        from overlay_types import _build_rounded_mask
        mask = _build_rounded_mask(width=200, height=150, radius=12)
        assert mask is not None
        assert mask.size == (200, 150)
        assert mask.mode == 'L'  # 8-bit alpha mask

    def test_corner_radius_clamped_to_half_min_dimension(self):
        """A radius larger than min(width,height)/2 must be clamped, not crash."""
        from overlay_types import _build_rounded_mask
        mask = _build_rounded_mask(width=40, height=20, radius=999)
        assert mask is not None
        assert mask.size == (40, 20)
