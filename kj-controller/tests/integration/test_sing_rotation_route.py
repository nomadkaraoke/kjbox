"""Integration tests for GET /sing/rotation."""

import pytest


def _enable_requests(sing_app):
    sing_app.sing_store.set_enabled(True)


class TestSingRotationRoute:
    def test_requires_token(self, client):
        resp = client.get("/sing/rotation")
        assert resp.status_code == 403

    def test_stale_token_rejected(self, client, sing_app, token):
        _enable_requests(sing_app)
        # Rotate the event token — the previously-handed-out one is now stale.
        sing_app.sing_store.regenerate_token()
        resp = client.get(f"/sing/rotation?t={token}")
        assert resp.status_code == 403

    def test_disabled_when_sing_not_enabled(self, client, sing_app, token):
        # Explicitly disable; SingStore defaults to enabled when the meta key
        # is unset, so we must force it off to exercise this path.
        sing_app.sing_store.set_enabled(False)
        resp = client.get(f"/sing/rotation?t={token}")
        assert resp.status_code == 403

    def test_empty_rotation(self, client, sing_app, token):
        _enable_requests(sing_app)
        resp = client.get(f"/sing/rotation?t={token}")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["entries"] == []
        assert data["spread_source"] == "fallback"

    def test_active_rotation_response_shape(self, client, sing_app, token):
        _enable_requests(sing_app)
        sing_app.rotation.add_entry("Sarah Smith", song_artist="ABBA — Dancing Queen")
        sing_app.rotation.add_entry("Mike", song_artist="Eagles — Hotel California")
        entries = sing_app.rotation.get_rotation()
        sing_app.rotation.update_status(entries[0]["id"], "Now Singing")

        resp = client.get(f"/sing/rotation?t={token}")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["spread_source"] in ("tonight", "fallback")
        assert len(data["entries"]) == 2
        first = data["entries"][0]
        assert first["position"] == 1
        assert first["first_name"] == "Sarah"  # last name dropped
        assert first["song_artist"] == "ABBA — Dancing Queen"
        assert first["now_singing"] is True
        assert "expected_s" in first
        assert "range_low_s" in first
        assert "range_high_s" in first

        second = data["entries"][1]
        assert second["position"] == 2
        assert second["first_name"] == "Mike"
        assert second["now_singing"] is False

    def test_done_and_left_filtered_out(self, client, sing_app, token):
        _enable_requests(sing_app)
        sing_app.rotation.add_entry("Alice", song_artist="Queen — Bohemian Rhapsody")
        sing_app.rotation.add_entry("Bob", song_artist="Pop")
        sing_app.rotation.add_entry("Carol", song_artist="Jazz")
        entries = sing_app.rotation.get_rotation()
        sing_app.rotation.update_status(entries[0]["id"], "Done")
        sing_app.rotation.update_status(entries[1]["id"], "Left")

        resp = client.get(f"/sing/rotation?t={token}")
        data = resp.get_json()
        assert len(data["entries"]) == 1
        assert data["entries"][0]["first_name"] == "Carol"
        assert data["entries"][0]["position"] == 1

    def test_first_name_only(self, client, sing_app, token):
        _enable_requests(sing_app)
        sing_app.rotation.add_entry("Jane Smith Doe", song_artist="Test")
        resp = client.get(f"/sing/rotation?t={token}")
        data = resp.get_json()
        assert data["entries"][0]["first_name"] == "Jane"
