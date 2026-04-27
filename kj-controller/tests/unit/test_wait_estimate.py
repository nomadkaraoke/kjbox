"""Unit tests for wait-time estimate calculation."""

import pytest

from wait_estimate import compute_all_estimates, compute_estimate


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


class TestComputeAllEstimates:
    def test_empty_entries(self):
        estimates, spread_source = compute_all_estimates([], DEFAULT_CFG)
        assert estimates == []
        assert spread_source == "fallback"

    def test_done_and_left_excluded(self):
        entries = [
            _entry(1, status="Done", duration=200),
            _entry(2, status="Left"),
            _entry(3),
            _entry(4),
        ]
        estimates, _ = compute_all_estimates(entries, DEFAULT_CFG)
        assert [e["position"] for e in estimates] == [1, 2]

    def test_cumulative_sum_with_known_durations(self):
        entries = [
            _entry(1, duration=180),
            _entry(2, duration=240),
            _entry(3, duration=200),
        ]
        estimates, _ = compute_all_estimates(entries, DEFAULT_CFG)
        # transition_s=30
        assert estimates[0]["expected_s"] == 0
        assert estimates[1]["expected_s"] == 180 + 30
        assert estimates[2]["expected_s"] == 180 + 240 + 30 + 30

    def test_parity_with_compute_estimate(self):
        # For every active entry, compute_all_estimates[i] must produce the
        # same expected_s / range_low_s / range_high_s as
        # compute_estimate(entries, target_id, cfg).
        entries = [
            _entry(10, status="done", duration=180),
            _entry(11, status="done", duration=240),
            _entry(12, status="done", duration=300),
            _entry(1, duration=210),
            _entry(2, duration=180),
            _entry(3),
            _entry(4, duration=300),
        ]
        all_ests, _ = compute_all_estimates(entries, DEFAULT_CFG)
        active_ids = [1, 2, 3, 4]
        for i, target_id in enumerate(active_ids):
            single = compute_estimate(entries, target_id, DEFAULT_CFG)
            assert all_ests[i]["expected_s"] == single["expected_s"]
            assert all_ests[i]["range_low_s"] == single["range_low_s"]
            assert all_ests[i]["range_high_s"] == single["range_high_s"]
            assert all_ests[i]["position"] == single["position"]

    def test_spread_source_tonight_with_three_done(self):
        entries = [
            _entry(10, status="done", duration=180),
            _entry(11, status="done", duration=240),
            _entry(12, status="done", duration=300),
            _entry(1),
        ]
        _, spread_source = compute_all_estimates(entries, DEFAULT_CFG)
        assert spread_source == "tonight"

    def test_spread_source_fallback_with_two_done(self):
        entries = [
            _entry(10, status="done", duration=180),
            _entry(11, status="done", duration=240),
            _entry(1),
        ]
        _, spread_source = compute_all_estimates(entries, DEFAULT_CFG)
        assert spread_source == "fallback"

    def test_now_singing_flag_case_insensitive(self):
        entries = [
            _entry(1, status="now singing"),
            _entry(2, status="Now Singing"),
            _entry(3),
        ]
        estimates, _ = compute_all_estimates(entries, DEFAULT_CFG)
        assert estimates[0]["now_singing"] is True
        assert estimates[1]["now_singing"] is True
        assert estimates[2]["now_singing"] is False

    def test_close_to_front_flags(self):
        entries = [_entry(1), _entry(2), _entry(3), _entry(4)]
        estimates, _ = compute_all_estimates(entries, DEFAULT_CFG)
        assert estimates[0]["close_to_front"] is True
        assert estimates[1]["close_to_front"] is True
        assert estimates[2]["close_to_front"] is False
        assert estimates[3]["close_to_front"] is False

    def test_range_low_clamped_to_zero(self):
        # Position 1 has expected_s = 0; range_low_s must be max(0, ...).
        entries = [_entry(1)]
        estimates, _ = compute_all_estimates(entries, DEFAULT_CFG)
        assert estimates[0]["range_low_s"] == 0

    def test_negative_or_zero_durations_use_baseline(self):
        # Entry 1 has duration=0 → ahead total uses baseline (240) for entry 2.
        entries = [_entry(1, duration=0), _entry(2)]
        estimates, _ = compute_all_estimates(entries, DEFAULT_CFG)
        assert estimates[1]["expected_s"] == 240 + 30
