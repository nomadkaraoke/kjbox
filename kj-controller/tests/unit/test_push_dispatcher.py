"""Unit tests for PushDispatcher — ladder decisions, dedup, send loop."""

import json
from unittest.mock import MagicMock, patch

import pytest

from push_dispatcher import (
    PushDispatcher,
    decide_ladder_step,
    next_entry_for_phone,
    render_payload,
)


def _entry(id, status="Waiting", duration=240, singer="X", song_artist="Song"):
    return {
        "id": id, "status": status, "duration": duration,
        "singer": singer, "song_artist": song_artist,
    }


# ---------------------------------------------------------------------------
# decide_ladder_step
# ---------------------------------------------------------------------------

class TestDecideLadderStep:
    def test_now_singing(self):
        target = _entry(1, status="Now Singing")
        assert decide_ladder_step(target, [target]) == "now_singing"

    def test_now_singing_case_insensitive(self):
        target = _entry(1, status="now singing")
        assert decide_ladder_step(target, [target]) == "now_singing"

    def test_position_1_up_next(self):
        target = _entry(1)
        assert decide_ladder_step(target, [_entry(1), _entry(2)]) == "up_next"

    def test_position_2_up_next(self):
        target = _entry(2)
        assert decide_ladder_step(target, [_entry(1), _entry(2)]) == "up_next"

    def test_position_3_up_in_2(self):
        target = _entry(3)
        assert decide_ladder_step(
            target, [_entry(1), _entry(2), _entry(3)]
        ) == "up_in_2"

    def test_position_4_no_ladder(self):
        target = _entry(4)
        assert decide_ladder_step(
            target, [_entry(i) for i in (1, 2, 3, 4)]
        ) is None

    def test_done_entries_excluded_from_active_position(self):
        target = _entry(5)
        entries = [
            _entry(10, status="Done"),
            _entry(11, status="Done"),
            _entry(1),
            _entry(5),  # target at active position 2
        ]
        assert decide_ladder_step(target, entries) == "up_next"

    def test_left_entries_also_excluded(self):
        target = _entry(5)
        entries = [
            _entry(10, status="Left"),
            _entry(1),
            _entry(2),
            _entry(5),  # active position 3
        ]
        assert decide_ladder_step(target, entries) == "up_in_2"

    def test_target_not_in_list_returns_none(self):
        target = _entry(99)
        assert decide_ladder_step(target, [_entry(1), _entry(2)]) is None


# ---------------------------------------------------------------------------
# next_entry_for_phone
# ---------------------------------------------------------------------------

class TestNextEntryForPhone:
    def test_returns_closest_entry(self):
        entries = [_entry(1, singer="+A"), _entry(2, singer="+B"), _entry(3, singer="+A")]
        got = next_entry_for_phone(entries, "+A", lambda e: e["singer"])
        assert got["id"] == 1

    def test_skips_done_and_left(self):
        entries = [
            _entry(1, status="Done", singer="+A"),
            _entry(2, status="Left", singer="+A"),
            _entry(3, singer="+A"),
        ]
        got = next_entry_for_phone(entries, "+A", lambda e: e["singer"])
        assert got["id"] == 3

    def test_no_match_returns_none(self):
        entries = [_entry(1, singer="+X")]
        assert next_entry_for_phone(entries, "+A", lambda e: e["singer"]) is None


# ---------------------------------------------------------------------------
# render_payload
# ---------------------------------------------------------------------------

class TestRenderPayload:
    def test_all_five_steps_have_copy(self):
        for step in ("approved", "rejected", "up_in_2", "up_next", "now_singing"):
            pl = render_payload(step, _entry(1, song_artist="Foo — Bar"))
            assert pl["title"]
            assert pl["body"]
            assert "Foo — Bar" in pl["body"]
            assert pl["data"]["step"] == step

    def test_tag_scopes_to_entry_id(self):
        pl = render_payload("up_next", _entry(42))
        assert "42" in pl["tag"]

    def test_request_id_propagated_to_data(self):
        pl = render_payload("approved", _entry(1), request_id=99)
        assert pl["data"]["request_id"] == 99


# ---------------------------------------------------------------------------
# PushDispatcher
# ---------------------------------------------------------------------------

