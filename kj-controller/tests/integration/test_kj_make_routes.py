"""KJ Gen modal routes (kj_make.py): /rotation/gen/*."""

import json
from unittest.mock import MagicMock, patch

import pytest

from app import create_app
from gen_client import GenApiError, GenStatus


@pytest.fixture
def gen_app(mock_config):
    mock_config["rotation_db_path"] = ":memory:"
    mock_config["gen_api_url"] = "https://api.example.com"
    mock_config["gen_api_token"] = "test-token"
    app = create_app(config=mock_config)
    app.config["TESTING"] = True
    app.gen_client = MagicMock(wraps=app.gen_client)
    yield app
    app.catalog.close()


@pytest.fixture
def client(gen_app):
    with gen_app.test_client() as c:
        yield c


def _post(client, path, body):
    return client.post(path, data=json.dumps(body), content_type="application/json")


PICK = {"artist": "Radiohead", "title": "Creep", "search_session_id": "ss-1", "selection_index": 2}


class TestCreate:
    def test_new_entry_is_being_made_and_tracked(self, client, gen_app):
        gen_app.gen_client.kj_create_job_from_search = MagicMock(return_value={"job_id": "job-1"})
        resp = _post(client, "/rotation/gen/create", {**PICK, "singers": ["Alice"]})
        assert resp.status_code == 200, resp.get_json()
        data = resp.get_json()
        gen_app.gen_client.kj_create_job_from_search.assert_called_once_with("ss-1", 2, "Radiohead", "Creep")
        entry = gen_app.rotation.store.get_entry(data["entry_id"])
        assert entry["singer"] == "Alice"
        assert entry["song_artist"] == "Creep - Radiohead"
        assert entry["status"] == "Being Made (!)"
        assert entry["gen_job_id"] == "job-1"
        assert entry["gen_status"] == GenStatus.PROCESSING
        assert any(e["id"] == data["entry_id"] for e in data["entries"])

    def test_youtube_link(self, client, gen_app):
        gen_app.gen_client.kj_create_job_from_url = MagicMock(return_value={"job_id": "job-yt"})
        resp = _post(client, "/rotation/gen/create", {
            "artist": "A", "title": "T", "youtube_url": "https://youtu.be/x", "singers": ["Bob"]})
        assert resp.status_code == 200
        gen_app.gen_client.kj_create_job_from_url.assert_called_once_with("https://youtu.be/x", "A", "T")

    def test_gen_failure_leaves_rotation_untouched(self, client, gen_app):
        gen_app.gen_client.kj_create_job_from_search = MagicMock(side_effect=GenApiError(502, "down"))
        resp = _post(client, "/rotation/gen/create", {**PICK, "singers": ["Alice"]})
        assert resp.status_code == 502
        assert resp.get_json()["error"] == "gen_unavailable"
        assert gen_app.rotation.get_rotation() == []

    def test_expired_search_session(self, client, gen_app):
        gen_app.gen_client.kj_create_job_from_search = MagicMock(side_effect=GenApiError(404, "nope"))
        resp = _post(client, "/rotation/gen/create", {**PICK, "singers": ["Alice"]})
        assert resp.status_code == 409 and resp.get_json()["error"] == "search_expired"

    def test_existing_entry_link_mode(self, client, gen_app):
        entry = gen_app.rotation.add_entry("Carol", "creep radiohed")
        gen_app.gen_client.kj_create_job_from_search = MagicMock(return_value={"job_id": "job-2"})
        resp = _post(client, "/rotation/gen/create", {**PICK, "id": entry["id"]})
        assert resp.status_code == 200
        updated = gen_app.rotation.store.get_entry(entry["id"])
        assert updated["song_artist"] == "Creep - Radiohead"
        assert updated["status"] == "Being Made (!)"
        assert updated["gen_job_id"] == "job-2"
        assert len(gen_app.rotation.get_rotation()) == 1

    def test_existing_entry_with_active_job_is_refused(self, client, gen_app):
        entry = gen_app.rotation.add_entry("Carol", "x")
        gen_app.rotation.set_gen_status(entry["id"], "job-old", GenStatus.RENDERING)
        gen_app.gen_client.kj_create_job_from_search = MagicMock()
        resp = _post(client, "/rotation/gen/create", {**PICK, "id": entry["id"]})
        assert resp.status_code == 409 and resp.get_json()["error"] == "already_generating"
        gen_app.gen_client.kj_create_job_from_search.assert_not_called()

    def test_replace_supersedes_a_stuck_job(self, client, gen_app):
        entry = gen_app.rotation.add_entry("Carol", "x")
        gen_app.rotation.set_gen_status(entry["id"], "job-old", GenStatus.PROCESSING)
        gen_app.gen_client.kj_create_job_from_search = MagicMock(return_value={"job_id": "job-new"})
        resp = _post(client, "/rotation/gen/create", {**PICK, "id": entry["id"], "replace": True})
        assert resp.status_code == 200
        assert gen_app.rotation.store.get_entry(entry["id"])["gen_job_id"] == "job-new"

    def test_existing_entry_with_a_linked_file_keeps_its_status(self, client, gen_app, tmp_path):
        f = tmp_path / "song.mp4"
        f.write_bytes(b"x")
        entry = gen_app.rotation.add_entry("Dan", "y", file_path=str(f), duration=200)
        gen_app.gen_client.kj_create_job_from_search = MagicMock(return_value={"job_id": "job-3"})
        resp = _post(client, "/rotation/gen/create", {**PICK, "id": entry["id"]})
        assert resp.status_code == 200
        assert gen_app.rotation.store.get_entry(entry["id"])["status"] != "Being Made (!)"

    @pytest.mark.parametrize("body,code", [
        ({"artist": "A", "title": "T", "singers": ["X"]}, 400),                      # no source
        ({"artist": "", "title": "T", "youtube_url": "u", "singers": ["X"]}, 400),   # no artist
        ({**PICK}, 400),                                                             # no singer
        ({**PICK, "selection_index": True, "singers": ["X"]}, 400),                  # bool index
        ({**PICK, "id": 9999}, 404),
    ])
    def test_validation(self, client, gen_app, body, code):
        gen_app.gen_client.kj_create_job_from_search = MagicMock()
        assert _post(client, "/rotation/gen/create", body).status_code == code
        gen_app.gen_client.kj_create_job_from_search.assert_not_called()

    def test_no_gen_configured(self, client, gen_app):
        gen_app.gen_client = None
        assert _post(client, "/rotation/gen/create", {**PICK, "singers": ["X"]}).status_code == 503


