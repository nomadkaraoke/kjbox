"""PushDispatcher — Web Push sender for singer expectations UI.

Plugged into RotationManager._after_mutation(). Scans active subscriptions
for the current event token, decides which ladder step applies to each
singer's next-closest entry, dedups against last_sent_state, and fires
webpush() on a ThreadPoolExecutor so slow push-service responses don't
block the KJ UI critical path.

Performance budget: ≤50 subs × ≤100ms/call / 2 workers ≈ 2.5s drain.
Off the KJ-UI critical path because the executor.submit call returns
immediately.
"""

import json
import logging
import threading
from concurrent.futures import ThreadPoolExecutor

import requests
from pywebpush import WebPushException, webpush


log = logging.getLogger(__name__)


# ----------------------------------------------------------------------
# Pure ladder-decision helpers (module-level for testability)
# ----------------------------------------------------------------------

def decide_ladder_step(target_entry, all_entries):
    """Return the ladder step for `target_entry`, or None.

    - "now_singing" if status is Now Singing (case-insensitive).
    - "up_next" for active positions 1 and 2 (catches big-reorder jumps
      where a singer skips from pos 4 straight to pos 1).
    - "up_in_2" for active position 3 (two entries ahead).
    - None for active position 4+ and for target not in active list.
    """
    status = (target_entry.get("status") or "").lower()
    if status == "now singing":
        return "now_singing"
    active = [
        e for e in all_entries
        if (e.get("status") or "").lower() not in ("done", "left")
    ]
    try:
        pos = [e["id"] for e in active].index(target_entry["id"]) + 1
    except ValueError:
        return None
    if pos <= 2:
        return "up_next"
    if pos == 3:
        return "up_in_2"
    return None


def next_entry_for_phone(entries, phone, get_linked_phone):
    """First non-done/left entry whose linked phone matches `phone`."""
    return next_entry_for_sub(entries, {"phone": phone}, get_linked_phone)


def next_entry_for_sub(entries, sub, get_linked_phone, get_linked_device=None):
    """First non-done/left entry belonging to subscription `sub`.

    An entry belongs to the sub when its linked request has the sub's phone, or
    — for a singer who signed up without a phone — the sub's device_id. Empty
    keys never match (an un-linked KJ entry has neither).
    """
    phone = sub.get("phone") or ""
    device = (sub.get("device_id") or "") if get_linked_device else ""
    if not phone and not device:
        return None
    for e in entries:
        if (e.get("status") or "").lower() in ("done", "left"):
            continue
        if phone and get_linked_phone(e) == phone:
            return e
        if device and get_linked_device(e) == device:
            return e
    return None


# Copy per ladder step — {step: (title, body_template)}
_PAYLOADS = {
    "approved":    ("You're in! 🎶",         "The KJ added you — {song}."),
    "rejected":    ("The KJ needs a word",    "Come to the desk about: {song}."),
    "up_in_2":     ("You're up in 2! 🎤",    "{song} — head back to the venue."),
    "up_next":     ("You're up NEXT 🎤",     "{song} — stand by the mic."),
    "now_singing": ("🎤 You're singing now",  "{song} — you're up!"),
    # Auto-fallback outcomes for singer submissions.
    "resolved_alt":  ("You're all set 🎶",     "We queued an alternate version of {song}."),
    "unavailable":   ("The KJ needs a word",   "We couldn't find a playable video for {song} — your KJ has been notified."),
    # Make-it: gen's rough scrolling-lyrics draft landed on the box.
    "quick_ready":   ("⚡ Quick version ready",  "{song} — preview it and sing it now, or wait for the full version."),
}


# Expo (mobile app) copy. Singer-facing text says "host", never "KJ", and has
# no emoji; the app shows these as-is, so they stay short whole sentences.
EXPO_PUSH_URL = "https://exp.host/--/api/v2/push/send"
EXPO_TIMEOUT_S = 10

