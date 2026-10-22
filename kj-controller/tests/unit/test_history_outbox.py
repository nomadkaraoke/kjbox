"""HistoryOutbox: every performed song of a linked singer goes to decide's
karaoke log exactly once, attributed to the right person (spec 5.5)."""

import logging
from datetime import datetime, timedelta, timezone

import pytest

from history_ids import history_id_for, night_id_for_marker
from history_outbox import HistoryOutbox
from rotation import RotationManager
from rotation_store import RotationStore
from sing_store import SingStore
from stats_store import StatsStore

BOX = "testbox"
CFG = {"box_id": BOX, "decide_api_url": "https://decide.test",
       "decide_kjbox_secret": "s3cret", "sing_tip_kj_name": "Nomad Andrew"}
NOW = datetime(2026, 10, 22, 3, 0, 0)  # naive UTC


def _local(dt):
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def _set_night(sing_store, marker):
    conn = sing_store._get_conn()
    conn.execute("INSERT OR REPLACE INTO rotation_meta (key, value) "
                 "VALUES ('night_started_at', ?)", (marker,))
    conn.commit()


class Resp:
    def __init__(self, status_code):
        self.status_code = status_code
        self.text = ""


class FakeHttp:
    """Records calls; answers with queued status codes (default 200/204) or
    raises a queued exception."""

    def __init__(self):
        self.calls = []
        self.answers = []

    def _answer(self, default):
        if self.answers:
            a = self.answers.pop(0)
            if isinstance(a, Exception):
                raise a
            return Resp(a)
        return Resp(default)

    def put(self, url, json=None, headers=None, timeout=None):
        self.calls.append(("PUT", url, json, headers, None))
        return self._answer(200)

    def delete(self, url, params=None, headers=None, timeout=None):
        self.calls.append(("DELETE", url, None, headers, params))
        return self._answer(204)


class Env:
    def __init__(self, tmp_path):
        self.db = str(tmp_path / "rotation.db")
        self.rotation = RotationManager(self.db)
        self.rs = self.rotation.store
        self.ss = SingStore(self.db)
        self.stats = StatsStore(str(tmp_path / "media.db"))
        self.night = _local(datetime.now() - timedelta(hours=1))
        _set_night(self.ss, self.night)
        self.http = FakeHttp()
        self.now = NOW
        self.cfg = dict(CFG)

    def outbox(self, **kw):
        kw.setdefault("debounce_s", 0)
        return HistoryOutbox(self.rs, self.ss, self.stats, self.cfg,
                             http_post=self.http.put, http_delete=self.http.delete,
                             clock=lambda: self.now, **kw)

    def link(self, device, email, alias=None):
        self.ss.link_account(device, email, "app", self.night)
        if alias:
            self.ss.set_alias(device, alias)

    def entry(self, singer, song="Golden Hour - Nova Lane", singers=None):
        return self.rs.add_entry(singer, song, singers=singers)

    def request(self, device, name, entry_id, artist="Nova Lane", title="Golden Hour",
                friend=False, source_type="local", additional_singers=None):
        req = self.ss.create_request(name, "", song_artist=artist, song_title=title,
                                     source_type=source_type, device_id=device,
                                     additional_singers=additional_singers)
        self.ss.mark_approved(req["id"], linked_entry_id=entry_id)
        if friend:
            self.ss.mark_friend_request(req["id"], name)
        return req

    def done(self, entry_id):
        self.rs.update_status(entry_id, "Done")

    def due(self):
        return self.ss.outbox_due(self.now + timedelta(days=365), limit=100)

    def due_by_email(self):
        return {r["payload"]["gen_email"]: r for r in self.due()}


@pytest.fixture
def env(tmp_path):
    e = Env(tmp_path)
    yield e
    e.ss.close()


# --- attribution -----------------------------------------------------------------


