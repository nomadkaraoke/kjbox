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
