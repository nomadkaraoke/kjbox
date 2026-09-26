"""Tests for duet-partner auto-matching against tonight's known singers.

Rotation identity is the exact singer-name string, so a partner typed as
"sara" when "Sarah B." is already singing would create a phantom duplicate.
``/sing/submit`` canonicalizes partner names via ``match_known_singer``; the
confirm screen's chips come from ``GET /sing/singers``.
"""

import json

from sing import match_known_singer, _fold_name, _split_duet_name


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

    def test_ambiguous_first_name_never_falls_through_to_typo_pass(self):
        # "sarah" is within typo distance of "Sarah B." (fold "sarah b") but
        # NOT of "Sarah Christopher" — the typo pass would "uniquely" pick
        # Sarah B. if ambiguity didn't hard-stop the ladder.
        known = ["Sarah B.", "Sarah Christopher"]
        assert match_known_singer("sarah", known) is None

    def test_typo_match_when_unambiguous(self):
        assert match_known_singer("Mkie", self.KNOWN) == "Mike"

    def test_unrelated_name_unmatched(self):
        assert match_known_singer("Gregory", self.KNOWN) is None

    def test_requester_self_excluded(self):
        assert match_known_singer("mike", self.KNOWN, exclude="Mike") is None

    def test_empty_typed_unmatched(self):
        assert match_known_singer("", self.KNOWN) is None


class TestSplitDuetName:
    def test_splits_on_ampersand_and_plus(self):
        assert _split_duet_name("Anya & Celeste") == ["Anya", "Celeste"]
        assert _split_duet_name("Cam+Taylor + Jo") == ["Cam", "Taylor", "Jo"]

    def test_leaves_single_names_and_and_alone(self):
        assert _split_duet_name("Sarah B.") == ["Sarah B."]
        assert _split_duet_name("Anderson") == ["Anderson"]
        assert _split_duet_name("Rock and Roll Rob") == ["Rock and Roll Rob"]

    def test_empty(self):
        assert _split_duet_name("") == []
        assert _split_duet_name(None) == []
        assert _split_duet_name(" & ") == []


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

    def test_kj_typed_duet_listed_as_individuals(self, client, sing_app, token):
        # A KJ-typed "Anya & Celeste" (no singers_json) is two people — the
        # "Existing singer" picker must never offer the pair as one person.
        sing_app.rotation.add_entry("Anya & Celeste", "Song A")
        sing_app.rotation.add_entry("Cam + Taylor", "Song B")
        sing_app.rotation.add_entry("Celeste", "Song C")
        singers = client.get(f"/sing/singers?t={token}").get_json()["singers"]
        assert "Anya & Celeste" not in singers and "Cam + Taylor" not in singers
        for name in ("Anya", "Cam", "Taylor"):
            assert name in singers
        assert singers.count("Celeste") == 1

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


class TestKjKnownSingers:
    """GET /rotation/singers/known — the KJ add/edit singer picker's list."""

    def test_lists_singers_with_phone_flag(self, client, sing_app, token):
        sing_app.sing_store.set_auto_approve(True)
        client.post(f"/sing/submit?t={token}", json={
            "singer_name": "Ashlee A", "phone": "", "song_artist": "Q", "song_title": "S",
            "source_type": "local", "source_ref": "/s.mp4", "device_id": "dev1"})
        sing_app.rotation.add_entry("Walk In", "Song - X")
        done = sing_app.rotation.add_entry("Already Sang", "Song - Y")
        sing_app.rotation.update_status(done["id"], "Done")
        singers = {s["name"]: s["on_phone"]
                   for s in client.get("/rotation/singers/known").get_json()["singers"]}
        assert singers["Ashlee A"] is True
        assert singers["Walk In"] is False
        # A singer who has already sung is still tonight's singer.
        assert "Already Sang" in singers
