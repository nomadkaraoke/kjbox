# Auto Order — fair, best-effort rotation reordering (design + rationale)

**Date:** 2026-07-16 · **Status:** COMPLETE — algorithm validated (14/14 review scenarios
approved across 3 feedback rounds) **and wired** (on-demand button + auto-mode toggle),
shipped in v0.90.0.
**Module:** `kj-controller/auto_order.py` · **Tests:** `kj-controller/tests/unit/test_auto_order.py`
(100% coverage) + `tests/integration/test_auto_order_routes.py` · **Review harness:**
`scripts/auto_order_review.py` (+ `scripts/auto_order_sim.py`)

> **If Auto Order ever does something surprising in a live show, start here.** This
> document explains *why* each rule exists so you can reason about the behaviour and
> tune it without re-introducing a problem a past round already fixed. Every scoring
> weight is a named constant in `AutoOrderConfig` — change a weight, re-run the harness,
> compare before/after on real nights, and check the unit tests still pass.

---

## 1. What it's for

A one-click / automatic reordering of the live karaoke rotation that imitates what
Andrew does by hand: keep it **fair** to all singers while keeping everyone **happy**.
The end goal is an **auto-reorder MODE** that re-runs on every rotation change (e.g. a
new song is added), so a stand-in KJ never has to think about ordering.

It is deliberately *best-effort*: fairness, happiness, spacing and wait-bounding are
contradictory forces. The target is "what Andrew would do", not mathematical optimality.

## 2. Hard constraints (never violated — enforced + unit-tested)

1. **Rows 1-3 are sacred.** Never moved. Those singers are about to go on.
2. **Rows 4-5 are frozen too**, with ONE exception: if a singer already appears in rows
   1-3, their *second* copy sitting at row 4 or 5 may be **bumped out of the top five**
   (down to row 6+). Nobody should occupy two of the first five slots. (`_locked_indices`)
3. **Each singer's own songs keep submission order.** Their next song stays next. (We only
   ever move the *head* of each singer's queue, so a 2nd song can't jump ahead of a 1st.)

## 3. Soft goals (scored, best-effort)

1. New singers (0 sung tonight) ahead of anyone who's already sung.
2. Fewer-sung singers ahead of heavy repeat singers.
3. Never the same singer twice in a row; ideally ≥ `spread_target` other singers between
   a singer's songs. Spacing is by **individual member**, so "Tara" then "Anya & Tara"
   counts as Tara twice.
4. Nobody waits unreasonably long (~>60 min) *by the time they actually sing*.

## 4. The algorithm — scored greedy weave

`compute_auto_order(entries, config)` is a **pure function** (no Flask/SQLite/IO). It
operates on `EntryView` objects (decoupled from DB rows; build them from decorated
rotation entries with `build_entry_views`, or from archived rows in the harness).

