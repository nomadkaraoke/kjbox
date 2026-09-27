"""Make-it quick (draft) version: gen's scrolling-lyrics video lands on the box
minutes after a make-it request, and the singer (or KJ) can sing it right away.

Flow under test: GenPoller sees gen's ``state_data.quick_version`` ready →
downloads + imports it (QUICK label) → pushes ``quick_ready`` → the singer's
My-songs card offers Preview / Sing it now → ``/sing/make/use-quick`` links it
and makes the entry singable → when the full NOMAD master lands before the
singer is up, it replaces the draft.
"""

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from gen_poller import GenPoller

DEVICE = "0123456789abcdef" * 2
JOB = "job-mary"


@pytest.fixture
def gen(sing_app):
    client = MagicMock()
    client.singer_flow_configured.return_value = True
    client.match_judge.return_value = {"kind": "cosmetic", "confident": True,
                                       "canonical_artist": "Radiohead", "canonical_title": "Creep"}
    client.create_job_from_search.return_value = {"job_id": JOB, "status": "pending"}
    sing_app.gen_client = client
    sing_app.sing_store.set_gen_account(DEVICE, "mary@example.com", "sess-mary")
    return client


@pytest.fixture
def made(client, token, gen):
    """A submitted make-it request → (request_id, entry_id, edit_token)."""
    body = client.post(f"/sing/submit?t={token}", json={
        "singer_name": "Mary", "device_id": DEVICE,
        "source_type": "make", "song_artist": "Radiohead", "song_title": "Creep",
        "source_meta": {"search_session_id": "ss-1", "selection_index": 0},
    }).get_json()
    req = body["request"]
    return req["id"], req["linked_entry_id"], req["edit_token"]


@pytest.fixture
def quick_file(tmp_path):
    path = tmp_path / "upload" / "Radiohead - Creep [up-abc].mp4"
    path.parent.mkdir()
    path.write_bytes(b"\x00" * 64)
    return str(path)


@pytest.fixture
def poller(sing_app, gen, quick_file, tmp_path):
    media = MagicMock()
    media.import_upload.return_value = {"path": quick_file, "media_id": "up-abc",
                                        "filename": "x.mp4", "display_name": "x"}
    stats = MagicMock()
    sing_app.rotation.push_dispatcher = MagicMock()

    def download(job_id, dest):
        with open(dest, "wb") as fh:
            fh.write(b"mp4")
        return 3
    gen.download_quick_version.side_effect = download
    return GenPoller(gen, sing_app.rotation, media, str(tmp_path / "dl"),
                     stats=stats, sing_store=sing_app.sing_store)


def _job(status="separating_stage1", quick_status="ready", with_file=True):
    data = {"status": status, "artist": "Radiohead", "title": "Creep",
            "state_data": {"quick_version": {"status": quick_status, "lyrics_tier": "synced"}}}
    if with_file:
        data["file_urls"] = {"quick": {"video_mp4": f"jobs/{JOB}/quick/quick.mp4"}}
    return data


def _item(client, token, rid):
    return client.get(f"/sing/my-requests?ids={rid}&t={token}").get_json()["requests"][0]


def _use_quick(client, token, rid, edit):
    return client.post(f"/sing/make/use-quick/{rid}?t={token}",
                       json={"device_id": DEVICE, "edit_token": edit})


