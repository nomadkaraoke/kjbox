"""Unit tests for wait-time estimate calculation."""

import pytest

from wait_estimate import compute_estimate


DEFAULT_CFG = {
    "sing_estimate_transition_s": 30,
    "sing_estimate_default_song_s": 240,
    "sing_estimate_min_spread_s": 120,
}


def _entry(id, status="Waiting", duration=None):
    return {"id": id, "status": status, "duration": duration}


class TestComputeEstimate:
    def test_target_not_in_list(self):
        result = compute_estimate([_entry(1), _entry(2)], 99, DEFAULT_CFG)
        assert result["position"] is None

    def test_target_at_position_1(self):
        entries = [_entry(1), _entry(2)]
        result = compute_estimate(entries, 1, DEFAULT_CFG)
        assert result["position"] == 1
        assert result["expected_s"] == 0  # nothing ahead
        assert result["close_to_front"] is True
        assert result["now_singing"] is False

    def test_now_singing_flag(self):
        entries = [_entry(1, status="Now Singing")]
        result = compute_estimate(entries, 1, DEFAULT_CFG)
        assert result["now_singing"] is True

    def test_fallback_baseline_when_no_sung_entries(self):
        # position 3 with no done entries → use fallback baseline for all ahead
        entries = [_entry(1), _entry(2), _entry(3)]
        result = compute_estimate(entries, 3, DEFAULT_CFG)
        # 2 ahead at baseline 240s + 2 transitions of 30s
        assert result["expected_s"] == 240 * 2 + 30 * 2
        assert result["spread_source"] == "fallback"

    def test_tonight_baseline_used_with_3_done(self):
        entries = [
            _entry(10, status="done", duration=180),
            _entry(11, status="done", duration=240),
            _entry(12, status="done", duration=300),  # mean=240, pstdev=48.99
            _entry(1),  # target ahead position 1 (active), 0 songs ahead
            _entry(2),  # target
        ]
        result = compute_estimate(entries, 2, DEFAULT_CFG)
        assert result["position"] == 2
        assert result["spread_source"] == "tonight"
        # 1 ahead uses mean 240s + 1 transition 30s
        assert result["expected_s"] == 240 + 30

    def test_linked_file_duration_preferred_over_baseline(self):
        entries = [
            _entry(10, status="done", duration=180),
            _entry(11, status="done", duration=240),
            _entry(12, status="done", duration=300),
            _entry(1, duration=420),  # custom duration ahead
            _entry(2),
        ]
        result = compute_estimate(entries, 2, DEFAULT_CFG)
        # 1 ahead uses its own 420s + transition 30s
        assert result["expected_s"] == 420 + 30

    def test_negative_and_zero_durations_treated_as_missing(self):
        entries = [
            _entry(1, duration=-1),
            _entry(2, duration=0),
            _entry(3),  # target
        ]
        result = compute_estimate(entries, 3, DEFAULT_CFG)
        # Both ahead fall back to baseline
        assert result["expected_s"] == 240 * 2 + 30 * 2

    def test_done_and_left_entries_excluded_from_ahead(self):
        entries = [
            _entry(1, status="Done", duration=100),
            _entry(2, status="Left"),
            _entry(3),  # target — nothing actually ahead
        ]
        result = compute_estimate(entries, 3, DEFAULT_CFG)
        assert result["position"] == 1
        assert result["expected_s"] == 0

    def test_spread_clamped_to_min(self):
        # Only 3 done entries with near-identical durations → tiny stdev
        entries = [
            _entry(10, status="done", duration=200),
            _entry(11, status="done", duration=200),
            _entry(12, status="done", duration=200),
            _entry(1),
            _entry(2),
        ]
        result = compute_estimate(entries, 2, DEFAULT_CFG)
        spread = result["range_high_s"] - result["expected_s"]
        assert spread == DEFAULT_CFG["sing_estimate_min_spread_s"]

    def test_range_never_below_zero(self):
        # position 1, expected_s = 0; range_low should be clamped to 0
        result = compute_estimate([_entry(1)], 1, DEFAULT_CFG)
        assert result["range_low_s"] == 0

    def test_close_to_front_flag(self):
        entries = [_entry(1), _entry(2), _entry(3), _entry(4)]
        # position 2 → close
        assert compute_estimate(entries, 2, DEFAULT_CFG)["close_to_front"] is True
        # position 3 → not close
        assert compute_estimate(entries, 3, DEFAULT_CFG)["close_to_front"] is False
