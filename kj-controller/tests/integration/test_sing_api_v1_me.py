"""App-facing singer API v1: GET /me ("Your songs") and PUT /me (name, phone,
photo consent).

/me lists the account's songs tonight from every device linked to it (never
client-supplied ids), plus queued entries naming the singer that the host or a
duet partner added. Every response is checked against
docs/api/singer-v1.openapi.json.
"""

import hashlib
import uuid

import pytest

import sing
from app_identity import app_device_id
from history_ids import history_id_for
from tests.integration.v1_contract import V1, assert_matches_contract, bearer

EMAIL = "sam.singer@example.com"
OTHER = "olive.other@example.com"


def _expect(resp, op, status, error=None):
    assert resp.status_code == status, resp.get_json()
    body = assert_matches_contract(resp, op)
    if error is not None:
        assert body["error"] == error
    return body


def _join(client, tok, t, name="Sam S"):
    _expect(client.post(f"{V1}/join", json={"t": t, "name": name}, headers=bearer(tok)),
            "joinNight", 200)


def _song(title="Bo Rhap", **o):
    body = {"song_artist": "Queen", "song_title": title, "source_type": "local",
            "source_ref": f"/{title}.mp4"}
    body.update(o)
    return body


def _submit(client, tok, t, **body):
    payload = {"t": t, "client_request_id": str(uuid.uuid4()), **(body or _song())}
    return _expect(client.post(f"{V1}/requests", json=payload, headers=bearer(tok)),
                   "createRequest", 200)["request"]


def _web_submit(client, t, device_id, singer="Sam S", phone="", **o):
    body = {"singer_name": singer, "phone": phone, "device_id": device_id, **_song(**o)}
    resp = client.post(f"/sing/submit?t={t}", json=body)
    assert resp.status_code == 200, resp.get_json()
    return resp.get_json()["request"]


def _me(client, tok, t):
    return _expect(client.get(f"{V1}/me", query_string={"t": t}, headers=bearer(tok)),
                   "getMe", 200)


def _put_me(client, tok, t, **body):
    return client.put(f"{V1}/me", json={"t": t, **body}, headers=bearer(tok))


def _link(sing_app, device_id, email=EMAIL):
    store = sing_app.sing_store
    store.link_account(device_id, email, "web", store.get_night_started_at())


def _titles(songs):
    return [s["request"]["song_title"] for s in songs]


@pytest.fixture
def sam(client, token, app_token):
    tok = app_token(EMAIL, "Sam Singer")
    _join(client, tok, token)
    return tok


@pytest.fixture(autouse=True)
def _fresh_rate_limits():
    sing._rate_limit_state.clear()
    yield
    sing._rate_limit_state.clear()


# --- GET /me -----------------------------------------------------------------

def test_me_empty_before_any_song(client, token, sam):
    body = _me(client, sam, token)
    assert body["me"] == {"name": "Sam S", "phone": None, "photo_consent": None,
                          "instagram": None}
    assert body["songs"] == [] and body["sung_tonight"] == []
    assert body["next"] is None
    assert body["unlinked_identity_entries"] == 0


def test_me_lists_owned_host_and_partner_songs(client, token, sam, sing_app):
    sing_app.sing_store.set_auto_approve(True)
    own = _submit(client, sam, token, **_song("Mine"))
    _link(sing_app, "web-sam")
    web = _web_submit(client, token, "web-sam", singer="Sam S", title="From Web")
    host = sing_app.rotation.add_entry("Sam S", "Host Pick - H")
    _web_submit(client, token, "web-bob", singer="Bob", title="Duet",
                additional_singers=[{"name": "Sam S"}])

    body = _me(client, sam, token)
    by_title = {s["request"]["song_title"]: s for s in body["songs"]}
    assert set(by_title) == {"Mine", "From Web", "Host Pick - H", "Duet - Queen"}
    assert by_title["Mine"]["owner"] == "me"
    assert by_title["Mine"]["request"]["id"] == own["id"]
    assert by_title["From Web"]["owner"] == "me"
    assert by_title["From Web"]["request"]["id"] == web["id"]
    assert by_title["Host Pick - H"]["owner"] == "host"
    assert by_title["Host Pick - H"]["entry_id"] == host["id"]
    assert by_title["Duet - Queen"]["owner"] == "partner"
    assert by_title["Duet - Queen"]["added_by"] == "Bob"
    for s in body["songs"]:
        assert "edit_token" not in s["request"]
        assert "history_id" not in s
    # Queue order (A.49): positions ascend.
    positions = [s["estimate"]["position"] for s in body["songs"]]
    assert positions == sorted(positions)
    assert body["next"]["entry_id"] == body["songs"][0]["request"]["linked_entry_id"]
    assert body["next"]["position"] == positions[0]


