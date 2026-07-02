"""Tests for the download-and-link rotation endpoint."""

import json
import time
from unittest.mock import MagicMock, patch

import pytest

from app import create_app


@pytest.fixture
def dl_app(mock_config):
    mock_config["rotation_db_path"] = ":memory:"
    app = create_app(config=mock_config)
    app.config['TESTING'] = True
    yield app
    app.catalog.close()


@pytest.fixture
def dl_client(dl_app):
    with dl_app.test_client() as client:
        yield client


class TestDownloadAndLink:
    def test_divebar_download(self, dl_client, dl_app):
        """Divebar download sets download_source and queues download."""
        # First add a singer
        resp = dl_client.post('/rotation/add',
            data=json.dumps({"singer": "Alice", "song_artist": "Bohemian Rhapsody"}),
            content_type='application/json')
        entry_id = resp.get_json()["entry"]["id"]

        with patch('routes.divebar.get_download_url', return_value="https://storage.googleapis.com/test"):
            resp = dl_client.post('/rotation/download-and-link',
                data=json.dumps({
                    "id": entry_id,
                    "source": "divebar",
                    "file_id": "abc123",
                    "filename": "KFN-1234 - Queen - Bohemian Rhapsody.mp4"
                }),
                content_type='application/json')
            assert resp.status_code == 200
            data = resp.get_json()
            assert data["entry"]["download_source"] == "divebar"
            assert data["entry"]["download_status"] == "queued"

    def test_divebar_download_classifies_gcs_url(self, dl_client, dl_app):
        """Queue item carries source_detail='gcs' when divebar returns a GCS URL."""
        resp = dl_client.post('/rotation/add',
            data=json.dumps({"singer": "Alice", "song_artist": "Song"}),
            content_type='application/json')
        entry_id = resp.get_json()["entry"]["id"]
        with patch('routes.divebar.get_download_url',
                   return_value="https://storage.googleapis.com/divebar-mirror/x.mp4"):
            dl_client.post('/rotation/download-and-link',
                data=json.dumps({"id": entry_id, "source": "divebar",
                                 "file_id": "abc", "filename": "x.mp4"}),
                content_type='application/json')
        items = dl_app.download_queue['items']
        assert items[-1]['source_detail'] == 'gcs'

    def test_divebar_download_classifies_drive_url(self, dl_client, dl_app):
        """Queue item carries source_detail='drive' when divebar returns a Drive URL."""
        resp = dl_client.post('/rotation/add',
            data=json.dumps({"singer": "Alice", "song_artist": "Song"}),
            content_type='application/json')
        entry_id = resp.get_json()["entry"]["id"]
        with patch('routes.divebar.get_download_url',
                   return_value="https://drive.google.com/uc?export=download&id=xyz"):
            dl_client.post('/rotation/download-and-link',
                data=json.dumps({"id": entry_id, "source": "divebar",
                                 "file_id": "xyz", "filename": "x.mp4"}),
                content_type='application/json')
        items = dl_app.download_queue['items']
        assert items[-1]['source_detail'] == 'drive'

    def test_youtube_download(self, dl_client, dl_app):
        """YouTube download sets download_source and queues download."""
        resp = dl_client.post('/rotation/add',
            data=json.dumps({"singer": "Bob", "song_artist": "Song B"}),
            content_type='application/json')
        entry_id = resp.get_json()["entry"]["id"]

        resp = dl_client.post('/rotation/download-and-link',
            data=json.dumps({
                "id": entry_id,
                "source": "youtube",
                "youtube_url": "https://youtube.com/watch?v=abc",
                "filename": "KV-5678 - Queen - Bohemian Rhapsody.mp4"
            }),
            content_type='application/json')
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["entry"]["download_source"] == "youtube"

    def test_missing_source_returns_400(self, dl_client, dl_app):
        resp = dl_client.post('/rotation/add',
            data=json.dumps({"singer": "Alice"}),
            content_type='application/json')
        entry_id = resp.get_json()["entry"]["id"]

        resp = dl_client.post('/rotation/download-and-link',
            data=json.dumps({"id": entry_id}),
            content_type='application/json')
        assert resp.status_code == 400

    def test_missing_id_and_singer_returns_400(self, dl_client):
        resp = dl_client.post('/rotation/download-and-link',
            data=json.dumps({"source": "divebar", "file_id": "abc"}),
            content_type='application/json')
        assert resp.status_code == 400

    def test_creates_entry_if_singer_provided(self, dl_client, dl_app):
        """If id is omitted but singer provided, creates entry first."""
        with patch('routes.divebar.get_download_url', return_value="https://storage.googleapis.com/test"):
            resp = dl_client.post('/rotation/download-and-link',
                data=json.dumps({
                    "source": "divebar",
                    "file_id": "abc123",
                    "filename": "KFN-1234 - Queen - Bohemian Rhapsody.mp4",
                    "singer": "Alice",
                    "song_artist": "Bohemian Rhapsody - Queen"
                }),
                content_type='application/json')
            assert resp.status_code == 200
            assert resp.get_json()["entry"]["singer"] == "Alice"

    def test_creates_entry_from_singers_array(self, dl_client, dl_app):
        """Regression: if id is omitted and singers (plural) array is sent
        instead of singer (singular), the entry is still created. The UI sends
        `singers` from the pill input, so this must work or the user's entry
        is silently lost."""
        with patch('routes.divebar.get_download_url', return_value="https://storage.googleapis.com/test"):
            resp = dl_client.post('/rotation/download-and-link',
                data=json.dumps({
                    "source": "divebar",
                    "file_id": "abc123",
                    "filename": "KFN-1234 - Queen - Bohemian Rhapsody.mp4",
                    "singers": ["Alice"],
                    "song_artist": "Bohemian Rhapsody - Queen"
                }),
                content_type='application/json')
            assert resp.status_code == 200
            assert resp.get_json()["entry"]["singer"] == "Alice"

    def test_creates_multi_singer_entry_from_singers_array(self, dl_client, dl_app):
        """Multi-singer add mode: singers list joined with ' & ' for display."""
        with patch('routes.divebar.get_download_url', return_value="https://storage.googleapis.com/test"):
            resp = dl_client.post('/rotation/download-and-link',
                data=json.dumps({
                    "source": "youtube",
                    "youtube_url": "https://youtube.com/watch?v=abc",
                    "filename": "KV-1 - Queen - Duet.mp4",
                    "singers": ["Phil", "Anya"],
                    "song_artist": "Duet Song"
                }),
                content_type='application/json')
            assert resp.status_code == 200
            entry = resp.get_json()["entry"]
            assert entry["singer"] == "Phil & Anya"
            # singers_json persisted so per-singer sung counts work
            assert entry.get("singers_json") == '["Phil", "Anya"]'

    def test_unknown_source_returns_400(self, dl_client, dl_app):
        resp = dl_client.post('/rotation/add',
            data=json.dumps({"singer": "Alice"}),
            content_type='application/json')
        entry_id = resp.get_json()["entry"]["id"]

        resp = dl_client.post('/rotation/download-and-link',
            data=json.dumps({"id": entry_id, "source": "invalid"}),
            content_type='application/json')
        assert resp.status_code == 400


