"""App (Expo) pushes: only up_next and now_singing. Web push is untouched."""

import json
from unittest.mock import MagicMock, patch

import pytest

from push_dispatcher import PushDispatcher, render_expo_message
from sing_store import SingStore

TOKEN = "ExponentPushToken[abc123]"
DEV = "dev-app-1"


def _entry(id, status="Waiting", device=None):
    return {"id": id, "status": status, "device": device, "song_artist": "Invented Song, Fake Band"}


@pytest.fixture
def store():
    s = SingStore(":memory:")
    s.ensure_night_started()
    yield s
    s.close()


def _dispatcher(store, entries):
    rotation = MagicMock()
    rotation.get_rotation.side_effect = lambda: entries
    return PushDispatcher(
        store=store, rotation=rotation, cfg={"vapid_private_key": "k", "box_id": "nomadpc"},
        get_current_token=lambda: "tok1",
        get_linked_phone_for_entry=lambda e: None,
        get_linked_device_for_entry=lambda e: e.get("device"),
        get_linked_request_id_for_entry=lambda e: 700 + e["id"],
    )


def _ok():
    r = MagicMock(status_code=200)
    r.json.return_value = {"data": {"status": "ok", "id": "t1"}}
    return r


def _walk_ladder(d, entries):
    """Singer is last in a queue of 4, then moves up one slot at a time to Now Singing."""
    for _ in range(3):
        d._dispatch_now()
        entries.pop(0)
    entries[0]["status"] = "Now Singing"
    d._dispatch_now()
    d.executor.shutdown(wait=True)


def _queue():
    return [_entry(1, device="o"), _entry(2, device="o"), _entry(3, device="o"),
            _entry(4, device=DEV)]


def test_app_sub_gets_only_up_next_and_now_singing(store):
    store.add_expo_subscription(DEV, TOKEN, "ios")
    entries = _queue()
    d = _dispatcher(store, entries)
    sent = []
    with patch("push_dispatcher.requests.post",
               side_effect=lambda url, **kw: (sent.append(kw["json"]), _ok())[1]):
        _walk_ladder(d, entries)
    assert [m["data"]["step"] for m in sent] == ["up_next", "now_singing"]
    assert [m["title"] for m in sent] == ["You're up next", "It's your turn"]


def test_app_sub_gets_no_decision_or_sung_pushes(store):
    store.add_expo_subscription(DEV, TOKEN, "ios")
    entries = [_entry(1, status="Now Singing", device=DEV)]
    d = _dispatcher(store, entries)
    with patch("push_dispatcher.requests.post", return_value=_ok()) as post:
        for decision in ("approved", "rejected", "resolved_alt", "unavailable", "quick_ready"):
            d.notify_request_decision(
                55, decision, {"device_id": DEV, "song_title": "X", "song_artist": "Y"})
        d.executor.shutdown(wait=True)
    post.assert_not_called()


def test_sung_is_not_sent_to_app(store):
    store.add_expo_subscription(DEV, TOKEN, "ios")
    entries = [_entry(1, status="Now Singing", device=DEV), _entry(2, device="o")]
    d = _dispatcher(store, entries)
    sent = []
    with patch("push_dispatcher.requests.post",
               side_effect=lambda url, **kw: (sent.append(kw["json"]), _ok())[1]):
        d._dispatch_now()
        entries[0]["status"] = "Done"
        d._dispatch_now()
        d.executor.shutdown(wait=True)
    assert [m["data"]["step"] for m in sent] == ["now_singing"]


def test_web_sub_still_gets_all_steps(store):
    web = {"id": 900, "endpoint": "ep", "p256dh": "p", "auth": "a", "phone": "",
           "device_id": DEV, "last_sent_state": None}
    store.list_active_push_subscriptions = lambda token: [web]
    entries = _queue()
    d = _dispatcher(store, entries)
    steps = []
    with patch("push_dispatcher.webpush",
               side_effect=lambda **kw: steps.append(json.loads(kw["data"])["data"]["step"])):
        _walk_ladder(d, entries)
    assert steps == ["up_in_2", "up_next", "now_singing"]


def test_web_decision_pushes_unchanged(store):
    store.find_subs_by_device = lambda token, device_id: [
        {"id": 901, "endpoint": "ep", "p256dh": "p", "auth": "a", "phone": "",
         "device_id": DEV, "last_sent_state": None}]
    d = _dispatcher(store, [])
    with patch("push_dispatcher.webpush") as wp:
        d.notify_request_decision(
            55, "approved", {"device_id": DEV, "song_title": "X", "song_artist": "Y"})
        d.executor.shutdown(wait=True)
    assert json.loads(wp.call_args.kwargs["data"])["data"]["step"] == "approved"


def test_payload_has_tonight_url():
    for step in ("up_next", "now_singing"):
        msg = render_expo_message(TOKEN, step, _entry(1), request_id=7, night_id="n1")
        assert msg["data"] == {"step": step, "request_id": 7, "night_id": "n1",
                               "url": "/tonight"}


def test_remove_expo_subscription_scoped_to_device(store):
    store.add_expo_subscription(DEV, TOKEN, "ios")
    assert store.remove_expo_subscription("someone-else", TOKEN) == 0
    assert len(store.list_active_expo_subscriptions()) == 1
    assert store.remove_expo_subscription(DEV, TOKEN) == 1
    assert store.list_active_expo_subscriptions() == []
    assert store.remove_expo_subscription(DEV, TOKEN) == 0   # idempotent