def test_me_lists_more_than_the_web_id_cap(client, token, sam):
    """The web /my-requests takes at most 20 ids; /me has no such cap."""
    n = sing._MY_REQUESTS_MAX_IDS + 2
    for i in range(n):
        _submit(client, sam, token, **_song(f"Song {i:02d}"))
        sing._rate_limit_state.clear()
    body = _me(client, sam, token)
    assert sorted(_titles(body["songs"])) == [f"Song {i:02d}" for i in range(n)]


def test_me_never_lists_other_devices_songs(client, token, sam, app_token, sing_app):
    sing_app.sing_store.set_auto_approve(True)
    _submit(client, sam, token, **_song("Mine"))
    _web_submit(client, token, "web-bob", singer="Bob", title="Bobs")
    olive = app_token(OTHER, "Olive O")
    _join(client, olive, token, "Olive O")
    _submit(client, olive, token, **_song("Olives"))
    sing_app.rotation.add_entry("Someone Else", "Stranger - S")
    # Client-supplied ids are ignored.
    resp = client.get(f"{V1}/me", query_string={"t": token, "ids": "1,2,3"},
                      headers=bearer(sam))
    body = _expect(resp, "getMe", 200)
    assert _titles(body["songs"]) == ["Mine"]
    assert _titles(_me(client, olive, token)["songs"]) == ["Olives"]


def test_me_excludes_previous_night(client, token, sam, sing_app):
    old = _submit(client, sam, token, **_song("Yesterday"))
    _submit(client, sam, token, **_song("Tonight"))
    conn = sing_app.sing_store._get_conn()
    conn.execute("UPDATE sing_requests SET created_at='2000-01-01 00:00:00' WHERE id=?",
                 (old["id"],))
    conn.commit()
    assert _titles(_me(client, sam, token)["songs"]) == ["Tonight"]


def test_me_hides_cancelled_tips_and_reorders(client, token, sam, sing_app):
    sing_app.sing_store.set_auto_approve(True)
    gone = _submit(client, sam, token, **_song("Gone"))
    a = _submit(client, sam, token, **_song("A"))
    b = _submit(client, sam, token, **_song("B"))
    _expect(client.post(f"{V1}/requests/{gone['id']}/cancel", json={"t": token},
                        headers=bearer(sam)), "cancelRequest", 200)
    _expect(client.post(f"{V1}/requests/reorder",
                        json={"t": token, "items": [{"id": b["id"]}, {"id": a["id"]}]},
                        headers=bearer(sam)), "reorderRequests", 200)
    body = _me(client, sam, token)
    assert _titles(body["songs"]) == ["B", "A"]
    assert all(s["request"]["source_type"] not in ("reorder", "tip") for s in body["songs"])


def test_me_host_removed_song_stays_with_removed_flag(client, token, sam, sing_app):
    sing_app.sing_store.set_auto_approve(True)
    req = _submit(client, sam, token, **_song("Removed"))
    entry_id = sing_app.sing_store.get_request(req["id"])["linked_entry_id"]
    sing_app.rotation.update_status(entry_id, "Cancelled")
    body = _me(client, sam, token)
    assert body["songs"][0]["removed"] is True
    assert body["next"] is None


def test_me_sung_have_history_id(client, token, sam, sing_app):
    sing_app.sing_store.set_auto_approve(True)
    sung = _submit(client, sam, token, **_song("Sung"))
    _submit(client, sam, token, **_song("Queued"))
    entry_id = sing_app.sing_store.get_request(sung["id"])["linked_entry_id"]
    sing_app.rotation.update_status(entry_id, "Done")
    body = _me(client, sam, token)
    assert _titles(body["songs"]) == ["Queued"]
    assert "history_id" not in body["songs"][0]
    assert _titles(body["sung_tonight"]) == ["Sung"]
    item = body["sung_tonight"][0]
    assert item["performed"] is True and item["owner"] == "me"
    expected = "kj_" + hashlib.sha256(f"nomadpc:{entry_id}:{EMAIL}".encode()).hexdigest()[:24]
    assert item["history_id"] == expected
    assert item["history_id"] == history_id_for("nomadpc", entry_id, EMAIL)


