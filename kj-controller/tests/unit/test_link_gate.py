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
    rotation.store.get_first_entered_times.return_value = {}
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

    def test_link_enqueues_tier2_render_check(self, gate_client, mock_rotation):
        """A successful link schedules a background tier-2 render check."""
        good_result = types.SimpleNamespace(
            verdict={"overall_ok": True, "reasons": []}
        )
        with patch("routes._playability_gate", return_value=good_result), \
             patch("routes._resolve_or_create_rotation_entry_id", return_value=(1, None)), \
             patch("routes._enqueue_tier2") as enq:
            gate_client.post(
                "/rotation/link",
                data=json.dumps({"id": 1, "file_path": "/media/good.mp4"}),
                content_type="application/json",
            )
        enq.assert_called_once()
        # entry_id + file_path are threaded through (app is positional first).
        assert enq.call_args.args[1:] == (1, "/media/good.mp4")

    def test_link_does_not_enqueue_tier2_when_blocked(self, gate_client):
        """A gate-blocked (422) link never schedules a tier-2 check."""
        bad_result = types.SimpleNamespace(
            verdict={"overall_ok": False, "reasons": ["no video stream"]}
        )
        with patch("routes._playability_gate", return_value=bad_result), \
             patch("routes._enqueue_tier2") as enq:
            gate_client.post(
                "/rotation/link",
                data=json.dumps({"id": 1, "file_path": "/media/bad.mp4"}),
                content_type="application/json",
            )
        enq.assert_not_called()


class TestLinkLibraryMaterialization:
    """Linking an SSD path schedules background identity materialization (D3.3)."""

    def test_link_materializes_library_row_async(self, gate_client, mock_rotation):
        import routes
        good_result = types.SimpleNamespace(verdict={"overall_ok": True, "reasons": []})
        gate_client.application.kj_config["external_media_mount"] = "/media/nomad/Nomad4TBOne"
        with patch("routes._playability_gate", return_value=good_result), \
             patch("routes._resolve_or_create_rotation_entry_id", return_value=(1, None)), \
             patch("routes._enqueue_tier2"), \
             patch("routes.library_media.run_async") as run_async:
            resp = gate_client.post(
                "/rotation/link",
                data=json.dumps({"id": 1,
                                 "file_path": "/media/nomad/Nomad4TBOne/Discs/a.zip"}),
                content_type="application/json",
            )
        assert resp.status_code == 200
        args = run_async.call_args[0]
        assert args[0] is routes.library_media.ensure_library_row_for_app
        assert args[2] == "/media/nomad/Nomad4TBOne/Discs/a.zip"

    def test_link_non_library_path_no_materialization(self, gate_client, mock_rotation):
        good_result = types.SimpleNamespace(verdict={"overall_ok": True, "reasons": []})
        gate_client.application.kj_config["external_media_mount"] = "/media/nomad/Nomad4TBOne"
        with patch("routes._playability_gate", return_value=good_result), \
             patch("routes._resolve_or_create_rotation_entry_id", return_value=(1, None)), \
             patch("routes._enqueue_tier2"), \
             patch("routes.library_media.run_async") as run_async:
            gate_client.post(
                "/rotation/link",
                data=json.dumps({"id": 1, "file_path": "/opt/nomad/downloads/a.mp4"}),
                content_type="application/json",
            )
        run_async.assert_not_called()

    def test_link_materialization_error_does_not_fail_link(self, gate_client, mock_rotation):
        """A post-link materialization failure must not turn a 200 into a 500."""
        good_result = types.SimpleNamespace(verdict={"overall_ok": True, "reasons": []})
        gate_client.application.kj_config["external_media_mount"] = "/media/nomad/Nomad4TBOne"
        with patch("routes._playability_gate", return_value=good_result), \
             patch("routes._resolve_or_create_rotation_entry_id", return_value=(1, None)), \
             patch("routes._enqueue_tier2"), \
             patch("routes.library_media.run_async", side_effect=RuntimeError("thread pool down")):
            resp = gate_client.post(
                "/rotation/link",
                data=json.dumps({"id": 1,
                                 "file_path": "/media/nomad/Nomad4TBOne/Discs/a.zip"}),
                content_type="application/json",
            )
        assert resp.status_code == 200



_NFC = "/media/nomad/SSD/PMK-0000 - Band (Feat. Mon\u00e1e) - Song.mp4"


def _only_nfc_exists(path):
    # Linux (ext4/exfat-on-Linux) compares raw bytes; macOS APFS would not,
    # so emulate the Linux drive instead of touching the real filesystem.
    return path == _NFC


class TestLinkUnicodeNormalization:
    """Index paths can be NFD while the drive holds NFC (2,823 SSD songs, 2026-11-20)."""

    def test_link_uses_on_disk_spelling_of_nfd_path(self, gate_client, mock_rotation):
        import unicodedata
        nfd_path = unicodedata.normalize("NFD", _NFC)
        assert nfd_path != _NFC
        good_result = types.SimpleNamespace(verdict={"overall_ok": True, "reasons": []})
        with patch("utils.os.path.exists", side_effect=_only_nfc_exists), \
             patch("routes._playability_gate", return_value=good_result) as gate, \
             patch("routes._resolve_or_create_rotation_entry_id", return_value=(1, None)), \
             patch("routes._enqueue_tier2"):
            resp = gate_client.post(
                "/rotation/link",
                data=json.dumps({"id": 1, "file_path": nfd_path}),
                content_type="application/json",
            )
        assert resp.status_code == 200
        gate.assert_called_once_with(_NFC)
        mock_rotation.link_file.assert_called_once_with(1, _NFC)


def test_existing_unicode_variant():
    import unicodedata
    from utils import existing_unicode_variant
    nfd = unicodedata.normalize("NFD", _NFC)
    with patch("utils.os.path.exists", side_effect=_only_nfc_exists):
        assert existing_unicode_variant(nfd) == _NFC
        assert existing_unicode_variant(_NFC) == _NFC
        assert existing_unicode_variant("/nope/caf\u00e9.mp4") == "/nope/caf\u00e9.mp4"
        assert existing_unicode_variant("") == ""
