"""Per-song media consent + Instagram handle (2026-10-14).

A singer can say "don't post THIS song" without revoking their night-wide
consent; the KJ can override per song; /history/night-media resolves every
performer's consent for the night-clips tool.
"""

import datetime as dt

import pytest

from media_consent import resolve_performer, resolve_song, split_performers
from night_log import night_of
from sing_store import SingStore


def _submit(client, token, name, consent=None, device="dev-1", **extra):
    body = {"singer_name": name, "device_id": device, "source_type": "youtube",
            "source_ref": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            "song_artist": "Rick Astley", "song_title": "Never Gonna Give You Up"}
    if consent is not None:
        body["photo_consent"] = consent
    body.update(extra)
    return client.post(f"/sing/submit?t={token}", json=body)


@pytest.fixture
def store(tmp_path):
    s = SingStore(str(tmp_path / "rot.db"))
    s.ensure_night_started()
    return s


TONIGHT = night_of(dt.datetime.now())


# --- Pure resolution --------------------------------------------------------------------

class TestResolution:
    def test_unknown_is_not_postable(self):
        performers, postable = resolve_song({"singer": "Mija"})
        assert performers[0]["consent"] == "unknown" and postable is False

    def test_night_default(self):
        d = {"mija": {"consent": "yes", "instagram": "mija.sings"}}
        performers, postable = resolve_song({"singer": "Mija"}, night_defaults=d)
        assert performers == [{"name": "Mija", "consent": "yes", "reason": "night_default",
                               "instagram": "mija.sings"}]
        assert postable

    def test_singer_song_override_beats_default(self):
        d = {"mija": {"consent": "yes"}}
        req = {"singer_name": "Mija", "media_consent": "no"}
        p, postable = resolve_song({"singer": "Mija"}, req, None, d)
        assert p[0]["consent"] == "no" and p[0]["reason"] == "song_override_singer"
        assert not postable

    def test_singer_yes_override_on_a_no_night(self):
        req = {"singer_name": "Mija", "media_consent": "yes"}
        p, postable = resolve_song({"singer": "Mija"}, req, None, {"mija": {"consent": "no"}})
        assert postable and p[0]["reason"] == "song_override_singer"

    def test_request_override_only_applies_to_requester(self):
        entry = {"singer": "A & B", "singers": ["A", "B"]}
        req = {"singer_name": "A", "media_consent": "yes"}
        p, postable = resolve_song(entry, req, None, {})
        assert [x["consent"] for x in p] == ["yes", "unknown"]
        assert not postable

    def test_any_no_override_wins(self):
        req = {"singer_name": "Mija", "media_consent": "yes"}
        p, _ = resolve_song({"singer": "Mija"}, req, {"*": "no"}, {})
        assert p[0]["consent"] == "no" and p[0]["reason"] == "song_override_kj"

    def test_kj_whole_song_override(self):
        entry = {"singer": "A & B", "singers": ["A", "B"]}
        _, postable = resolve_song(entry, None, {"*": "yes"}, {})
        assert postable

    def test_group_needs_everyone(self):
        entry = {"singer": "A & B", "singers": ["A", "B"]}
        d = {"a": {"consent": "yes"}, "b": {"consent": "no"}}
        assert resolve_song(entry, None, None, d)[1] is False
        d["b"]["consent"] = "yes"
        assert resolve_song(entry, None, None, d)[1] is True

    def test_composite_label_resolves_by_parts(self):
        d = {"ana": {"consent": "yes"}, "brooke": {"consent": "yes"}}
        assert resolve_performer("Ana & Brooke", night_defaults=d)["consent"] == "yes"
        d["brooke"]["consent"] = "no"
        assert resolve_performer("Ana, Brooke", night_defaults=d)["consent"] == "no"
        assert resolve_performer("Ana & Zed", night_defaults={"ana": {"consent": "yes"}}
                                 )["consent"] == "unknown"

    def test_name_with_and_inside_is_not_split(self):
        assert resolve_performer("Andrés", night_defaults={"andrés": {"consent": "yes"}}
                                 )["consent"] == "yes"

    def test_split_performers(self):
        assert split_performers({"singer": "X", "singers": ["A", " ", "B"]}) == ["A", "B"]
        assert split_performers({"singer": "X"}) == ["X"]
        assert split_performers({"singer": ""}) == []


# --- Store ------------------------------------------------------------------------------

