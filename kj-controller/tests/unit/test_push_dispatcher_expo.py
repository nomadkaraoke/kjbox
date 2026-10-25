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
    entries = [_entry(1, device="other"),
               _entry(2, device=DEV, song="Mr Brightside — The Killers")]
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
    assert msg["data"] == {"step": "up_next", "request_id": 702, "night_id": night_id,
                           "url": "/tonight"}


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
        # up_in_2: web only (the app is told at up_next and now_singing)
        assert post.call_count == 0 and wp.call_count == 1
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
