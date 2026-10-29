"""history_claims.suggestions: the past-night songs an account might have sung
(Phase 4c E4, spec 6.10). Fixture singers and emails are invented."""

import datetime as dt

import pytest

import night_log
from history_claims import MAX_NIGHTS, suggestions
from history_ids import night_id_for_marker
from night_log import NightLog
from rotation_store import RotationStore
from sing_store import SingStore
from stats_store import StatsStore
from tests.unit.night_log_helpers import (
    _exact_archive,
    _legacy_archive,
    _play,
    _request,
    _utc_of_local,
)

BOX = "testbox"
CFG = {"box_id": BOX, "sing_tip_kj_name": "Nomad Host"}
WREN = "e2e-wren@example.com"
JUNO = "e2e-juno@example.com"
QUILL = "e2e-quill@example.com"
NOW = dt.datetime(2026, 10, 29, 12, 0, 0)
MARKER = "2026-10-29 19:00:00"


@pytest.fixture
def env(tmp_path):
    rot_path = str(tmp_path / "rotation.db")
    rot = RotationStore(rot_path)
    sing = SingStore(rot_path)
    stats = StatsStore(str(tmp_path / "media.db"))
    night_log.clear_build_cache()
    yield rot, sing, stats
    rot.close()
    sing.close()


def _sugg(env, words=("wren",), email=WREN):
    rot, sing, stats = env
    night_log.clear_build_cache()  # rows change between calls within a test
    names = {"words": set(words), "names": []}
    return suggestions(NightLog(rot, stats._get_conn), sing, CFG, names, email, NOW)


def _ids(night):
    return [s["entry_id"] for s in night["songs"]]


def _done(rot, eid, singer, song="Hello - Adele", night="2026-10-20", hhmm="21:00",
          singers_json=None, status="Done"):
    _exact_archive(rot, eid, singer, song, f"{night} 20:00:00", f"{night} {hhmm}:00",
                   singers_json=singers_json, status=status)


def _utc_z(local):
    return _utc_of_local(local).replace(" ", "T") + "Z"


# ---------------------------------------------------------------------------

def test_suggests_matching_done_entries_only(env):
    rot, _, _ = env
    _done(rot, 5001, "Wren B")
    _done(rot, 5002, "Wren", status="Waiting")
    _done(rot, 5003, "wren", status="Cancelled")
    _done(rot, 5004, "Wrenna")
    _done(rot, 5005, "Juno")
    _done(rot, 5006, "DJ Wren")
    got = _sugg(env)
    assert got["more_nights"] == 0
    [night] = got["nights"]
    assert night["date"] == "2026-10-20"
    assert _ids(night) == [5001]
    assert night["names"] == ["Wren B"]
    assert night["venue"] == "Nomad Host"
    assert night["unmatched"] == 0


def test_no_names_no_suggestions(env):
    rot, _, _ = env
    _done(rot, 5001, "Wren B")
    assert _sugg(env, words=()) == {"nights": [], "more_nights": 0}


def test_legacy_night_only_linked_entries_and_unmatched_count(env):
    rot, _, stats = env
    # Inferred: the play event knows the original entry id.
    _legacy_archive(rot, "Wren", "Hello - Adele", "/m/a [lib-hello1].zip",
                    "2026-10-08 20:00:00")
    _play(stats, "lib-hello1", 1477, "Wren", _utc_of_local("2026-10-08 20:30:00"))
    # Unlinked and matching: only counted.
    _legacy_archive(rot, "Wren B", "Uprising - Muse", None, "2026-10-08 21:00:00")
    # A legacy night with only unlinked matches is dropped (R38).
    _legacy_archive(rot, "Wren", "Starlight - Muse", None, "2026-10-01 21:00:00")
    got = _sugg(env)
    [night] = got["nights"]
    assert night["date"] == "2026-10-08"
    assert _ids(night) == [1477]
    assert night["unmatched"] == 1


