"""Integration tests for the server-side undo/redo routes.

Uses the real in-memory RotationManager wired by the app factory (conftest's
``flask_app`` / ``flask_test_client``), so these exercise the genuine
checkpoint → preview → apply flow over HTTP.
"""

import json


def _undo_count(app):
    return app.rotation.store.history_counts()["undo"]


class TestRotationGetIncludesHistory:
    def test_get_rotation_exposes_rev_and_history(self, flask_app, flask_test_client):
        flask_app.rotation.add_entry("Alice", "Song A")
        resp = flask_test_client.get("/rotation")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "rev" in data
        assert "history" in data
        assert data["history"]["undo"] >= 1


class TestUndoRoute:
    def test_undo_without_confirm_previews_only(self, flask_app, flask_test_client):
        flask_app.rotation.add_entry("Alice", "Song A")
        flask_app.rotation.add_entry("Bob", "Song B")

        resp = flask_test_client.post(
            "/rotation/undo", data=json.dumps({}), content_type="application/json"
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["preview"] is True
        assert "diff" in data
        # Preview applies nothing — both singers still present.
        assert len(flask_app.rotation.store.get_entries(include_done=True)) == 2

    def test_undo_with_confirm_applies(self, flask_app, flask_test_client):
        flask_app.rotation.add_entry("Alice", "Song A")
        flask_app.rotation.add_entry("Bob", "Song B")

        resp = flask_test_client.post(
            "/rotation/undo",
            data=json.dumps({"confirm": True}),
            content_type="application/json",
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"] is True
        entries = flask_app.rotation.store.get_entries(include_done=True)
        assert len(entries) == 1
        assert entries[0]["singer"] == "Alice"

    def test_undo_empty_history_reports_empty(self, flask_app, flask_test_client):
        resp = flask_test_client.post(
            "/rotation/undo",
            data=json.dumps({"confirm": True}),
            content_type="application/json",
        )
        data = resp.get_json()
        assert data["success"] is False
        assert data["reason"] == "empty"


class TestBatchStatusAdvance:
    def test_batch_status_is_single_undo_step(self, flask_app, flask_test_client):
        flask_app.rotation.add_entry("Alice", "Song A")
        flask_app.rotation.add_entry("Bob", "Song B")
        before = flask_app.rotation.store.history_counts()["undo"]

        resp = flask_test_client.post(
            "/rotation/status",
            data=json.dumps({"updates": [
                {"id": 1, "status": "Now Singing"},
                {"id": 2, "status": "Up Next"},
            ]}),
            content_type="application/json",
        )
        assert resp.status_code == 200
        assert resp.get_json()["success"] is True
        after = flask_app.rotation.store.history_counts()["undo"]
        assert after == before + 1
        assert flask_app.rotation.store.get_entry(1)["status"] == "Now Singing"
        assert flask_app.rotation.store.get_entry(2)["status"] == "Up Next"


class TestRevGuard:
    def test_preview_includes_rev(self, flask_app, flask_test_client):
        flask_app.rotation.add_entry("Alice", "Song A")
        resp = flask_test_client.post(
            "/rotation/undo", data=json.dumps({}), content_type="application/json"
        )
        data = resp.get_json()
        assert data["preview"] is True
        assert "rev" in data

    def test_apply_with_stale_rev_is_rejected(self, flask_app, flask_test_client):
        flask_app.rotation.add_entry("Alice", "Song A")
        flask_app.rotation.add_entry("Bob", "Song B")
        # Preview to learn the current rev.
        preview = flask_test_client.post(
            "/rotation/undo", data=json.dumps({}), content_type="application/json"
        ).get_json()
        stale_rev = preview["rev"]
        # A concurrent change bumps the rev, making stale_rev out of date.
        flask_app.rotation.add_entry("Carol", "Song C")

        resp = flask_test_client.post(
            "/rotation/undo",
            data=json.dumps({"confirm": True, "expected_rev": stale_rev}),
            content_type="application/json",
        )
        data = resp.get_json()
        assert data["success"] is False
        assert data["reason"] == "stale"
        # Nothing applied — Carol still present.
        assert len(flask_app.rotation.store.get_entries(include_done=True)) == 3

    def test_apply_with_current_rev_succeeds(self, flask_app, flask_test_client):
        flask_app.rotation.add_entry("Alice", "Song A")
        flask_app.rotation.add_entry("Bob", "Song B")
        preview = flask_test_client.post(
            "/rotation/undo", data=json.dumps({}), content_type="application/json"
        ).get_json()
        resp = flask_test_client.post(
            "/rotation/undo",
            data=json.dumps({"confirm": True, "expected_rev": preview["rev"]}),
            content_type="application/json",
        )
        assert resp.get_json()["success"] is True


class TestRedoRoute:
    def test_redo_reapplies_after_undo(self, flask_app, flask_test_client):
        flask_app.rotation.add_entry("Alice", "Song A")
        flask_app.rotation.add_entry("Bob", "Song B")
        flask_app.rotation.undo()  # back to just Alice

        resp = flask_test_client.post(
            "/rotation/redo",
            data=json.dumps({"confirm": True}),
            content_type="application/json",
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"] is True
        entries = flask_app.rotation.store.get_entries(include_done=True)
        assert {e["singer"] for e in entries} == {"Alice", "Bob"}
