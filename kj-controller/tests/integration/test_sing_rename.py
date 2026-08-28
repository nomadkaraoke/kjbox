"""Integration tests for persistent singer rename.

Covers the device-alias mechanism that makes a rename (KJ-side or the singer's
own, via the portal) stick to FUTURE submissions instead of the singer
re-appearing under whatever free-text name is cached on their phone.

- /sing/submit resolves the typed name through the device's alias.
- /sing/rename (self-service) rewrites the device's owned entries + requests and
  records the alias, gated per-request by edit_token.
- /sing/forget drops the alias when a new person takes over the device.
- /rotation/singer/rename (KJ) records the alias for the renamed devices.
"""

import pytest


def _submit(client, token, *, device_id, singer_name, artist="Queen",
            title="Bohemian Rhapsody", source_ref="/tmp/song.mp4"):
    resp = client.post(
        f"/sing/submit?t={token}",
        json={
            "singer_name": singer_name,
            "device_id": device_id,
            "phone": "",
            "song_artist": artist,
            "song_title": title,
            "source_type": "local",
            "source_ref": source_ref,
        },
    )
    return resp


@pytest.fixture
def auto_approve(sing_app):
    """Auto-approve so a /sing/submit immediately produces a linked entry."""
    sing_app.sing_store.set_auto_approve(True)
    return sing_app


class TestSubmitAliasOverride:
    def test_alias_overrides_typed_name(self, client, sing_app, token):
        sing_app.sing_store.set_alias("dev-1", "Lyle")
        resp = _submit(client, token, device_id="dev-1",
                       singer_name="The only Lyle at karaoke")
        assert resp.status_code == 200
        req = resp.get_json()["request"]
        assert req["singer_name"] == "Lyle"

    def test_no_alias_keeps_typed_name(self, client, sing_app, token):
        resp = _submit(client, token, device_id="dev-2", singer_name="Dave")
        assert resp.status_code == 200
        assert resp.get_json()["request"]["singer_name"] == "Dave"

    def test_missing_device_id_keeps_typed_name(self, client, sing_app, token):
        # An alias exists for some other device, but this submit carries no id.
        sing_app.sing_store.set_alias("dev-x", "Someone Else")
        resp = client.post(
            f"/sing/submit?t={token}",
            json={
                "singer_name": "Nina", "phone": "",
                "song_artist": "A", "song_title": "B",
                "source_type": "local", "source_ref": "/tmp/s.mp4",
            },
        )
        assert resp.get_json()["request"]["singer_name"] == "Nina"


