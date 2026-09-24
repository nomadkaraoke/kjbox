"""Footer social links + singer social-media photo consent (v0.112.0).

Covers the store (validation, night scoping, rename carry), the KJ config and
consent endpoints, the singer submit/change endpoints, and the rotation
decoration the KJ UI renders the 📷 marker from.
"""

import json
from pathlib import Path

import pytest

from sing_store import SingStore

MESSAGES = Path(__file__).resolve().parent.parent / "static-sing" / "messages"


def _submit(client, token, name, consent=None, device="dev-1"):
    body = {"singer_name": name, "device_id": device, "source_type": "youtube",
            "source_ref": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            "song_artist": "Rick Astley", "song_title": "Never Gonna Give You Up"}
    if consent is not None:
        body["photo_consent"] = consent
    return client.post(f"/sing/submit?t={token}", json=body)


@pytest.fixture
def store(tmp_path):
    s = SingStore(str(tmp_path / "rot.db"))
    s.ensure_night_started()
    return s


# --- Social links ------------------------------------------------------------------

class TestSocialLinks:
    def test_defaults(self, store):
        fs = store.get_footer_settings()
        assert fs["social"] == {}
        assert fs["ask_photo_consent"] is False

    def test_normalises_and_drops_blanks_and_unknown_keys(self, store):
        saved = store.set_footer_settings({"social": {
            "instagram": " https://www.instagram.com/nomadkaraoke ",
            "facebook": "www.facebook.com/people/Nomad-Karaoke/61591358020477/",
            "website": "",
            "email": "mailto:andrew@nomadkaraoke.com",
            "myspace": "https://myspace.com/x",
        }})
        assert saved["social"] == {
            "instagram": "https://www.instagram.com/nomadkaraoke",
            "facebook": "https://www.facebook.com/people/Nomad-Karaoke/61591358020477/",
            "email": "andrew@nomadkaraoke.com",
        }
        # Replacing the set clears links that aren't re-sent.
        store.set_footer_settings({"social": {"website": "https://nomadkaraoke.com"}})
        assert store.get_footer_settings()["social"] == {"website": "https://nomadkaraoke.com"}

    @pytest.mark.parametrize("key,value", [
        ("instagram", "javascript:alert(1)"),
        ("website", "data:text/html,hi"),
        ("website", "not a url"),
        ("email", "andrew-at-example"),
        ("facebook", 42),
        ("website", "https://" + "x" * 400 + ".com"),
    ])
    def test_rejects_bad_values(self, store, key, value):
        with pytest.raises(ValueError):
            store.set_footer_settings({"social": {key: value}})

    def test_other_settings_untouched_by_social_update(self, store):
        store.set_footer_settings({"message": "Hi", "notices": ["wifi"]})
        store.set_footer_settings({"social": {"website": "https://a.com"}, "ask_photo_consent": True})
        fs = store.get_footer_settings()
        assert fs["message"] == "Hi" and fs["notices"] == ["wifi"]
        assert fs["ask_photo_consent"] is True

    def test_kj_config_to_singer_event_info(self, flask_test_client, flask_app):
        resp = flask_test_client.post("/rotation/requests/config", json={"footer_settings": {
            "social": {"instagram": "https://www.instagram.com/nomadkaraoke",
                       "email": "andrew@nomadkaraoke.com"},
            "ask_photo_consent": True,
        }})
        assert resp.status_code == 200
        token = flask_app.sing_store.ensure_token()
        info = flask_test_client.get(f"/sing/event-info?t={token}").get_json()
        assert info["social"] == {"instagram": "https://www.instagram.com/nomadkaraoke",
                                  "email": "andrew@nomadkaraoke.com"}
        assert info["ask_photo_consent"] is True

    def test_bad_link_is_400_with_reason(self, flask_test_client):
        resp = flask_test_client.post("/rotation/requests/config", json={
            "footer_settings": {"social": {"website": "javascript:alert(1)"}}})
        assert resp.status_code == 400
        assert "social.website" in resp.get_json()["error"]


