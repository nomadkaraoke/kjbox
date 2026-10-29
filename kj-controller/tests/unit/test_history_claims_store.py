"""SingStore history claims + rejections (Phase 4c): first claimer wins per
entry and per singer slot, rejections are kept by email hash only, and an
account deletion erases both. Fixture emails/names are invented."""

import threading
from types import SimpleNamespace

import pytest

import localstack
from sing_store import SingStore

NIGHT = "2026-09-03"
MARKER = "2026-10-25 20:00:00"
WREN = "e2e-wren@example.com"
JUNO = "e2e-juno@example.com"
QUILL = "e2e-quill@example.com"


@pytest.fixture
def db_path(tmp_path):
    return str(tmp_path / "rotation.db")


@pytest.fixture
def store(db_path):
    s = SingStore(db_path)
    yield s
    s.close()


def _count(store, table):
    return store._get_conn().execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


# --- try_claim -----------------------------------------------------------------

def test_try_claim_first_wins_solo(store):
    assert store.try_claim(NIGHT, 12, WREN, "kj_a", "Wren B", 1) == "claimed"
    assert store.try_claim(NIGHT, 12, JUNO, "kj_b", "Wren", 1) == "taken"
    assert store.claims_for_nights([NIGHT]) == {(NIGHT, 12): [WREN]}


def test_try_claim_already_mine_is_idempotent(store):
    assert store.try_claim(NIGHT, 12, WREN, "kj_a", "Wren B", 1) == "claimed"
    assert store.try_claim(NIGHT, 12, WREN.upper(), "kj_a", "Wren B", 1) == "already_mine"
    assert _count(store, "history_claims") == 1


def test_try_claim_duet_capacity_two(store):
    assert store.try_claim(NIGHT, 7, WREN, "kj_a", "Wren B", 2) == "claimed"
    assert store.try_claim(NIGHT, 7, JUNO, "kj_b", "Juno", 2) == "claimed"
    assert store.try_claim(NIGHT, 7, QUILL, "kj_c", "Juno", 2) == "taken"
    assert sorted(store.claims_for_nights([NIGHT])[(NIGHT, 7)]) == sorted([WREN, JUNO])


def test_try_claim_capacity_is_at_least_one(store):
    assert store.try_claim(NIGHT, 3, WREN, "kj_a", "Wren", 0) == "claimed"
    assert store.try_claim(NIGHT, 3, JUNO, "kj_b", "Wren", 0) == "taken"


def test_try_claim_same_entry_id_other_night_is_independent(store):
    assert store.try_claim("2026-05-01", 12, WREN, "kj_a", "Wren", 1) == "claimed"
    assert store.try_claim("2026-05-08", 12, JUNO, "kj_b", "Juno", 1) == "claimed"


def test_try_claim_counts_outbox_holders(store):
    store.outbox_enqueue("kj_live", "put", {"gen_email": JUNO, "entry_id": "12"}, MARKER)
    store.outbox_mark_sent("kj_live")
    assert store.outbox_holders([12]) == {12: {JUNO}}
    assert store.try_claim(NIGHT, 12, WREN, "kj_a", "Wren", 1) == "taken"
    # The live record's own account already holds it.
    assert store.try_claim(NIGHT, 12, JUNO, "kj_live", "Juno", 1) == "already_mine"
    assert _count(store, "history_claims") == 0


def test_outbox_holders_ignores_deletes_and_failed_rows(store):
    store.outbox_enqueue("kj_del", "delete", {"gen_email": JUNO, "entry_id": "5"}, MARKER)
    store.outbox_enqueue("kj_fail", "put", {"gen_email": QUILL, "entry_id": "5"}, MARKER)
    store.outbox_mark_failed("kj_fail", "boom", __import__("datetime").datetime(2026, 1, 1),
                             permanent=True)
    store.outbox_enqueue("kj_ok", "put", {"gen_email": WREN, "entry_id": "6"}, MARKER)
    assert store.outbox_holders([5, 6]) == {6: {WREN}}
    assert store.outbox_holders([]) == {}


