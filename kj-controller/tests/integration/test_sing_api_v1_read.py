"""App-facing singer API v1 read endpoints: search, identify, resolve, search
event, singers, rotation (ETag), now, preview, tips and my-stats. Every response
is checked against docs/api/singer-v1.openapi.json."""

import pytest

import routes
import sing
from tests.integration.v1_contract import V1, assert_matches_contract, bearer

EMAIL = "sam.singer@example.com"


def _expect(resp, op, status, error=None):
    assert resp.status_code == status, resp.get_json()
    body = assert_matches_contract(resp, op)
    if error is not None:
        assert body["error"] == error
    return body


@pytest.fixture
def sam(client, token, app_token):
    tok = app_token(EMAIL, "Sam Singer")
    _expect(client.post(f"{V1}/join", json={"t": token, "name": "Sam S"}, headers=bearer(tok)),
            "joinNight", 200)
    return tok


@pytest.fixture(autouse=True)
def _fresh_limits():
    sing._resolve_rate_state.clear()
    sing._preview_rate_limit_state.clear()
    sing._tip_rate_limit_state.clear()
    yield
    sing._resolve_rate_state.clear()
    sing._preview_rate_limit_state.clear()
    sing._tip_rate_limit_state.clear()


def _get(client, tok, path, op, status=200, **q):
    return _expect(client.get(f"{V1}{path}", query_string=q, headers=bearer(tok)), op, status)


def _post(client, tok, path, op, body, status=200):
    return _expect(client.post(f"{V1}{path}", json=body, headers=bearer(tok)), op, status)


# --- search ------------------------------------------------------------------

GROUP = {"key": "queen|bohemian rhapsody", "artist": "Queen", "title": "Bohemian Rhapsody",
         "version_count": 1, "has_community_only": False,
         "versions": [{"source": "local", "local": {"artist": "Queen", "title": "Bohemian Rhapsody"}}]}


def test_search_contract(client, token, sam, monkeypatch):
    seen = {}

    def fake(q, app, **kw):
        seen["q"] = q
        return {"songs": [GROUP], "karaoke_nerds_timeout": True}
    monkeypatch.setattr(routes, "unified_search", fake)
    body = _get(client, sam, "/search", "searchSongs", t=token, q="bohemian")
    assert seen["q"] == "bohemian"
    assert body["songs"][0]["artist"] == "Queen"
    assert body["karaoke_nerds_timeout"] is True
    assert {"make_requests_enabled", "simple_mode"} <= set(body)


def test_search_short_query_is_invalid(client, token, sam):
    _expect(client.get(f"{V1}/search", query_string={"t": token, "q": "ab"}, headers=bearer(sam)),
            "searchSongs", 400, "invalid")


def test_search_not_open_maps_error(client, token, sam, sing_app):
    sing_app.sing_store.set_enabled(False)
    _expect(client.get(f"{V1}/search", query_string={"t": token, "q": "queen"}, headers=bearer(sam)),
            "searchSongs", 403, "not_open")


def test_search_logs_with_app_device(client, token, sam, sing_app, monkeypatch):
    monkeypatch.setattr(routes, "unified_search", lambda q, app, **kw: {"songs": []})
    logged = []

    class Log:
        def log(self, type_, **kw):
            logged.append((type_, kw))
    sing_app.search_log = Log()
    _get(client, sam, "/search", "searchSongs", t=token, q="queen", sid="s9")
    kw = logged[0][1]
    assert kw["search_id"] == "s9" and kw["device_id"]


def test_search_identify_contract(client, token, sam, sing_app):
    class Ident:
        available = True

        def identify(self, q):
            return {"status": "confident", "best": {"artist": "Queen", "title": "Bohemian Rhapsody",
                                                    "karaoke": True, "score": 1.0},
                    "candidates": [{"artist": "Queen", "title": "Bohemian Rhapsody", "karaoke": True}]}
    sing_app.song_identifier = Ident()
    body = _get(client, sam, "/search/identify", "identifySong", t=token, q="bohemian rhapsody queen")
    assert body["status"] == "confident" and body["song"]["artist"] == "Queen"


def test_search_identify_unavailable(client, token, sam, sing_app):
    sing_app.song_identifier = None
    body = _get(client, sam, "/search/identify", "identifySong", t=token, q="queen")
    assert body["status"] == "none" and body["unavailable"] is True


def test_search_resolve_contract(client, token, sam, sing_app, monkeypatch):
    monkeypatch.setattr(sing, "_resolve_query", lambda q: {
        "kind": "content", "confident": True, "canonical_artist": "Queen",
        "canonical_title": "Bohemian Rhapsody", "typed_artist": "Quen", "typed_title": "Bohemian"})
    monkeypatch.setattr(routes, "unified_search", lambda q, app, **kw: {"songs": [GROUP]})
    body = _get(client, sam, "/search/resolve", "resolveSearch", t=token, q="quen bohemian")
    assert body["corrected"] == {"artist": "Queen", "title": "Bohemian Rhapsody"}
    assert body["songs"][0]["title"] == "Bohemian Rhapsody"