class TestStore:
    @pytest.mark.parametrize("raw,want", [
        ("@Mija.Sings", "mija.sings"),
        ("https://www.instagram.com/nomadkaraoke/", "nomadkaraoke"),
        ("instagram.com/foo_bar?igsh=x", "foo_bar"),
        ("  ", None), (None, None),
    ])
    def test_normalize_instagram(self, raw, want):
        assert SingStore.normalize_instagram(raw) == want

    @pytest.mark.parametrize("raw", ["has space", "x" * 31, "bad!char", 5])
    def test_normalize_instagram_rejects(self, raw):
        with pytest.raises(ValueError):
            SingStore.normalize_instagram(raw)

    def test_handle_stored_and_kept_when_omitted(self, store):
        store.set_photo_consent("Mija", "yes", instagram="@Mija")
        assert store.get_photo_consents()["mija"]["instagram"] == "mija"
        store.set_photo_consent("Mija", "yes")
        assert store.get_photo_consents()["mija"]["instagram"] == "mija"

    def test_events_reconstruct_night_after_row_overwritten(self, store):
        store.set_photo_consent("Mija", "yes", instagram="mija")
        # A later night overwrites the per-name row...
        conn = store._get_conn()
        conn.execute("UPDATE singer_photo_consent SET consent='no', updated_at='2099-01-01 21:00:00'")
        conn.commit()
        defaults, source = store.get_night_consent_defaults(TONIGHT)
        assert source == "events"
        assert defaults["mija"] == {"consent": "yes", "instagram": "mija"}

    def test_last_event_wins_and_clear_removes(self, store):
        store.set_photo_consent("A", "yes")
        store.set_photo_consent("A", "no")
        store.set_photo_consent("B", "yes")
        store.set_photo_consent("B", None)
        defaults, _ = store.get_night_consent_defaults(TONIGHT)
        assert defaults == {"a": {"consent": "no", "instagram": None}}

    def test_legacy_night_uses_rows_updated_that_night(self, store):
        conn = store._get_conn()
        conn.execute("INSERT INTO singer_photo_consent (name_key, display_name, consent, "
                     "updated_at) VALUES ('old', 'Old', 'yes', '2020-01-02 23:00:00')")
        conn.commit()
        defaults, source = store.get_night_consent_defaults("2020-01-02")
        assert source == "legacy" and defaults == {"old": {"consent": "yes", "instagram": None}}

    def test_song_overrides_keyed_by_entry_and_created_at(self, store):
        store.set_song_media_consent(7, "2026-10-08 21:00:00", "Mija", "no")
        store.set_song_media_consent(7, "2026-10-15 21:00:00", None, "yes")
        got = store.get_song_media_consents()
        assert got[(7, "2026-10-08 21:00:00")] == {"mija": "no"}
        assert got[(7, "2026-10-15 21:00:00")] == {"*": "yes"}
        store.set_song_media_consent(7, "2026-10-08 21:00:00", "Mija", None)
        assert (7, "2026-10-08 21:00:00") not in store.get_song_media_consents()

    def test_rename_carries_handle_and_song_overrides(self, store):
        store.set_photo_consent("Andy", "yes", instagram="andy")
        store.set_song_media_consent(3, "c", "Andy", "no")
        store.carry_photo_consent("Andy", "Andrew")
        assert store.get_photo_consents()["andrew"]["instagram"] == "andy"
        assert store.get_song_media_consents()[(3, "c")] == {"andrew": "no"}


# --- Endpoints --------------------------------------------------------------------------

class TestSingerEndpoints:
    def test_submit_with_handle(self, flask_test_client, flask_app):
        token = flask_app.sing_store.ensure_token()
        r = _submit(flask_test_client, token, "Mija", consent="yes", instagram="@Mija")
        assert r.status_code == 200
        assert flask_app.sing_store.get_photo_consents()["mija"]["instagram"] == "mija"

    def test_submit_bad_handle_400(self, flask_test_client, flask_app):
        token = flask_app.sing_store.ensure_token()
        assert _submit(flask_test_client, token, "M", consent="yes",
                       instagram="no spaces").status_code == 400

    def test_photo_consent_change_sets_handle(self, flask_test_client, flask_app):
        token = flask_app.sing_store.ensure_token()
        req = _submit(flask_test_client, token, "Mija", consent="yes").get_json()["request"]
        r = flask_test_client.post(f"/sing/photo-consent?t={token}", json={
            "consent": "yes", "instagram": "mija.k",
            "items": [{"id": req["id"], "edit_token": req["edit_token"]}]})
        assert r.status_code == 200
        assert flask_app.sing_store.get_photo_consents()["mija"]["instagram"] == "mija.k"

    def test_song_consent_requires_ownership(self, flask_test_client, flask_app):
        store = flask_app.sing_store
        token = store.ensure_token()
        req = _submit(flask_test_client, token, "Mija", consent="yes").get_json()["request"]
        bad = flask_test_client.post(f"/sing/song-consent?t={token}", json={
            "consent": "no", "item": {"id": req["id"], "edit_token": "nope"}})
        assert bad.status_code == 404
        ok = flask_test_client.post(f"/sing/song-consent?t={token}", json={
            "consent": "no", "item": {"id": req["id"], "edit_token": req["edit_token"]}})
        assert ok.status_code == 200
        assert ok.get_json()["request"]["media_consent"] == "no"
        assert store.get_request(req["id"])["media_consent"] == "no"
        # Night-wide choice untouched.
        assert store.get_photo_consent("Mija") == "yes"

    def test_song_consent_validation(self, flask_test_client, flask_app):
        token = flask_app.sing_store.ensure_token()
        assert flask_test_client.post(f"/sing/song-consent?t={token}",
                                      json={"consent": "maybe", "item": {}}).status_code == 400
        assert flask_test_client.post(f"/sing/song-consent?t={token}",
                                      json={"consent": "no"}).status_code == 400
        assert flask_test_client.post("/sing/song-consent?t=bad",
                                      json={"consent": "no", "item": {}}).status_code == 403