# --- Photo consent store --------------------------------------------------------------

class TestPhotoConsentStore:
    def test_set_get_clear_case_insensitive(self, store):
        assert store.get_photo_consent("Lindsay") is None
        store.set_photo_consent("Lindsay", "yes")
        assert store.get_photo_consent("  lindsay ") == "yes"
        store.set_photo_consent("LINDSAY", "no", source="kj")
        assert store.get_photo_consents()["lindsay"] == {"consent": "no", "source": "kj"}
        store.set_photo_consent("Lindsay", None)
        assert store.get_photo_consent("Lindsay") is None

    def test_rejects_bad_values(self, store):
        with pytest.raises(ValueError):
            store.set_photo_consent("A", "maybe")
        with pytest.raises(ValueError):
            store.set_photo_consent("   ", "yes")

    def test_night_scoped(self, store):
        store.set_photo_consent("Celine", "yes")
        # A New Rotation moves the night marker past the stored choice.
        conn = store._get_conn()
        conn.execute("UPDATE singer_photo_consent SET updated_at = '2000-01-01 00:00:00'")
        conn.commit()
        assert store.get_photo_consent("Celine") is None

    def test_fails_closed_without_night_marker(self, tmp_path):
        s = SingStore(str(tmp_path / "x.db"))
        s.set_photo_consent("A", "yes")
        s._get_conn().execute("DELETE FROM rotation_meta WHERE key LIKE '%night%'")
        s._get_conn().commit()
        assert s.get_photo_consents() == {}

    def test_carry_on_rename(self, store):
        store.set_photo_consent("Bev", "yes")
        assert store.carry_photo_consent("Bev", "Bevbot") == "yes"
        assert store.get_photo_consent("Bevbot") == "yes"

    def test_carry_conflict_no_wins(self, store):
        store.set_photo_consent("A", "yes")
        store.set_photo_consent("B", "no")
        assert store.carry_photo_consent("A", "B") == "no"
        store.set_photo_consent("C", "no")
        store.set_photo_consent("D", "yes")
        assert store.carry_photo_consent("C", "D") == "no"

    def test_carry_noop_without_old_choice(self, store):
        store.set_photo_consent("B", "yes")
        assert store.carry_photo_consent("A", "B") is None
        assert store.get_photo_consent("B") == "yes"

    def test_persist_rename_carries_consent(self, store):
        store.set_photo_consent("Andy", "no")
        store.persist_rename("Andy", "Andrew")
        assert store.get_photo_consent("Andrew") == "no"


# --- Singer endpoints -----------------------------------------------------------------

class TestSingerConsentEndpoints:
    def test_submit_records_consent(self, flask_test_client, flask_app):
        token = flask_app.sing_store.ensure_token()
        resp = _submit(flask_test_client, token, "Lindsay", consent="no")
        assert resp.status_code == 200, resp.get_json()
        assert flask_app.sing_store.get_photo_consent("Lindsay") == "no"

    def test_submit_without_consent_leaves_it_unset(self, flask_test_client, flask_app):
        token = flask_app.sing_store.ensure_token()
        assert _submit(flask_test_client, token, "Celine").status_code == 200
        assert flask_app.sing_store.get_photo_consent("Celine") is None

    def test_submit_bad_consent_400(self, flask_test_client, flask_app):
        token = flask_app.sing_store.ensure_token()
        assert _submit(flask_test_client, token, "X", consent="sure").status_code == 400

    def test_change_requires_edit_token(self, flask_test_client, flask_app):
        store = flask_app.sing_store
        token = store.ensure_token()
        req = _submit(flask_test_client, token, "Bevbot", consent="yes").get_json()["request"]

        forged = flask_test_client.post(f"/sing/photo-consent?t={token}", json={
            "consent": "no", "items": [{"id": req["id"], "edit_token": "wrong"}]})
        assert forged.get_json()["updated"] == 0
        assert store.get_photo_consent("Bevbot") == "yes"

        ok = flask_test_client.post(f"/sing/photo-consent?t={token}", json={
            "consent": "no", "items": [{"id": req["id"], "edit_token": req["edit_token"]}]})
        assert ok.status_code == 200 and ok.get_json()["updated"] == 1
        assert store.get_photo_consent("Bevbot") == "no"

    def test_change_validation(self, flask_test_client, flask_app):
        token = flask_app.sing_store.ensure_token()
        assert flask_test_client.post(f"/sing/photo-consent?t={token}",
                                      json={"consent": "maybe"}).status_code == 400
        assert flask_test_client.post("/sing/photo-consent?t=bogus",
                                      json={"consent": "yes"}).status_code == 403
        none = flask_test_client.post(f"/sing/photo-consent?t={token}", json={"consent": "yes"})
        assert none.get_json() == {"success": True, "updated": 0}


