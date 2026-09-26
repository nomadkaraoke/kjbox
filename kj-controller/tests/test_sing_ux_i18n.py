"""Unit tests for the 2026-09-23 singer-UX walkthrough fixes + i18n plumbing.

Covers: per-device rate limiting (shared venue wifi), duet display names,
relevance-ranked search groups, the KJ footer settings, the singer event-info
endpoint, rotation preview metadata, and message-file integrity.
"""

import json
import os
import re
from pathlib import Path

import pytest

import routes
import sing
from sing_store import SingStore

KJ = Path(__file__).resolve().parent.parent
MESSAGES = KJ / "static-sing" / "messages"


# --- Rate limiting: per device, then a loose per-IP ceiling -------------------

class _Req:
    def __init__(self, ip, body=None):
        self.remote_addr = ip
        self.headers = {}
        self._body = body

    def get_json(self, force=False, silent=False):
        return self._body


class TestSingerRateLimit:
    def test_each_device_gets_its_own_budget_on_a_shared_ip(self, flask_app):
        with flask_app.app_context():
            flask_app.kj_config["sing_rate_limit_per_device"] = 2
            flask_app.kj_config["sing_rate_limit_per_ip"] = 100
            a = {"device_id": "phone-a"}
            b = {"device_id": "phone-b"}
            assert not sing._singer_rate_limited(_Req("10.0.0.5", a), a)
            assert not sing._singer_rate_limited(_Req("10.0.0.5", a), a)
            assert sing._singer_rate_limited(_Req("10.0.0.5", a), a)       # phone A spent
            assert not sing._singer_rate_limited(_Req("10.0.0.5", b), b)   # phone B unaffected

    def test_per_ip_ceiling_still_backstops_a_device_id_flood(self, flask_app):
        with flask_app.app_context():
            flask_app.kj_config["sing_rate_limit_per_device"] = 100
            flask_app.kj_config["sing_rate_limit_per_ip"] = 3
            for i in range(3):
                body = {"device_id": f"minted-{i}"}
                assert not sing._singer_rate_limited(_Req("10.0.0.9", body), body)
            body = {"device_id": "minted-99"}
            assert sing._singer_rate_limited(_Req("10.0.0.9", body), body)

    def test_rejected_device_does_not_burn_the_shared_ip_budget(self, flask_app):
        """A phone that has hit its own limit must not consume an IP slot on
        every rejected retry — that would 429 everyone else at the venue."""
        with flask_app.app_context():
            flask_app.kj_config["sing_rate_limit_per_device"] = 1
            flask_app.kj_config["sing_rate_limit_per_ip"] = 3
            spent = {"device_id": "spent"}
            assert not sing._singer_rate_limited(_Req("10.0.0.3", spent), spent)
            for _ in range(10):
                assert sing._singer_rate_limited(_Req("10.0.0.3", spent), spent)
            # Only ONE ip slot was ever recorded → two more devices still fit.
            for name in ("fresh-1", "fresh-2"):
                body = {"device_id": name}
                assert not sing._singer_rate_limited(_Req("10.0.0.3", body), body)

    def test_refund_removes_only_this_requests_slot(self, flask_app):
        """A refund must not drop a concurrent request's newer timestamp."""
        with flask_app.test_request_context():
            flask_app.kj_config["sing_rate_limit_per_device"] = 100
            body = {"device_id": "race"}
            assert not sing._singer_rate_limited(_Req("10.0.0.8", body), body)
            mine = sing.g.sing_rl_device_slot[1]
            sing._rate_limit_state["dev:race"].append(mine + 1.0)   # concurrent request
            sing._refund_device_rate_slot()
            assert list(sing._rate_limit_state["dev:race"]) == [mine + 1.0]

    def test_reads_body_when_not_supplied(self, flask_app):
        with flask_app.app_context():
            flask_app.kj_config["sing_rate_limit_per_device"] = 1
            req = _Req("10.0.0.7", {"device_id": "d1"})
            assert not sing._singer_rate_limited(req)
            assert sing._singer_rate_limited(req)

    def test_submit_and_cancel_share_the_device_budget(self, flask_test_client, flask_app):
        store = flask_app.sing_store
        token = store.ensure_token()
        flask_app.kj_config["sing_rate_limit_per_device"] = 1
        flask_app.kj_config["sing_rate_limit_per_ip"] = 100
        first = flask_test_client.post(
            f"/sing/submit?t={token}",
            json={"singer_name": "Zed", "device_id": "dev-z", "source_type": "local",
                  "source_ref": "/tmp/a.mp4", "song_artist": "A", "song_title": "B"},
        )
        assert first.status_code == 200
        second = flask_test_client.post(
            f"/sing/submit?t={token}",
            json={"singer_name": "Zed", "device_id": "dev-z", "source_type": "local",
                  "source_ref": "/tmp/c.mp4", "song_artist": "A", "song_title": "C"},
        )
        assert second.status_code == 429
        assert second.get_json()["error"] == "rate_limited"

    def test_rejected_submits_do_not_burn_the_device_budget(self, flask_test_client, flask_app):
        """A singer retrying a submit the server rejects (400) must not lock
        themselves out with "too many attempts" (Owen, 2026-09-24)."""
        store = flask_app.sing_store
        token = store.ensure_token()
        flask_app.kj_config["sing_rate_limit_per_device"] = 2
        flask_app.kj_config["sing_rate_limit_per_ip"] = 100
        bad = {"singer_name": "Owen", "device_id": "dev-owen", "source_type": "local",
               "song_artist": "A", "song_title": "B"}   # missing source_ref → 400
        for _ in range(5):
            assert flask_test_client.post(f"/sing/submit?t={token}", json=bad).status_code == 400
        good = {**bad, "source_ref": "/tmp/ok.mp4"}
        assert flask_test_client.post(f"/sing/submit?t={token}", json=good).status_code == 200


    def test_photo_consent_taps_spend_the_device_budget_not_the_venues(
            self, flask_test_client, flask_app):
        """2026-09-24: one singer's 11 photo-consent taps (no device_id) 429'd
        against the venue-wide IP budget. With device_id, a tappy phone hits its
        OWN limit and every other phone on the same IP can still save."""
        token = flask_app.sing_store.ensure_token()
        flask_app.kj_config["sing_rate_limit_per_device"] = 3
        flask_app.kj_config["sing_rate_limit_per_ip"] = 5
        tappy = {"consent": "yes", "device_id": "tappy", "items": []}
        codes = [flask_test_client.post(f"/sing/photo-consent?t={token}", json=tappy).status_code
                 for _ in range(11)]
        assert codes[:3] == [200, 200, 200]
        assert set(codes[3:]) == {429}
        other = {"consent": "no", "device_id": "neighbour", "items": []}
        assert flask_test_client.post(f"/sing/photo-consent?t={token}", json=other).status_code == 200

    def test_shipped_defaults_keep_the_per_ip_budget_venue_sized(self, tmp_path):
        """config.py defaulted sing_rate_limit_per_ip to 5 (from the old
        IP-only limiter), silently overriding sing's 60 — on venue wifi the 6th
        singer action in 5 min 429'd everyone (2026-09-24)."""
        from config import load_config
        cfg = load_config(str(tmp_path / "absent.json"))
        assert cfg["sing_rate_limit_per_ip"] == sing._IP_RATE_DEFAULT
        assert cfg["sing_rate_limit_per_device"] == sing._DEVICE_RATE_DEFAULT
        assert cfg["sing_rate_limit_per_ip"] >= 5 * cfg["sing_rate_limit_per_device"]

