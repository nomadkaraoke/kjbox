"""Explainer for Auto Order: curated scenarios run LIVE through the real algorithm.

Backs the KJ UI's "How Auto Order works" modal (System section). Nothing here is
a hand-drawn picture of the algorithm: every scenario is a small rotation that is
fed through :func:`auto_order.compute_auto_order` (with ``trace=True``) each time
the modal opens, so the before/after and the per-slot score breakdowns are always
exactly what the code does today.

Each scenario carries a plain-English story (situation / what happens / why) plus
machine-checkable ``checks`` that state the story's claims ("Nia moves up to row
6", "Kai is never twice in a row"). The checks run with the scenario and are
asserted in ``tests/unit/test_auto_order_explain.py``, so if someone retunes the
algorithm and a story stops being true, the tests fail and the modal flags that
example as out of date instead of silently lying.

Names are invented. Songs are just labels (each one unique within its scenario so
checks can refer to entries by song).
"""

from auto_order import AutoOrderConfig, EntryView, compute_auto_order


# ---------------------------------------------------------------------------
# Scenario building
# ---------------------------------------------------------------------------

def _entries(spec):
    """spec rows: (singer, sung_tonight, wait_minutes, song[, opts]).

    ``opts`` may set ``being_made=True``, ``bias=+1/-1`` (KJ bump) or
    ``minutes`` (song length; default = the 4-minute fallback).
    """
    per_owner = {}
    out = []
    for idx, row in enumerate(spec):
        singer, sung, wait, song = row[:4]
        opts = row[4] if len(row) > 4 else {}
        owner = singer.strip().lower()
        seq = per_owner.get(owner, 0)
        per_owner[owner] = seq + 1
        members = tuple(p.strip().lower() for p in owner.split("&") if p.strip()) or (owner,)
        minutes = opts.get("minutes")
        out.append(EntryView(
            id=idx + 1, owner=owner, sung=sung, wait_minutes=wait, seq=seq,
            orig_index=idx, singer=singer, song_artist=song, members=members,
            duration=(minutes * 60.0) if minutes else None,
            being_made=bool(opts.get("being_made")),
            priority_bias=int(opts.get("bias") or 0),
        ))
    return out


