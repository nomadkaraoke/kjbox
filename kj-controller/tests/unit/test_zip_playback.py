"""Unit tests for ZipPlayback."""

import os
import shutil
import stat
import struct
import zipfile

import pytest

from zip_playback import ZipPlayback


def _make_deflate64_zip(zip_path, members):
    """Write a ZIP whose entries are flagged as Deflate64 (compression method 9).

    Python's ``zipfile`` cannot *write* method 9, so we deflate normally and then
    patch the compression-method field (8 -> 9) in every local file header and
    central-directory header. Using incompressible (random) payloads keeps the
    deflate stream free of length-258 matches, so it remains bit-identical under
    inflate64 and external tools like ``unzip`` decode it correctly — exactly the
    legacy "MP3+G Toolz .NET" CDG zips that Python rejects on the live box.
    """
    with zipfile.ZipFile(str(zip_path), 'w', zipfile.ZIP_DEFLATED) as zf:
        for name, payload in members.items():
            zf.writestr(name, payload)

    with open(str(zip_path), 'rb') as fh:
        data = bytearray(fh.read())

    with zipfile.ZipFile(str(zip_path)) as zf:
        infos = zf.infolist()
    for info in infos:
        off = info.header_offset
        assert data[off:off + 4] == b'PK\x03\x04'
        struct.pack_into('<H', data, off + 8, 9)  # local header: method -> 9

    eocd = data.rfind(b'PK\x05\x06')
    cd_offset = struct.unpack_from('<I', data, eocd + 16)[0]
    pos = cd_offset
    while data[pos:pos + 4] == b'PK\x01\x02':
        struct.pack_into('<H', data, pos + 10, 9)  # central dir: method -> 9
        fn_len = struct.unpack_from('<H', data, pos + 28)[0]
        ex_len = struct.unpack_from('<H', data, pos + 30)[0]
        cm_len = struct.unpack_from('<H', data, pos + 32)[0]
        pos += 46 + fn_len + ex_len + cm_len

    with open(str(zip_path), 'wb') as fh:
        fh.write(bytes(data))
    return str(zip_path)


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
def deflate64_zip(tmp_path):
    """Create a CDG+MP3 ZIP using Deflate64 (compression method 9).

    Returns (zip_path, expected_mp3_bytes) so tests can verify the extracted
    audio is byte-correct, not just present.
    """
    mp3_bytes = os.urandom(1200)
    zip_path = tmp_path / "deflate64_song.zip"
    _make_deflate64_zip(zip_path, {
        "KST012-11 - Artist - Song.cdg": os.urandom(800),
        "KST012-11 - Artist - Song.mp3": mp3_bytes,
    })
    return str(zip_path), mp3_bytes


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

    @pytest.mark.skipif(shutil.which('unzip') is None, reason="unzip not installed")
    def test_extract_deflate64_falls_back_to_unzip(self, zip_playback, deflate64_zip):
        """Deflate64 zips (unsupported by Python's zipfile) extract via unzip fallback."""
        zip_path, expected_mp3 = deflate64_zip
        # Sanity: Python's zipfile genuinely cannot extract this file.
        with zipfile.ZipFile(zip_path) as zf:
            assert all(i.compress_type == 9 for i in zf.infolist())

        mp3_path = zip_playback.extract_and_get_mp3(zip_path)

        assert mp3_path is not None
        assert mp3_path.endswith('.mp3')
        assert os.path.exists(mp3_path)
        # Bytes must be correct, not just present.
        with open(mp3_path, 'rb') as fh:
            assert fh.read() == expected_mp3
        # Matching .cdg extracted alongside for VLC overlay discovery.
        assert os.path.exists(mp3_path.replace('.mp3', '.cdg'))

    def test_deflate64_unzip_failure_returns_none(self, zip_playback, deflate64_zip, monkeypatch):
        """If the unzip fallback fails, extract returns None and cleans up temp dir."""
        import zip_playback as zp_module

        def boom(*_args, **_kwargs):
            raise FileNotFoundError("unzip not installed")

        monkeypatch.setattr(zp_module.subprocess, "run", boom)
        zip_path, _ = deflate64_zip
        result = zip_playback.extract_and_get_mp3(zip_path)
        assert result is None
        assert zip_playback._temp_dir is None  # temp dir cleaned up

    def test_deflate64_unzip_nonzero_returncode_returns_none(self, zip_playback, deflate64_zip, monkeypatch):
        """A non-zero unzip exit (e.g. CRC error) is treated as failure -> None."""
        import subprocess as _subprocess

        import zip_playback as zp_module

        def fake_run(*_args, **_kwargs):
            return _subprocess.CompletedProcess(args=_args, returncode=1, stdout=b"", stderr=b"crc error")

        monkeypatch.setattr(zp_module.subprocess, "run", fake_run)
        zip_path, _ = deflate64_zip
        result = zip_playback.extract_and_get_mp3(zip_path)
        assert result is None
        assert zip_playback._temp_dir is None

    @pytest.mark.skipif(shutil.which('unzip') is None, reason="unzip not installed")
    def test_deflate64_permissions_world_readable(self, zip_playback, deflate64_zip):
        """unzip-fallback extractions are world-readable for VLC, like the fast path."""
        zip_path, _ = deflate64_zip
        mp3_path = zip_playback.extract_and_get_mp3(zip_path)
        assert mp3_path is not None
        dir_stat = os.stat(zip_playback._temp_dir)
        assert dir_stat.st_mode & stat.S_IROTH
        assert dir_stat.st_mode & stat.S_IXOTH
        assert os.stat(mp3_path).st_mode & stat.S_IROTH

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
