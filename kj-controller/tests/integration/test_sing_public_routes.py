"""Integration tests for the public /sing/* blueprint."""

from unittest.mock import MagicMock

import pytest

import sing


class TestLanding:
    def test_landing_no_token_shows_code_entry(self, client):
        """Requests are open but visitor has no token — invite them to type it in."""
        resp = client.get("/sing/")
        assert resp.status_code == 200
        assert b"sing-enter-code" in resp.data
        # Must NOT leak any valid-token form fields.
        assert b"sing-root" not in resp.data

    def test_landing_invalid_token_shows_code_entry_with_error(self, client):
        resp = client.get("/sing/?t=nope")
        # 400 — the client supplied a token and it didn't match. Still render
        # the code-entry form so the singer can correct the typo.
        assert resp.status_code == 400
        assert b"sing-enter-code" in resp.data
        assert b'data-bad-code="1"' in resp.data

    def test_landing_valid_token(self, client, token):
        resp = client.get(f"/sing/?t={token}")
        assert resp.status_code == 200
        assert b"sing-root" in resp.data

    def test_landing_when_disabled(self, client, sing_app, token):
        """With requests disabled, both token and no-token paths show the closed page."""
        sing_app.sing_store.set_enabled(False)
        resp = client.get(f"/sing/?t={token}")
        assert resp.status_code == 403
        assert b"sing-closed" in resp.data
        resp_no_tok = client.get("/sing/")
        assert resp_no_tok.status_code == 403
        assert b"sing-closed" in resp_no_tok.data


class TestValidateCode:
    def test_validate_accepts_current_token(self, client, token):
        resp = client.post("/sing/validate", json={"t": token})
        assert resp.status_code == 200
        assert resp.get_json() == {"ok": True}

    def test_validate_rejects_bad_token(self, client):
        resp = client.post("/sing/validate", json={"t": "0000"})
        assert resp.status_code == 400
        assert resp.get_json()["ok"] is False

    def test_validate_rejects_empty(self, client):
        resp = client.post("/sing/validate", json={})
        assert resp.status_code == 400

    def test_validate_rate_limited(self, client, monkeypatch):
        """Per-IP limit keeps the 10 000-combo space un-brute-forceable in one sitting."""
        # Reset the validate bucket so a previous test's attempts don't leak in.
        sing._validate_rate_limit_state.clear()
        for _ in range(10):
            client.post("/sing/validate", json={"t": "0000"})
        # 11th attempt from the same IP gets blocked regardless of correctness.
        resp = client.post("/sing/validate", json={"t": "0000"})
        assert resp.status_code == 429


