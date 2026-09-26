"""Singer make-it flow: verified gen account → gen's search/correction → job.

The singer verifies their email with a gen-issued 6-digit code (kjbox keeps
the resulting gen session server-side, per device), kjbox proxies gen's own
match-judge + audio search as that singer (topping up tonight's free credit
first), and on submit creates the gen job on the singer's account. Make-its
skip the KJ approval queue and go straight into the rotation as
"Being Made (!)".
"""

from unittest.mock import MagicMock

import pytest

from gen_client import GenApiError

DEVICE = "0123456789abcdef" * 2   # the singer UI's random 32-hex device id


@pytest.fixture
def gen(sing_app):
    client = MagicMock()
    client.singer_flow_configured.return_value = True
    client.verify_login_code.return_value = {"session_token": "sess-mary", "user": {}}
    client.match_judge.return_value = {"kind": "cosmetic", "confident": True,
                                       "canonical_artist": "Radiohead", "canonical_title": "Creep"}
    client.search_audio.return_value = {"search_session_id": "ss-1", "results": [
        {"index": 0, "provider": "RED", "title": "Pablo Honey", "is_lossless": True, "seeders": 80}]}
    client.create_job_from_search.return_value = {"job_id": "job-mary", "status": "pending"}
    client.create_job_from_url.return_value = {"job_id": "job-url", "status": "pending"}
    client.validate_url.return_value = {"supported": True}
    sing_app.gen_client = client
    return client


@pytest.fixture
def signed_in(sing_app, gen):
    sing_app.sing_store.set_gen_account(DEVICE, "mary@example.com", "sess-mary")


def _post(client, token, path, **body):
    return client.post(f"/sing/make/{path}?t={token}", json={"device_id": DEVICE, **body})


def _submit(client, token, **overrides):
    body = {
        "singer_name": "Mary", "device_id": DEVICE,
        "source_type": "make", "song_artist": "Radiohead", "song_title": "Creep",
        "source_meta": {"search_session_id": "ss-1", "selection_index": 0},
    }
    body.update(overrides)
    return client.post(f"/sing/submit?t={token}", json=body)


class TestAccount:
    def test_not_ready_without_partner_secret(self, client, sing_app, token):
        sing_app.gen_client = None
        resp = client.get(f"/sing/make/account?device_id={DEVICE}&t={token}")
        assert resp.get_json() == {"ready": False, "email": None}
        assert _post(client, token, "send-code", email="a@b.co").status_code == 400

    def test_make_flag_needs_flow_ready(self, client, sing_app, token, gen):
        assert client.get(f"/sing/search?q=zzz&t={token}").get_json()["make_requests_enabled"] is True
        gen.singer_flow_configured.return_value = False
        assert client.get(f"/sing/search?q=zzz&t={token}").get_json()["make_requests_enabled"] is False

    def test_send_and_verify_code_stores_session_server_side(self, client, sing_app, token, gen):
        sent = _post(client, token, "send-code", email="Mary@Example.com", locale="es")
        assert sent.status_code == 200
        gen.send_login_code.assert_called_once()
        assert gen.send_login_code.call_args.args[0] == "mary@example.com"

        ok = _post(client, token, "verify-code", email="mary@example.com", code="123 456")
        assert ok.status_code == 200
        assert ok.get_json() == {"email": "mary@example.com"}          # token never sent to the phone
        gen.verify_login_code.assert_called_once_with("mary@example.com", "123456", locale=None)
        acct = sing_app.sing_store.get_gen_account(DEVICE)
        assert (acct["email"], acct["session_token"]) == ("mary@example.com", "sess-mary")
        resp = client.get(f"/sing/make/account?device_id={DEVICE}&t={token}")
        assert resp.get_json()["email"] == "mary@example.com"

    def test_bad_email_and_code(self, client, token, gen):
        assert _post(client, token, "send-code", email="nope").get_json()["error"] == "email_invalid"
        gen.send_login_code.side_effect = GenApiError(422, "disposable")
        assert _post(client, token, "send-code", email="x@tempmail.co").get_json()["error"] == "email_invalid"
        gen.verify_login_code.side_effect = GenApiError(401, "invalid_code")
        bad = _post(client, token, "verify-code", email="mary@example.com", code="000000")
        assert (bad.status_code, bad.get_json()["error"]) == (400, "invalid_code")
        gen.verify_login_code.side_effect = GenApiError(401, "expired")
        assert _post(client, token, "verify-code", email="mary@example.com",
                     code="000000").get_json()["error"] == "expired"
        gen.verify_login_code.side_effect = GenApiError(429, "too_many_attempts")
        assert _post(client, token, "verify-code", email="mary@example.com",
                     code="000000").status_code == 429

    def test_sign_out(self, client, sing_app, token, signed_in):
        _post(client, token, "sign-out")
        assert sing_app.sing_store.get_gen_account(DEVICE) is None


