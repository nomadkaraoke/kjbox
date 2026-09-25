"""Tests for GenPoller — background gen job status polling."""

from unittest.mock import MagicMock, patch

import pytest

from gen_poller import GenPoller


@pytest.fixture
def mock_gen_client():
    return MagicMock()


@pytest.fixture
def mock_rotation():
    return MagicMock()


@pytest.fixture
def mock_media():
    return MagicMock()


@pytest.fixture
def poller(mock_gen_client, mock_rotation, mock_media):
    return GenPoller(mock_gen_client, mock_rotation, mock_media, "/tmp/downloads", poll_interval=1)


class TestPollOnce:
    def test_no_active_entries(self, poller, mock_rotation):
        mock_rotation.store.get_active_gen_entries.return_value = []
        poller.poll_once()
        mock_rotation.store.get_active_gen_entries.assert_called_once()

    def test_updates_status(self, poller, mock_gen_client, mock_rotation):
        mock_rotation.store.get_active_gen_entries.return_value = [
            {"id": 1, "gen_job_id": "job-1", "gen_status": "processing", "song_artist": "Song - Artist"}
        ]
        mock_gen_client.get_job_status.return_value = {"status": "awaiting_review"}
        poller.poll_once()
        mock_rotation.set_gen_status.assert_called_once_with(1, "job-1", "awaiting_review")

    def test_no_update_when_status_same(self, poller, mock_gen_client, mock_rotation):
        mock_rotation.store.get_active_gen_entries.return_value = [
            {"id": 1, "gen_job_id": "job-1", "gen_status": "processing", "song_artist": "Song - Artist"}
        ]
        mock_gen_client.get_job_status.return_value = {"status": "transcribing"}  # maps to processing
        poller.poll_once()
        mock_rotation.set_gen_status.assert_not_called()

    def test_handles_api_error(self, poller, mock_gen_client, mock_rotation):
        mock_rotation.store.get_active_gen_entries.return_value = [
            {"id": 1, "gen_job_id": "job-1", "gen_status": "processing", "song_artist": "Song - Artist"}
        ]
        mock_gen_client.get_job_status.side_effect = Exception("API error")
        poller.poll_once()  # Should not raise


class TestHandleComplete:
    def test_downloads_and_links(self, poller, mock_gen_client, mock_rotation, mock_media):
        entry = {"id": 1, "gen_job_id": "job-123", "gen_status": "rendering",
                 "song_artist": "Bohemian Rhapsody - Queen"}

        mock_rotation.store.get_active_gen_entries.return_value = [entry]
        mock_gen_client.get_job_status.return_value = {"status": "complete"}
        mock_gen_client.get_download_url.return_value = "https://api.example.com/download"
        mock_media.download_from_url.return_value = ("/tmp/downloads/song.mp4", "song.mp4")

        poller.poll_once()

        mock_gen_client.get_download_url.assert_called_once_with("job-123")
        mock_media.download_from_url.assert_called_once()
        mock_rotation.complete_gen_job.assert_called_once_with("job-123", "/tmp/downloads/song.mp4")

    def test_handles_no_download_url(self, poller, mock_gen_client, mock_rotation, mock_media):
        entry = {"id": 1, "gen_job_id": "job-123", "gen_status": "rendering",
                 "song_artist": "Song - Artist"}

        mock_rotation.store.get_active_gen_entries.return_value = [entry]
        mock_gen_client.get_job_status.return_value = {"status": "complete"}
        mock_gen_client.get_download_url.return_value = None

        poller.poll_once()
        mock_media.download_from_url.assert_not_called()

    def test_handles_download_failure(self, poller, mock_gen_client, mock_rotation, mock_media):
        entry = {"id": 1, "gen_job_id": "job-123", "gen_status": "rendering",
                 "song_artist": "Song - Artist"}

        mock_rotation.store.get_active_gen_entries.return_value = [entry]
        mock_gen_client.get_job_status.return_value = {"status": "complete"}
        mock_gen_client.get_download_url.return_value = "https://api.example.com/download"
        mock_media.download_from_url.return_value = (None, None)

        poller.poll_once()
        mock_rotation.complete_gen_job.assert_not_called()


class TestStartStop:
    def test_start_stop(self, poller):
        poller.start()
        assert poller._thread.is_alive()
        poller.stop()
        assert not poller._thread.is_alive()

    def test_start_idempotent(self, poller):
        poller.start()
        thread1 = poller._thread
        poller.start()
        assert poller._thread is thread1
        poller.stop()


