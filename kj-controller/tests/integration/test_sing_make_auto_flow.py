"""Singer "make" requests: gen job at submit time → attached on approval.

The gen job starts the moment the singer submits (background thread), approval
attaches it to the new "Being Made (!)" rotation entry without creating a
duplicate, and whichever of (submit worker, approval) finishes second does the
hand-over.
"""

from unittest.mock import MagicMock

import pytest

import make_jobs


class _DeferredThread:
    """Stand-in for threading.Thread that runs the target only when told to."""

    started = []

    def __init__(self, target=None, args=(), **_kw):
        self.target, self.args = target, args

    def start(self):
        _DeferredThread.started.append(self)

    @classmethod
    def run_all(cls):
        pending, cls.started = cls.started, []
        for t in pending:
            t.target(*t.args)


@pytest.fixture
def deferred_threads(monkeypatch):
    _DeferredThread.started = []
    monkeypatch.setattr(make_jobs.threading, "Thread", _DeferredThread)
    return _DeferredThread


@pytest.fixture
def gen(sing_app):
    client = MagicMock()
    client.create_job.return_value = {"job_id": "job-early", "status": "pending"}
    sing_app.gen_client = client
    return client


def _submit(client, token, **overrides):
    body = {
        "singer_name": "Mary", "device_id": "dev-mary",
        "source_type": "make", "song_artist": "Radiohead", "song_title": "Creep",
    }
    body.update(overrides)
    return client.post(f"/sing/submit?t={token}", json=body)


def _approve(client, req_id):
    return client.post(f"/rotation/requests/{req_id}/approve")


class TestSubmitStartsGenJob:
    def test_submit_starts_job_before_approval(self, client, sing_app, token, gen, deferred_threads):
        resp = _submit(client, token)
        assert resp.status_code == 200
        rid = resp.get_json()["request"]["id"]
        assert sing_app.sing_store.get_request(rid)["gen_submit_state"] == "submitting"
        deferred_threads.run_all()
        gen.create_job.assert_called_once_with("Radiohead", "Creep")
        req = sing_app.sing_store.get_request(rid)
        assert req["status"] == "pending"
        assert (req["gen_job_id"], req["gen_submit_state"]) == ("job-early", "submitted")

    def test_approval_attaches_early_job_without_duplicate(
        self, client, sing_app, token, gen, deferred_threads
    ):
        rid = _submit(client, token).get_json()["request"]["id"]
        deferred_threads.run_all()
        resp = _approve(client, rid)
        assert resp.status_code == 200
        entry = sing_app.rotation.store.get_entry(resp.get_json()["entry_id"])
        assert entry["gen_job_id"] == "job-early"
        assert entry["status"] == "Being Made (!)"
        gen.create_job.assert_called_once()

    def test_approval_while_submitting_attaches_when_job_arrives(
        self, client, sing_app, token, gen, deferred_threads
    ):
        rid = _submit(client, token).get_json()["request"]["id"]
        resp = _approve(client, rid)   # worker hasn't run yet
        entry_id = resp.get_json()["entry_id"]
        entry = sing_app.rotation.store.get_entry(entry_id)
        assert entry["gen_job_id"] is None
        assert entry["status"] == "Being Made (!)"
        gen.create_job.assert_not_called()

        deferred_threads.run_all()
        entry = sing_app.rotation.store.get_entry(entry_id)
        assert entry["gen_job_id"] == "job-early"
        assert entry["gen_status"] == "processing"
        gen.create_job.assert_called_once()

    def test_auto_approve_attaches_early_job(self, client, sing_app, token, gen, deferred_threads):
        sing_app.sing_store.set_auto_approve(True)
        resp = _submit(client, token)
        assert resp.get_json()["auto_approved"] is True
        entry_id = resp.get_json()["request"]["linked_entry_id"]
        deferred_threads.run_all()
        entry = sing_app.rotation.store.get_entry(entry_id)
        assert entry["gen_job_id"] == "job-early"
        gen.create_job.assert_called_once()

    def test_failed_early_submit_falls_back_on_approval(
        self, client, sing_app, token, gen, deferred_threads
    ):
        gen.create_job.side_effect = [RuntimeError("no results"),
                                      {"job_id": "job-retry", "status": "pending"}]
        rid = _submit(client, token).get_json()["request"]["id"]
        deferred_threads.run_all()
        assert sing_app.sing_store.get_request(rid)["gen_submit_state"] == "failed"
        resp = _approve(client, rid)
        entry = sing_app.rotation.store.get_entry(resp.get_json()["entry_id"])
        assert entry["gen_job_id"] == "job-retry"
        assert gen.create_job.call_count == 2

    def test_no_gen_configured_keeps_legacy_behaviour(self, client, sing_app, token, deferred_threads):
        sing_app.gen_client = None
        rid = _submit(client, token).get_json()["request"]["id"]
        assert sing_app.sing_store.get_request(rid)["gen_submit_state"] is None
        assert deferred_threads.started == []