class TestSearch:
    def test_missing_token(self, client):
        resp = client.get("/sing/search?q=hello")
        assert resp.status_code == 403

    def test_short_query(self, client, token):
        resp = client.get(f"/sing/search?q=ab&t={token}")
        assert resp.status_code == 400

    def test_search_returns_grouped_shape(self, client, token, monkeypatch):
        """Phase A contract: /sing/search returns ``{songs: [...]}`` — not the
        old flat ``{local, karaoke_nerds}`` shape. One entry per unique song,
        each with a ``versions[]`` snapshot."""
        import karaoke_nerds
        monkeypatch.setattr(karaoke_nerds, "search", lambda *a, **kw: [])
        resp = client.get(f"/sing/search?q=hello&t={token}")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "songs" in data
        assert isinstance(data["songs"], list)
        # Old keys must be gone — clients updated in the same PR and nothing
        # else should depend on them.
        assert "local" not in data
        assert "karaoke_nerds" not in data

    def test_search_groups_same_song(self, client, token, monkeypatch):
        """Two KN tracks for the same artist+title collapse to one group with
        two versions."""
        import karaoke_nerds
        monkeypatch.setattr(karaoke_nerds, "search", lambda *a, **kw: [{
            "artist": "Queen",
            "title": "Bohemian Rhapsody",
            "tracks": [
                {"brand_code": "KV", "brand_name": "Karaoke Version",
                 "youtube_url": "https://youtu.be/a", "is_community": False},
                {"brand_code": "SK", "brand_name": "Sound Choice",
                 "youtube_url": "https://youtu.be/b", "is_community": False},
            ],
        }])
        resp = client.get(f"/sing/search?q=bohemian&t={token}")
        data = resp.get_json()
        assert len(data["songs"]) == 1
        g = data["songs"][0]
        assert g["artist"] == "Queen"
        assert g["title"] == "Bohemian Rhapsody"
        assert g["version_count"] == 2
        assert g["in_library"] is False  # YouTube only, no local/divebar
        assert len(g["versions"]) == 2
        assert all(v["source"] == "kn" for v in g["versions"])

    def test_search_defensive_filter_drops_unapprovable_kn_tracks(
        self, client, token, monkeypatch,
    ):
        """A KN track with no YouTube URL AND no divebar mirror is unapprovable
        — it must be filtered out before grouping so the singer never sees it.
        (Design: Phase B §4)"""
        import karaoke_nerds
        monkeypatch.setattr(karaoke_nerds, "search", lambda *a, **kw: [{
            "artist": "Queen",
            "title": "Bohemian Rhapsody",
            "tracks": [
                # Good track with YouTube URL.
                {"brand_code": "KV", "brand_name": "Karaoke Version",
                 "youtube_url": "https://youtu.be/a", "is_community": False},
                # Bad: blank youtube_url, no divebar.
                {"brand_code": "ORPHAN", "brand_name": "Orphan",
                 "youtube_url": "", "is_community": False},
            ],
        }])
        resp = client.get(f"/sing/search?q=bohemian&t={token}")
        data = resp.get_json()
        assert len(data["songs"]) == 1
        # Orphan is stripped; only the KV track appears.
        assert data["songs"][0]["version_count"] == 1
        assert data["songs"][0]["versions"][0]["kn"]["brand_code"] == "KV"

    def test_search_exposes_make_requests_flag(self, client, token, monkeypatch, sing_app):
        """Phase C — singer UI uses this to show/hide the empty-state 'ask the
        KJ to make' card without a second round-trip."""
        import karaoke_nerds
        monkeypatch.setattr(karaoke_nerds, "search", lambda *a, **kw: [])
        resp = client.get(f"/sing/search?q=hello&t={token}")
        data = resp.get_json()
        assert data["make_requests_enabled"] is True  # default on

        sing_app.sing_store.set_accepting_make_requests(False)
        resp2 = client.get(f"/sing/search?q=hello&t={token}")
        assert resp2.get_json()["make_requests_enabled"] is False

    def test_search_empty_result(self, client, token, monkeypatch):
        """Empty query produces an empty `songs` list — Phase C's empty-state
        triage will consume this shape."""
        import karaoke_nerds
        monkeypatch.setattr(karaoke_nerds, "search", lambda *a, **kw: [])
        resp = client.get(f"/sing/search?q=nothingmatchesthisquery&t={token}")
        data = resp.get_json()
        assert data["songs"] == []