def test_history_id_for_lowercases_email():
    assert history_id_for("box", 7, "Sam.Singer@Example.COM ") == \
        history_id_for("box", 7, "sam.singer@example.com")
    assert history_id_for("box", 7, EMAIL).startswith("kj_")
    assert len(history_id_for("box", 7, EMAIL)) == 3 + 24
    assert history_id_for("box", 7, EMAIL) != history_id_for("box", 8, EMAIL)


def test_me_friend_songs_marked_and_excluded_from_next(client, token, sam, sing_app):
    """A.13: the friend's song is first in the queue but isn't "your next song"."""
    sing_app.sing_store.set_auto_approve(True)
    friend = _submit(client, sam, token, **_song("For Nats"), for_friend=True,
                     friend_name="Nats")
    own = _submit(client, sam, token, **_song("Mine"))
    body = _me(client, sam, token)
    assert _titles(body["songs"]) == ["For Nats", "Mine"]
    assert body["songs"][0]["owner"] == "friend"
    assert body["songs"][0]["request"]["for_friend"] is True
    assert body["songs"][1]["owner"] == "me"
    own_entry = sing_app.sing_store.get_request(own["id"])["linked_entry_id"]
    assert body["next"]["entry_id"] == own_entry
    assert body["next"]["position"] == 2
    assert friend["id"] != own["id"]
    for key in ("eta_s", "range_low_s", "range_high_s"):
        assert isinstance(body["next"][key], int)


def test_unlinked_identity_entries_counted(client, token, sam, sing_app):
    sing_app.sing_store.set_auto_approve(True)
    _submit(client, sam, token, **_song("Mine"))
    _web_submit(client, token, "web-sam", singer="Sam S", title="From Web")
    _web_submit(client, token, "web-bob", singer="Bob", title="Bobs")
    body = _me(client, sam, token)
    assert body["unlinked_identity_entries"] == 1
    _link(sing_app, "web-sam")
    body = _me(client, sam, token)
    assert body["unlinked_identity_entries"] == 0
    by_title = {s["request"]["song_title"]: s for s in body["songs"]}
    assert by_title["From Web"]["owner"] == "me"


def test_unlinked_own_web_request_is_owner_web(client, token, sam, sing_app):
    sing_app.sing_store.set_auto_approve(True)
    _web_submit(client, token, "web-sam", singer="Sam S", title="From Web")
    body = _me(client, sam, token)
    [item] = body["songs"]
    assert item["owner"] == "web"
    assert item["request"]["id"] is None and item["added_by"] is None
    assert item["added_by_host"] is False


def test_me_closed_and_unauthenticated(client, token, sam, sing_app):
    _expect(client.get(f"{V1}/me", query_string={"t": token}), "getMe", 401, "unauthenticated")
    sing_app.sing_store.set_enabled(False)
    _expect(client.get(f"{V1}/me", query_string={"t": token}, headers=bearer(sam)),
            "getMe", 403, "not_open")


# --- PUT /me -----------------------------------------------------------------

def test_rename_guard_conflict_then_confirm(client, token, sam, sing_app):
    sing_app.sing_store.set_auto_approve(True)
    req = _submit(client, sam, token, **_song("Mine"))
    entry_id = sing_app.sing_store.get_request(req["id"])["linked_entry_id"]
    body = _expect(_put_me(client, sam, token, name="Alex B"), "updateMe", 409, "conflict")
    assert body["reason"] == "rename_guard"
    assert body["from"] == "Sam S" and body["to"] == "Alex B"
    assert sing_app.sing_store.get_alias(app_device_id(EMAIL, sing_app.app_identity_secret)) == "Sam S"
    assert sing_app.rotation.store.get_entry(entry_id)["singer"] == "Sam S"

    body = _expect(_put_me(client, sam, token, name="Alex B", confirm_rename=True),
                   "updateMe", 200)
    assert body["me"]["name"] == "Alex B"
    assert sing_app.sing_store.get_alias(app_device_id(EMAIL, sing_app.app_identity_secret)) == "Alex B"
    assert sing_app.rotation.store.get_entry(entry_id)["singer"] == "Alex B"
    assert sing_app.sing_store.get_request(req["id"])["singer_name"] == "Alex B"


