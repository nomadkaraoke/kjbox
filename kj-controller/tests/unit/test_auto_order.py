"""Unit tests for the Auto Order rotation algorithm (auto_order.py).

These lock the HARD constraints (never violated) and check that the SOFT goals
move in the right direction. The scored greedy weave is heuristic, so soft goals
are asserted as improvements / bounded outcomes rather than exact orderings.
"""

from auto_order import (
    AutoOrderConfig, EntryView, build_entry_views, compute_auto_order,
    compute_metrics, _projected_wait, _slot_minutes,
)


def _mk(spec):
    """spec: list of (singer, sung, wait, song[, duration_seconds]). seq auto per owner."""
    per_owner = {}
    out = []
    for idx, row in enumerate(spec):
        singer, sung, wait, song = row[:4]
        dur = row[4] if len(row) > 4 else None
        owner = singer.strip().lower()
        seq = per_owner.get(owner, 0)
        per_owner[owner] = seq + 1
        members = tuple(p.strip().lower() for p in owner.split("&") if p.strip()) or (owner,)
        out.append(EntryView(id=idx + 1, owner=owner, sung=sung, wait_minutes=wait,
                             seq=seq, orig_index=idx, singer=singer, song_artist=song,
                             members=members, duration=dur))
    return out


# ---------------------------------------------------------------------------
# Hard constraints
# ---------------------------------------------------------------------------

def test_empty_and_singleton():
    assert compute_auto_order([]).order == []
    one = _mk([("A", 0, 5, "s")])
    assert compute_auto_order(one).ordered_ids == [1]


def test_rows_1_to_5_are_frozen():
    before = _mk([
        ("A", 5, 5, "a"), ("B", 5, 5, "b"), ("C", 5, 5, "c"),
        ("D", 5, 5, "d"), ("E", 5, 5, "e"),
        ("New", 0, 90, "n"), ("F", 6, 5, "f"), ("G", 6, 5, "g"),
    ])
    res = compute_auto_order(before)
    # First five ids unchanged despite the new singer + long waiter below.
    assert res.ordered_ids[:5] == [1, 2, 3, 4, 5]


def test_frozen_head_respects_short_queue():
    # Fewer than lock_head entries: nothing can move.
    before = _mk([("A", 1, 5, "a"), ("B", 2, 5, "b"), ("C", 3, 5, "c")])
    res = compute_auto_order(before)
    assert res.ordered_ids == [1, 2, 3]
    assert not res.changed


def test_per_singer_submission_order_preserved():
    before = _mk([
        ("A", 3, 5, "a1"), ("B", 3, 5, "b"), ("C", 3, 5, "c"),
        ("D", 3, 5, "d"), ("E", 3, 5, "e"),
        ("Z", 2, 5, "z1"), ("Z", 2, 5, "z2"), ("Z", 2, 5, "z3"),
        ("Y", 2, 5, "y"), ("X", 2, 5, "x"),
    ])
    res = compute_auto_order(before)
    # Z's three songs stay in submission order (z1 before z2 before z3).
    z_songs = [e.song_artist for e in res.order if e.owner == "z"]
    assert z_songs == ["z1", "z2", "z3"]


def test_entry_set_is_preserved():
    before = _mk([(f"S{i}", i % 4, 5, f"s{i}") for i in range(14)])
    res = compute_auto_order(before)
    assert sorted(res.ordered_ids) == sorted(e.id for e in before)
    assert len(res.order) == len(before)


# ---------------------------------------------------------------------------
# Soft goals — asserted as direction/bounds
# ---------------------------------------------------------------------------

def test_new_singer_promoted_above_sung_singers():
    before = _mk([
        ("A", 4, 5, "a"), ("B", 5, 5, "b"), ("C", 4, 5, "c"),
        ("D", 5, 5, "d"), ("E", 4, 5, "e"),
        ("F", 5, 5, "f"), ("G", 4, 5, "g"), ("H", 5, 5, "h"),
        ("New", 0, 45, "n"),  # brand new, stuck at the back
    ])
    res = compute_auto_order(before)
    new_idx = next(i for i, e in enumerate(res.order) if e.owner == "new")
    # Promoted out of the back and into (or above) the protected band.
    assert new_idx < 8
    assert new_idx >= 5  # never breaches the frozen head