class TestDispatcher:
    def _make_dispatcher(self, sub_list=None, phone_lookup=None):
        store = MagicMock()
        store.list_active_push_subscriptions.return_value = sub_list or []
        store.find_subs_by_phone.side_effect = lambda token, phone: (phone_lookup or {}).get(phone, [])
        rotation = MagicMock()
        rotation.get_rotation.return_value = []
        cfg = {
            "vapid_public_key": "pub",
            "vapid_private_key": "priv",
            "vapid_subject": "mailto:test@x.com",
        }
        d = PushDispatcher(
            store=store, rotation=rotation, cfg=cfg,
            get_current_token=lambda: "tok1",
            get_linked_phone_for_entry=lambda e: e.get("singer"),
        )
        # Tests call _dispatch_now directly, bypassing the debounce timer.
        return d, store, rotation

    def test_no_token_no_send(self):
        d, store, rotation = self._make_dispatcher()
        d.get_current_token = lambda: None
        with patch("push_dispatcher.webpush") as wp:
            d._dispatch_now()
        wp.assert_not_called()

    def test_dispatches_up_in_2(self):
        sub = {
            "id": 99, "endpoint": "ep", "p256dh": "p", "auth": "a",
            "phone": "+1", "last_sent_state": None,
        }
        d, store, _ = self._make_dispatcher(sub_list=[sub])
        d.rotation.get_rotation.return_value = [
            _entry(1, singer="+2"),
            _entry(2, singer="+3"),
            _entry(3, singer="+1"),  # target at active position 3 → up_in_2
        ]
        with patch("push_dispatcher.webpush") as wp:
            d._dispatch_now()
            d.executor.shutdown(wait=True)  # drain the pool
        assert wp.call_count == 1
        args, kwargs = wp.call_args
        payload = json.loads(kwargs.get("data") or args[1])
        assert payload["data"]["step"] == "up_in_2"
        store.update_push_sent_state.assert_called_once_with(
            99, {"entry_id": 3, "ladder_step": "up_in_2"},
        )

    def test_dedup_blocks_same_state(self):
        sub = {
            "id": 99, "endpoint": "ep", "p256dh": "p", "auth": "a",
            "phone": "+1",
            "last_sent_state": json.dumps({"entry_id": 3, "ladder_step": "up_in_2"}),
        }
        d, store, _ = self._make_dispatcher(sub_list=[sub])
        d.rotation.get_rotation.return_value = [
            _entry(1, singer="+2"), _entry(2, singer="+3"), _entry(3, singer="+1"),
        ]
        with patch("push_dispatcher.webpush") as wp:
            d._dispatch_now()
            d.executor.shutdown(wait=True)
        wp.assert_not_called()
        store.update_push_sent_state.assert_not_called()

    def test_ladder_reset_on_new_entry(self):
        """When target entry changes, ladder resets — same step fires again."""
        sub = {
            "id": 99, "endpoint": "ep", "p256dh": "p", "auth": "a",
            "phone": "+1",
            "last_sent_state": json.dumps({"entry_id": 3, "ladder_step": "up_next"}),
        }
        d, store, _ = self._make_dispatcher(sub_list=[sub])
        # Now the singer's closest entry is a different id (entry 3 is gone, 7 is up)
        d.rotation.get_rotation.return_value = [
            _entry(1, singer="+2"),
            _entry(7, singer="+1"),  # new target at active position 2 → up_next
        ]
        with patch("push_dispatcher.webpush") as wp:
            d._dispatch_now()
            d.executor.shutdown(wait=True)
        assert wp.call_count == 1  # fired because entry_id differs from last_sent_state

    def test_no_target_no_send(self):
        """Singer has no active entries → skip."""
        sub = {
            "id": 99, "endpoint": "ep", "p256dh": "p", "auth": "a",
            "phone": "+1", "last_sent_state": None,
        }
        d, store, _ = self._make_dispatcher(sub_list=[sub])
        d.rotation.get_rotation.return_value = [
            _entry(1, status="Done", singer="+1"),  # only her done entry
        ]
        with patch("push_dispatcher.webpush") as wp:
            d._dispatch_now()
            d.executor.shutdown(wait=True)
        wp.assert_not_called()

    def test_410_disables_sub(self):
        sub = {
            "id": 99, "endpoint": "ep", "p256dh": "p", "auth": "a",
            "phone": "+1", "last_sent_state": None,
        }
        d, store, _ = self._make_dispatcher(sub_list=[sub])
        d.rotation.get_rotation.return_value = [
            _entry(3, status="Now Singing", singer="+1"),
        ]
        from pywebpush import WebPushException
        response_mock = MagicMock(); response_mock.status_code = 410
        exc = WebPushException("gone", response=response_mock)
        with patch("push_dispatcher.webpush", side_effect=exc):
            d._dispatch_now()
            d.executor.shutdown(wait=True)
        store.disable_push_subscription.assert_called_once_with(99)

    def test_429_does_not_disable_sub(self):
        sub = {
            "id": 99, "endpoint": "ep", "p256dh": "p", "auth": "a",
            "phone": "+1", "last_sent_state": None,
        }
        d, store, _ = self._make_dispatcher(sub_list=[sub])
        d.rotation.get_rotation.return_value = [_entry(3, status="Now Singing", singer="+1")]
        from pywebpush import WebPushException
        response_mock = MagicMock(); response_mock.status_code = 429
        exc = WebPushException("rate limited", response=response_mock)
        with patch("push_dispatcher.webpush", side_effect=exc):
            d._dispatch_now()
            d.executor.shutdown(wait=True)
        store.disable_push_subscription.assert_not_called()

    def test_disabled_subs_filtered_out(self):
        """store.list_active_push_subscriptions never returns disabled rows —
        but verify dispatcher trusts it (doesn't re-check)."""
        d, store, _ = self._make_dispatcher(sub_list=[])  # empty active list
        d.rotation.get_rotation.return_value = [_entry(1, singer="+1")]
        with patch("push_dispatcher.webpush") as wp:
            d._dispatch_now()
            d.executor.shutdown(wait=True)
        wp.assert_not_called()

    def test_notify_request_decision_sends_approved(self):
        sub = {
            "id": 99, "endpoint": "ep", "p256dh": "p", "auth": "a",
            "phone": "+1", "last_sent_state": None,
        }
        d, store, _ = self._make_dispatcher(phone_lookup={"+1": [sub]})
        req = {"phone": "+1", "song_artist": "Queen", "song_title": "Radio"}
        with patch("push_dispatcher.webpush") as wp:
            d.notify_request_decision(42, "approved", req)
            d.executor.shutdown(wait=True)
        assert wp.call_count == 1
        args, kwargs = wp.call_args
        payload = json.loads(kwargs.get("data") or args[1])
        assert payload["data"]["step"] == "approved"
        assert payload["data"]["request_id"] == 42

    def test_notify_request_decision_sends_rejected(self):
        sub = {
            "id": 99, "endpoint": "ep", "p256dh": "p", "auth": "a",
            "phone": "+1", "last_sent_state": None,
        }
        d, store, _ = self._make_dispatcher(phone_lookup={"+1": [sub]})
        req = {"phone": "+1", "song_artist": "Queen", "song_title": "Radio"}
        with patch("push_dispatcher.webpush") as wp:
            d.notify_request_decision(42, "rejected", req)
            d.executor.shutdown(wait=True)
        args, kwargs = wp.call_args
        payload = json.loads(kwargs.get("data") or args[1])
        assert payload["data"]["step"] == "rejected"

    def test_notify_request_decision_no_phone_skips(self):
        d, store, _ = self._make_dispatcher()
        req = {"phone": None}
        with patch("push_dispatcher.webpush") as wp:
            d.notify_request_decision(42, "approved", req)
            d.executor.shutdown(wait=True)
        wp.assert_not_called()

    def test_state_reserved_before_send(self):
        """last_sent_state should be updated synchronously in _dispatch_now
        BEFORE the webpush send, so a re-entrant dispatch sees the reserved
        state and dedups."""
        sub = {
            "id": 99, "endpoint": "ep", "p256dh": "p", "auth": "a",
            "phone": "+1", "last_sent_state": None,
        }
        d, store, _ = self._make_dispatcher(sub_list=[sub])
        d.rotation.get_rotation.return_value = [
            _entry(3, status="Now Singing", singer="+1"),
        ]
        # Patch webpush to block — the state should still be reserved by the time
        # webpush is CALLED (not after).
        import threading
        wp_called = threading.Event()
        wp_release = threading.Event()
        def slow_webpush(*args, **kwargs):
            wp_called.set()
            wp_release.wait(timeout=1)  # wait until the test releases
        with patch("push_dispatcher.webpush", side_effect=slow_webpush):
            d._dispatch_now()
            # At this point the executor is running webpush (or about to).
            # The reservation must have happened synchronously.
            store.update_push_sent_state.assert_called_with(
                99, {"entry_id": 3, "ladder_step": "now_singing"},
            )
            wp_release.set()
            d.executor.shutdown(wait=True)

    def test_notify_rotation_changed_debounces(self):
        """Multiple calls within debounce window coalesce into one _dispatch_now."""
        import time
        d, _, _ = self._make_dispatcher()
        d.DEBOUNCE_SECONDS = 0.1
        with patch.object(d, "_dispatch_now") as mock_dispatch:
            d.notify_rotation_changed()
            d.notify_rotation_changed()
            d.notify_rotation_changed()
            time.sleep(0.3)  # enough for the timer to fire once
        assert mock_dispatch.call_count == 1
