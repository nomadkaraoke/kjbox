import pytest
from stats_store import StatsStore, _norm_singer, _norm_artist


@pytest.fixture
def store():
    return StatsStore(":memory:")


def _tables(store):
    conn = store._get_conn()
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    return {r["name"] for r in rows}


def test_schema_creates_all_tables(store):
    assert {"play_events", "preview_events", "version_notes"} <= _tables(store)


def test_schema_idempotent_on_file(tmp_path):
    db = str(tmp_path / "media_library.db")
    StatsStore(db)
    s2 = StatsStore(db)  # second open must not raise
    assert {"play_events", "preview_events", "version_notes"} <= _tables(s2)


def test_norm_singer():
    assert _norm_singer("  Celeste   B ") == "celeste b"
    assert _norm_singer(None) == ""


def _count(store, table):
    return store._get_conn().execute(f"SELECT COUNT(*) c FROM {table}").fetchone()["c"]


def test_record_play_inserts(store):
    assert store.record_play("yt-abc", entry_id=1, singer="Celeste",
                             artist="ABBA", title="SOS", song_key="abba sos") == True
    assert _count(store, "play_events") == 1


def test_record_play_dedups_same_entry(store):
    assert store.record_play("yt-abc", entry_id=7) == True
    assert store.record_play("yt-abc", entry_id=7) == False  # re-press same entry
    assert _count(store, "play_events") == 1


def test_record_play_distinct_entries_both_count(store):
    store.record_play("yt-abc", entry_id=7)
    store.record_play("yt-abc", entry_id=8)  # different entry
    assert _count(store, "play_events") == 2


def test_record_play_no_entry_window_dedups(store):
    assert store.record_play("db-FBK-x", source='live') == True
    assert store.record_play("db-FBK-x", source='live') == False  # same media within 120s
    assert _count(store, "play_events") == 1


def test_record_play_empty_media_returns_false(store):
    assert store.record_play("") == False
    assert store.record_play(None) == False
    assert _count(store, "play_events") == 0


def test_record_play_no_entry_outside_window_counts(store):
    # A no-entry play older than the 120s window must NOT dedup a fresh one.
    store.record_play("db-OLD", entry_id=None, played_at="2020-01-01 00:00:00")
    assert store.record_play("db-OLD", entry_id=None) is True
    assert _count(store, "play_events") == 2


def test_record_preview_inserts_and_windows(store):
    assert store.record_preview("yt-abc", title="ABBA - SOS", song_key="abba sos") is True
    assert store.record_preview("yt-abc") is False            # within 60s window
    assert _count(store, "preview_events") == 1


def test_record_preview_empty_noop(store):
    assert store.record_preview("") is False
    assert _count(store, "preview_events") == 0


def test_stats_for_zero_fills_and_counts(store):
    store.record_play("yt-a", entry_id=1)
    store.record_play("yt-a", entry_id=2)
    store.record_preview("yt-a")
    out = store.stats_for(["yt-a", "yt-missing"])
    assert out["yt-a"]["plays"] == 2
    assert out["yt-a"]["previews"] == 1
    assert out["yt-a"]["last_played"] is not None
    assert out["yt-missing"] == {"plays": 0, "previews": 0, "last_played": None}


def test_stats_for_empty(store):
    assert store.stats_for([]) == {}


def test_usual_media_id_picks_max(store):
    store.record_play("yt-a", entry_id=1)
    store.record_play("yt-b", entry_id=2)
    store.record_play("yt-b", entry_id=3)
    assert store.usual_media_id(["yt-a", "yt-b"]) == "yt-b"


def test_usual_media_id_none_when_all_zero(store):
    assert store.usual_media_id(["yt-a", "yt-b"]) is None


def test_usual_media_id_tiebreak_by_recency(store):
    # Equal play counts -> the id with the most recent play wins.
    store.record_play("yt-old", entry_id=1, played_at="2020-01-01 00:00:00")
    store.record_play("yt-new", entry_id=2, played_at="2026-01-01 00:00:00")
    assert store.usual_media_id(["yt-old", "yt-new"]) == "yt-new"


def test_usual_media_id_empty_input_none(store):
    assert store.usual_media_id([]) is None


