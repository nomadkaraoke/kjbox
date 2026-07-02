"""Tests for the playability hard-gate on MediaIndex.download_video and
MediaIndex.download_from_url."""

import os
from unittest.mock import MagicMock, patch

import pytest

from media import MediaIndex


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _fake_ydl_info():
    return {
        'title': 'Test Song',
        'channel': 'TestChannel',
        'id': 'abc12345678',
        'duration': 180,
        'upload_date': '20240101',
    }


def _make_ytdlp_mock(mocker, info, download_folder, create_file=True):
    """Return a yt_dlp module mock whose extract_info(download=False) returns info
    and extract_info(download=True) creates the expected mp4 file on disk."""
    mock_instance = mocker.MagicMock()
    mock_instance.extract_info.return_value = info

    mock_class = mocker.MagicMock()
    mock_class.return_value.__enter__ = mocker.MagicMock(return_value=mock_instance)
    mock_class.return_value.__exit__ = mocker.MagicMock(return_value=False)

    mock_module = mocker.MagicMock()
    mock_module.YoutubeDL = mock_class

    mocker.patch.dict('sys.modules', {'yt_dlp': mock_module})

    if create_file:
        youtube_id = info.get('id', 'unknown')
        channel = info.get('channel', info.get('uploader', 'Unknown'))
        title = info.get('title', 'Unknown Title')
        fname = f"{youtube_id}__{channel}__{title}.mp4"
        dest = os.path.join(download_folder, fname)
        with open(dest, 'wb') as f:
            f.write(b'fake video data')

    return mock_module


def _good_verdict():
    mock = MagicMock()
    mock.verdict = {'overall_ok': True, 'reasons': []}
    return mock


def _bad_verdict(reason='truncated file'):
    mock = MagicMock()
    mock.verdict = {'overall_ok': False, 'reasons': [reason]}
    return mock


# ---------------------------------------------------------------------------
# download_video gate
# ---------------------------------------------------------------------------

class TestDownloadVideoGate:

    def test_rejected_returns_none_none(self, mock_config, tmp_media_dir, mocker):
        """Gate rejects: download_video returns (None, None)."""
        mi = MediaIndex(mock_config)
        download_dir = str(tmp_media_dir / "downloads")
        info = _fake_ydl_info()
        _make_ytdlp_mock(mocker, info, download_dir)

        with patch('media._gate_playable', return_value=_bad_verdict()):
            result = mi.download_video("https://youtube.com/watch?v=abc12345678")

        assert result == (None, None)

    def test_rejected_file_deleted(self, mock_config, tmp_media_dir, mocker):
        """Gate rejects: downloaded file is removed from disk."""
        mi = MediaIndex(mock_config)
        download_dir = str(tmp_media_dir / "downloads")
        info = _fake_ydl_info()
        _make_ytdlp_mock(mocker, info, download_dir)

        with patch('media._gate_playable', return_value=_bad_verdict()):
            mi.download_video("https://youtube.com/watch?v=abc12345678")

        remaining = os.listdir(download_dir)
        mp4s = [f for f in remaining if f.endswith('.mp4')]
        assert mp4s == [], f"Expected no mp4 files but found: {mp4s}"

    def test_rejected_not_in_index(self, mock_config, tmp_media_dir, mocker):
        """Gate rejects: file must NOT appear in self.index."""
        mi = MediaIndex(mock_config)
        download_dir = str(tmp_media_dir / "downloads")
        info = _fake_ydl_info()
        _make_ytdlp_mock(mocker, info, download_dir)

        with patch('media._gate_playable', return_value=_bad_verdict()):
            mi.download_video("https://youtube.com/watch?v=abc12345678")

        assert mi.index == {}

    def test_passing_returns_path_and_title(self, mock_config, tmp_media_dir, mocker):
        """Gate passes: download_video returns (real_path, title)."""
        mi = MediaIndex(mock_config)
        download_dir = str(tmp_media_dir / "downloads")
        info = _fake_ydl_info()
        _make_ytdlp_mock(mocker, info, download_dir)

        with patch('media._gate_playable', return_value=_good_verdict()):
            file_path, title = mi.download_video("https://youtube.com/watch?v=abc12345678")

        assert title == 'Test Song'
        assert file_path is not None
        assert 'abc12345678' in file_path

    def test_passing_added_to_index(self, mock_config, tmp_media_dir, mocker):
        """Gate passes: entry appears in self.index."""
        mi = MediaIndex(mock_config)
        download_dir = str(tmp_media_dir / "downloads")
        info = _fake_ydl_info()
        _make_ytdlp_mock(mocker, info, download_dir)

        with patch('media._gate_playable', return_value=_good_verdict()):
            file_path, _ = mi.download_video("https://youtube.com/watch?v=abc12345678")

        assert file_path in mi.index

    def test_passing_verdict_cached_in_entry(self, mock_config, tmp_media_dir, mocker):
        """Gate passes: verdict dict stored in index entry under 'playability'."""
        mi = MediaIndex(mock_config)
        download_dir = str(tmp_media_dir / "downloads")
        info = _fake_ydl_info()
        _make_ytdlp_mock(mocker, info, download_dir)

        good = _good_verdict()
        with patch('media._gate_playable', return_value=good):
            file_path, _ = mi.download_video("https://youtube.com/watch?v=abc12345678")

        assert mi.index[file_path]['playability'] == good.verdict

    def test_uses_run_ytdlp_download_method(self, mock_config, tmp_media_dir, mocker):
        """download_video delegates the actual yt-dlp call to _run_ytdlp_download."""
        mi = MediaIndex(mock_config)
        download_dir = str(tmp_media_dir / "downloads")
        info = _fake_ydl_info()

        # Mock Phase 1 (extract_info for metadata) via yt_dlp module
        _make_ytdlp_mock(mocker, info, download_dir, create_file=True)

        ran = []

        def fake_run(ydl_opts, url):
            ran.append(url)
            # File already created by _make_ytdlp_mock

        mocker.patch.object(mi, '_run_ytdlp_download', side_effect=fake_run)

        with patch('media._gate_playable', return_value=_good_verdict()):
            mi.download_video("https://youtube.com/watch?v=abc12345678")

        assert ran == ["https://youtube.com/watch?v=abc12345678"]


