"""Wait-time estimate for singer-facing status page.

Computes the expected wait for a target rotation entry, producing both a
point estimate and a range derived from tonight's actual sung-entry
variance (falling back to a configurable minimum spread when we have
too little data).

Called from `sing.py` on each `/sing/status/<id>` poll.
"""

from statistics import mean, pstdev


def compute_estimate(entries, target_id, cfg):
    """Return the estimate dict for `target_id` given `entries`.

    `entries` is a list of rotation entry dicts as returned by
    `RotationManager.get_rotation()`. `target_id` is the entry id whose
    wait we're estimating. `cfg` is the app config dict.

    Returned dict shape:
        {
          "position": int | None,     # 1-based active position; None if not found
          "expected_s": int,          # point estimate (sum of ahead + transitions)
          "range_low_s": int,         # clamped to 0
          "range_high_s": int,
          "spread_source": "tonight" | "fallback",
          "close_to_front": bool,     # position is not None and <= 2
          "now_singing": bool,        # target's status == "Now Singing"
        }
    """
    baseline, stdev_s, spread_source = _baseline(entries, cfg)

    active = [e for e in entries if e.get("status", "").lower() not in ("done", "left")]

    position = None
    target_status = None
    ahead_durations = []
    for i, e in enumerate(active):
        if e["id"] == target_id:
            position = i + 1
            target_status = e.get("status", "")
            break
        ahead_durations.append(_sanitise(e.get("duration"), baseline))

    buffer_s = cfg["sing_estimate_transition_s"] * len(ahead_durations)
    expected = int(sum(ahead_durations) + buffer_s)

    raw_spread = int(stdev_s) if stdev_s is not None else 180
    spread = max(cfg["sing_estimate_min_spread_s"], raw_spread)

    return {
        "position": position,
        "expected_s": expected,
        "range_low_s": max(0, expected - spread),
        "range_high_s": expected + spread,
        "spread_source": spread_source,
        "close_to_front": position is not None and position <= 2,
        "now_singing": (target_status or "").lower() == "now singing",
    }


def _baseline(entries, cfg):
    """Return (baseline_seconds, stdev_or_None, spread_source)."""
    done_durations = [
        e["duration"]
        for e in entries
        if e.get("status", "").lower() == "done"
        and e.get("duration")
        and e["duration"] > 0
    ]
    if len(done_durations) >= 3:
        return mean(done_durations), pstdev(done_durations), "tonight"
    return cfg["sing_estimate_default_song_s"], None, "fallback"


def _sanitise(duration, baseline):
    """Treat missing, zero, or negative durations as the baseline value."""
    if duration is None or duration <= 0:
        return baseline
    return duration
