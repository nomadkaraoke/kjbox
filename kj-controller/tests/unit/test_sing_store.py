"""Unit tests for SingStore — public request storage + event token helpers."""

import json

import pytest

from sing_store import (
    SingStore,
    TOKEN_KEY,
    ENABLED_KEY,
    AUTO_APPROVE_KEY,
    ACCEPT_MAKE_REQUESTS_KEY,
)


@pytest.fixture
def store():
    """In-memory SingStore for each test."""
    s = SingStore(":memory:")
    yield s
    s.close()


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

class TestSchemaInit:
    def test_tables_created(self, store):
        conn = store._get_conn()
        tables = {row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
        assert "sing_requests" in tables
        assert "rotation_meta" in tables

    def test_sing_requests_columns(self, store):
        conn = store._get_conn()
        cols = {row[1] for row in conn.execute(
            "PRAGMA table_info(sing_requests)"
        ).fetchall()}
        expected = {
            "id", "created_at", "token", "singer_name", "phone",
            "song_artist", "song_title", "source_type", "source_ref",
            "source_meta", "notes", "status", "rejected_reason",
            "reviewed_at", "linked_entry_id", "additional_singers",
        }
        assert expected <= cols

    def test_indexes_created(self, store):
        conn = store._get_conn()
        indexes = {row[1] for row in conn.execute(
            "SELECT type, name FROM sqlite_master WHERE type='index'"
        ).fetchall()}
        assert "idx_sing_requests_status" in indexes
        assert "idx_sing_requests_token" in indexes

    def test_schema_idempotent(self, store):
        store.init_schema()
        store.init_schema()

    def test_wal_mode(self, tmp_path):
        db_path = str(tmp_path / "rotation.db")
        s = SingStore(db_path)
        mode = s._get_conn().execute("PRAGMA journal_mode").fetchone()[0]
        s.close()
        assert mode == "wal"

    def test_additional_singers_column_present(self, store):
        conn = store._get_conn()
        cols = {row[1] for row in conn.execute(
            "PRAGMA table_info(sing_requests)"
        ).fetchall()}
        assert "additional_singers" in cols

    def test_additional_singers_migration_idempotent(self, tmp_path):
        """Re-initialising on an existing DB must not fail on the new column."""
        db_path = str(tmp_path / "rotation.db")
        SingStore(db_path).close()
        # Second open re-runs init_schema; must not raise.
        SingStore(db_path).close()

    def test_shares_rotation_meta_with_rotation_store(self, tmp_path):
        """SingStore and RotationStore both use rotation_meta in the same DB."""
        from rotation_store import RotationStore
        db_path = str(tmp_path / "shared.db")
        rs = RotationStore(db_path)
        ss = SingStore(db_path)
        # RotationStore writes a meta key; SingStore should see it
        rs._get_conn().execute(
            "INSERT INTO rotation_meta (key, value) VALUES ('foo', 'bar')"
        )
        rs._get_conn().commit()
        assert ss._get_meta("foo") == "bar"
        rs.close()
        ss.close()


# ---------------------------------------------------------------------------
# Token helpers
# ---------------------------------------------------------------------------

class TestTokenHelpers:
    def test_get_token_initially_none(self, store):
        assert store.get_token() is None

    def test_regenerate_creates_token(self, store):
        tok = store.regenerate_token()
        assert isinstance(tok, str)
        # 4-digit numeric code — singers type this from the venue screen.
        assert len(tok) == 4
        assert tok.isdigit()
        assert store.get_token() == tok

    def test_regenerate_changes_token(self, store):
        t1 = store.regenerate_token()
        t2 = store.regenerate_token()
        assert t1 != t2
        assert store.get_token() == t2
        assert t2.isdigit() and len(t2) == 4

    def test_ensure_token_creates_if_absent(self, store):
        assert store.get_token() is None
        tok = store.ensure_token()
        assert tok == store.get_token()

    def test_ensure_token_preserves_existing(self, store):
        tok = store.regenerate_token()
        assert store.ensure_token() == tok

    def test_ensure_night_started_sets_if_absent(self, store):
        assert store.get_night_started_at() is None
        val = store.ensure_night_started()
        assert val
        assert store.get_night_started_at() == val

    def test_ensure_night_started_preserves_existing(self, store):
        from sing_store import NIGHT_STARTED_KEY
        store._set_meta(NIGHT_STARTED_KEY, "2026-05-28 21:00:00")
        # Must never overwrite an existing (possibly newer) value.
        assert store.ensure_night_started() == "2026-05-28 21:00:00"
        assert store.get_night_started_at() == "2026-05-28 21:00:00"

    def test_set_token_pins_specific_value(self, store):
        store.regenerate_token()  # start with a random token
        result = store.set_token("2121")
        assert result == "2121"
        assert store.get_token() == "2121"

    def test_set_token_idempotent(self, store):
        store.set_token("2121")
        store.set_token("2121")
        assert store.get_token() == "2121"

    def test_set_token_rejects_non_string(self, store):
        with pytest.raises(ValueError):
            store.set_token(2121)

    def test_set_token_rejects_wrong_length(self, store):
        with pytest.raises(ValueError):
            store.set_token("123")
        with pytest.raises(ValueError):
            store.set_token("12345")

    def test_set_token_rejects_non_digits(self, store):
        with pytest.raises(ValueError):
            store.set_token("abcd")
        with pytest.raises(ValueError):
            store.set_token("12a4")

    def test_set_token_rejects_empty(self, store):
        with pytest.raises(ValueError):
            store.set_token("")

    def test_enabled_default_true(self, store):
        assert store.is_enabled() is True

    def test_enabled_round_trip(self, store):
        store.set_enabled(False)
        assert store.is_enabled() is False
        store.set_enabled(True)
        assert store.is_enabled() is True

    def test_accept_make_requests_default_true(self, store):
        assert store.is_accepting_make_requests() is True

    def test_accept_make_requests_round_trip(self, store):
        store.set_accepting_make_requests(False)
        assert store.is_accepting_make_requests() is False
        store.set_accepting_make_requests(True)
        assert store.is_accepting_make_requests() is True

    def test_accept_make_requests_persists_across_instances(self, tmp_path):
        db = tmp_path / "a.db"
        s1 = SingStore(str(db))
        s1.set_accepting_make_requests(False)
        s1.close()
        s2 = SingStore(str(db))
        assert s2.is_accepting_make_requests() is False
        s2.close()

    def test_simple_mode_default_false(self, store):
        assert store.is_simple_mode() is False

    def test_simple_mode_round_trip(self, store):
        store.set_simple_mode(True)
        assert store.is_simple_mode() is True
        store.set_simple_mode(False)
        assert store.is_simple_mode() is False

    def test_simple_mode_persists_across_instances(self, tmp_path):
        from sing_store import SingStore
        db = tmp_path / "simple_mode.db"
        s1 = SingStore(str(db))
        s1.set_simple_mode(True)
        s1.close()
        s2 = SingStore(str(db))
        assert s2.is_simple_mode() is True
        s2.close()

    def test_auto_approve_default_false(self, store):
        assert store.is_auto_approve() is False

    def test_auto_approve_round_trip(self, store):
        store.set_auto_approve(True)
        assert store.is_auto_approve() is True
        store.set_auto_approve(False)
        assert store.is_auto_approve() is False

    def test_auto_sms_next_default_false(self, store):
        assert store.is_auto_sms_next() is False

    def test_auto_sms_next_round_trip(self, store):
        store.set_auto_sms_next(True)
        assert store.is_auto_sms_next() is True
        store.set_auto_sms_next(False)
        assert store.is_auto_sms_next() is False

    def test_concurrent_writes_from_many_threads(self, tmp_path):
        """Regression: 2026-05-01 outage. SingStore shared one sqlite3
        connection across Flask + background threads; an implicit BEGIN
        in one thread blocked every other writer until busy_timeout.
        Per-thread connections must isolate transactions.
        """
        import threading
        import time

        db_path = str(tmp_path / "rotation.db")
        s = SingStore(db_path)
        try:
            errors = []

            def worker(idx):
                try:
                    s.create_request(
                        singer_name=f"singer_{idx}",
                        phone="+1 555 0000",
                        source_type="local",
                        source_ref=f"/x/{idx}.mp4",
                    )
                except Exception as exc:
                    errors.append((idx, str(exc)))

            threads = [threading.Thread(target=worker, args=(i,))
                       for i in range(20)]
            t0 = time.time()
            for t in threads: t.start()
            for t in threads: t.join(timeout=15)
            elapsed = time.time() - t0
            assert not errors, (
                f"{len(errors)} concurrent writes raised, e.g. {errors[0]}")
            assert elapsed < 10, f"writes took {elapsed:.1f}s — likely lock contention"
            assert s.count_pending() == 20
        finally:
            s.close()

    def test_meta_keys_persist_across_instances(self, tmp_path):
        db_path = str(tmp_path / "rotation.db")
        s1 = SingStore(db_path)
        tok = s1.regenerate_token()
        s1.set_enabled(False)
        s1.set_auto_approve(True)
        s1.close()
        s2 = SingStore(db_path)
        assert s2.get_token() == tok
        assert s2.is_enabled() is False
        assert s2.is_auto_approve() is True
        s2.close()


# ---------------------------------------------------------------------------
# Request CRUD
# ---------------------------------------------------------------------------

class TestCreateRequest:
    def test_create_minimal(self, store):
        store.regenerate_token()
        req = store.create_request(
            singer_name="Andrew",
            phone="+61 400 123 456",
            source_type="local",
            source_ref="/media/song.mp4",
        )
        assert req["id"] > 0
        assert req["singer_name"] == "Andrew"
        assert req["phone"] == "+61 400 123 456"
        assert req["source_type"] == "local"
        assert req["source_ref"] == "/media/song.mp4"
        assert req["status"] == "pending"
        assert req["token"] == store.get_token()

    def test_create_all_fields(self, store):
        req = store.create_request(
            singer_name="Bea",
            phone="+61 2 9999 9999",
            song_artist="Queen",
            song_title="Bohemian Rhapsody",
            source_type="divebar",
            source_ref="gdrive_abc",
            source_meta={"brand_code": "KV", "disc_id": "X123"},
            notes="high key",
        )
        assert req["song_artist"] == "Queen"
        assert req["song_title"] == "Bohemian Rhapsody"
        assert req["source_type"] == "divebar"
        assert req["source_ref"] == "gdrive_abc"
        assert req["notes"] == "high key"
        assert json.loads(req["source_meta"]) == {"brand_code": "KV", "disc_id": "X123"}

    def test_trims_whitespace(self, store):
        req = store.create_request(
            singer_name="  Andrew  ",
            phone="  +61 400 123 456  ",
            source_type="local",
        )
        assert req["singer_name"] == "Andrew"
        assert req["phone"] == "+61 400 123 456"

    def test_missing_singer_name_raises(self, store):
        with pytest.raises(ValueError):
            store.create_request(singer_name="", phone="+123", source_type="local")

    def test_phone_optional(self, store):
        # Phone is optional at the store layer too — empty stays empty so
        # /push/subscribe (which requires phone) won't accidentally bind a
        # subscription to a singer who skipped it.
        req = store.create_request(
            singer_name="Andrew", phone="", source_type="local",
        )
        assert req["phone"] == ""

    def test_missing_phone_treated_as_empty(self, store):
        # Defensive: None coerces to empty string, not crash, since the
        # column is NOT NULL.
        req = store.create_request(
            singer_name="Andrew", phone=None, source_type="local",
        )
        assert req["phone"] == ""

    def test_missing_source_type_raises(self, store):
        with pytest.raises(ValueError):
            store.create_request(singer_name="A", phone="+1", source_type="")

    def test_explicit_token_overrides_current(self, store):
        store.regenerate_token()
        req = store.create_request(
            singer_name="A", phone="+1", source_type="local", token="override",
        )
        assert req["token"] == "override"


class TestListAndGet:
    def test_get_unknown_returns_none(self, store):
        assert store.get_request(9999) is None

    def test_list_empty(self, store):
        assert store.list_requests() == []

    def test_list_orders_newest_first(self, store):
        ids = []
        for name in ("A", "B", "C"):
            ids.append(
                store.create_request(
                    singer_name=name, phone="+1", source_type="local"
                )["id"]
            )
        listed = [r["id"] for r in store.list_requests()]
        assert listed == list(reversed(ids))

    def test_list_filters_by_status(self, store):
        r1 = store.create_request(singer_name="A", phone="+1", source_type="local")
        r2 = store.create_request(singer_name="B", phone="+1", source_type="local")
        store.mark_approved(r1["id"])
        pending = store.list_requests(status="pending")
        approved = store.list_requests(status="approved")
        assert [r["id"] for r in pending] == [r2["id"]]
        assert [r["id"] for r in approved] == [r1["id"]]

    def test_list_filters_by_token(self, store):
        store.create_request(
            singer_name="A", phone="+1", source_type="local", token="tok-a"
        )
        store.create_request(
            singer_name="B", phone="+1", source_type="local", token="tok-b"
        )
        by_a = store.list_requests(token="tok-a")
        assert len(by_a) == 1
        assert by_a[0]["singer_name"] == "A"

    def test_list_limit(self, store):
        for i in range(5):
            store.create_request(
                singer_name=f"s{i}", phone="+1", source_type="local"
            )
        assert len(store.list_requests(limit=3)) == 3

    def test_count_by_status(self, store):
        r1 = store.create_request(singer_name="A", phone="+1", source_type="local")
        r2 = store.create_request(singer_name="B", phone="+1", source_type="local")
        store.create_request(singer_name="C", phone="+1", source_type="local")
        store.mark_approved(r1["id"])
        store.mark_rejected(r2["id"])
        counts = store.count_by_status()
        assert counts == {"pending": 1, "approved": 1, "rejected": 1}
        assert store.count_pending() == 1


class TestUpdateAndStatus:
    def test_update_fields(self, store):
        req = store.create_request(
            singer_name="Andrew", phone="+1", source_type="local"
        )
        updated = store.update_request(
            req["id"],
            singer_name="Bea",
            song_artist="Queen",
            song_title="Bohemian Rhapsody",
            source_type="youtube",
            source_ref="https://youtu.be/abc",
            source_meta={"bitrate": "128"},
            notes="please",
        )
        assert updated["singer_name"] == "Bea"
        assert updated["song_artist"] == "Queen"
        assert updated["song_title"] == "Bohemian Rhapsody"
        assert updated["source_type"] == "youtube"
        assert updated["source_ref"] == "https://youtu.be/abc"
        assert json.loads(updated["source_meta"]) == {"bitrate": "128"}
        assert updated["notes"] == "please"

    def test_update_preserves_unset_fields(self, store):
        req = store.create_request(
            singer_name="Andrew", phone="+1",
            song_artist="Queen", source_type="local",
        )
        updated = store.update_request(req["id"], singer_name="Andy")
        assert updated["singer_name"] == "Andy"
        assert updated["song_artist"] == "Queen"  # unchanged

    def test_update_unknown_raises(self, store):
        with pytest.raises(ValueError):
            store.update_request(9999, singer_name="X")

    def test_update_request_source_rewrites_source_fields(self, store):
        """kj_pick approval path calls this to swap the placeholder for the
        picked version — other fields (singer/notes) must not be touched."""
        req = store.create_request(
            singer_name="Andrew", phone="+1",
            song_artist="Queen", song_title="Bo Rhap",
            source_type="kj_pick", source_ref=None,
            source_meta={"versions": [{"source": "local"}]},
            notes="keep me",
        )
        updated = store.update_request_source(
            req["id"],
            source_type="local",
            source_ref="/media/queen.mp4",
            source_meta=None,
        )
        assert updated["source_type"] == "local"
        assert updated["source_ref"] == "/media/queen.mp4"
        assert updated["source_meta"] is None
        # Untouched fields survive.
        assert updated["singer_name"] == "Andrew"
        assert updated["song_artist"] == "Queen"
        assert updated["notes"] == "keep me"

    def test_update_request_source_stores_meta_as_json(self, store):
        req = store.create_request(
            singer_name="A", phone="+1", source_type="kj_pick",
        )
        updated = store.update_request_source(
            req["id"],
            source_type="divebar",
            source_ref="drive-file-123",
            source_meta={"brand_code": "SF", "disc_id": "SF-001"},
        )
        assert json.loads(updated["source_meta"]) == {
            "brand_code": "SF", "disc_id": "SF-001",
        }

    def test_update_request_source_unknown_raises(self, store):
        with pytest.raises(ValueError):
            store.update_request_source(9999, "local", "/x.mp4", None)

    def test_mark_approved_sets_fields(self, store):
        req = store.create_request(
            singer_name="A", phone="+1", source_type="local"
        )
        approved = store.mark_approved(req["id"], linked_entry_id=42)
        assert approved["status"] == "approved"
        assert approved["linked_entry_id"] == 42
        assert approved["reviewed_at"] is not None

    def test_mark_rejected_sets_fields(self, store):
        req = store.create_request(
            singer_name="A", phone="+1", source_type="local"
        )
        rejected = store.mark_rejected(req["id"], reason="duplicate")
        assert rejected["status"] == "rejected"
        assert rejected["rejected_reason"] == "duplicate"
        assert rejected["reviewed_at"] is not None

    def test_set_linked_entry(self, store):
        req = store.create_request(
            singer_name="A", phone="+1", source_type="local"
        )
        updated = store.set_linked_entry(req["id"], 7)
        assert updated["linked_entry_id"] == 7
        assert updated["status"] == "pending"  # unchanged

    def test_mark_unknown_raises(self, store):
        with pytest.raises(ValueError):
            store.mark_approved(9999)
        with pytest.raises(ValueError):
            store.mark_rejected(9999)


# ---------------------------------------------------------------------------
# Constants are exposed for other modules to reference
# ---------------------------------------------------------------------------

class TestConstants:
    def test_meta_keys(self):
        assert TOKEN_KEY == "request_token"
        assert ENABLED_KEY == "request_token_enabled"
        assert AUTO_APPROVE_KEY == "request_auto_approve"
        assert ACCEPT_MAKE_REQUESTS_KEY == "sing_accept_make_requests"


# ---------------------------------------------------------------------------
# Push subscription CRUD
# ---------------------------------------------------------------------------

class TestPushSubscriptions:
    def test_insert_subscription(self, store):
        sub_id = store.insert_push_subscription(
            token="tok1", phone="+61400000001", singer_name="Alice",
            endpoint="https://fcm.googleapis.com/fcm/send/abc",
            p256dh="p256dh1", auth="auth1", user_agent="Mozilla/5.0",
        )
        assert sub_id > 0
        subs = store.list_active_push_subscriptions("tok1")
        assert len(subs) == 1
        assert subs[0]["phone"] == "+61400000001"
        assert subs[0]["disabled_at"] is None
        assert subs[0]["singer_name"] == "Alice"

    def test_insert_or_replace_on_same_endpoint(self, store):
        store.insert_push_subscription(
            token="tok1", phone="+61400000001", singer_name="Alice",
            endpoint="https://fcm.googleapis.com/fcm/send/same", p256dh="a", auth="a",
        )
        # Re-subscribe same (token, endpoint) with fresh keys
        store.insert_push_subscription(
            token="tok1", phone="+61400000001", singer_name="Alice",
            endpoint="https://fcm.googleapis.com/fcm/send/same", p256dh="b", auth="b",
        )
        subs = store.list_active_push_subscriptions("tok1")
        assert len(subs) == 1
        assert subs[0]["p256dh"] == "b"

    def test_insert_re_enables_previously_disabled_sub(self, store):
        sid = store.insert_push_subscription(
            token="tok1", phone="+1", singer_name="Alice",
            endpoint="ep", p256dh="a", auth="a",
        )
        store.disable_push_subscription(sid)
        # Re-insert same (token, endpoint) — disabled_at should clear
        store.insert_push_subscription(
            token="tok1", phone="+1", singer_name="Alice",
            endpoint="ep", p256dh="b", auth="b",
        )
        subs = store.list_active_push_subscriptions("tok1")
        assert len(subs) == 1

    def test_disable_subscription_filters_it_out(self, store):
        sid = store.insert_push_subscription(
            token="tok1", phone="+61400000001", singer_name="Alice",
            endpoint="https://fcm.googleapis.com/fcm/send/abc", p256dh="p", auth="a",
        )
        store.disable_push_subscription(sid)
        assert store.list_active_push_subscriptions("tok1") == []

    def test_update_last_sent_state(self, store):
        sid = store.insert_push_subscription(
            token="tok1", phone="+61400000001", singer_name="Alice",
            endpoint="ep", p256dh="p", auth="a",
        )
        store.update_push_sent_state(sid, {"entry_id": 5, "ladder_step": "up_next"})
        subs = store.list_active_push_subscriptions("tok1")
        assert json.loads(subs[0]["last_sent_state"])["ladder_step"] == "up_next"

    def test_find_subs_by_phone_scopes_to_token(self, store):
        store.insert_push_subscription(
            token="tok1", phone="+1", singer_name="A",
            endpoint="ep1", p256dh="p", auth="a",
        )
        store.insert_push_subscription(
            token="tok2", phone="+1", singer_name="A",
            endpoint="ep2", p256dh="p", auth="a",
        )
        assert len(store.find_subs_by_phone("tok1", "+1")) == 1
        assert len(store.find_subs_by_phone("tok2", "+1")) == 1
        assert len(store.find_subs_by_phone("tok1", "+2")) == 0

    def test_find_subs_by_phone_excludes_disabled(self, store):
        sid = store.insert_push_subscription(
            token="tok1", phone="+1", singer_name="A",
            endpoint="ep1", p256dh="p", auth="a",
        )
        store.disable_push_subscription(sid)
        assert store.find_subs_by_phone("tok1", "+1") == []

    def test_token_rotation_cleanup(self, store):
        # Old sub on tok1
        store.insert_push_subscription(
            token="tok1", phone="+1", singer_name="Old",
            endpoint="old-ep", p256dh="p", auth="a",
        )
        # Manually age the row by 8 days via updated_at (the new cleanup predicate)
        store._get_conn().execute(
            "UPDATE sing_push_subscriptions "
            "SET updated_at = datetime('now', '-8 days', 'localtime')"
        )
        store._get_conn().commit()
        # New sub on tok2 (today)
        store.insert_push_subscription(
            token="tok2", phone="+2", singer_name="New",
            endpoint="new-ep", p256dh="p", auth="a",
        )
        store.cleanup_stale_push_subscriptions(current_token="tok2")
        # old sub on tok1 (>7d) deleted; new sub on tok2 kept
        assert store.list_active_push_subscriptions("tok1") == []
        assert len(store.list_active_push_subscriptions("tok2")) == 1

    def test_cleanup_keeps_refreshed_old_sub(self, store):
        """A sub created long ago but recently refreshed (upsert) should survive."""
        store.insert_push_subscription(
            token="tok1", phone="+1", singer_name="R",
            endpoint="ep", p256dh="p", auth="a",
        )
        # Simulate: created_at 30 days ago, updated_at today (just upserted)
        store._get_conn().execute(
            "UPDATE sing_push_subscriptions "
            "SET created_at = datetime('now', '-30 days', 'localtime')"
        )
        store._get_conn().commit()
        store.cleanup_stale_push_subscriptions(current_token="tok2")
        # Even though it's 30 days old by created_at, updated_at is today → kept
        assert len(store.list_active_push_subscriptions("tok1")) == 1

    def test_disable_by_endpoint_helper(self, store):
        store.insert_push_subscription(
            token="tok1", phone="+1", singer_name="A",
            endpoint="ep1", p256dh="p", auth="a",
        )
        assert store.disable_push_subscription_by_endpoint("tok1", "ep1") is True
        assert store.list_active_push_subscriptions("tok1") == []

    def test_disable_by_endpoint_unknown_is_false(self, store):
        assert store.disable_push_subscription_by_endpoint("tok1", "never") is False

    def test_cleanup_preserves_recent_other_token_subs(self, store):
        """A sub on a different token that's still recent (<7d) should survive cleanup."""
        store.insert_push_subscription(
            token="tok1", phone="+1", singer_name="R",
            endpoint="ep", p256dh="p", auth="a",
        )
        store.cleanup_stale_push_subscriptions(current_token="tok2")
        # tok1 sub is recent, not deleted
        assert len(store.list_active_push_subscriptions("tok1")) == 1


# ---------------------------------------------------------------------------
# additional_singers JSON column round-trips
# ---------------------------------------------------------------------------

class TestAdditionalSingers:
    """additional_singers JSON column round-trips through create / get / update."""

    def _partners(self):
        return [
            {"name": "Sarah B.", "phone": "+61 400 111 222"},
            {"name": "Mike", "phone": ""},
        ]

    def test_create_with_partners_round_trips(self, store):
        req = store.create_request(
            singer_name="Alice",
            phone="+61 400 000 000",
            source_type="local",
            source_ref="/tmp/x.mp4",
            additional_singers=self._partners(),
        )
        assert req["additional_singers"] == self._partners()
        again = store.get_request(req["id"])
        assert again["additional_singers"] == self._partners()

    def test_create_without_partners_is_none(self, store):
        req = store.create_request(
            singer_name="Solo",
            phone="",
            source_type="local",
            source_ref="/tmp/y.mp4",
        )
        assert req["additional_singers"] is None

    def test_update_can_set_partners(self, store):
        req = store.create_request(
            singer_name="Alice", phone="", source_type="local",
            source_ref="/tmp/x.mp4",
        )
        updated = store.update_request(req["id"], additional_singers=self._partners())
        assert updated["additional_singers"] == self._partners()

    def test_update_can_clear_partners(self, store):
        req = store.create_request(
            singer_name="Alice", phone="", source_type="local",
            source_ref="/tmp/x.mp4",
            additional_singers=self._partners(),
        )
        cleared = store.update_request(req["id"], additional_singers=[])
        assert cleared["additional_singers"] == []

    def test_update_preserves_partners_when_not_passed(self, store):
        """Sentinel default: omitting additional_singers leaves it untouched."""
        req = store.create_request(
            singer_name="Alice", phone="", source_type="local",
            source_ref="/tmp/x.mp4",
            additional_singers=self._partners(),
        )
        updated = store.update_request(req["id"], singer_name="Alicia")
        assert updated["singer_name"] == "Alicia"
        assert updated["additional_singers"] == self._partners()


# ---------------------------------------------------------------------------
# edit_token + cancel (2026-07-09 self-service)
# ---------------------------------------------------------------------------

class TestEditTokenAndCancel:
    def test_create_request_mints_unique_edit_token(self, store):
        a = store.create_request(singer_name="Alice", phone="", source_type="local", source_ref="/a.mp4")
        b = store.create_request(singer_name="Bob", phone="", source_type="local", source_ref="/b.mp4")
        assert a["edit_token"] and isinstance(a["edit_token"], str)
        assert len(a["edit_token"]) >= 16
        assert a["edit_token"] != b["edit_token"]
        # Round-trips through get_request unchanged.
        assert store.get_request(a["id"])["edit_token"] == a["edit_token"]

    def test_mark_cancelled_sets_status(self, store):
        r = store.create_request(singer_name="Alice", phone="", source_type="local", source_ref="/a.mp4")
        out = store.mark_cancelled(r["id"])
        assert out["status"] == "cancelled"
        assert out["reviewed_at"]
        with pytest.raises(ValueError):
            store.mark_cancelled(999999)


class TestSupersedes:
    def test_supersedes_request_id_persists(self, store):
        orig = store.create_request(singer_name="Al", phone="", source_type="local", source_ref="/a.mp4")
        new = store.create_request(singer_name="Al", phone="", source_type="local", source_ref="/b.mp4",
                                   supersedes_request_id=orig["id"])
        assert new["supersedes_request_id"] == orig["id"]
        assert store.get_request(orig["id"])["supersedes_request_id"] is None
