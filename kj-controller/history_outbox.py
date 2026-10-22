"""History outbox: every song a linked singer performs goes to decide's karaoke log.

When the host marks a rotation entry Done, one ``history_outbox`` row is queued
per linked singer on it (spec 5.5), keyed by the deterministic ``history_id``
the app also sees on ``GET /me``. A background thread delivers the rows to
decide's partner API, so a record survives restarts and is never counted twice:

- **Who.** The account of the device whose request created the entry (never for
  an app "song for a friend": that's the friend's performance), plus every
  account whose singer is named on the entry (a duet partner, a host-typed
  entry, a friend song for them) when that name is unambiguous tonight (A5-R10,
  ``SingStore.name_claimed_by_others``). The same rule ``GET /me`` uses to hand
  out ``history_id``s, so the app and decide agree.
- **What.** Artist/title from the linked request, else the first live play's
  (the media library's identity for the file), else the rotation text split
  the way the KJ Gen modal does; a brand code is media, never identity (A.16).
- **When.** ``sung_at`` is the first live play start (A.96); ``ended_at`` is
  ``done_at``. Done without a play is ``sung_unplayed`` with ``sung_at`` null
  (A.97).
- **Undo.** An entry that is no longer Done gets a delete for the same id. Only
  tonight's rows and entries are compared (A.15), so a New Rotation never
  retracts last night's songs.

``RotationManager._after_mutation`` only wakes the delivery thread
(``notify_rotation_changed``); the thread coalesces a burst for about a second,
then runs ``scan()`` (tonight only, builds nothing in steady state, never
raises) and delivers. It also rescans every tick, so device links and restarts
are caught up. Delivery is inert while decide isn't configured.
"""

import datetime as dt
import logging
import re
import threading
import time

from history_ids import DEFAULT_BOX_ID, history_id_for, night_id_for_marker
from singer_names import fold_name

logger = logging.getLogger(__name__)

_TS_FMT = "%Y-%m-%d %H:%M:%S"
_PATH = "/api/partner/kjbox/karaoke-log/"
_NOT_SONGS = ("reorder", "tip")
_DEAD = ("cancelled", "rejected")
_MAX_SINGERS = 6
_MAX_SINGER_LEN = 100
_BACKOFF_BASE_S = 30
_BACKOFF_CAP_S = 1800
_PERMANENT_RETRY = dt.timedelta(days=1)
_PERMANENT_AFTER = dt.timedelta(days=7)
_MASTER_RE = re.compile(r"^(NOMAD-\d+)\b", re.IGNORECASE)


def _utc_now():
    return dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)


def _parse(ts):
    try:
        return dt.datetime.strptime((ts or "")[:19], _TS_FMT)
    except (TypeError, ValueError):
        return None


def _local_to_utc(ts):
    """Box-local 'YYYY-MM-DD HH:MM:SS' → naive UTC datetime, or None."""
    t = _parse(ts)
    if t is None:
        return None
    return t.astimezone().astimezone(dt.timezone.utc).replace(tzinfo=None)


def _iso_z(t):
    return t.strftime("%Y-%m-%dT%H:%M:%SZ") if t else None


def _is_done(entry):
    return (entry.get("status") or "").strip().lower() == "done"


def _brand_code(file_path):
    base = (file_path or "").replace("\\", "/").rsplit("/", 1)[-1]
    m = _MASTER_RE.match(base)
    return m.group(1).upper() if m else None


class _NightContext:
    """Everything a scan needs about tonight, read once per scan."""

    def __init__(self, sing_store, night, accounts, done_entries):
        self.store = sing_store
        self.night = night
        self.accounts = accounts  # {email: [device_id]}
        self.email_of = {d: e for e, devs in accounts.items() for d in devs}
        rows = sing_store.requests_linked_to_entries(
            [e["id"] for e in done_entries], night)
        self.requests = {}
        for r in rows:
            if r.get("source_type") in _NOT_SONGS or r.get("status") in _DEAD:
                continue
            self.requests.setdefault(r["linked_entry_id"], []).append(r)
        self.friend_ids = sing_store.friend_request_ids([r["id"] for r in rows])
        self.identity = {email: self._identity(devs) for email, devs in accounts.items()}
        self._claimed = {}

    def _identity(self, devices):
        """Folded names an account goes by tonight (as ``GET /me``): its
        devices' aliases plus the names on its own non-friend requests."""
        names = list(self.store.account_request_names(devices, self.night))
        for d in devices:
            alias = self.store.get_alias(d)
            if alias:
                names.append(alias)
        return {f for f in (fold_name(n) for n in names) if f}

    def claimed(self, folded, email):
        key = (folded, email)
        if key not in self._claimed:
            self._claimed[key] = self.store.name_claimed_by_others(
                folded, self.accounts.get(email, []), self.night)
        return self._claimed[key]


