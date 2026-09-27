"""Unit tests for the make-it quick-version plumbing (store, gen client, poll rate)."""

from unittest.mock import MagicMock, patch

import pytest

from gen_client import GenApiError, GenClient, quick_version_info
from gen_poller import GenPoller
from rotation_store import RotationStore


@pytest.fixture
def store():
    s = RotationStore(":memory:")
    s.init_schema()
    return s


class TestStore:
    def test_upsert_insert_update_and_bulk_get(self, store):
        assert store.get_quick_version("j1") is None
        row = store.upsert_quick_version("j1", attempts=1)
        assert row["status"] == "downloading" and row["attempts"] == 1
        row = store.upsert_quick_version("j1", status="ready", file_path="/a.mp4")
        assert (row["status"], row["file_path"], row["attempts"]) == ("ready", "/a.mp4", 1)
        store.upsert_quick_version("j2", status="failed")
        assert set(store.get_quick_versions(["j1", "j2", "nope", None])) == {"j1", "j2"}
        assert store.get_quick_versions([]) == {}

    def test_rejects_unknown_fields(self, store):
        with pytest.raises(ValueError):
            store.upsert_quick_version("j1", gen_job_id="x")

    def test_survives_undo(self, store):
        """Quick state lives outside rotation_entries, so an undo can't erase it."""
        store.add_entry("Mary", "Creep - Radiohead")
        store.checkpoint("x")
        store.upsert_quick_version("j1", status="ready")
        store.undo()
        assert store.get_quick_version("j1")["status"] == "ready"


class TestQuickInfo:
    def test_available_needs_ready_and_file(self):
        job = {"state_data": {"quick_version": {"status": "ready", "lyrics_tier": "synced"}},
               "file_urls": {"quick": {"video_mp4": "jobs/j/quick/quick.mp4"}}}
        info = quick_version_info(job)
        assert info["available"] is True and info["lyrics_tier"] == "synced"
        job["file_urls"] = {}
        assert quick_version_info(job)["available"] is False
        assert quick_version_info({"state_data": {"quick_version": "junk"}}) == {}
        assert quick_version_info(None) == {}


class TestDownload:
    def _resp(self, chunks, status=200):
        resp = MagicMock()
        resp.__enter__.return_value = resp
        resp.iter_content.return_value = chunks
        resp.raise_for_status.side_effect = None if status < 400 else Exception(str(status))
        return resp

    def test_streams_with_admin_header(self, tmp_path):
        client = GenClient("https://api.x/", "tok")
        dest = tmp_path / "q.mp4"
        with patch("gen_client.requests.get", return_value=self._resp([b"ab", b"", b"cd"])) as get:
            assert client.download_quick_version("job1", str(dest)) == 4
        assert dest.read_bytes() == b"abcd"
        assert get.call_args.args[0] == "https://api.x/api/jobs/job1/download/quick/video_mp4"
        assert get.call_args.kwargs["headers"] == {"X-Admin-Token": "tok"}

    def test_empty_download_raises(self, tmp_path):
        client = GenClient("https://api.x", "tok")
        with patch("gen_client.requests.get", return_value=self._resp([])):
            with pytest.raises(GenApiError):
                client.download_quick_version("job1", str(tmp_path / "q.mp4"))


class TestPollRate:
    def _poller(self, store, sing_request):
        rotation = MagicMock()
        rotation.store = store
        sing = MagicMock()
        sing.get_request_by_gen_job_id.return_value = sing_request
        return GenPoller(MagicMock(), rotation, MagicMock(), "/tmp", poll_interval=60,
                         sing_store=sing)

    def _entry(self, store):
        e = store.add_entry("Mary", "Creep - Radiohead")
        store.set_gen_status(e["id"], "j1", "processing")
        return store.get_entry(e["id"])

    def test_fast_only_for_singer_make_its(self, store):
        poller = self._poller(store, sing_request={"id": 1})
        poller.gen_client.get_job_status.return_value = {"status": "downloading"}
        self._entry(store)
        poller.poll_once()
        assert poller._fast is True

        kj_job = self._poller(store, sing_request=None)
        kj_job.gen_client.get_job_status.return_value = {"status": "downloading"}
        kj_job.poll_once()
        assert kj_job._fast is False

    def test_no_fast_poll_once_job_is_past_processing(self, store):
        poller = self._poller(store, sing_request={"id": 1})
        poller.gen_client.get_job_status.return_value = {"status": "awaiting_review"}
        self._entry(store)
        poller.poll_once()
        assert poller._fast is False

    def test_fast_interval_capped_by_normal_interval(self):
        p = GenPoller(MagicMock(), MagicMock(), MagicMock(), "/tmp", poll_interval=5)
        assert p.fast_poll_interval == 5
