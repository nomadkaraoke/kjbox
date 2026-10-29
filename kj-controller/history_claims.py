"""History claims: a singer claims the songs they sang at past Nomad nights
(Phase 4c, spec 6.10).

Matching is deliberately loose (Andrew: "people often use variations of their
name, sometimes with or without a last initial") but never automatic: a box
name matches an account when its folded FIRST word equals the first word of
any name the account goes by (R35). The songs are shown before anything is
claimed, and only an explicit claim attributes an entry.

- ``first_word`` folds with ``singer_names.fold_name`` and needs at least
  ``MIN_FIRST_WORD`` characters, so "W" or "J. Smith" never match anyone.
- ``entry_matches`` checks each member of an entry (a duet matches on any
  listed singer).
- ``account_names`` collects the names an account goes by: the gen profile
  display name (no email-derived fallback), the names its devices used on
  their own requests on any night, and those devices' aliases (KJ renames and
  merges included).
"""

import datetime as dt
import logging

from history_ids import (
    DEFAULT_BOX_ID,
    ENTRY_IDS_UNIQUE_SINCE,
    night_id_for_marker,
)
from history_outbox import _iso_z, _local_to_utc
from singer_names import fold_name

logger = logging.getLogger(__name__)

MIN_FIRST_WORD = 2
MAX_NIGHTS = 20
# RotationStore.archive's starter entry ("Andrew" every night), R36.
STARTER_SONG = "First Song of the Night"


def first_word(name):
    """Folded first word of ``name``, or None when it is shorter than
    ``MIN_FIRST_WORD`` (or there is no word at all)."""
    words = fold_name(name).split()
    if not words or len(words[0]) < MIN_FIRST_WORD:
        return None
    return words[0]


def entry_matches(entry_members, account_words):
    """The members of an entry whose first word is one of ``account_words``,
    in the entry's order."""
    if not account_words:
        return []
    return [m for m in (entry_members or []) if first_word(m) in account_words]


def account_names(store, email, display_name, app_device):
    """``{"words": set[str], "names": list[str]}``: the names the gen account
    ``email`` goes by on this box and their matchable first words.

    Sources (R35), in order: the gen ``display_name``; the singer names on the
    non-friend, non-meta requests of the account's devices; those devices'
    aliases. The devices are ``app_device`` plus every device
    ``singer_accounts`` ever linked to the email (app and claimed web devices).

    Ownership (R49): ``app_device`` is the HMAC of the verified email, so all
    of its requests and its alias count. Any other device's requests count only
    on the nights it was linked to this email (``SingStore.linked_request_names``,
    which also documents the accepted same-night risk), and its alias only
    while this email is its latest owner (the rule ``forget_gen_account``
    uses) and the alias was set at or after this email's latest link to it
    (``SingStore.alias_since_link``), so a phone that was someone else's in
    between never lends their names.
    """
    me = (email or "").strip().lower()
    others = [d for d in store.devices_for_email_any_night(email) if d and d != app_device]
    owners = store.latest_owner_emails(others)

    candidates = [display_name]
    if app_device:
        candidates.extend(store.request_names_any_night([app_device]))
    candidates.extend(store.linked_request_names(email))
    if app_device:
        candidates.append(store.get_alias(app_device))
    for d in others:
        if owners.get(d) == me:
            candidates.append(store.alias_since_link(d, email))

    names, seen, words = [], set(), set()
    for raw in candidates:
        name = (raw or "").strip()
        folded = fold_name(name)
        if not folded or folded in seen:
            continue
        seen.add(folded)
        names.append(name)
        w = first_word(name)
        if w:
            words.add(w)
    return {"words": words, "names": names}


# --- suggestions (E4) -----------------------------------------------------------

def box_id_of(cfg):
    return (cfg or {}).get("box_id") or DEFAULT_BOX_ID


def _venue_name(cfg, store):
    try:
        import sing
        return sing._tip_settings(cfg, store).get("kj_name") or None
    except Exception:
        logger.warning("history claims: kj_name lookup failed", exc_info=True)
        return None


