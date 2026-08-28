"""Auto Order: fair, best-effort reordering of a live karaoke rotation.

Pure algorithm — no Flask, no SQLite, no I/O. The web layer decorates rotation
rows and hands them to :func:`compute_auto_order`, which returns a new ordering
plus a per-entry rationale and before/after metrics. This module is deliberately
decoupled from the DB row shape (see ``EntryView``) so it can be exercised by the
offline review harness (``scripts/auto_order_sim.py``) against real archived nights.

The design rationale, the constraints/goals it encodes, and the data that motivated
the defaults live in ``docs/archive/2026-07-16-auto-order-rotation-plan.md``.

High level:

* Entries still **being made** (``being_made``) are pinned to the very bottom of
  the queue in their existing relative order and held out of the weave entirely —
  the KJ flips them back to "Waiting" once the track is ready. Everything below
  reasons only about the remaining (woven) entries.
* Rows 1..3 (``always_lock``) are SACRED — never moved.
* Rows 4-5 are frozen too, EXCEPT a duplicate of a singer already sitting in rows 1-3
  may be bumped out of the top five (a singer shouldn't occupy two of the first five
  slots). Rows 6+ are freely reordered (with a mild stickiness bias to stay put).
* The rest is a **scored greedy weave** filling one slot at a time. Each candidate is
  scored on: fairness (fewest PROJECTED songs-sung first), a new-singer boost (first
  song only), projected wait pressure + an overdue escalation (both RESET when a singer
  sings), spacing (never the same singer — by individual member — twice in a row, and a
  strong preference for >= ``spread_target`` between their songs), and mild stickiness.

Two ideas do most of the work and are applied to BOTH fairness and wait:
  * **project forward** — a singer's 2nd queued song is judged as if they've already
    sung their 1st (less urgent), using real linked-file durations (+turnaround) to
    estimate when each slot actually happens; and
  * **reset on placement** — a singer's wait resets the moment they're scheduled, so
    their later songs don't keep a stale "waited 2 hours" boost that buries new singers.

The full rationale, every weight, and the review-feedback history live in
``docs/archive/2026-07-16-auto-order-rotation-plan.md``. Validate changes with the
offline harness (``scripts/auto_order_review.py``) before shipping.
"""

from dataclasses import dataclass, field
from typing import Optional


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

@dataclass
class AutoOrderConfig:
    """Tunable knobs. Every weight is exposed so we can iterate on real output."""

    # --- Hard/soft region boundaries (1-indexed row counts) ---
    always_lock: int = 3        # rows 1..3 are sacred — NEVER moved.
    lock_head: int = 5          # rows 1..5 frozen by default, BUT a row 4/5 entry may
                                # be bumped DOWN if that same singer already appears in
                                # rows 1..3 (a singer shouldn't sit twice inside 1-5).

    # --- Spacing ---
    # We want a genuinely different, under-served singer between repeats whenever
    # possible — not just the bare "no back-to-back" minimum. ``spread_target`` is
    # how many slots of separation we actively push for (repeats inside this window
    # are strongly penalised; the penalty ramps down toward the edge).
    spread_target: int = 5

    # --- Wait pressure ---
    max_wait_minutes: int = 60  # waiting longer than this is "unreasonable"
    avg_song_minutes: float = 4.0    # fallback song length for UNLINKED entries only
    turnaround_minutes: float = 0.5  # gap between songs (setup / mic handoff)
    overdue_step_minutes: int = 30   # each step past max_wait adds w_overdue
    overdue_max_steps: float = 3.0   # cap the escalation

    # --- Scoring weights (higher = more influence) ---
    w_fair: float = 160.0       # fewer songs sung -> earlier
    w_wait: float = 60.0        # longer wait -> earlier
    w_stick: float = 18.0       # protected/other rows prefer to stay near home
    w_back_to_back: float = 100000.0  # ~hard: never the same singer twice in a row
    w_bump_top5: float = 6000.0  # strong (but below the back-to-back veto): a bumped
                                # row-4/5 duplicate should leave the top five — UNLESS
                                # the only alternative is a back-to-back.
    w_spacing: float = 300.0    # strong: prefer a distinct under-served singer over
                                # repeating someone within ``spread_target`` slots
    w_new_promote: float = 400.0  # extra boost for a brand-new (0-sung) singer
    w_overdue: float = 450.0    # per overdue step: a singer waiting well past the
                                # max can leapfrog even new singers for ONE song
                                # (then their wait resets and they drop back)
    w_priority_bias: float = 8000.0  # KJ manual bump up/down (per-entry priority_bias
                                # of +1/-1). Deliberately dominates every SOFT term above
                                # (fairness 160, wait <=96, overdue <=1350, new-singer 400,
                                # bump-top5 6000) so a bump wins — but stays far below the
                                # back-to-back veto (100000), so it can never force the same
                                # singer twice in a row. Does not override the locked head or
                                # the "being made" pin (both decided before scoring).

    # Wait pressure keeps growing past max_wait_minutes (up to this multiple) so an
    # egregious 2.5-hour wait outranks a merely-long one instead of both capping out.
    wait_overflow_cap: float = 1.6

    # Stickiness for a brand-new singer is scaled by this (they should move freely up).
    new_singer_stick_factor: float = 0.0


