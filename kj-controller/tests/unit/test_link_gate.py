"""Unit tests for the inline playability hard-gate on /rotation/link."""

import json
import types
from unittest.mock import MagicMock, patch

import pytest

from app import create_app


# ---------------------------------------------------------------------------
# Fixtures — reuse the conftest.py mock_config; wire a minimal rotation mock.
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_rotation():
    """Minimal RotationManager mock sufficient for the link route."""
    rotation = MagicMock()
    rotation.get_rotation.return_value = [
        {
            "id": 1, "position": 1, "singer": "Alice",
            "song_artist": "Test Song", "status": "Waiting",
            "notes": "", "file_path": None, "duration": None,
        }
    ]
    rotation.store.get_songs_sung_counts.return_value = {}
    rotation.store.get_last_sang_times.return_value = {}
    rotation.store.get_rev.return_value = 0
    rotation.history_status.return_value = {
        "undo": 0, "redo": 0, "undo_label": None, "redo_label": None,
    }
    rotation.sync = None
    rotation.media = None
    return rotation


@pytest.fixture
def gate_app(mock_config, mock_rotation):
    """Flask app with a real rotation mock attached."""
    app = create_app(config=mock_config)
    app.rotation = mock_rotation
    app.config["TESTING"] = True
    yield app
    app.catalog.close()


@pytest.fixture
def gate_client(gate_app):
    with gate_app.test_client() as client:
        yield client


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestLinkPlayabilityGate:
    def test_link_blocked_when_unplayable(self, gate_client):
        """422 with error + verdict when the gate says overall_ok=False."""
        bad_result = types.SimpleNamespace(
            verdict={"overall_ok": False, "reasons": ["no video stream"]}
        )
        with patch("routes._playability_gate", return_value=bad_result):
            resp = gate_client.post(
                "/rotation/link",
                data=json.dumps({"id": 1, "file_path": "/media/bad.mp4"}),
                content_type="application/json",
            )
        assert resp.status_code == 422
        body = resp.get_json()
        assert "no video stream" in body["error"]
        assert "verdict" in body

    def test_link_allowed_when_playable(self, gate_client, mock_rotation):
        """200 and link_file called exactly once when gate says overall_ok=True."""
        good_result = types.SimpleNamespace(
            verdict={"overall_ok": True, "reasons": []}
        )
        with patch("routes._playability_gate", return_value=good_result), \
             patch("routes._resolve_or_create_rotation_entry_id", return_value=(1, None)):
            resp = gate_client.post(
                "/rotation/link",
                data=json.dumps({"id": 1, "file_path": "/media/good.mp4"}),
                content_type="application/json",
            )
        assert resp.status_code == 200
        mock_rotation.link_file.assert_called_once_with(1, "/media/good.mp4")