class TestSearchAndCheck:
    def test_search_proxies_gen(self, client, gen_app):
        gen_app.gen_client.kj_search_audio = MagicMock(
            return_value={"search_session_id": "ss", "results": [{"index": 0}], "extra": 1})
        resp = _post(client, "/rotation/gen/search", {"artist": "A", "title": "T"})
        assert resp.get_json() == {"search_session_id": "ss", "results": [{"index": 0}]}

    def test_search_auth_error(self, client, gen_app):
        gen_app.gen_client.kj_search_audio = MagicMock(side_effect=GenApiError(401, "bad"))
        resp = _post(client, "/rotation/gen/search", {"artist": "A", "title": "T"})
        assert resp.status_code == 502 and resp.get_json()["error"] == "gen_auth"

    def test_check_fails_open(self, client, gen_app):
        gen_app.gen_client.kj_match_judge = MagicMock(side_effect=GenApiError(500, "x"))
        resp = _post(client, "/rotation/gen/check", {"artist": "A", "title": "T", "stage": "full", "tier": 3})
        assert resp.get_json() == {"kind": "none", "confident": False}
        gen_app.gen_client.kj_match_judge.assert_called_once_with("A", "T", stage="full", audio_confidence_tier=3)

    def test_validate_url(self, client, gen_app):
        gen_app.gen_client.kj_validate_url = MagicMock(return_value={"supported": True})
        assert _post(client, "/rotation/gen/validate-url", {"url": "https://youtu.be/x"}).get_json() == {"supported": True}
        assert _post(client, "/rotation/gen/validate-url", {"url": ""}).get_json() == {"supported": False}


class TestResolve:
    def test_gen_correction_wins(self, client):
        with patch("sing._resolve_query", return_value={
                "kind": "content", "confident": True,
                "canonical_artist": "Amy Macdonald", "canonical_title": "This Is the Life"}):
            data = client.get("/rotation/gen/resolve?q=amy macdonald this is the life").get_json()
        assert data == {"artist": "Amy Macdonald", "title": "This Is the Life", "source": "gen"}

    def test_gen_split(self, client):
        with patch("sing._resolve_query", return_value={
                "kind": "none", "typed_artist": "amy macdonald", "typed_title": "run"}):
            data = client.get("/rotation/gen/resolve?q=amy macdonald run").get_json()
        assert (data["artist"], data["title"], data["source"]) == ("amy macdonald", "run", "split")

    def test_naive_split_when_gen_unavailable(self, client):
        with patch("sing._resolve_query", return_value=None):
            data = client.get("/rotation/gen/resolve?q=Run - Amy Macdonald").get_json()
            assert (data["artist"], data["title"]) == ("Amy Macdonald", "Run")
            data = client.get("/rotation/gen/resolve?q=amy macdonald").get_json()
            assert (data["artist"], data["title"]) == ("", "amy macdonald")
