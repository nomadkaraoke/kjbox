"""history_claims.claim: claimed past-night songs go to decide through the
history outbox as ordinary kj_ records (Phase 4c E5), plus the live outbox
never re-attributing a solo entry someone already claimed (R47). Fixture
singers and emails are invented."""

from datetime import datetime, timedelta

import pytest

import night_log
from history_claims import ClaimError, claim, suggestions
from history_ids import history_id_for, night_id_for_marker
from history_outbox import HistoryOutbox
from night_log import NightLog
from rotation import RotationManager
from sing_store import SingStore
from stats_store import StatsStore
from tests.unit.night_log_helpers import _exact_archive, _play, _request, _utc_of_local

BOX = "testbox"
WREN = "e2e-wren@example.com"
JUNO = "e2e-juno@example.com"
NOW = datetime(2026, 10, 29, 12, 0, 0)
NIGHT = "2026-10-20"


class FakeHttp:
    def __init__(self):
        self.calls = []

    def put(self, url, json=None, headers=None, timeout=None):
        self.calls.append(("PUT", url, json, headers))
        return type("R", (), {"status_code": 200, "text": ""})()

    def delete(self, url, params=None, headers=None, timeout=None):
        self.calls.append(("DELETE", url, params, headers))
        return type("R", (), {"status_code": 204, "text": ""})()


class Env:
    def __init__(self, tmp_path):
        db = str(tmp_path / "rotation.db")
        self.rotation = RotationManager(db)
        self.rs = self.rotation.store
        self.ss = SingStore(db)
        self.stats = StatsStore(str(tmp_path / "media.db"))
        self.cfg = {"box_id": BOX, "sing_tip_kj_name": "Nomad Host"}
        self.http = FakeHttp()

    def outbox(self):
        return HistoryOutbox(self.rs, self.ss, self.stats, self.cfg,
                             http_post=self.http.put, http_delete=self.http.delete,
                             clock=lambda: NOW, debounce_s=0)

    def log(self):
        night_log.clear_build_cache()
        return NightLog(self.rs, self.stats._get_conn)

    def names(self, *words):
        return {"words": set(words or ("wren",)), "names": []}

    def suggest(self, email=WREN, words=("wren",)):
        return suggestions(self.log(), self.ss, self.cfg, self.names(*words), email, NOW)

    def night_id(self, date=NIGHT, email=WREN, words=("wren",)):
        return next(n["night_id"] for n in self.suggest(email, words)["nights"]
                    if n["date"] == date)

    def claim(self, night_id, mine=(), not_mine=(), email=WREN, words=("wren",),
              outbox=None):
        return claim(self.log(), self.ss, outbox or self.outbox(), self.cfg,
                     self.names(*words), email, night_id, list(mine), list(not_mine), NOW)

    def outbox_rows(self):
        return [dict(r) for r in self.ss._get_conn().execute(
            "SELECT history_id, op, state, night_started_at FROM history_outbox")]

    def payload(self, hid):
        import json
        row = self.ss._get_conn().execute(
            "SELECT payload_json FROM history_outbox WHERE history_id=?", (hid,)).fetchone()
        return json.loads(row[0])


@pytest.fixture
def env(tmp_path):
    e = Env(tmp_path)
    yield e
    e.ss.close()


def _done(env, eid, singer, night=NIGHT, hhmm="21:00", song="Hello - Adele", **kw):
    _exact_archive(env.rs, eid, singer, song, f"{night} 20:00:00", f"{night} {hhmm}:00", **kw)


def _utc_z(local):
    return _utc_of_local(local).replace(" ", "T") + "Z"


# ---------------------------------------------------------------------------