def test_started_at_not_done_time(env):
    rot, _, stats = env
    _exact_archive(rot, 5012, "Wren B", "Starlight - Muse", "2026-10-20 21:40:00",
                   "2026-10-20 22:10:00", file_path="/m/x [lib-star1].mp4")
    _play(stats, "lib-star1", 5012, "Wren B", _utc_of_local("2026-10-20 22:06:00"),
          artist="Muse", title="Starlight")
    _done(rot, 5013, "Wren", song="Hello - Adele", hhmm="22:30")  # no play
    [night] = _sugg(env)["nights"]
    by_id = {s["entry_id"]: s for s in night["songs"]}
    assert by_id[5012]["started_at"] == _utc_z("2026-10-20 22:06:00")
    assert by_id[5012]["state"] == "sung"
    assert (by_id[5012]["artist"], by_id[5012]["title"]) == ("Muse", "Starlight")
    assert by_id[5013]["started_at"] is None
    assert by_id[5013]["state"] == "sung_unplayed"
    assert (by_id[5013]["artist"], by_id[5013]["title"]) == ("Adele", "Hello")
    # Sorted by started_at (then added_at): the played song first.
    assert _ids(night) == [5012, 5013]


def test_excludes_claimed_by_other_rejected_and_own(env):
    rot, sing, _ = env
    for eid in (5001, 5002, 5003, 5004):
        _done(rot, eid, "Wren B", hhmm=f"21:0{eid - 5000}")
    assert sing.try_claim("2026-10-20", 5001, JUNO, "kj_j", "Wren B", 1) == "claimed"
    sing.add_rejections(SingStore.email_hash(WREN), "2026-10-20", [5002])
    assert sing.try_claim("2026-10-20", 5003, WREN, "kj_w", "Wren B", 1) == "claimed"
    [night] = _sugg(env)["nights"]
    assert _ids(night) == [5004]


def test_outbox_recorded_entry_excluded(env):
    rot, sing, _ = env
    _done(rot, 5001, "Wren B")
    _done(rot, 5002, "Wren B", hhmm="21:30")
    sing.outbox_enqueue("kj_live", "put", {"gen_email": JUNO, "entry_id": "5001"},
                        "2026-10-20 19:00:00")
    [night] = _sugg(env)["nights"]
    assert _ids(night) == [5002]
    # Before 2026-06-04 entry ids were recycled: the live record doesn't hide it.
    _done(rot, 5001, "Wren B", night="2026-05-14")
    got = {n["date"]: _ids(n) for n in _sugg(env)["nights"]}
    assert got["2026-05-14"] == [5001]


def test_duet_still_offered_until_capacity(env):
    rot, sing, _ = env
    _done(rot, 5001, "Juno", singers_json='["Juno", "Wren B"]')
    assert sing.try_claim("2026-10-20", 5001, JUNO, "kj_j", "Juno", 2) == "claimed"
    [night] = _sugg(env)["nights"]
    assert _ids(night) == [5001]
    assert night["songs"][0]["members"] == ["Juno", "Wren B"]
    assert night["names"] == ["Wren B"]
    assert sing.try_claim("2026-10-20", 5001, QUILL, "kj_q", "Wren B", 2) == "claimed"
    assert _sugg(env)["nights"] == []


def test_live_night_hidden_while_open_shown_when_closed(env):
    rot, sing, _ = env
    e = rot.add_entry("Wren B", song_artist="Hello - Adele")
    rot.update_status(e["id"], "Done")
    assert sing.is_enabled()
    assert _sugg(env)["nights"] == []
    sing.set_enabled(False)
    [night] = _sugg(env)["nights"]
    assert _ids(night) == [e["id"]]


def test_starter_entry_never_suggested(env):
    rot, _, _ = env
    _done(rot, 5001, "Andrew", song="First Song of the Night")
    _done(rot, 5002, "Andrew", song="first song of the night ", hhmm="21:05")
    assert _sugg(env, words=("andrew",))["nights"] == []