_EXPO_PAYLOADS = {
    "approved":    ("You're in", "The host added you: {song}."),
    "rejected":    ("The host needs a word", "Come to the host about: {song}."),
    "up_in_2":     ("You're up in 2", "{song}: head back to the venue."),
    "up_next":     ("You're up next", "{song}: stand by the mic."),
    "now_singing": ("You're singing now", "{song}: you're up."),
    "resolved_alt": ("You're all set", "We queued an alternate version of {song}."),
    "unavailable": ("The host needs a word",
                    "We couldn't find a playable video for {song}. Your host has been told."),
    "quick_ready": ("Quick version ready",
                    "{song}: preview it and sing it now, or wait for the full version."),
    "sung":        ("Nice one", "{song} is in your history."),
}
# The app only buzzes at the moments a singer must act. Everything else is
# already visible in the Tonight tab; web push keeps the full ladder.
_EXPO_APP_STEPS = frozenset({"up_next", "now_singing"})
# Per-step title overrides on top of _EXPO_PAYLOADS.
_EXPO_TITLES = {"now_singing": "It's your turn"}


def render_expo_message(token, step, entry, request_id=None, night_id=None):
    """Build one Expo push message for `step`."""
    song = (entry.get("song_artist") or "your song") if entry else "your song"
    title, body_tpl = _EXPO_PAYLOADS.get(step) or _EXPO_PAYLOADS["rejected"]
    title = _EXPO_TITLES.get(step, title)
    return {
        "to": token,
        "title": title,
        "body": body_tpl.format(song=song),
        "sound": "default",
        "priority": "high",
        "data": {"step": step, "request_id": request_id, "night_id": night_id,
                 "url": "/tonight"},
    }


def render_payload(step, entry, request_id=None):
    """Build the JSON payload that the SW receives on 'push'."""
    song = (entry.get("song_artist") or "your song") if entry else "your song"
    title, body_tpl = _PAYLOADS[step]
    tag = f"sing-ladder-{entry.get('id')}" if entry else f"sing-{step}"
    return {
        "title": title,
        "body": body_tpl.format(song=song),
        "tag": tag,
        "icon": "/sing/static/icon-192.png",
        "badge": "/sing/static/badge-72.png",
        "data": {
            "request_id": request_id,
            "step": step,
        },
    }


# ----------------------------------------------------------------------
# PushDispatcher
# ----------------------------------------------------------------------