class TestMasterFirstCompletion:
    """Completed public NOMAD jobs link the master pulled by master-sync."""

    ENTRY = {"id": 7, "gen_job_id": "job-m", "gen_status": "rendering",
             "song_artist": "Creep - Radiohead"}

    def _poller(self, gen, rotation, media, now, wait=600):
        return GenPoller(gen, rotation, media, "/tmp/downloads",
                         poll_interval=1, master_wait_seconds=wait, clock=lambda: now[0])

    def _complete(self, gen, rotation, entry=None, brand="NOMAD-1714"):
        rotation.store.get_active_gen_entries.return_value = [dict(entry or self.ENTRY)]
        gen.get_job_status.return_value = {"status": "complete",
                                           "state_data": {"brand_code": brand}}

    def test_links_indexed_master(self, mock_gen_client, mock_rotation, tmp_path):
        master = tmp_path / "NOMAD-1714 - Radiohead - Creep.mp4"
        master.write_bytes(b"x")
        other = tmp_path / "NOMAD-17140 - Someone - Else.mp4"
        other.write_bytes(b"x")
        media = MagicMock()
        media.index = {str(other): {}, str(master): {}}
        self._complete(mock_gen_client, mock_rotation)
        self._poller(mock_gen_client, mock_rotation, media, [0.0]).poll_once()
        mock_rotation.complete_gen_job.assert_called_once_with("job-m", str(master))
        media.download_from_url.assert_not_called()

    def test_waits_as_syncing_then_links_when_master_arrives(
        self, mock_gen_client, mock_rotation, tmp_path
    ):
        media = MagicMock()
        media.index = {}
        now = [0.0]
        poller = self._poller(mock_gen_client, mock_rotation, media, now)
        self._complete(mock_gen_client, mock_rotation)
        poller.poll_once()
        mock_rotation.set_gen_status.assert_called_once_with(7, "job-m", "syncing")
        mock_rotation.complete_gen_job.assert_not_called()
        media.download_from_url.assert_not_called()

        # Next poll: master-sync has delivered the file.
        master = tmp_path / "NOMAD-1714 - Radiohead - Creep.mp4"
        master.write_bytes(b"x")
        media.index = {str(master): {}}
        now[0] = 120.0
        self._complete(mock_gen_client, mock_rotation,
                       entry={**self.ENTRY, "gen_status": "syncing"})
        poller.poll_once()
        mock_rotation.complete_gen_job.assert_called_once_with("job-m", str(master))

    def test_falls_back_to_direct_download_after_wait(self, mock_gen_client, mock_rotation):
        media = MagicMock()
        media.index = {}
        media.download_from_url.return_value = ("/tmp/downloads/gen.mp4", "gen.mp4")
        mock_gen_client.get_download_url.return_value = "https://api/dl"
        now = [0.0]
        poller = self._poller(mock_gen_client, mock_rotation, media, now, wait=600)
        self._complete(mock_gen_client, mock_rotation)
        poller.poll_once()
        media.download_from_url.assert_not_called()
        now[0] = 601.0
        poller.poll_once()
        mock_rotation.complete_gen_job.assert_called_once_with("job-m", "/tmp/downloads/gen.mp4")

    def test_private_track_downloads_directly(self, mock_gen_client, mock_rotation):
        """NOMADNP- tracks are never pushed to the mirror — don't wait for them."""
        media = MagicMock()
        media.index = {}
        media.download_from_url.return_value = ("/tmp/downloads/gen.mp4", "gen.mp4")
        mock_gen_client.get_download_url.return_value = "https://api/dl"
        self._complete(mock_gen_client, mock_rotation, brand="NOMADNP-0042")
        self._poller(mock_gen_client, mock_rotation, media, [0.0]).poll_once()
        media.download_from_url.assert_called_once()

    def test_master_wait_disabled_downloads_directly(self, mock_gen_client, mock_rotation):
        media = MagicMock()
        media.index = {}
        media.download_from_url.return_value = ("/tmp/downloads/gen.mp4", "gen.mp4")
        mock_gen_client.get_download_url.return_value = "https://api/dl"
        self._complete(mock_gen_client, mock_rotation)
        self._poller(mock_gen_client, mock_rotation, media, [0.0], wait=0).poll_once()
        media.download_from_url.assert_called_once()

    def test_failed_direct_download_retries_then_marks_failed(
        self, mock_gen_client, mock_rotation
    ):
        media = MagicMock()
        media.index = {}
        media.download_from_url.return_value = (None, None)
        mock_gen_client.get_download_url.return_value = "https://api/dl"
        poller = self._poller(mock_gen_client, mock_rotation, media, [0.0], wait=0)
        self._complete(mock_gen_client, mock_rotation)
        poller.poll_once()
        poller.poll_once()
        mock_rotation.set_gen_status.assert_not_called()   # still retrying
        poller.poll_once()
        mock_rotation.set_gen_status.assert_called_once_with(7, "job-m", "failed")
        mock_rotation.complete_gen_job.assert_not_called()


class TestCompleteGenJobRotationStatus:
    """RotationManager.complete_gen_job flips Being Made (!) → Waiting."""

    @pytest.fixture
    def rotation(self):
        from rotation import RotationManager
        mgr = RotationManager(":memory:")
        yield mgr

    def _made_entry(self, rotation, status="Being Made (!)"):
        entry = rotation.add_entry("Mary", "Creep - Radiohead")
        rotation.store.update_status(entry["id"], status)
        rotation.set_gen_status(entry["id"], "job-x", "syncing")
        return entry["id"]

    def test_being_made_becomes_waiting(self, rotation):
        eid = self._made_entry(rotation)
        rotation.complete_gen_job("job-x", "/m/NOMAD-1 - Radiohead - Creep.mp4")
        entry = rotation.store.get_entry(eid)
        assert entry["status"] == "Waiting"
        assert entry["file_path"] == "/m/NOMAD-1 - Radiohead - Creep.mp4"
        assert entry["gen_status"] == "complete"

    def test_other_status_left_alone(self, rotation):
        eid = self._made_entry(rotation, status="On Hold (BRB)")
        rotation.complete_gen_job("job-x", "/m/a.mp4")
        assert rotation.store.get_entry(eid)["status"] == "On Hold (BRB)"

    def test_kj_manual_link_wins(self, rotation):
        eid = self._made_entry(rotation)
        rotation.store.link_file(eid, "/m/kj-choice.mp4", None)
        rotation.complete_gen_job("job-x", "/m/gen.mp4")
        entry = rotation.store.get_entry(eid)
        assert entry["file_path"] == "/m/kj-choice.mp4"
        assert entry["status"] == "Waiting"
