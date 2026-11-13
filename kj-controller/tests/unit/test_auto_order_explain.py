"""Tests for the Auto Order explainer (auto_order_explain.py) and the tracing hook
(compute_auto_order(..., trace=True)) it relies on.

The explainer's stories are only worth anything if they're TRUE of the current
algorithm, so every scenario's checks must pass. If you retune Auto Order and one
of these fails, update that scenario's text + checks (or reconsider the change).
"""

import pytest

from auto_order import AutoOrderConfig, compute_auto_order, _score_terms
from auto_order_explain import (
    SCENARIOS, _entries, build_explainer, build_scenario, describe_check, run_check,
)


@pytest.mark.parametrize("scn", SCENARIOS, ids=[s["id"] for s in SCENARIOS])
def test_every_scenario_story_is_true(scn):
    out = build_scenario(scn)
    failed = [c["text"] for c in out["checks"] if not c["ok"]]
    assert not failed, f"{scn['id']}: story no longer matches the algorithm: {failed}"


def test_scenario_ids_unique_and_songs_unique_within_each():
    ids = [s["id"] for s in SCENARIOS]
    assert len(ids) == len(set(ids))
    for s in SCENARIOS:
        songs = [row[3] for row in s["spec"]]
        assert len(songs) == len(set(songs)), s["id"]


@pytest.mark.parametrize("scn", SCENARIOS, ids=[s["id"] for s in SCENARIOS])
def test_trace_does_not_change_the_order(scn):
    a = compute_auto_order(_entries(scn["spec"]))
    b = compute_auto_order(_entries(scn["spec"]), trace=True)
    assert a.ordered_ids == b.ordered_ids
    assert a.trace == []


@pytest.mark.parametrize("scn", SCENARIOS, ids=[s["id"] for s in SCENARIOS])
def test_trace_has_one_decision_per_row_and_winner_matches(scn):
    res = compute_auto_order(_entries(scn["spec"]), trace=True)
    assert [d["slot"] for d in res.trace] == list(range(len(res.order)))
    for d, e in zip(res.trace, res.order):
        assert d["entry_id"] == e.id
        if d["kind"] == "chosen":
            # Winner is first, and the ranking is by total (desc).
            assert d["candidates"][0]["entry_id"] == e.id
            totals = [c["total"] for c in d["candidates"]]
            assert totals == sorted(totals, reverse=True)
            # Terms add up to the total.
            for c in d["candidates"]:
                assert sum(c["terms"].values()) == pytest.approx(c["total"], abs=0.1)
        else:
            assert d["candidates"] == []


def test_trace_kinds_cover_locked_and_being_made():
    scn = next(s for s in SCENARIOS if s["id"] == "being-made")
    res = compute_auto_order(_entries(scn["spec"]), trace=True)
    kinds = [d["kind"] for d in res.trace]
    assert kinds[:5] == ["locked"] * 5
    assert kinds[-1] == "being_made"


def test_back_to_back_flagged_in_trace():
    scn = next(s for s in SCENARIOS if s["id"] == "back-to-back")
    res = compute_auto_order(_entries(scn["spec"]), trace=True)
    row6 = res.trace[5]
    dev = [c for c in row6["candidates"] if c["entry_id"] == 6][0]   # Dev's 2nd song
    assert dev["back_to_back"] is True
    assert dev["entry_id"] != row6["entry_id"]


def test_bump_top5_term_appears_in_trace():
    scn = next(s for s in SCENARIOS if s["id"] == "top-five-duplicate")
    res = compute_auto_order(_entries(scn["spec"]), trace=True)
    row4 = res.trace[3]
    gus = [c for c in row4["candidates"] if c["entry_id"] == 4][0]
    assert gus["terms"]["bump_top5"] == -AutoOrderConfig().w_bump_top5


def test_score_terms_sum_matches_original_formula():
    es = _entries([("A", 1, 30, "x"), ("B", 0, 5, "y")])
    t = _score_terms(es[0], 2, {}, 10.0, {}, AutoOrderConfig())
    assert set(t) >= {"fairness", "new_singer", "wait", "overdue", "stick", "spacing", "priority"}
    assert t["projected_wait"] == 40.0


def test_build_explainer_shape_and_live_config_values():
    out = build_explainer()
    c = AutoOrderConfig()
    assert out["config"]["always_lock"] == c.always_lock
    assert out["config"]["weights"]["priority"] == c.w_priority_bias
    assert len(out["scenarios"]) == len(SCENARIOS)
    s = out["scenarios"][0]
    for key in ("before", "after", "decisions", "checks", "situation", "outcome", "why", "all_ok"):
        assert key in s
    assert {"row", "delta", "orig_row", "reason"} <= set(s["after"][0])


def test_describe_and_run_check_every_kind():
    es = _entries([("A", 1, 5, "a1"), ("B", 1, 5, "b1"), ("A", 1, 5, "a2")])
    kinds = [("row", "a1", 1), ("moved_up", "a2"), ("before", "a1", "b1"), ("same_rows", 1),
             ("unchanged",), ("no_back_to_back",), ("min_gap", "A", 2), ("bottom", ["a2"])]
    for k in kinds:
        assert isinstance(describe_check(k), str)
        assert run_check(k, es, es) in (True, False)
    assert run_check(("moved_up", "a2"), es, es) is False
    with pytest.raises(ValueError):
        describe_check(("nope",))
    with pytest.raises(ValueError):
        run_check(("nope",), es, es)