class TestRotationDownloadStateSync:
    """Rotation entry's download_status must mirror the queue item across all
    transitions (success, failure, cancel, dismiss) so UI actions never get stuck.

    These tests drive the sync helpers directly instead of racing the background
    download worker thread — the helpers are the contract that the worker and
    the cancel/ack routes both depend on, and testing them synchronously makes
    the assertions deterministic.
    """

    def _setup_rotation_download(self, dl_app, singer="Alice"):
        """Create a rotation entry + matching download-queue item.

        Mirrors what /rotation/download-and-link does, minus the worker
        dispatch. Returns (entry_id, queue_item).
        """
        entry = dl_app.rotation.add_entry(singer, "Some Song")
        download_id = f"dl-{singer}"
        dl_app.rotation.set_download_status(
            entry["id"], "youtube", "queued", download_id)
        queue_item = {
            'id': download_id,
            'url': 'https://example.com',
            'title': 'x.mp4',
            'source': 'youtube',
            'status': 'queued',
            'error': None,
            'rotation_entry_id': entry["id"],
        }
        dl_app.download_queue['items'].append(queue_item)
        return entry["id"], queue_item

    def test_sync_mirrors_downloading(self, dl_app):
        from routes import _sync_rotation_download
        entry_id, item = self._setup_rotation_download(dl_app)

        item['status'] = 'downloading'
        _sync_rotation_download(dl_app, item)

        assert dl_app.rotation.store.get_entry(entry_id)["download_status"] == "downloading"

    def test_sync_mirrors_error_as_failed(self, dl_app):
        """THE BUG: worker was setting queue item to 'error' but leaving the
        rotation entry at 'queued' — the UI then hid all action buttons
        because download_status != 'failed'."""
        from routes import _sync_rotation_download
        entry_id, item = self._setup_rotation_download(dl_app)

        item['status'] = 'error'
        _sync_rotation_download(dl_app, item)

        assert dl_app.rotation.store.get_entry(entry_id)["download_status"] == "failed"

    def test_sync_noop_when_download_id_mismatch(self, dl_app):
        """A stale item from a previous download must not clobber the entry's
        current download_id (e.g. when a retry has been queued)."""
        from routes import _sync_rotation_download
        entry_id, item = self._setup_rotation_download(dl_app)

        # Simulate a retry: entry now tracks a different download_id.
        dl_app.rotation.set_download_status(
            entry_id, "youtube", "queued", "dl-retry")

        # Stale first item's error callback fires late.
        item['status'] = 'error'
        _sync_rotation_download(dl_app, item)

        entry = dl_app.rotation.store.get_entry(entry_id)
        assert entry["download_status"] == "queued"  # unchanged by stale sync
        assert entry["download_id"] == "dl-retry"

    def test_clear_removes_rotation_download_fields(self, dl_app):
        from routes import _clear_rotation_download_for_item
        entry_id, item = self._setup_rotation_download(dl_app)

        _clear_rotation_download_for_item(dl_app, item)

        entry = dl_app.rotation.store.get_entry(entry_id)
        assert entry["download_status"] is None
        assert entry["download_id"] is None
        assert entry["download_source"] is None

    def test_clear_noop_when_download_id_mismatch(self, dl_app):
        """Dismissing a stale errored item mustn't wipe a fresh retry's state."""
        from routes import _clear_rotation_download_for_item
        entry_id, item = self._setup_rotation_download(dl_app)
        dl_app.rotation.set_download_status(
            entry_id, "youtube", "queued", "dl-retry")

        _clear_rotation_download_for_item(dl_app, item)

        entry = dl_app.rotation.store.get_entry(entry_id)
        assert entry["download_status"] == "queued"
        assert entry["download_id"] == "dl-retry"

    def test_cancel_route_clears_rotation_entry(self, dl_client, dl_app):
        """POST /download/cancel of a rotation-linked queued item clears
        the entry's download fields."""
        entry_id, item = self._setup_rotation_download(dl_app, "Bob")

        resp = dl_client.post('/download/cancel',
            data=json.dumps({"id": item['id']}),
            content_type='application/json')
        assert resp.status_code == 200

        entry = dl_app.rotation.store.get_entry(entry_id)
        assert entry["download_status"] is None
        assert entry["download_id"] is None

    def test_ack_route_clears_errored_rotation_entry(self, dl_client, dl_app):
        """POST /download/ack of a rotation-linked errored item clears the
        stuck rotation entry (this is the user-initiated escape hatch)."""
        entry_id, item = self._setup_rotation_download(dl_app, "Carol")
        # Simulate worker having run: queue item errored, entry marked failed.
        item['status'] = 'error'
        dl_app.rotation.set_download_status(
            entry_id, "youtube", "failed", item['id'])

        resp = dl_client.post('/download/ack',
            data=json.dumps({"id": item['id']}),
            content_type='application/json')
        assert resp.status_code == 200

        entry = dl_app.rotation.store.get_entry(entry_id)
        assert entry["download_status"] is None
        assert entry["download_id"] is None

    def test_download_and_link_response_shows_queued_state(self, dl_client, dl_app, mocker):
        """The route response must reflect the 'queued' state the caller just
        requested, even though the worker thread may race to transition it."""
        # Stub out the worker so the response is the only thing under test.
        mocker.patch('routes._download_worker', lambda app: None)
        resp = dl_client.post('/rotation/add',
            data=json.dumps({"singer": "Dave", "song_artist": "Song"}),
            content_type='application/json')
        entry_id = resp.get_json()["entry"]["id"]

        resp = dl_client.post('/rotation/download-and-link',
            data=json.dumps({
                "id": entry_id, "source": "youtube",
                "youtube_url": "https://youtube.com/watch?v=x",
                "filename": "x.mp4",
            }),
            content_type='application/json')
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["entry"]["download_status"] == "queued"
        assert data["entry"]["download_id"] is not None