# ---------------------------------------------------------------------------
# Entry contract
# ---------------------------------------------------------------------------

@dataclass
class EntryView:
    """Decoupled view of a rotation row that the algorithm reasons about.

    The web layer builds these from decorated rotation entries; the harness builds
    them from archived rows. Only the fields below matter to the algorithm.
    """

    id: int
    owner: str                       # submission-stream identity (lowercased entry string)
    sung: int = 0                    # songs already done tonight (fairness tier)
    wait_minutes: Optional[int] = None  # since last sang, else since first entered; None = ∞
    seq: int = 0                     # per-owner submission sequence (0 = earliest queued)
    orig_index: int = 0              # current display index (0-based)
    singer: str = ""                 # display name (for rationale/rendering only)
    song_artist: str = ""            # display song (for rationale/rendering only)
    members: tuple = ()              # individual singers in this entry (lowercased) — used
                                     # for spacing so a solo + a duet that share a person
                                     # (e.g. "Tara" then "Anya & Tara") don't go back-to-back.
    duration: Optional[float] = None  # linked karaoke file length in SECONDS (for
                                     # projecting real wait times; None -> avg fallback).
    being_made: bool = False         # track is still being generated ("Being Made") —
                                     # pinned to the very bottom and held out of the
                                     # fair weave until the KJ flips it back to Waiting.
    priority_bias: int = 0           # KJ manual bump: +1 up, -1 down, 0 normal. Adds a
                                     # strong scoring term so the weave leans this entry
                                     # earlier/later (never overriding the back-to-back veto).

    def __post_init__(self):
        if not self.members:
            self.members = (self.owner,)


@dataclass
class Placement:
    """Result row: an entry in its new slot with a human-readable reason."""

    entry: EntryView
    new_index: int
    reason: str = ""
    moved: bool = False


@dataclass
class AutoOrderResult:
    order: list = field(default_factory=list)          # list[EntryView] in new order
    placements: list = field(default_factory=list)     # list[Placement]
    metrics_before: dict = field(default_factory=dict)
    metrics_after: dict = field(default_factory=dict)

    @property
    def ordered_ids(self):
        return [e.id for e in self.order]

    @property
    def changed(self):
        return any(p.moved for p in self.placements)


# ---------------------------------------------------------------------------
# Public helper: build EntryViews from decorated rotation entries
# ---------------------------------------------------------------------------

