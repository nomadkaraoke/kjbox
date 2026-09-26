"""End-to-end: KJ toggles make-requests off, singer submits get rejected.

Walks through:
  1. Flag on (default) → `/sing/search` response carries make_requests_enabled=true.
  2. KJ flips off via admin config endpoint.
  3. `/sing/search` now carries make_requests_enabled=false.
  4. Singer (stale client) submits source_type=make → 400 make_requests_disabled.
  5. KJ flips back on → submit succeeds (a verified singer's make-it goes
     straight into the rotation as "Being Made (!)").

Make-it is only offered when gen's singer flow is configured (partner
secret) — the fixture stubs a configured gen client.
"""

from unittest.mock import MagicMock

import pytest


@pytest.fixture(autouse=True)
def _gen_flow_ready(sing_app):
    gen = MagicMock()
    gen.singer_flow_configured.return_value = True
    gen.create_job_from_search.return_value = {"job_id": "job-e2e", "status": "pending"}
    sing_app.gen_client = gen
    return gen


def _make_body(**overrides):
    body = {
        "singer_name": "Punk Tester",
        "phone": "+1 555 7777",
        "song_artist": "Niche Local Band",
        "song_title": "Obscure Track",
        "source_type": "make",
        "source_ref": None,
        "device_id": "dev-punk",
        "source_meta": {"search_session_id": "sess-1", "selection_index": 0},
    }
    body.update(overrides)
    return body


class TestMakeRequestDisableE2E:
    def test_full_round_trip(self, client, sing_app, token, monkeypatch):
        # Stub the KN fetch so search completes without network I/O.
        import karaoke_nerds
        monkeypatch.setattr(karaoke_nerds, "search", lambda *a, **kw: [])
        admin = sing_app.test_client()

        # 1. Default: make-requests are on.
        s1 = client.get(f"/sing/search?q=obscure&t={token}")
        assert s1.get_json()["make_requests_enabled"] is True

        # 2. KJ toggles off.
        cfg = admin.post(
            "/rotation/requests/config",
            json={"accept_make_requests": False},
        )
        assert cfg.status_code == 200
        assert cfg.get_json()["changed"]["accept_make_requests"] is False

        # 3. Next search reflects the flip.
        s2 = client.get(f"/sing/search?q=obscure&t={token}")
        assert s2.get_json()["make_requests_enabled"] is False

        # 4. Stale-client make submit is rejected.
        sub1 = client.post(f"/sing/submit?t={token}", json=_make_body())
        assert sub1.status_code == 400
        assert "make_requests_disabled" in sub1.get_json()["error"]
        assert sing_app.sing_store.count_pending() == 0

        # 5. KJ flips back on; submit works again.
        admin.post(
            "/rotation/requests/config",
            json={"accept_make_requests": True},
        )
        sing_app.sing_store.set_gen_account("dev-punk", "punk@example.com", "sess-token")
        sub2 = client.post(f"/sing/submit?t={token}", json=_make_body())
        assert sub2.status_code == 200
        assert sub2.get_json()["auto_approved"] is True
        assert sing_app.sing_store.count_pending() == 0

    def test_landing_dataset_carries_flag(self, client, sing_app, token):
        """The landing template forwards the flag so singer JS has it before
        the first search."""
        sing_app.sing_store.set_accepting_make_requests(False)
        resp = client.get(f"/sing/?t={token}")
        assert resp.status_code == 200
        assert b'data-make-requests-enabled="0"' in resp.data
        sing_app.sing_store.set_accepting_make_requests(True)
        resp2 = client.get(f"/sing/?t={token}")
        assert b'data-make-requests-enabled="1"' in resp2.data
