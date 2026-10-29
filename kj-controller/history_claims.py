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
    history_id_for,
    night_id_for_marker,
)
from history_outbox import _MAX_SINGER_LEN, _MAX_SINGERS, _brand_code, _iso_z, _local_to_utc
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


def store_night_markers(store):
    """``{night_date: [(marker, outbox_rows)]}`` for every known live marker:
    live outbox nights, ``singer_accounts`` nights and the current marker."""
    from night_log import marker_night
    conn = store._get_conn()
    counts = {}
    for r in conn.execute(
            "SELECT night_started_at, COUNT(*) FROM history_outbox "
            "WHERE night_started_at NOT LIKE 'claim:%' GROUP BY night_started_at"):
        counts[r[0]] = r[1]
    for r in conn.execute("SELECT DISTINCT night_started_at FROM singer_accounts"):
        counts.setdefault(r[0], 0)
    current = store.get_night_started_at()
    if current:
        counts.setdefault(current, 0)
    out = {}
    for marker, n in counts.items():
        night = marker_night(marker)
        if night:
            out.setdefault(night, []).append((marker, n))
    return out


class NightIds:
    """R32 night ids for claims, read once per call.

    In order: the ``night_id`` this night's existing claim records already
    carry (so a night keeps its id after its marker goes away, e.g. the only
    linked account was deleted); else the marker decide already uses for that
    night's live records (with several, the most outbox rows, then the
    latest); else ``night_id_for_marker(box_id, "night:" + date)``."""

    def __init__(self, store, box_id):
        self.box_id = box_id
        self.markers = store_night_markers(store)
        self.claimed = store.claim_night_ids()

    def for_night(self, night):
        if night in self.claimed:
            return self.claimed[night]
        cands = self.markers.get(night) or []
        if not cands:
            return night_id_for_marker(self.box_id, "night:" + night)
        best = max(cands, key=lambda m: (m[1], m[0]))
        return night_id_for_marker(self.box_id, best[0])


def claim_night_id(store, box_id, night):
    """The R32 night id for one night (see ``NightIds``)."""
    return NightIds(store, box_id).for_night(night)


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
    if not me:
        return {}
    held = store.claims_for_email(me)  # {(night, entry_id): member}
    if not words and not held:
        return {}
    live_open = store.is_enabled()
    nights = {}
    seen = set()
    entries = night_log.build_cached()
    for e in entries:
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
            slot["unmatched"] += 1  # a recycled id twice in one night: keep the first
            continue
        seen.add((night, eid))
        slot["songs"].append({"entry": e, "matched": matched, "status": "open"})
    # Songs this account already holds stay valid for a replay even after its
    # names changed (they are "mine", so never suggested again).
    if held:
        by_key = {(e.get("night"), e.get("entry_id")): e for e in entries
                  if e.get("sung") and e.get("entry_id") is not None}
        for key, member in held.items():
            if key in seen or key not in by_key:
                continue
            seen.add(key)
            nights.setdefault(key[0], {"songs": [], "unmatched": 0})["songs"].append(
                {"entry": by_key[key], "matched": [member], "status": "open"})
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


def suggestions(night_log, store, cfg, names, email, now=None):
    """``{"nights": [...], "more_nights": int}``: the past Nomad nights with
    songs the account might have sung and can still claim, newest first, at
    most ``MAX_NIGHTS``. Nothing here attributes anything (spec 6.10).

    Each night is ``{night_id, date, venue, names, unmatched, songs}`` and each
    song ``{entry_id, singer_name, members, artist, title, started_at, state}``,
    built from an allowlist: no emails, devices, request ids, phones or notes."""
    del now  # reserved: the build is cached by state, not by clock
    nights = night_candidates(night_log, store, names, email)
    ids = NightIds(store, box_id_of(cfg)) if nights else None
    venue = None
    out = []
    for night in sorted(nights, reverse=True):
        open_songs = [c for c in nights[night]["songs"] if c["status"] == "open"]
        if not open_songs:
            continue  # R38
        night_id = ids.for_night(night)
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


# --- claims (E5) ------------------------------------------------------------------

class ClaimError(Exception):
    """A claim that writes nothing: ``code`` is ``not_found`` (no such night
    for this account) or ``invalid`` (an id that isn't one of its songs)."""

    def __init__(self, code):
        super().__init__(code)
        self.code = code


def claim_history_id(box_id, night, entry_id, email):
    """R30: the live outbox's id from 2026-06-04 on (so a claim and a later
    live record are one decide record); before that, entry ids were recycled,
    so the id is namespaced by the night."""
    if night >= ENTRY_IDS_UNIQUE_SINCE:
        return history_id_for(box_id, entry_id, email)
    return history_id_for(box_id, f"{night}:{entry_id}", email)