def build_entry_views(entries):
    """Convert decorated rotation entries (dicts) into ``EntryView`` objects.

    ``entries`` is the visible queue in display order (as returned by
    ``rotation.get_rotation()`` + ``_decorate_rotation_entries``). We derive:

    * ``owner`` — lowercased ``singer`` display string (the group is one unit).
    * ``sung`` — ``songs_sung`` decorator field.
    * ``wait_minutes`` — ``wait_minutes`` decorator field.
    * ``seq`` — per-owner submission rank, from the entry's display order (which
      already reflects submission order for a given singer's own songs — their
      relative order in the queue is their submission order).
    """
    import json as _json
    per_owner_count = {}
    views = []
    for idx, e in enumerate(entries):
        owner = (e.get("singer") or "").strip().lower()
        # Individual members: prefer singers_json, else split the display string on "&".
        members = None
        sj = e.get("singers_json")
        if sj:
            try:
                parsed = _json.loads(sj)
                if isinstance(parsed, list) and parsed:
                    members = tuple(str(m).strip().lower() for m in parsed)
            except (ValueError, TypeError):
                members = None
        if members is None:
            members = tuple(p.strip().lower() for p in owner.split("&") if p.strip()) or (owner,)
        seq = per_owner_count.get(owner, 0)
        per_owner_count[owner] = seq + 1
        # "Being Made (!)" status → pin to the bottom until the KJ flips it back.
        being_made = "being made" in (e.get("status") or "").strip().lower()
        views.append(EntryView(
            id=e.get("id"),
            owner=owner,
            sung=int(e.get("songs_sung") or 0),
            wait_minutes=e.get("wait_minutes"),
            seq=seq,
            orig_index=idx,
            singer=e.get("singer") or "",
            song_artist=e.get("song_artist") or "",
            members=members,
            duration=e.get("duration"),
            being_made=being_made,
            priority_bias=int(e.get("priority_bias") or 0),
        ))
    return views


def _slot_minutes(e, config):
    """How long this entry occupies the stage: linked song length (avg fallback for
    unlinked entries) plus the between-songs turnaround gap."""
    d = getattr(e, "duration", None)
    song = (d / 60.0) if (d and d > 0) else config.avg_song_minutes
    return song + config.turnaround_minutes


# ---------------------------------------------------------------------------
# Core algorithm
# ---------------------------------------------------------------------------

def compute_auto_order(entries, config=None):
    """Compute a fair reordering of the visible rotation queue.

    ``entries`` is a list of :class:`EntryView` in current display order.
    Returns an :class:`AutoOrderResult`.

    Entries still ``being_made`` are pinned to the very bottom (in their existing
    relative order) and held out of the weave entirely; only the remaining entries
    are fairly reordered above them.
    """
    config = config or AutoOrderConfig()
    if len(entries) == 0:
        return AutoOrderResult(order=[], placements=[])

    # Split off "being made" entries — they always sink to the bottom, untouched by
    # the fair weave, until the KJ flips their status back to Waiting.
    making = [e for e in entries if e.being_made]
    woven_input = [e for e in entries if not e.being_made] if making else entries

    locked_idx = _locked_indices(woven_input, config)
    result_order = _weave(woven_input, locked_idx, config) + making

    # Build placements + rationale. Making entries were never woven, so their reason
    # reflects the pin, not the locked head.
    placements = []
    for new_idx, e in enumerate(result_order):
        moved = (new_idx != e.orig_index)
        if e.being_made:
            reason = "held at bottom (being made)"
        else:
            reason = _reason_for(e, new_idx, config, locked_region=(new_idx in locked_idx))
        placements.append(Placement(entry=e, new_index=new_idx, moved=moved, reason=reason))

    return AutoOrderResult(
        order=result_order,
        placements=placements,
        metrics_before=compute_metrics(entries, config),
        metrics_after=compute_metrics(result_order, config),
    )


