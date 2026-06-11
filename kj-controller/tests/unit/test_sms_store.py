"""Unit tests for SmsStore — append-only SMS audit log."""

import pytest

from sms_store import SmsStore


@pytest.fixture
def store():
    s = SmsStore(":memory:")
    yield s
    s.close()


class TestSchema:
    def test_table_created(self, store):
        conn = store._get_conn()
        tables = {row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
        assert "sms_log" in tables

    def test_schema_idempotent(self, store):
        store.init_schema()
        store.init_schema()


class TestRecordSend:
    def _kwargs(self, **overrides):
        kwargs = {
            "rotation_entry_id": 64,
            "sing_request_id": 12,
            "phone_e164": "+18432594507",
            "body": "Hi Celeste! ...",
            "status": "sent",
            "telnyx_message_id": "msg_abc",
            "error": None,
            "kj_user_agent": "Mozilla/5.0",
        }
        kwargs.update(overrides)
        return kwargs

    def test_record_sent(self, store):
        row = store.record_send(**self._kwargs())
        assert row["id"] > 0
        assert row["status"] == "sent"
        assert row["phone_e164"] == "+18432594507"
        assert row["telnyx_message_id"] == "msg_abc"
        assert row["error"] is None
        assert row["sent_at"]

    def test_record_failed(self, store):
        row = store.record_send(**self._kwargs(
            status="failed", telnyx_message_id=None, error="HTTP 400",
        ))
        assert row["status"] == "failed"
        assert row["telnyx_message_id"] is None
        assert row["error"] == "HTTP 400"

    def test_invalid_status_raises(self, store):
        with pytest.raises(ValueError):
            store.record_send(**self._kwargs(status="queued"))

    def test_missing_phone_raises(self, store):
        with pytest.raises(ValueError):
            store.record_send(**self._kwargs(phone_e164=""))

    def test_missing_body_raises(self, store):
        with pytest.raises(ValueError):
            store.record_send(**self._kwargs(body=""))

    def test_null_rotation_entry_id_allowed(self, store):
        # Row may be deleted later — we still want the audit trail.
        row = store.record_send(**self._kwargs(rotation_entry_id=None))
        assert row["rotation_entry_id"] is None


class TestLookup:
    def test_get_latest_for_entry_none(self, store):
        assert store.get_latest_for_entry(64) is None

    def test_get_latest_for_entry_returns_most_recent(self, store):
        for i in range(3):
            store.record_send(
                rotation_entry_id=64, sing_request_id=12,
                phone_e164="+1", body=f"msg {i}", status="sent",
                telnyx_message_id=f"id_{i}",
            )
        latest = store.get_latest_for_entry(64)
        assert latest["telnyx_message_id"] == "id_2"

    def test_get_latest_for_entries_bulk(self, store):
        store.record_send(rotation_entry_id=1, sing_request_id=None,
                          phone_e164="+1", body="a", status="sent")
        store.record_send(rotation_entry_id=2, sing_request_id=None,
                          phone_e164="+1", body="b", status="failed",
                          error="oops")
        store.record_send(rotation_entry_id=1, sing_request_id=None,
                          phone_e164="+1", body="a2", status="sent")
        result = store.get_latest_for_entries([1, 2, 3])
        assert set(result.keys()) == {1, 2}  # entry 3 has no sends
        assert result[1]["body"] == "a2"
        assert result[2]["status"] == "failed"


class TestUpdateStatusByTelnyxId:
    def test_updates_matching_row(self, store):
        store.record_send(
            rotation_entry_id=64, sing_request_id=12, phone_e164="+1",
            body="hi", status="sent", telnyx_message_id="msg_abc",
        )
        updated = store.update_status_by_telnyx_id("msg_abc", "delivered")
        assert updated == 1
        row = store.get_latest_for_entry(64)
        assert row["status"] == "delivered"

    def test_updates_error_on_failure(self, store):
        store.record_send(
            rotation_entry_id=64, sing_request_id=12, phone_e164="+1",
            body="hi", status="sent", telnyx_message_id="msg_abc",
        )
        store.update_status_by_telnyx_id(
            "msg_abc", "delivery_failed", error="40010 not 10DLC-registered",
        )
        row = store.get_latest_for_entry(64)
        assert row["status"] == "delivery_failed"
        assert row["error"] == "40010 not 10DLC-registered"

    def test_no_match_returns_zero(self, store):
        assert store.update_status_by_telnyx_id("nope", "delivered") == 0

    def test_blank_message_id_returns_zero(self, store):
        # A failed send logs telnyx_message_id=None; a DLR with an empty id
        # must never blanket-match those NULL rows.
        store.record_send(
            rotation_entry_id=64, sing_request_id=12, phone_e164="+1",
            body="hi", status="failed", telnyx_message_id=None, error="x",
        )
        assert store.update_status_by_telnyx_id("", "delivered") == 0
        assert store.update_status_by_telnyx_id(None, "delivered") == 0


class TestOptOut:
    def test_record_then_is_opted_out(self, store):
        assert store.is_opted_out("+18432594507") is False
        store.record_opt_out("+18432594507", keyword="STOP")
        assert store.is_opted_out("+18432594507") is True

    def test_clear_opt_out(self, store):
        store.record_opt_out("+18432594507", keyword="STOP")
        store.clear_opt_out("+18432594507")
        assert store.is_opted_out("+18432594507") is False

    def test_record_opt_out_is_idempotent(self, store):
        store.record_opt_out("+18432594507", keyword="STOP")
        store.record_opt_out("+18432594507", keyword="STOP")
        assert store.is_opted_out("+18432594507") is True

    def test_unknown_phone_not_opted_out(self, store):
        assert store.is_opted_out("+19998887777") is False


class TestConcurrentWrites:
    def test_concurrent_writes_from_many_threads(self, tmp_path):
        """Regression guard for the 2026-05-01 shared-connection bug.

        New stores must use per-thread connections — proven by spinning up
        20 threads that each insert one row and verifying every insert
        succeeds inside busy_timeout.
        """
        import threading
        import time

        db_path = str(tmp_path / "rotation.db")
        s = SmsStore(db_path)
        try:
            errors = []

            def worker(idx):
                try:
                    s.record_send(
                        rotation_entry_id=idx,
                        sing_request_id=None,
                        phone_e164="+1",
                        body=f"msg {idx}",
                        status="sent",
                    )
                except Exception as exc:
                    errors.append((idx, str(exc)))

            threads = [threading.Thread(target=worker, args=(i,)) for i in range(20)]
            t0 = time.time()
            for t in threads: t.start()
            for t in threads: t.join(timeout=15)
            elapsed = time.time() - t0
            assert not errors, f"{len(errors)} writes raised, e.g. {errors[0]}"
            assert elapsed < 10
        finally:
            s.close()