class TestSelfRename:
    def test_rename_rewrites_entry_request_and_sets_alias(self, client, auto_approve, token):
        sing_app = auto_approve
        r = _submit(client, token, device_id="dev-lyle",
                    singer_name="The only Lyle at karaoke")
        req = r.get_json()["request"]
        rid, edit_token = req["id"], req["edit_token"]
        entry_id = req["linked_entry_id"]
        assert entry_id is not None

        resp = client.post(
            f"/sing/rename?t={token}",
            json={"new_name": "Lyle", "device_id": "dev-lyle",
                  "items": [{"id": rid, "edit_token": edit_token}]},
        )
        assert resp.status_code == 200
        assert resp.get_json()["new_name"] == "Lyle"

        # Existing rotation entry renamed…
        assert sing_app.rotation.store.get_entry(entry_id)["singer"] == "Lyle"
        # …the request row renamed (provenance/done screen)…
        assert sing_app.sing_store.get_request(rid)["singer_name"] == "Lyle"
        # …and the alias recorded for future submissions.
        assert sing_app.sing_store.get_alias("dev-lyle") == "Lyle"

    def test_future_submission_uses_renamed_identity(self, client, auto_approve, token):
        sing_app = auto_approve
        r = _submit(client, token, device_id="dev-lyle",
                    singer_name="The only Lyle at karaoke")
        req = r.get_json()["request"]
        client.post(
            f"/sing/rename?t={token}",
            json={"new_name": "Lyle", "device_id": "dev-lyle",
                  "items": [{"id": req["id"], "edit_token": req["edit_token"]}]},
        )
        # The device's localStorage still holds the old name; the alias wins.
        r2 = _submit(client, token, device_id="dev-lyle",
                     singer_name="The only Lyle at karaoke", title="Another One")
        assert r2.get_json()["request"]["singer_name"] == "Lyle"

    def test_rename_with_no_owned_songs_still_sets_alias(self, client, sing_app, token):
        resp = client.post(
            f"/sing/rename?t={token}",
            json={"new_name": "Lyle", "device_id": "dev-empty", "items": []},
        )
        assert resp.status_code == 200
        assert sing_app.sing_store.get_alias("dev-empty") == "Lyle"

    def test_rename_rejects_wrong_edit_token(self, client, auto_approve, token):
        sing_app = auto_approve
        r = _submit(client, token, device_id="dev-a", singer_name="Amy")
        req = r.get_json()["request"]
        entry_id = req["linked_entry_id"]
        # Attacker knows the id but not the edit_token → entry must NOT be renamed.
        resp = client.post(
            f"/sing/rename?t={token}",
            json={"new_name": "Hacked", "device_id": "dev-a",
                  "items": [{"id": req["id"], "edit_token": "wrong"}]},
        )
        assert resp.status_code == 200
        assert sing_app.rotation.store.get_entry(entry_id)["singer"] == "Amy"
        assert sing_app.sing_store.get_request(req["id"])["singer_name"] == "Amy"

    def test_rename_requires_name(self, client, sing_app, token):
        resp = client.post(
            f"/sing/rename?t={token}",
            json={"new_name": "  ", "device_id": "dev-a", "items": []},
        )
        assert resp.status_code == 400

    def test_rename_requires_token(self, client):
        resp = client.post(
            "/sing/rename",
            json={"new_name": "Lyle", "device_id": "dev-a", "items": []},
        )
        assert resp.status_code == 403


class TestForget:
    def test_forget_clears_alias(self, client, sing_app, token):
        sing_app.sing_store.set_alias("dev-f", "Lyle")
        resp = client.post(f"/sing/forget?t={token}", json={"device_id": "dev-f"})
        assert resp.status_code == 204
        assert sing_app.sing_store.get_alias("dev-f") is None

    def test_forget_unknown_device_is_noop(self, client, token):
        resp = client.post(f"/sing/forget?t={token}", json={"device_id": "nope"})
        assert resp.status_code == 204


class TestKjRenamePersists:
    def test_kj_rename_sets_alias_for_device(self, client, auto_approve, token):
        sing_app = auto_approve
        _submit(client, token, device_id="dev-kev", singer_name="Kev")
        resp = client.post(
            "/rotation/singer/rename",
            json={"old_name": "Kev", "new_name": "Kevin"},
        )
        assert resp.status_code == 200
        assert sing_app.sing_store.get_alias("dev-kev") == "Kevin"

    def test_future_submission_after_kj_rename_uses_new_name(self, client, auto_approve, token):
        sing_app = auto_approve
        _submit(client, token, device_id="dev-kev", singer_name="Kev")
        client.post("/rotation/singer/rename",
                    json={"old_name": "Kev", "new_name": "Kevin"})
        # Device's cached name is still "Kev" but the alias resolves it.
        r2 = _submit(client, token, device_id="dev-kev", singer_name="Kev",
                     title="Second Song")
        assert r2.get_json()["request"]["singer_name"] == "Kevin"

    def test_kj_merge_sets_alias(self, client, auto_approve, token):
        sing_app = auto_approve
        _submit(client, token, device_id="dev-rob", singer_name="Robert")
        resp = client.post(
            "/rotation/singer/merge",
            json={"source_name": "Robert", "target_name": "Rob"},
        )
        assert resp.status_code == 200
        assert sing_app.sing_store.get_alias("dev-rob") == "Rob"
