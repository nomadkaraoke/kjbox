"""SingStore storage for the app-facing singer API: accounts, idempotency,
history outbox, Expo push."""

import sqlite3
from datetime import datetime, timedelta

import pytest

from sing_store import SingStore

N1 = "2026-10-21 19:00:00"
N2 = "2026-10-22 19:00:00"


@pytest.fixture
def store():
    s = SingStore(":memory:")
    yield s
    s.close()


def test_link_and_lookup_devices_scoped_to_night(store):
    store.link_account("dev-app", "a@x.com", "app", N1)
    store.link_account("dev-web", "a@x.com", "web", N1)
    assert sorted(store.account_devices("a@x.com", N1)) == ["dev-app", "dev-web"]
    assert store.account_devices("a@x.com", N2) == []
    assert store.account_for_device("dev-web", N1) == "a@x.com"
    assert store.account_for_device("dev-web", N2) is None


def test_link_is_upsert(store):
    store.link_account("d", "a@x.com", "web", N1)
    store.link_account("d", "b@x.com", "app", N1)
    assert store.account_for_device("d", N1) == "b@x.com"
    assert store.account_devices("a@x.com", N1) == []


def test_unlink(store):
    store.link_account("d", "a@x.com", "app", N1)
    store.link_account("d", "a@x.com", "app", N2)
    store.unlink_account("d", N1)
    assert store.account_for_device("d", N1) is None
    assert store.account_for_device("d", N2) == "a@x.com"


def test_idempotency_roundtrip_and_expiry(store):
    assert store.idempotent_get("k") is None
    store.idempotent_put("k", {"ok": True, "n": 1})
    assert store.idempotent_get("k") == {"ok": True, "n": 1}
    store.idempotent_put("old", {"x": 1}, ttl_s=-1)
    assert store.idempotent_get("old") is None
    store.idempotent_put("k", {"ok": False})  # overwrite
    assert store.idempotent_get("k") == {"ok": False}


NOW = datetime(2026, 10, 21, 20, 0, 0)


def _enq(store, hid, op, payload=None):
    return store.outbox_enqueue(hid, op, payload or {"h": hid}, night_started_at=N1, now=NOW)


def test_outbox_enqueue_put_then_delete_replaces_pending(store):
    assert _enq(store, "kj_1", "put", {"v": 1}) is True
    assert _enq(store, "kj_1", "delete", {"v": 2}) is True
    due = store.outbox_due(NOW)
    assert len(due) == 1
    assert due[0]["op"] == "delete" and due[0]["payload"] == {"v": 2}
    assert due[0]["history_id"] == "kj_1"


def test_outbox_enqueue_same_op_is_noop(store):
    assert _enq(store, "kj_1", "put", {"v": 1}) is True
    assert _enq(store, "kj_1", "put", {"v": 2}) is False
    assert store.outbox_due(NOW)[0]["payload"] == {"v": 1}


def test_outbox_due_respects_next_attempt(store):
    _enq(store, "kj_1", "put")
    _enq(store, "kj_2", "put")
    store.outbox_mark_failed("kj_1", "boom", NOW + timedelta(minutes=5), permanent=False)
    assert [r["history_id"] for r in store.outbox_due(NOW)] == ["kj_2"]
    later = store.outbox_due(NOW + timedelta(minutes=6))
    assert sorted(r["history_id"] for r in later) == ["kj_1", "kj_2"]
    row = [r for r in later if r["history_id"] == "kj_1"][0]
    assert row["attempts"] == 1 and row["last_error"] == "boom"


def test_outbox_due_limit(store):
    for i in range(5):
        _enq(store, f"kj_{i}", "put")
    assert len(store.outbox_due(NOW, limit=2)) == 2


def test_outbox_sent_and_permanent_failure_leave_due(store):
    _enq(store, "kj_1", "put")
    _enq(store, "kj_2", "put")
    store.outbox_mark_sent("kj_1")
    store.outbox_mark_failed("kj_2", "400", NOW, permanent=True)
    assert store.outbox_due(NOW + timedelta(days=1)) == []


def test_outbox_redo_after_sent_delete_requeues(store):
    _enq(store, "kj_1", "put")
    store.outbox_mark_sent("kj_1")
    assert _enq(store, "kj_1", "delete") is True      # undo
    store.outbox_mark_sent("kj_1")
    assert _enq(store, "kj_1", "put") is True         # redo
    assert [r["op"] for r in store.outbox_due(NOW)] == ["put"]


