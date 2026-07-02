import pytest
from stats_store import StatsStore, _norm_singer


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