# Each scenario: id, title, rule (short tag shown as a chip), situation, outcome,
# why, spec, checks. Checks reference entries by SONG (unique per scenario).
#   ("row", song, n)            -> song ends at row n (1-indexed)
#   ("moved_up", song)          -> song ends higher than it started
#   ("before", song_a, song_b)  -> song_a ends ahead of song_b
#   ("same_rows", n)            -> rows 1..n are untouched
#   ("unchanged",)              -> nothing moves at all
#   ("no_back_to_back",)        -> no individual singer sings twice in a row
#   ("min_gap", singer, k)      -> that singer has >= k-1 other entries between songs
#   ("bottom", [songs])         -> these songs are the last rows, in this order
SCENARIOS = [
    {
        "id": "new-singer",
        "title": "A brand-new singer is stuck at the back",
        "rule": "New singers first",
        "situation": "Nia just put her name in for the first time tonight. She's at the bottom "
                     "(row 10), behind people who have already sung three or four times.",
        "outcome": "Nia jumps to row 6, the first slot Auto Order is allowed to change. Nobody "
                   "in rows 1-5 moves.",
        "why": "Someone who hasn't sung yet gets a big one-off boost. Getting a new person on stage is "
               "the most important thing for the room. Rows 1-5 stay put because those people "
               "already know they're about to go on.",
        "spec": [
            ("Marco", 3, 12, "Mr. Brightside"),
            ("Lena", 4, 8, "Jolene"),
            ("Gus", 3, 15, "Wonderwall"),
            ("Faye", 4, 10, "Valerie"),
            ("Omar", 3, 18, "Hey Ya!"),
            ("Marco", 3, 12, "Africa"),
            ("Lena", 4, 8, "Zombie"),
            ("Gus", 3, 15, "Creep"),
            ("Faye", 4, 10, "Torn"),
            ("Nia", 0, 25, "Dancing Queen"),
        ],
        "checks": [("same_rows", 5), ("row", "Dancing Queen", 6)],
    },
    {
        "id": "fewer-sung",
        "title": "Light singers ahead of heavy repeat singers",
        "rule": "Fewer songs first",
        "situation": "Two people who have sung once each (Kai and Rosa) are queued behind "
                     "regulars on their sixth or seventh song.",
        "outcome": "Rosa and Kai both move up into the first open slots, Rosa first because "
                   "she has waited longer. The heavy singers keep their relative order behind them.",
        "why": "Fairness counts how many songs each person has sung tonight. Fewer songs means a "
               "higher score: 1 song scores about three times as much as 6. Waiting time "
               "only nudges, so a heavy singer who has waited a little longer still goes after "
               "someone on their first or second turn.",
        "spec": [
            ("Wes", 7, 10, "Purple Rain"),
            ("Uma", 6, 14, "Rolling in the Deep"),
            ("Ivan", 7, 9, "Livin' on a Prayer"),
            ("Zoe", 6, 12, "Shallow"),
            ("Quinn", 7, 11, "Don't Stop Believin'"),
            ("Wes", 7, 10, "Kiss"),
            ("Uma", 6, 14, "Hello"),
            ("Kai", 1, 35, "Fly Me to the Moon"),
            ("Ivan", 7, 9, "Bed of Roses"),
            ("Rosa", 1, 40, "Crazy"),
            ("Zoe", 6, 12, "Always Remember Us"),
        ],
        "checks": [("same_rows", 5), ("row", "Crazy", 6),
                   ("row", "Fly Me to the Moon", 7)],
    },
    {
        "id": "burst",
        "title": "One singer submits four songs at once",
        "rule": "Everyone gets a turn first",
        "situation": "Kai queued four songs together (rows 6-9). Left alone, Kai would sing four "
                     "times in a row while everyone below waits.",
        "outcome": "Kai's first song keeps its spot. The other three go behind everyone else's "
                   "next turn, in the order Kai asked for. Nobody else has more songs queued, so "
                   "for now they sit together at the bottom.",
        "why": "Auto Order looks ahead. Kai's 2nd song is scored as if Kai has already sung "
               "the 1st, the 3rd as if Kai has sung twice, and so on, so each one loses to anyone "
               "who will have had fewer turns by then. It works like taking turns: one song per "
               "person per lap. Leaving the extras together at the bottom is deliberate, because "
               "the top of the queue is what matters right now. As new requests come in, Auto "
               "Order fits them in between Kai's songs.",
        "spec": [
            ("Lena", 2, 8, "Jolene"),
            ("Marco", 2, 20, "Mr. Brightside"),
            ("Gus", 2, 12, "Wonderwall"),
            ("Faye", 2, 6, "Valerie"),
            ("Omar", 2, 30, "Hey Ya!"),
            ("Kai", 2, 15, "Fly Me to the Moon"),
            ("Kai", 2, 15, "Feeling Good"),
            ("Kai", 2, 15, "Sway"),
            ("Kai", 2, 15, "Beyond the Sea"),
            ("Rosa", 2, 16, "Crazy"),
            ("Ivan", 2, 18, "Bed of Roses"),
            ("Zoe", 2, 22, "Shallow"),
            ("Uma", 2, 14, "Hello"),
        ],
        "checks": [("row", "Fly Me to the Moon", 6),
                   ("bottom", ["Feeling Good", "Sway", "Beyond the Sea"])],
    },
    {
        "id": "back-to-back",
        "title": "Same singer twice in a row",
        "rule": "Never twice in a row",
        "situation": "Dev is at row 5 (frozen) and Dev's next song landed straight after it at "
                     "row 6.",
        "outcome": "Dev's second song is moved several places down, so other singers go "
                   "between Dev's two turns.",
        "why": "Singing twice in a row is the one spacing rule that's effectively absolute: its "
               "penalty is far bigger than everything else combined. Beyond that, Auto Order "
               "aims for about 5 other singers between someone's songs. A repeat sooner than "
               "that is penalised, more heavily the closer it is. Spacing also counts the "
               "frozen rows, so a row-6 pick is kept away from someone at row 5.",
        "spec": [
            ("Marco", 3, 8, "Mr. Brightside"),
            ("Lena", 2, 20, "Jolene"),
            ("Gus", 3, 6, "Wonderwall"),
            ("Faye", 3, 30, "Valerie"),
            ("Dev", 2, 12, "Uptown Funk"),
            ("Dev", 2, 12, "Kiss"),
            ("Rosa", 2, 22, "Crazy"),
            ("Ivan", 2, 18, "Bed of Roses"),
            ("Zoe", 3, 16, "Shallow"),
            ("Uma", 3, 10, "Hello"),
        ],
        "checks": [("same_rows", 5), ("no_back_to_back",), ("min_gap", "Dev", 4)],
    },
    {
        "id": "new-singer-many-songs",
        "title": "A new singer queues three songs",
        "rule": "New-singer boost is once",
        "situation": "Pip is new tonight and queued three songs. Everyone else has sung once.",
        "outcome": "Pip's first song moves up to row 6. The second and third move to the end "
                   "of the queue, behind everyone else's turns. Nobody is left to put between them, "
                   "so for now they sit together at the bottom.",
        "why": "Auto Order looks ahead. When it reaches Pip's second song it scores Pip as "
               "someone who has already sung once (the first song is already scheduled), and the "
               "third as someone who has sung twice. The new-singer boost only applies to a "
               "first song, so Pip gets a fast start but doesn't take over the queue. Leaving "
               "extra songs together at the bottom is deliberate. The top of the queue is what "
               "matters right now, and as new requests come in Auto Order fits them in between "
               "Pip's songs.",
        "spec": [
            ("Marco", 1, 10, "Mr. Brightside"),
            ("Lena", 1, 12, "Jolene"),
            ("Gus", 1, 9, "Wonderwall"),
            ("Faye", 1, 14, "Valerie"),
            ("Omar", 1, 11, "Hey Ya!"),
            ("Uma", 1, 13, "Hello"),
            ("Wes", 1, 15, "Purple Rain"),
            ("Pip", 0, 5, "Toxic"),
            ("Pip", 0, 5, "Umbrella"),
            ("Pip", 0, 5, "Mamma Mia"),
            ("Zoe", 1, 16, "Shallow"),
            ("Ivan", 1, 17, "Bed of Roses"),
        ],
        "checks": [("row", "Toxic", 6), ("before", "Shallow", "Umbrella"),
                   ("before", "Bed of Roses", "Umbrella"),
                   ("bottom", ["Umbrella", "Mamma Mia"])],
    },
    {
        "id": "top-five-duplicate",
        "title": "Same singer twice in the top five",
        "rule": "One spot in the top 5",
        "situation": "Gus is at row 2 and also at row 4. Rows 4-5 are normally frozen, but "
                     "nobody should have two of the next five turns.",
        "outcome": "Gus's second song (row 4) is moved out of the top five. Everyone in rows "
                   "1-3 stays exactly where they are.",
        "why": "Rows 1-3 are never touched. Rows 4-5 are frozen too, with one exception: a "
               "second copy of someone who's already in rows 1-3. That copy gets bumped down so "
               "the next few turns go to different people. If bumping it would put someone "
               "twice in a row, the bump gives way.",
        "spec": [
            ("Marco", 2, 10, "Mr. Brightside"),
            ("Gus", 2, 14, "Wonderwall"),
            ("Lena", 2, 9, "Jolene"),
            ("Gus", 2, 14, "Creep"),
            ("Faye", 2, 11, "Valerie"),
            ("Omar", 2, 20, "Hey Ya!"),
            ("Uma", 2, 18, "Hello"),
            ("Zoe", 2, 16, "Shallow"),
            ("Ivan", 2, 22, "Bed of Roses"),
        ],
        "checks": [("same_rows", 3), ("row", "Valerie", 5), ("min_gap", "Gus", 4)],
    },
    {
        "id": "duet",
        "title": "Duets count for each person in them",
        "rule": "Spacing is per person",
        "situation": "Hana is singing solo at row 6, and \"Hana & Dev\" (a duet) sits right "
                     "after it at row 7.",
        "outcome": "The duet is pushed further down so Hana gets a few other singers between her "
                   "solo and the duet.",
        "why": "Spacing is worked out for each person, not each name on the list. \"Hana\" "
               "followed by \"Hana & Dev\" is Hana singing twice in a row, so it's treated "
               "exactly like a back-to-back.",
        "spec": [
            ("Marco", 2, 10, "Mr. Brightside"),
            ("Lena", 2, 9, "Jolene"),
            ("Gus", 2, 12, "Wonderwall"),
            ("Faye", 2, 11, "Valerie"),
            ("Omar", 2, 13, "Hey Ya!"),
            ("Hana", 1, 25, "Someone Like You"),
            ("Hana & Dev", 1, 25, "Islands in the Stream"),
            ("Uma", 2, 14, "Hello"),
            ("Zoe", 2, 16, "Shallow"),
            ("Ivan", 2, 18, "Bed of Roses"),
            ("Wes", 2, 15, "Purple Rain"),
        ],
        "checks": [("no_back_to_back",), ("min_gap", "Hana", 3)],
    },
    {
        "id": "long-wait",
        "title": "Someone has waited well over an hour",
        "rule": "Nobody waits forever",
        "situation": "Sam last sang 95 minutes ago. Everyone else has sung recently, including a "
                     "brand-new singer, Cleo.",
        "outcome": "Sam moves to the front of the open slots, ahead of even the new singer.",
        "why": "Once someone's wait (counting the songs still ahead of them) goes past 60 "
               "minutes, an \"overdue\" boost builds every 30 minutes. It can grow big enough to "
               "beat the new-singer boost, so nobody gets buried by a flood of new names. It "
               "only lasts for one song: once Sam is scheduled the wait clock resets, so Sam's "
               "next song is ordinary again.",
        "spec": [
            ("Marco", 3, 8, "Mr. Brightside"),
            ("Lena", 3, 6, "Jolene"),
            ("Gus", 3, 10, "Wonderwall"),
            ("Faye", 3, 5, "Valerie"),
            ("Omar", 3, 9, "Hey Ya!"),
            ("Uma", 3, 7, "Hello"),
            ("Zoe", 3, 11, "Shallow"),
            ("Cleo", 0, 10, "Valerie (Live)"),
            ("Ivan", 3, 12, "Bed of Roses"),
            ("Sam", 2, 95, "Sweet Caroline"),
        ],
        "checks": [("row", "Sweet Caroline", 6), ("before", "Sweet Caroline", "Valerie (Live)")],
    },
    {
        "id": "moderate-wait",
        "title": "A moderate wait doesn't beat a new singer",
        "rule": "New singers first",
        "situation": "Sam has waited 35 minutes, which is a while but not unreasonable. Cleo is "
                     "new tonight.",
        "outcome": "Cleo goes first and Sam comes right after.",
        "why": "Below the 60-minute line, waiting time only gives a mild nudge. It decides "
               "between two otherwise-similar singers but doesn't beat \"hasn't sung yet\". The "
               "strong overdue boost only starts once a wait is actually too long.",
        "spec": [
            ("Marco", 3, 8, "Mr. Brightside"),
            ("Lena", 3, 6, "Jolene"),
            ("Gus", 3, 10, "Wonderwall"),
            ("Faye", 3, 5, "Valerie"),
            ("Omar", 3, 9, "Hey Ya!"),
            ("Uma", 3, 7, "Hello"),
            ("Zoe", 3, 11, "Shallow"),
            ("Sam", 2, 35, "Sweet Caroline"),
            ("Ivan", 3, 12, "Bed of Roses"),
            ("Cleo", 0, 10, "Valerie (Live)"),
        ],
        "checks": [("row", "Valerie (Live)", 6), ("row", "Sweet Caroline", 7)],
    },
    {
        "id": "being-made",
        "title": "A track that's still being made",
        "rule": "Being Made sinks",
        "situation": "Jo asked for a song we don't have yet, so it's still being generated "
                     "(status \"Being Made\"). It's sitting at row 2.",
        "outcome": "Jo's entry drops to the very bottom, even from inside the normally untouchable "
                   "top three. Everyone else is ordered as usual above it.",
        "why": "A track that isn't ready can't be played, so it would only get in the way. "
               "Being Made entries are kept at the bottom, in the order they were added, until "
               "the KJ switches them back to Waiting. After that they're ordered fairly like "
               "everyone else.",
        "spec": [
            ("Marco", 2, 10, "Mr. Brightside"),
            ("Jo", 0, 15, "Brand New Song", {"being_made": True}),
            ("Lena", 2, 9, "Jolene"),
            ("Gus", 2, 12, "Wonderwall"),
            ("Faye", 2, 11, "Valerie"),
            ("Omar", 2, 13, "Hey Ya!"),
            ("Uma", 2, 14, "Hello"),
            ("Zoe", 2, 16, "Shallow"),
        ],
        "checks": [("bottom", ["Brand New Song"]), ("row", "Mr. Brightside", 1)],
    },
    {
        "id": "kj-bump",
        "title": "The KJ bumps someone up (or a $20+ tip)",
        "rule": "KJ bump wins",
        "situation": "The KJ tapped \"bump up\" on Wes, who has sung five times (the same thing "
                     "happens automatically when a tip at or over the threshold is confirmed). "
                     "Wes is at row 9.",
        "outcome": "Wes goes to row 6, ahead of the lighter singers. A bump still can't put "
                   "anyone twice in a row or move rows 1-3.",
        "why": "A bump (+1) or a bump-down (-1) is deliberately stronger than everything else "
               "Auto Order weighs (fairness, waiting, new singers), so the KJ always gets the "
               "final say. The only things stronger are the hard rules: rows 1-3 are "
               "never moved, Being Made stays at the bottom, and never the same person twice in "
               "a row.",
        "spec": [
            ("Marco", 2, 10, "Mr. Brightside"),
            ("Lena", 2, 9, "Jolene"),
            ("Gus", 2, 12, "Wonderwall"),
            ("Faye", 2, 11, "Valerie"),
            ("Omar", 2, 13, "Hey Ya!"),
            ("Uma", 1, 14, "Hello"),
            ("Zoe", 1, 16, "Shallow"),
            ("Ivan", 1, 18, "Bed of Roses"),
            ("Wes", 5, 6, "Purple Rain", {"bias": 1}),
            ("Rosa", 1, 20, "Crazy"),
        ],
        "checks": [("same_rows", 5), ("row", "Purple Rain", 6)],
    },
    {
        "id": "already-fair",
        "title": "A queue that's already fair",
        "rule": "Don't shuffle for nothing",
        "situation": "Everyone has sung the same amount, nobody is twice in a row, and people "
                     "who have waited longer are already nearer the front.",
        "outcome": "Nothing moves. Pressing Reorder does nothing and doesn't even add an Undo "
                   "step.",
        "why": "Each entry gets a small \"stay where you are\" preference, so Auto Order only "
               "moves people when there's a real reason to. People don't like seeing their spot "
               "jump around for no visible reason. (When two people have sung the same amount, "
               "the one who has waited longer can still move ahead, so a queue sorted by "
               "waiting time is the one that stays put.)",
        "spec": [
            ("Marco", 2, 40, "Mr. Brightside"),
            ("Lena", 2, 36, "Jolene"),
            ("Gus", 2, 31, "Wonderwall"),
            ("Faye", 2, 27, "Valerie"),
            ("Omar", 2, 22, "Hey Ya!"),
            ("Uma", 2, 18, "Hello"),
            ("Zoe", 2, 13, "Shallow"),
            ("Ivan", 2, 9, "Bed of Roses"),
        ],
        "checks": [("unchanged",)],
    },
]


# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------

def _row_of(order, song):
    for i, e in enumerate(order):
        if e.song_artist == song:
            return i + 1
    raise KeyError(song)


def _orig_row_of(entries, song):
    for e in entries:
        if e.song_artist == song:
            return e.orig_index + 1
    raise KeyError(song)


def _member_positions(order, singer):
    m = singer.strip().lower()
    return [i for i, e in enumerate(order) if m in e.members]


def describe_check(check):
    kind = check[0]
    if kind == "row":
        return f"“{check[1]}” ends up at row {check[2]}"
    if kind == "moved_up":
        return f"“{check[1]}” moves up"
    if kind == "before":
        return f"“{check[1]}” is ahead of “{check[2]}”"
    if kind == "same_rows":
        return f"Rows 1-{check[1]} are untouched"
    if kind == "unchanged":
        return "Nothing moves"
    if kind == "no_back_to_back":
        return "Nobody sings twice in a row"
    if kind == "min_gap":
        return f"{check[1]} has at least {check[2] - 1} other singers between songs"
    if kind == "bottom":
        return "Ends with " + ", ".join(f"“{s}”" for s in check[1])
    raise ValueError(f"unknown check {kind!r}")


def run_check(check, before, order):
    """Evaluate one claim against the real result. Returns bool."""
    kind = check[0]
    if kind == "row":
        return _row_of(order, check[1]) == check[2]
    if kind == "moved_up":
        return _row_of(order, check[1]) < _orig_row_of(before, check[1])
    if kind == "before":
        return _row_of(order, check[1]) < _row_of(order, check[2])
    if kind == "same_rows":
        n = check[1]
        return [e.id for e in order[:n]] == [e.id for e in before[:n]]
    if kind == "unchanged":
        return [e.id for e in order] == [e.id for e in before]
    if kind == "no_back_to_back":
        return all(not (set(a.members) & set(b.members)) for a, b in zip(order, order[1:]))
    if kind == "min_gap":
        pos = _member_positions(order, check[1])
        return all(b - a >= check[2] for a, b in zip(pos, pos[1:]))
    if kind == "bottom":
        tail = [e.song_artist for e in order[-len(check[1]):]]
        return tail == list(check[1])
    raise ValueError(f"unknown check {kind!r}")


