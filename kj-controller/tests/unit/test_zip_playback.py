"""Unit tests for ZipPlayback."""

import os
import stat
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
def cdg_only_zip(tmp_path):
    """Create a ZIP with .cdg but no .mp3 file."""
    zip_path = tmp_path / "no_mp3.zip"
    with zipfile.ZipFile(str(zip_path), 'w') as zf:
        zf.writestr("song.cdg", b"fake cdg data")
    return str(zip_path)


class TestZipPlayback:
    def test_extract_and_get_mp3(self, zip_playback, cdg_zip):
        """Extracts ZIP and returns .mp3 path."""
        mp3_path = zip_playback.extract_and_get_mp3(cdg_zip)
        assert mp3_path is not None
        assert mp3_path.endswith('.mp3')
        assert os.path.exists(mp3_path)
        # Verify matching .cdg exists in same dir
        cdg_path = mp3_path.replace('.mp3', '.cdg')
        assert os.path.exists(cdg_path)

    def test_cleanup_removes_temp_dir(self, zip_playback, cdg_zip):
        """Cleanup removes the temp extraction directory."""
        zip_playback.extract_and_get_mp3(cdg_zip)
        temp_dir = zip_playback._temp_dir
        assert os.path.isdir(temp_dir)
        zip_playback.cleanup()
        assert not os.path.exists(temp_dir)
        assert zip_playback._temp_dir is None

    def test_no_mp3_in_zip(self, zip_playback, cdg_only_zip):
        """Returns None when ZIP has no .mp3 file."""
        result = zip_playback.extract_and_get_mp3(cdg_only_zip)
        assert result is None

    def test_bad_zip_file(self, zip_playback, tmp_path):
        """Returns None for corrupt/invalid ZIP."""
        bad_zip = tmp_path / "bad.zip"
        bad_zip.write_text("this is not a zip file")
        result = zip_playback.extract_and_get_mp3(str(bad_zip))
        assert result is None

    def test_nonexistent_file(self, zip_playback):
        """Returns None for nonexistent file."""
        result = zip_playback.extract_and_get_mp3("/nonexistent/file.zip")
        assert result is None

    def test_path_traversal_blocked(self, zip_playback, tmp_path):
        """Blocks ZIP with path traversal entries."""
        zip_path = tmp_path / "evil.zip"
        with zipfile.ZipFile(str(zip_path), 'w') as zf:
            zf.writestr("../../../etc/passwd", b"evil")
            zf.writestr("song.mp3", b"fake mp3 data")
        result = zip_playback.extract_and_get_mp3(str(zip_path))
        assert result is None

    def test_extract_cleans_previous(self, zip_playback, cdg_zip):
        """Second extraction cleans up first temp dir."""
        zip_playback.extract_and_get_mp3(cdg_zip)
        first_temp = zip_playback._temp_dir
        zip_playback.extract_and_get_mp3(cdg_zip)
        assert not os.path.exists(first_temp)

    def test_cleanup_when_no_temp(self, zip_playback):
        """Cleanup is safe when no extraction has happened."""
        zip_playback.cleanup()  # Should not raise
        assert zip_playback._temp_dir is None

    def test_permissions_world_readable(self, zip_playback, cdg_zip):
        """Extracted files are world-readable for VLC (runs as different user)."""
        mp3_path = zip_playback.extract_and_get_mp3(cdg_zip)
        temp_dir = zip_playback._temp_dir
        # Check temp dir is world-readable+executable
        dir_stat = os.stat(temp_dir)
        assert dir_stat.st_mode & stat.S_IROTH
        assert dir_stat.st_mode & stat.S_IXOTH
        # Check files are world-readable
        file_stat = os.stat(mp3_path)
        assert file_stat.st_mode & stat.S_IROTH