class TestQuickVersionFlow:
    def test_end_to_end(self, client, sing_app, token, gen, made, poller, quick_file):
        rid, entry_id, edit = made
        store = sing_app.rotation.store

        # gen still rendering the draft → nothing yet, but poll fast.
        gen.get_job_status.return_value = _job(quick_status="rendering", with_file=False)
        poller.poll_once()
        assert poller._fast is True
        assert store.get_quick_version(JOB) is None
        assert "quick" not in _item(client, token, rid)

        # Ready → downloaded, imported, labelled, singer pushed.
        gen.get_job_status.return_value = _job()
        poller.poll_once()
        rec = store.get_quick_version(JOB)
        assert rec["status"] == "ready" and rec["file_path"] == quick_file
        assert rec["lyrics_tier"] == "synced" and rec["notified_at"]
        poller.media.import_upload.assert_called_once()
        assert poller.media.import_upload.call_args.kwargs["ext"] == ".mp4"
        poller.stats.upsert_note.assert_called_once()
        assert poller.stats.upsert_note.call_args.args[2] == "QUICK"
        push = sing_app.rotation.push_dispatcher.notify_request_decision
        push.assert_called_once()
        assert push.call_args.args[:2] == (rid, "quick_ready")
        assert poller._fast is False

        item = _item(client, token, rid)
        assert item["quick"] == "ready" and item["make"] == "making"

        # Singer previews the draft by entry id (path resolved server-side).
        sing_app.preview = MagicMock()
        sing_app.preview.resolve.return_value = {"mode": "direct", "url": "/x"}
        resp = client.post(f"/sing/preview/resolve?t={token}",
                           json={"source": "entry_quick", "entry_id": entry_id})
        assert resp.status_code == 200
        assert sing_app.preview.resolve.call_args.args[0]["file_path"] == quick_file

        # A second poll doesn't re-download.
        poller.poll_once()
        assert gen.download_quick_version.call_count == 1

        # Sing it now → linked, singable, card explains the draft.
        assert _use_quick(client, token, rid, edit).status_code == 200
        entry = store.get_entry(entry_id)
        assert entry["file_path"] == quick_file and entry["status"] == "Waiting"
        item = _item(client, token, rid)
        assert item["quick"] == "chosen" and item.get("make") is None
        assert item["previewable"] is True
        assert _use_quick(client, token, rid, edit).status_code == 200   # idempotent

        # The full NOMAD master lands before Mary is up → it replaces the draft.
        sing_app.rotation.complete_gen_job(JOB, "/m/NOMAD-1 - Radiohead - Creep.mp4")
        assert store.get_entry(entry_id)["file_path"] == "/m/NOMAD-1 - Radiohead - Creep.mp4"
        assert store.get_quick_version(JOB)["status"] == "upgraded"
        assert _item(client, token, rid)["quick"] == "upgraded"

    def test_no_swap_while_singing(self, client, sing_app, token, made, poller, gen, quick_file):
        rid, entry_id, edit = made
        gen.get_job_status.return_value = _job()
        poller.poll_once()
        _use_quick(client, token, rid, edit)
        sing_app.rotation.update_status(entry_id, "Now Singing")
        sing_app.rotation.complete_gen_job(JOB, "/m/NOMAD-1 - Radiohead - Creep.mp4")
        assert sing_app.rotation.store.get_entry(entry_id)["file_path"] == quick_file
        assert sing_app.rotation.store.get_quick_version(JOB)["status"] == "chosen"

    def test_unlinked_draft_is_offered_again(self, client, sing_app, token, made, poller, gen):
        rid, entry_id, edit = made
        gen.get_job_status.return_value = _job()
        poller.poll_once()
        _use_quick(client, token, rid, edit)
        sing_app.rotation.store.unlink_file(entry_id)
        assert _item(client, token, rid)["quick"] == "ready"
        entry = next(e for e in client.get("/rotation").get_json()["entries"] if e["id"] == entry_id)
        assert entry["quick"]["state"] == "ready"
        assert _use_quick(client, token, rid, edit).status_code == 200

    def test_hand_linked_draft_is_still_upgraded(self, client, sing_app, token, made, poller, gen,
                                                  quick_file):
        rid, entry_id, _edit = made
        gen.get_job_status.return_value = _job()
        poller.poll_once()
        sing_app.rotation.store.link_file(entry_id, quick_file)   # KJ picked it from the library
        assert _item(client, token, rid)["quick"] == "chosen"
        sing_app.rotation.complete_gen_job(JOB, "/m/NOMAD-1 - Radiohead - Creep.mp4")
        assert sing_app.rotation.store.get_entry(entry_id)["file_path"].startswith("/m/NOMAD-1")
        assert _item(client, token, rid)["quick"] == "upgraded"

    def test_master_first_then_sing_now_keeps_master(self, client, sing_app, token, made, poller, gen):
        rid, entry_id, edit = made
        gen.get_job_status.return_value = _job()
        poller.poll_once()
        sing_app.rotation.complete_gen_job(JOB, "/m/NOMAD-1 - Radiohead - Creep.mp4")
        assert _use_quick(client, token, rid, edit).status_code == 409
        assert sing_app.rotation.store.get_entry(entry_id)["file_path"].startswith("/m/NOMAD-1")

    def test_kj_link_wins_over_quick(self, client, sing_app, token, made, poller, gen):
        rid, entry_id, edit = made
        sing_app.rotation.store.link_file(entry_id, "/kj/picked.mp4")
        gen.get_job_status.return_value = _job()
        poller.poll_once()
        gen.download_quick_version.assert_not_called()   # already has a file
        assert _use_quick(client, token, rid, edit).status_code == 404

    def test_download_failure_retries_then_gives_up(self, sing_app, made, poller, gen):
        gen.download_quick_version.side_effect = RuntimeError("503")
        gen.get_job_status.return_value = _job()
        for _ in range(5):
            poller.poll_once()
        rec = sing_app.rotation.store.get_quick_version(JOB)
        assert rec["status"] == "failed" and rec["attempts"] == 3
        assert gen.download_quick_version.call_count == 3
        assert not list(Path(poller.download_folder).glob(".quick_staging_*"))   # cleaned up

    def test_gen_quick_failure_stops_fast_polling(self, sing_app, made, poller, gen):
        gen.get_job_status.return_value = _job(quick_status="failed", with_file=False)
        poller.poll_once()
        assert poller._fast is False
        gen.download_quick_version.assert_not_called()

    def test_disabled_by_config(self, client, sing_app, token, made, poller, gen):
        poller.quick_enabled = False
        gen.get_job_status.return_value = _job()
        poller.poll_once()
        gen.download_quick_version.assert_not_called()
        sing_app.kj_config["make_quick_version_enabled"] = False
        rid, _entry_id, edit = made
        assert _use_quick(client, token, rid, edit).status_code == 400
        assert client.get(f"/sing/event-info?t={token}").get_json()["make_quick_version"] is False


