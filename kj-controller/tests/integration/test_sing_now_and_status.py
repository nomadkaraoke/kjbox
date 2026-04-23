"""Integration tests for /sing/now and the updated /sing/status response shape."""

import pytest


def _enable_requests(sing_app):
    """Helper: enable public requests against the ensure_token()-generated token."""
    sing_app.sing_store.set_enabled(True)


class TestSingNow:
    def test_requires_token(self, client):
        resp = client.get("/sing/now")
        assert resp.status_code == 403

    def test_empty_rotation(self, client, sing_app, token):
        _enable_requests(sing_app)
        resp = client.get(f"/sing/now?t={token}")
        assert resp.status_code == 200
        assert resp.get_json() == {
            "now_singing": None,
            "up_next": None,
            "queued_count": 0,
        }

    def test_with_rotation(self, client, sing_app, token):
        _enable_requests(sing_app)
        sing_app.rotation.add_entry("Sarah", song_artist="ABBA — Dancing Queen")
        sing_app.rotation.add_entry("Mike", song_artist="Eagles — Hotel California")
        entries = sing_app.rotation.get_rotation()
        sing_app.rotation.update_status(entries[0]["id"], "Now Singing")

        resp = client.get(f"/sing/now?t={token}")
        data = resp.get_json()
        assert data["now_singing"]["first_name"] == "Sarah"
        assert data["up_next"]["first_name"] == "Mike"
        assert data["queued_count"] == 2


class TestStatusResponseShape:
    def test_status_includes_estimate_and_now_playing(self, client, sing_app, token):
        _enable_requests(sing_app)
        req = sing_app.sing_store.create_request(
            singer_name="Alice", phone="+61400000001",
            song_artist="Queen", song_title="Bohemian Rhapsody",
            source_type="local", source_ref="/tmp/song.mp4", source_meta=None, notes="",
        )
        from routes import approve_sing_request
        entry_id = approve_sing_request(sing_app, req)
        sing_app.sing_store.mark_approved(req["id"], linked_entry_id=entry_id)

        resp = client.get(f"/sing/status/{req['id']}?t={token}")
        data = resp.get_json()
        assert "estimate" in data
        assert set(data["estimate"].keys()) >= {
            "position", "expected_s", "range_low_s", "range_high_s",
            "spread_source", "close_to_front", "now_singing",
        }
        assert "now_playing" in data
        assert "queue" in data  # still read by the client