def test_upsert_note_creates_then_edits(store):
    n = store.upsert_note("yt-a", "censored version", "censored",
                          artist="ABBA", title="SOS")
    assert n["note"] == "censored version" and n["label"] == "censored"
    n2 = store.upsert_note("yt-a", "edited", "video-bg")
    assert n2["note"] == "edited" and n2["label"] == "video-bg"
    assert n2["artist"] == "ABBA"          # preserved
    assert _count(store, "version_notes") == 1


def test_get_note_missing(store):
    assert store.get_note("nope") is None


def test_distinct_labels(store):
    store.upsert_note("yt-a", "x", "censored")
    store.upsert_note("yt-b", "y", "video-bg")
    store.upsert_note("yt-c", "z", "")     # blank excluded
    assert store.distinct_labels() == ["censored", "video-bg"]


def test_top_songs_overall_and_by_singer(store):
    store.record_play("yt-a", entry_id=1, singer="Celeste", artist="ABBA",
                     title="SOS", song_key="abba sos")
    store.record_play("db-x", entry_id=2, singer="Celeste", artist="ABBA",
                     title="SOS", song_key="abba sos")   # 2nd version, same song
    store.record_play("yt-c", entry_id=3, singer="Dan", artist="Queen",
                     title="Bohemian Rhapsody", song_key="queen bohemian rhapsody")
    overall = store.top_songs(limit=10)
    assert overall[0]["song_key"] == "abba sos" and overall[0]["plays"] == 2
    celeste = store.top_songs(singer="celeste", limit=10)
    assert len(celeste) == 1 and celeste[0]["song_key"] == "abba sos"


def test_top_singers(store):
    store.record_play("yt-a", entry_id=1, singer="Celeste", song_key="s1")
    store.record_play("yt-b", entry_id=2, singer="Celeste", song_key="s2")
    store.record_play("yt-c", entry_id=3, singer="Dan", song_key="s1")
    top = store.top_singers(limit=10)
    assert top[0]["singer"] == "Celeste" and top[0]["plays"] == 2
    assert top[0]["distinct_songs"] == 2


# ===== Song Stats section: new StatsStore methods =====

def test_norm_artist():
    assert _norm_artist("  The   BEATLES ") == "the beatles"
    assert _norm_artist(None) == ""


def test_artist_norm_column_exists(store):
    cols = {r["name"] for r in store._get_conn().execute("PRAGMA table_info(play_events)")}
    assert "artist_norm" in cols


def test_record_play_populates_artist_norm(store):
    store.record_play("yt-a", entry_id=1, artist="The Beatles", title="Hey Jude",
                      song_key="the beatles hey jude", singer="Al")
    row = store._get_conn().execute(
        "SELECT artist_norm FROM play_events WHERE media_id='yt-a'").fetchone()
    assert row["artist_norm"] == "the beatles"


def test_artist_norm_backfilled_on_reopen(tmp_path):
    db = str(tmp_path / "m.db")
    s1 = StatsStore(db)
    s1.record_play("yt-b", entry_id=2, artist="ABBA", title="SOS", song_key="abba sos")
    # Simulate a legacy row written before the column existed.
    conn = s1._get_conn()
    conn.execute("UPDATE play_events SET artist_norm=NULL WHERE media_id='yt-b'")
    conn.commit()
    s2 = StatsStore(db)  # reopen -> backfill runs
    row = s2._get_conn().execute(
        "SELECT artist_norm FROM play_events WHERE media_id='yt-b'").fetchone()
    assert row["artist_norm"] == "abba"


def test_overview_counts(store):
    store.record_play("yt-a", entry_id=1, singer="Al", artist="ABBA", title="SOS", song_key="abba sos")
    store.record_play("yt-b", entry_id=2, singer="Bo", artist="Queen", title="One", song_key="queen one")
    store.record_play("yt-a", entry_id=3, singer="Al", artist="ABBA", title="SOS", song_key="abba sos")
    o = store.overview()
    assert o["total_plays"] == 3
    assert o["distinct_songs"] == 2
    assert o["distinct_singers"] == 2
    assert o["distinct_artists"] == 2
    assert o["plays_last_30d"] == 3


def test_overview_empty(store):
    o = store.overview()
    assert o["total_plays"] == 0 and o["distinct_songs"] == 0