def _weave(entries, locked_idx, config):
    """Scored greedy weave of ``entries`` (list[EntryView]) → new ordered list.

    Rows are positional in the passed list; ``locked_idx`` (from ``_locked_indices``)
    are frozen in place and everything else fills the open slots. Callers that pin
    some entries out of the weave (e.g. "being made") pass only the woven entries.
    """
    n = len(entries)
    if n == 0:
        return []

    # Place locked entries at their fixed positions; the rest are the pool that
    # fills the remaining "open" slots (ascending).
    result = [None] * n
    for i in locked_idx:
        result[i] = entries[i]
    pool = [entries[i] for i in range(n) if i not in locked_idx]
    open_slots = [i for i in range(n) if i not in locked_idx]

    # Per-owner remaining-song queues, in submission (seq) order. Only the head
    # of each owner's queue is eligible so we never reorder a singer's own songs.
    owner_queues = {}
    for e in pool:
        owner_queues.setdefault(e.owner, []).append(e)
    for q in owner_queues.values():
        q.sort(key=lambda e: (e.seq, e.orig_index))
    remaining = {owner: list(q) for owner, q in owner_queues.items()}

    # Spacing memory: for each individual member, the set of slot indices they are
    # placed at — seeded with the FROZEN entries (both before and after an open
    # slot) so we space relative to the locked head too (a row-6 pick shouldn't
    # repeat a singer sitting at row 3). Distances are absolute (nearest in either
    # direction), so a future locked placement blocks a back-to-back before it too.
    member_slots = {}
    for i in locked_idx:
        for m in entries[i].members:
            member_slots.setdefault(m, set()).add(i)

    # A bumped row-4/5 entry (a duplicate of a singer already in rows 1-3) should
    # leave the top five entirely. This is a strong scoring penalty (not a hard
    # exclusion) so that avoiding a back-to-back always wins over it — otherwise
    # bumping a duplicate down could force a spacing-violating singer up into the
    # slot it vacated. Identified by WOVEN position (not orig_index): "being made"
    # entries pinned out of the weave shift the head, so an entry originally below
    # row 5 can land in woven row 4/5 and must be eligible to bump.
    bumped_ids = {id(entries[i]) for i in range(min(config.lock_head, n))
                  if i not in locked_idx}

    # Walk every position in order; locked ones are already placed (just accumulate
    # their real duration), open ones get filled. ``time_ahead`` = minutes of songs
    # before the current position, using linked-file durations (avg fallback) — the
    # real time a singer placed here will have to wait before they sing.
    # member_last_time[m] = the minutes-from-now at which member m most recently
    # sings (their wait RESETS then). Built as we walk the queue in order, so a
    # singer's 2nd/3rd song is judged on the gap since their previous song — not on
    # the stale "time since they last sang before this queue". Without this, a singer
    # who last sang 100 min ago keeps a huge overdue boost on ALL their queued songs
    # and sings repeatedly ahead of a brand-new singer.
    member_last_time = {}
    open_set = set(open_slots)
    time_ahead = 0.0
    for pos in range(n):
        if pos not in open_set:
            e = result[pos]
            for m in e.members:
                member_last_time[m] = time_ahead
            time_ahead += _slot_minutes(e, config)
            continue
        candidates = [q[0] for q in remaining.values() if q]
        best = _pick_best(candidates, pos, member_slots, bumped_ids,
                          time_ahead, member_last_time, config)
        remaining[best.owner].pop(0)
        result[pos] = best
        for m in best.members:
            member_slots.setdefault(m, set()).add(pos)
            member_last_time[m] = time_ahead
        time_ahead += _slot_minutes(best, config)

    return result


def _locked_indices(entries, config):
    """Return the set of indices that stay fixed.

    Rows 1..``always_lock`` (indices 0..always_lock-1) are always frozen. A row in
    [always_lock, lock_head) (i.e. rows 4-5) is ALSO frozen unless one of its
    singers already appears in rows 1..always_lock — in that case the singer would
    otherwise sit twice inside the top five, so we free that later entry to be
    bumped down.
    """
    n = len(entries)
    always = max(0, min(config.always_lock, n))
    head_end = max(always, min(config.lock_head, n))

    early_members = set()
    for i in range(always):
        early_members.update(entries[i].members)

    locked = set(range(always))
    for i in range(always, head_end):
        if not (set(entries[i].members) & early_members):
            locked.add(i)
    return locked


def _pick_best(candidates, slot, member_slots, bumped_ids, time_ahead, member_last_time, config):
    """Return the highest-scoring eligible candidate for ``slot``.

    Back-to-back (any shared member at distance 1) carries a near-infinite penalty,
    so the same singer is never placed twice in a row unless they are literally the
    only option. A strong ramped penalty for repeats within ``spread_target`` slots
    makes the weave prefer a genuinely different, under-served singer over repeating
    someone too soon — matching how the KJ fills the early queue by hand. When a
    burst-singer's later songs are all that's left, they cluster at the tail (which
    is what the KJ wants: later requests will arrive to re-space them).
    """
    best = None
    best_score = None
    for e in candidates:
        score = _score(e, slot, member_slots, time_ahead, member_last_time, config)
        # A bumped duplicate placed back inside the top five is strongly penalised.
        if id(e) in bumped_ids and slot < config.lock_head:
            score -= config.w_bump_top5
        # Deterministic tiebreak: higher score, then earliest submission, then
        # lowest original index, then id — no randomness (keeps replays stable).
        key = (score, -e.seq, -e.orig_index, -(e.id or 0))
        if best_score is None or key > best_score:
            best_score = key
            best = e
    return best