class PushDispatcher:
    """Scans active subs on rotation change and dispatches pushes.

    Thread-safety: debounce timer is serialised by a lock. webpush() calls
    run on a small thread pool so slow push-service responses don't block
    _after_mutation() which is on the KJ UI critical path.
    """

    DEBOUNCE_SECONDS = 0.5

    def __init__(self, store, rotation, cfg,
                 get_current_token, get_linked_phone_for_entry,
                 get_linked_device_for_entry=None,
                 get_linked_request_id_for_entry=None):
        """
        store: SingStore instance (with list_active_push_subscriptions,
            find_subs_by_phone, update_push_sent_state, disable_push_subscription).
        rotation: RotationManager (needs get_rotation()).
        cfg: app config dict — must have vapid_private_key + vapid_subject.
        get_current_token: callable returning the currently-active event token.
        get_linked_phone_for_entry: callable(entry dict) -> phone or None.
            app.py wires this to sing_requests.phone via linked_entry_id.
        get_linked_device_for_entry: callable(entry dict) -> device_id or None.
            Same lookup for sing_requests.device_id — matches the subs of
            singers who signed up without a phone.
        """
        self.store = store
        self.rotation = rotation
        self.cfg = cfg
        self.get_current_token = get_current_token
        self.get_linked_phone_for_entry = get_linked_phone_for_entry
        self.get_linked_device_for_entry = get_linked_device_for_entry
        self.get_linked_request_id_for_entry = get_linked_request_id_for_entry
        # Entries seen active (not Done/Left) this night: an Expo "sung" push
        # fires only on that transition, never for rows already Done when a
        # token registered. In memory; a restart can miss one best-effort push.
        self._seen_active = set()
        self._seen_night = None
        self.executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="push-")
        self._debounce_timer = None
        self._debounce_lock = threading.Lock()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def notify_rotation_changed(self):
        """Called from RotationManager._after_mutation(). Debounced."""
        with self._debounce_lock:
            if self._debounce_timer is not None:
                self._debounce_timer.cancel()
            self._debounce_timer = threading.Timer(
                self.DEBOUNCE_SECONDS, self._dispatch_now,
            )
            self._debounce_timer.daemon = True
            self._debounce_timer.start()

    def notify_request_decision(self, request_id, decision, request_dict):
        """Immediate push for approve/reject — bypasses the rotation scan."""
        token = self.get_current_token()
        if not token:
            return
        phone = (request_dict or {}).get("phone")
        device_id = (request_dict or {}).get("device_id")
        subs = {s["id"]: s for s in self.store.find_subs_by_phone(token, phone)}
        # Phone-less singers subscribe by device (a sub with a phone is already
        # covered above; the id-keyed dict stops a double push if both match).
        for s in self.store.find_subs_by_device(token, device_id):
            subs.setdefault(s["id"], s)
        subs = list(subs.values())
        expo_subs = self._expo_subs_for_device(device_id)
        if not subs and not expo_subs:
            return
        # approve/reject plus the auto-fallback outcomes carry their own copy;
        # anything unrecognised falls back to the neutral "rejected" template.
        step = decision if decision in _PAYLOADS else "rejected"
        song = f"{request_dict.get('song_title') or ''} — {request_dict.get('song_artist') or ''}".strip(" —")
        fake_entry = {"id": request_id, "song_artist": song or None}
        payload = render_payload(step, fake_entry, request_id=request_id)
        for sub in subs:
            proposed_state = {"entry_id": request_id, "ladder_step": step}
            self.executor.submit(self._send, sub, payload, proposed_state)
        if expo_subs and step in _EXPO_APP_STEPS:
            night_id = self._night_id()
            for sub in expo_subs:
                msg = render_expo_message(sub["expo_token"], step, fake_entry,
                                          request_id=request_id, night_id=night_id)
                self.executor.submit(self._send_expo, sub, msg)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _dispatch_now(self):
        token = self.get_current_token()
        if not token:
            return
        try:
            entries = self.rotation.get_rotation()
        except Exception:
            log.exception("Rotation fetch failed during push dispatch")
            return
        subs = self.store.list_active_push_subscriptions(token)
        try:
            expo_subs = list(self.store.list_active_expo_subscriptions())
        except Exception:
            log.warning("expo subscription lookup failed", exc_info=True)
            expo_subs = []
        # Each lookup is a DB query; memoise per dispatch since every sub scans
        # the same entries.
        phones, devices = {}, {}

        def linked_phone(e):
            if e.get("id") not in phones:
                phones[e.get("id")] = self.get_linked_phone_for_entry(e)
            return phones[e.get("id")]

        linked_device = None
        if self.get_linked_device_for_entry is not None:
            def linked_device(e):
                if e.get("id") not in devices:
                    devices[e.get("id")] = self.get_linked_device_for_entry(e)
                return devices[e.get("id")]

        for sub in subs:
            target = next_entry_for_sub(entries, sub, linked_phone, linked_device)
            if target is None:
                continue
            step = decide_ladder_step(target, entries)
            if step is None:
                continue
            proposed = {"entry_id": target["id"], "ladder_step": step}
            last = {}
            if sub.get("last_sent_state"):
                try:
                    last = json.loads(sub["last_sent_state"])
                except Exception:
                    last = {}
            if (last.get("entry_id") == proposed["entry_id"]
                    and last.get("ladder_step") == proposed["ladder_step"]):
                continue
            # Reserve the state BEFORE submitting the send, so a second
            # _dispatch_now() landing while the send is in flight sees the
            # reserved state and skips. On send failure we accept missing
            # this one push — the next rotation change picks up the next
            # ladder step, and polling remains the safety net.
            self.store.update_push_sent_state(sub["id"], proposed)
            sub["last_sent_state"] = json.dumps(proposed)  # keep in-memory view consistent too
            payload = render_payload(step, target, request_id=None)
            self.executor.submit(self._send, sub, payload, proposed)

        if expo_subs:
            try:
                self._dispatch_expo(expo_subs, entries, linked_device)
            except Exception:
                log.warning("expo push dispatch failed", exc_info=True)

    # ------------------------------------------------------------------
    # Expo (mobile app) channel
    # ------------------------------------------------------------------

    def _night_id(self):
        try:
            from sing_api_v1 import night_id_for
            return night_id_for(self.store, self.cfg)
        except Exception:
            return None

    def _expo_subs_for_device(self, device_id):
        if not device_id:
            return []
        try:
            return list(self.store.find_expo_subs_by_device(device_id))
        except Exception:
            log.warning("expo subscription lookup failed", exc_info=True)
            return []

    @staticmethod
    def _load_state(sub):
        if sub.get("last_sent_state"):
            try:
                state = json.loads(sub["last_sent_state"])
                if isinstance(state, dict):
                    return state
            except Exception:
                pass
        return {}

    def _reserve_expo_state(self, sub, state):
        self.store.update_push_sent_state(sub["id"], state)
        sub["last_sent_state"] = json.dumps(state)

    def _dispatch_expo(self, expo_subs, entries, linked_device):
        """Ladder steps (same rules and dedup as web), but only `_EXPO_APP_STEPS` are sent."""
        if linked_device is None:
            return
        night_id = self._night_id()
        if night_id != self._seen_night:
            self._seen_active = set()
            self._seen_night = night_id
        done = [e for e in entries if (e.get("status") or "").lower() == "done"]
        previously_seen = set(self._seen_active)
        for e in entries:
            if (e.get("status") or "").lower() not in ("done", "left"):
                self._seen_active.add(e.get("id"))

        for sub in expo_subs:
            device = sub.get("device_id")
            if not device:
                continue
            last = self._load_state(sub)

            target = next_entry_for_sub(entries, {"device_id": device}, lambda _e: None,
                                        linked_device)
            if target is None:
                continue
            step = decide_ladder_step(target, entries)
            if step is None:
                continue
            if (last.get("entry_id") == target["id"]
                    and last.get("ladder_step") == step):
                continue
            state = {"entry_id": target["id"], "ladder_step": step}
            self._reserve_expo_state(sub, state)
            if step not in _EXPO_APP_STEPS:
                continue
            msg = render_expo_message(
                sub["expo_token"], step, target,
                request_id=self._request_id(target), night_id=night_id)
            self.executor.submit(self._send_expo, sub, msg)

    def _request_id(self, entry):
        if self.get_linked_request_id_for_entry is None:
            return None
        try:
            return self.get_linked_request_id_for_entry(entry)
        except Exception:
            return None

    def _send_expo(self, sub, message):
        """POST one message to Expo. Best effort: failures log at WARNING."""
        try:
            resp = requests.post(
                EXPO_PUSH_URL, json=message, timeout=EXPO_TIMEOUT_S,
                headers={"Accept": "application/json"},
            )
            if resp.status_code != 200:
                log.warning("expo push http %s sub=%s", resp.status_code, sub.get("id"))
                return
            data = (resp.json() or {}).get("data")
            ticket = data[0] if isinstance(data, list) and data else data
            if isinstance(ticket, dict) and ticket.get("status") == "error":
                err = (ticket.get("details") or {}).get("error")
                if err == "DeviceNotRegistered":
                    self.store.disable_push_subscription(sub["id"])
                else:
                    log.warning("expo push rejected sub=%s error=%s", sub.get("id"), err)
        except Exception as e:
            log.warning("expo push send error sub=%s: %s", sub.get("id"), e)

    def _send(self, sub, payload, proposed_state):
        try:
            webpush(
                subscription_info={
                    "endpoint": sub["endpoint"],
                    "keys": {"p256dh": sub["p256dh"], "auth": sub["auth"]},
                },
                data=json.dumps(payload),
                vapid_private_key=self.cfg["vapid_private_key"],
                vapid_claims={
                    "sub": self.cfg.get("vapid_subject", "mailto:admin@nomadkaraoke.com"),
                },
            )
            # Sent state was already reserved in _dispatch_now() before submit.
        except WebPushException as e:
            status = None
            resp = getattr(e, "response", None)
            if resp is not None:
                status = getattr(resp, "status_code", None)
            if status in (404, 410):
                self.store.disable_push_subscription(sub["id"])
            elif status in (400, 401, 403, 413):
                log.error("push send failed sub=%s status=%s", sub["id"], status)
            elif status == 429:
                log.warning("push service rate-limited sub=%s", sub["id"])
            else:
                log.warning("push send error sub=%s: %s", sub["id"], e)
        except Exception:
            log.exception("unexpected push send error sub=%s", sub.get("id"))