def test_rename_spelling_fix_and_no_songs_go_straight_through(client, token, sam, sing_app):
    sing_app.sing_store.set_auto_approve(True)
    # No songs: any rename goes through.
    body = _expect(_put_me(client, sam, token, name="Alex B"), "updateMe", 200)
    assert body["me"]["name"] == "Alex B"
    req = _submit(client, sam, token, **_song("Mine"))
    # Same first name: a spelling fix, no guard.
    body = _expect(_put_me(client, sam, token, name="alex  Bee"), "updateMe", 200)
    assert body["me"]["name"] == "alex Bee"
    assert sing_app.sing_store.get_alias(app_device_id(EMAIL, sing_app.app_identity_secret)) == "alex Bee"
    assert sing_app.sing_store.get_request(req["id"])["singer_name"] == "alex Bee"


def test_rename_does_not_touch_done_entries(client, token, sam, sing_app):
    sing_app.sing_store.set_auto_approve(True)
    sung = _submit(client, sam, token, **_song("Sung"))
    queued = _submit(client, sam, token, **_song("Queued"))
    friend = _submit(client, sam, token, **_song("For Nats"), for_friend=True,
                     friend_name="Nats")
    store = sing_app.sing_store
    sung_entry = store.get_request(sung["id"])["linked_entry_id"]
    sing_app.rotation.update_status(sung_entry, "Done")
    _expect(_put_me(client, sam, token, name="Roy O", confirm_rename=True), "updateMe", 200)
    assert sing_app.rotation.store.get_entry(sung_entry)["singer"] == "Sam S"
    assert store.get_request(sung["id"])["singer_name"] == "Sam S"
    queued_entry = store.get_request(queued["id"])["linked_entry_id"]
    assert sing_app.rotation.store.get_entry(queued_entry)["singer"] == "Roy O"
    friend_row = store.get_request(friend["id"])
    assert friend_row["singer_name"] == "Nats"
    assert sing_app.rotation.store.get_entry(friend_row["linked_entry_id"])["singer"] == "Nats"


def test_rename_applies_to_linked_web_device(client, token, sam, sing_app):
    sing_app.sing_store.set_auto_approve(True)
    _link(sing_app, "web-sam")
    web = _web_submit(client, token, "web-sam", singer="Sam S", title="From Web")
    _expect(_put_me(client, sam, token, name="Sam Smith"), "updateMe", 200)
    assert sing_app.sing_store.get_request(web["id"])["singer_name"] == "Sam Smith"
    assert sing_app.sing_store.get_alias("web-sam") == "Sam Smith"


def test_phone_update_applies_to_owned_requests(client, token, sam, sing_app):
    own = _submit(client, sam, token, **_song("Mine"))
    friend = _submit(client, sam, token, **_song("For Nats"), for_friend=True,
                     friend_name="Nats")
    _link(sing_app, "web-sam")
    web = _web_submit(client, token, "web-sam", singer="Sam S", title="From Web")
    other = _web_submit(client, token, "web-bob", singer="Bob", title="Bobs")
    body = _expect(_put_me(client, sam, token, phone="+1 555 0100"), "updateMe", 200)
    assert body["me"]["phone"] == "+1 555 0100"
    store = sing_app.sing_store
    assert store.get_request(own["id"])["phone"] == "+1 555 0100"
    assert store.get_request(web["id"])["phone"] == "+1 555 0100"
    assert (store.get_request(friend["id"])["phone"] or "") == ""
    assert (store.get_request(other["id"])["phone"] or "") == ""
    assert _me(client, sam, token)["me"]["phone"] == "+1 555 0100"
    _expect(_put_me(client, sam, token, phone="call me"), "updateMe", 400, "invalid")


def test_photo_consent_applies_to_own_name(client, token, sam, sing_app):
    _submit(client, sam, token, **_song("Mine"))
    _submit(client, sam, token, **_song("For Nats"), for_friend=True, friend_name="Nats")
    body = _expect(_put_me(client, sam, token, photo_consent="yes", instagram="@Sam.Sings"),
                   "updateMe", 200)
    assert body["me"]["photo_consent"] == "yes"
    assert body["me"]["instagram"] == "sam.sings"
    store = sing_app.sing_store
    assert store.get_photo_consent("Sam S") == "yes"
    assert store.get_photo_consent("Nats") is None
    body = _expect(_put_me(client, sam, token, photo_consent="no"), "updateMe", 200)
    assert body["me"]["photo_consent"] == "no"