def test_night_id_matches_outbox_night_when_marker_known(env):
    rot, sing, _ = env
    _done(rot, 5001, "Wren B")
    marker = "2026-10-20 18:55:00"
    sing.outbox_enqueue("kj_other", "put", {"gen_email": JUNO, "entry_id": "4999"}, marker)
    # A singer_accounts marker in the small hours belongs to the previous night.
    _done(rot, 5101, "Wren B", night="2026-10-26")
    sing.link_account("dev-x", QUILL, "app", "2026-10-27 01:30:00")
    got = {n["date"]: n["night_id"] for n in _sugg(env)["nights"]}
    assert got["2026-10-20"] == night_id_for_marker(BOX, marker)
    assert got["2026-10-26"] == night_id_for_marker(BOX, "2026-10-27 01:30:00")


def test_night_id_ignores_claim_rows(env):
    rot, sing, _ = env
    _done(rot, 5001, "Wren B")
    sing.outbox_enqueue("kj_c", "put", {"gen_email": JUNO, "entry_id": "4999"},
                        "claim:2026-10-20")
    [night] = _sugg(env)["nights"]
    assert night["night_id"] == night_id_for_marker(BOX, "night:2026-10-20")


def test_night_id_fallback_for_old_nights(env):
    rot, _, _ = env
    _done(rot, 12, "Wren", night="2026-05-01")
    [night] = _sugg(env)["nights"]
    assert night["night_id"] == night_id_for_marker(BOX, "night:2026-05-01")


def test_cap_20_nights_more_count(env):
    rot, _, _ = env
    base = dt.date(2026, 7, 1)
    for i in range(MAX_NIGHTS + 3):
        day = (base + dt.timedelta(days=7 * i)).isoformat()
        _done(rot, 6000 + i, "Wren", night=day)
    got = _sugg(env)
    assert MAX_NIGHTS == 20
    assert len(got["nights"]) == 20
    assert got["more_nights"] == 3
    dates = [n["date"] for n in got["nights"]]
    assert dates == sorted(dates, reverse=True)
    assert dates[0] == (base + dt.timedelta(days=7 * 22)).isoformat()


def test_response_has_no_private_fields(env):
    rot, sing, stats = env
    conn = rot._get_conn()
    _exact_archive(rot, 5001, "Wren B", "Hello - Adele", "2026-10-20 20:00:00",
                   "2026-10-20 21:00:00", file_path="/opt/nomad/downloads/youtube/x [yt-q].mp4")
    conn.execute("UPDATE rotation_archive SET notes='call me later', paid=1")
    conn.commit()
    _request(rot, "Wren B", "Adele", "Hello", "youtube", "2026-10-20 19:59:00",
             linked_entry_id=5001)
    sing.link_account("dev-w", WREN, "app", "2026-10-20 19:00:00")
    sing.outbox_enqueue("kj_x", "put", {"gen_email": JUNO, "entry_id": "4000"},
                        "2026-10-20 19:00:00")
    got = _sugg(env)
    [night] = got["nights"]
    assert set(got) == {"nights", "more_nights"}
    assert set(night) == {"night_id", "date", "venue", "names", "unmatched", "songs"}
    assert set(night["songs"][0]) == {"entry_id", "singer_name", "members", "artist",
                                      "title", "started_at", "state"}
    banned = ("device", "phone", "request", "email", "note", "paid", "file", "media")

    def walk(x):
        if isinstance(x, dict):
            for k, v in x.items():
                assert not any(b in k.lower() for b in banned), k
                walk(v)
        elif isinstance(x, list):
            for v in x:
                walk(v)
        elif isinstance(x, str):
            assert "@" not in x and "call me" not in x and "/opt/" not in x, x

    walk(got)


def test_recycled_id_twice_in_one_night_counts_as_unmatched(env):
    rot, _, _ = env
    _done(rot, 12, "Wren", night="2026-05-14", hhmm="21:00")
    _done(rot, 12, "Wren B", night="2026-05-14", hhmm="22:00", song="Uprising - Muse")
    [night] = _sugg(env)["nights"]
    assert _ids(night) == [12]
    assert night["unmatched"] == 1
