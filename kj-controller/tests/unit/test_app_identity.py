import logging

import pytest

from app_identity import (
    AppAuthError, AppUser, GenTokenVerifier, app_device_id, suggested_name,
)
from gen_client import GenApiError

TOKEN = "tok-SECRET-123"


class FakeClock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


class FakeGen:
    def __init__(self, response=None, exc=None):
        self.response = response if response is not None else {
            "user": {"email": "Jane.Doe@X.com", "display_name": "Jane Doe", "tenant_id": None},
            "has_session": True,
        }
        self.exc = exc
        self.calls = []

    def get_me(self, session_token):
        self.calls.append(session_token)
        if self.exc:
            raise self.exc
        return self.response


def make(gen=None):
    clock = FakeClock()
    gen = gen or FakeGen()
    return GenTokenVerifier(gen, clock=clock), gen, clock


def test_device_id_is_32_hex_and_case_insensitive():
    a = app_device_id("Jane@X.com ")
    assert len(a) == 32 and int(a, 16) >= 0
    assert a == app_device_id("jane@x.com")


def test_verify_returns_user():
    v, _, _ = make()
    u = v.verify(TOKEN)
    assert u == AppUser(email="jane.doe@x.com", display_name="Jane Doe",
                        device_id=app_device_id("jane.doe@x.com"))


def test_verify_caches_positive_60s():
    v, gen, clock = make()
    v.verify(TOKEN)
    clock.t += 59
    v.verify(TOKEN)
    assert len(gen.calls) == 1
    clock.t += 2
    v.verify(TOKEN)
    assert len(gen.calls) == 2


def test_verify_caches_negative_10s():
    v, gen, clock = make(FakeGen(exc=GenApiError(401, "bad")))
    for _ in range(2):
        with pytest.raises(AppAuthError) as e:
            v.verify(TOKEN)
        assert e.value.code == "unauthenticated"
    assert len(gen.calls) == 1
    clock.t += 11
    with pytest.raises(AppAuthError):
        v.verify(TOKEN)
    assert len(gen.calls) == 2


def test_403_is_unauthenticated():
    v, _, _ = make(FakeGen(exc=GenApiError(403, "no")))
    with pytest.raises(AppAuthError) as e:
        v.verify(TOKEN)
    assert e.value.code == "unauthenticated"


@pytest.mark.parametrize("status", [0, 500, 502, 503])
def test_gen_down_raises_gen_unavailable_not_cached(status):
    v, gen, _ = make(FakeGen(exc=GenApiError(status, "down")))
    for _ in range(2):
        with pytest.raises(AppAuthError) as e:
            v.verify(TOKEN)
        assert e.value.code == "gen_unavailable"
    assert len(gen.calls) == 2


def test_tenant_user_rejected():
    v, _, _ = make(FakeGen(response={
        "user": {"email": "a@b.com", "tenant_id": "acme"}, "has_session": True}))
    with pytest.raises(AppAuthError) as e:
        v.verify(TOKEN)
    assert e.value.code == "unauthenticated"


def test_empty_tenant_id_ok():
    v, _, _ = make(FakeGen(response={
        "user": {"email": "a@b.com", "tenant_id": ""}, "has_session": True}))
    assert v.verify(TOKEN).email == "a@b.com"


def test_no_session_rejected():
    v, _, _ = make(FakeGen(response={
        "user": {"email": "admin@b.com", "tenant_id": None}, "has_session": False}))
    with pytest.raises(AppAuthError) as e:
        v.verify(TOKEN)
    assert e.value.code == "unauthenticated"


def test_malformed_response_rejected():
    v, _, _ = make(FakeGen(response={"user": {}, "has_session": True}))
    with pytest.raises(AppAuthError) as e:
        v.verify(TOKEN)
    assert e.value.code == "unauthenticated"


def test_cache_bounded_lru():
    v, gen, _ = make()
    for i in range(1001):
        v.verify(f"t{i}")
    assert len(v._cache) == 1000
    n = len(gen.calls)
    v.verify("t0")  # evicted
    assert len(gen.calls) == n + 1


def test_token_never_in_repr_or_logs(caplog):
    caplog.set_level(logging.DEBUG)
    for gen in (FakeGen(), FakeGen(exc=GenApiError(401, "x")), FakeGen(exc=GenApiError(500, "x"))):
        v, _, _ = make(gen)
        try:
            v.verify(TOKEN)
        except AppAuthError as e:
            assert TOKEN not in repr(e) and TOKEN not in str(e)
        assert TOKEN not in repr(v)
        assert all(TOKEN not in str(k) for k in v._cache)
        assert all(TOKEN not in repr(val) for val in v._cache.values())
    assert TOKEN not in caplog.text


def test_suggested_name_cases():
    assert suggested_name(AppUser("a@x.com", "Jane Doe", "d")) == "Jane"
    assert suggested_name(AppUser("jane.doe@x.com", None, "d")) == "Jane Doe"
    assert suggested_name(AppUser("jane_doe@x.com", "  ", "d")) == "Jane Doe"
    assert len(suggested_name(AppUser("a" * 80 + "@x.com", None, "d"))) == 40


def test_get_me_sends_bearer_and_client_id(monkeypatch):
    import gen_client
    seen = {}

    class R:
        status_code = 200
        reason = "OK"

        def json(self):
            return {"user": {}, "has_session": True}

    def fake(method, url, json=None, timeout=None, headers=None):
        seen.update(method=method, url=url, headers=headers)
        return R()

    monkeypatch.setattr(gen_client.requests, "request", fake)
    c = gen_client.GenClient.__new__(gen_client.GenClient)
    c.api_url, c.kjbox_secret = "http://gen", "s"
    c.get_me("TOK")
    assert seen["method"] == "GET" and seen["url"] == "http://gen/api/users/me"
    assert seen["headers"]["Authorization"] == "Bearer TOK"
    assert "X-Client-Id" in seen["headers"] and "X-Kjbox-Secret" not in seen["headers"]