# --- Duet display names ----------------------------------------------------------

class TestDisplayNames:
    @pytest.mark.parametrize("raw, expected", [
        ("José Álvarez & Maria G.", "José & Maria"),
        ("Andrew", "Andrew"),
        ("Andrew B & Celeste & Jasmine K.", "Andrew & Celeste & Jasmine"),
        ("", ""),
        ("  Solo  ", "Solo"),
    ])
    def test_every_first_name(self, raw, expected):
        assert sing._display_names(raw) == expected


# --- Search group relevance --------------------------------------------------------

def _local(artist, title, n=1):
    return [{"path": f"/m/{artist}-{title}-{i}.mp4", "filename": f"{artist} - {title}.mp4",
             "artist": artist, "title": title, "disc_id": f"XX-{i}"} for i in range(n)]


def _kn(artist, title, n=1, community=True):
    return {"artist": artist, "title": title,
            "tracks": [{"brand_code": f"B{i}", "brand_name": f"Brand {i}",
                        "is_community": community, "youtube_url": f"https://y/{i}"}
                       for i in range(n)]}


class TestGroupRelevance:
    def test_popular_song_with_many_versions_ranks_first(self, flask_app):
        with flask_app.app_context():
            locals_ = (_local("Boyce Avenue", "Despacito", 1)
                       + _local("Luis Fonsi", "Despacito", 4))
            groups = routes._group_search_results(locals_, [], query="despacito")
            assert [g["artist"] for g in groups][:2] == ["Luis Fonsi", "Boyce Avenue"]

    def test_exact_title_beats_variant_titles(self, flask_app):
        with flask_app.app_context():
            locals_ = (_local("Lordi", "Hard Rock Hallelujah", 5)
                       + _local("Jeff Buckley", "Hallelujah", 2))
            groups = routes._group_search_results(locals_, [], query="hallelujah")
            assert groups[0]["artist"] == "Jeff Buckley"

    def test_in_library_beats_online_only(self, flask_app):
        with flask_app.app_context():
            locals_ = _local("Glow", "Dancing Queen", 1)
            kn = [_kn("Panic! At the Disco", "Dancing Queen", 3)]
            groups = routes._group_search_results(locals_, kn, query="dancing queen")
            assert groups[0]["artist"] == "Glow"
            assert groups[0]["in_library"] is True

    def test_without_query_keeps_catalog_order(self, flask_app):
        with flask_app.app_context():
            locals_ = _local("Zed", "Song", 1) + _local("Abe", "Song", 3)
            groups = routes._group_search_results(locals_, [])
            assert [g["artist"] for g in groups] == ["Zed", "Abe"]