def test_outbox_sent_ids_for_night(store):
    store.outbox_enqueue("a", "put", {}, night_started_at=N1, now=NOW)
    store.outbox_enqueue("b", "put", {}, night_started_at=N1, now=NOW)
    store.outbox_enqueue("c", "put", {}, night_started_at=N1, now=NOW)
    store.outbox_enqueue("d", "put", {}, night_started_at=N2, now=NOW)
    store.outbox_enqueue("e", "put", {}, night_started_at=N1, now=NOW)
    store.outbox_mark_sent("b")
    store.outbox_enqueue("c", "delete", {}, night_started_at=N1, now=NOW)
    store.outbox_mark_failed("e", "x", NOW, permanent=True)
    assert store.outbox_sent_ids_for_night(N1) == {"a", "b"}


def test_expo_subscription_roundtrip(store):
    store.add_expo_subscription("dev", "ExponentPushToken[abc]", "ios")
    store.add_expo_subscription("dev", "ExponentPushToken[abc]", "ios")  # idempotent
    rows = store._get_conn().execute(
        "SELECT kind, expo_token, device_id FROM sing_push_subscriptions"
    ).fetchall()
    assert [tuple(r) for r in rows] == [("expo", "ExponentPushToken[abc]", "dev")]


def test_expo_subscription_columns_migrate_existing_db(tmp_path):
    p = str(tmp_path / "rotation.db")
    conn = sqlite3.connect(p)
    conn.executescript(
        """
        CREATE TABLE sing_push_subscriptions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now')),
            token TEXT NOT NULL, phone TEXT NOT NULL, singer_name TEXT NOT NULL,
            endpoint TEXT NOT NULL, p256dh TEXT NOT NULL, auth TEXT NOT NULL,
            user_agent TEXT, last_sent_state TEXT, last_seen_at TEXT,
            disabled_at TEXT, device_id TEXT DEFAULT NULL,
            UNIQUE(token, endpoint));
        INSERT INTO sing_push_subscriptions (token, phone, singer_name, endpoint, p256dh, auth)
            VALUES ('t', '+1', 'Al', 'https://e', 'k', 'a');
        """
    )
    conn.commit()
    conn.close()
    s = SingStore(p)
    SingStore(p).close()  # second open is idempotent
    cols = {r[1] for r in s._get_conn().execute("PRAGMA table_info(sing_push_subscriptions)")}
    assert {"kind", "expo_token"} <= cols
    row = s._get_conn().execute("SELECT kind FROM sing_push_subscriptions").fetchone()
    assert row["kind"] == "webpush"
    s.close()


def test_cleanup_stale_keeps_expo_rows(store):
    store.add_expo_subscription("dev", "ExponentPushToken[abc]", "ios")
    store._get_conn().execute(
        "UPDATE sing_push_subscriptions SET updated_at='2020-01-01 00:00:00'")
    store.cleanup_stale_push_subscriptions("current")
    assert store._get_conn().execute(
        "SELECT COUNT(*) FROM sing_push_subscriptions").fetchone()[0] == 1


def test_config_defaults(tmp_path):
    from config import load_config
    cfg = load_config(str(tmp_path / "missing.json"))
    assert cfg["box_id"] == "nomadpc"
    assert cfg["decide_api_url"] == ""
    assert cfg["decide_kjbox_secret"] == ""
    assert cfg["history_outbox_interval_s"] == 20


def test_idempotent_claim_is_exclusive_until_completed_or_released(store):
    assert store.idempotent_claim("k") is True
    # A second caller loses the claim, and a pending claim reads as "no response yet".
    assert store.idempotent_claim("k") is False
    assert store.idempotent_get("k") is None
    assert store.idempotent_pending("k") is True
    # Completing overwrites the claim with the response.
    store.idempotent_put("k", {"status": 200, "body": {"id": 1}})
    assert store.idempotent_pending("k") is False
    assert store.idempotent_get("k") == {"status": 200, "body": {"id": 1}}
    assert store.idempotent_claim("k") is False
    # Releasing a pending claim lets a retry claim it again; release never
    # deletes a completed response.
    assert store.idempotent_claim("k2") is True
    store.idempotent_release("k2")
    assert store.idempotent_pending("k2") is False
    assert store.idempotent_claim("k2") is True
    store.idempotent_release("k")
    assert store.idempotent_get("k") == {"status": 200, "body": {"id": 1}}


def test_idempotent_claim_expires(store):
    assert store.idempotent_claim("k", ttl_s=-1) is True
    # An abandoned (crashed) claim doesn't wedge the key forever.
    assert store.idempotent_claim("k") is True


def test_friend_requests_marked(store):
    store.mark_friend_request(5, "Nats")
    store.mark_friend_request(5, "Nats")  # idempotent
    assert store.friend_request_ids([5, 6]) == {5}
    assert store.is_friend_request(5) is True
    assert store.is_friend_request(6) is False
    assert store.friend_request_ids([]) == set()
