"""POST /sing/identity/claim — a phone that is given a name re-joins that singer's night.

Live 2026-11-05 a singer in Safari private tabs got a fresh device id per tab, so
her new tab's My songs was empty until she submitted again.
"""

import pytest


def _submit(client, token, *, device_id, singer_name, phone="", title="Bohemian Rhapsody"):
    resp = client.post(
        f"/sing/submit?t={token}",
        json={
            "singer_name": singer_name, "device_id": device_id, "phone": phone,
            "song_artist": "Queen", "song_title": title,
            "source_type": "local", "source_ref": "/tmp/song.mp4",
        },
    )
    assert resp.status_code == 200, resp.get_json()
    return resp.get_json()["request"]


def _claim(client, token, **body):
    return client.post(f"/sing/identity/claim?t={token}", json=body)


def _my_songs(client, token, device_id, ids=""):
    resp = client.get(f"/sing/my-requests?t={token}&ids={ids}&device_id={device_id}")
    assert resp.status_code == 200
    return resp.get_json()["requests"]


@pytest.fixture
def auto_approve(sing_app):
    sing_app.sing_store.set_auto_approve(True)
    return sing_app


class TestIdentityClaim:
    def test_new_device_with_same_name_sees_songs_from_the_old_device(self, client, auto_approve, token):
        _submit(client, token, device_id="old-tab", singer_name="Alanna M")
        assert _my_songs(client, token, "new-tab") == []

        resp = _claim(client, token, device_id="new-tab", name="alanna m")
        body = resp.get_json()
        assert resp.status_code == 200
        assert body["matched"] is True
        assert body["name"] == "Alanna M"     # the list's spelling wins
        assert body["songs"] == 1
        assert body["items"] == []            # no phone → no edit rights

        songs = _my_songs(client, token, "new-tab")
        assert [s["request"]["song_title"] for s in songs] == ["Bohemian Rhapsody - Queen"]

    def test_matching_phone_returns_edit_tokens(self, client, auto_approve, token):
        req = _submit(client, token, device_id="old-tab", singer_name="Alanna M",
                      phone="(803) 677-2185")
        body = _claim(client, token, device_id="new-tab", name="Alanna M",
                      phone="803-677-2185").get_json()
        assert body["items"] == [{"id": req["id"], "edit_token": req["edit_token"]}]

    def test_wrong_phone_links_but_gives_no_edit_tokens(self, client, auto_approve, token):
        _submit(client, token, device_id="old-tab", singer_name="Alanna M", phone="8036772185")
        body = _claim(client, token, device_id="new-tab", name="Alanna M",
                      phone="5550001111").get_json()
        assert body["matched"] is True
        assert body["items"] == []

    def test_host_added_singer_is_matched_by_rotation_name(self, client, sing_app, token):
        sing_app.rotation.add_entry("Alanna M", "Tom Petty - You and Me")
        body = _claim(client, token, device_id="new-tab", name="ALANNA M").get_json()
        assert body == {"matched": True, "name": "Alanna M", "items": [], "songs": 1}
        songs = _my_songs(client, token, "new-tab")
        assert [s["entry_id"] is not None for s in songs] == [True]

    def test_future_submissions_use_the_matched_spelling(self, client, sing_app, token):
        sing_app.rotation.add_entry("Alanna M", "Tom Petty - You and Me")
        _claim(client, token, device_id="new-tab", name="alanna m")
        req = _submit(client, token, device_id="new-tab", singer_name="alanna m", title="Risk")
        assert req["singer_name"] == "Alanna M"

    def test_unknown_name_does_not_match(self, client, auto_approve, token):
        _submit(client, token, device_id="old-tab", singer_name="Alanna M")
        body = _claim(client, token, device_id="new-tab", name="Alanna").get_json()
        assert body == {"matched": False}
        assert sing_app_alias(auto_approve, "new-tab") is None

    def test_tip_claims_do_not_count_as_a_singer(self, client, sing_app, token):
        sing_app.sing_store.create_request(
            singer_name="Tipper T", phone="", song_artist="", song_title="",
            source_type="tip", source_ref=None, source_meta={"amount": 5},
            notes="Tip claim", user_agent="", device_id="old-tab", token=token,
        )
        assert _claim(client, token, device_id="new-tab", name="Tipper T").get_json() == {"matched": False}

    def test_requires_name_and_device(self, client, sing_app, token):
        assert _claim(client, token, device_id="", name="Alanna M").status_code == 400
        assert _claim(client, token, device_id="d", name="  ").status_code == 400

    def test_requires_open_event(self, client, sing_app, token):
        assert _claim(client, "wrong", device_id="d", name="Alanna M").status_code == 403


def sing_app_alias(app, device_id):
    return app.sing_store.get_alias(device_id)


class TestStrictClaim:
    """The automatic claim on page load (strict) needs a matching phone."""

    def test_strict_without_phone_match_does_not_link(self, client, sing_app, token):
        sing_app.sing_store.set_auto_approve(True)
        _submit(client, token, device_id="old-tab", singer_name="Mike S", phone="8035550100")
        body = _claim(client, token, device_id="stale", name="Mike S", strict=True).get_json()
        assert body == {"matched": False}
        assert sing_app.sing_store.get_alias("stale") is None
        body = _claim(client, token, device_id="stale", name="Mike S",
                      phone="8035559999", strict=True).get_json()
        assert body == {"matched": False}

    def test_strict_with_phone_match_links(self, client, sing_app, token):
        sing_app.sing_store.set_auto_approve(True)
        _submit(client, token, device_id="old-tab", singer_name="Mike S", phone="8035550100")
        body = _claim(client, token, device_id="new-tab", name="Mike S",
                      phone="+1 803 555 0100", strict=True).get_json()
        assert body["matched"] is True and len(body["items"]) == 1
        assert sing_app.sing_store.get_alias("new-tab") == "Mike S"