def test_done_enqueues_one_record_per_linked_singer(env):
    env.link("dev-a", "a@x.com", alias="Alice")
    env.link("dev-b", "b@x.com", alias="Bea")
    ea = env.entry("Alice")
    eb = env.entry("Bea", "Paper Moons - The Quiet Hours")
    waiting = env.entry("Alice", "Not Yet - Anyone")
    env.request("dev-a", "Alice", ea["id"])
    env.request("dev-b", "Bea", eb["id"], artist="The Quiet Hours", title="Paper Moons",
                source_type="make")
    env.request("dev-a", "Alice", waiting["id"])
    env.done(ea["id"])
    env.done(eb["id"])

    ob = env.outbox()
    assert ob.scan() == 2
    assert ob.scan() == 0  # unchanged → nothing new

    rows = env.due_by_email()
    assert set(rows) == {"a@x.com", "b@x.com"}
    a = rows["a@x.com"]
    assert a["op"] == "put"
    assert a["history_id"] == history_id_for(BOX, ea["id"], "a@x.com")
    p = a["payload"]
    assert p["history_id"] == a["history_id"]
    assert p["source"] == "kjbox"
    assert p["box_id"] == BOX
    assert p["night_id"] == night_id_for_marker(BOX, env.night)
    assert p["entry_id"] == str(ea["id"])
    assert (p["artist"], p["title"]) == ("Nova Lane", "Golden Hour")
    assert p["singers"] == ["Alice"]
    assert p["venue"] == {"kind": "nomad", "name": "Nomad Andrew"}
    assert p["media"]["source_type"] == "local"
    assert len(p["night_date"]) == 10
    b = rows["b@x.com"]["payload"]
    assert (b["artist"], b["title"]) == ("The Quiet Hours", "Paper Moons")
    assert b["media"]["source_type"] == "make"


def test_unlinked_singer_not_enqueued(env):
    e1 = env.entry("Walker")
    env.request("dev-web", "Walker", e1["id"])  # web device, never linked
    e2 = env.entry("Host Added")                 # KJ typed, no linked name
    env.done(e1["id"])
    env.done(e2["id"])
    ob = env.outbox()
    assert ob.scan() == 0
    assert env.due() == []


def test_host_added_entry_uses_rotation_text_never_brand_code(env):
    env.link("dev-a", "a@x.com", alias="Alice")
    e = env.rs.add_entry("Alice", "Midnight Ferris Wheel - The Lanterns",
                         file_path="/media/NOMAD-1234 - The Lanterns - Midnight.mp4")
    env.done(e["id"])
    ob = env.outbox()
    assert ob.scan() == 1
    p = env.due()[0]["payload"]
    assert (p["artist"], p["title"]) == ("The Lanterns", "Midnight Ferris Wheel")
    assert p["media"]["source_type"] == "rotation"
    assert p["media"]["brand_code"] == "NOMAD-1234"


def test_duet_and_friend_attribution(env):
    env.link("dev-a", "a@x.com", alias="Alice")
    env.link("dev-b", "b@x.com", alias="Bob")
    env.link("dev-d", "d@x.com", alias="Dave")

    # Duet: Alice requests with Bob; both use the app → each gets a record.
    duet = env.entry("Alice & Bob", singers=["Alice", "Bob"])
    env.request("dev-a", "Alice", duet["id"], additional_singers=[{"name": "Bob"}])
    # Friend song for an unlinked Carol: nobody gets it (never the requester).
    carol = env.entry("Carol")
    env.request("dev-a", "Carol", carol["id"], friend=True)
    # Dave's friend song for Bob: Bob (linked, unambiguous) gets it, Dave doesn't.
    for_bob = env.entry("Bob", "Second Wind - Kite Season")
    env.request("dev-d", "Bob", for_bob["id"], artist="Kite Season", title="Second Wind",
                friend=True)
    for e in (duet, carol, for_bob):
        env.done(e["id"])

    ob = env.outbox()
    ob.scan()
    got = {(r["payload"]["entry_id"], r["payload"]["gen_email"]) for r in env.due()}
    assert got == {(str(duet["id"]), "a@x.com"), (str(duet["id"]), "b@x.com"),
                   (str(for_bob["id"]), "b@x.com")}
    duet_rec = next(r for r in env.due() if r["payload"]["entry_id"] == str(duet["id"]))
    assert duet_rec["payload"]["singers"] == ["Alice", "Bob"]


def test_name_match_needs_an_unambiguous_name(env):
    # A5-R10: a host-added "Bob" entry only counts for linked Bob when no other
    # device is "Bob" tonight.
    env.link("dev-b", "b@x.com", alias="Bob")
    other = env.ss.create_request("Bob", "", song_artist="X", song_title="Y",
                                  device_id="dev-other-bob")
    assert other
    e = env.entry("Bob")
    env.done(e["id"])
    ob = env.outbox()
    assert ob.scan() == 0
    # Bob's OWN request always counts, whatever the name.
    own = env.entry("Bob", "Own Song - Me")
    env.request("dev-b", "Bob", own["id"])
    env.done(own["id"])
    assert ob.scan() == 1
    assert env.due()[0]["payload"]["entry_id"] == str(own["id"])


