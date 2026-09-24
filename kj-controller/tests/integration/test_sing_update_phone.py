"""Tests for POST /sing/update-phone — adding/changing a contact number after
submitting, written onto the device's owned request rows (where the "you're
up" SMS resolves the phone from)."""


def _submit(client, token, name="Alice", phone=""):
    resp = client.post(f"/sing/submit?t={token}", json={
        "singer_name": name, "phone": phone,
        "song_artist": "Q", "song_title": "One",
        "source_type": "local", "source_ref": "/m/one.mp4",
    })
    assert resp.status_code == 200
    return resp.get_json()["request"]


class TestUpdatePhone:
    def test_requires_token(self, client):
        assert client.post("/sing/update-phone", json={"phone": "+1 555"}).status_code == 403

    def test_rejects_bad_phone(self, client, token):
        resp = client.post(f"/sing/update-phone?t={token}",
                           json={"phone": "not-a-number", "items": []})
        assert resp.status_code == 400

    def test_updates_owned_requests_only(self, client, sing_app, token):
        mine = _submit(client, token)
        other = _submit(client, token, name="Bob")
        resp = client.post(f"/sing/update-phone?t={token}", json={
            "phone": "+1 555 111 2222",
            "device_id": "dev-x",
            "items": [
                {"id": mine["id"], "edit_token": mine["edit_token"]},
                {"id": other["id"], "edit_token": "wrong-token"},
            ],
        })
        assert resp.status_code == 200
        assert resp.get_json()["updated"] == 1
        store = sing_app.sing_store
        assert store.get_request(mine["id"])["phone"] == "+1 555 111 2222"
        assert store.get_request(other["id"])["phone"] == ""

    def test_change_overwrites_previous_number(self, client, sing_app, token):
        mine = _submit(client, token, phone="+1 555 000 0000")
        client.post(f"/sing/update-phone?t={token}", json={
            "phone": "+1 555 999 8888",
            "items": [{"id": mine["id"], "edit_token": mine["edit_token"]}],
        })
        assert sing_app.sing_store.get_request(mine["id"])["phone"] == "+1 555 999 8888"