def test_photo_consent_before_any_song_is_kept(client, token, sam, sing_app):
    body = _expect(_put_me(client, sam, token, photo_consent="no"), "updateMe", 200)
    assert body["me"]["photo_consent"] == "no"
    assert sing_app.sing_store.get_photo_consent("Sam S") == "no"


def test_put_me_validates(client, token, sam, app_token):
    _expect(_put_me(client, sam, token, name="   "), "updateMe", 400, "invalid")
    _expect(_put_me(client, sam, token, name="x" * 101), "updateMe", 400, "invalid")
    _expect(_put_me(client, sam, token, photo_consent="maybe"), "updateMe", 400, "invalid")
    _expect(_put_me(client, sam, token, instagram="not a handle!"), "updateMe", 400, "invalid")
    _expect(_put_me(client, sam, token, confirm_rename="yes"), "updateMe", 400, "invalid")
    _expect(_put_me(client, sam, token), "updateMe", 400, "invalid")
    newbie = app_token("new.person@example.com", "New Person")
    _expect(_put_me(client, newbie, token, phone="+1 555 0100"), "updateMe", 400, "needs_name")
    # A name sets it for someone who never joined.
    body = _expect(_put_me(client, newbie, token, name="New P"), "updateMe", 200)
    assert body["me"]["name"] == "New P"


def test_sung_friend_song_has_no_history_id(client, token, sam, sing_app):
    sing_app.sing_store.set_auto_approve(True)
    friend = _submit(client, sam, token, **_song("For Nats"), for_friend=True,
                     friend_name="Nats")
    entry_id = sing_app.sing_store.get_request(friend["id"])["linked_entry_id"]
    sing_app.rotation.update_status(entry_id, "Done")
    [item] = _me(client, sam, token)["sung_tonight"]
    assert item["owner"] == "friend" and item["performed"] is True
    assert "history_id" not in item


def test_sung_host_and_partner_entries_reach_sung_tonight(client, token, sam, sing_app):
    sing_app.sing_store.set_auto_approve(True)
    host = sing_app.rotation.add_entry("Sam S", "Host Pick - H")
    duet = _web_submit(client, token, "web-bob", singer="Bob", title="Duet",
                       additional_singers=[{"name": "Sam S"}])
    stranger = sing_app.rotation.add_entry("Someone Else", "Stranger - S")
    duet_entry = sing_app.sing_store.get_request(duet["id"])["linked_entry_id"]
    for eid in (host["id"], duet_entry, stranger["id"]):
        sing_app.rotation.update_status(eid, "Done")
    body = _me(client, sam, token)
    assert body["songs"] == []
    by_entry = {s["entry_id"]: s for s in body["sung_tonight"]}
    assert set(by_entry) == {host["id"], duet_entry}
    assert by_entry[host["id"]]["owner"] == "host"
    assert by_entry[duet_entry]["owner"] == "partner"
    assert by_entry[duet_entry]["added_by"] == "Bob"
    for eid, item in by_entry.items():
        assert item["performed"] is True
        assert item["history_id"] == history_id_for("nomadpc", eid, EMAIL)


def test_sung_unlinked_web_entry_listed_without_history_id(client, token, sam, sing_app):
    """The unlinked web page claims the name too, so the song isn't proven to
    be this account's until the page is linked."""
    sing_app.sing_store.set_auto_approve(True)
    web = _web_submit(client, token, "web-sam", singer="Sam S", title="From Web")
    web_entry = sing_app.sing_store.get_request(web["id"])["linked_entry_id"]
    sing_app.rotation.update_status(web_entry, "Done")
    [item] = _me(client, sam, token)["sung_tonight"]
    assert item["owner"] == "web" and item["entry_id"] == web_entry
    assert item["history_id"] is None
    _link(sing_app, "web-sam")
    [item] = _me(client, sam, token)["sung_tonight"]
    assert item["owner"] == "me"
    assert item["history_id"] == history_id_for("nomadpc", web_entry, EMAIL)