It walks the queue **one slot at a time, front to back**. Locked rows keep their entry;
each open slot is filled by the highest-scoring *eligible* candidate (the head of each
singer's remaining-songs queue). Score terms (all weights in `AutoOrderConfig`):

| Term | What it does | Default | Notes |
|---|---|---|---|
| **fairness** | `w_fair · 1/(1+projected_sung)` — fewer songs → earlier | `w_fair=160` | uses *projected* sung count (§5) |
| **new promote** | one-time boost to get a brand-new singer into the queue | `w_new_promote=400` | only on their **first** placement (§5) |
| **wait** | mild pull for a longer *projected* wait | `w_wait=60` | up to `wait_overflow_cap=1.6×` |
| **overdue** | strong, capped escalation once projected wait blows past the max | `w_overdue=450`/`overdue_step_minutes=30`, cap `×3` | can beat the new-singer boost so nobody is buried past ~1 hr (§6) |
| **back-to-back veto** | near-infinite penalty for the same member at distance 1 | `w_back_to_back=1e5` | the only ~hard spacing rule |
| **spacing** | ramped penalty for a repeat within `spread_target` slots | `w_spacing=300`, `spread_target=5` | prefer a *distinct* under-served singer over repeating someone too soon |
| **bump-out-of-top-5** | strong penalty for leaving a bumped row-4/5 duplicate inside the top five | `w_bump_top5=6000` | **below** the back-to-back veto, so it yields when bumping would create a back-to-back (§7) |
| **stickiness** | mild bias to keep an entry near its original slot | `w_stick=18` | ~0 for a fresh new singer so they float up freely |

Determinism: no randomness anywhere; ties break by (score, earliest submission, lowest
original index, id). Same input → identical output (unit-tested).

## 5. The two big ideas — "project forward" and "reset on placement"

These emerged from review feedback and do most of the heavy lifting. Both are applied to
**fairness** *and* **wait**, so a burst-singer's later songs correctly defer to others.

- **Projected sung count** (`_placed_before`): fairness scores a singer's Nth queued song
  as if they've already sung the earlier N-1. So a new singer with 3 songs is judged
  NEW → 1× → 2× down the queue, and their 3rd song rightly loses to a genuinely
  under-served singer. The new-singer promote boost likewise only fires until their first
  song is placed.

- **Projected wait with reset** (`_projected_wait`): a singer's wait is how long they'll
  have waited *when their slot comes up*. If they've already been scheduled earlier in the
  queue (or sit in the frozen head), the wait is measured from that song — it **reset**
  then — not from "time since they last sang before this queue". Without this, a singer
  who last sang 100 min ago kept a huge overdue boost on *every* queued song and sang
  repeatedly ahead of a brand-new singer (the round-3 #7 bug).

- **Real durations** (`_slot_minutes`): "when a slot comes up" uses the **actual linked
  karaoke-file duration** of each song ahead (`EntryView.duration`, seconds), falling back
  to `avg_song_minutes=4` only for unlinked entries, plus `turnaround_minutes=0.5` between
  songs. In production almost every rotation entry is linked, so this is real data.

## 6. The fairness vs. wait tension (goal #1 vs #4)

A flood of new singers could leave an established singer waiting hours. The **overdue**
term resolves this: once a singer's *projected* wait passes the max it escalates strongly
enough to leapfrog even new singers — but for **one song only**, because placing them
resets their wait and they drop back. Moderate waits (well under an hour) do **not** beat
a new singer. Both directions are unit-tested (`test_egregiously_overdue…`,
`test_moderate_waiter_does_not_bury_a_new_singer`).

Note: in a queue longer than an hour of music, *many* people are unavoidably projected to
wait >1 hr — that's physics, not a bug. Judge by **`worst_projected_wait`** (the most
-overdue person getting pulled up), not by the raw count.

## 7. Why "bump out of top 5" is a penalty, not a hard rule

Bumping a row-4/5 duplicate down frees its slot, which must be filled by someone else. If
the only remaining candidate would land next to a frozen appearance of *their own* member,
a hard bump would create a back-to-back. So the bump is a strong **penalty** ranked below
the back-to-back veto: it normally happens, but yields when the alternative is worse
(`test_bump_yields_when_it_would_force_a_back_to_back`).

## 8. Data grounding

`rotation_archive` on nomadpc holds ~20 real nights. Each row has `created_at` (raw
submission order = the INPUT) and `position` (Andrew's final performance order = his
ACTUAL hand-ordering, a supervised signal). Findings that shaped the defaults:

- Andrew spreads a singer's repeat songs a lot: **median gap 10** other singers, mean 14;
  back-to-back only ~5% (early night, unavoidable). So a fairness-first weave *naturally*
  produces big gaps when the pool is large; `spread_target` is only the small-pool floor.
- **Gotcha — dead-timestamp nights:** some nights (e.g. 2026-04-09) have all `created_at`
  in an ~80-minute window, so reconstructed waits collapse to 0/None and can't exercise the
  wait dimension. The harness filters these out (requires real, spread waits).
- The device has **no `sqlite3` CLI** — query with `python3`. A read-only snapshot copy
  lives at `/tmp/nomadpc_rotation_snapshot.db` (re-copy with `scp nomadpc:~/kjdata/rotation.db …`).

## 9. Review harness (how to iterate)

- `python3 scripts/auto_order_review.py` → writes & opens a self-contained
  `/tmp/auto_order_review.html`: 6 synthetic edge cases + 8 auto-selected diverse real
  scenarios, each showing **before/after side-by-side** with move arrows, sung/wait pills,
  and metrics. Per-scenario ✅/⚠️ + notes; "Export my feedback" → paste back to a session.
  **Scenario selection keys on INPUT features only**, so it stays stable across algorithm
  changes (you re-review the same set and can confirm a fix).
- `python3 scripts/auto_order_sim.py [--night … --perf-index …]` → text before/after for a
  specific reconstructed snapshot; includes a live hard-constraint checker.
- Workflow: change a weight → re-run harness → eyeball real nights → run the unit tests.

## 10. Metrics reference (`compute_metrics`)

`back_to_back`, `close_repeats` (repeats within `spread_target`), `fairness_inversions`
(higher-sung before lower-sung — a *crude* indicator; wait-justified inversions look "bad"
but aren't), `over_max_wait` (elapsed — the reorder CAN'T reduce this), `projected_over_hour`
& `worst_projected_wait` (what the reorder CAN improve — use these), `median_repeat_gap`.

## 11. Review-feedback history (the "why" behind each change)

- **Round 1** (edge cases all ✅): reworked from the initial design — added the **frozen-head
  exception** (bump row 4/5 duplicate), **reversed the burst philosophy** (don't force a
  burst to spread early; nail the early queue and let extras bunch at the tail — more
  requests arrive to re-space them; removed the "pigeonhole" force-early rule), made
  **spacing dominant** (prefer a distinct under-served singer over a gap-2 repeat), keyed
  spacing on **individual members** (duets), and gated the new-promote boost to a singer's
  **first** placement.
- **Round 2** (13/14): **projected sung count** so a burst-singer's later songs defer;
  first pass at overdue wait handling.
- **Round 3** (13/14 → then 14/14): **wait resets on placement** (the real #7 fix — a
  repeat singer no longer buries a new singer), **real song durations + 30 s turnaround**
  for the projection, excluded dead-timestamp nights, and made scenario selection stable.

## 12. How it's wired (v0.90.0)

Two entry points, both feeding the same `run_auto_order(app)` glue in `routes.py`
(decorate the queue → `build_entry_views` → `compute_auto_order` → apply):

- **On-demand button** — "Auto Order" in the rotation header (`templates/index.html`) →
  `autoOrderRotation()` (`static/app.js`) → `POST /rotation/auto-order`. Applies once and
  returns decorated entries + history for re-render.
- **Auto-mode toggle** — "Auto Order the rotation" checkbox in the Requests settings modal,
  persisted as `SingStore` meta `rotation_auto_reorder` (default OFF, opt-in) via
  `/rotation/requests/config`. When on, `maybe_auto_reorder(app)` re-runs Auto Order after
  every new entry — hooked into the three add paths: admin request approval + auto-approve
  (`sing.py`) + KJ manual add (`/rotation/add`). Best-effort: a reorder failure never blocks
  the add/approve.

**Apply mechanism** — `RotationStore.reorder_by_ids(ordered_ids)` reassigns the visible
entries' *currently-occupied* position slots in the new order, so any interleaved Done rows
keep their positions. `RotationManager.reorder_by_ids` wraps it in one undo checkpoint and
skips entirely when the queue is already in order (no undo-stack pollution).

**Safety:** the frozen rows 1-3 (and un-bumped 4-5) can never move because `compute_auto_order`
never emits them out of place; the endpoint/trigger tests assert rows 1-3 are unchanged.

## 13. Open items / future work

- **Group sung-count nuance:** fairness/`sung` is keyed on the whole entry (`songs_sung` =
  min across members via the existing decorator); spacing already uses individual members.
  A singer who performs both solo and in a duet is counted as two "owners" for fairness. Not
  yet a problem in review, but revisit if it mis-handles a heavy solo+duet singer.
- **Tuning knobs most likely to want adjustment:** `spread_target` (how spread the queue
  feels), `w_overdue`/`overdue_step_minutes` (how aggressively long-waiters jump), `max_wait_minutes`.