# --- KJ endpoint + rotation decoration --------------------------------------------------

class TestKjConsentAndRotation:
    def test_rotation_entries_carry_per_singer_consent(self, flask_test_client, flask_app):
        rot = flask_app.rotation
        rot.add_entry("Lindsay", "Song A")
        rot.add_entry("Celine & Bevbot", "Duet", singers=["Celine", "Bevbot"])
        flask_app.sing_store.set_photo_consent("Lindsay", "no")
        flask_app.sing_store.set_photo_consent("Bevbot", "yes")

        entries = flask_test_client.get("/rotation").get_json()["entries"]
        by_singer = {e["singer"]: e for e in entries}
        assert by_singer["Lindsay"]["photo_consent"] == {"Lindsay": "no"}
        assert by_singer["Celine & Bevbot"]["photo_consent"] == {"Celine": None, "Bevbot": "yes"}

    def test_kj_sets_and_clears(self, flask_test_client, flask_app):
        flask_app.rotation.add_entry("Andrew", "Song")
        resp = flask_test_client.post("/rotation/singer/photo-consent",
                                      json={"singer": "Andrew", "consent": "yes"})
        assert resp.status_code == 200
        entry = next(e for e in resp.get_json()["entries"] if e["singer"] == "Andrew")
        assert entry["photo_consent"] == {"Andrew": "yes"}
        assert flask_app.sing_store.get_photo_consents()["andrew"]["source"] == "kj"

        resp = flask_test_client.post("/rotation/singer/photo-consent",
                                      json={"singer": "Andrew", "consent": None})
        entry = next(e for e in resp.get_json()["entries"] if e["singer"] == "Andrew")
        assert entry["photo_consent"] == {"Andrew": None}

    def test_kj_validation(self, flask_test_client):
        assert flask_test_client.post("/rotation/singer/photo-consent",
                                      json={"singer": "", "consent": "yes"}).status_code == 400
        assert flask_test_client.post("/rotation/singer/photo-consent",
                                      json={"singer": "A", "consent": "ok"}).status_code == 400

    def test_kj_rename_carries_consent(self, flask_test_client, flask_app):
        flask_app.rotation.add_entry("Andy", "Song")
        flask_app.sing_store.set_photo_consent("Andy", "no")
        resp = flask_test_client.post("/rotation/singer/rename",
                                      json={"old_name": "Andy", "new_name": "Andrew"})
        entry = next(e for e in resp.get_json()["entries"] if e["singer"] == "Andrew")
        assert entry["photo_consent"] == {"Andrew": "no"}


def test_singer_copy_exists_for_new_keys():
    en = json.loads((MESSAGES / "en.json").read_text(encoding="utf-8"))
    for key in ("question", "yes", "no", "hint", "saved", "saveFailed"):
        assert en["photoConsent"][key]
    for key in ("followUs", "website", "email"):
        assert en["footer"][key]