def test_same_name_walk_in_blocks_history_id(client, token, sam, sing_app):
    """Two phones are "Sam S" tonight: a host-added Sam S song that was sung
    can't be attributed to either by name (A5-R10)."""
    sing_app.sing_store.set_auto_approve(True)
    own = _submit(client, sam, token, **_song("Mine"))
    _web_submit(client, token, "walk-in", singer="Sam S", title="Theirs")
    host = sing_app.rotation.add_entry("Sam S", "Host Pick - H")
    sing_app.rotation.update_status(host["id"], "Done")
    own_entry = sing_app.sing_store.get_request(own["id"])["linked_entry_id"]
    sing_app.rotation.update_status(own_entry, "Done")
    by_entry = {s["request"]["linked_entry_id"]: s for s in _me(client, sam, token)["sung_tonight"]}
    assert by_entry[host["id"]]["owner"] == "host"
    assert by_entry[host["id"]]["history_id"] is None
    # The account's own request is still its own.
    assert by_entry[own_entry]["history_id"] == history_id_for("nomadpc", own_entry, EMAIL)
    # An alias tonight claims the name too (no request needed).
    store = sing_app.sing_store
    assert store.name_claimed_by_others("sam s", [app_device_id(EMAIL, sing_app.app_identity_secret)],
                                        store.get_night_started_at())
    assert not store.name_claimed_by_others("nobody", [app_device_id(EMAIL, sing_app.app_identity_secret)],
                                            store.get_night_started_at())
    store.set_alias("alias-only", "Zed Z")
    assert store.name_claimed_by_others("zed z", [app_device_id(EMAIL, sing_app.app_identity_secret)],
                                        store.get_night_started_at())
    assert not store.name_claimed_by_others("zed z", ["alias-only"],
                                            store.get_night_started_at())
    assert store.name_claimed_by_others("zed z", [], None)   # fails closed


def _traced_statements(sing_app, fn):
    """SQL statements run on the request store while ``fn`` runs."""
    seen = []
    conn = sing_app.sing_store._get_conn()
    conn.set_trace_callback(seen.append)
    try:
        fn()
    finally:
        conn.set_trace_callback(None)
    return seen


def test_me_cost_tracks_tonight_not_past_nights(client, token, sam, sing_app):
    """The event code is reused across nights: /me must not read (or loop
    over) every past request under it on each 15 s poll."""
    sing_app.sing_store.set_auto_approve(True)
    _submit(client, sam, token, **_song("Mine"))
    _web_submit(client, token, "web-sam", singer="Sam S", title="From Web")
    sing_app.rotation.add_entry("Sam S", "Host Pick - H")
    before = _traced_statements(sing_app, lambda: _me(client, sam, token))

    store = sing_app.sing_store
    for i in range(150):
        for dev, name in ((app_device_id(EMAIL, sing_app.app_identity_secret), "Sam S"), ("web-sam", "Sam S"),
                          ("web-old", "Old Timer")):
            row = store.create_request(name, "", "Old", f"Song {i}", token=token,
                                       device_id=dev)
            store.mark_approved(row["id"], linked_entry_id=None)
    conn = store._get_conn()
    conn.execute("UPDATE sing_requests SET created_at='2000-01-01 00:00:00' "
                 "WHERE song_artist='Old'")
    conn.commit()
    after = _traced_statements(sing_app, lambda: _me(client, sam, token))

    assert len(after) == len(before)
    body = _me(client, sam, token)
    assert "Song 1" not in _titles(body["songs"])
    # No read of the request table that isn't keyed by id or night-scoped.
    for sql in after:
        if "FROM sing_requests" in sql and sql.lstrip().upper().startswith("SELECT"):
            assert ("created_at >=" in sql or "WHERE id" in sql
                    or "r.created_at >=" in sql), sql


def test_rename_refused_when_host_named(client, token, sam, sing_app):
    store = sing_app.sing_store
    store.set_alias(app_device_id(EMAIL, sing_app.app_identity_secret), "Sam S", origin="kj")
    for name in ("Alex B", "Sam Smith"):
        body = _expect(_put_me(client, sam, token, name=name, confirm_rename=True),
                       "updateMe", 409, "conflict")
        assert body["reason"] == "host_named"
        assert body["from"] == "Sam S" and body["to"] == name
    assert store.get_alias(app_device_id(EMAIL, sing_app.app_identity_secret)) == "Sam S"
    # Phone and consent still work for a host-named singer.
    _expect(_put_me(client, sam, token, photo_consent="yes"), "updateMe", 200)