# ---------------------------------------------------------------------------
# download_from_url gate
# ---------------------------------------------------------------------------

class TestDownloadFromUrlGate:

    def _mock_http(self, mocker, mi, download_dir, filename, content=b'fake data'):
        """Patch mi._http_download to create a fake file and return a mock response."""
        def fake_http(url, file_path):
            if file_path is not None:
                with open(file_path, 'wb') as f:
                    f.write(content)
            mock_resp = MagicMock()
            mock_resp.headers = {}
            mock_resp.iter_content = lambda chunk_size=8192: iter([])
            return mock_resp

        mocker.patch.object(mi, '_http_download', side_effect=fake_http)

    def test_rejected_returns_none_none(self, mock_config, tmp_media_dir, mocker):
        mi = MediaIndex(mock_config)
        download_dir = str(tmp_media_dir / "downloads")
        self._mock_http(mocker, mi, download_dir, 'song.mp4')

        with patch('media._gate_playable', return_value=_bad_verdict()):
            result = mi.download_from_url("http://example.com/song.mp4", filename="song.mp4")

        assert result == (None, None)

    def test_rejected_file_deleted(self, mock_config, tmp_media_dir, mocker):
        mi = MediaIndex(mock_config)
        download_dir = str(tmp_media_dir / "downloads")
        self._mock_http(mocker, mi, download_dir, 'song.mp4')

        with patch('media._gate_playable', return_value=_bad_verdict()):
            mi.download_from_url("http://example.com/song.mp4", filename="song.mp4")

        mp4s = [f for f in os.listdir(download_dir) if f.endswith('.mp4')]
        assert mp4s == [], f"Expected no mp4 files but found: {mp4s}"

    def test_rejected_not_in_index(self, mock_config, tmp_media_dir, mocker):
        mi = MediaIndex(mock_config)
        download_dir = str(tmp_media_dir / "downloads")
        self._mock_http(mocker, mi, download_dir, 'song.mp4')

        with patch('media._gate_playable', return_value=_bad_verdict()):
            mi.download_from_url("http://example.com/song.mp4", filename="song.mp4")

        assert mi.index == {}

    def test_passing_returns_path_and_display_name(self, mock_config, tmp_media_dir, mocker):
        mi = MediaIndex(mock_config)
        download_dir = str(tmp_media_dir / "downloads")
        self._mock_http(mocker, mi, download_dir, 'song.mp4')

        with patch('media._gate_playable', return_value=_good_verdict()):
            file_path, display_name = mi.download_from_url(
                "http://example.com/song.mp4", filename="song.mp4"
            )

        assert file_path is not None
        assert display_name == 'song'

    def test_passing_added_to_index(self, mock_config, tmp_media_dir, mocker):
        mi = MediaIndex(mock_config)
        download_dir = str(tmp_media_dir / "downloads")
        self._mock_http(mocker, mi, download_dir, 'song.mp4')

        with patch('media._gate_playable', return_value=_good_verdict()):
            file_path, _ = mi.download_from_url(
                "http://example.com/song.mp4", filename="song.mp4"
            )

        assert file_path in mi.index

    def test_passing_verdict_cached_in_entry(self, mock_config, tmp_media_dir, mocker):
        mi = MediaIndex(mock_config)
        download_dir = str(tmp_media_dir / "downloads")
        self._mock_http(mocker, mi, download_dir, 'song.mp4')

        good = _good_verdict()
        with patch('media._gate_playable', return_value=good):
            file_path, _ = mi.download_from_url(
                "http://example.com/song.mp4", filename="song.mp4"
            )

        assert mi.index[file_path]['playability'] == good.verdict

    def test_http_download_receives_correct_file_path(self, mock_config, tmp_media_dir, mocker):
        """_http_download is called with the correct destination file_path."""
        mi = MediaIndex(mock_config)
        download_dir = str(tmp_media_dir / "downloads")

        captured_paths = []

        def fake_http(url, file_path):
            captured_paths.append(file_path)
            if file_path is not None:
                with open(file_path, 'wb') as f:
                    f.write(b'fake')
            mock_resp = MagicMock()
            mock_resp.headers = {}
            mock_resp.iter_content = lambda chunk_size=8192: iter([])
            return mock_resp

        mocker.patch.object(mi, '_http_download', side_effect=fake_http)

        with patch('media._gate_playable', return_value=_good_verdict()):
            mi.download_from_url("http://example.com/song.mp4", filename="song.mp4")

        assert len(captured_paths) == 1
        # Downloads now stage under a .staging__ name, then _finalize moves the
        # file into downloads/<source>/ with an Artist - Title [media_id] slug.
        assert captured_paths[0].endswith('.staging__song.mp4')


