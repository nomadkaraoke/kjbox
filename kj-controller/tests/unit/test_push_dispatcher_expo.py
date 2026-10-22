"""Expo (mobile app) channel of PushDispatcher. Expo's HTTP API is always mocked."""

import json
from unittest.mock import MagicMock, patch

import pytest

from push_dispatcher import EXPO_PUSH_URL, PushDispatcher
from sing_store import SingStore

TOKEN = "ExponentPushToken[abc123]"
DEV = "dev-app-1"


def _entry(id, status="Waiting", device=None, song="Song — Artist"):
    return {"id": id, "status": status, "device": device, "song_artist": song}


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


def _run(d):
    d._dispatch_now()
    d.executor.shutdown(wait=True)


def test_expo_payload_shape(store):
    store.add_expo_subscription(DEV, TOKEN, "ios")
    entries = [_entry(1, device="other"), _entry(2, device="other"),
               _entry(3, device=DEV, song="Mr Brightside — The Killers")]
    d = _dispatcher(store, entries)
    with patch("push_dispatcher.requests.post", return_value=_ok()) as post, \
         patch("push_dispatcher.webpush") as wp:
        _run(d)
    wp.assert_not_called()
    assert post.call_count == 1
    args, kwargs = post.call_args
    assert args[0] == EXPO_PUSH_URL == "https://exp.host/--/api/v2/push/send"
    assert kwargs["timeout"] == 10
    msg = kwargs["json"]
    assert msg["to"] == TOKEN
    assert msg["title"] and msg["body"]
    assert "Mr Brightside — The Killers" in msg["body"]
    assert "KJ" not in msg["title"] + msg["body"]
    night_id = d._night_id()
    assert night_id
    assert msg["data"] == {"step": "up_in_2", "request_id": 703, "night_id": night_id}


def test_expo_and_webpush_dedup_per_step(store):
    store.add_expo_subscription(DEV, TOKEN, "android")
    web = {"id": 900, "endpoint": "ep", "p256dh": "p", "auth": "a", "phone": "",
           "device_id": DEV, "last_sent_state": None}
    entries = [_entry(1, device="o"), _entry(2, device="o"), _entry(3, device=DEV)]
    d = _dispatcher(store, entries)
    # web-push row comes from the (patched) list so this test pins both channels
    store.list_active_push_subscriptions = lambda token: [web]
    store.update_push_sent_state = MagicMock(wraps=store.update_push_sent_state)
    with patch("push_dispatcher.requests.post", return_value=_ok()) as post, \
         patch("push_dispatcher.webpush") as wp:
        d._dispatch_now()
        d._dispatch_now()          # same step again: both channels dedup
        d.executor.shutdown(wait=True)
        assert post.call_count == 1 and wp.call_count == 1
    # a new step fires on both again
    entries.pop(0)
    d2 = _dispatcher(store, entries)
    store.list_active_push_subscriptions = lambda token: [web]
    with patch("push_dispatcher.requests.post", return_value=_ok()) as post, \
         patch("push_dispatcher.webpush") as wp:
        _run(d2)
        assert post.call_count == 1 and wp.call_count == 1
        steps = json.loads(wp.call_args.kwargs["data"])["data"]["step"], post.call_args.kwargs["json"]["data"]["step"]
        assert steps == ("up_next", "up_next")


def test_expo_device_not_registered_disables_subscription(store):
    store.add_expo_subscription(DEV, TOKEN, "ios")
    entries = [_entry(1, device=DEV)]
    d = _dispatcher(store, entries)
    bad = MagicMock(status_code=200)
    bad.json.return_value = {"data": {"status": "error", "message": "gone",
                                      "details": {"error": "DeviceNotRegistered"}}}
    with patch("push_dispatcher.requests.post", return_value=bad):
        _run(d)
    assert store.list_active_expo_subscriptions() == []


def test_expo_failures_never_raise_or_log_error(store, caplog):
    store.add_expo_subscription(DEV, TOKEN, "ios")
    d = _dispatcher(store, [_entry(1, device=DEV)])
    with patch("push_dispatcher.requests.post", side_effect=OSError("boom")), \
         caplog.at_level("DEBUG"):
        _run(d)
    assert [r for r in caplog.records if r.levelname == "ERROR"] == []
    assert len(store.list_active_expo_subscriptions()) == 1   # not disabled on a network error


def test_sung_fires_once_when_app_entry_becomes_done(store):
    store.add_expo_subscription(DEV, TOKEN, "ios")
    entries = [_entry(1, status="Now Singing", device=DEV), _entry(2, device="o")]
    d = _dispatcher(store, entries)
    sent = []
    with patch("push_dispatcher.requests.post",
               side_effect=lambda url, **kw: (sent.append(kw["json"]), _ok())[1]):
        d._dispatch_now()
        entries[0]["status"] = "Done"
        d._dispatch_now()
        d._dispatch_now()          # dedup: no second sung
        entries.append(_entry(3, device=DEV))
        d._dispatch_now()          # a new entry; entry 1 stays sung-once
        d.executor.shutdown(wait=True)
    steps = [m["data"]["step"] for m in sent]
    assert steps.count("sung") == 1
    assert steps[0] == "now_singing"


def test_sung_not_sent_for_rows_already_done_at_registration(store):
    store.add_expo_subscription(DEV, TOKEN, "ios")
    d = _dispatcher(store, [_entry(1, status="Done", device=DEV)])
    with patch("push_dispatcher.requests.post", return_value=_ok()) as post:
        _run(d)
    post.assert_not_called()


def test_decision_push_reaches_expo(store):
    store.add_expo_subscription(DEV, TOKEN, "ios")
    d = _dispatcher(store, [])
    with patch("push_dispatcher.requests.post", return_value=_ok()) as post:
        d.notify_request_decision(55, "approved", {"device_id": DEV, "song_title": "X", "song_artist": "Y"})
        d.executor.shutdown(wait=True)
    assert post.call_args.kwargs["json"]["data"]["step"] == "approved"
    assert post.call_args.kwargs["json"]["data"]["request_id"] == 55