class TestSubmit:
    def _body(self, **overrides):
        body = {
            "singer_name": "Andrew",
            "phone": "+61 400 123 456",
            "song_artist": "Queen",
            "song_title": "Bohemian Rhapsody",
            "source_type": "youtube",
            "source_ref": "https://youtu.be/abc",
        }
        body.update(overrides)
        return body

    def test_missing_token(self, client):
        resp = client.post("/sing/submit", json=self._body())
        assert resp.status_code == 403

    def test_happy_path_creates_pending_request(self, client, sing_app, token):
        resp = client.post(f"/sing/submit?t={token}", json=self._body())
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["auto_approved"] is False
        req = data["request"]
        assert req["singer_name"] == "Andrew"
        assert req["status"] == "pending"
        # phone is NOT leaked in the public view
        assert "phone" not in req
        # Stored
        assert sing_app.sing_store.count_pending() == 1

    def test_missing_singer_name(self, client, token):
        resp = client.post(f"/sing/submit?t={token}", json=self._body(singer_name=""))
        assert resp.status_code == 400

    def test_phone_optional(self, client, sing_app, token):
        # Phone is optional — KJs use it to text singers when they're up,
        # but a singer can still submit without one (KJ just calls the name).
        resp = client.post(f"/sing/submit?t={token}", json=self._body(phone=""))
        assert resp.status_code == 200
        # The empty phone is stored as-is so /push/subscribe (which still
        # requires phone) won't accidentally succeed for this singer.
        req_id = resp.get_json()["request"]["id"]
        stored = sing_app.sing_store.get_request(req_id)
        assert stored["phone"] == ""

    def test_invalid_phone_format(self, client, token):
        # When supplied, the phone must still parse — empty stays valid
        # (covered above) but garbage is rejected so the KJ doesn't waste
        # time dialling it.
        resp = client.post(f"/sing/submit?t={token}", json=self._body(phone="abc"))
        assert resp.status_code == 400

    def test_invalid_source_type(self, client, token):
        resp = client.post(f"/sing/submit?t={token}", json=self._body(source_type="bogus"))
        assert resp.status_code == 400

    def test_make_requires_artist_and_title(self, client, token):
        body = self._body(source_type="make", song_artist="", song_title="", source_ref=None)
        resp = client.post(f"/sing/submit?t={token}", json=body)
        assert resp.status_code == 400

    def test_make_rejected_when_flag_off(self, client, sing_app, token):
        """Phase C — the KJ can turn make-requests off; submits must 400."""
        sing_app.sing_store.set_accepting_make_requests(False)
        body = self._body(
            source_type="make",
            song_artist="Radiohead",
            song_title="Creep",
            source_ref=None,
        )
        resp = client.post(f"/sing/submit?t={token}", json=body)
        assert resp.status_code == 400
        assert "make_requests_disabled" in resp.get_json()["error"]
        assert sing_app.sing_store.count_pending() == 0

    def test_make_accepted_when_flag_on(self, client, sing_app, token):
        """Regression guard — make submissions still work when flag is on (default)."""
        body = self._body(
            source_type="make",
            song_artist="Radiohead",
            song_title="Creep",
            source_ref=None,
        )
        resp = client.post(f"/sing/submit?t={token}", json=body)
        assert resp.status_code == 200

    def test_rate_limit_blocks_after_limit(self, client, sing_app, token):
        sing_app.kj_config["sing_rate_limit_per_ip"] = 2
        sing_app.kj_config["sing_rate_limit_window_s"] = 60
        # 1st and 2nd accepted
        for _ in range(2):
            r = client.post(f"/sing/submit?t={token}", json=self._body())
            assert r.status_code == 200
        # 3rd blocked
        r = client.post(f"/sing/submit?t={token}", json=self._body())
        assert r.status_code == 429

    def test_untrusted_peer_cannot_spoof_forwarded_ip(self, sing_app, token):
        """A non-loopback peer must NOT be able to bypass rate limits via CF-Connecting-IP."""
        sing_app.kj_config["sing_rate_limit_per_ip"] = 2
        sing_app.kj_config["sing_rate_limit_window_s"] = 60
        # Simulate an internet origin (not loopback) sending different spoofed
        # CF-Connecting-IP values on each request. The rate limiter must fall
        # back to the real REMOTE_ADDR and still block after the limit.
        client = sing_app.test_client()
        client.environ_base["REMOTE_ADDR"] = "1.2.3.4"
        for _ in range(2):
            r = client.post(
                f"/sing/submit?t={token}",
                json=self._body(),
                headers={"CF-Connecting-IP": "5.6.7.8"},
            )
            assert r.status_code == 200
        r = client.post(
            f"/sing/submit?t={token}",
            json=self._body(),
            headers={"CF-Connecting-IP": "9.9.9.9"},  # try to escape with new spoof
        )
        assert r.status_code == 429

    def test_trusted_loopback_peer_honours_forwarded_ip(self, sing_app, token):
        """cloudflared on localhost gets to pass the real singer IP through."""
        sing_app.kj_config["sing_rate_limit_per_ip"] = 2
        sing_app.kj_config["sing_rate_limit_window_s"] = 60
        client = sing_app.test_client()  # default REMOTE_ADDR=127.0.0.1
        # Two different forwarded IPs from a trusted loopback peer → each
        # gets its own rate-limit bucket.
        for forwarded in ("11.11.11.11", "22.22.22.22"):
            for _ in range(2):
                r = client.post(
                    f"/sing/submit?t={token}",
                    json=self._body(),
                    headers={"CF-Connecting-IP": forwarded},
                )
                assert r.status_code == 200
        # Third request from IP 11.11.11.11 is blocked; IP 22.22.22.22 still ok
        # is ambiguous here because they share the loopback peer — what matters
        # is that the forwarded IP, not the peer, keys the bucket.
        r = client.post(
            f"/sing/submit?t={token}",
            json=self._body(),
            headers={"CF-Connecting-IP": "11.11.11.11"},
        )
        assert r.status_code == 429

    def test_auto_approve_creates_rotation_entry(self, client, sing_app, token):
        sing_app.sing_store.set_auto_approve(True)
        body = self._body(source_type="local", source_ref="/tmp/song.mp4")
        resp = client.post(f"/sing/submit?t={token}", json=body)
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["auto_approved"] is True
        assert data["request"]["status"] == "approved"
        # Rotation entry created
        entries = sing_app.rotation.get_rotation()
        assert any(e["singer"] == "Andrew" for e in entries)

    def test_submit_with_partners(self, client, sing_app, token):
        body = self._body()
        body["additional_singers"] = [
            {"name": "Sarah B.", "phone": "+61 400 111 222"},
            {"name": "Mike", "phone": ""},
        ]
        resp = client.post(f"/sing/submit?t={token}", json=body)
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["request"]["additional_singers"] == [
            {"name": "Sarah B.", "phone": "+61 400 111 222"},
            {"name": "Mike", "phone": ""},
        ]

    def test_submit_partners_omitted_is_solo(self, client, sing_app, token):
        resp = client.post(f"/sing/submit?t={token}", json=self._body())
        assert resp.status_code == 200
        assert resp.get_json()["request"]["additional_singers"] is None

    def test_submit_rejects_too_many_partners(self, client, token):
        body = self._body()
        body["additional_singers"] = [
            {"name": f"Singer {i}"} for i in range(4)
        ]
        resp = client.post(f"/sing/submit?t={token}", json=body)
        assert resp.status_code == 400
        assert "additional_singers" in resp.get_json()["error"]

    def test_submit_rejects_empty_partner_name(self, client, token):
        body = self._body()
        body["additional_singers"] = [{"name": "  ", "phone": ""}]
        resp = client.post(f"/sing/submit?t={token}", json=body)
        assert resp.status_code == 400
        assert "additional_singers" in resp.get_json()["error"]

    def test_submit_rejects_malformed_partner_phone(self, client, token):
        body = self._body()
        body["additional_singers"] = [
            {"name": "Sarah", "phone": "not-a-phone"}
        ]
        resp = client.post(f"/sing/submit?t={token}", json=body)
        assert resp.status_code == 400
        assert "additional_singers" in resp.get_json()["error"]

    def test_submit_accepts_partner_without_phone(self, client, sing_app, token):
        body = self._body()
        body["additional_singers"] = [{"name": "Phoneless Pete"}]
        resp = client.post(f"/sing/submit?t={token}", json=body)
        assert resp.status_code == 200
        partners = resp.get_json()["request"]["additional_singers"]
        # Server normalises missing phone → "".
        assert partners == [{"name": "Phoneless Pete", "phone": ""}]

    def test_submit_auto_approve_with_partners_creates_multi_singer_entry(
        self, client, sing_app, token,
    ):
        """Auto-approve path: a duet request flows through approve_sing_request,
        and the rotation entry gets the joined singer name + singers_json."""
        sing_app.sing_store.set_auto_approve(True)
        try:
            body = self._body()
            body["additional_singers"] = [
                {"name": "Sarah B.", "phone": "+61 400 111 222"},
                {"name": "Mike", "phone": ""},
            ]
            # Use a source_type that doesn't require external services.
            body["source_type"] = "local"
            body["source_ref"] = "/tmp/x.mp4"
            resp = client.post(f"/sing/submit?t={token}", json=body)
            assert resp.status_code == 200
            data = resp.get_json()
            assert data["auto_approved"] is True
            linked_id = data["request"]["linked_entry_id"]
            assert linked_id is not None
            entry = sing_app.rotation.store.get_entry(linked_id)
            assert entry["singer"] == "Andrew & Sarah B. & Mike"
            import json as _json
            names = _json.loads(entry["singers_json"])
            assert names == ["Andrew", "Sarah B.", "Mike"]
        finally:
            sing_app.sing_store.set_auto_approve(False)