def test_claim_enqueues_kj_record_with_started_at(env):
    _exact_archive(env.rs, 5012, "Wren B", "Starlite - Muse", f"{NIGHT} 21:40:00",
                   f"{NIGHT} 22:10:00", file_path="/media/NOMAD-1234 - Muse - Starlight.mp4")
    _play(env.stats, "nomad-1234", 5012, "Wren B", _utc_of_local(f"{NIGHT} 22:06:00"))
    _request(env.rs, "Wren B", "Muse", "Starlight", "youtube", f"{NIGHT} 21:39:00",
             linked_entry_id=5012)
    nid = env.night_id()
    ob = env.outbox()
    got = env.claim(nid, mine=[5012], outbox=ob)
    assert got == {"saved": 1, "already_saved": 0, "taken": [], "rejected": 0}

    hid = history_id_for(BOX, 5012, WREN)
    assert env.outbox_rows() == [{"history_id": hid, "op": "put", "state": "pending",
                                  "night_started_at": "claim:" + NIGHT}]
    assert env.payload(hid) == {
        "history_id": hid,
        "gen_email": WREN,
        "source": "kjbox",
        "box_id": BOX,
        "night_id": nid,
        "night_date": NIGHT,
        "entry_id": "5012",
        "artist": "Muse",
        "title": "Starlight",
        "sung_at": _utc_z(f"{NIGHT} 22:06:00"),   # play start, never Done (#284)
        "ended_at": _utc_z(f"{NIGHT} 22:10:00"),
        "state": "sung",
        "singers": ["Wren B"],
        "venue": {"kind": "nomad", "name": "Nomad Host"},
        "media": {"source_type": "youtube", "brand_code": "NOMAD-1234"},
    }
    assert env.ss.claims_for_nights([NIGHT]) == {(NIGHT, 5012): [WREN]}
    member = env.ss._get_conn().execute("SELECT member FROM history_claims").fetchone()[0]
    assert member == "Wren B"
    assert ob._wake.is_set()  # delivery woken


def test_claim_history_id_formula_and_pre_june_namespacing(env):
    _done(env, 5001, "Wren")
    _done(env, 12, "Wren", night="2026-05-14")
    env.claim(env.night_id(), mine=[5001])
    env.claim(env.night_id("2026-05-14"), mine=[12])
    hids = {r["history_id"] for r in env.outbox_rows()}
    assert hids == {history_id_for(BOX, 5001, WREN),
                    history_id_for(BOX, "2026-05-14:12", WREN)}
    old = env.payload(history_id_for(BOX, "2026-05-14:12", WREN))
    assert old["entry_id"] == "12" and old["night_date"] == "2026-05-14"
    assert old["night_id"] == night_id_for_marker(BOX, "night:2026-05-14")


def test_claim_twice_is_idempotent(env):
    _done(env, 5001, "Wren")
    nid = env.night_id()
    assert env.claim(nid, mine=[5001])["saved"] == 1
    again = env.claim(nid, mine=[5001])
    assert again == {"saved": 0, "already_saved": 1, "taken": [], "rejected": 0}
    rows = env.outbox_rows()
    assert [r["history_id"] for r in rows] == [history_id_for(BOX, 5001, WREN)]
    # Repair: the claim exists but its outbox row went missing -> re-enqueued.
    env.ss._get_conn().execute("DELETE FROM history_outbox")
    env.ss._get_conn().commit()
    assert env.claim(nid, mine=[5001])["already_saved"] == 1
    assert [r["history_id"] for r in env.outbox_rows()] == [history_id_for(BOX, 5001, WREN)]


def test_claim_already_sent_row_not_requeued(env):
    _done(env, 5001, "Wren")
    nid = env.night_id()
    env.claim(nid, mine=[5001])
    env.ss.outbox_mark_sent(history_id_for(BOX, 5001, WREN))
    env.claim(nid, mine=[5001])
    assert [r["state"] for r in env.outbox_rows()] == ["sent"]


def test_claim_taken_entry_reported(env):
    _done(env, 5001, "Wren B")
    _done(env, 5002, "Wren B", hhmm="21:30")
    nid = env.night_id()
    assert env.claim(nid, mine=[5001], email=JUNO, words=("wren",))["saved"] == 1
    got = env.claim(nid, mine=[5001, 5002])
    assert got == {"saved": 1, "already_saved": 0, "taken": [5001], "rejected": 0}
    assert {r["history_id"] for r in env.outbox_rows()} == {
        history_id_for(BOX, 5001, JUNO), history_id_for(BOX, 5002, WREN)}


def test_claim_rejects_foreign_entry_invalid_and_writes_nothing(env):
    _done(env, 5001, "Wren B")
    _done(env, 5005, "Juno", hhmm="21:30")          # not this account's name
    _done(env, 5006, "Wren B", night="2026-10-13")  # another night
    nid = env.night_id()
    for mine, not_mine in (([5001, 5005], []), ([5001], [5006]), ([5001], [5001]),
                           (["5001x"], []), ([True], []), ([], [])):
        with pytest.raises(ClaimError) as err:
            env.claim(nid, mine=mine, not_mine=not_mine)
        assert err.value.code == "invalid", (mine, not_mine)
    with pytest.raises(ClaimError) as err:
        env.claim("0000000000000000", mine=[5001])
    assert err.value.code == "not_found"
    assert env.outbox_rows() == []
    assert env.ss.claims_for_nights([NIGHT, "2026-10-13"]) == {}
    assert env.ss.rejections_for(SingStore.email_hash(WREN)) == set()


