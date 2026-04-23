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
    for e in entries:
        if (e.get("status") or "").lower() in ("done", "left"):
            continue
        if get_linked_phone(e) == phone:
            return e
    return None


# Copy per ladder step — {step: (title, body_template)}
_PAYLOADS = {
    "approved":    ("You're in! 🎶",         "The KJ added you — {song}."),
    "rejected":    ("The KJ needs a word",    "Come to the desk about: {song}."),
    "up_in_2":     ("You're up in 2! 🎤",    "{song} — head back to the venue."),
    "up_next":     ("You're up NEXT 🎤",     "{song} — stand by the mic."),
    "now_singing": ("🎤 You're singing now",  "{song} — you're up!"),
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
                 get_current_token, get_linked_phone_for_entry):
        """
        store: SingStore instance (with list_active_push_subscriptions,
            find_subs_by_phone, update_push_sent_state, disable_push_subscription).
        rotation: RotationManager (needs get_rotation()).
        cfg: app config dict — must have vapid_private_key + vapid_subject.
        get_current_token: callable returning the currently-active event token.
        get_linked_phone_for_entry: callable(entry dict) -> phone or None.
            app.py wires this to sing_requests.phone via linked_entry_id.
        """
        self.store = store
        self.rotation = rotation
        self.cfg = cfg
        self.get_current_token = get_current_token
        self.get_linked_phone_for_entry = get_linked_phone_for_entry
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
        if not phone:
            return
        subs = self.store.find_subs_by_phone(token, phone)
        if not subs:
            return
        step = "approved" if decision == "approved" else "rejected"
        song = f"{request_dict.get('song_title') or ''} — {request_dict.get('song_artist') or ''}".strip(" —")
        fake_entry = {"id": request_id, "song_artist": song or None}
        payload = render_payload(step, fake_entry, request_id=request_id)
        for sub in subs:
            proposed_state = {"entry_id": request_id, "ladder_step": step}
            self.executor.submit(self._send, sub, payload, proposed_state)

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
        for sub in subs:
            target = next_entry_for_phone(
                entries, sub["phone"], self.get_linked_phone_for_entry,
            )
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
