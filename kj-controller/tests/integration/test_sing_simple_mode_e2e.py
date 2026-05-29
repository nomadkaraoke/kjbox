"""End-to-end: KJ flips Simple Mode on, singer submits get narrowed.

Walks through:
  1. Default (off) → all source types accepted.
  2. KJ flips Simple Mode on.
  3. Singer (stale client) submits source_type=youtube → 400.
  4. Singer submits source_type=make → 400 (simple_mode wins over the
     existing make_requests_disabled message).
  5. Singer submits source_type=kj_pick → 400.
  6. Singer submits source_type=local → 200.
  7. KJ flips back off; youtube submit succeeds again.
"""

import sing


def _body(**overrides):
    body = {
        "singer_name": "Test Singer",
        "phone": "+1 555 0100",
        "song_artist": "Test Artist",
        "song_title": "Test Title",
        "source_type": "local",
        "source_ref": "/library/test.mp4",
    }
    body.update(overrides)
    return body


class TestSimpleModeE2E:
    def test_simple_mode_narrows_sources(
        self, client, sing_app, token, monkeypatch,
    ):
        # Stub KN so /sing/search doesn't reach the network.
        import karaoke_nerds
        monkeypatch.setattr(karaoke_nerds, "search", lambda *a, **kw: [])
        admin = sing_app.test_client()

        # 1. Default: youtube submit works.
        sub0 = client.post(
            f"/sing/submit?t={token}",
            json=_body(source_type="youtube", source_ref="https://youtu.be/x"),
        )
        assert sub0.status_code == 200, sub0.get_json()

        # 2. KJ enables Simple Mode.
        cfg = admin.post(
            "/rotation/requests/config", json={"simple_mode": True},
        )
        assert cfg.status_code == 200
        assert cfg.get_json()["changed"]["simple_mode"] is True

        # 3. youtube → 400.
        sub_yt = client.post(
            f"/sing/submit?t={token}",
            json=_body(source_type="youtube", source_ref="https://youtu.be/y"),
        )
        assert sub_yt.status_code == 400
        assert sub_yt.get_json()["error"] == "simple_mode_disabled_source"

        # 4. make → 400.
        sub_mk = client.post(
            f"/sing/submit?t={token}",
            json=_body(source_type="make", source_ref=None),
        )
        assert sub_mk.status_code == 400
        assert sub_mk.get_json()["error"] == "simple_mode_disabled_source"

        # 5. kj_pick → 400.
        sub_kp = client.post(
            f"/sing/submit?t={token}",
            json=_body(
                source_type="kj_pick",
                source_ref=None,
                source_meta={
                    "versions": [
                        {"brand": "CC", "youtube_url": "https://youtu.be/a"},
                    ],
                },
            ),
        )
        assert sub_kp.status_code == 400
        assert sub_kp.get_json()["error"] == "simple_mode_disabled_source"

        # Clear rate-limit state so steps 1–5 (which all consume a slot,
        # even 400s) don't exhaust the per-IP budget before steps 6–7.
        sing._rate_limit_state.clear()

        # 6. local → 200.
        sub_lc = client.post(
            f"/sing/submit?t={token}",
            json=_body(source_type="local", source_ref="/library/t.mp4"),
        )
        assert sub_lc.status_code == 200

        # 7. Flip off; youtube works again.
        admin.post("/rotation/requests/config", json={"simple_mode": False})
        sub_yt2 = client.post(
            f"/sing/submit?t={token}",
            json=_body(source_type="youtube", source_ref="https://youtu.be/z"),
        )
        assert sub_yt2.status_code == 200

    def test_simple_mode_allows_divebar_and_kn(
        self, client, sing_app, token,
    ):
        sing_app.sing_store.set_simple_mode(True)
        # divebar
        sub_db = client.post(
            f"/sing/submit?t={token}",
            json=_body(
                source_type="divebar",
                source_ref="https://storage.googleapis.com/divebar/x.mp4",
            ),
        )
        assert sub_db.status_code == 200, sub_db.get_json()
        # kn
        sub_kn = client.post(
            f"/sing/submit?t={token}",
            json=_body(
                source_type="kn",
                source_ref="https://youtu.be/kn-track",
            ),
        )
        assert sub_kn.status_code == 200, sub_kn.get_json()