def _nearest_member_distance(e, slot, member_slots):
    """Absolute distance from ``slot`` to the nearest slot any of ``e``'s members
    is already placed at (past OR future locked placement). ``None`` if never."""
    best = None
    for m in e.members:
        for i in member_slots.get(m, ()):  # empty when the member isn't placed yet
            d = abs(slot - i)
            if best is None or d < best:
                best = d
    return best


def _placed_before(e, slot, member_slots):
    """How many of this entry's singers' songs are scheduled strictly before
    ``slot`` (the max across members — the member who will have sung the most by
    then drives the projected fairness tier)."""
    most = 0
    for m in e.members:
        c = sum(1 for i in member_slots.get(m, ()) if i < slot)
        if c > most:
            most = c
    return most


def _projected_wait(e, time_ahead, member_last_time, config):
    """Minutes this singer will have waited when this slot comes up.

    If they've already sung earlier in the (re)ordered queue, the wait is measured
    from that song (it reset then); otherwise it's their elapsed wait now plus the
    time until this slot. Group entries use the member who sang most recently.
    """
    last_t = None
    for m in e.members:
        t = member_last_time.get(m)
        if t is not None and (last_t is None or t > last_t):
            last_t = t
    if last_t is None:
        elapsed = e.wait_minutes if e.wait_minutes is not None else 0
        return elapsed + time_ahead
    return time_ahead - last_t


def _score(e, slot, member_slots, time_ahead, member_last_time, config):
    """Score a candidate for a given slot. Higher = place sooner.

    ``time_ahead`` = real minutes of songs before this slot (linked durations +
    turnaround), used to project how long this singer will actually have waited.
    """
    # Fairness uses the PROJECTED sung-count: how many times this singer will have
    # sung by the time this slot comes up = their done-count now + the number of
    # their songs already scheduled before this slot (frozen head + earlier picks).
    # So a singer's 2nd queued song is judged as "sung once", their 3rd as "twice",
    # etc. — it defers to others who will still be more under-served at that point,
    # instead of every one of a burst-singer's songs staying maximally urgent.
    projected_sung = e.sung + _placed_before(e, slot, member_slots)
    fairness = config.w_fair * (1.0 / (1.0 + projected_sung))

    # New-singer promotion boost — applies only while the singer is still projected
    # to be brand new (i.e. none of their songs is scheduled ahead of this slot).
    # Getting a new singer INTO the queue is urgent; their later songs are ordinary.
    is_fresh_new = (projected_sung == 0)
    promote = config.w_new_promote if is_fresh_new else 0.0

    # Projected wait: how long this singer will have waited when this slot comes up,
    # RESETTING each time they sing — so a singer's 2nd/3rd queued song is judged on
    # the gap since their previous song, not on their stale pre-queue wait. Drives
    # both the mild wait pull and the strong overdue escalation.
    projected_wait = _projected_wait(e, time_ahead, member_last_time, config)

    # Wait pressure: longer projected wait -> higher (up to wait_overflow_cap).
    wait_frac = min(config.wait_overflow_cap,
                    max(0.0, projected_wait / float(config.max_wait_minutes)))
    wait = config.w_wait * wait_frac

    # Overdue escalation: once the projection blows past the max, add a strong,
    # capped boost that can override even the new-singer priority — so nobody is
    # left waiting well over an hour by a flood ahead of them. Self-limiting: once
    # they sing, their wait resets and this drops away.
    overdue = 0.0
    if projected_wait > config.max_wait_minutes:
        steps = (projected_wait - config.max_wait_minutes) / float(config.overdue_step_minutes)
        overdue = config.w_overdue * min(steps, config.overdue_max_steps)

    # Stickiness: prefer keeping an entry near its original slot. New singers get
    # ~0 stickiness so they float up freely.
    stick_factor = config.new_singer_stick_factor if e.sung == 0 else 1.0
    distance = abs(e.orig_index - slot)
    stick = -config.w_stick * stick_factor * (distance / 10.0)

    # Spacing: never the same member twice in a row (veto), and a strong ramped
    # preference for >= spread_target slots between one singer's songs.
    spacing = 0.0
    dist = _nearest_member_distance(e, slot, member_slots)
    if dist is not None:
        if dist <= 1:
            spacing = -config.w_back_to_back
        elif dist <= config.spread_target:
            # Ramp: closer repeats are worse; fades to 0 at spread_target.
            deficit = (config.spread_target + 1 - dist) / float(config.spread_target)
            spacing = -config.w_spacing * deficit

    # KJ manual bump: a strong, flat lean toward the front (+1) or back (-1). It
    # dominates every soft term above so the KJ's intent wins, but since it's well
    # below w_back_to_back, a bumped-up singer still yields rather than sing twice
    # in a row (the -w_back_to_back spacing term cancels it out).
    bias = config.w_priority_bias * e.priority_bias

    return fairness + promote + wait + overdue + stick + spacing + bias


