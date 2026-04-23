"""End-to-end integration test for the kj_pick flow (Phase A).

Walks through the full path a singer + KJ take when nobody picks a version
up front:

  1. Singer submits a kj_pick request carrying the full candidate snapshot.
  2. Admin GET /rotation/requests?status=pending shows the request with
     source_meta intact.
  3. Admin approves with version_index pointing at a kn+divebar candidate.
  4. Verify the post-approval row is rewritten to source_type=divebar and
     a download was queued.

Also covers the reject path — version binding isn't required for reject.
"""

from unittest.mock import patch


_VERSIONS = [
    {"source": "local", "local": {
        "path": "/media/q/queen-bo-rhap.cdg",
        "filename": "queen-bo-rhap.cdg",
        "artist": "Queen",
        "title": "Bohemian Rhapsody",
    }},
    {"source": "kn", "kn": {
        "brand_code": "SF",
        "divebar": {"file_id": "gdrive-sf-001", "drive_path": "/SF/001.mp4"},
        "youtube_url": "https://youtu.be/fallback",
    }},
    {"source": "kn", "kn": {
        "brand_code": "KV",
        "is_community": True,
        "youtube_url": "https://youtu.be/kv-community",
    }},
]


def _submit_kj_pick(client, token, versions=None):
    return client.post(f"/sing/submit?t={token}", json={
        "singer_name": "E2E Singer",
        "phone": "+1 555 0000",
        "song_artist": "Queen",
        "song_title": "Bohemian Rhapsody",
        "source_type": "kj_pick",
        "source_ref": None,
        "source_meta": {"versions": versions if versions is not None else _VERSIONS},
    })


class TestKjPickE2E:
    def test_full_flow_submit_then_pick_divebar(self, client, sing_app, token):
        # 1. Singer submits
        resp = _submit_kj_pick(client, token)
        assert resp.status_code == 200
        req_id = resp.get_json()["request"]["id"]

        # 2. Admin sees the request with source_meta intact
        admin = sing_app.test_client()
        list_resp = admin.get("/rotation/requests?status=pending")
        assert list_resp.status_code == 200
        requests = list_resp.get_json()["requests"]
        ours = next(r for r in requests if r["id"] == req_id)
        assert ours["source_type"] == "kj_pick"
        assert ours["source_meta"]  # JSON string, admin UI parses it

        # 3. Admin approves with index=1 (SF+divebar)
        with patch(
            "routes.divebar.get_download_url",
            return_value="https://dl/sf-001.mp4",
        ), patch("routes._download_worker"):
            ap = admin.post(
                f"/rotation/requests/{req_id}/approve",
                json={"version_index": 1},
            )
        assert ap.status_code == 200
        approved = ap.get_json()["request"]

        # 4. Row rewritten; download queued
        assert approved["source_type"] == "divebar"
        assert approved["source_ref"] == "gdrive-sf-001"
        assert approved["status"] == "approved"

        items = sing_app.download_queue["items"]
        assert any(
            it["source"] == "divebar" and it["url"] == "https://dl/sf-001.mp4"
            for it in items
        )

        # Rotation entry linked to the request
        entries = sing_app.rotation.get_rotation()
        assert any(e["singer"] == "E2E Singer" for e in entries)

    def test_reject_skips_version_binding(self, client, sing_app, token):
        """Rejecting a kj_pick should work with no version_index — we're not
        picking anything, we're telling the singer no."""
        resp = _submit_kj_pick(client, token)
        req_id = resp.get_json()["request"]["id"]
        admin = sing_app.test_client()
        rj = admin.post(
            f"/rotation/requests/{req_id}/reject",
            json={"reason": "we don't have any version tonight"},
        )
        assert rj.status_code == 200
        stored = sing_app.sing_store.get_request(req_id)
        assert stored["status"] == "rejected"
        # Row was never rewritten; it's still a kj_pick.
        assert stored["source_type"] == "kj_pick"

    def test_pick_local_short_circuits_to_in_library(self, client, sing_app, token):
        """version_index=0 picks the local version — approval should not queue
        any download."""
        resp = _submit_kj_pick(client, token)
        req_id = resp.get_json()["request"]["id"]
        admin = sing_app.test_client()
        ap = admin.post(
            f"/rotation/requests/{req_id}/approve",
            json={"version_index": 0},
        )
        assert ap.status_code == 200
        approved = ap.get_json()["request"]
        assert approved["source_type"] == "local"
        assert approved["source_ref"] == "/media/q/queen-bo-rhap.cdg"
        # No download queued for a local pick.
        assert not sing_app.download_queue["items"]