# ---------------------------------------------------------------------------
# Serialisation for the UI
# ---------------------------------------------------------------------------

def _row_dict(e, new_index=None):
    d = {
        "id": e.id,
        "singer": e.singer,
        "song": e.song_artist,
        "sung": e.sung,
        "wait_minutes": e.wait_minutes,
        "being_made": e.being_made,
        "priority_bias": e.priority_bias,
        "orig_row": e.orig_index + 1,
    }
    if new_index is not None:
        d["row"] = new_index + 1
        d["delta"] = (e.orig_index - new_index)   # +N = moved up N rows
    return d


def explain_entries(entries, config=None):
    """Run ``entries`` through Auto Order with tracing; return a JSON-ready dict
    with before/after rows and the per-slot decisions."""
    config = config or AutoOrderConfig()
    res = compute_auto_order(entries, config, trace=True)
    return {
        "before": [_row_dict(e) for e in entries],
        "after": [_row_dict(p.entry, p.new_index) | {"reason": p.reason, "moved": p.moved}
                  for p in res.placements],
        "decisions": res.trace,
        "changed": res.changed,
        "metrics_before": res.metrics_before,
        "metrics_after": res.metrics_after,
    }, res


def build_scenario(scn, config=None):
    entries = _entries(scn["spec"])
    payload, res = explain_entries(entries, config)
    checks = [{"text": describe_check(c), "ok": run_check(c, entries, res.order)}
              for c in scn["checks"]]
    return {
        "id": scn["id"], "title": scn["title"], "rule": scn["rule"],
        "situation": scn["situation"], "outcome": scn["outcome"], "why": scn["why"],
        "checks": checks, "all_ok": all(c["ok"] for c in checks),
        **payload,
    }


def config_summary(config=None):
    """The knobs the explainer quotes, so the copy always shows live values."""
    c = config or AutoOrderConfig()
    return {
        "always_lock": c.always_lock, "lock_head": c.lock_head,
        "spread_target": c.spread_target, "max_wait_minutes": c.max_wait_minutes,
        "overdue_step_minutes": c.overdue_step_minutes,
        "overdue_max_steps": c.overdue_max_steps,
        "avg_song_minutes": c.avg_song_minutes, "turnaround_minutes": c.turnaround_minutes,
        "weights": {
            "fairness": c.w_fair, "new_singer": c.w_new_promote, "wait": c.w_wait,
            "wait_overflow_cap": c.wait_overflow_cap, "overdue": c.w_overdue,
            "spacing": c.w_spacing, "stick": c.w_stick, "bump_top5": c.w_bump_top5,
            "priority": c.w_priority_bias, "back_to_back": c.w_back_to_back,
        },
    }


def build_explainer(config=None):
    config = config or AutoOrderConfig()
    return {
        "config": config_summary(config),
        "scenarios": [build_scenario(s, config) for s in SCENARIOS],
    }