class TestCheckAndSearch:
    def test_requires_sign_in(self, client, token, gen):
        resp = _post(client, token, "search", artist="Radiohead", title="Creep")
        assert (resp.status_code, resp.get_json()["error"]) == (401, "signin_required")

    def test_check_proxies_match_judge_as_singer(self, client, token, gen, signed_in):
        resp = _post(client, token, "check", artist="radiohed", title="creep", stage="full", tier=3)
        assert resp.get_json()["canonical_artist"] == "Radiohead"
        gen.match_judge.assert_called_once_with("sess-mary", "radiohed", "creep",
                                                stage="full", audio_confidence_tier=3)

    def test_check_fails_open(self, client, token, gen, signed_in):
        gen.match_judge.side_effect = GenApiError(502, "boom")
        assert _post(client, token, "check", artist="a", title="b").get_json()["kind"] == "none"

    def test_expired_session_is_forgotten(self, client, sing_app, token, gen, signed_in):
        gen.match_judge.side_effect = GenApiError(401, "expired")
        resp = _post(client, token, "check", artist="a", title="b")
        assert resp.status_code == 401
        assert sing_app.sing_store.get_gen_account(DEVICE) is None

    def test_search_tops_up_only_an_empty_balance(self, client, token, gen, signed_in):
        r1 = _post(client, token, "search", artist="Radiohead", title="Creep")
        assert r1.get_json()["search_session_id"] == "ss-1"
        _post(client, token, "search", artist="radiohead", title="CREEP!")   # same song
        _post(client, token, "search", artist="Radiohead", title="Karma Police")
        calls = gen.grant_show_credit.call_args_list
        keys = [c.args[1] for c in calls]
        assert len(keys) == 3
        assert keys[0] == keys[1] != keys[2]      # idempotency key repeats for the same song
        assert all(len(k) == 64 for k in keys)    # hashed (gen caps keys at 128 chars)
        assert all(c.kwargs["only_if_empty"] is True for c in calls)
        gen.search_audio.assert_called_with("sess-mary", "Radiohead", "Karma Police")

    def test_long_song_names_still_get_a_key_gen_accepts(self, client, token, gen, signed_in):
        _post(client, token, "search", artist="Fall Out Boy",
              title="I Slept With Someone in Fall Out Boy and All I Got Was This Stupid Song Written About Me")
        assert len(gen.grant_show_credit.call_args.args[1]) == 64

    def test_zero_cap_means_unlimited(self, client, sing_app, token, gen, signed_in):
        sing_app.kj_config["sing_make_max_per_device"] = 0
        for title in "ABCDE":
            assert _post(client, token, "search", artist="X", title=title).status_code == 200

    def test_short_device_ids_refused(self, client, token, gen):
        resp = client.post(f"/sing/make/send-code?t={token}", json={"device_id": "d123", "email": "a@b.co"})
        assert (resp.status_code, resp.get_json()["error"]) == (400, "device_unsupported")

    def test_send_code_has_its_own_tight_budget(self, client, token, gen):
        codes = [_post(client, token, "send-code", email="a@b.co").status_code for _ in range(6)]
        assert codes[:5] == [200] * 5 and codes[5] == 429

    def test_search_song_cap(self, client, sing_app, token, gen, signed_in):
        sing_app.kj_config["sing_make_max_per_device"] = 1   # → 2 distinct songs
        for title in ("A", "B"):
            assert _post(client, token, "search", artist="X", title=title).status_code == 200
        resp = _post(client, token, "search", artist="X", title="C")
        assert (resp.status_code, resp.get_json()["error"]) == (429, "make_limit")

    def test_search_without_credits(self, client, token, gen, signed_in):
        gen.grant_show_credit.side_effect = GenApiError(429, "show_credit_cap")
        gen.search_audio.side_effect = GenApiError(402, "no credits")
        resp = _post(client, token, "search", artist="Radiohead", title="Creep")
        assert (resp.status_code, resp.get_json()["error"]) == (402, "no_credits")


