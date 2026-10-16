"""start_app reindexes a stale catalog FTS index on a background thread."""
from unittest.mock import MagicMock

from app import reindex_stale_catalog_async


def test_current_index_starts_no_thread():
    cat = MagicMock()
    cat.index_is_stale.return_value = False
    assert reindex_stale_catalog_async(cat, {}) is None
    cat.rebuild_fts.assert_not_called()


def test_stale_index_rebuilds_in_background():
    cat = MagicMock()
    cat.index_is_stale.return_value = True
    cat.rebuild_fts.return_value = 3
    t = reindex_stale_catalog_async(cat, {})
    t.join(timeout=5)
    assert not t.is_alive() and t.daemon
    cat.init_schema.assert_called_once()
    cat.rebuild_fts.assert_called_once_with(only_if_stale=True)


def test_reindex_skipped_when_build_already_refreshed():
    cat = MagicMock()
    cat.index_is_stale.return_value = True
    cat.rebuild_fts.return_value = None  # a concurrent full build won the lock
    t = reindex_stale_catalog_async(cat, {})
    t.join(timeout=5)
    assert not t.is_alive()


def test_rebuild_failure_is_logged_not_raised():
    cat = MagicMock()
    cat.index_is_stale.return_value = True
    cat.rebuild_fts.side_effect = RuntimeError("disk full")
    t = reindex_stale_catalog_async(cat, {})
    t.join(timeout=5)
    assert not t.is_alive()


def test_staleness_check_error_starts_no_thread():
    cat = MagicMock()
    cat.index_is_stale.side_effect = RuntimeError("db locked")
    assert reindex_stale_catalog_async(cat, {}) is None


def test_real_catalog_end_to_end(tmp_path):
    from catalog import ExternalCatalog
    lst = tmp_path / "l.txt"
    lst.write_text("/m/NOMAD-0147 - Nia Archives - (Over)Thinking.mp4\n")
    cat = ExternalCatalog({}, db_path=str(tmp_path / "c.db"))
    cat.init_schema()
    cat.build_from_file_list(str(lst))
    conn = cat._get_conn()
    conn.execute("UPDATE catalog_meta SET value='3' WHERE key='normalizer_version'")
    conn.commit()
    t = reindex_stale_catalog_async(cat, {})
    t.join(timeout=30)
    assert cat.index_is_stale() is False
    assert [r["title"] for r in cat.search("overthinking")] == ["(Over)Thinking"]