# --- times -------------------------------------------------------------------------


def test_sung_at_is_play_start_not_done_at(env):
    env.link("dev-a", "a@x.com", alias="Alice")
    e = env.entry("Alice")
    env.request("dev-a", "Alice", e["id"])
    started = datetime.now(timezone.utc).replace(tzinfo=None, microsecond=0) - timedelta(minutes=4)
    env.stats.record_play("yt-abc", entry_id=e["id"], played_at=_local(started))
    env.done(e["id"])
    done_at = env.rs.get_entry(e["id"])["done_at"]

    env.outbox().scan()
    p = env.due()[0]["payload"]
    assert p["sung_at"] == started.strftime("%Y-%m-%dT%H:%M:%SZ")
    expected_end = (datetime.strptime(done_at, "%Y-%m-%d %H:%M:%S").astimezone()
                    .astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))
    assert p["ended_at"] == expected_end
    assert p["sung_at"] != p["ended_at"]
    assert p["state"] == "sung"


def test_done_without_play_is_sung_unplayed(env):
    env.link("dev-a", "a@x.com", alias="Alice")
    e = env.entry("Alice")
    env.request("dev-a", "Alice", e["id"])
    env.done(e["id"])
    env.outbox().scan()
    p = env.due()[0]["payload"]
    assert p["sung_at"] is None
    assert p["state"] == "sung_unplayed"
    assert p["ended_at"].endswith("Z")


def test_first_play_started_at(tmp_path):
    st = StatsStore(str(tmp_path / "m.db"))
    assert st.first_play_started_at(5) is None
    st.record_play("yt-a", entry_id=5, played_at="2026-10-21 20:15:00")
    st.record_play("yt-b", entry_id=None, played_at="2026-10-21 20:10:00")
    assert st.first_play_started_at(5) == "2026-10-21T20:15:00Z"
    assert st.first_play_started_at(5, not_before="2026-10-21 21:00:00") is None


# --- undo / redo -------------------------------------------------------------------


def test_done_undo_redo_single_record(env):
    env.link("dev-a", "a@x.com", alias="Alice")
    ob = env.outbox()
    env.rotation.history_outbox = ob  # _after_mutation wakes the thread
    e = env.rotation.add_entry("Alice", "Golden Hour - Nova Lane")
    env.request("dev-a", "Alice", e["id"])
    hid = history_id_for(BOX, e["id"], "a@x.com")

    env.rotation.update_status(e["id"], "Done")
    assert ob._wake.is_set()
    assert ob.run_once() == 1                 # the thread's tick: scan + deliver
    env.rotation.undo()                       # host un-does Done
    assert ob.run_once() == 1
    env.rotation.redo()                       # ...and marks it Done again
    assert ob.run_once() == 1
    assert ob.run_once() == 0

    methods = [(c[0], c[1].rsplit("/", 1)[1]) for c in env.http.calls]
    assert methods == [("PUT", hid), ("DELETE", hid), ("PUT", hid)]
    assert env.http.calls[1][4] == {"gen_email": "a@x.com"}
    rows = env.ss.outbox_rows_for_night(env.night)
    assert list(rows) == [hid]
    assert rows[hid]["op"] == "put" and rows[hid]["state"] == "sent"
    assert env.http.calls[2][2]["state"] in ("sung", "sung_unplayed")


def test_undo_before_delivery_sends_only_the_delete(env):
    env.link("dev-a", "a@x.com", alias="Alice")
    ob = env.outbox()
    env.rotation.history_outbox = ob
    e = env.rotation.add_entry("Alice", "Golden Hour - Nova Lane")
    env.request("dev-a", "Alice", e["id"])
    env.rotation.update_status(e["id"], "Done")
    ob.scan()
    env.rotation.undo()
    ob.run_once()
    assert [c[0] for c in env.http.calls] == ["DELETE"]