def _marker_night(marker):
    """Night date a night marker starts (``NightLog._live_night``'s rule: a
    reset before 06:00 is a mid-show reset of the previous evening)."""
    try:
        t = dt.datetime.strptime((marker or "")[:19], "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None
    return (t - dt.timedelta(days=1) if t.hour < 6 else t).date().isoformat()


def claim_night_id(store, box_id, night, _markers=None):
    """R32: the night id decide already uses for that night's live records
    (a ``history_outbox`` / ``singer_accounts`` marker mapping to ``night``),
    else ``night_id_for_marker(box_id, "night:" + night)``. With several
    markers, the one with the most outbox rows wins, then the latest."""
    markers = _markers if _markers is not None else store_night_markers(store)
    cands = markers.get(night) or []
    if not cands:
        return night_id_for_marker(box_id, "night:" + night)
    best = max(cands, key=lambda m: (m[1], m[0]))
    return night_id_for_marker(box_id, best[0])


def store_night_markers(store):
    """``{night_date: [(marker, outbox_rows)]}`` for every known live marker."""
    conn = store._get_conn()
    counts = {}
    for r in conn.execute(
            "SELECT night_started_at, COUNT(*) FROM history_outbox "
            "WHERE night_started_at NOT LIKE 'claim:%' GROUP BY night_started_at"):
        counts[r[0]] = r[1]
    for r in conn.execute("SELECT DISTINCT night_started_at FROM singer_accounts"):
        counts.setdefault(r[0], 0)
    out = {}
    for marker, n in counts.items():
        night = _marker_night(marker)
        if night:
            out.setdefault(night, []).append((marker, n))
    return out


def _is_starter(entry):
    return fold_name(entry.get("song")) == fold_name(STARTER_SONG)


def capacity_of(entry):
    """How many accounts may hold an entry (R31): one per singer on it."""
    return max(1, len(entry.get("members") or []))


def night_candidates(night_log, store, names, email):
    """``{night: {"songs": [cand], "unmatched": int}}`` where each ``cand`` is
    ``{"entry", "matched", "status"}`` and ``status`` is ``open`` (claimable),
    ``mine`` (this email holds it), ``taken`` (at capacity) or ``rejected``
    ("not mine"). Only linked Done entries whose members match the account
    are candidates; matching unlinked rows only count in ``unmatched``.

    The Night Log entry in ``cand["entry"]`` is the KJ's view (not app-safe):
    callers build responses from an allowlist (``_song_view``)."""
    words = (names or {}).get("words") or set()
    me = (email or "").strip().lower()
    if not words or not me:
        return {}
    live_open = store.is_enabled()
    nights = {}
    seen = set()
    for e in night_log.build_cached():
        night = e.get("night")
        if not e.get("sung") or not night:
            continue
        if e.get("live") and live_open:
            continue  # tonight belongs to Tonight while requests are open (R34)
        if _is_starter(e):
            continue  # R36
        matched = entry_matches(e.get("members") or [e.get("singer")], words)
        if not matched:
            continue
        slot = nights.setdefault(night, {"songs": [], "unmatched": 0})
        eid = e.get("entry_id")
        if e.get("link") == "none" or eid is None:
            slot["unmatched"] += 1
            continue
        if (night, eid) in seen:
            continue  # a recycled id twice in one night: keep the first
        seen.add((night, eid))
        slot["songs"].append({"entry": e, "matched": matched, "status": "open"})
    if not nights:
        return {}

    rejected = store.rejections_for(store.email_hash(me))
    claims = store.claims_for_nights(list(nights))
    recent = [c["entry"]["entry_id"] for n, slot in nights.items()
              if n >= ENTRY_IDS_UNIQUE_SINCE for c in slot["songs"]]
    live = store.outbox_holders(recent)
    for night, slot in nights.items():
        for c in slot["songs"]:
            eid = c["entry"]["entry_id"]
            holders = {x.strip().lower() for x in claims.get((night, eid), [])}
            if night >= ENTRY_IDS_UNIQUE_SINCE:
                holders |= live.get(eid, set())
            if me in holders:
                c["status"] = "mine"
            elif (night, eid) in rejected:
                c["status"] = "rejected"
            elif len(holders) >= capacity_of(c["entry"]):
                c["status"] = "taken"
    return nights


def _utc_z(local_ts):
    return _iso_z(_local_to_utc(local_ts)) if local_ts else None


def _song_view(e):
    """The app-safe view of a Night Log entry (R37): an explicit allowlist."""
    ident = e.get("song_identity") or {}
    started = _utc_z(e.get("started_at"))
    return {
        "entry_id": e["entry_id"],
        "singer_name": e.get("singer") or "",
        "members": list(e.get("members") or []),
        "artist": ident.get("artist") or "",
        "title": ident.get("title") or "",
        "started_at": started,
        "state": "sung" if started else "sung_unplayed",
    }


def _song_order(e):
    return (e.get("started_at") or "~", e.get("added_at") or "")


def suggestions(night_log, store, cfg, names, email, now=None, only_night_id=None):
    """``{"nights": [...], "more_nights": int}``: the past Nomad nights with
    songs the account might have sung and can still claim, newest first, at
    most ``MAX_NIGHTS``. Nothing here attributes anything (spec 6.10).

    Each night is ``{night_id, date, venue, names, unmatched, songs}`` and each
    song ``{entry_id, singer_name, members, artist, title, started_at, state}``,
    built from an allowlist: no emails, devices, request ids, phones or notes.
    ``only_night_id`` keeps just that night (claims revalidate with it)."""
    del now  # reserved: the build is cached by state, not by clock
    nights = night_candidates(night_log, store, names, email)
    box_id = box_id_of(cfg)
    markers = store_night_markers(store) if nights else {}
    venue = None
    out = []
    for night in sorted(nights, reverse=True):
        open_songs = [c for c in nights[night]["songs"] if c["status"] == "open"]
        if not open_songs:
            continue  # R38
        night_id = claim_night_id(store, box_id, night, markers)
        if only_night_id is not None and night_id != only_night_id:
            continue
        if venue is None:
            venue = _venue_name(cfg, store) or ""
        open_songs.sort(key=lambda c: _song_order(c["entry"]))
        matched = []
        for c in open_songs:
            for m in c["matched"]:
                if m not in matched:
                    matched.append(m)
        out.append({
            "night_id": night_id,
            "date": night,
            "venue": venue or None,
            "names": matched,
            "unmatched": nights[night]["unmatched"],
            "songs": [_song_view(c["entry"]) for c in open_songs],
        })
    return {"nights": out[:MAX_NIGHTS], "more_nights": max(0, len(out) - MAX_NIGHTS)}
