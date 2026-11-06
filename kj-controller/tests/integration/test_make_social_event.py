"""POST /sing/make/social-event — Google/Apple sign-in steps reach the box's log
(live 2026-11-05 a singer's taps did nothing and left no trace)."""

import logging


def _post(client, token, **body):
    return client.post(f"/sing/make/social-event?t={token}", json=body)


def test_valid_event_is_logged(client, sing_app, token, caplog):
    with caplog.at_level(logging.INFO):
        resp = _post(client, token, device_id="abcdef123456", provider="apple",
                     event="stall", detail="visible", purpose="make")
    assert resp.status_code == 204
    assert any("make social: apple stall device=abcdef12" in r.getMessage() for r in caplog.records)
    assert not any(r.levelno >= logging.ERROR for r in caplog.records)


def test_unknown_provider_or_event_is_ignored(client, sing_app, token, caplog):
    with caplog.at_level(logging.INFO):
        assert _post(client, token, device_id="d", provider="facebook", event="tap").status_code == 204
        assert _post(client, token, device_id="d", provider="google", event="hack").status_code == 204
    assert not any("make social:" in r.getMessage() for r in caplog.records)


def test_needs_open_event(client, sing_app, token):
    assert client.post("/sing/make/social-event?t=nope", json={"provider": "google", "event": "tap"}).status_code == 403