class TestUseQuickGuards:
    def test_requires_edit_token(self, client, token, made, poller, gen):
        rid, _entry_id, _edit = made
        gen.get_job_status.return_value = _job()
        poller.poll_once()
        assert _use_quick(client, token, rid, "wrong").status_code == 403

    def test_not_ready_yet(self, client, token, made):
        rid, _entry_id, edit = made
        resp = _use_quick(client, token, rid, edit)
        assert resp.status_code == 404 and resp.get_json()["error"] == "quick_not_ready"

    def test_unknown_request(self, client, token, gen):
        assert _use_quick(client, token, 9999, "x").status_code == 404


class TestKjUseQuick:
    def test_rotation_decoration_and_kj_link(self, client, sing_app, made, poller, gen, quick_file):
        _rid, entry_id, _edit = made
        gen.get_job_status.return_value = _job()
        poller.poll_once()
        entry = next(e for e in client.get("/rotation").get_json()["entries"] if e["id"] == entry_id)
        assert entry["quick"] == {"state": "ready", "lyrics_tier": "synced"}

        resp = client.post("/rotation/use-quick", json={"id": entry_id})
        assert resp.status_code == 200
        entry = next(e for e in resp.get_json()["entries"] if e["id"] == entry_id)
        assert entry["file_path"] == quick_file and entry["quick"]["state"] == "chosen"
        assert entry["status"] == "Waiting"
        assert sing_app.rotation.history_status()["undo_label"] == "Use quick version (KJ)"

    def test_kj_link_errors(self, client, sing_app, made):
        _rid, entry_id, _edit = made
        assert client.post("/rotation/use-quick", json={"id": entry_id}).status_code == 404
        assert client.post("/rotation/use-quick", json={}).status_code == 400


def test_event_info_advertises_quick_version(client, token):
    assert client.get(f"/sing/event-info?t={token}").get_json()["make_quick_version"] is True
