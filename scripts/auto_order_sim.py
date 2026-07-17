#!/usr/bin/env python3
"""Offline review harness for the Auto Order rotation algorithm.

Runs the pure ``compute_auto_order`` against:

  * REAL scenarios reconstructed from an archived rotation DB (a read-only copy of
    the device DB), and
  * SYNTHETIC edge cases hand-built to match the requirement bullets.

For each scenario it prints a BEFORE | AFTER side-by-side table (with each row's
sung-count, wait, and what moved) plus a metrics line, so the KJ can eyeball whether
the button did what he'd do — and give feedback per scenario. Nothing here touches
production; it only reads a DB snapshot.

Usage:
    python3 scripts/auto_order_sim.py                       # synthetic + a few real
    python3 scripts/auto_order_sim.py --db /tmp/snap.db     # use a specific snapshot
    python3 scripts/auto_order_sim.py --night 2026-07-09    # a specific real night
    python3 scripts/auto_order_sim.py --synthetic-only
    python3 scripts/auto_order_sim.py --list-nights
"""

import argparse
import os
import sqlite3
import sys
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "kj-controller"))

from auto_order import (  # noqa: E402
    AutoOrderConfig, EntryView, compute_auto_order, _locked_indices,
)

DEFAULT_DB = "/tmp/nomadpc_rotation_snapshot.db"


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def _tier(sung):
    if sung == 0:
        return "NEW"
    if sung == 1:
        return "1x"
    if sung > 5:
        return "HEAVY"
    return f"{sung}x"


def _fmt_wait(w):
    if w is None:
        return "  ∞"
    return f"{w:>3}m"


def _row_label(e):
    who = e.singer[:13]
    song = (e.song_artist or "")[:26]
    return f"{who:<13} {_tier(e.sung):>5} {_fmt_wait(e.wait_minutes)}  {song}"


def render_scenario(title, before, result, config, note=""):
    print("\n" + "=" * 108)
    print(f"SCENARIO: {title}")
    if note:
        print(f"  {note}")
    print("=" * 108)

    after = result.order
    # Map id -> new index for move arrows.
    before_index = {e.id: i for i, e in enumerate(before)}
    locked_before = _locked_indices(before, config)
    locked_after = _locked_indices(before, config)  # locked positions are fixed slots

    left_w = 52
    print(f"{'BEFORE (raw submission order)':<{left_w}}    {'AFTER (Auto Order)'}")
    print(f"{'-' * left_w}    {'-' * left_w}")

    for i in range(max(len(before), len(after))):
        lft = ""
        if i < len(before):
            marker = "🔒" if i in locked_before else "  "
            lft = f"{marker}{i + 1:>2}. {_row_label(before[i])}"
        rgt = ""
        if i < len(after):
            e = after[i]
            old = before_index.get(e.id)
            if i in locked_after:
                arrow = "🔒"
            elif old is None:
                arrow = "  "
            elif old == i:
                arrow = "· "
            elif old > i:
                arrow = "↑ "
            else:
                arrow = "↓ "
            rgt = f"{arrow}{i + 1:>2}. {_row_label(e)}"
        print(f"{lft:<{left_w}}    {rgt}")

    mb, ma = result.metrics_before, result.metrics_after
    def mline(tag, m):
        return (f"  {tag}: back-to-back={m['back_to_back']} "
                f"close_repeats(<{m['spread_target']})={m['close_repeats']} "
                f"fairness_inversions={m['fairness_inversions']} "
                f"proj_over_1hr={m['projected_over_hour']} "
                f"worst_proj={m['worst_projected_wait']}m "
                f"median_repeat_gap={m['median_repeat_gap']}")
    print()
    print(mline("BEFORE", mb))
    print(mline("AFTER ", ma))
    # Highlight any hard-constraint breaches introduced by the reorder.
    breaches = _hard_constraint_breaches(before, after, config)
    if breaches:
        print("  ⚠️  CONSTRAINT BREACHES:")
        for b in breaches:
            print(f"      - {b}")
    else:
        print("  ✅ constraints OK (rows 1-5 frozen, submission order preserved)")


