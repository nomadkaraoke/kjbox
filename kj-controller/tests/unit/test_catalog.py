"""Unit tests for ExternalCatalog, filename parser, and FTS5 query sanitizer."""

import os
import sqlite3

import pytest

from catalog import ExternalCatalog, parse_karaoke_filename, _fts5_safe_query, _detect_format


# --- parse_karaoke_filename tests ---

class TestParseKaraokeFilename:
    def test_three_parts(self):
        """Standard format: disc_id - artist - title."""
        assert parse_karaoke_filename("SC2411-08 - Rascal Flatts - Life Is A Highway.zip") == (
            "SC2411-08", "Rascal Flatts", "Life Is A Highway"
        )

    def test_three_parts_with_dash_in_title(self):
        """Title containing ' - ' stays intact (split limited to 2)."""
        assert parse_karaoke_filename("PHK004 - Bon Jovi - Livin On A Prayer - Extended.zip") == (
            "PHK004", "Bon Jovi", "Livin On A Prayer - Extended"
        )

    def test_two_parts_disc_id(self):
        """Two parts where first has digits -> disc_id."""
        assert parse_karaoke_filename("SC2411 - Some Title.mp4") == (
            "SC2411", "", "Some Title"
        )

    def test_two_parts_artist(self):
        """Two parts where first has no digits -> artist."""
        assert parse_karaoke_filename("Michael Jackson - Billie Jean.mp4") == (
            "", "Michael Jackson", "Billie Jean"
        )

    def test_one_part(self):
        """Single part -> entire stem as title."""
        assert parse_karaoke_filename("SomeRandomFilename.mp4") == (
            "", "", "SomeRandomFilename"
        )

    def test_strips_whitespace(self):
        """Whitespace around parts is stripped."""
        assert parse_karaoke_filename("  PHK004  -  Artist  -  Title  .zip") == (
            "PHK004", "Artist", "Title"
        )

    def test_empty_stem(self):
        """Extension-only filename: splitext treats '.mp4' as stem with no ext."""
        assert parse_karaoke_filename(".mp4") == ("", "", ".mp4")


# --- _fts5_safe_query tests ---

class TestFts5SafeQuery:
    def test_single_term(self):
        assert _fts5_safe_query("bon") == '"bon"*'

    def test_multiple_terms(self):
        assert _fts5_safe_query("bon jovi livin") == '"bon" "jovi" "livin"*'

    def test_strips_special_chars(self):
        result = _fts5_safe_query('bon "jovi" (livin)')
        assert '"' not in result.replace('"bon"', '').replace('"jovi"', '').replace('"livin"*', '')

    def test_empty_query(self):
        assert _fts5_safe_query("") == ''

    def test_only_special_chars(self):
        assert _fts5_safe_query("!@#$%") == ''

    def test_mixed_special_and_words(self):
        result = _fts5_safe_query("bon! jovi?")
        assert '"bon"' in result
        assert '"jovi"' in result


# --- _detect_format tests ---

class TestDetectFormat:
    def test_zip(self):
        assert _detect_format("song.zip") == "cdg+mp3"

    def test_mp4(self):
        assert _detect_format("song.mp4") == "mp4"

    def test_cdg(self):
        assert _detect_format("song.cdg") == "cdg"

    def test_unknown_ext(self):
        assert _detect_format("song.flv") == "flv"

    def test_no_ext(self):
        assert _detect_format("noext") == "unknown"


# --- ExternalCatalog tests ---