# --- Footer settings (store + KJ config + singer endpoint) --------------------------

class TestFooterSettings:
    def test_store_defaults_and_validation(self, tmp_path):
        store = SingStore(str(tmp_path / "rot.db"))
        assert store.get_footer_settings() == {"message": "", "notices": [], "social": {}, "ask_photo_consent": False}
        saved = store.set_footer_settings({
            "message": "  Kitchen closes at 11  ",
            "notices": ["chargers", "bogus", "wifi", "chargers"],
        })
        assert saved == {"message": "Kitchen closes at 11", "notices": ["chargers", "wifi"], "social": {}, "ask_photo_consent": False}
        assert store.get_footer_settings() == saved
        # Partial update keeps the other half.
        store.set_footer_settings({"notices": []})
        assert store.get_footer_settings() == {"message": "Kitchen closes at 11", "notices": [], "social": {}, "ask_photo_consent": False}
        with pytest.raises(ValueError):
            store.set_footer_settings({"notices": "chargers"})
        with pytest.raises(ValueError):
            store.set_footer_settings({"message": 12})

    def test_message_is_capped(self, tmp_path):
        store = SingStore(str(tmp_path / "rot.db"))
        store.set_footer_settings({"message": "x" * 1000})
        assert len(store.get_footer_settings()["message"]) == SingStore.FOOTER_MESSAGE_MAX

    def test_kj_config_round_trip_and_singer_event_info(self, flask_test_client, flask_app):
        resp = flask_test_client.post("/rotation/requests/config", json={
            "footer_settings": {"message": "Last call 12:30", "notices": ["lyricsScreen"]},
        })
        assert resp.status_code == 200
        assert resp.get_json()["changed"]["footer_settings"] == {
            "message": "Last call 12:30", "notices": ["lyricsScreen"], "social": {}, "ask_photo_consent": False}
        cfg = flask_test_client.get("/rotation/requests/config").get_json()
        assert cfg["footer_settings"] == {"message": "Last call 12:30", "notices": ["lyricsScreen"], "social": {}, "ask_photo_consent": False}

        token = flask_app.sing_store.ensure_token()
        info = flask_test_client.get(f"/sing/event-info?t={token}").get_json()
        assert info["footer_message"] == "Last call 12:30"
        assert info["notices"] == ["lyricsScreen"]
        assert "kj_name" in info

    def test_bad_footer_settings_400(self, flask_test_client):
        resp = flask_test_client.post("/rotation/requests/config",
                                      json={"footer_settings": {"notices": 5}})
        assert resp.status_code == 400

    def test_every_notice_key_has_singer_copy(self):
        en = json.loads((MESSAGES / "en.json").read_text(encoding="utf-8"))
        for key in SingStore.FOOTER_NOTICE_KEYS:
            assert key in en["notices"], key