def _hard_constraint_breaches(before, after, config):
    """Verify the hard constraints the algorithm promises."""
    breaches = []
    locked = _locked_indices(before, config)
    # 1) locked rows unchanged (rows 1-3 always; 4-5 unless bumped for a duplicate)
    for i in locked:
        if i >= len(after) or after[i].id != before[i].id:
            breaches.append(f"row {i+1} changed (should be frozen)")
    # 2) per-owner submission order preserved
    seen_seq = {}
    for e in after:
        prev = seen_seq.get(e.owner)
        if prev is not None and e.seq < prev:
            breaches.append(f"{e.singer}: submission order violated (seq {e.seq} after {prev})")
        seen_seq[e.owner] = e.seq
    # 3) same set of entries
    if {e.id for e in before} != {e.id for e in after}:
        breaches.append("entry set changed (lost/added a row)")
    return breaches


# ---------------------------------------------------------------------------
# Real scenario reconstruction
# ---------------------------------------------------------------------------

def _split_members(name):
    """Individual singers in a display string, lowercased (splits duets on '&')."""
    parts = tuple(p.strip().lower() for p in (name or "").split("&") if p.strip())
    return parts or ((name or "").strip().lower(),)


def _parse_ts(s):
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M:%S.%f"):
        try:
            return datetime.strptime(s, fmt)
        except (ValueError, TypeError):
            continue
    return None


