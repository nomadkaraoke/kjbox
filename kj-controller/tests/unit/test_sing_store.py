"""Unit tests for SingStore — public request storage + event token helpers."""

import json

import pytest

from sing_store import (
    SingStore,
    TOKEN_KEY,
    ENABLED_KEY,
    AUTO_APPROVE_KEY,
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
            "reviewed_at", "linked_entry_id",
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
        assert len(tok) >= 12
        assert store.get_token() == tok

    def test_regenerate_changes_token(self, store):
        t1 = store.regenerate_token()
        t2 = store.regenerate_token()
        assert t1 != t2
        assert store.get_token() == t2

    def test_ensure_token_creates_if_absent(self, store):
        assert store.get_token() is None
        tok = store.ensure_token()
        assert tok == store.get_token()

    def test_ensure_token_preserves_existing(self, store):
        tok = store.regenerate_token()
        assert store.ensure_token() == tok

    def test_enabled_default_true(self, store):
        assert store.is_enabled() is True

    def test_enabled_round_trip(self, store):
        store.set_enabled(False)
        assert store.is_enabled() is False
        store.set_enabled(True)
        assert store.is_enabled() is True

    def test_auto_approve_default_false(self, store):
        assert store.is_auto_approve() is False

    def test_auto_approve_round_trip(self, store):
        store.set_auto_approve(True)
        assert store.is_auto_approve() is True
        store.set_auto_approve(False)
        assert store.is_auto_approve() is False

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

    def test_missing_phone_raises(self, store):
        with pytest.raises(ValueError):
            store.create_request(singer_name="Andrew", phone="", source_type="local")

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