# --- Rotation preview metadata + entry preview resolve ---------------------------------

class TestEntryPreview:
    def test_rotation_rows_carry_entry_id_and_previewable(self, flask_test_client, flask_app, tmp_path):
        rotation = flask_app.rotation
        f = tmp_path / "song.mp4"
        f.write_bytes(b"x")
        e1 = rotation.add_entry("Ana & Ben", "Song - Artist", file_path=str(f))["id"]
        e2 = rotation.add_entry("Cy", "Other - Artist")["id"]
        token = flask_app.sing_store.ensure_token()
        data = flask_test_client.get(f"/sing/rotation?t={token}").get_json()
        by_id = {row["entry_id"]: row for row in data["entries"]}
        assert by_id[e1]["previewable"] is True
        assert by_id[e1]["display_name"] == "Ana & Ben"
        assert by_id[e2]["previewable"] is False
        # No file paths leak.
        assert "file_path" not in json.dumps(data)

    def test_preview_resolve_entry_source(self, flask_test_client, flask_app, tmp_path):
        rotation = flask_app.rotation
        token = flask_app.sing_store.ensure_token()
        bare = rotation.add_entry("Dee", "No file yet")["id"]
        resp = flask_test_client.post(f"/sing/preview/resolve?t={token}",
                                      json={"source": "entry", "entry_id": bare})
        assert resp.status_code == 404
        resp = flask_test_client.post(f"/sing/preview/resolve?t={token}",
                                      json={"source": "entry", "entry_id": "nope"})
        assert resp.status_code == 404
        f = tmp_path / "ready.mp4"
        f.write_bytes(b"x")
        ready = rotation.add_entry("Eve", "Ready - Artist", file_path=str(f))["id"]
        resp = flask_test_client.post(f"/sing/preview/resolve?t={token}",
                                      json={"source": "entry", "entry_id": ready})
        # Descriptor accepted; the test app has no preview service (503) or
        # resolves it (200) — either proves the entry path is wired.
        assert resp.status_code in (200, 503)


# --- Message files -------------------------------------------------------------------

def _flatten(obj, prefix=""):
    out = {}
    for k, v in obj.items():
        key = f"{prefix}{k}"
        if isinstance(v, dict):
            out.update(_flatten(v, key + "."))
        else:
            out[key] = v
    return out


class TestMessageFiles:
    def test_en_json_is_valid_and_has_no_empty_strings(self):
        en = json.loads((MESSAGES / "en.json").read_text(encoding="utf-8"))
        flat = _flatten(en)
        assert len(flat) > 250
        assert all(isinstance(v, str) and v.strip() for v in flat.values())

    def test_every_locale_has_key_parity_and_placeholders(self):
        en = _flatten(json.loads((MESSAGES / "en.json").read_text(encoding="utf-8")))
        locales = sorted(p for p in MESSAGES.glob("*.json") if p.stem != "en")
        ph = re.compile(r"\{(\w+)\}")
        for path in locales:
            data = _flatten(json.loads(path.read_text(encoding="utf-8")))
            assert set(data) == set(en), f"{path.name}: key mismatch"
            for key, val in en.items():
                assert set(ph.findall(val)) == set(ph.findall(str(data[key]))), \
                    f"{path.name}: placeholder mismatch in {key}"

    def test_sing_js_keys_exist_in_en(self):
        js = (KJ / "static-sing" / "sing.js").read_text(encoding="utf-8")
        html = (KJ / "templates" / "sing.html").read_text(encoding="utf-8")
        en = _flatten(json.loads((MESSAGES / "en.json").read_text(encoding="utf-8")))
        used = set(re.findall(r'\btn?\("([a-zA-Z0-9_.]+)"', js))
        used |= set(re.findall(r'data-i18n(?:-[a-z-]+)?="([a-zA-Z0-9_.]+)"', html))
        missing = [k for k in used if k not in en and not any(e.startswith(k + ".") for e in en)]
        assert not missing, missing

    def test_no_hardcoded_kj_jargon_in_singer_copy(self):
        """Singer-facing English says "host"; "KJ" only survives in the one
        explanatory mention on the name screen."""
        en = _flatten(json.loads((MESSAGES / "en.json").read_text(encoding="utf-8")))
        offenders = [k for k, v in en.items() if re.search(r"\bKJ\b", v) and k != "identity.intro"]
        assert not offenders, offenders