def _reason_for(e, new_idx, config, locked_region):
    if locked_region:
        return "locked (rows 1-{})".format(config.lock_head)
    if new_idx == e.orig_index:
        return "held"
    if e.sung == 0 and new_idx < e.orig_index:
        return "promoted: new singer"
    if new_idx < e.orig_index:
        return "moved up (fairness)"
    return "moved down (spread / fairness)"


# ---------------------------------------------------------------------------
# Metrics (for the harness + preview)
# ---------------------------------------------------------------------------

def compute_metrics(order, config=None):
    """Summarise how fair/spread an ordering is. ``order`` = list[EntryView]."""
    config = config or AutoOrderConfig()
    n = len(order)

    # Spacing, counted per individual member so a solo + duet sharing a person
    # register as a repeat. Gap = slots since that member was last seen.
    back_to_back = 0
    close_repeats = 0            # repeats closer than the spread target
    last_seen = {}
    gaps = []
    for i, e in enumerate(order):
        nearest = None
        for m in e.members:
            if m in last_seen:
                g = i - last_seen[m]
                nearest = g if nearest is None else min(nearest, g)
        if nearest is not None:
            gaps.append(nearest)
            if nearest == 1:
                back_to_back += 1
            if nearest < config.spread_target:
                close_repeats += 1
        for m in e.members:
            last_seen[m] = i

    # Fairness inversions: a higher-sung singer placed before a lower-sung one
    # (ignoring same-owner pairs and the locked head where we can't move things).
    inversions = 0
    for i in range(n):
        for j in range(i + 1, n):
            if order[i].owner == order[j].owner:
                continue
            if order[i].sung > order[j].sung:
                inversions += 1

    waits = [e.wait_minutes for e in order if e.wait_minutes is not None]
    over_max_wait = sum(1 for w in waits if w > config.max_wait_minutes)

    # Projected wait: how long each singer will have waited BY THE TIME their song
    # comes up = elapsed now + (songs ahead of them) × avg song length. This is what
    # the reorder can actually improve — count how many are projected to exceed the
    # max, and the worst projection. (Elapsed-only over_max_wait can't be undone.)
    projected_over_hour = 0
    worst_projected = 0
    time_ahead = 0.0
    member_last_time = {}
    for e in order:
        pw = _projected_wait(e, time_ahead, member_last_time, config)
        if pw > config.max_wait_minutes:
            projected_over_hour += 1
        worst_projected = max(worst_projected, pw)
        for m in e.members:
            member_last_time[m] = time_ahead
        time_ahead += _slot_minutes(e, config)

    return {
        "count": n,
        "back_to_back": back_to_back,
        "close_repeats": close_repeats,
        "spread_target": config.spread_target,
        "fairness_inversions": inversions,
        "over_max_wait": over_max_wait,
        "projected_over_hour": projected_over_hour,
        "worst_projected_wait": int(worst_projected),
        "median_repeat_gap": (sorted(gaps)[len(gaps) // 2] if gaps else None),
    }