class TestStatus:
    def test_status_without_token_rejected(self, client):
        resp = client.get("/sing/status/99999")
        assert resp.status_code == 403

    def test_status_not_found(self, client, token):
        resp = client.get(f"/sing/status/99999?t={token}")
        assert resp.status_code == 404

    def test_status_pending(self, client, sing_app, token):
        resp = client.post(f"/sing/submit?t={token}", json={
            "singer_name": "Andrew", "phone": "+1 555 0000",
            "source_type": "local", "source_ref": "/x.mp4",
        })
        req_id = resp.get_json()["request"]["id"]
        resp = client.get(f"/sing/status/{req_id}?t={token}")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["request"]["status"] == "pending"
        assert "position" not in data  # no rotation link yet

    def test_status_after_approval_includes_position(self, client, sing_app, token):
        resp = client.post(f"/sing/submit?t={token}", json={
            "singer_name": "Andrew", "phone": "+1 555 0000",
            "source_type": "local", "source_ref": "/x.mp4",
        })
        req_id = resp.get_json()["request"]["id"]
        # Admin approves
        resp = client.post(f"/rotation/requests/{req_id}/approve")
        assert resp.status_code == 200
        # Singer checks status
        resp = client.get(f"/sing/status/{req_id}?t={token}")
        data = resp.get_json()
        assert data["request"]["status"] == "approved"
        assert "estimate" in data
        assert data["estimate"]["position"] is not None
        assert "queue" in data

    def test_status_from_previous_event_rejected(self, client, sing_app, token):
        """Requests submitted under an old token must not be readable with a new one."""
        resp = client.post(f"/sing/submit?t={token}", json={
            "singer_name": "Andrew", "phone": "+1 555 0000",
            "source_type": "local", "source_ref": "/x.mp4",
        })
        req_id = resp.get_json()["request"]["id"]
        # KJ starts a new event → new token
        new_token = sing_app.sing_store.regenerate_token()
        # Singer's old tab still has the old token — we verify with the new one
        resp = client.get(f"/sing/status/{req_id}?t={new_token}")
        assert resp.status_code == 404