def test_search_resolve_alternatives_and_empty(client, token, sam, monkeypatch):
    monkeypatch.setattr(sing, "_resolve_query", lambda q: {
        "kind": "ambiguous", "alternatives": [{"artist": "A", "title": "B"}]})
    body = _get(client, sam, "/search/resolve", "resolveSearch", t=token, q="hallelujah")
    assert body["alternatives"] == [{"artist": "A", "title": "B"}]
    monkeypatch.setattr(sing, "_resolve_query", lambda q: None)
    body = _get(client, sam, "/search/resolve", "resolveSearch", t=token, q="zzzz zzzz")
    assert "corrected" not in body


def test_resolve_rate_limited_per_app_device(client, token, sam, app_token, monkeypatch):
    monkeypatch.setattr(sing, "_resolve_query", lambda q: None)
    monkeypatch.setattr(sing, "_RESOLVE_RATE_PER_DEVICE", 2)
    for _ in range(2):
        _get(client, sam, "/search/resolve", "resolveSearch", t=token, q="some song")
    resp = client.get(f"{V1}/search/resolve", query_string={"t": token, "q": "some song"},
                      headers=bearer(sam))
    body = _expect(resp, "resolveSearch", 429, "rate_limited")
    assert body["retry_after"] > 0 and resp.headers["Retry-After"]
    # another account's device has its own budget
    other = app_token("olive.other@example.com", "Olive")
    _get(client, other, "/search/resolve", "resolveSearch", t=token, q="some song")


def test_search_event_contract(client, token, sam, sing_app):
    logged = []

    class Log:
        def log(self, type_, **kw):
            logged.append((type_, kw))
    sing_app.search_log = Log()
    body = _post(client, sam, "/search/event", "logSearchEvent",
                 {"t": token, "sid": "s1", "action": "keep_typed", "q": "queen",
                  "data": {"shown": "Queen"}})
    assert body["ok"] is True
    assert logged and logged[0][1]["device_id"]


def test_search_event_unknown_action_invalid(client, token, sam):
    _post(client, sam, "/search/event", "logSearchEvent",
          {"t": token, "action": "nope"}, status=400)


# --- singers / now / rotation ---------------------------------------------------

def test_singers_contract(client, token, sam, sing_app):
    sing_app.rotation.add_entry("Sara B", "Queen - Radio")
    body = _get(client, sam, "/singers", "listSingers", t=token)
    assert "Sara B" in body["singers"]


def test_now_contract(client, token, sam, sing_app):
    a = sing_app.rotation.add_entry("Sara B", "Queen - Radio")
    sing_app.rotation.add_entry("Tom Q & Jo R", "ABBA - SOS")
    sing_app.rotation.update_status(a["id"], "Now Singing")
    body = _get(client, sam, "/now", "getNow", t=token)
    assert body["now_singing"]["first_name"] == "Sara"
    assert body["up_next"]["display_name"] == "Tom & Jo"
    assert body["queued_count"] == 2


def test_rotation_contract_and_etag(client, token, sam, sing_app):
    sing_app.rotation.add_entry("Sara B", "Queen - Radio")
    resp = client.get(f"{V1}/rotation", query_string={"t": token}, headers=bearer(sam))
    body = _expect(resp, "getRotation", 200)
    rev = sing_app.rotation.store.get_rev()
    assert resp.headers["ETag"] == f'"{rev}"' and body["rev"] == rev
    assert body["entries"][0]["first_name"] == "Sara"


def test_rotation_etag_304(client, token, sam, sing_app):
    sing_app.rotation.add_entry("Sara B", "Queen - Radio")
    rev = sing_app.rotation.store.get_rev()
    for inm in (f'"{rev}"', str(rev), f'W/"{rev}"'):
        resp = client.get(f"{V1}/rotation", query_string={"t": token},
                          headers={**bearer(sam), "If-None-Match": inm})
        assert resp.status_code == 304 and resp.data == b""
        assert_matches_contract(resp, "getRotation")
        assert resp.headers["ETag"] == f'"{rev}"'
    # a change bumps the rev -> 200 again with the new ETag
    sing_app.rotation.add_entry("Tom Q", "ABBA - SOS")
    resp = client.get(f"{V1}/rotation", query_string={"t": token},
                      headers={**bearer(sam), "If-None-Match": f'"{rev}"'})
    body = _expect(resp, "getRotation", 200)
    assert body["rev"] != rev and resp.headers["ETag"] == f'"{body["rev"]}"'


