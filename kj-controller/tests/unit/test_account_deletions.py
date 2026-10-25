"""Account-deletion poller + SingStore.forget_gen_account."""

import hashlib
import json
from unittest.mock import MagicMock

import pytest

from account_deletions import AccountDeletionPoller
from gen_client import GenApiError
from sing_store import SingStore

NIGHT = "2026-10-25 20:00:00"


def h(email):
    return hashlib.sha256(email.lower().encode()).hexdigest()


@pytest.fixture
def store(tmp_path):
    return SingStore(str(tmp_path / "rotation.db"))


def _req(store, device, name, phone="5551230000", friend=False):
    req = store.create_request(name, phone, song_artist="Nova Lane",
                               song_title="Golden Hour", source_type="local",
                               device_id=device)
    if friend:
        store.mark_friend_request(req["id"], name)
    return req


def _seed(store, device, email, name):
    """A fully-linked person: account links, push, alias, request, consent, outbox."""
    store.link_account(device, email, "app", NIGHT)
    store.set_gen_account(device, email, "tok-" + device)
    store.add_expo_subscription(device, "ExponentPushToken[" + device + "]", "ios")
    store.set_alias(device, name)
    req = _req(store, device, name)
    conn = store._get_conn()
    conn.execute("INSERT INTO singer_photo_consent (name_key, display_name, consent) "
                 "VALUES (?, ?, 'yes')", (name.lower(), name))
    conn.execute("INSERT INTO song_media_consent (entry_id, entry_created_at, name_key, "
                 "display_name, consent) VALUES (1, 'x', ?, ?, 'yes')", (name.lower(), name))
    conn.execute("INSERT INTO photo_consent_events (night, scope, name_key, display_name, "
                 "request_id, consent, instagram, source) VALUES (?, 'singer', ?, ?, ?, 'yes', "
                 "'@handle', 'singer')", (NIGHT, name.lower(), name, req["id"]))
    conn.commit()
    store.outbox_enqueue("hid-" + device, "put", {"gen_email": email, "entry_id": 1}, NIGHT)
    return req


def _count(store, table):
    return store._get_conn().execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


def test_forget_removes_linked_rows_only(store):
    _seed(store, "dev-a", "Ann@Example.com", "Ann")
    _seed(store, "dev-b", "bob@example.com", "Bob")
    friend = _req(store, "dev-a", "Zed", friend=True)

    counts = store.forget_gen_account(h("ann@example.com"))

    assert counts["singer_accounts"] == 1 and counts["sing_gen_accounts"] == 1
    assert counts["push_subscriptions"] == 1 and counts["aliases"] == 1
    assert counts["history_outbox"] == 1 and counts["app_friend_requests"] == 1
    assert counts["singer_photo_consent"] == 1 and counts["song_media_consent"] == 1
    conn = store._get_conn()
    # Ann's phone numbers are cleared; Bob's stay.
    phones = {r["device_id"]: r["phone"] for r in conn.execute(
        "SELECT device_id, phone FROM sing_requests")}
    assert phones["dev-a"] == "" and phones["dev-b"] == "5551230000"
    assert conn.execute("SELECT COUNT(*) FROM sing_requests WHERE device_id='dev-a'"
                        ).fetchone()[0] == 2  # requests themselves are kept
    # Bob is untouched everywhere.
    assert store.get_gen_account("dev-b")["email"] == "bob@example.com"
    assert store.get_alias("dev-b") == "Bob"
    assert _count(store, "singer_accounts") == 1
    assert _count(store, "history_outbox") == 1
    assert [r[0] for r in conn.execute("SELECT name_key FROM singer_photo_consent")] == ["bob"]
    assert _count(store, "song_media_consent") == 1
    assert len(store.list_active_expo_subscriptions()) == 1
    # The friend-request marker went with the requester; the friend's name did not
    # become a consent key to delete.
    assert conn.execute("SELECT COUNT(*) FROM app_friend_requests WHERE request_id=?",
                        (friend["id"],)).fetchone()[0] == 0


def test_forget_leaves_sent_history_rows(store):
    _seed(store, "dev-a", "ann@example.com", "Ann")
    store.outbox_mark_sent("hid-dev-a")
    assert store.forget_gen_account(h("ann@example.com"))["history_outbox"] == 0
    assert _count(store, "history_outbox") == 1


def test_consent_events_unlinked_not_deleted(store):
    req = _seed(store, "dev-a", "ann@example.com", "Ann")
    store.forget_gen_account(h("ann@example.com"))
    rows = store._get_conn().execute(
        "SELECT request_id, instagram, consent FROM photo_consent_events").fetchall()
    assert len(rows) == 1
    assert rows[0]["request_id"] is None and rows[0]["instagram"] is None
    assert rows[0]["consent"] == "yes"
    assert req["id"]