def test_not_mine_remembered_and_hidden(env):
    _done(env, 5001, "Wren B")
    _done(env, 5002, "Wren B", hhmm="21:30")
    nid = env.night_id()
    got = env.claim(nid, mine=[5001], not_mine=[5002])
    assert got == {"saved": 1, "already_saved": 0, "taken": [], "rejected": 1}
    assert env.ss.rejections_for(SingStore.email_hash(WREN)) == {(NIGHT, 5002)}
    assert env.suggest()["nights"] == []  # one claimed, one rejected: night gone
    # Another account still sees 5002.
    juno = env.suggest(email=JUNO)["nights"]
    assert [s["entry_id"] for s in juno[0]["songs"]] == [5002]


def test_claim_row_survives_tonights_scan(env):
    # Tonight's live rotation has an un-Done entry with the same id as a
    # claimed past entry: tonight's scan must never retract the claim row.
    night = (datetime.now() - timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")
    env.ss._set_meta("night_started_at", night)
    live = env.rs.add_entry("Wren B", "Uprising - Muse")
    env.ss.link_account("dev-w", WREN, "app", night)
    _done(env, live["id"], "Wren B")
    env.claim(env.night_id(), mine=[live["id"]])
    ob = env.outbox()
    ob.scan()
    assert [(r["op"], r["night_started_at"]) for r in env.outbox_rows()] == [
        ("put", "claim:" + NIGHT)]


def test_deliver_due_sends_claim_put_to_decide(env):
    env.cfg.update({"decide_api_url": "https://decide.test", "decide_kjbox_secret": "s3cret"})
    _done(env, 5001, "Wren")
    env.claim(env.night_id(), mine=[5001])
    ob = env.outbox()
    assert ob.deliver_due() == 1
    hid = history_id_for(BOX, 5001, WREN)
    [(method, url, body, headers)] = env.http.calls
    assert method == "PUT"
    assert url == "https://decide.test/api/partner/kjbox/karaoke-log/" + hid
    assert body["history_id"] == hid and body["gen_email"] == WREN
    assert headers == {"X-Kjbox-Secret": "s3cret"}
    assert [r["state"] for r in env.outbox_rows()] == ["sent"]


def test_unplayed_entry_sung_unplayed_null_sung_at(env):
    _done(env, 5001, "Wren", hhmm="21:15")
    env.claim(env.night_id(), mine=[5001])
    p = env.payload(history_id_for(BOX, 5001, WREN))
    assert p["state"] == "sung_unplayed"
    assert p["sung_at"] is None
    assert p["ended_at"] == _utc_z(f"{NIGHT} 21:15:00")
    assert (p["artist"], p["title"]) == ("Adele", "Hello")
    assert p["media"] == {"source_type": "rotation"}


def test_duet_claim_singers_capped_like_the_outbox(env):
    _done(env, 5001, "Juno", singers_json='["Juno", "Wren B"]')
    env.claim(env.night_id(), mine=[5001])
    p = env.payload(history_id_for(BOX, 5001, WREN))
    assert p["singers"] == ["Juno", "Wren B"]
    member = env.ss._get_conn().execute("SELECT member FROM history_claims").fetchone()[0]
    assert member == "Wren B"


# --- R47: the live outbox never re-attributes a claimed solo entry ----------------

def _live_closed_night(env, singers=None):
    night = (datetime.now() - timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")
    env.ss._set_meta("night_started_at", night)
    env.ss.link_account("dev-j", JUNO, "app", night)
    e = env.rs.add_entry("Wren B", "Hello - Adele", singers=singers)
    req = env.ss.create_request("Wren B", "", song_artist="Adele", song_title="Hello",
                                source_type="local", device_id="dev-j")
    env.ss.mark_approved(req["id"], linked_entry_id=e["id"])
    env.rs.update_status(e["id"], "Done")
    env.ss.set_enabled(False)  # requests closed: tonight's Done songs claimable (R34)
    return e


def test_live_attribution_skips_solo_entry_claimed_by_another(env):
    e = _live_closed_night(env)
    [night] = env.suggest()["nights"]
    assert env.claim(night["night_id"], mine=[e["id"]])["saved"] == 1
    env.outbox().scan()
    emails = {env.payload(r["history_id"])["gen_email"] for r in env.outbox_rows()}
    assert emails == {WREN}  # Juno's device made the request, but Wren claimed it


def test_live_attribution_fills_remaining_duet_slots(env):
    e = _live_closed_night(env, singers=["Wren B", "Juno"])
    [night] = env.suggest()["nights"]
    env.claim(night["night_id"], mine=[e["id"]])
    env.outbox().scan()
    emails = {env.payload(r["history_id"])["gen_email"] for r in env.outbox_rows()}
    assert emails == {WREN, JUNO}


def test_not_mine_on_own_claim_is_invalid(env):
    # R51: "not mine" on a song the account holds rejects the whole request
    # (a stored rejection would hide the song if a host undo later frees it).
    _done(env, 5001, "Wren B")
    _done(env, 5002, "Wren B", hhmm="21:30")
    nid = env.night_id()
    env.claim(nid, mine=[5001])
    with pytest.raises(ClaimError) as err:
        env.claim(nid, not_mine=[5001, 5002])
    assert err.value.code == "invalid"
    assert env.ss.claims_for_nights([NIGHT]) == {(NIGHT, 5001): [WREN]}
    assert env.ss.rejections_for(SingStore.email_hash(WREN)) == set()


def test_mine_on_a_rejected_song_clears_the_rejection(env):
    _done(env, 5001, "Wren B")
    _done(env, 5002, "Wren B", hhmm="21:30")
    nid = env.night_id()
    env.claim(nid, mine=[5001], not_mine=[5002])
    assert env.ss.rejections_for(SingStore.email_hash(WREN)) == {(NIGHT, 5002)}
    got = env.claim(nid, mine=[5002])
    assert got["saved"] == 1
    assert env.ss.rejections_for(SingStore.email_hash(WREN)) == set()


def test_replay_after_names_change_still_accepted(env):
    # Claimed as "Wren"; the account's names changed since, so 5001 no longer
    # matches, but a replay of the same claim is still idempotent.
    _done(env, 5001, "Wren B")
    nid = env.night_id()
    env.claim(nid, mine=[5001])
    got = env.claim(nid, mine=[5001], words=("sparrow",))
    assert got == {"saved": 0, "already_saved": 1, "taken": [], "rejected": 0}
    # The repair path still has the entry to rebuild the payload from.
    env.ss._get_conn().execute("DELETE FROM history_outbox")
    env.ss._get_conn().commit()
    env.claim(nid, mine=[5001], words=("sparrow",))
    assert [r["history_id"] for r in env.outbox_rows()] == [history_id_for(BOX, 5001, WREN)]
    # Still never suggested under names that don't match.
    assert env.suggest(words=("sparrow",))["nights"] == []


def test_night_id_stays_stable_when_its_marker_disappears(env):
    marker = f"{NIGHT} 19:00:00"
    env.ss.link_account("dev-j", JUNO, "app", marker)
    _done(env, 5001, "Wren B")
    _done(env, 5002, "Wren B", hhmm="21:30")
    nid = env.night_id()
    assert nid == night_id_for_marker(BOX, marker)
    env.claim(nid, mine=[5001])
    # Juno deletes their account: the only marker for that night goes with it.
    env.ss.forget_gen_account(SingStore.email_hash(JUNO))
    assert env.night_id() == nid  # reused from the night's claim records
    assert env.claim(nid, mine=[5002])["saved"] == 1
    assert env.payload(history_id_for(BOX, 5002, WREN))["night_id"] == nid


def test_night_id_uses_the_current_marker(env):
    marker = f"{NIGHT} 18:30:00"
    env.ss._set_meta("night_started_at", marker)
    _done(env, 5001, "Wren B")
    assert env.night_id() == night_id_for_marker(BOX, marker)


def test_claim_two_threads_one_winner(env):
    import threading
    _done(env, 5001, "Wren B")
    nid = env.night_id()
    results = {}
    barrier = threading.Barrier(2)

    def go(email):
        barrier.wait()
        try:
            results[email] = env.claim(nid, mine=[5001], email=email)
        finally:
            env.ss.close()  # this thread's connection

    threads = [threading.Thread(target=go, args=(e,)) for e in (WREN, JUNO)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(r["saved"] for r in results.values()) == [0, 1]
    assert sorted(len(r["taken"]) for r in results.values()) == [0, 1]
    assert len(env.ss.claims_for_nights([NIGHT])[(NIGHT, 5001)]) == 1
    assert len(env.outbox_rows()) == 1


# --- R52: the host un-Dones a claimed song from tonight ---------------------------------

def test_host_undone_live_entry_releases_claim_and_retracts(env):
    e = _live_closed_night(env)
    [night] = env.suggest()["nights"]
    env.claim(night["night_id"], mine=[e["id"]])
    hid = history_id_for(BOX, e["id"], WREN)
    env.outbox().scan()
    env.rs.update_status(e["id"], "Waiting")  # the host undid the song
    env.outbox().scan()
    assert env.ss.claims_for_nights([night["date"]]) == {}
    row = next(r for r in env.outbox_rows() if r["history_id"] == hid)
    assert row["op"] == "delete"
    assert env.payload(hid) == {"history_id": hid, "gen_email": WREN,
                                "entry_id": str(e["id"])}
    # Sung again: claimable again.
    env.rs.update_status(e["id"], "Done")
    [again] = env.suggest()["nights"]
    assert [s["entry_id"] for s in again["songs"]] == [e["id"]]


def test_scan_rechecks_claims_before_enqueueing(env, monkeypatch):
    # A claim lands after the scan read its context but before it enqueues.
    e = _live_closed_night(env)
    [night] = env.suggest()["nights"]
    real = SingStore.claim_holders
    calls = []

    def first_read_empty(self, ids):
        calls.append(ids)
        if len(calls) == 1:
            out = real(self, ids)
            env.claim(night["night_id"], mine=[e["id"]])  # lands mid-scan
            return out
        return real(self, ids)

    monkeypatch.setattr(SingStore, "claim_holders", first_read_empty)
    env.outbox().scan()
    emails = {env.payload(r["history_id"])["gen_email"] for r in env.outbox_rows()}
    assert emails == {WREN}


# --- release_claims: one retraction path (host undo + R52) ---------------------------

def test_release_claims_deletes_and_queues_retractions(env):
    from history_claims import release_claims
    _done(env, 5001, "Juno & Wren B")
    nid = env.night_id()
    env.claim(nid, mine=[5001])
    env.claim(nid, mine=[5001], email=JUNO, words=("juno",))
    rows = release_claims(env.ss, NIGHT, 5001, email=JUNO.upper(), now=NOW)
    assert [r["gen_email"] for r in rows] == [JUNO]
    hj = history_id_for(BOX, 5001, JUNO)
    assert env.payload(hj) == {"history_id": hj, "gen_email": JUNO, "entry_id": "5001"}
    ops = {r["history_id"]: (r["op"], r["night_started_at"]) for r in env.outbox_rows()}
    assert ops[hj] == ("delete", "claim:" + NIGHT)
    assert ops[history_id_for(BOX, 5001, WREN)][0] == "put"
    assert [r["gen_email"] for r in release_claims(env.ss, NIGHT, 5001)] == [WREN]
    assert release_claims(env.ss, NIGHT, 5001) == []


def test_claim_released_mid_claim_leaves_no_orphan_put(env, monkeypatch):
    # The host undoes the claim between try_claim and the put's enqueue: the
    # put must not survive without its claim row.
    from history_claims import release_claims
    _done(env, 5001, "Wren B")
    nid = env.night_id()
    real = SingStore.outbox_enqueue
    fired = []

    def undo_first(self, hid, op, payload, night_started_at, now=None):
        if op == "put" and not fired:
            fired.append(1)
            release_claims(self, NIGHT, 5001)
        return real(self, hid, op, payload, night_started_at, now=now)

    monkeypatch.setattr(SingStore, "outbox_enqueue", undo_first)
    env.claim(nid, mine=[5001])
    assert env.ss.claims_for_nights([NIGHT]) == {}
    [row] = env.outbox_rows()
    assert row["op"] == "delete"


def test_release_undone_leaves_old_night_claims_alone(env):
    # Since 2026-06-04 ids don't collide, but if one did, tonight's un-Done live
    # entry must never release an older night's claim on the same id.
    night = (datetime.now() - timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")
    env.ss._set_meta("night_started_at", night)
    live = env.rs.add_entry("Wren B", "Uprising - Muse")  # not Done
    assert env.ss.try_claim("2026-07-01", live["id"], WREN, "kj_old", "Wren B", 1) == "claimed"
    env.outbox().scan()
    assert env.ss.claims_for_nights(["2026-07-01"]) == {("2026-07-01", live["id"]): [WREN]}
