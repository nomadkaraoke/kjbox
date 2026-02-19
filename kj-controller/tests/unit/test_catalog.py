"""Unit tests for ExternalCatalog, filename parser, and FTS5 query sanitizer."""

import os
import sqlite3
import unicodedata

import pytest

from catalog import ExternalCatalog, parse_karaoke_filename, _fts5_safe_query, _detect_format, _normalize_for_search, LATIN_SPECIAL_MAP
from tests.fixtures import (
    KARAOKE_CATALOG_ENTRIES,
    SEARCH_NORMALIZATION_CASES,
    FILENAME_PARSE_CASES,
)


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

    @pytest.mark.parametrize("filename,expected", FILENAME_PARSE_CASES,
                             ids=[c[0][:40] for c in FILENAME_PARSE_CASES])
    def test_real_world_filenames(self, filename, expected):
        """Parse real karaoke filenames with special characters."""
        result = parse_karaoke_filename(filename)
        # Normalize to NFC for comparison — input may be NFD (decomposed)
        # but expected values use NFC (precomposed)
        nfc = lambda t: tuple(unicodedata.normalize('NFC', s) for s in t)
        assert nfc(result) == nfc(expected)


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


# --- _normalize_for_search tests (real karaoke catalog examples) ---

class TestNormalizeForSearch:
    """Test search normalization using real artist/song names from karaoke catalog."""

    # NFD-decomposable diacritics (combining marks)
    def test_diaeresis(self):
        assert _normalize_for_search("Maxïmo Park") == "Maximo Park"

    def test_acute(self):
        assert _normalize_for_search("Beyoncé") == "Beyonce"

    def test_tilde(self):
        assert _normalize_for_search("Señor Coconut") == "Senor Coconut"

    def test_cedilla(self):
        assert _normalize_for_search("Convicção") == "Conviccao"

    def test_circumflex(self):
        assert _normalize_for_search("Derê") == "Dere"

    def test_ring_above(self):
        assert _normalize_for_search("Blå Øjne") == "Bla Ojne"

    def test_grave(self):
        assert _normalize_for_search("Phèdre") == "Phedre"

    def test_caron(self):
        assert _normalize_for_search("Netšajeva") == "Netsajeva"

    # Non-decomposable Latin characters
    def test_o_stroke(self):
        assert _normalize_for_search("MØ") == "MO"
        assert _normalize_for_search("Eivør") == "Eivor"
        assert _normalize_for_search("Kyrkjebø") == "Kyrkjebo"

    def test_ae_ligature(self):
        assert _normalize_for_search("Ænima") == "AEnima"
        assert _normalize_for_search("Lækker") == "Laekker"

    def test_sharp_s(self):
        assert _normalize_for_search("Straßenbande") == "Strassenbande"

    def test_eth(self):
        assert _normalize_for_search("Daði Freyr") == "Dadi Freyr"

    def test_l_stroke(self):
        assert _normalize_for_search("biały") == "bialy"

    def test_dotless_i(self):
        assert _normalize_for_search("Yalnız") == "Yalniz"

    # Combined: diacritics + non-decomposable in same string
    def test_mixed(self):
        assert _normalize_for_search("Millionär") == "Millionar"  # ä decomposes, no special chars
        assert _normalize_for_search("Süsser") == "Susser"  # ü decomposes

    # Edge cases
    def test_empty(self):
        assert _normalize_for_search("") == ""

    def test_none(self):
        assert _normalize_for_search(None) is None

    def test_ascii_passthrough(self):
        assert _normalize_for_search("Bon Jovi") == "Bon Jovi"