def reconstruct_real_scenario(db_path, night, perf_index):
    """Rebuild the queue state partway through a real night.

    At the moment song #``perf_index`` (in performance order) is being sung:
      * songs with position < perf_index (Done-ish) set each singer's sung-count and
        last-sang proxy;
      * songs submitted by then but not yet performed form the waiting queue in raw
        submission order (= the pre-Auto-Order input).
    """
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    rows = [dict(r) for r in conn.execute(
        "SELECT singer, song_artist, status, position, created_at, duration "
        "FROM rotation_archive WHERE night_date=? ORDER BY position", (night,))]
    conn.close()
    if len(rows) <= perf_index + 3:
        return None

    now_row = rows[perf_index]
    now_t = _parse_ts(now_row["created_at"])
    if now_t is None:
        return None

    sung = {}
    last_sang_t = {}
    for r in rows[:perf_index]:
        owner = r["singer"].strip().lower()
        sung[owner] = sung.get(owner, 0) + 1
        t = _parse_ts(r["created_at"])
        if t and (owner not in last_sang_t or t > last_sang_t[owner]):
            last_sang_t[owner] = t

    first_entered_t = {}
    for r in rows:
        owner = r["singer"].strip().lower()
        t = _parse_ts(r["created_at"])
        if t and (owner not in first_entered_t or t < first_entered_t[owner]):
            first_entered_t[owner] = t

    # Waiting queue: submitted (created_at <= now_t) but not yet performed
    # (position >= perf_index), skipping Left rows.
    waiting = []
    for pos_i, r in enumerate(rows[perf_index:], start=perf_index):
        if (r["status"] or "").lower() == "left":
            continue
        t = _parse_ts(r["created_at"])
        if t is None or t > now_t:
            continue
        waiting.append((pos_i, t, r))
    if len(waiting) < 6:
        return None

    # Realistic live state: the head (next few to perform) is already curated, so
    # order it by performance position; the backlog below is raw submission order
    # (what Auto Order actually has to sort out).
    head_n = 5
    head = sorted(waiting, key=lambda x: x[0])[:head_n]
    head_keys = {id(x) for x in head}
    tail = [x for x in waiting if id(x) not in head_keys]
    tail.sort(key=lambda x: x[1])  # submission order
    ordered = head + tail
    ordered = ordered[:24]  # realistic on-screen queue depth

    # Reject nights with degenerate timestamps: if the whole night's submissions
    # span too short a window, per-singer waits collapse to ~0 and the scenario
    # can't exercise the wait/fairness tradeoff realistically (e.g. 2026-04-09,
    # where 118 songs share an 80-minute created_at window).
    night_times = [x for x in (_parse_ts(rr["created_at"]) for rr in rows) if x]
    if night_times:
        span_min = (max(night_times) - min(night_times)).total_seconds() / 60.0
        if span_min < 150:  # a real night spans hours; <2.5h means dead timestamps
            return None

    per_owner = {}
    views = []
    for idx, (pos_i, t, r) in enumerate(ordered):
        owner = r["singer"].strip().lower()
        seq = per_owner.get(owner, 0)
        per_owner[owner] = seq + 1
        if owner in last_sang_t:
            wait = int((now_t - last_sang_t[owner]).total_seconds() // 60)
        elif owner in first_entered_t:
            wait = int((now_t - first_entered_t[owner]).total_seconds() // 60)
        else:
            wait = None
        views.append(EntryView(
            id=idx + 1,
            owner=owner,
            sung=sung.get(owner, 0),
            wait_minutes=max(0, wait) if wait is not None else None,
            seq=seq,
            orig_index=idx,
            singer=r["singer"],
            song_artist=r["song_artist"] or "",
            members=_split_members(r["singer"]),
            duration=r["duration"],
        ))

    # Reject scenarios whose wait data is unusable — a night with degenerate
    # timestamps (e.g. 2026-04-09) yields all-None/near-zero waits and can't
    # exercise the wait/fairness tradeoff. Require real, spread waits.
    real_waits = [v.wait_minutes for v in views if v.wait_minutes]
    if len(real_waits) < max(3, len(views) // 3) or max(real_waits) < 25:
        return None
    return views


# ---------------------------------------------------------------------------
# Synthetic edge cases (the requirement bullets, made concrete)
# ---------------------------------------------------------------------------

def _mk(views_spec):
    """views_spec: list of (singer, sung, wait, song). seq auto-assigned per owner."""
    per_owner = {}
    out = []
    for idx, (singer, sung, wait, song) in enumerate(views_spec):
        owner = singer.strip().lower()
        seq = per_owner.get(owner, 0)
        per_owner[owner] = seq + 1
        out.append(EntryView(
            id=idx + 1, owner=owner, sung=sung, wait_minutes=wait, seq=seq,
            orig_index=idx, singer=singer, song_artist=song,
            members=_split_members(singer),
        ))
    return out


def synthetic_scenarios():
    scenarios = []

    # 1) A brand-new singer stuck deep in the queue behind repeat singers.
    scenarios.append((
        "New singer stuck at the back",
        "Priya has never sung; she's at row 12 behind Mars (5x) and Shylo (4x). "
        "Should be promoted up past the 6-10 band.",
        _mk([
            ("Alanna", 4, 10, "Careless Whisper"),
            ("Mars W", 5, 15, "Get Scared"),
            ("Alanna", 4, 10, "Billie Jean"),
            ("Jaime E", 1, 27, "Too Much Love"),
            ("Matthew", 2, 1, "Distant Lover"),
            ("Mars W", 5, 15, "I'm Not Okay"),
            ("Shylo R.", 4, 37, "Todo De Ti"),
            ("Lulu K", 5, 5, "Low On Gas"),
            ("Matthew", 2, 1, "Lover You Should"),
            ("Jaime E", 1, 27, "Man Without Love"),
            ("Mars W", 5, 15, "Idontwannabeyou"),
            ("Priya", 0, 45, "Dancing Queen"),
        ]),
    ))

    # 2) One singer's 4-song burst needs spreading.
    scenarios.append((
        "One singer's 4-song burst",
        "Mars submitted 4 songs at once (rows 6,7,8,9). Spread them out with others between.",
        _mk([
            ("Alanna", 3, 8, "Careless Whisper"),
            ("Jaime E", 2, 20, "Too Much Love"),
            ("Matthew", 2, 12, "Distant Lover"),
            ("Lulu K", 3, 6, "Low On Gas"),
            ("Shylo R.", 3, 30, "Todo De Ti"),
            ("Mars W", 2, 15, "Get Scared"),
            ("Mars W", 2, 15, "I'm Not Okay"),
            ("Mars W", 2, 15, "Idontwanna"),
            ("Mars W", 2, 15, "Home"),
            ("Donte", 1, 40, "I'd Rather Be"),
            ("Eris", 2, 18, "Folsom Prison"),
            ("Jessie", 2, 22, "In A Jar"),
        ]),
    ))

    # 3) Fairness: one-timers behind heavy singers.
    scenarios.append((
        "One-timers behind heavy repeat singers",
        "Two singers who've sung once each are stuck behind singers with 6-8 songs.",
        _mk([
            ("Andrew", 8, 20, "ABBA"),
            ("Lyle", 7, 15, "Foo Fighters"),
            ("Anya", 6, 18, "Free Throw"),
            ("Luther", 5, 22, "Bob Dylan"),
            ("Shylo R.", 8, 12, "Palaye Royale"),
            ("Lyle", 7, 15, "Tool"),
            ("Anya", 6, 18, "Three Days Grace"),
            ("Kasey", 1, 50, "Laufey"),
            ("Andrew", 8, 20, "Hole In Soul"),
            ("Walter", 1, 55, "Michael Buble"),
            ("Lyle", 7, 15, "The Pot"),
            ("Anya", 6, 18, "Gives You Hell"),
        ]),
    ))

    # 4) Back-to-back at the 5->6 boundary (row 5 and row 6 same singer).
    scenarios.append((
        "Back-to-back across the frozen boundary",
        "Row 5 (frozen) and row 6 are both Matthew. Row 6 must be pushed apart from row 5.",
        _mk([
            ("Alanna", 3, 8, "Careless Whisper"),
            ("Jaime E", 2, 20, "Too Much Love"),
            ("Lulu K", 3, 6, "Low On Gas"),
            ("Shylo R.", 3, 30, "Todo De Ti"),
            ("Matthew", 2, 12, "Distant Lover"),
            ("Matthew", 2, 12, "Lover You Should"),
            ("Donte", 1, 40, "I'd Rather Be"),
            ("Eris", 2, 18, "Folsom Prison"),
            ("Jessie", 2, 22, "In A Jar"),
            ("Vince", 3, 10, "Space Oddity"),
        ]),
    ))

    # 5) Everyone is new (early night) — tiny pool, spacing floor must degrade gracefully.
    scenarios.append((
        "Early night — everyone new, small pool",
        "6 singers, nobody has sung. Only real job: keep any repeat submissions apart.",
        _mk([
            ("Andrew", 0, 5, "First Song"),
            ("Lyle", 0, 8, "My Hero"),
            ("Anya", 0, 3, "Two Beers In"),
            ("Lyle", 0, 8, "Escape"),
            ("Luther", 0, 12, "The Man In Me"),
            ("Anya", 0, 3, "I Hate Everything"),
        ]),
    ))

    # 6) Long-waiter who hasn't sung in over an hour.
    scenarios.append((
        "Someone waiting over an hour",
        "Bella last sang 75m ago; should jump ahead of recently-sung singers.",
        _mk([
            ("Alanna", 3, 8, "Careless Whisper"),
            ("Mars W", 4, 12, "Get Scared"),
            ("Lulu K", 3, 6, "Low On Gas"),
            ("Shylo R.", 3, 15, "Todo De Ti"),
            ("Matthew", 2, 10, "Distant Lover"),
            ("Vince", 3, 9, "Space Oddity"),
            ("Bella", 2, 75, "Landslide"),
            ("Eris", 2, 18, "Folsom Prison"),
            ("Jessie", 2, 22, "In A Jar"),
            ("Donte", 3, 14, "I'd Rather Be"),
        ]),
    ))

    return scenarios


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def list_nights(db_path):
    conn = sqlite3.connect(db_path)
    for r in conn.execute(
        "SELECT night_date, COUNT(*) n, COUNT(DISTINCT singer) s "
        "FROM rotation_archive GROUP BY night_date HAVING n > 20 "
        "ORDER BY night_date DESC"):
        print(f"  {r[0]}  {r[1]:>3} songs  {r[2]:>2} singers")
    conn.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=DEFAULT_DB)
    ap.add_argument("--night", default=None)
    ap.add_argument("--perf-index", type=int, default=None,
                    help="performance index (song #) to snapshot at; default: a few spread out")
    ap.add_argument("--synthetic-only", action="store_true")
    ap.add_argument("--real-only", action="store_true")
    ap.add_argument("--list-nights", action="store_true")
    args = ap.parse_args()

    config = AutoOrderConfig()

    if args.list_nights:
        list_nights(args.db)
        return

    if not args.real_only:
        for title, note, before in synthetic_scenarios():
            result = compute_auto_order(before, config)
            render_scenario(title, before, result, config, note)

    if args.synthetic_only:
        return

    if not os.path.exists(args.db):
        print(f"\n(no DB snapshot at {args.db}; skipping real scenarios)")
        return

    nights = [args.night] if args.night else ["2026-07-09", "2026-05-14", "2026-04-30"]
    for night in nights:
        indices = [args.perf_index] if args.perf_index is not None else [15, 35, 55]
        for pidx in indices:
            before = reconstruct_real_scenario(args.db, night, pidx)
            if not before:
                continue
            result = compute_auto_order(before, config)
            render_scenario(
                f"REAL {night} — snapshot at song #{pidx}",
                before, result, config,
                note="reconstructed from archive (raw submission order in, Auto Order out)")


if __name__ == "__main__":
    main()
