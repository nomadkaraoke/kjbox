"""Phone link codes: the KJ links any phone (even one that never sent a song) to
a singer by the code on its My songs screen (live 2026-11-05, private tabs)."""

import pytest


def _submit(client, token, *, device_id, singer_name, title="Risk"):
    resp = client.post(
        f"/sing/submit?t={token}",
        json={"singer_name": singer_name, "device_id": device_id, "phone": "",
              "song_artist": "Deftones", "song_title": title,
              "source_type": "local", "source_ref": "/tmp/song.mp4"},
    )
    assert resp.status_code == 200, resp.get_json()
    return resp.get_json()["request"]


def _code(client, token, device_id):
    resp = client.get(f"/sing/link-code?t={token}&device_id={device_id}")
    assert resp.status_code == 200, resp.get_json()
    return resp.get_json()


def _link(client, name, code):
    return client.post("/rotation/singer/link-phone", json={"name": name, "code": code})


@pytest.fixture
def auto_approve(sing_app):
    sing_app.sing_store.set_auto_approve(True)
    return sing_app


class TestLinkCode:
    def test_code_is_four_digits_and_stable_per_device(self, client, sing_app, token):
        a = _code(client, token, "phone-a")
        assert len(a["code"]) == 4 and a["code"].isdigit()
        assert a["linked"] is None
        assert _code(client, token, "phone-a")["code"] == a["code"]
        assert _code(client, token, "phone-b")["code"] != a["code"]

    def test_needs_device_and_open_event(self, client, sing_app, token):
        assert client.get(f"/sing/link-code?t={token}").status_code == 400
        assert client.get("/sing/link-code?t=nope&device_id=x").status_code == 403

    def test_codes_from_an_earlier_night_do_not_resolve(self, client, sing_app, token):
        code = _code(client, token, "phone-a")["code"]
        store = sing_app.sing_store
        store._get_conn().execute(
            "UPDATE device_link_codes SET created_at = '2000-01-01 00:00:00'")
        store._get_conn().commit()
        assert store.device_for_link_code(code) is None


class TestKjLinkPhone:
    def test_link_gives_the_phone_the_singers_songs_and_edit_rights(self, client, auto_approve, token):
        req = _submit(client, token, device_id="old-tab", singer_name="Alanna M")
        code = _code(client, token, "fresh-tab")["code"]

        resp = _link(client, "Alanna M", code)
        assert resp.status_code == 200
        assert resp.get_json()["success"] is True

        linked = _code(client, token, "fresh-tab")["linked"]
        assert linked == {"name": "Alanna M",
                          "items": [{"id": req["id"], "edit_token": req["edit_token"]}]}
        # My songs on the fresh tab lists the song (via the alias identity).
        songs = client.get(f"/sing/my-requests?t={token}&ids=&device_id=fresh-tab").get_json()["requests"]
        assert len(songs) == 1

    def test_works_for_a_singer_with_no_requests(self, client, sing_app, token):
        sing_app.rotation.add_entry("Gus M", "Song - Artist")
        code = _code(client, token, "fresh-tab")["code"]
        assert _link(client, "Gus M", code).status_code == 200
        assert _code(client, token, "fresh-tab")["linked"] == {"name": "Gus M", "items": []}

    def test_unknown_code_is_404_with_a_message(self, client, sing_app, token):
        resp = _link(client, "Alanna M", "0000")
        assert resp.status_code == 404
        assert resp.get_json()["error"] == "code_not_found"

    def test_requires_name_and_code(self, client, sing_app, token):
        assert _link(client, "", "1234").status_code == 400
        assert _link(client, "Alanna M", "").status_code == 400

    def test_a_self_alias_is_not_reported_as_a_host_link(self, client, sing_app, token):
        sing_app.sing_store.set_alias("phone-a", "Alanna M")   # origin self
        assert _code(client, token, "phone-a")["linked"] is None

    def test_a_host_link_from_an_earlier_night_is_not_reported(self, client, sing_app, token):
        store = sing_app.sing_store
        store.set_alias("phone-a", "Mike S", origin="kj")
        store._get_conn().execute(
            "UPDATE singer_aliases SET updated_at = '2000-01-01 00:00:00' WHERE device_id = 'phone-a'")
        store._get_conn().commit()
        assert _code(client, token, "phone-a")["linked"] is None