def _seed_artist_rows(store):
    store.record_play("yt-a", entry_id=1, singer="Al", artist="ABBA", title="SOS", song_key="abba sos")
    store.record_play("yt-a", entry_id=2, singer="Bo", artist="ABBA", title="SOS", song_key="abba sos")
    store.record_play("yt-c", entry_id=3, singer="Al", artist="ABBA", title="Mamma", song_key="abba mamma")
    store.record_play("yt-d", entry_id=4, singer="Al", artist="Queen", title="One", song_key="queen one")


def test_top_artists(store):
    _seed_artist_rows(store)
    rows = store.top_artists()
    assert rows[0]["artist"] == "ABBA"
    assert rows[0]["plays"] == 3 and rows[0]["distinct_songs"] == 2


def test_artist_songs(store):
    _seed_artist_rows(store)
    rows = store.artist_songs("abba")
    keys = [r["song_key"] for r in rows]
    assert keys == ["abba sos", "abba mamma"]  # SOS(2) before Mamma(1)
    assert rows[0]["plays"] == 2 and rows[0]["distinct_singers"] == 2


def test_artist_songs_empty_artist(store):
    assert store.artist_songs("") == []


def test_singer_songs(store):
    store.record_play("yt-a", entry_id=1, singer="Celeste", artist="ABBA", title="SOS", song_key="abba sos")
    store.record_play("yt-a", entry_id=2, singer="Celeste", artist="ABBA", title="SOS", song_key="abba sos")
    store.record_play("yt-c", entry_id=3, singer="Celeste", artist="Queen", title="One", song_key="queen one")
    rows = store.singer_songs("celeste")
    assert rows[0]["song_key"] == "abba sos" and rows[0]["plays"] == 2
    assert rows[0]["first_sung"] and rows[0]["last_sung"]


def test_singer_song_history(store):
    store.record_play("yt-a", entry_id=1, singer="Celeste", song_key="abba sos", night_date="2026-06-01")
    store.record_play("yt-a", entry_id=2, singer="Celeste", song_key="abba sos", night_date="2026-06-08")
    hist = store.singer_song_history("celeste", "abba sos")
    assert len(hist) == 2 and hist[0]["night_date"] in ("2026-06-01", "2026-06-08")


def test_singer_songs_empty(store):
    assert store.singer_songs("") == []
    assert store.singer_song_history("x", "") == []


def test_song_history(store):
    store.record_play("yt-a", entry_id=1, singer="Al", song_key="abba sos", night_date="2026-06-01")
    store.record_play("yt-b", entry_id=2, singer="Bo", song_key="abba sos", night_date="2026-06-08")
    hist = store.song_history("abba sos")
    assert len(hist) == 2
    assert {h["singer"] for h in hist} == {"Al", "Bo"}
    assert "media_id" in hist[0]


def test_song_history_empty_key(store):
    assert store.song_history("") == []


def test_busiest_nights(store):
    store.record_play("yt-a", entry_id=1, singer="Al", song_key="k1", night_date="2026-06-01")
    store.record_play("yt-b", entry_id=2, singer="Bo", song_key="k2", night_date="2026-06-01")
    store.record_play("yt-c", entry_id=3, singer="Al", song_key="k1", night_date="2026-06-08")
    rows = store.busiest_nights()
    assert rows[0]["night_date"] == "2026-06-01" and rows[0]["plays"] == 2
    assert rows[0]["distinct_singers"] == 2 and rows[0]["distinct_songs"] == 2


def test_night_setlist(store):
    store.record_play("yt-a", entry_id=1, singer="Al", artist="ABBA", title="SOS",
                      song_key="abba sos", night_date="2026-06-01")
    rows = store.night_setlist("2026-06-01")
    assert rows[0]["singer"] == "Al" and rows[0]["song_key"] == "abba sos"


def test_night_setlist_empty(store):
    assert store.night_setlist("") == []


def test_most_repeated(store):
    for eid in (1, 2, 3):
        store.record_play("yt-a", entry_id=eid, singer="Celeste", artist="Gaga",
                          title="Bad Romance", song_key="gaga bad romance")
    store.record_play("yt-b", entry_id=4, singer="Al", song_key="one off")
    rows = store.most_repeated()
    assert rows[0]["singer"] == "Celeste" and rows[0]["plays"] == 3
    assert all(r["plays"] > 1 for r in rows)  # one-offs excluded
