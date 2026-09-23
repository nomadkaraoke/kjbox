"""Tests for duet-partner auto-matching against tonight's known singers.

Rotation identity is the exact singer-name string, so a partner typed as
"sara" when "Sarah B." is already singing would create a phantom duplicate.
``/sing/submit`` canonicalizes partner names via ``match_known_singer``; the
confirm screen's chips come from ``GET /sing/singers``.
"""

import json

from sing import match_known_singer, _fold_name


class TestFoldName:
    def test_case_and_accents(self):
        assert _fold_name("José") == _fold_name("jose")
        assert _fold_name("  Sarah   B. ") == "sarah b"

    def test_empty(self):
        assert _fold_name("") == ""
        assert _fold_name(None) == ""


class TestMatchKnownSinger:
    KNOWN = ["Sarah B.", "Mike", "Jen", "José"]

    def test_exact_folded_match(self):
        assert match_known_singer("sarah b", self.KNOWN) == "Sarah B."
        assert match_known_singer("jose", self.KNOWN) == "José"

    def test_first_name_match_when_unambiguous(self):
        assert match_known_singer("sarah", self.KNOWN) == "Sarah B."

    def test_first_name_ambiguous_stays_unmatched(self):
        known = ["Sarah B.", "Sarah K."]
        assert match_known_singer("sarah", known) is None

    def test_typo_match_when_unambiguous(self):
        assert match_known_singer("Mkie", self.KNOWN) == "Mike"

    def test_unrelated_name_unmatched(self):
        assert match_known_singer("Gregory", self.KNOWN) is None

    def test_requester_self_excluded(self):
        assert match_known_singer("mike", self.KNOWN, exclude="Mike") is None

    def test_empty_typed_unmatched(self):
        assert match_known_singer("", self.KNOWN) is None


class TestKnownSingersEndpoint:
    def test_requires_token(self, client):
        assert client.get("/sing/singers").status_code == 403

    def test_lists_rotation_and_request_singers(self, client, sing_app, token):
        sing_app.rotation.add_entry("Sarah B.", "Song A")
        sing_app.rotation.add_entry("Mike", "Song B", singers=["Mike", "Duet Dana"])
        client.post(f"/sing/submit?t={token}", json={
            "singer_name": "Pending Pete",
            "song_artist": "X", "song_title": "Y",
            "source_type": "local", "source_ref": "/m/x.mp4",
        })
        resp = client.get(f"/sing/singers?t={token}")
        assert resp.status_code == 200
        singers = resp.get_json()["singers"]
        for expected in ("Sarah B.", "Mike", "Duet Dana", "Pending Pete"):
            assert expected in singers

    def test_dedupes_by_folded_name(self, client, sing_app, token):
        sing_app.rotation.add_entry("Sarah B.", "Song A")
        sing_app.rotation.add_entry("sarah b.", "Song B")
        resp = client.get(f"/sing/singers?t={token}")
        singers = resp.get_json()["singers"]
        assert singers.count("Sarah B.") == 1
        assert "sarah b." not in singers


class TestSubmitCanonicalizesPartners:
    def test_partner_folded_onto_known_singer(self, client, sing_app, token):
        sing_app.rotation.add_entry("Sarah B.", "Song A")
        resp = client.post(f"/sing/submit?t={token}", json={
            "singer_name": "Andrew",
            "song_artist": "Queen", "song_title": "Under Pressure",
            "source_type": "local", "source_ref": "/m/up.mp4",
            "additional_singers": [{"name": "sara b", "phone": ""}],
        })
        assert resp.status_code == 200
        req_id = resp.get_json()["request"]["id"]
        stored = sing_app.sing_store.get_request(req_id)
        assert stored["additional_singers"][0]["name"] == "Sarah B."

    def test_unknown_partner_kept_verbatim(self, client, sing_app, token):
        sing_app.rotation.add_entry("Sarah B.", "Song A")
        resp = client.post(f"/sing/submit?t={token}", json={
            "singer_name": "Andrew",
            "song_artist": "Queen", "song_title": "Under Pressure",
            "source_type": "local", "source_ref": "/m/up.mp4",
            "additional_singers": [{"name": "Brand New Person", "phone": ""}],
        })
        assert resp.status_code == 200
        req_id = resp.get_json()["request"]["id"]
        stored = sing_app.sing_store.get_request(req_id)
        assert stored["additional_singers"][0]["name"] == "Brand New Person"

    def test_partner_phone_preserved_through_match(self, client, sing_app, token):
        sing_app.rotation.add_entry("Sarah B.", "Song A")
        resp = client.post(f"/sing/submit?t={token}", json={
            "singer_name": "Andrew",
            "song_artist": "Queen", "song_title": "Under Pressure",
            "source_type": "local", "source_ref": "/m/up.mp4",
            "additional_singers": [{"name": "sarah", "phone": "+1 555 1234"}],
        })
        assert resp.status_code == 200
        stored = sing_app.sing_store.get_request(resp.get_json()["request"]["id"])
        assert stored["additional_singers"][0] == {
            "name": "Sarah B.", "phone": "+1 555 1234"}