class TestDownloadAndLinkDivebarFilename:
    """Server-side filename construction for /rotation/download-and-link
    when source=divebar."""

    def test_builds_filename_from_structured_fields(self, dl_client, dl_app):
        resp = dl_client.post('/rotation/add',
            data=json.dumps({"singer": "Alice", "song_artist": "Bohemian Rhapsody"}),
            content_type='application/json')
        entry_id = resp.get_json()["entry"]["id"]
        with patch('routes.divebar.get_download_url',
                   return_value="https://storage.googleapis.com/m/x.mp4"):
            dl_client.post('/rotation/download-and-link',
                data=json.dumps({
                    "id": entry_id, "source": "divebar", "file_id": "abc",
                    "artist": "Queen", "title": "Bohemian Rhapsody",
                    "brand_code": "WTF",
                }),
                content_type='application/json')
        items = dl_app.download_queue['items']
        assert items[-1]['title'] == "WTF - Queen - Bohemian Rhapsody.mp4"
        assert items[-1]['divebar_file_id'] == "abc"

    def test_falls_back_to_db_when_brand_missing(self, dl_client, dl_app):
        resp = dl_client.post('/rotation/add',
            data=json.dumps({"singer": "Bob", "song_artist": "Song"}),
            content_type='application/json')
        entry_id = resp.get_json()["entry"]["id"]
        with patch('routes.divebar.get_download_url',
                   return_value="https://storage.googleapis.com/m/x.mp4"):
            dl_client.post('/rotation/download-and-link',
                data=json.dumps({
                    "id": entry_id, "source": "divebar", "file_id": "abc",
                    "artist": "Queen", "title": "Bohemian Rhapsody",
                }),
                content_type='application/json')
        items = dl_app.download_queue['items']
        assert items[-1]['title'] == "DB - Queen - Bohemian Rhapsody.mp4"

    def test_falls_back_to_file_id_when_no_metadata(self, dl_client, dl_app):
        resp = dl_client.post('/rotation/add',
            data=json.dumps({"singer": "Carol", "song_artist": "X"}),
            content_type='application/json')
        entry_id = resp.get_json()["entry"]["id"]
        with patch('routes.divebar.get_download_url',
                   return_value="https://storage.googleapis.com/m/x.mp4"):
            dl_client.post('/rotation/download-and-link',
                data=json.dumps({
                    "id": entry_id, "source": "divebar", "file_id": "xyz",
                }),
                content_type='application/json')
        items = dl_app.download_queue['items']
        assert items[-1]['title'] == "divebar-xyz.mp4"

    def test_zip_url_produces_zip_extension(self, dl_client, dl_app):
        # CDG+MP3 zip must land as .zip so the gate classifies it as cdg_zip.
        resp = dl_client.post('/rotation/add',
            data=json.dumps({"singer": "Dan", "song_artist": "Admiration"}),
            content_type='application/json')
        entry_id = resp.get_json()["entry"]["id"]
        with patch('routes.divebar.get_download_url',
                   return_value="https://storage.googleapis.com/m/CKK%20-%20Incubus%20-%20Admiration.zip"):
            dl_client.post('/rotation/download-and-link',
                data=json.dumps({
                    "id": entry_id, "source": "divebar", "file_id": "z9",
                    "artist": "Incubus", "title": "Admiration", "brand_code": "CKK",
                }),
                content_type='application/json')
        items = dl_app.download_queue['items']
        assert items[-1]['title'] == "CKK - Incubus - Admiration.zip"

    def test_drive_url_uses_format_for_extension(self, dl_client, dl_app):
        resp = dl_client.post('/rotation/add',
            data=json.dumps({"singer": "Eve", "song_artist": "Y"}),
            content_type='application/json')
        entry_id = resp.get_json()["entry"]["id"]
        with patch('routes.divebar.get_download_url',
                   return_value="https://drive.google.com/uc?export=download&id=d2"):
            dl_client.post('/rotation/download-and-link',
                data=json.dumps({
                    "id": entry_id, "source": "divebar", "file_id": "d2",
                    "artist": "A", "title": "B", "brand_code": "RSK", "format": "zip",
                }),
                content_type='application/json')
        items = dl_app.download_queue['items']
        assert items[-1]['title'] == "RSK - A - B.zip"