def test_try_claim_concurrent_threads_one_winner(db_path):
    SingStore(db_path).close()  # schema
    results = []
    barrier = threading.Barrier(2)

    def claim(email):
        s = SingStore(db_path)
        try:
            barrier.wait()
            results.append(s.try_claim(NIGHT, 12, email, "kj_" + email[:8], "Wren", 1))
        finally:
            s.close()

    threads = [threading.Thread(target=claim, args=(e,)) for e in (WREN, JUNO)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(results) == ["claimed", "taken"]
    check = SingStore(db_path)
    assert _count(check, "history_claims") == 1
    check.close()


# --- rejections ------------------------------------------------------------------

def test_rejections_by_hash_only(store):
    hw = SingStore.email_hash(WREN)
    store.add_rejections(hw, NIGHT, [3, 4])
    store.add_rejections(hw, NIGHT, [4, 5])  # INSERT OR IGNORE
    assert store.rejections_for(hw) == {(NIGHT, 3), (NIGHT, 4), (NIGHT, 5)}
    assert store.rejections_for(SingStore.email_hash(JUNO)) == set()
    cols = {r[1] for r in store._get_conn().execute("PRAGMA table_info(history_rejections)")}
    assert not any("email" in c and c != "email_hash" for c in cols)
    assert "email_hash" in cols


# --- host undo -------------------------------------------------------------------

def test_delete_claim_one_or_all(store):
    store.try_claim(NIGHT, 7, WREN, "kj_a", "Wren B", 2)
    store.try_claim(NIGHT, 7, JUNO, "kj_b", "Juno", 2)
    gone = store.delete_claim(NIGHT, 7, email=JUNO.upper())
    assert [r["gen_email"] for r in gone] == [JUNO]
    assert gone[0]["history_id"] == "kj_b"
    gone = store.delete_claim(NIGHT, 7)
    assert [r["gen_email"] for r in gone] == [WREN]
    assert store.delete_claim(NIGHT, 7) == []


# --- account deletion ---------------------------------------------------------------

def test_forget_gen_account_erases_claims_rejections_and_unsent_claim_rows(store):
    store.try_claim(NIGHT, 7, WREN, "kj_w7", "Wren B", 2)
    store.try_claim(NIGHT, 7, JUNO, "kj_j7", "Juno", 2)
    store.try_claim(NIGHT, 8, WREN, "kj_w8", "Wren B", 1)
    store.add_rejections(SingStore.email_hash(WREN), NIGHT, [9])
    store.add_rejections(SingStore.email_hash(JUNO), NIGHT, [9])
    # Claimed songs go out as ordinary outbox puts; Wren's are still unsent.
    store.outbox_enqueue("kj_w7", "put", {"gen_email": WREN, "entry_id": "7"}, NIGHT)
    store.outbox_enqueue("kj_j7", "put", {"gen_email": JUNO, "entry_id": "7"}, NIGHT)

    counts = store.forget_gen_account(SingStore.email_hash(WREN))

    assert counts["history_claims"] == 2
    assert counts["history_rejections"] == 1
    assert counts["history_outbox"] == 1
    assert store.claims_for_nights([NIGHT]) == {(NIGHT, 7): [JUNO]}
    assert store.rejections_for(SingStore.email_hash(WREN)) == set()
    assert store.rejections_for(SingStore.email_hash(JUNO)) == {(NIGHT, 9)}
    left = {r[0] for r in store._get_conn().execute("SELECT history_id FROM history_outbox")}
    assert left == {"kj_j7"}
    # Idempotent.
    again = store.forget_gen_account(SingStore.email_hash(WREN))
    assert again["history_claims"] == 0 and again["history_rejections"] == 0


# --- local stack reset ----------------------------------------------------------------

def test_reset_clears_claim_tables(store, monkeypatch):
    assert "history_claims" in localstack._STATE_TABLES
    assert "history_rejections" in localstack._STATE_TABLES
    store.try_claim(NIGHT, 7, WREN, "kj_w7", "Wren B", 1)
    store.add_rejections(SingStore.email_hash(WREN), NIGHT, [9])
    rotation = SimpleNamespace(store=store, notify_changed=lambda: None,
                               push_dispatcher=None)
    app = SimpleNamespace(rotation=rotation, sing_store=store)
    monkeypatch.setattr(localstack, "_start_new_night", lambda s: None)
    localstack.reset_night(app)
    assert _count(store, "history_claims") == 0
    assert _count(store, "history_rejections") == 0