class TestNormalizationConsistency:
    """Verify the JS normalizeForSearch (built from LATIN_SPECIAL_MAP) matches Python."""

    def _js_normalize(self, text):
        """Pure-Python reimplementation of the JS normalizeForSearch logic.

        This mirrors exactly what the browser does:
        1. NFD decompose + strip combining marks (regex [\u0300-\u036f])
        2. Replace chars in LATIN_SPECIAL_MAP
        """
        import re
        s = unicodedata.normalize('NFD', text)
        s = re.sub(r'[\u0300-\u036f]', '', s)
        for char, replacement in LATIN_SPECIAL_MAP.items():
            s = s.replace(char, replacement)
        return s

    @pytest.mark.parametrize("text", [
        # Combining diacritics
        "Chance Peña", "Beyoncé", "Maxïmo Park", "Jürgens",
        "congrès", "Måneskin", "Netšajeva", "Barūn",
        # Non-decomposable Latin
        "MØ", "Eivør", "Ænima", "Lækker", "Straßenbande",
        "Daði Freyr", "biały", "Yalnız", "Kyrkjebø",
        # Mixed
        "187 Straßenbande - Millionär",
        # ASCII passthrough
        "Bon Jovi", "Queen",
        # Empty
        "",
    ])
    def test_js_matches_python(self, text):
        """JS implementation (from LATIN_SPECIAL_MAP) produces same output as Python."""
        assert self._js_normalize(text) == _normalize_for_search(text)

    def test_map_injected_to_template(self, flask_test_client):
        """LATIN_SPECIAL_MAP is rendered into the page via KJ_CONFIG."""
        import json, re
        response = flask_test_client.get('/')
        html = response.data.decode('utf-8')
        # Extract the JSON object from window.KJ_CONFIG = { latinSpecialMap: ... }
        match = re.search(r'latinSpecialMap:\s*({.*?})\s*[,\n]', html)
        assert match, "latinSpecialMap not found in rendered template"
        rendered_map = json.loads(match.group(1))
        assert rendered_map == LATIN_SPECIAL_MAP


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

    def test_build_skips_directories(self, mock_config, tmp_path):
        """Entries without file extensions (directories) are excluded."""
        file_list = tmp_path / "with_dirs.txt"
        file_list.write_text(
            "/media/Karaoke/Andrew The Nomad MP4/MP4\n"
            "/media/Karaoke/Andrew The Nomad MP4/CDG\n"
            "/media/Karaoke/NOMAD 0501-\n"
            "/media/Karaoke/NOMAD-0001-0099\n"
            "/media/Karaoke/SC2411-08 - Rascal Flatts - Life Is A Highway.zip\n"
            "/media/Karaoke/Queen - Bohemian Rhapsody.mp4\n"
        )
        catalog = ExternalCatalog(mock_config)
        count = catalog.build_from_file_list(str(file_list))
        assert count == 2
        stats = catalog.stats()
        assert 'unknown' not in stats['by_format']
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

    @pytest.mark.parametrize("ascii_query,expected_match", SEARCH_NORMALIZATION_CASES,
                             ids=[c[0] for c in SEARCH_NORMALIZATION_CASES])
    def test_search_normalization_real_catalog(self, mock_config, full_catalog_file_list,
                                               ascii_query, expected_match):
        """ASCII query finds entries with special characters in full catalog."""
        catalog = ExternalCatalog(mock_config)
        catalog.build_from_file_list(full_catalog_file_list)
        results = catalog.search(ascii_query)
        # Normalize to NFC for comparison — DB may store NFD form
        all_text = unicodedata.normalize('NFC', ' '.join(
            f"{r.get('artist', '')} {r.get('title', '')} {r.get('disc_id', '')}"
            for r in results
        ))
        expected_nfc = unicodedata.normalize('NFC', expected_match)
        assert expected_nfc in all_text, (
            f"Searching '{ascii_query}' should find '{expected_match}', "
            f"got {len(results)} results: {all_text[:200]}"
        )
        catalog.close()

    def test_full_catalog_builds_successfully(self, mock_config, full_catalog_file_list):
        """Full real-world catalog fixture builds without errors."""
        catalog = ExternalCatalog(mock_config)
        count = catalog.build_from_file_list(full_catalog_file_list)
        assert count == len(KARAOKE_CATALOG_ENTRIES)
        assert catalog.is_available() is True
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