def _back_to_back_in_prefix(order, prefix_len):
    seen = {}
    count = 0
    for i, e in enumerate(order[:prefix_len]):
        for m in e.members:
            if seen.get(m) == i - 1:
                count += 1
                break
        for m in e.members:
            seen[m] = i
    return count


def test_burst_singer_spread_early_may_cluster_at_tail():
    # The KJ's stated preference: space a burst-singer well in the EARLY queue and
    # let their leftover songs bunch at the very bottom (later requests re-space them).
    before = _mk([
        ("A", 3, 5, "a"), ("B", 2, 5, "b"), ("C", 2, 5, "c"),
        ("D", 3, 5, "d"), ("E", 3, 5, "e"),
        ("Burst", 2, 5, "x1"), ("Burst", 2, 5, "x2"),
        ("Burst", 2, 5, "x3"), ("Burst", 2, 5, "x4"),
        ("P", 1, 5, "p"), ("Q", 2, 5, "q"), ("R", 2, 5, "r"),
    ])
    res = compute_auto_order(before)
    # No back-to-back in the first two thirds of the queue.
    assert _back_to_back_in_prefix(res.order, (2 * len(res.order)) // 3) == 0
    # The burst singer is not crammed into the top of the reorderable region.
    burst_positions = [i for i, e in enumerate(res.order) if e.owner == "burst"]
    assert burst_positions[1] - burst_positions[0] >= 2  # first two are spaced apart


def test_group_member_overlap_not_back_to_back():
    # "Tara" solo directly followed by "Anya & Tara" would be Tara twice in a row.
    before = _mk([
        ("A", 2, 5, "a"), ("B", 2, 5, "b"), ("C", 2, 5, "c"),
        ("D", 2, 5, "d"), ("E", 2, 5, "e"),
        ("Tara", 1, 5, "t1"),
    ])
    # Build a duet entry sharing "Tara".
    duet = EntryView(id=99, owner="anya & tara", sung=1, wait_minutes=5, seq=0,
                     orig_index=6, singer="Anya & Tara", song_artist="d",
                     members=("anya", "tara"))
    extra = _mk([("Mia", 1, 5, "m"), ("Nate", 1, 5, "n")])
    for i, e in enumerate(extra):
        e.orig_index = 7 + i
        e.id = 100 + i
    before = before + [duet] + extra
    res = compute_auto_order(before)
    order = res.order
    # Find Tara-solo and the duet; they must not be adjacent.
    idx_solo = next(i for i, e in enumerate(order) if e.owner == "tara")
    idx_duet = next(i for i, e in enumerate(order) if e.owner == "anya & tara")
    assert abs(idx_solo - idx_duet) >= 2


def test_lighter_longer_waiter_beats_heavier_recent():
    before = _mk([
        ("H1", 5, 5, "h1"), ("H2", 5, 5, "h2"), ("H3", 5, 5, "h3"),
        ("H4", 5, 5, "h4"), ("H5", 5, 5, "h5"),
        ("Heavy", 6, 20, "hv"), ("Light", 2, 150, "lt"),
    ])
    res = compute_auto_order(before)
    heavy_idx = next(i for i, e in enumerate(res.order) if e.owner == "heavy")
    light_idx = next(i for i, e in enumerate(res.order) if e.owner == "light")
    assert light_idx < heavy_idx


def test_spacing_never_regresses_on_messy_input():
    # A deliberately clustered input. Spacing is the strong guarantee — it must
    # never get worse. (Fairness may trade slightly for spacing by design.)
    before = _mk([
        ("A", 1, 5, "a"), ("B", 2, 5, "b"), ("C", 1, 5, "c"),
        ("D", 2, 5, "d"), ("E", 1, 5, "e"),
        ("Z", 3, 5, "z1"), ("Z", 3, 5, "z2"), ("Z", 3, 5, "z3"),
        ("New", 0, 30, "n"), ("Y", 4, 5, "y"), ("W", 5, 5, "w"),
    ])
    res = compute_auto_order(before)
    assert res.metrics_after["back_to_back"] <= res.metrics_before["back_to_back"]
    assert res.metrics_after["close_repeats"] <= res.metrics_before["close_repeats"]


def test_idempotent_second_pass_is_stable():
    before = _mk([
        ("A", 4, 5, "a"), ("B", 5, 5, "b"), ("C", 4, 5, "c"),
        ("D", 5, 5, "d"), ("E", 4, 5, "e"),
        ("New", 0, 45, "n"), ("F", 5, 5, "f"), ("Z", 3, 5, "z1"),
        ("Z", 3, 5, "z2"), ("G", 2, 5, "g"),
    ])
    once = compute_auto_order(before)
    # Feed the result back in (re-derive orig_index/seq from the new order).
    relabelled = build_entry_views([
        {"id": e.id, "singer": e.singer, "songs_sung": e.sung,
         "wait_minutes": e.wait_minutes, "song_artist": e.song_artist}
        for e in once.order
    ])
    twice = compute_auto_order(relabelled)
    assert [e.id for e in twice.order] == [e.id for e in once.order]


# ---------------------------------------------------------------------------
# build_entry_views + config
# ---------------------------------------------------------------------------

def test_build_entry_views_assigns_seq_per_owner():
    entries = [
        {"id": 10, "singer": "Anya", "songs_sung": 4, "wait_minutes": 70, "song_artist": "s1"},
        {"id": 11, "singer": "Bob", "songs_sung": 0, "wait_minutes": 5, "song_artist": "s2"},
        {"id": 12, "singer": "Anya", "songs_sung": 4, "wait_minutes": 70, "song_artist": "s3"},
    ]
    views = build_entry_views(entries)
    anya = [v for v in views if v.owner == "anya"]
    assert [v.seq for v in anya] == [0, 1]
    assert views[1].owner == "bob" and views[1].sung == 0


def test_config_no_lock_allows_full_reorder():
    cfg = AutoOrderConfig(lock_head=0, always_lock=0)
    before = _mk([("Heavy", 8, 5, "h"), ("New", 0, 5, "n")])
    res = compute_auto_order(before, cfg)
    # With nothing frozen, the new singer can take row 1.
    assert res.order[0].owner == "new"


def test_row4_or_5_bumped_down_when_singer_repeats_in_head():
    # Same singer at row 1 AND row 5 -> the row-5 entry may be bumped down; rows 2/3
    # are sacred and never move.
    before = _mk([
        ("Celina", 7, 5, "c1"), ("B", 1, 5, "b"), ("C", 1, 5, "c"),
        ("D", 1, 5, "d"), ("Celina", 7, 5, "c2"),
        ("E", 0, 20, "e"), ("F", 1, 5, "f"), ("G", 1, 5, "g"),
        ("H", 1, 5, "h"), ("I", 1, 5, "i"), ("J", 1, 5, "j"),
    ])
    res = compute_auto_order(before)
    order = res.order
    # Rows 1-3 unchanged.
    assert [order[i].id for i in range(3)] == [1, 2, 3]
    # Celina's second song (was row 5) has been bumped downward (heavy singer).
    c2_idx = next(i for i, e in enumerate(order) if e.song_artist == "c2")
    assert c2_idx > 4


def test_compute_metrics_counts_back_to_back():
    order = _mk([("A", 1, 5, "a1"), ("A", 1, 5, "a2"), ("B", 1, 5, "b")])
    m = compute_metrics(order)
    assert m["back_to_back"] == 1


def test_always_lock_rows_2_and_3_never_move():
    # Even the frozen-head EXCEPTION (bump a row-4/5 duplicate) must never touch
    # rows 2 or 3. Here the same singer sits at rows 1, 2, 4 — only the row-4 copy
    # may move; rows 2 and 3 are sacred.
    before = _mk([
        ("Dup", 3, 5, "d1"), ("Dup", 3, 5, "d2"), ("C", 1, 5, "c"),
        ("Dup", 3, 5, "d4"), ("E", 1, 5, "e"),
        ("F", 0, 20, "f"), ("G", 1, 5, "g"), ("H", 1, 5, "h"),
    ])
    order = compute_auto_order(before).order
    # Rows 1,2,3 unchanged (ids 1,2,3); row 2 is "Dup" and stays put.
    assert [order[i].id for i in range(3)] == [1, 2, 3]


# ---------------------------------------------------------------------------
# Projected fairness — a burst singer's later songs get LESS urgent (round 2, #7)
# ---------------------------------------------------------------------------

def test_projected_sung_defers_a_singers_later_songs():
    # A brand-new singer with THREE songs shouldn't sing all three before other
    # singers: their 1st is judged "new", 2nd as "sung once", 3rd as "twice".
    before = _mk([
        ("A", 4, 5, "a"), ("B", 4, 5, "b"), ("C", 4, 5, "c"),
        ("D", 4, 5, "d"), ("E", 4, 5, "e"),
        ("Trip", 0, 10, "t1"), ("Trip", 0, 10, "t2"), ("Trip", 0, 10, "t3"),
        ("Solo", 1, 10, "s"), ("F", 2, 10, "f"), ("G", 3, 10, "g"),
    ])
    order = compute_auto_order(before).order
    trip = [i for i, e in enumerate(order) if e.owner == "trip"]
    solo = next(i for i, e in enumerate(order) if e.owner == "solo")
    # Trip's 1st comes early (new), but Solo (1x) beats Trip's 3rd (projected 2x).
    assert trip[0] < solo
    assert solo < trip[2]


# ---------------------------------------------------------------------------
# Wait projection + reset — the round-3 #7 fix
# ---------------------------------------------------------------------------

def test_projected_wait_pure_resets_after_a_placement():
    e = EntryView(id=1, owner="x", wait_minutes=100, members=("x",))
    # Never placed: elapsed + time until this slot.
    assert _projected_wait(e, 20.0, {}, AutoOrderConfig()) == 120.0
    # Placed 30 min into the queue; by minute 50 they've waited only 20, not 120.
    assert _projected_wait(e, 50.0, {"x": 30.0}, AutoOrderConfig()) == 20.0


def test_wait_resets_so_a_repeat_singer_does_not_bury_a_new_singer():
    # The round-3 golden case (Mari & Angel). A returning singer sits in the frozen
    # head (they sing very soon), so their SECOND song's wait resets — it must not
    # keep a huge overdue boost that buries a brand-new singer.
    before = _mk([
        ("Rep", 1, 100, "r1"),   # frozen row 1 — sings immediately, wait resets
        ("B", 2, 8, "b"), ("C", 2, 8, "c"), ("D", 2, 8, "d"), ("E", 2, 8, "e"),
        ("Rep", 1, 100, "r2"), ("Rep", 1, 100, "r3"),   # Rep's later songs
        ("New", 0, 20, "n"),     # brand-new singer stuck below
        ("F", 2, 8, "f"), ("G", 2, 8, "g"),
    ])
    order = compute_auto_order(before).order
    new_idx = next(i for i, e in enumerate(order) if e.owner == "new")
    rep_later = [i for i, e in enumerate(order) if e.owner == "rep" and e.song_artist != "r1"]
    # The new singer sings before Rep's SECOND song (Rep already sang at row 1).
    assert new_idx < min(rep_later)


def test_egregiously_overdue_singer_leapfrogs_a_new_singer():
    # Goal #4 (nobody waits way over an hour) beats goal #1 (new first) when the
    # wait is egregious: a singer 2+ hours overdue gets ONE song ahead of a new one.
    before = _mk([
        ("A", 3, 5, "a"), ("B", 3, 5, "b"), ("C", 3, 5, "c"),
        ("D", 3, 5, "d"), ("E", 3, 5, "e"),
        ("New", 0, 8, "n"), ("Overdue", 2, 135, "o"), ("F", 3, 5, "f"),
    ])
    order = compute_auto_order(before).order
    new_idx = next(i for i, e in enumerate(order) if e.owner == "new")
    over_idx = next(i for i, e in enumerate(order) if e.owner == "overdue")
    assert over_idx < new_idx


def test_moderate_waiter_does_not_bury_a_new_singer():
    # The flip side: a singer who's only waited a normal amount (well under an hour)
    # must NOT jump ahead of a brand-new singer — otherwise goal #4 would swallow #1.
    before = _mk([
        ("A", 3, 5, "a"), ("B", 3, 5, "b"), ("C", 3, 5, "c"),
        ("D", 3, 5, "d"), ("E", 3, 5, "e"),
        ("Mod", 2, 35, "m"), ("New", 0, 8, "n"), ("F", 3, 5, "f"),
    ])
    order = compute_auto_order(before).order
    new_idx = next(i for i, e in enumerate(order) if e.owner == "new")
    mod_idx = next(i for i, e in enumerate(order) if e.owner == "mod")
    assert new_idx < mod_idx


# ---------------------------------------------------------------------------
# Real durations + turnaround feed the wait projection (round 2 follow-ups)
# ---------------------------------------------------------------------------

def test_slot_minutes_uses_linked_duration_plus_turnaround():
    cfg = AutoOrderConfig()  # avg 4 min, turnaround 0.5 min
    linked = EntryView(id=1, owner="x", duration=300)     # 5:00
    unlinked = EntryView(id=2, owner="y", duration=None)
    assert _slot_minutes(linked, cfg) == 5.0 + 0.5
    assert _slot_minutes(unlinked, cfg) == cfg.avg_song_minutes + 0.5


def test_longer_songs_ahead_push_a_waiter_up_sooner():
    # Same singers/waits; only the durations of the songs AHEAD differ. With long
    # songs ahead, the borderline waiter's projected wait crosses the hour sooner,
    # so they should be placed earlier.
    # A long queue of equal singers (wait 10) with one singer W (wait 20) at the back.
    # With short songs ahead, nobody's projected wait reaches an hour, so W stays near
    # the back (fairness/spacing/stickiness only). With long songs ahead, W's projection
    # crosses the hour first, the overdue boost fires, and W is pulled STRICTLY earlier.
    # If the real durations were ignored (flat fallback) both orderings would match and
    # this assertion would fail.
    def build(dur):
        spec = [(f"A{i}", 2, 10, f"a{i}", dur) for i in range(5)]      # frozen head
        spec += [(f"F{i}", 2, 10, f"f{i}", dur) for i in range(14)]    # fillers
        spec += [("W", 2, 20, "w", dur)]                               # the waiter, at the back
        return _mk(spec)
    short = compute_auto_order(build(90)).order    # 1.5-min songs
    long = compute_auto_order(build(600)).order     # 10-min songs
    w_short = next(i for i, e in enumerate(short) if e.owner == "w")
    w_long = next(i for i, e in enumerate(long) if e.owner == "w")
    assert w_long < w_short


# ---------------------------------------------------------------------------
# Bump penalty is soft — yields to the back-to-back veto (round 1, #5)
# ---------------------------------------------------------------------------

def test_bump_yields_when_it_would_force_a_back_to_back():
    # Anya sits at row 3 (frozen). Bumping a row-4/5 duplicate down would pull the
    # only remaining candidate (another Anya entry) up next to row 3 → back-to-back.
    # Avoiding the back-to-back must win: no adjacent Anya.
    before = _mk([
        ("Andrew", 0, 5, "x1"), ("Lyle", 0, 5, "y"), ("Anya", 0, 5, "a1"),
        ("Lyle", 0, 5, "y2"),   # row 4: Lyle duplicate (also row 2) -> bumpable
        ("Luther", 0, 5, "z"),
        ("Anya", 0, 5, "a2"),   # the only tail entry to fill row 4 is another Anya
    ])
    order = compute_auto_order(before).order
    # No two adjacent entries share a member.
    for i in range(1, len(order)):
        assert not (set(order[i].members) & set(order[i - 1].members))


# ---------------------------------------------------------------------------
# build_entry_views: groups + durations (round 2/3) — and metrics
# ---------------------------------------------------------------------------

def test_build_entry_views_parses_group_members_and_duration():
    entries = [
        {"id": 1, "singer": "Anya & Tara", "songs_sung": 1, "wait_minutes": 20,
         "song_artist": "s1", "singers_json": '["Anya", "Tara"]', "duration": 210},
        {"id": 2, "singer": "Bob & Sue", "songs_sung": 0, "wait_minutes": 5,
         "song_artist": "s2", "duration": None},   # no singers_json -> split on "&"
    ]
    views = build_entry_views(entries)
    assert views[0].members == ("anya", "tara")
    assert views[0].duration == 210
    assert views[1].members == ("bob", "sue")


def test_build_entry_views_tolerates_malformed_singers_json():
    # Corrupt data must not crash the reorder — fall back to splitting the display.
    entries = [{"id": 1, "singer": "Ann & Bo", "songs_sung": 0,
                "wait_minutes": 5, "song_artist": "s", "singers_json": "{not json"}]
    views = build_entry_views(entries)
    assert views[0].members == ("ann", "bo")


def test_metrics_projected_wait_uses_real_durations():
    # Two 10-min songs ahead (+turnaround) => the third singer is projected to wait
    # well over an hour; also exercises the reset (each distinct singer here).
    order = _mk([
        ("A", 0, 0, "a", 600), ("B", 0, 0, "b", 600), ("C", 0, 55, "c", 240),
    ])
    m = compute_metrics(order)
    # C waits (10 + 0.5) + (10 + 0.5) = 21 min of songs ahead + 55 elapsed = 76 > 60.
    # Threshold is 76 (not 60) so this regresses if the real durations are ignored
    # in favour of the 4-min fallback (which would give only 55 + 2*4.5 = 64).
    assert m["projected_over_hour"] >= 1
    assert m["worst_projected_wait"] >= 76
    assert "close_repeats" in m


# ---------------------------------------------------------------------------
# "Being made" pinning — tracks still being generated sink to the very bottom
# ---------------------------------------------------------------------------

def test_being_made_entries_pinned_to_bottom():
    # M1/M2 are being made but sit mid-queue; a long-waiting new singer is below
    # them. After reorder, both making entries drop to the very bottom regardless
    # of fairness/wait, and the new singer is woven above them.
    before = _mk([
        ("A", 0, 5, "a"), ("B", 0, 5, "b"), ("C", 0, 5, "c"),
        ("M1", 0, 90, "m1"), ("D", 0, 5, "d"), ("M2", 0, 80, "m2"),
        ("New", 0, 120, "n"),
    ])
    before[3].being_made = True   # M1
    before[5].being_made = True   # M2
    res = compute_auto_order(before)
    ids = res.ordered_ids
    # Both making entries occupy the final two slots (relative order preserved).
    assert ids[-2:] == [4, 6]
    # Neither making id appears anywhere above the bottom two.
    assert 4 not in ids[:-2] and 6 not in ids[:-2]


def test_being_made_at_top_is_still_sunk():
    # Even a making entry sitting in the sacred locked head (row 1) drops to the
    # bottom — the pin overrides the lock.
    before = _mk([
        ("M", 0, 5, "m"), ("A", 3, 5, "a"), ("B", 3, 5, "b"),
        ("C", 3, 5, "c"), ("D", 3, 5, "d"),
    ])
    before[0].being_made = True
    res = compute_auto_order(before)
    assert res.ordered_ids[-1] == 1          # M sunk to the bottom
    assert 1 not in res.ordered_ids[:-1]


def test_being_made_reason_and_moved_flag():
    before = _mk([("A", 0, 5, "a"), ("M", 0, 5, "m"), ("B", 0, 5, "b")])
    before[1].being_made = True
    res = compute_auto_order(before)
    pin = next(p for p in res.placements if p.entry.id == 2)
    assert "being made" in pin.reason.lower()
    assert pin.new_index == 2                # moved from index 1 to the bottom
    assert pin.moved


def test_all_being_made_preserves_order():
    before = _mk([("A", 0, 5, "a"), ("B", 0, 5, "b"), ("C", 0, 5, "c")])
    for e in before:
        e.being_made = True
    res = compute_auto_order(before)
    assert res.ordered_ids == [1, 2, 3]
    assert not res.changed


def test_being_made_pin_preserves_top_five_bump():
    # A "being made" entry sits in row 1 (removed from the weave, sunk to the
    # bottom), which shifts a DUPLICATE of a locked singer from original row 6 up
    # into woven row 5. That duplicate must still be bumped OUT of the top five —
    # bump detection uses woven position, not the stale original index.
    before = _mk([
        ("M", 0, 5, "m"),                                   # id 1 — being made
        ("A", 1, 5, "a1"), ("B", 1, 5, "b"), ("C", 1, 5, "c"),
        ("D", 1, 5, "d"),
        ("A", 1, 5, "a2"),                                  # id 6 — duplicate of A
        ("New", 0, 5, "n"),                                 # id 7 — fills the freed row
    ])
    before[0].being_made = True
    res = compute_auto_order(before)
    ids = res.ordered_ids
    assert ids[-1] == 1                    # M sunk to the very bottom
    assert 6 not in ids[:5]                # duplicate A bumped out of the top five
    assert ids[4] == 7                     # a genuinely different singer takes row 5


def test_build_entry_views_flags_being_made_status():
    entries = [
        {"id": 1, "singer": "A", "song_artist": "a", "status": "Waiting"},
        {"id": 2, "singer": "B", "song_artist": "b", "status": "Being Made (!)"},
    ]
    views = build_entry_views(entries)
    assert views[0].being_made is False
    assert views[1].being_made is True


# ---------------------------------------------------------------------------
# Determinism — same input always yields the same output (no hidden randomness)
# ---------------------------------------------------------------------------

def test_output_is_deterministic_and_non_mutating():
    before = _mk([
        ("A", 4, 5, "a"), ("B", 5, 5, "b"), ("New", 0, 40, "n"),
        ("C", 4, 5, "c"), ("D", 5, 5, "d"), ("Z", 3, 5, "z1"),
        ("Z", 3, 5, "z2"), ("E", 2, 20, "e"), ("F", 1, 30, "f"),
    ])
    def snapshot(views):
        return [(e.id, e.owner, e.sung, e.wait_minutes, e.seq, e.orig_index,
                 e.members, e.duration) for e in views]

    before_state = snapshot(before)
    r1 = compute_auto_order(before)
    r2 = compute_auto_order(before)
    assert r1.ordered_ids == r2.ordered_ids
    # Pure function: the input entries are not mutated in order OR field values.
    assert snapshot(before) == before_state


# ---------------------------------------------------------------------------
# Priority bias — KJ manual bump up / down
# ---------------------------------------------------------------------------

def test_bump_up_lifts_a_mid_queue_singer():
    # Everyone equally fair/waiting; without a bump the order is stable. Bumping the
    # last singer up must lift them above their un-bumped peers (out of the frozen
    # head, which is rows 1-5 by default).
    before = _mk([
        ("A", 1, 5, "a"), ("B", 1, 5, "b"), ("C", 1, 5, "c"),
        ("D", 1, 5, "d"), ("E", 1, 5, "e"), ("F", 1, 5, "f"),
        ("G", 1, 5, "g"), ("H", 1, 5, "h"),
    ])
    baseline = compute_auto_order(before).ordered_ids
    assert baseline.index(8) > 5  # H sits low without a bump
    before[7].priority_bias = 1   # bump H up
    order = compute_auto_order(before).ordered_ids
    assert order.index(8) < baseline.index(8)   # H rose
    assert order.index(8) == 5                   # lands at the first free (post-head) slot


def test_bump_down_sinks_an_otherwise_fair_singer():
    # New (0 sung) is the fairest, so in the free region (rows 6+) it normally floats
    # to the first open slot; bumping New down must drop it to the very tail instead.
    before = _mk([
        ("A", 1, 5, "a"), ("B", 1, 5, "b"), ("C", 1, 5, "c"),
        ("D", 1, 5, "d"), ("E", 1, 5, "e"),   # frozen head (rows 1-5)
        ("F", 1, 5, "f"), ("G", 1, 5, "g"), ("New", 0, 5, "n"),  # free region
    ])
    baseline = compute_auto_order(before).ordered_ids
    assert baseline.index(8) == 5   # New normally floats to the first free slot
    before[7].priority_bias = -1    # bump New down
    order = compute_auto_order(before).ordered_ids
    assert order.index(8) == len(order) - 1   # New sinks to the very bottom


def test_bump_up_never_forces_a_back_to_back():
    # Anya has two entries, both bumped up hard. The veto must still prevent her two
    # songs landing adjacent — other free-region singers get woven between them.
    before = _mk([
        ("Andrew", 1, 5, "x"), ("Bob", 1, 5, "b"), ("Cara", 1, 5, "c"),
        ("Dan", 1, 5, "d"), ("Ed", 1, 5, "e"),   # frozen head
        ("Anya", 0, 5, "a1"), ("Fred", 1, 5, "f"),
        ("Anya", 0, 5, "a2"), ("Gwen", 1, 5, "g"),
    ])
    before[5].priority_bias = 1
    before[7].priority_bias = 1
    order = compute_auto_order(before).order
    for i in range(1, len(order)):
        assert not (set(order[i].members) & set(order[i - 1].members))


def test_bump_does_not_disturb_being_made_pin():
    # A bumped-up "being made" entry is still held at the very bottom — the pin is
    # structural (decided before scoring), so the bias can't lift it into the weave.
    before = _mk([
        ("A", 1, 5, "a"), ("B", 1, 5, "b"), ("C", 1, 5, "c"),
        ("M", 0, 5, "m"),
    ])
    before[3].being_made = True
    before[3].priority_bias = 1
    order = compute_auto_order(before).ordered_ids
    assert order[-1] == 4   # M stays pinned at the bottom despite the bump-up


def test_build_entry_views_reads_priority_bias():
    entries = [
        {"id": 1, "singer": "A", "song_artist": "a", "priority_bias": 1},
        {"id": 2, "singer": "B", "song_artist": "b", "priority_bias": -1},
        {"id": 3, "singer": "C", "song_artist": "c"},  # missing -> 0
    ]
    views = build_entry_views(entries)
    assert views[0].priority_bias == 1
    assert views[1].priority_bias == -1
    assert views[2].priority_bias == 0