def test_mark_sent_ignores_a_swapped_op(env):
    env.ss.outbox_enqueue("kj_x", "put", {"gen_email": "a@x.com"}, env.night, now=NOW)
    env.ss.outbox_enqueue("kj_x", "delete", {"gen_email": "a@x.com"}, env.night, now=NOW)
    env.ss.outbox_mark_sent("kj_x", op="put")  # an in-flight PUT finishing late
    assert env.ss.outbox_rows_for_night(env.night)["kj_x"]["state"] == "pending"


# --- delivery ----------------------------------------------------------------------


def _one_pending(env):
    env.link("dev-a", "a@x.com", alias="Alice")
    e = env.entry("Alice")
    env.request("dev-a", "Alice", e["id"])
    env.done(e["id"])
    ob = env.outbox()
    ob.scan()
    return ob, history_id_for(BOX, e["id"], "a@x.com")


def _row(env, hid):
    return dict(env.ss._get_conn().execute(
        "SELECT * FROM history_outbox WHERE history_id=?", (hid,)).fetchone())


def test_delivery_sends_put_with_secret(env):
    ob, hid = _one_pending(env)
    assert ob.deliver_due() == 1
    method, url, body, headers, _ = env.http.calls[0]
    assert method == "PUT"
    assert url == f"https://decide.test/api/partner/kjbox/karaoke-log/{hid}"
    assert headers == {"X-Kjbox-Secret": "s3cret"}
    assert body["history_id"] == hid
    assert _row(env, hid)["state"] == "sent"


def test_delivery_backoff_and_permanent_failure(env, caplog):
    caplog.set_level(logging.INFO)
    ob, hid = _one_pending(env)
    env.http.answers = [503, 500, 429, ConnectionError("down")]
    expected_waits = [30, 60, 120, 240]
    for wait in expected_waits:
        ob.deliver_due()
        r = _row(env, hid)
        assert r["state"] == "pending"
        assert r["next_attempt_at"] == _local(env.now + timedelta(seconds=wait))
        env.now += timedelta(seconds=wait)
    # Backoff caps at 30 minutes.
    env.ss.outbox_mark_failed(hid, "x", env.now, permanent=False)
    env.ss._get_conn().execute("UPDATE history_outbox SET attempts=10 WHERE history_id=?", (hid,))
    env.ss._get_conn().commit()
    env.http.answers = [502]
    ob.deliver_due()
    assert _row(env, hid)["next_attempt_at"] == _local(env.now + timedelta(seconds=1800))
    env.now += timedelta(seconds=1800)

    # 400: retried daily for 7 days, then permanent; WARNING, never ERROR.
    created = datetime.strptime(_row(env, hid)["created_at"], "%Y-%m-%d %H:%M:%S")
    for _ in range(10):
        env.http.answers = [400]
        assert ob.deliver_due() == 0
        r = _row(env, hid)
        if r["state"] == "failed":
            break
        assert r["next_attempt_at"] == _local(env.now + timedelta(days=1))
        env.now += timedelta(days=1)
    r = _row(env, hid)
    assert r["state"] == "failed"
    assert env.now - created >= timedelta(days=7)
    assert env.now - created < timedelta(days=8)
    assert ob.deliver_due() == 0  # never retried again
    assert not [rec for rec in caplog.records if rec.levelno >= logging.ERROR]
    assert any(rec.levelno == logging.WARNING for rec in caplog.records)


def test_inert_without_config(env, caplog):
    caplog.set_level(logging.INFO)
    env.cfg["decide_kjbox_secret"] = ""
    ob, hid = _one_pending(env)
    assert ob.deliver_due() == 0
    assert ob.deliver_due() == 0
    assert env.http.calls == []
    assert _row(env, hid)["state"] == "pending"
    inert = [r for r in caplog.records if "inert" in r.getMessage()]
    assert len(inert) == 1 and inert[0].levelno == logging.INFO
    env.cfg["decide_kjbox_secret"] = "s3cret"
    env.cfg["decide_api_url"] = ""
    assert ob.deliver_due() == 0 and env.http.calls == []


# --- restarts and nights ------------------------------------------------------------


