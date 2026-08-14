"""Unit tests for routes._add_singer_session_info — singer provenance.

Reproduces the real "two Chailas" incident: a self-registered singer
("Chaila R", own device) vs a duet-partner label ("Chaila", typed into
Ashlee's submission) vs a KJ-hand-added singer (no request).
"""

import types

from sing_store import SingStore
import routes


def _seed_store():
    store = SingStore(":memory:")
    # Night started in the past so today's rows are in-scope.
    store._set_meta("night_started_at", "2000-01-01 00:00:00")

    # Ashlee A submits a duet crediting "Chaila" (no own device) → entry 706.
    r_ashlee = store.create_request(
        singer_name="Ashlee A", phone="8082177808", source_type="local",
        source_ref="/grenade.zip", additional_singers=[{"name": "Chaila", "phone": ""}],
        user_agent="UA-ashlee-iphone",
    )
    store.mark_approved(r_ashlee["id"], linked_entry_id=706)

    # Chaila registers herself as "Chaila R" and submits a solo → entry 711.
    r_chaila = store.create_request(
        singer_name="Chaila R", phone="", source_type="kn",
        source_ref="https://youtu.be/x", user_agent="UA-chaila-android",
    )
    store.mark_approved(r_chaila["id"], linked_entry_id=711)
    return store


def _fake_app(store):
    return types.SimpleNamespace(sing_store=store)


def _singer_stats():
    # Mirrors get_singer_stats(): the duet entry 706 is credited to BOTH
    # Ashlee A and Chaila; Chaila R owns 711; Walter is KJ-added (999).
    return [
        {"name": "Ashlee A", "entries": [{"id": 706}]},
        {"name": "Chaila", "entries": [{"id": 706}]},
        {"name": "Chaila R", "entries": [{"id": 711}]},
        {"name": "Walter", "entries": [{"id": 999}]},
    ]


def _by_name(stats):
    return {s["name"]: s for s in stats}


class TestSessionClassification:
    def test_self_registered_is_singer_ui_with_device(self):
        store = _seed_store()
        stats = _singer_stats()
        routes._add_singer_session_info(stats, app=_fake_app(store))
        chaila_r = _by_name(stats)["Chaila R"]["session"]
        assert chaila_r["origin"] == "singer_ui"
        assert chaila_r["has_device"] is True
        assert chaila_r["device"]["device"] == "Android device" or chaila_r["device"]["raw"] == "UA-chaila-android"
        assert chaila_r["request_count"] == 1
        store.close()

    def test_duet_partner_label_has_no_device(self):
        store = _seed_store()
        stats = _singer_stats()
        routes._add_singer_session_info(stats, app=_fake_app(store))
        chaila = _by_name(stats)["Chaila"]["session"]
        assert chaila["origin"] == "duet_partner"
        assert chaila["has_device"] is False
        assert chaila["phone"] == ""
        store.close()

    def test_primary_submitter_is_singer_ui(self):
        store = _seed_store()
        stats = _singer_stats()
        routes._add_singer_session_info(stats, app=_fake_app(store))
        ashlee = _by_name(stats)["Ashlee A"]["session"]
        assert ashlee["origin"] == "singer_ui"
        assert ashlee["has_device"] is True
        assert ashlee["phone"] == "8082177808"
        store.close()

    def test_kj_added_has_no_request(self):
        store = _seed_store()
        stats = _singer_stats()
        routes._add_singer_session_info(stats, app=_fake_app(store))
        walter = _by_name(stats)["Walter"]["session"]
        assert walter["origin"] == "kj_added"
        assert walter["has_device"] is False
        store.close()

    def test_no_sing_store_is_noop(self):
        stats = _singer_stats()
        routes._add_singer_session_info(stats, app=types.SimpleNamespace(sing_store=None))
        assert all("session" not in s for s in stats)

    def test_missing_night_marker_fails_closed(self):
        store = SingStore(":memory:")  # no night_started set
        r = store.create_request(singer_name="Chaila R", phone="", source_type="kn", source_ref="x")
        store.mark_approved(r["id"], linked_entry_id=711)
        stats = [{"name": "Chaila R", "entries": [{"id": 711}]}]
        routes._add_singer_session_info(stats, app=_fake_app(store))
        # Fails closed → treated as KJ-added rather than phantom-matching.
        assert stats[0]["session"]["origin"] == "kj_added"
        store.close()