class TestKjAndNightMedia:
    def test_kj_song_override_shows_on_rotation(self, flask_test_client, flask_app):
        eid = flask_app.rotation.add_entry("Mija", "Song")
        if isinstance(eid, dict):
            eid = eid["id"]
        r = flask_test_client.post("/rotation/entry/media-consent",
                                   json={"entry_id": eid, "consent": "no"})
        assert r.status_code == 200
        entry = next(e for e in r.get_json()["entries"] if e["id"] == eid)
        assert entry["song_consent"] == {"*": "no"}
        r = flask_test_client.post("/rotation/entry/media-consent",
                                   json={"entry_id": eid, "consent": "yes", "name": "mija"})
        entry = next(e for e in r.get_json()["entries"] if e["id"] == eid)
        assert entry["song_consent"] == {"*": "no", "Mija": "yes"}

    def test_kj_override_validation(self, flask_test_client):
        assert flask_test_client.post("/rotation/entry/media-consent",
                                      json={"entry_id": "x", "consent": "no"}).status_code == 400
        assert flask_test_client.post("/rotation/entry/media-consent",
                                      json={"entry_id": 1, "consent": "ok"}).status_code == 400
        assert flask_test_client.post("/rotation/entry/media-consent",
                                      json={"entry_id": 99999, "consent": "no"}).status_code == 404

    def test_night_media_resolves_sung_songs(self, flask_test_client, flask_app):
        rot, store = flask_app.rotation, flask_app.sing_store
        a = rot.add_entry("Mija", "Make You Feel My Love - Adele")
        b = rot.add_entry("Katie B", "Sugar - Fall Out Boy")
        c = rot.add_entry("Nobody", "Not sung yet")
        a, b = (x["id"] if isinstance(x, dict) else x for x in (a, b))
        store.set_photo_consent("Mija", "yes", instagram="mija")
        store.set_photo_consent("Katie B", "yes")
        flask_test_client.post("/rotation/entry/media-consent",
                               json={"entry_id": b, "consent": "no"})
        for eid in (a, b):
            rot.update_status(eid, "Done")
        data = flask_test_client.get(f"/history/night-media?night={TONIGHT}").get_json()
        assert data["consent_source"] == "events"
        by = {s["singer"]: s for s in data["songs"]}
        assert "Nobody" not in by
        assert by["Mija"]["postable"] is True
        assert by["Mija"]["performers"][0]["instagram"] == "mija"
        assert by["Katie B"]["postable"] is False
        assert by["Katie B"]["performers"][0]["reason"] == "song_override_kj"

    def test_night_media_bad_night(self, flask_test_client):
        assert flask_test_client.get("/history/night-media?night=nope").status_code == 400


def test_singer_song_override_shows_on_rotation(flask_test_client, flask_app):
    store = flask_app.sing_store
    token = store.ensure_token()
    req = _submit(flask_test_client, token, "Mija", consent="yes").get_json()["request"]
    req = store.get_request(req["id"])
    if not req.get("linked_entry_id"):
        eid = flask_app.rotation.add_entry("Mija", "Song")
        eid = eid["id"] if isinstance(eid, dict) else eid
        store.mark_approved(req["id"], linked_entry_id=eid)
    flask_test_client.post(f"/sing/song-consent?t={token}", json={
        "consent": "no", "item": {"id": req["id"], "edit_token": req["edit_token"]}})
    entries = flask_test_client.get("/rotation").get_json()["entries"]
    entry = next(e for e in entries if e["id"] == store.get_request(req["id"])["linked_entry_id"])
    assert entry["song_consent"] == {"Mija": "no"}
    assert entry["photo_consent"] == {"Mija": "yes"}
