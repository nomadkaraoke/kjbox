import pytest

from nightclips.totp import seconds_left, totp

# RFC 6238 appendix B, SHA1 seed "12345678901234567890" (base32 below).
SEED = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"


@pytest.mark.parametrize("t,code", [
    (59, "94287082"), (1111111109, "07081804"), (1111111111, "14050471"),
    (1234567890, "89005924"), (2000000000, "69279037"),
])
def test_rfc6238_vectors(t, code):
    assert totp(SEED, at=t, digits=8) == code


def test_six_digits_and_secret_formatting():
    assert totp(SEED, at=59) == "287082"
    # Authenticator apps show secrets lower-case, spaced and unpadded.
    assert totp("gezd gnbv gy3t qojq gezd gnbv gy3t qojq", at=59) == "287082"


def test_seconds_left():
    assert seconds_left(at=60) == 30
    assert seconds_left(at=59) == 1


def test_login_credentials_need_all_three(monkeypatch):
    from nightclips import publish
    for k in (publish.ENV_USER, publish.ENV_PASS, publish.ENV_2FA):
        monkeypatch.setenv(k, "x")
    assert publish.login_credentials() == ("x", "x", "x")
    monkeypatch.setenv(publish.ENV_2FA, "  ")
    assert publish.login_credentials() is None
    monkeypatch.delenv(publish.ENV_USER)
    assert publish.login_credentials() is None