def test_rotation_304_still_requires_auth(client, token):
    resp = client.get(f"{V1}/rotation", query_string={"t": token},
                      headers={"If-None-Match": '"0"'})
    _expect(resp, "getRotation", 401, "unauthenticated")


def test_rotation_excludes_cancelled_from_now(client, token, sam, sing_app):
    a = sing_app.rotation.add_entry("Sara B", "Queen - Radio")
    b = sing_app.rotation.add_entry("Tom Q", "ABBA - SOS")
    sing_app.rotation.update_status(a["id"], "Cancelled")
    rot = _get(client, sam, "/rotation", "getRotation", t=token)
    assert [e["entry_id"] for e in rot["entries"]] == [b["id"]]
    now = _get(client, sam, "/now", "getNow", t=token)
    assert now["queued_count"] == 1 and now["up_next"]["first_name"] == "Tom"


# --- preview ------------------------------------------------------------------

class FakePreview:
    def __init__(self, result):
        self.result = result
        self.closed = []
        self.descriptors = []

    def resolve(self, descriptor):
        self.descriptors.append(descriptor)
        return self.result

    def close(self, token=None):
        self.closed.append(token)


def _preview(client, tok, t, **desc):
    return client.post(f"{V1}/preview/resolve", json={"t": t, "source": "local", "file_path": "/a.mp4",
                                                       **desc}, headers=bearer(tok))


@pytest.mark.parametrize("result,keys", [
    ({"mode": "native_video", "token": "tok1", "title": "T"}, {"stream_url"}),
    ({"mode": "native_audio", "token": "tok1", "title": "T", "format": "MP3", "ext": ".mp3"}, {"stream_url"}),
    ({"mode": "hls", "token": "tok1", "title": "T"}, {"hls_url"}),
    ({"mode": "cdg", "token": "tok1", "title": "T"}, {"cdg_audio_url", "cdg_graphics_url"}),
])
def test_preview_resolve_contract(client, token, sam, sing_app, result, keys):
    sing_app.preview = FakePreview(result)
    body = _expect(_preview(client, sam, token), "resolvePreview", 200)
    assert body["mode"] == result["mode"] and body["token"] == "tok1"
    assert keys <= set(body)
    assert all(body[k].startswith("http://localhost/sing/preview/") for k in keys)
    assert all(f"t={token}" in body[k] for k in keys)


def test_preview_youtube_and_unavailable(client, token, sam, sing_app):
    sing_app.preview = FakePreview({"mode": "youtube", "youtube_url": "https://www.youtube.com/watch?v=x",
                                    "title": "T"})
    body = _expect(_preview(client, sam, token, source="youtube"), "resolvePreview", 200)
    assert body["youtube_url"].startswith("https://www.youtube.com/")
    sing_app.preview = FakePreview({"mode": "unavailable", "reason": "Unsupported file type", "title": ""})
    body = _expect(_preview(client, sam, token), "resolvePreview", 200)
    assert body["mode"] == "unavailable" and body["reason"]


def test_preview_urls_absolute_on_public_host(client, token, sam, sing_app):
    sing_app.preview = FakePreview({"mode": "hls", "token": "tok1", "title": "T"})
    # admin host: keeps /sing
    admin = _expect(_preview(client, sam, token), "resolvePreview", 200)
    assert admin["hls_url"] == f"http://localhost/sing/preview/hls/tok1/index.m3u8?t={token}"
    # public host: the rewriter adds /sing, so it is dropped
    sing_app.kj_config["sing_public_host"] = "sing.test"
    resp = client.post(f"{V1}/preview/resolve", base_url="http://sing.test",
                       json={"t": token, "source": "local", "file_path": "/a.mp4"}, headers=bearer(sam))
    pub = _expect(resp, "resolvePreview", 200)
    assert pub["hls_url"] == f"http://sing.test/preview/hls/tok1/index.m3u8?t={token}"


def test_preview_urls_are_served_on_both_hosts(client, token, sam, sing_app, tmp_path):
    f = tmp_path / "a.mp3"
    f.write_bytes(b"ID3" + b"\0" * 64)

    class RealishPreview(FakePreview):
        def token_info(self, tok):
            return {"kind": "native_audio", "path": str(f), "mime": "audio/mpeg"}
    sing_app.preview = RealishPreview({"mode": "native_audio", "token": "tok1", "title": "T"})
    sing_app.kj_config["sing_public_host"] = "sing.test"
    admin = _expect(_preview(client, sam, token), "resolvePreview", 200)
    pub = _expect(client.post(f"{V1}/preview/resolve", base_url="http://sing.test",
                              json={"t": token, "source": "local", "file_path": "/a.mp4"},
                              headers=bearer(sam)), "resolvePreview", 200)
    assert client.get(admin["stream_url"]).status_code == 200
    assert client.get(pub["stream_url"], base_url="http://sing.test").status_code == 200