class TestDownloadAndLinkCdgPairing:
    """A loose CDG track (format=cdg) must be paired with its sibling audio and
    queued as a cdg+mp3 zip — never a bare, silent .cdg."""

    GCS = "https://storage.googleapis.com/divebar-mirror/"

    def _add_singer(self, dl_client, name="Alice", song="ABBA - Dancing Queen"):
        resp = dl_client.post('/rotation/add',
            data=json.dumps({"singer": name, "song_artist": song}),
            content_type='application/json')
        return resp.get_json()["entry"]["id"]

    def test_cdg_pairs_with_sibling_audio(self, dl_client, dl_app):
        entry_id = self._add_singer(dl_client)

        def fake_url(file_id, *a, **k):
            return self.GCS + ("sdk.cdg" if file_id == "cdg_fid" else "sdk.mp3")

        with patch('routes._download_worker'), \
             patch('routes.divebar.get_download_url', side_effect=fake_url), \
             patch('routes.divebar.find_sibling_audio',
                   return_value={"file_id": "mp3_fid", "format": "mp3"}):
            resp = dl_client.post('/rotation/download-and-link',
                data=json.dumps({
                    "id": entry_id, "source": "divebar", "file_id": "cdg_fid",
                    "format": "cdg", "artist": "ABBA", "title": "Dancing Queen",
                    "brand_code": "SDK",
                }), content_type='application/json')

        assert resp.status_code == 200
        item = dl_app.download_queue['items'][-1]
        assert item.get('pair') is True
        assert item['cdg_url'].endswith("sdk.cdg")
        assert item['mp3_url'].endswith("sdk.mp3")
        assert item['title'].endswith(".zip")

    def test_cdg_without_sibling_audio_is_rejected_and_not_queued(self, dl_client, dl_app):
        entry_id = self._add_singer(dl_client, name="Bob", song="Orphan")
        before = len(dl_app.download_queue['items'])

        with patch('routes._download_worker'), \
             patch('routes.divebar.get_download_url', return_value=self.GCS + "x.cdg"), \
             patch('routes.divebar.find_sibling_audio', return_value=None):
            resp = dl_client.post('/rotation/download-and-link',
                data=json.dumps({
                    "id": entry_id, "source": "divebar", "file_id": "cdg_fid",
                    "format": "cdg", "artist": "Nobody", "title": "Orphan",
                    "brand_code": "SDK",
                }), content_type='application/json')

        assert resp.status_code == 422
        assert "audio" in (resp.get_json().get("error") or "").lower()
        assert len(dl_app.download_queue['items']) == before

    def test_worker_dispatches_pair_item_to_download_cdg_pair(self, dl_app, mocker):
        import routes
        mocker.patch.object(dl_app.media, 'download_cdg_pair',
                            return_value=("/tmp/foo.zip", "foo"))
        mocker.patch.object(dl_app.media, 'download_from_url',
                            return_value=("/tmp/should_not.mp4", "x"))
        dl_app.download_queue['items'] = [{
            'id': 'd1', 'pair': True,
            'cdg_url': 'http://gcs/cdg', 'mp3_url': 'http://gcs/mp3',
            'title': 'SDK - ABBA - Dancing Queen.zip',
            'source': 'divebar', 'status': 'queued', 'error': None,
            'rotation_entry_id': None,
        }]
        dl_app.download_queue['worker_running'] = True

        routes._download_worker(dl_app)

        dl_app.media.download_cdg_pair.assert_called_once_with(
            'http://gcs/cdg', 'http://gcs/mp3', filename='SDK - ABBA - Dancing Queen.zip',
            source='community', source_ref=None, artist=None, title=None)
        dl_app.media.download_from_url.assert_not_called()