def claim_payload(entry, night, night_id, email, hid, venue, box_id=DEFAULT_BOX_ID):
    """The outbox's spec 5.5 record for a claimed Night Log entry, so decide
    sees an ordinary ``kj_`` record. ``sung_at`` is the play start (never the
    Done time, #284); no play gives ``sung_unplayed`` with ``sung_at`` null."""
    ident = entry.get("song_identity") or {}
    sung_at = _utc_z(entry.get("started_at"))
    ended_at = _utc_z(entry.get("done_at"))
    if sung_at and ended_at and ended_at < sung_at:
        ended_at = sung_at  # DST fall-back hour, as the outbox clamps
    media_in = entry.get("media") or {}
    media = {"source_type": str((entry.get("added_by") or {}).get("source_type")
                                or "rotation")}
    brand = _brand_code(media_in.get("file_path"))
    if brand:
        media["brand_code"] = brand
    if media_in.get("gen_job_id"):
        media["gen_job_id"] = str(media_in["gen_job_id"])
    members = entry.get("members") or [entry.get("singer") or ""]
    return {
        "history_id": hid,
        "gen_email": email,
        "source": "kjbox",
        "box_id": str(box_id),
        "night_id": night_id,
        "night_date": night,
        "entry_id": str(entry["entry_id"]),
        "artist": ident.get("artist") or "",
        "title": ident.get("title") or "",
        "sung_at": sung_at,
        "ended_at": ended_at,
        "state": "sung" if sung_at else "sung_unplayed",
        "singers": [str(m)[:_MAX_SINGER_LEN] for m in members][:_MAX_SINGERS],
        "venue": {"kind": "nomad", "name": venue},
        "media": media,
    }


def _entry_ids(values):
    out = []
    for v in values or []:
        if isinstance(v, bool) or not isinstance(v, int):
            raise ClaimError("invalid")
        if v not in out:
            out.append(v)
    return out


def release_claims(store, night, entry_id, email=None, now=None):
    """Release the claims on ``(night, entry_id)`` (only ``email``'s when
    given) and retract each one's record from decide: ``delete_claim``, then a
    ``delete`` for the stored ``history_id`` with the outbox's
    ``{history_id, gen_email, entry_id}`` body, under the night
    ``claim:<night>``. Returns the released rows. Used by the host undo
    (R41) and by the outbox when the host un-Dones a claimed song (R52)."""
    rows = store.delete_claim(night, entry_id, email=email)
    for row in rows:
        gone = {"history_id": row["history_id"], "gen_email": row["gen_email"],
                "entry_id": str(row["entry_id"])}
        store.outbox_enqueue(row["history_id"], "delete", gone,
                             night_started_at="claim:" + row["night"], now=now)
    return rows


def claim(night_log, store, outbox, cfg, names, email, night_id, mine, not_mine, now=None):
    """Save the ticked songs of one night as this account's (``mine``) and
    remember the unticked ones (``not_mine``, R33).

    Every id must be a song of that night that matches the account (or one it
    already holds), else ``ClaimError("invalid")`` and nothing is written; an
    unknown night is ``ClaimError("not_found")``. ``not_mine`` on a song the
    account holds is also ``invalid`` (R51: a stored rejection would hide it if
    a host undo later frees it); ``mine`` on a song it once marked not mine
    clears that rejection.

    Claims are first come per singer slot (``try_claim``) and go to decide
    through the outbox under the night ``claim:<date>``, which tonight's scan
    never compares or retracts. Returns ``{saved, already_saved, taken,
    rejected}``.
    """
    me = (email or "").strip().lower()
    mine, not_mine = _entry_ids(mine), _entry_ids(not_mine)
    if not me or (not mine and not not_mine) or set(mine) & set(not_mine):
        raise ClaimError("invalid")
    nights = night_candidates(night_log, store, names, me)
    box_id = box_id_of(cfg)
    ids = NightIds(store, box_id) if nights else None
    night = next((n for n in nights if ids.for_night(n) == night_id), None)
    if night is None:
        raise ClaimError("not_found")
    songs = {c["entry"]["entry_id"]: c for c in nights[night]["songs"]}
    if any(eid not in songs for eid in mine + not_mine):
        raise ClaimError("invalid")
    if any(songs[eid]["status"] == "mine" for eid in not_mine):
        raise ClaimError("invalid")  # R51

    venue = _venue_name(cfg, store) if mine else None
    saved, already, taken = 0, 0, []
    queued = False
    for eid in mine:
        c = songs[eid]
        entry = c["entry"]
        hid = claim_history_id(box_id, night, eid, me)
        result = store.try_claim(night, eid, me, hid, c["matched"][0], capacity_of(entry))
        if result == "taken":
            taken.append(eid)
            continue
        if c["status"] == "rejected":
            store.remove_rejections(store.email_hash(me), night, [eid])
        if result == "already_mine":
            already += 1
            if store.outbox_has_put(hid):
                continue  # queued or delivered already: idempotent
        else:
            saved += 1
        payload = claim_payload(entry, night, night_id, me, hid, venue, box_id)
        store.outbox_enqueue(hid, "put", payload, night_started_at="claim:" + night, now=now)
        queued = True
        # Orphan-put window: a release (host undo, or the host un-Doning the
        # song) between try_claim and the enqueue above already queued its
        # delete, which this put just replaced. With the claim row gone, put
        # the delete back so decide never keeps a record nobody holds. (Next
        # to the R47 re-check in HistoryOutbox._scan: the same kind of window.)
        if (night, eid) not in store.claims_for_email(me):
            store.outbox_enqueue(hid, "delete", {"history_id": hid, "gen_email": me,
                                                 "entry_id": str(eid)},
                                 night_started_at="claim:" + night, now=now)
    if not_mine:
        store.add_rejections(store.email_hash(me), night, not_mine)
    if queued and outbox is not None:
        outbox.notify_rotation_changed()
    return {"saved": saved, "already_saved": already, "taken": taken,
            "rejected": len(not_mine)}
