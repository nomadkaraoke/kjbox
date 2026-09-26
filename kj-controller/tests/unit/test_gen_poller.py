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


class TestMasterOnlyCompletion:
    """Completed jobs link ONLY the NOMAD master pulled by master-sync."""

    ENTRY = {"id": 7, "gen_job_id": "job-m", "gen_status": "rendering",
             "song_artist": "Creep - Radiohead"}

    def _complete(self, gen, rotation, entry=None, brand="NOMAD-1714"):
        rotation.store.get_active_gen_entries.return_value = [dict(entry or self.ENTRY)]
        gen.get_job_status.return_value = {"status": "complete",
                                           "state_data": {"brand_code": brand}}

    def test_links_indexed_master(self, mock_gen_client, mock_rotation, tmp_path):
        master = tmp_path / "NOMAD-1714 - Radiohead - Creep.mp4"
        master.write_bytes(b"x")
        other = tmp_path / "NOMAD-17140 - Someone - Else.mp4"   # prefix look-alike
        other.write_bytes(b"x")
        media = MagicMock()
        media.index = {str(other): {}, str(master): {}}
        self._complete(mock_gen_client, mock_rotation)
        GenPoller(mock_gen_client, mock_rotation, media, "/tmp/d").poll_once()
        mock_rotation.complete_gen_job.assert_called_once_with("job-m", str(master))
        media.download_from_url.assert_not_called()

    def test_waits_as_syncing_then_links_when_master_arrives(
        self, mock_gen_client, mock_rotation, tmp_path
    ):
        media = MagicMock()
        media.index = {}
        poller = GenPoller(mock_gen_client, mock_rotation, media, "/tmp/d")
        self._complete(mock_gen_client, mock_rotation)
        poller.poll_once()
        mock_rotation.set_gen_status.assert_called_once_with(7, "job-m", "syncing")
        mock_rotation.complete_gen_job.assert_not_called()

        # Still missing on the next poll: no duplicate status write, no download.
        self._complete(mock_gen_client, mock_rotation, entry={**self.ENTRY, "gen_status": "syncing"})
        poller.poll_once()
        assert mock_rotation.set_gen_status.call_count == 1

        master = tmp_path / "NOMAD-1714 - Radiohead - Creep.mp4"
        master.write_bytes(b"x")
        media.index = {str(master): {}}
        poller.poll_once()
        mock_rotation.complete_gen_job.assert_called_once_with("job-m", str(master))
        media.download_from_url.assert_not_called()

    def test_never_downloads_directly(self, mock_gen_client, mock_rotation):
        """Andrew 2026-09-25: only ever the NOMAD file — no fallback download."""
        media = MagicMock()
        media.index = {}
        poller = GenPoller(mock_gen_client, mock_rotation, media, "/tmp/d")
        self._complete(mock_gen_client, mock_rotation)
        for _ in range(5):
            poller.poll_once()
        media.download_from_url.assert_not_called()
        mock_gen_client.get_download_url.assert_not_called()

    def test_private_track_waits_and_is_not_downloaded(self, mock_gen_client, mock_rotation):
        media = MagicMock()
        media.index = {}
        self._complete(mock_gen_client, mock_rotation, brand="NOMADNP-0042")
        GenPoller(mock_gen_client, mock_rotation, media, "/tmp/d").poll_once()
        mock_rotation.set_gen_status.assert_called_once_with(7, "job-m", "syncing")
        media.download_from_url.assert_not_called()


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