def test_rename_refused_when_another_device_is_host_merged(client, token, sam, sing_app):
    sing_app.sing_store.set_auto_approve(True)
    _submit(client, sam, token, **_song("Mine"))
    # The host merged some other phone into "Sam S": a KJ-established identity.
    sing_app.sing_store.set_alias("web-merged", "Sam S", origin="kj")
    body = _expect(_put_me(client, sam, token, name="Alex B", confirm_rename=True),
                   "updateMe", 409, "conflict")
    assert body["reason"] == "host_named"


def test_rename_with_no_songs_keeps_photo_consent(client, token, sam, sing_app):
    _expect(_put_me(client, sam, token, photo_consent="yes", instagram="sam.sings"),
            "updateMe", 200)
    body = _expect(_put_me(client, sam, token, name="Alex B"), "updateMe", 200)
    assert body["me"]["photo_consent"] == "yes"
    assert body["me"]["instagram"] == "sam.sings"
    assert sing_app.sing_store.get_photo_consent("Alex B") == "yes"


def test_old_host_alias_does_not_block_rename(client, token, sam, sing_app):
    """Aliases outlive the night: a host spelling fix on a past night mustn't
    lock the singer out of renaming forever."""
    store = sing_app.sing_store
    store.set_alias(app_device_id(EMAIL, sing_app.app_identity_secret), "Sam S", origin="kj")
    conn = store._get_conn()
    conn.execute("UPDATE singer_aliases SET updated_at='2000-01-01 00:00:00'")
    conn.commit()
    body = _expect(_put_me(client, sam, token, name="Alex B"), "updateMe", 200)
    assert body["me"]["name"] == "Alex B"


def test_host_named_alias_alone_or_case_only_change_is_allowed(client, token, sam, sing_app):
    store = sing_app.sing_store
    # A KJ-established "Sam S" (another phone merged) with no songs of ours
    # tonight: the web rename would rewrite nothing, so it goes through.
    store.set_alias("web-merged", "Sam S", origin="kj")
    _expect(_put_me(client, sam, token, name="Sam Smith"), "updateMe", 200)
    # A host alias tonight: a case-only change is still fine.
    store.set_alias(app_device_id(EMAIL, sing_app.app_identity_secret), "Sam Smith", origin="kj")
    body = _expect(_put_me(client, sam, token, name="sam smith"), "updateMe", 200)
    assert body["me"]["name"] == "sam smith"


# --- A-R9: make songs expose their gen job id (the app opens its native review) ----

def test_me_make_songs_carry_gen_job_id(client, token, sam, sing_app):
    store = sing_app.sing_store
    device = app_device_id(EMAIL, sing_app.app_identity_secret)

    def make_req(name, title, job=None, friend=False):
        entry = sing_app.rotation.add_entry(name, f"{title} - Nova Lane")
        sing_app.rotation.mark_being_made(entry["id"])
        req = store.create_request(name, "", song_artist="Nova Lane", song_title=title,
                                   source_type="make", device_id=device)
        if job:
            store.set_request_gen(req["id"], job, "submitted")
        store.mark_approved(req["id"], linked_entry_id=entry["id"])
        if friend:
            store.mark_friend_request(req["id"], name)
        return req

    make_req("Sam S", "Made Tonight", job="job-mine-1")
    make_req("Kim", "Made For Kim", job="job-friend-2", friend=True)
    make_req("Sam S", "Not Started Yet")
    sing_app.sing_store.set_auto_approve(True)
    _submit(client, sam, token, **_song("Plain Song"))
    sing_app.rotation.add_entry("Sam S", "Host Pick - H")

    by_title = {s["request"]["song_title"]: s for s in _me(client, sam, token)["songs"]}
    assert by_title["Made Tonight"]["owner"] == "me"
    assert by_title["Made Tonight"]["gen_job_id"] == "job-mine-1"
    assert by_title["Made For Kim"]["owner"] == "friend"
    assert by_title["Made For Kim"]["gen_job_id"] == "job-friend-2"
    assert by_title["Not Started Yet"]["gen_job_id"] is None
    assert "gen_job_id" not in by_title["Plain Song"]
    host = next(s for s in by_title.values() if s["owner"] == "host")
    assert "gen_job_id" not in host