class TestMakeLimit:
    def test_per_device_night_cap(self, client, sing_app, token, gen, deferred_threads):
        sing_app.kj_config["sing_make_max_per_device"] = 2
        sing_app.kj_config["sing_rate_limit_per_device"] = 100
        assert _submit(client, token, song_title="One").status_code == 200
        assert _submit(client, token, song_title="Two").status_code == 200
        third = _submit(client, token, song_title="Three")
        assert third.status_code == 429
        assert third.get_json()["error"] == "make_limit"
        # Other devices are unaffected.
        assert _submit(client, token, device_id="dev-other").status_code == 200


class TestChangeSong:
    def test_changing_make_song_resubmits_and_drops_stale_job(
        self, client, sing_app, token, gen, deferred_threads
    ):
        resp = _submit(client, token)
        req = resp.get_json()["request"]
        gen.create_job.side_effect = [{"job_id": "job-old"}, {"job_id": "job-new"}]
        # Change the song while the first job is still being created.
        chg = client.post(
            f"/sing/requests/{req['id']}/change?t={token}",
            json={"edit_token": req["edit_token"], "source_type": "make",
                  "song_artist": "Radiohead", "song_title": "Karma Police"},
        )
        assert chg.status_code == 200
        deferred_threads.run_all()
        row = sing_app.sing_store.get_request(req["id"])
        assert row["gen_job_id"] == "job-new"
        assert row["song_title"] == "Karma Police"

    def test_changing_away_from_make_clears_job(self, client, sing_app, token, gen, deferred_threads):
        req = _submit(client, token).get_json()["request"]
        deferred_threads.run_all()
        chg = client.post(
            f"/sing/requests/{req['id']}/change?t={token}",
            json={"edit_token": req["edit_token"], "source_type": "youtube",
                  "source_ref": "https://youtu.be/abc", "song_artist": "R", "song_title": "C"},
        )
        assert chg.status_code == 200
        row = sing_app.sing_store.get_request(req["id"])
        assert (row["gen_job_id"], row["gen_submit_state"]) == (None, None)


class TestMyRequestsMakePhase:
    def _phase(self, client, token, rid):
        resp = client.get(f"/sing/my-requests?ids={rid}&t={token}")
        return resp.get_json()["requests"][0].get("make")

    def test_phases(self, client, sing_app, token, gen, deferred_threads):
        rid = _submit(client, token).get_json()["request"]["id"]
        assert self._phase(client, token, rid) == "making"      # pending, submitting
        deferred_threads.run_all()
        entry_id = _approve(client, rid).get_json()["entry_id"]
        assert self._phase(client, token, rid) == "making"      # processing
        sing_app.rotation.set_gen_status(entry_id, "job-early", "awaiting_review")
        assert self._phase(client, token, rid) == "needs_host"
        sing_app.rotation.complete_gen_job("job-early", "/m/NOMAD-1 - Radiohead - Creep.mp4")
        assert self._phase(client, token, rid) is None
        assert sing_app.rotation.store.get_entry(entry_id)["status"] == "Waiting"

    def test_non_make_request_has_no_phase(self, client, sing_app, token):
        rid = _submit(client, token, source_type="youtube", source_ref="https://youtu.be/x"
                      ).get_json()["request"]["id"]
        resp = client.get(f"/sing/my-requests?ids={rid}&t={token}")
        assert "make" not in resp.get_json()["requests"][0]