def test_idempotent(store):
    _seed(store, "dev-a", "ann@example.com", "Ann")
    first = store.forget_gen_account(h("ann@example.com"))
    second = store.forget_gen_account(h("ann@example.com"))
    assert sum(first.values()) > 0
    assert sum(second.values()) == 0


def test_unknown_or_empty_hash_is_a_noop(store):
    _seed(store, "dev-a", "ann@example.com", "Ann")
    assert sum(store.forget_gen_account(h("nobody@example.com")).values()) == 0
    assert sum(store.forget_gen_account("").values()) == 0
    assert _count(store, "singer_accounts") == 1


def test_failed_erase_rolls_back(store, monkeypatch):
    _seed(store, "dev-a", "ann@example.com", "Ann")
    monkeypatch.setattr("sing_store.fold_name", lambda n: (_ for _ in ()).throw(RuntimeError("boom")))
    with pytest.raises(RuntimeError):
        store.forget_gen_account(h("ann@example.com"))
    assert _count(store, "singer_accounts") == 1 and _count(store, "history_outbox") == 1


def _poller(store, deletions=None, list_exc=None, ack_exc=None):
    gen = MagicMock()
    if list_exc:
        gen.list_account_deletions.side_effect = list_exc
    else:
        gen.list_account_deletions.return_value = deletions or []
    if ack_exc:
        gen.ack_account_deletion.side_effect = ack_exc
    return AccountDeletionPoller(gen, store, interval=1), gen


def test_poller_acks_after_delete(store):
    _seed(store, "dev-a", "ann@example.com", "Ann")
    order = []
    poller, gen = _poller(store, [{"id": h("ann@example.com"), "email_hash": h("ann@example.com"),
                                   "created_at": "2026-10-25T12:00:00Z"}])
    gen.ack_account_deletion.side_effect = lambda i: order.append(
        ("ack", i, _count(store, "singer_accounts")))
    assert poller.poll_once() == 1
    assert order == [("ack", h("ann@example.com"), 0)]  # rows already gone at ack time
    assert store.get_deletions_cursor() == "2026-10-25T12:00:00Z"
    poller.poll_once()
    assert gen.list_account_deletions.call_args[0][0] == "2026-10-25T12:00:00Z"


def test_poller_does_not_ack_when_erase_fails(store, monkeypatch):
    poller, gen = _poller(store, [{"id": "x", "email_hash": "x", "created_at": "t1"}])
    monkeypatch.setattr(store, "forget_gen_account", MagicMock(side_effect=RuntimeError("db")))
    assert poller.poll_once() == 0
    gen.ack_account_deletion.assert_not_called()
    assert store.get_deletions_cursor() is None


def test_poller_survives_gen_down(store, caplog):
    poller, gen = _poller(store, list_exc=GenApiError(0, "connection refused"))
    with caplog.at_level("WARNING"):
        assert poller.poll_once() == 0
    assert not [r for r in caplog.records if r.levelname == "ERROR"]
    assert any("unreachable" in r.message for r in caplog.records)


def test_poller_survives_ack_failure_and_keeps_going(store):
    _seed(store, "dev-a", "ann@example.com", "Ann")
    _seed(store, "dev-b", "bob@example.com", "Bob")
    poller, gen = _poller(store, [
        {"id": h("ann@example.com"), "email_hash": h("ann@example.com"), "created_at": "t1"},
        {"id": h("bob@example.com"), "email_hash": h("bob@example.com"), "created_at": "t2"},
    ], ack_exc=GenApiError(0, "down"))
    assert poller.poll_once() == 2
    assert _count(store, "singer_accounts") == 0
    assert store.get_deletions_cursor() == "t2"


def test_gen_client_sends_partner_secret_and_since(monkeypatch):
    from gen_client import GenClient
    seen = []

    class Resp:
        def __init__(self, status, body):
            self.status_code, self._body, self.reason = status, body, "x"

        def json(self):
            if self._body is None:
                raise ValueError("no body")
            return self._body

    def fake(method, url, json=None, timeout=None, headers=None):
        seen.append((method, url, headers))
        return Resp(204, None) if method == "POST" else Resp(200, {"deletions": [{"id": "a"}]})

    monkeypatch.setattr("gen_client.requests.request", fake)
    gc = GenClient("https://gen.test", "tok", "sekret")
    assert gc.list_account_deletions("2026-10-25T12:00:00Z") == [{"id": "a"}]
    gc.ack_account_deletion("a")
    assert seen[0][0] == "GET"
    assert seen[0][1] == "https://gen.test/api/kjbox/account-deletions?since=2026-10-25T12%3A00%3A00Z"
    assert seen[1][1] == "https://gen.test/api/kjbox/account-deletions/a/ack"
    assert all(s[2]["X-Kjbox-Secret"] == "sekret" for s in seen)