def test_preview_rate_limited_and_unready(client, token, sam, sing_app):
    sing_app.preview = FakePreview({"mode": "native_audio", "token": "t", "title": ""})
    sing_app.kj_config["sing_preview_rate_limit"] = 1
    _expect(_preview(client, sam, token), "resolvePreview", 200)
    body = _expect(_preview(client, sam, token), "resolvePreview", 429, "rate_limited")
    assert body["retry_after"] > 0
    sing_app.kj_config["sing_preview_rate_limit"] = 12
    sing._preview_rate_limit_state.clear()
    _expect(_preview(client, sam, token, source="entry", entry_id=99999), "resolvePreview", 404, "not_found")
    _expect(_preview(client, sam, token, source="nonsense"), "resolvePreview", 400, "invalid")


def test_preview_without_service_is_unavailable(client, token, sam, sing_app):
    sing_app.preview = None
    body = _expect(_preview(client, sam, token), "resolvePreview", 200)
    assert body["mode"] == "unavailable"


def test_preview_close_contract(client, token, sam, sing_app):
    sing_app.preview = FakePreview({})
    body = _post(client, sam, "/preview/close", "closePreview", {"t": token, "token": "tok1"})
    assert body["ok"] is True and sing_app.preview.closed == ["tok1"]


# --- tips -----------------------------------------------------------------------

def test_tips_contract(client, token, sam, sing_app):
    sing_app.kj_config["sing_tip_venmo"] = "@kj"
    body = _get(client, sam, "/tips", "getTips", t=token)
    assert body["enabled"] is True and any(m["key"] == "venmo" for m in body["methods"])


def test_tip_claim_uses_alias_and_hides_edit_token(client, token, sam, sing_app):
    body = _post(client, sam, "/tips/claim", "claimTip",
                 {"t": token, "amount": 5, "method": "venmo", "phone": "5551234567",
                  "singer_name": "Spoof"})
    req = body["request"]
    assert req["singer_name"] == "Sam S" and req["tip_amount"] == 5.0 and "edit_token" not in req
    row = sing_app.sing_store.get_request(req["id"])
    assert row["device_id"] and row["singer_name"] == "Sam S"


def test_tip_claim_needs_name(client, token, app_token):
    tok = app_token("new.person@example.com", "New Person")
    _expect(client.post(f"{V1}/tips/claim", json={"t": token, "amount": 5}, headers=bearer(tok)),
            "claimTip", 400, "needs_name")


def test_tip_claim_bad_amount_and_rate_limit(client, token, sam):
    _post(client, sam, "/tips/claim", "claimTip", {"t": token, "amount": "x"}, status=400)
    for _ in range(4):   # the bad-amount attempt above used 1 of the 5
        _post(client, sam, "/tips/claim", "claimTip", {"t": token, "amount": 1})
    body = _post(client, sam, "/tips/claim", "claimTip", {"t": token, "amount": 1}, status=429)
    assert body["error"] == "rate_limited" and body["retry_after"] > 0


def test_tip_claim_disabled(client, token, sam, sing_app):
    sing_app.sing_store.set_tip_settings({"enabled": False})
    _post(client, sam, "/tips/claim", "claimTip", {"t": token, "amount": 5}, status=400)


# --- my-stats -------------------------------------------------------------------

def test_my_stats_top_songs_only_and_ignores_name(client, token, sam, sing_app):
    calls = []

    class Stats:
        def singer_songs(self, name, limit=50):
            calls.append(name)
            return [{"artist": "Secret", "title": "Song", "plays": 3, "last_sung": "x"}]

        def top_songs(self, limit=10):
            return [{"artist": "Queen", "title": "Radio", "plays": 7}]
    sing_app.stats = Stats()
    body = _get(client, sam, "/my-stats", "getMyStats", t=token, name="Someone Else")
    assert body["top_songs"] == [{"artist": "Queen", "title": "Radio", "plays": 7}]
    assert "my_songs" not in body and calls == []


def test_my_stats_without_stats_service(client, token, sam, sing_app):
    sing_app.stats = None
    assert _get(client, sam, "/my-stats", "getMyStats", t=token)["top_songs"] == []


def test_read_endpoints_need_auth(client, token):
    for path in ("/search", "/search/identify", "/search/resolve", "/singers", "/rotation", "/now",
                 "/tips", "/my-stats"):
        resp = client.get(f"{V1}{path}", query_string={"t": token, "q": "queen"})
        assert resp.status_code == 401, path