def test_scan_survives_restart(env, tmp_path):
    ob, hid = _one_pending(env)
    ob.deliver_due()
    # A deploy: fresh stores and a fresh outbox on the same DB files.
    ss2 = SingStore(env.db)
    rs2 = RotationStore(env.db)
    ob2 = HistoryOutbox(rs2, ss2, StatsStore(str(tmp_path / "media.db")), env.cfg,
                        http_post=env.http.put, http_delete=env.http.delete,
                        clock=lambda: env.now, debounce_s=0)
    assert ob2.scan() == 0
    assert ob2.deliver_due() == 0
    assert len(env.http.calls) == 1
    # A pending row from before the restart is still delivered after it.
    e = env.entry("Alice", "Another One - Someone")
    env.request("dev-a", "Alice", e["id"])
    env.done(e["id"])
    ob.scan()
    ob3 = HistoryOutbox(rs2, ss2, env.stats, env.cfg, http_post=env.http.put,
                        http_delete=env.http.delete, clock=lambda: env.now, debounce_s=0)
    assert ob3.scan() == 0
    assert ob3.deliver_due() == 1
    ss2.close()


def test_entry_id_reuse_across_nights_scoped(env):
    # Night 1: Alice's song is sent.
    ob, hid1 = _one_pending(env)
    ob.deliver_due()
    eid = env.rs.get_completed_entries()[0]["id"]
    env.stats.record_play("yt-old", entry_id=eid, played_at=_local(
        datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=2)))
    # Backdate night 1's request, then start night 2.
    conn = env.ss._get_conn()
    conn.execute("UPDATE sing_requests SET created_at=?", (_local(datetime.now() - timedelta(minutes=50)),))
    conn.commit()
    night1 = env.night
    env.rs.archive()
    env.night = _local(datetime.now())
    _set_night(env.ss, env.night)

    # Tonight's scan never retracts night 1's records.
    assert ob.scan() == 0
    assert env.ss.outbox_rows_for_night(night1)[hid1]["op"] == "put"

    # A recycled entry id tonight (unlinked singer) never matches night 1's request.
    env.rs.restore_entries([{"id": eid, "singer": "Zed", "song_artist": "New - Song",
                             "status": "Done", "position": 1}])
    assert ob.scan() == 0

    # Linked tonight: the record is tonight's, without night 1's play time.
    env.link("dev-z", "z@x.com", alias="Zed")
    env.request("dev-z", "Zed", eid)
    assert ob.scan() == 1
    p = env.ss.outbox_rows_for_night(env.night)[history_id_for(BOX, eid, "z@x.com")]["payload"]
    assert p["sung_at"] is None and p["state"] == "sung_unplayed"
    assert p["night_id"] == night_id_for_marker(BOX, env.night)


def test_reused_history_id_from_another_night_is_requeued(env):
    env.ss.outbox_enqueue("kj_r", "put", {"n": 1}, "2026-01-01 10:00:00", now=NOW)
    env.ss.outbox_mark_sent("kj_r")
    assert env.ss.outbox_enqueue("kj_r", "put", {"n": 2}, env.night, now=NOW) is True
    assert env.ss.outbox_rows_for_night(env.night)["kj_r"]["state"] == "pending"


# --- scan cost and safety ------------------------------------------------------------


def test_after_mutation_only_wakes_the_thread(env):
    env.link("dev-a", "a@x.com", alias="Alice")
    ob = env.outbox()
    env.rotation.history_outbox = ob
    e = env.rotation.add_entry("Alice", "Golden Hour - Nova Lane")
    env.request("dev-a", "Alice", e["id"])
    env.rotation.update_status(e["id"], "Done")
    assert ob._wake.is_set()
    assert env.due() == []          # nothing built on the rotation op's path
    ob.run_once()                   # the delivery thread scans + delivers
    assert [c[0] for c in env.http.calls] == ["PUT"]


def test_steady_state_scan_builds_no_payloads(env, monkeypatch):
    ob, _ = _one_pending(env)
    calls = []
    monkeypatch.setattr(ob, "_payload", lambda *a, **k: calls.append(1) or {})
    assert ob.scan() == 0
    assert calls == []


def test_rotation_ops_survive_a_broken_outbox(env):
    class Broken:
        def notify_rotation_changed(self):
            raise RuntimeError("boom")

    env.rotation.history_outbox = Broken()
    e = env.rotation.add_entry("Alice", "Song - Artist")
    env.rotation.update_status(e["id"], "Done")
    assert env.rs.get_entry(e["id"])["status"] == "Done"