class TestOverlaySync:
    def test_sync_updates_linked_overlays_only(self, sing_app):
        overlay_manager = sing_app.overlay_manager
        # Overlay linked to event URL
        linked = overlay_manager.create_overlay({
            "type": "qr_code", "name": "Event QR",
            "config": {"url": "old", "follow_event_url": True},
        })
        # Overlay not linked — should NOT change
        unlinked = overlay_manager.create_overlay({
            "type": "qr_code", "name": "Other QR",
            "config": {"url": "leaveme"},
        })
        # Wrong type — should NOT change
        ticker = overlay_manager.create_overlay({
            "type": "ticker", "name": "Ticker",
            "config": {"text": "don't touch"},
        })
        count = sing.sync_event_url_overlays(overlay_manager, "new-url")
        assert count == 1
        assert overlay_manager.get_overlay(linked["id"])["config"]["url"] == "new-url"
        assert overlay_manager.get_overlay(unlinked["id"])["config"]["url"] == "leaveme"
        assert overlay_manager.get_overlay(ticker["id"])["config"]["text"] == "don't touch"


class TestPWAManifest:
    def test_manifest_served_with_current_token(self, client, sing_app, token):
        """Manifest.json carries the current token in start_url."""
        sing_app.sing_store.set_enabled(True)
        resp = client.get(f"/sing/manifest.json?t={token}")
        assert resp.status_code == 200
        assert resp.content_type.startswith("application/json")
        data = resp.get_json()
        assert data["name"] == "Nomad Karaoke"
        assert data["display"] == "standalone"
        assert data["start_url"] == f"/sing/?t={token}"
        assert any(icon["sizes"] == "192x192" for icon in data["icons"])
        assert any(icon["sizes"] == "512x512" for icon in data["icons"])

    def test_manifest_rejects_without_token(self, client):
        resp = client.get("/sing/manifest.json")
        assert resp.status_code == 403


class TestServiceWorker:
    def test_sw_served_at_sing_scope(self, client):
        """sw.js must be served from /sing/ so its scope is /sing/.

        Not token-gated — the browser must be able to fetch updates
        independent of token state.
        """
        resp = client.get("/sing/sw.js")
        assert resp.status_code == 200
        assert "javascript" in resp.content_type
        body = resp.get_data(as_text=True)
        assert "self.addEventListener('push'" in body
        assert "self.addEventListener('notificationclick'" in body

    def test_sw_cache_includes_app_version(self, client, sing_app):
        """SW cache key must be substituted with the app version, not left as a placeholder."""
        resp = client.get("/sing/sw.js")
        body = resp.get_data(as_text=True)
        # Placeholder should be replaced
        assert "__APP_VERSION__" not in body
        # Cache constant should appear (with some version)
        assert "nomad-sing-shell-" in body