# ---------------------------------------------------------------------------
# download_cdg_pair — loose cdg + mp3 packaged into one playable zip
# ---------------------------------------------------------------------------

class TestDownloadCdgPair:
    """A loose (un-zipped) CDG track must be downloaded together with its sibling
    audio and packaged into a single cdg+mp3 .zip — never a bare, silent .cdg."""

    @staticmethod
    def _writing_http_download(url, file_path):
        # Mimic a real download by writing bytes to the destination path.
        with open(file_path, 'wb') as f:
            f.write(b'FAKE-' + url.encode('utf-8', 'ignore')[:8])
        return MagicMock()

    def test_packages_cdg_and_mp3_into_single_zip(self, mock_config, tmp_media_dir, mocker):
        import zipfile
        mi = MediaIndex(mock_config)
        download_dir = str(tmp_media_dir / "downloads")
        mocker.patch.object(MediaIndex, '_http_download', side_effect=self._writing_http_download)

        with patch('media._gate_playable', return_value=_good_verdict()):
            file_path, display = mi.download_cdg_pair(
                "http://gcs/cdg", "http://gcs/mp3", "SDK - ABBA - Dancing Queen.zip")

        assert file_path is not None
        assert file_path.endswith('.zip')
        assert os.path.exists(file_path)
        # display is now the canonical Artist - Title parsed from the name (the
        # brand code SDK is stripped); assert the song title survived.
        assert "Dancing Queen" in display
        with zipfile.ZipFile(file_path) as zf:
            names = zf.namelist()
        assert any(n.lower().endswith('.cdg') for n in names), names
        assert any(n.lower().endswith('.mp3') for n in names), names
        # Only the zip lands in the download folder — no loose cdg/mp3 left behind.
        leftovers = [f for f in os.listdir(download_dir)
                     if f.lower().endswith(('.cdg', '.mp3'))]
        assert leftovers == []

    def test_indexed_on_success(self, mock_config, tmp_media_dir, mocker):
        mi = MediaIndex(mock_config)
        mocker.patch.object(MediaIndex, '_http_download', side_effect=self._writing_http_download)
        with patch('media._gate_playable', return_value=_good_verdict()):
            file_path, _ = mi.download_cdg_pair(
                "http://gcs/cdg", "http://gcs/mp3", "SDK - ABBA - Dancing Queen.zip")
        assert os.path.realpath(file_path) in mi.index

    def test_gate_rejected_returns_none_and_not_indexed(self, mock_config, tmp_media_dir, mocker):
        mi = MediaIndex(mock_config)
        download_dir = str(tmp_media_dir / "downloads")
        mocker.patch.object(MediaIndex, '_http_download', side_effect=self._writing_http_download)
        with patch('media._gate_playable',
                   return_value=_bad_verdict("no audio track in CDG zip")):
            result = mi.download_cdg_pair(
                "http://gcs/cdg", "http://gcs/mp3", "SDK - ABBA - Dancing Queen.zip")
        assert result == (None, None)
        assert mi.index == {}
        # Rejected zip is quarantined, not left clickable in the download root.
        root_zips = [f for f in os.listdir(download_dir) if f.lower().endswith('.zip')]
        assert root_zips == []

    def test_download_failure_returns_none_no_partial(self, mock_config, tmp_media_dir, mocker):
        mi = MediaIndex(mock_config)
        download_dir = str(tmp_media_dir / "downloads")
        mocker.patch.object(MediaIndex, '_http_download',
                            side_effect=RuntimeError("network boom"))
        with patch('media._gate_playable', return_value=_good_verdict()):
            result = mi.download_cdg_pair(
                "http://gcs/cdg", "http://gcs/mp3", "SDK - ABBA - Dancing Queen.zip")
        assert result == (None, None)
        assert [f for f in os.listdir(download_dir) if f.lower().endswith('.zip')] == []