def test_rotation_manager_accepts_history_outbox(tmp_path):
    calls = []

    class Spy:
        def notify_rotation_changed(self):
            calls.append(1)

    rm = RotationManager(str(tmp_path / "r.db"), history_outbox=Spy())
    rm.add_entry("Alice", "Song - Artist")
    assert calls


def test_no_accounts_no_work(env):
    e = env.entry("Alice")
    env.done(e["id"])
    ob = env.outbox()
    assert ob.scan() == 0
    assert ob.deliver_due() == 0
    assert env.http.calls == []


def test_create_app_wires_the_outbox_without_a_thread(mock_config, tmp_path):
    from app import create_app

    cfg = dict(mock_config)
    cfg["media_db_path"] = str(tmp_path / "media_library.db")
    flask_app = create_app(config=cfg)
    try:
        assert isinstance(flask_app.history_outbox, HistoryOutbox)
        assert flask_app.rotation.history_outbox is flask_app.history_outbox
        assert flask_app.history_outbox._thread is None
    finally:
        flask_app.catalog.close()


# --- decide contract -----------------------------------------------------------------

# decide's partner model (karaoke-log PUT), encoded here rather than imported:
# pydantic doesn't coerce, so every scalar must already be a str (or None).
_REQUIRED = {"history_id", "gen_email", "source", "box_id", "night_id", "night_date",
             "entry_id", "artist", "title", "sung_at", "ended_at", "state", "singers",
             "venue", "media"}


def _assert_decide_contract(p):
    assert _REQUIRED <= set(p), _REQUIRED - set(p)
    for key, value in p.items():
        if key == "singers":
            assert isinstance(value, list) and len(value) <= 6
            assert all(isinstance(s, str) and len(s) <= 100 for s in value)
        elif key in ("venue", "media"):
            assert isinstance(value, dict)
            assert all(v is None or isinstance(v, str) for v in value.values()), value
        else:
            assert value is None or isinstance(value, str), (key, value)
    assert p["state"] in ("sung", "sung_unplayed")
    assert p["source"] == "kjbox"
    assert p["history_id"].startswith("kj_")


def test_payload_matches_decide_contract(env):
    env.link("dev-a", "a@x.com", alias="Alice")
    long_names = [f"Singer {i} " + "x" * 120 for i in range(8)]
    e = env.rs.add_entry("Alice", "Golden Hour - Nova Lane",
                         file_path="/media/NOMAD-0042 - Nova Lane - Golden Hour.mp4",
                         singers=["Alice"] + long_names)
    env.rs.set_gen_status(e["id"], "job123", "complete")
    env.request("dev-a", "Alice", e["id"], source_type="make")
    env.stats.record_play("gen-1", entry_id=e["id"], played_at=_local(
        datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=3)))
    env.done(e["id"])
    played = env.entry("Alice", "Unplayed - Someone")
    env.done(played["id"])
    env.cfg["sing_tip_kj_name"] = ""
    ob = env.outbox()
    ob.scan()
    rows = env.due()
    assert len(rows) == 2
    for r in rows:
        _assert_decide_contract(r["payload"])
    assert {r["payload"]["state"] for r in rows} == {"sung", "sung_unplayed"}
    # Retractions carry string ids too and still match the entry.
    env.rs.update_status(e["id"], "Waiting")
    ob.scan()
    dels = [r for r in env.due() if r["op"] == "delete"]
    assert len(dels) == 1 and dels[0]["payload"]["entry_id"] == str(e["id"])


def test_ended_at_never_before_sung_at(env):
    # DST fall-back: the local done_at converts an hour early, before the start.
    env.link("dev-a", "a@x.com", alias="Alice")
    e = env.entry("Alice")
    env.request("dev-a", "Alice", e["id"])
    env.done(e["id"])
    done_utc = (datetime.strptime(env.rs.get_entry(e["id"])["done_at"], "%Y-%m-%d %H:%M:%S")
                .astimezone().astimezone(timezone.utc).replace(tzinfo=None))
    late_start = done_utc + timedelta(minutes=30)
    env.stats.record_play("yt-x", entry_id=e["id"], played_at=_local(late_start))
    env.outbox().scan()
    p = env.due()[0]["payload"]
    assert p["sung_at"] == late_start.strftime("%Y-%m-%dT%H:%M:%SZ")
    assert p["ended_at"] == p["sung_at"]
