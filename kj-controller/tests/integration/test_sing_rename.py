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

    def test_rename_requires_device_id(self, client, token):
        # Without a device_id the rename can't be made sticky — reject it rather
        # than silently doing a one-off rename that reverts on the next song.
        resp = client.post(
            f"/sing/rename?t={token}",
            json={"new_name": "Lyle", "device_id": "", "items": []},
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


class TestMergedIdentitySelfRename:
    """After a KJ merges two name-variants into one singer, that singer is ONE
    identity. A subsequent self-rename from any of her devices must carry the
    WHOLE merged group — not just the entries the one calling device owns —
    otherwise the singer re-splits under two names (the reported "Jasmine" /
    "Jasmine!" bug).
    """

    def _singer_names(self, sing_app):
        entries = sing_app.rotation.store.get_all_entries()
        return sorted(e["singer"] for e in entries)

    def test_self_rename_after_merge_renames_whole_group(
        self, client, auto_approve, token
    ):
        sing_app = auto_approve
        # Same person, two browser sessions → two device_ids, two typed variants.
        r1 = _submit(client, token, device_id="dev-jas-a",
                     singer_name="Jasmine", title="Song A")
        r2 = _submit(client, token, device_id="dev-jas-b",
                     singer_name="Jasmine!", title="Song B")
        e1 = r1.get_json()["request"]["linked_entry_id"]
        e2 = r2.get_json()["request"]["linked_entry_id"]
        assert e1 and e2

        # KJ merges the two variants into one displayed singer.
        assert client.post(
            "/rotation/singer/merge",
            json={"source_name": "Jasmine", "target_name": "Jasmine!"},
        ).status_code == 200
        assert self._singer_names(sing_app) == ["Jasmine!", "Jasmine!"]

        # She renames herself on ONE phone (device A only knows its own song).
        rr = r1.get_json()["request"]
        resp = client.post(
            f"/sing/rename?t={token}",
            json={"new_name": "Jazz", "device_id": "dev-jas-a",
                  "items": [{"id": rr["id"], "edit_token": rr["edit_token"]}]},
        )
        assert resp.status_code == 200

        # BOTH rotation entries must now read "Jazz" — no re-split.
        assert self._singer_names(sing_app) == ["Jazz", "Jazz"]
        assert sing_app.rotation.store.get_entry(e1)["singer"] == "Jazz"
        assert sing_app.rotation.store.get_entry(e2)["singer"] == "Jazz"

    def test_self_rename_after_merge_migrates_all_device_aliases(
        self, client, auto_approve, token
    ):
        sing_app = auto_approve
        _submit(client, token, device_id="dev-jas-a",
                singer_name="Jasmine", title="Song A")
        r2 = _submit(client, token, device_id="dev-jas-b",
                     singer_name="Jasmine!", title="Song B")
        client.post("/rotation/singer/merge",
                    json={"source_name": "Jasmine", "target_name": "Jasmine!"})

        rr2 = r2.get_json()["request"]
        client.post(
            f"/sing/rename?t={token}",
            json={"new_name": "Jazz", "device_id": "dev-jas-b",
                  "items": [{"id": rr2["id"], "edit_token": rr2["edit_token"]}]},
        )
        # Both devices' aliases follow the new name so neither re-splits on a
        # future submission.
        assert sing_app.sing_store.get_alias("dev-jas-a") == "Jazz"
        assert sing_app.sing_store.get_alias("dev-jas-b") == "Jazz"

    def test_self_rename_without_merge_stays_scoped(self, client, auto_approve, token):
        """No merge ⇒ no shared identity: two coincidental same-name walk-ins
        must NOT rename each other. The escalation only fires for a KJ-merged
        identity."""
        sing_app = auto_approve
        r_a = _submit(client, token, device_id="dev-mike-a",
                      singer_name="Mike", title="Song A")
        r_b = _submit(client, token, device_id="dev-mike-b",
                      singer_name="Mike", title="Song B")
        e_b = r_b.get_json()["request"]["linked_entry_id"]

        rr = r_a.get_json()["request"]
        client.post(
            f"/sing/rename?t={token}",
            json={"new_name": "Mike A", "device_id": "dev-mike-a",
                  "items": [{"id": rr["id"], "edit_token": rr["edit_token"]}]},
        )
        # Only device A's own entry renamed; the other Mike is untouched.
        assert sing_app.rotation.store.get_entry(e_b)["singer"] == "Mike"
        assert sing_app.sing_store.get_alias("dev-mike-b") is None

    def test_double_self_rename_never_hijacks_a_coincidental_name(
        self, client, auto_approve, token
    ):
        """A singer's OWN alias must never unlock a whole-group rename. Device A
        self-renames into "Mike" (creating a 'self' alias), then self-renames
        again FROM "Mike" — the independent "Mike" walk-in (device B) must stay
        untouched because only a KJ merge establishes a shared identity."""
        sing_app = auto_approve
        r_a = _submit(client, token, device_id="dev-alpha",
                      singer_name="Alpha", title="Song A")
        r_b = _submit(client, token, device_id="dev-beta",
                      singer_name="Mike", title="Song B")
        e_b = r_b.get_json()["request"]["linked_entry_id"]

        # First self-rename: Alpha → Mike (collides with the other singer's name).
        rr = r_a.get_json()["request"]
        client.post(
            f"/sing/rename?t={token}",
            json={"new_name": "Mike", "device_id": "dev-alpha",
                  "items": [{"id": rr["id"], "edit_token": rr["edit_token"]}]},
        )
        # Second self-rename from the now-shared name "Mike" → "Mikey".
        rr2 = sing_app.sing_store.get_request(rr["id"])
        client.post(
            f"/sing/rename?t={token}",
            json={"new_name": "Mikey", "device_id": "dev-alpha",
                  "items": [{"id": rr["id"], "edit_token": rr2["edit_token"]}]},
        )
        # Device B's genuinely-separate "Mike" entry must NOT have been renamed.
        assert sing_app.rotation.store.get_entry(e_b)["singer"] == "Mike"
        assert sing_app.sing_store.get_alias("dev-beta") is None