class TestSubmit:
    def test_submit_creates_job_and_goes_straight_in(self, client, sing_app, token, gen, signed_in):
        assert not sing_app.sing_store.is_auto_approve()
        resp = _submit(client, token)
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["auto_approved"] is True                 # no KJ approval step for make-its
        gen.create_job_from_search.assert_called_once_with("sess-mary", "ss-1", 0, "Radiohead", "Creep")
        gen.grant_show_credit.assert_not_called()            # no search this session → no key
        entry = sing_app.rotation.store.get_entry(body["request"]["linked_entry_id"])
        assert entry["status"] == "Being Made (!)"
        assert entry["gen_job_id"] == "job-mary"
        assert sing_app.sing_store.get_request(body["request"]["id"])["gen_job_id"] == "job-mary"
        gen.create_job.assert_not_called()                   # never the admin-owned path

    def test_submit_tops_up_with_the_search_key(self, client, sing_app, token, gen, signed_in):
        _post(client, token, "search", artist="Radiohead", title="Creep")
        search_key = gen.grant_show_credit.call_args.args[1]
        assert _submit(client, token).status_code == 200
        last = gen.grant_show_credit.call_args
        # Same key → gen grants at most one credit for this song across both calls.
        assert last.args[1] == search_key and last.kwargs.get("only_if_empty", False) is False

    def test_gen_403_on_create_is_a_stale_search(self, client, sing_app, token, gen, signed_in):
        gen.create_job_from_search.side_effect = GenApiError(403, "not your session")
        resp = _submit(client, token)
        assert (resp.status_code, resp.get_json()["error"]) == (409, "search_expired")

    def test_youtube_link_fallback(self, client, sing_app, token, gen, signed_in):
        resp = _submit(client, token, source_meta={"youtube_url": "https://youtu.be/abc"})
        assert resp.status_code == 200
        gen.create_job_from_url.assert_called_once_with("sess-mary", "https://youtu.be/abc",
                                                        "Radiohead", "Creep")

    def test_requires_verified_account(self, client, sing_app, token, gen):
        resp = _submit(client, token)
        assert (resp.status_code, resp.get_json()["error"]) == (401, "signin_required")
        assert sing_app.sing_store.list_requests() == []

    def test_requires_chosen_audio(self, client, sing_app, token, gen, signed_in):
        resp = _submit(client, token, source_meta=None)
        assert resp.status_code == 400
        assert sing_app.sing_store.list_requests() == []

    def test_expired_search_leaves_nothing_behind(self, client, sing_app, token, gen, signed_in):
        gen.create_job_from_search.side_effect = GenApiError(404, "Search expired")
        resp = _submit(client, token)
        assert (resp.status_code, resp.get_json()["error"]) == (409, "search_expired")
        assert sing_app.sing_store.list_requests() == []

    def test_per_device_night_cap(self, client, sing_app, token, gen, signed_in):
        sing_app.kj_config["sing_make_max_per_device"] = 1
        sing_app.kj_config["sing_rate_limit_per_device"] = 100
        assert _submit(client, token).status_code == 200
        resp = _submit(client, token, song_title="Karma Police")
        assert (resp.status_code, resp.get_json()["error"]) == (429, "make_limit")
        assert gen.create_job_from_search.call_count == 1

    def test_cannot_change_a_song_into_a_make(self, client, sing_app, token, gen, signed_in):
        req = client.post(f"/sing/submit?t={token}", json={
            "singer_name": "Mary", "device_id": DEVICE, "source_type": "youtube",
            "source_ref": "https://youtu.be/x", "song_artist": "R", "song_title": "C",
        }).get_json()["request"]
        resp = client.post(f"/sing/requests/{req['id']}/change?t={token}", json={
            "edit_token": req["edit_token"], "source_type": "make",
            "song_artist": "Radiohead", "song_title": "Creep"})
        assert (resp.status_code, resp.get_json()["error"]) == (400, "make_not_changeable")


class TestMyRequestsMakePhase:
    def _phase(self, client, token, rid):
        resp = client.get(f"/sing/my-requests?ids={rid}&t={token}")
        return resp.get_json()["requests"][0]

    def test_phases(self, client, sing_app, token, gen, signed_in):
        body = _submit(client, token).get_json()
        rid, entry_id = body["request"]["id"], body["request"]["linked_entry_id"]
        item = self._phase(client, token, rid)
        assert item["make"] == "making"
        assert "estimate" not in item                      # not singable yet → no queue slot
        sing_app.rotation.set_gen_status(entry_id, "job-mary", "awaiting_review")
        assert self._phase(client, token, rid)["make"] == "needs_host"
        sing_app.rotation.complete_gen_job("job-mary", "/m/NOMAD-1 - Radiohead - Creep.mp4")
        assert self._phase(client, token, rid).get("make") is None
        assert sing_app.rotation.store.get_entry(entry_id)["status"] == "Waiting"

    def test_non_make_request_has_no_phase(self, client, token):
        rid = client.post(f"/sing/submit?t={token}", json={
            "singer_name": "Mary", "device_id": DEVICE, "source_type": "youtube",
            "source_ref": "https://youtu.be/x", "song_artist": "R", "song_title": "C",
        }).get_json()["request"]["id"]
        assert "make" not in self._phase(client, token, rid)