class HistoryOutbox:
    def __init__(self, rotation_store, sing_store, stats_store, cfg,
                 http_post=None, http_delete=None, clock=None, debounce_s=1.0):
        """``http_post`` sends the upsert (an HTTP PUT; ``requests.put`` by
        default) and ``http_delete`` the retraction; both take
        ``(url, ..., headers=, timeout=)`` and return a response with
        ``status_code``. ``clock`` returns naive UTC. ``debounce_s`` is how
        long the thread waits after a wake-up to coalesce a burst of mutations."""
        self.rotation_store = rotation_store
        self.sing_store = sing_store
        self.stats_store = stats_store
        self.cfg = cfg if cfg is not None else {}
        if http_post is None or http_delete is None:
            import requests
            http_post = http_post or requests.put
            http_delete = http_delete or requests.delete
        self._put = http_post
        self._delete = http_delete
        self._clock = clock or _utc_now
        self._debounce_s = debounce_s
        self._scan_lock = threading.Lock()
        self._wake = threading.Event()
        self._thread = None
        self._inert_logged = False

    # -- config ---------------------------------------------------------------------

    @property
    def box_id(self):
        return self.cfg.get("box_id") or DEFAULT_BOX_ID

    def _decide(self):
        url = (self.cfg.get("decide_api_url") or "").strip().rstrip("/")
        secret = (self.cfg.get("decide_kjbox_secret") or "").strip()
        return (url, secret) if url and secret else (None, None)

    def _venue_name(self):
        try:
            import sing
            return sing._tip_settings(self.cfg, self.sing_store).get("kj_name") or None
        except Exception:
            logger.warning("history outbox: kj_name lookup failed", exc_info=True)
            return None

    # -- records --------------------------------------------------------------------

    def build_records(self, entry, night_started_at, ctx=None, venue_name=None):
        """``[(history_id, payload)]``, one per linked singer on a Done entry."""
        if ctx is None:
            accounts = self.sing_store.night_accounts(night_started_at)
            ctx = _NightContext(self.sing_store, night_started_at, accounts, [entry])
            venue_name = self._venue_name()
        emails, members, song_req = self._attribution(entry, ctx)
        if not emails:
            return []
        base = self._payload(entry, night_started_at, song_req, members, venue_name)
        out = []
        for email in emails:
            hid = history_id_for(self.box_id, entry["id"], email)
            out.append((hid, {"history_id": hid, "gen_email": email, **base}))
        return out

    def _attribution(self, entry, ctx):
        """``(emails, members, song_request)``: the linked singers an entry is
        recorded for, its member names, and the request that carries the song."""
        if not ctx.accounts:
            return [], [], None
        import sing  # _entry_members: the web's duet split (singers_json or "A & B")

        reqs = ctx.requests.get(entry["id"], [])
        owners, friend_owners = [], set()
        for r in reqs:
            email = ctx.email_of.get(r.get("device_id"))
            if not email:
                continue
            if r["id"] in ctx.friend_ids:
                friend_owners.add(email)  # a friend's song is never the requester's
            elif email not in owners:
                owners.append(email)
        members = sing._entry_members(entry)
        folds = {fold_name(m) for m in members} - {""}
        emails = list(owners)
        for email in ctx.accounts:
            if email in emails or email in friend_owners:
                continue
            matched = folds & ctx.identity.get(email, set())
            if matched and not any(ctx.claimed(f, email) for f in matched):
                emails.append(email)
        song_req = next((r for r in reversed(reqs) if r["id"] not in ctx.friend_ids),
                        reqs[-1] if reqs else None)
        return emails, members, song_req

    def _payload(self, entry, night, req, members, venue_name):
        """The spec 5.5 record minus ``history_id``/``gen_email``. Scalars are
        strings or None (decide's model doesn't coerce: an int ``entry_id`` is
        a 400)."""
        night_utc = _local_to_utc(night)
        play = self.stats_store.first_play(
            entry["id"], not_before=night_utc.strftime(_TS_FMT) if night_utc else None
        ) if self.stats_store is not None else None
        sung_at = (play or {}).get("played_at")
        artist = ((req or {}).get("song_artist") or "").strip()
        title = ((req or {}).get("song_title") or "").strip()
        if not (artist and title):
            # No request carries the song: the first play's media-library
            # identity (A-R10), else the box's "Title - Artist" text split
            # (_format_song_text order). Host-typed "Artist - Title" text with
            # no play stays swapped: nothing better is known.
            p_artist = ((play or {}).get("artist") or "").strip()
            p_title = ((play or {}).get("title") or "").strip()
            if p_artist and p_title:
                artist, title = p_artist, p_title
            else:
                from kj_make import _naive_split
                split = _naive_split(entry.get("song_artist") or "")
                artist, title = split["artist"], split["title"]
        done_at = entry.get("done_at")
        ended_at = _iso_z(_local_to_utc(done_at))
        if sung_at and ended_at and ended_at < sung_at:
            # done_at is box-local: in the DST fall-back hour it converts an hour
            # early. A song can't end before it started.
            ended_at = sung_at
        media = {"source_type": str((req or {}).get("source_type") or "rotation")}
        brand = _brand_code(entry.get("file_path"))
        if brand:
            media["brand_code"] = brand
        if entry.get("gen_job_id"):
            media["gen_job_id"] = str(entry["gen_job_id"])
        from night_log import night_of
        return {
            "source": "kjbox",
            "box_id": str(self.box_id),
            "night_id": night_id_for_marker(self.box_id, night),
            "night_date": night_of(done_at) or night_of(night),
            "entry_id": str(entry["id"]),
            "artist": artist,
            "title": title,
            "sung_at": sung_at,
            "ended_at": ended_at,
            "state": "sung" if sung_at else "sung_unplayed",
            "singers": [str(m)[:_MAX_SINGER_LEN] for m in members][:_MAX_SINGERS],
            "venue": {"kind": "nomad", "name": venue_name},
            "media": media,
        }

    # -- scan -----------------------------------------------------------------------

    def notify_rotation_changed(self):
        """Called from ``RotationManager._after_mutation``: only wakes the
        delivery thread, which scans and delivers within about a second.
        Never blocks a rotation op."""
        self._wake.set()

    def scan(self, force=False):
        """Queue puts for tonight's Done entries and deletes for un-done ones.
        Returns how many rows changed. Never raises. Runs on the delivery
        thread; also callable directly (e.g. right after a device links).
        ``force`` is accepted for callers; a scan already running on another
        thread makes this one a no-op that the thread catches up on."""
        try:
            if not self._scan_lock.acquire(blocking=False):
                self._wake.set()
                return 0
            try:
                return self._scan()
            finally:
                self._scan_lock.release()
        except Exception:
            logger.warning("history outbox scan failed", exc_info=True)
            self._wake.set()
            return 0

    def _scan(self):
        store = self.sing_store
        night = store.get_night_started_at()
        if not night:
            return 0
        accounts = store.night_accounts(night)
        rows = store.outbox_rows_for_night(night)
        if not accounts and not rows:
            return 0
        done = [e for e in self.rotation_store.get_completed_entries() if _is_done(e)]
        if store.get_night_started_at() != night:
            # A New Rotation landed mid-scan: tonight's entries are gone, so
            # comparing them with the old night's rows would retract them all.
            self._wake.set()
            return 0
        now = self._clock()
        changed = 0
        wanted = set()
        if accounts and done:
            ctx = _NightContext(store, night, accounts, done)
            venue = None
            for entry in done:
                emails, members, song_req = self._attribution(entry, ctx)
                hids = {history_id_for(self.box_id, entry["id"], e): e for e in emails}
                wanted.update(hids)
                if all((rows.get(h) or {}).get("op") == "put" for h in hids):
                    continue  # already queued/sent: steady state builds nothing
                if venue is None:
                    venue = self._venue_name() or ""
                base = self._payload(entry, night, song_req, members, venue or None)
                for hid, email in hids.items():
                    if (rows.get(hid) or {}).get("op") == "put":
                        continue
                    payload = {"history_id": hid, "gen_email": email, **base}
                    if store.outbox_enqueue(hid, "put", payload, night_started_at=night, now=now):
                        changed += 1
        done_ids = {str(e["id"]) for e in done}
        for hid, row in rows.items():
            if row["op"] != "put" or hid in wanted:
                continue
            payload = row["payload"] or {}
            if str(payload.get("entry_id")) in done_ids:
                continue  # still Done: attribution changes never retract a sent song
            gone = {"history_id": hid, "gen_email": payload.get("gen_email"),
                    "entry_id": str(payload.get("entry_id"))}
            if store.outbox_enqueue(hid, "delete", gone, night_started_at=night, now=now):
                changed += 1
        return changed

    # -- delivery -------------------------------------------------------------------

    def deliver_due(self, limit=20):
        """Send due rows to decide. Returns how many were delivered."""
        url, secret = self._decide()
        if not url:
            if not self._inert_logged:
                logger.info("history outbox inert: decide_api_url / decide_kjbox_secret "
                            "not configured; sung songs stay queued")
                self._inert_logged = True
            return 0
        self._inert_logged = False
        sent = 0
        for row in self.sing_store.outbox_due(self._clock(), limit=limit):
            if self._deliver(row, url, secret):
                sent += 1
        return sent

    def _deliver(self, row, base, secret):
        hid, op = row["history_id"], row["op"]
        payload = row["payload"] or {}
        headers = {"X-Kjbox-Secret": secret}
        try:
            if op == "delete":
                resp = self._delete(base + _PATH + hid,
                                    params={"gen_email": payload.get("gen_email")},
                                    headers=headers, timeout=10)
            else:
                resp = self._put(base + _PATH + hid, json=payload, headers=headers, timeout=10)
            status = int(resp.status_code)
            detail = f"HTTP {status}"
        except Exception as e:
            status, detail = None, f"{type(e).__name__}: {e}"[:300]
        now = self._clock()
        # A retraction decide never stored (404) or already removed (410) is
        # the end state we want: deletes are idempotent.
        if status is not None and (200 <= status < 300
                                   or (op == "delete" and status in (404, 410))):
            self.sing_store.outbox_mark_sent(hid, op=op)
            return True
        if status is not None and 400 <= status < 500 and status != 429:
            # Undeliverable as sent (invalid): daily for 7 days, then give up.
            created = _parse(row.get("created_at")) or now
            permanent = now - created >= _PERMANENT_AFTER
            logger.warning("history outbox: decide refused %s %s (%s)%s", op, hid, detail,
                           "; giving up" if permanent else "; retrying in a day")
            self.sing_store.outbox_mark_failed(hid, detail, now + _PERMANENT_RETRY,
                                               permanent=permanent, op=op)
            return False
        wait = min(_BACKOFF_BASE_S * 2 ** int(row.get("attempts") or 0), _BACKOFF_CAP_S)
        logger.info("history outbox: %s %s failed (%s), retrying in %ss", op, hid, detail, wait)
        self.sing_store.outbox_mark_failed(hid, detail, now + dt.timedelta(seconds=wait),
                                           permanent=False, op=op)
        return False

    # -- thread ---------------------------------------------------------------------

    def run_once(self):
        """One delivery-thread tick: scan (rotation changes, links, restarts),
        then deliver what's due."""
        self.scan(force=True)
        try:
            return self.deliver_due()
        except Exception:
            logger.warning("history outbox delivery failed", exc_info=True)
            return 0

    def start(self):
        if self._thread is not None:
            return
        interval = float(self.cfg.get("history_outbox_interval_s") or 20)

        def _loop():
            while True:
                woken = self._wake.wait(interval)
                self._wake.clear()
                if woken:
                    time.sleep(self._debounce_s)  # coalesce a burst of mutations
                self.run_once()

        self._thread = threading.Thread(target=_loop, name="history-outbox", daemon=True)
        self._thread.start()
