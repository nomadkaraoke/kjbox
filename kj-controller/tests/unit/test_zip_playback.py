"""Unit tests for ZipPlayback."""

import os
import zipfile

import pytest

from zip_playback import ZipPlayback


@pytest.fixture
def zip_playback(mock_config):
    """ZipPlayback instance for testing."""
    zp = ZipPlayback(mock_config)
    yield zp
    zp.cleanup()


@pytest.fixture
def cdg_zip(tmp_path):
    """Create a valid CDG+MP3 ZIP file."""
    zip_path = tmp_path / "test_song.zip"
    with zipfile.ZipFile(str(zip_path), 'w') as zf:
        zf.writestr("TEST001 - Artist - Song.cdg", b"fake cdg data")
        zf.writestr("TEST001 - Artist - Song.mp3", b"fake mp3 data")
    return str(zip_path)


@pytest.fixture
def mp3_only_zip(tmp_path):
    """Create a ZIP with no .cdg file."""
    zip_path = tmp_path / "no_cdg.zip"
    with zipfile.ZipFile(str(zip_path), 'w') as zf:
        zf.writestr("song.mp3", b"fake mp3 data")
    return str(zip_path)


class TestZipPlayback:
    def test_extract_and_get_cdg(self, zip_playback, cdg_zip):
        """Extracts ZIP and returns .cdg path."""
        cdg_path = zip_playback.extract_and_get_cdg(cdg_zip)
        assert cdg_path is not None
        assert cdg_path.endswith('.cdg')
        assert os.path.exists(cdg_path)
        # Verify matching .mp3 exists in same dir
        mp3_path = cdg_path.replace('.cdg', '.mp3')
        assert os.path.exists(mp3_path)

    def test_cleanup_removes_temp_dir(self, zip_playback, cdg_zip):
        """Cleanup removes the temp extraction directory."""
        cdg_path = zip_playback.extract_and_get_cdg(cdg_zip)
        temp_dir = zip_playback._temp_dir
        assert os.path.isdir(temp_dir)
        zip_playback.cleanup()
        assert not os.path.exists(temp_dir)
        assert zip_playback._temp_dir is None

    def test_no_cdg_in_zip(self, zip_playback, mp3_only_zip):
        """Returns None when ZIP has no .cdg file."""
        result = zip_playback.extract_and_get_cdg(mp3_only_zip)
        assert result is None

    def test_bad_zip_file(self, zip_playback, tmp_path):
        """Returns None for corrupt/invalid ZIP."""
        bad_zip = tmp_path / "bad.zip"
        bad_zip.write_text("this is not a zip file")
        result = zip_playback.extract_and_get_cdg(str(bad_zip))
        assert result is None

    def test_nonexistent_file(self, zip_playback):
        """Returns None for nonexistent file."""
        result = zip_playback.extract_and_get_cdg("/nonexistent/file.zip")
        assert result is None

    def test_path_traversal_blocked(self, zip_playback, tmp_path):
        """Blocks ZIP with path traversal entries."""
        zip_path = tmp_path / "evil.zip"
        with zipfile.ZipFile(str(zip_path), 'w') as zf:
            zf.writestr("../../../etc/passwd", b"evil")
            zf.writestr("song.cdg", b"fake cdg data")
        result = zip_playback.extract_and_get_cdg(str(zip_path))
        assert result is None

    def test_extract_cleans_previous(self, zip_playback, cdg_zip):
        """Second extraction cleans up first temp dir."""
        zip_playback.extract_and_get_cdg(cdg_zip)
        first_temp = zip_playback._temp_dir
        zip_playback.extract_and_get_cdg(cdg_zip)
        assert not os.path.exists(first_temp)

    def test_cleanup_when_no_temp(self, zip_playback):
        """Cleanup is safe when no extraction has happened."""
        zip_playback.cleanup()  # Should not raise
        assert zip_playback._temp_dir is None