class TestMyRequests:
    """`GET /sing/my-requests?ids=...` — multi-song done-screen feed."""

    def _create(self, sing_app, **overrides):
        token = sing_app.sing_store.ensure_token()
        body = {
            "singer_name": "Alice",
            "phone": "",
            "source_type": "local",
            "source_ref": "/tmp/x.mp4",
        }
        body.update(overrides)
        return sing_app.sing_store.create_request(token=token, **body)

    def test_returns_requests_in_requested_order(self, client, sing_app, token):
        r1 = self._create(sing_app, song_artist="Queen", song_title="Bohemian Rhapsody")
        r2 = self._create(sing_app, song_artist="Oasis", song_title="Wonderwall")
        r3 = self._create(sing_app, song_artist="Abba",  song_title="Dancing Queen")
        ids = f"{r2['id']},{r3['id']},{r1['id']}"
        resp = client.get(f"/sing/my-requests?ids={ids}&t={token}")
        assert resp.status_code == 200
        out = resp.get_json()
        assert [r["request"]["id"] for r in out["requests"]] == [
            r2["id"], r3["id"], r1["id"],
        ]
        # now_playing is present and structurally sane (may be empty).
        assert "now_playing" in out

    def test_drops_unknown_ids_silently(self, client, sing_app, token):
        r1 = self._create(sing_app)
        resp = client.get(f"/sing/my-requests?ids=999999,{r1['id']}&t={token}")
        assert resp.status_code == 200
        ids = [r["request"]["id"] for r in resp.get_json()["requests"]]
        assert ids == [r1["id"]]

    def test_drops_foreign_token_rows(self, client, sing_app, token):
        # Create a row, then rotate the token; the old row's token no longer matches.
        r1 = self._create(sing_app)
        sing_app.sing_store.regenerate_token()
        new_token = sing_app.sing_store.get_token()
        resp = client.get(f"/sing/my-requests?ids={r1['id']}&t={new_token}")
        assert resp.status_code == 200
        assert resp.get_json()["requests"] == []

    def test_drops_prior_night_rows_even_when_token_matches(self, client, sing_app, token):
        # The event token is reused across nights (KJs pin a memorable code and
        # a New Rotation does not rotate it), so token-match alone must not let
        # a returning singer's PRIOR-night request ids resolve into "tonight".
        old = self._create(sing_app, song_title="Old Song")
        conn = sing_app.sing_store._get_conn()
        conn.execute(
            "UPDATE sing_requests SET created_at = ? WHERE id = ?",
            ("2026-01-01 12:00:00", old["id"]),
        )
        conn.commit()
        new = self._create(sing_app, song_title="Tonight Song")
        # A New Rotation happened tonight, after the old request but before the new one.
        sing_app.sing_store._set_meta("night_started_at", "2026-06-01 00:00:00")

        resp = client.get(f"/sing/my-requests?ids={old['id']},{new['id']}&t={token}")
        assert resp.status_code == 200
        ids = [r["request"]["id"] for r in resp.get_json()["requests"]]
        assert ids == [new["id"]]  # old prior-night row dropped despite token match

    def test_fails_closed_when_night_marker_unset(self, client, sing_app, token):
        r1 = self._create(sing_app)
        conn = sing_app.sing_store._get_conn()
        conn.execute("DELETE FROM rotation_meta WHERE key = 'night_started_at'")
        conn.commit()
        resp = client.get(f"/sing/my-requests?ids={r1['id']}&t={token}")
        assert resp.status_code == 200
        assert resp.get_json()["requests"] == []

    def test_requires_token(self, client, sing_app):
        r1 = self._create(sing_app)
        resp = client.get(f"/sing/my-requests?ids={r1['id']}")
        assert resp.status_code == 403

    def test_caps_ids_count(self, client, token):
        too_many = ",".join(str(i) for i in range(21))
        resp = client.get(f"/sing/my-requests?ids={too_many}&t={token}")
        assert resp.status_code == 400

    def test_empty_ids_returns_empty_list(self, client, token):
        resp = client.get(f"/sing/my-requests?ids=&t={token}")
        assert resp.status_code == 200
        assert resp.get_json()["requests"] == []

    def test_estimate_present_when_linked(self, client, sing_app, token):
        r1 = self._create(sing_app)
        # Approve so a linked entry exists.
        from routes import approve_sing_request
        entry_id = approve_sing_request(sing_app, sing_app.sing_store.get_request(r1["id"]))
        sing_app.sing_store.mark_approved(r1["id"], linked_entry_id=entry_id)
        resp = client.get(f"/sing/my-requests?ids={r1['id']}&t={token}")
        item = resp.get_json()["requests"][0]
        assert "estimate" in item
        assert item["estimate"]["position"] >= 1