class TestExternalCatalog:
    def test_init_defaults(self, mock_config):
        catalog = ExternalCatalog(mock_config)
        assert catalog.db_path == mock_config['external_catalog_db']
        assert catalog._conn is None

    def test_init_custom_db_path(self, mock_config, tmp_path):
        custom_path = str(tmp_path / "custom.db")
        catalog = ExternalCatalog(mock_config, db_path=custom_path)
        assert catalog.db_path == custom_path

    def test_not_available_no_db(self, mock_config):
        catalog = ExternalCatalog(mock_config)
        assert catalog.is_available() is False

    def test_init_schema_creates_tables(self, mock_config):
        catalog = ExternalCatalog(mock_config)
        catalog.init_schema()
        conn = catalog._get_conn()
        tables = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
        table_names = {row['name'] for row in tables}
        assert 'media' in table_names
        catalog.close()

    def test_build_from_file_list(self, mock_config, sample_file_list):
        catalog = ExternalCatalog(mock_config)
        count = catalog.build_from_file_list(sample_file_list)
        assert count == 5
        assert catalog.is_available() is True
        catalog.close()

    def test_build_with_mount_replace(self, mock_config, sample_file_list):
        catalog = ExternalCatalog(mock_config)
        count = catalog.build_from_file_list(
            sample_file_list,
            mount_replace=('/Volumes/Nomad4TBOne/', '/mnt/Nomad4TBOne/')
        )
        assert count == 5
        # Verify paths were rewritten
        conn = catalog._get_conn()
        row = conn.execute("SELECT path FROM media LIMIT 1").fetchone()
        assert row['path'].startswith('/mnt/Nomad4TBOne/')
        catalog.close()

    def test_build_with_callback(self, mock_config, sample_file_list):
        catalog = ExternalCatalog(mock_config)
        callback_calls = []
        catalog.build_from_file_list(sample_file_list, callback=lambda c: callback_calls.append(c))
        # With 5 items and batch_size=5000, callback fires once
        assert len(callback_calls) == 1
        assert callback_calls[0] == 5
        catalog.close()

    def test_build_clears_existing(self, mock_config, sample_file_list):
        catalog = ExternalCatalog(mock_config)
        catalog.build_from_file_list(sample_file_list)
        assert catalog.count() == 5
        # Rebuild
        catalog.build_from_file_list(sample_file_list)
        assert catalog.count() == 5  # Not 10
        catalog.close()

    def test_build_skips_empty_lines(self, mock_config, tmp_path):
        file_list = tmp_path / "sparse.txt"
        file_list.write_text("\n\n/some/path/song.mp4\n\n")
        catalog = ExternalCatalog(mock_config)
        count = catalog.build_from_file_list(str(file_list))
        assert count == 1
        catalog.close()

    def test_search_basic(self, mock_config, sample_file_list):
        catalog = ExternalCatalog(mock_config)
        catalog.build_from_file_list(sample_file_list)
        results = catalog.search("Bon Jovi")
        assert len(results) >= 1
        assert any("Bon Jovi" in r.get('artist', '') for r in results)
        catalog.close()

    def test_search_prefix_match(self, mock_config, sample_file_list):
        catalog = ExternalCatalog(mock_config)
        catalog.build_from_file_list(sample_file_list)
        results = catalog.search("Bohem")
        assert len(results) >= 1
        catalog.close()

    def test_search_empty_query(self, mock_config, sample_file_list):
        catalog = ExternalCatalog(mock_config)
        catalog.build_from_file_list(sample_file_list)
        results = catalog.search("")
        assert results == []
        catalog.close()

    def test_search_no_results(self, mock_config, sample_file_list):
        catalog = ExternalCatalog(mock_config)
        catalog.build_from_file_list(sample_file_list)
        results = catalog.search("zzzznonexistent")
        assert results == []
        catalog.close()

    def test_search_pagination(self, mock_config, sample_file_list):
        catalog = ExternalCatalog(mock_config)
        catalog.build_from_file_list(sample_file_list)
        page1 = catalog.search("Prayer", limit=1, offset=0)
        page2 = catalog.search("Prayer", limit=1, offset=1)
        assert len(page1) <= 1
        # page2 may be empty if only one match
        catalog.close()

    def test_search_returns_dict_keys(self, mock_config, sample_file_list):
        catalog = ExternalCatalog(mock_config)
        catalog.build_from_file_list(sample_file_list)
        results = catalog.search("Bon Jovi")
        assert len(results) >= 1
        result = results[0]
        assert 'path' in result
        assert 'filename' in result
        assert 'folder' in result
        assert 'disc_id' in result
        assert 'artist' in result
        assert 'title' in result
        assert 'format' in result
        catalog.close()

    def test_count(self, mock_config, sample_file_list):
        catalog = ExternalCatalog(mock_config)
        catalog.build_from_file_list(sample_file_list)
        assert catalog.count() == 5
        catalog.close()

    def test_stats(self, mock_config, sample_file_list):
        catalog = ExternalCatalog(mock_config)
        catalog.build_from_file_list(sample_file_list)
        stats = catalog.stats()
        assert stats['total'] == 5
        assert 'cdg+mp3' in stats['by_format']
        assert 'mp4' in stats['by_format']
        assert stats['by_format']['cdg+mp3'] == 3
        assert stats['by_format']['mp4'] == 2
        catalog.close()

    def test_close_and_reopen(self, mock_config, sample_file_list):
        catalog = ExternalCatalog(mock_config)
        catalog.build_from_file_list(sample_file_list)
        catalog.close()
        assert catalog._conn is None
        # Reopen
        assert catalog.is_available() is True
        catalog.close()

    def test_close_idempotent(self, mock_config):
        catalog = ExternalCatalog(mock_config)
        catalog.close()
        catalog.close()  # Should not raise

    def test_search_diacritics_match(self, mock_config, tmp_path):
        """FTS5 unicode61 tokenizer matches diacritics to ASCII equivalents."""
        file_list = tmp_path / "diacritics.txt"
        file_list.write_text(
            "/path/KCD-102989 - Maxïmo Park - Books From Boxes.zip\n"
            "/path/VEVO-2392 - Beyoncé - Halo.mp4\n"
            "/path/PHK-001 - Señor Coconut - Smoke On The Water.zip\n"
        )
        catalog = ExternalCatalog(mock_config)
        catalog.build_from_file_list(str(file_list))
        # ASCII query should match diacritical artist names
        assert len(catalog.search("Maximo Park")) >= 1
        assert len(catalog.search("Beyonce")) >= 1
        assert len(catalog.search("Senor")) >= 1
        # Diacritical query should also match
        assert len(catalog.search("Maxïmo")) >= 1
        assert len(catalog.search("Beyoncé")) >= 1
        catalog.close()

    def test_search_special_chars_safe(self, mock_config, sample_file_list):
        """FTS5 special characters in query don't cause errors."""
        catalog = ExternalCatalog(mock_config)
        catalog.build_from_file_list(sample_file_list)
        # These should not raise
        catalog.search('"quoted"')
        catalog.search("bon AND jovi")
        catalog.search("(test)")
        catalog.search("NEAR/3")
        catalog.close()

    def test_build_duplicate_paths_ignored(self, mock_config, tmp_path):
        """INSERT OR IGNORE handles duplicate paths."""
        file_list = tmp_path / "dupes.txt"
        file_list.write_text("/path/song.mp4\n/path/song.mp4\n")
        catalog = ExternalCatalog(mock_config)
        count = catalog.build_from_file_list(str(file_list))
        # Both lines attempted, but second is ignored by UNIQUE constraint
        assert catalog.count() == 1
        catalog.close()

    def test_is_available_empty_table(self, mock_config):
        """is_available returns False when table exists but has no rows."""
        catalog = ExternalCatalog(mock_config)
        catalog.init_schema()
        assert catalog.is_available() is False
        catalog.close()

    def test_is_available_no_table(self, mock_config, tmp_path):
        """is_available returns False when DB file exists but media table doesn't."""
        db_path = str(tmp_path / "empty.db")
        # Create a valid SQLite DB with no media table
        conn = sqlite3.connect(db_path)
        conn.execute("CREATE TABLE other (id INTEGER)")
        conn.close()
        catalog = ExternalCatalog(mock_config, db_path=db_path)
        assert catalog.is_available() is False
        catalog.close()

    def test_is_available_sqlite_error(self, mock_config, tmp_path):
        """is_available returns False when DB is corrupt."""
        corrupt_db = tmp_path / "corrupt.db"
        corrupt_db.write_text("this is not a sqlite database")
        catalog = ExternalCatalog(mock_config, db_path=str(corrupt_db))
        assert catalog.is_available() is False

    def test_build_large_batch_overflow(self, mock_config, tmp_path):
        """Build with >5000 entries triggers mid-build batch commit."""
        file_list = tmp_path / "large.txt"
        lines = [f"/path/to/disc{i:05d} - Artist{i} - Title{i}.zip" for i in range(5500)]
        file_list.write_text('\n'.join(lines))
        callback_calls = []
        catalog = ExternalCatalog(mock_config)
        count = catalog.build_from_file_list(str(file_list), callback=lambda c: callback_calls.append(c))
        assert count == 5500
        assert catalog.count() == 5500
        # Should have 2 callback calls: one at 5000, one at 5500
        assert len(callback_calls) == 2
        assert callback_calls[0] == 5000
        assert callback_calls[1] == 5500
        catalog.close()

    def test_search_operational_error(self, mock_config):
        """Search returns empty list when FTS table is missing/corrupt."""
        catalog = ExternalCatalog(mock_config)
        # Create media table but NOT the FTS table
        conn = catalog._get_conn()
        conn.execute("""
            CREATE TABLE IF NOT EXISTS media (
                id INTEGER PRIMARY KEY, path TEXT UNIQUE NOT NULL,
                filename TEXT NOT NULL, folder TEXT NOT NULL,
                disc_id TEXT, artist TEXT, title TEXT, format TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE VIRTUAL TABLE IF NOT EXISTS media_fts USING fts5(
                artist, title, disc_id,
                content='media', content_rowid='id',
                tokenize='unicode61 remove_diacritics 2'
            )
        """)
        conn.execute(
            "INSERT INTO media (path, filename, folder, disc_id, artist, title, format) "
            "VALUES ('/test', 'test.mp4', '/dir', '', 'Test', 'Song', 'mp4')"
        )
        conn.commit()
        # Drop FTS table to force OperationalError on search
        conn.execute("DROP TABLE media_fts")
        results = catalog.search("Test")
        assert results == []
        catalog.close()
