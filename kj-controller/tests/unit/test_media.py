"""Tests for MediaIndex: scan, validate, list, delete."""

import json
import os

from media import MediaIndex, _ytdlp_base_opts


def test_validate_media_path_valid(mock_config, tmp_media_dir):
    mi = MediaIndex(mock_config)
    media_dir = tmp_media_dir / "media"
    test_file = media_dir / "song.mp4"
    test_file.write_text("fake video")

    result = mi.validate_path(str(test_file))
    assert result == os.path.realpath(str(test_file))


def test_validate_media_path_outside_media_folders(mock_config, tmp_path):
    mi = MediaIndex(mock_config)
    outside_file = tmp_path / "outside" / "song.mp4"
    outside_file.parent.mkdir()
    outside_file.write_text("fake video")

    result = mi.validate_path(str(outside_file))
    assert result is None


def test_validate_media_path_nonexistent_file(mock_config, tmp_media_dir):
    mi = MediaIndex(mock_config)
    result = mi.validate_path(str(tmp_media_dir / "media" / "missing.mp4"))
    assert result is None


def test_validate_media_path_symlink_traversal(mock_config, tmp_media_dir, tmp_path):
    """Symlink inside media folder pointing outside should be rejected."""
    mi = MediaIndex(mock_config)
    outside_file = tmp_path / "secret.txt"
    outside_file.write_text("secret data")

    symlink = tmp_media_dir / "media" / "sneaky.mp4"
    symlink.symlink_to(outside_file)

    result = mi.validate_path(str(symlink))
    assert result is None


def test_is_in_download_folder_true(mock_config, tmp_media_dir):
    mi = MediaIndex(mock_config)
    download_dir = tmp_media_dir / "downloads"
    test_file = download_dir / "video.mp4"
    test_file.write_text("fake video")

    result = mi.is_in_download_folder(str(test_file))
    assert result is True


def test_is_in_download_folder_false(mock_config, tmp_media_dir):
    mi = MediaIndex(mock_config)
    media_dir = tmp_media_dir / "media"
    test_file = media_dir / "video.mp4"
    test_file.write_text("fake video")

    result = mi.is_in_download_folder(str(test_file))
    assert result is False


def test_is_in_download_folder_subfolder(mock_config, tmp_media_dir):
    mi = MediaIndex(mock_config)
    subfolder = tmp_media_dir / "downloads" / "subdir"
    subfolder.mkdir()
    test_file = subfolder / "video.mp4"
    test_file.write_text("fake video")

    result = mi.is_in_download_folder(str(test_file))
    assert result is True


def test_scan_empty_folder(mock_config, tmp_media_dir):
    """Scan with no media files returns empty index."""
    mi = MediaIndex(mock_config)
    mi.scan()
    assert len(mi.index) == 0


def test_scan_finds_media_files(mock_config, tmp_media_dir):
    """Create files in media folders, verify they appear in the index."""
    mi = MediaIndex(mock_config)
    media_dir = tmp_media_dir / "media"
    (media_dir / "song1.mp4").write_text("fake1")
    (media_dir / "song2.mkv").write_text("fake2")
    (media_dir / "readme.txt").write_text("not media")

    mi.scan()
    assert len(mi.index) == 2
    paths = [entry["filename"] for entry in mi.index.values()]
    assert "song1.mp4" in paths
    assert "song2.mkv" in paths


def test_scan_skips_quarantine_dir(mock_config, tmp_media_dir):
    """Quarantined (rejected) downloads must never be re-indexed as playable."""
    import media
    mi = MediaIndex(mock_config)
    media_dir = tmp_media_dir / "media"
    (media_dir / "good.mp4").write_text("ok")
    qdir = media_dir / media.QUARANTINE_DIRNAME
    qdir.mkdir()
    (qdir / "rejected.mp4").write_text("bad")

    mi.scan()
    names = [entry["filename"] for entry in mi.index.values()]
    assert "good.mp4" in names
    assert "rejected.mp4" not in names


def test_scan_skips_preview_cache_dir(mock_config, tmp_media_dir):
    """Preview-cache artifacts must never be re-indexed, even if the cache is
    (mis)configured inside an indexed folder."""
    media_dir = tmp_media_dir / "media"
    (media_dir / "good.mp4").write_text("ok")
    pc = media_dir / ".preview-cache" / "cdg" / "abc"
    pc.mkdir(parents=True)
    (pc / "graphics.cdg").write_text("g")
    (pc / "audio.mp3").write_text("a")
    mock_config["preview_cache_dir"] = str(media_dir / ".preview-cache")

    mi = MediaIndex(mock_config)
    mi.scan()
    names = [entry["filename"] for entry in mi.index.values()]
    assert "good.mp4" in names
    assert "graphics.cdg" not in names
    assert "audio.mp3" not in names


def test_quarantine_download_moves_not_deletes(tmp_path):
    """A rejected download (and its yt-dlp sidecars) is moved aside, never
    deleted — an automated verdict can be wrong."""
    import media
    f = tmp_path / "song.mp4"; f.write_bytes(b"video")
    side = tmp_path / "song.webp"; side.write_bytes(b"thumb")

    q = media._quarantine_download(str(f), "moov atom not found", config={})

    assert q is not None
    assert not f.exists() and not side.exists()        # moved, not deleted
    qdir = tmp_path / media.QUARANTINE_DIRNAME
    assert (qdir / "song.mp4").is_file()
    assert (qdir / "song.webp").is_file()               # sidecar moved too
    assert (qdir / "song.mp4.reason.txt").is_file()     # reason recorded


def test_scan_preserves_metadata(mock_config, tmp_media_dir):
    """Existing index metadata (duration, upload_date) survives rescan."""
    mi = MediaIndex(mock_config)
    media_dir = tmp_media_dir / "media"
    test_file = media_dir / "song.mp4"
    test_file.write_text("fake video")
    real_path = os.path.realpath(str(test_file))

    # Pre-populate index file with metadata
    existing = {real_path: {"duration": 180, "upload_date": "20240101"}}
    index_path = mock_config["media_index_path"]
    with open(index_path, 'w') as f:
        json.dump(existing, f)

    mi.scan()
    assert mi.index[real_path]["duration"] == 180
    assert mi.index[real_path]["upload_date"] == "20240101"


def test_list_items_format(mock_config, tmp_media_dir):
    """Verify list_items() output shape."""
    mi = MediaIndex(mock_config)
    media_dir = tmp_media_dir / "media"
    (media_dir / "song.mp4").write_text("fake")
    mi.scan()

    items = mi.list_items()
    assert len(items) == 1
    item = items[0]
    assert "file_path" in item
    assert "display_name" in item
    assert "filename" in item
    assert "folder_name" in item
    assert "is_download" in item
    assert "mtime" in item
    assert "size" in item
    assert item["ext"] == ".mp4"
    assert item["media_kind"] == "mp4"


def test_list_items_type_labels_and_bare_cdg(mock_config, tmp_media_dir):
    """ext + media_kind per item; bare .cdg flagged cdg_no_audio unless paired."""
    mi = MediaIndex(mock_config)
    media_dir = tmp_media_dir / "media"
    (media_dir / "pack.zip").write_text("fake")
    (media_dir / "lonely.cdg").write_text("g")        # no sibling -> no audio
    (media_dir / "paired.cdg").write_text("g")
    (media_dir / "paired.mp3").write_text("a")        # sibling for paired.cdg
    mi.scan()

    by_name = {i["filename"]: i for i in mi.list_items()}
    assert by_name["pack.zip"]["media_kind"] == "cdg-zip"
    assert by_name["pack.zip"]["ext"] == ".zip"
    assert by_name["lonely.cdg"]["media_kind"] == "cdg"
    assert by_name["lonely.cdg"]["cdg_no_audio"] is True
    assert by_name["paired.cdg"]["cdg_no_audio"] is False
    # non-cdg items don't carry the flag
    assert "cdg_no_audio" not in by_name["paired.mp3"]


def test_delete_removes_file_and_sidecars(mock_config, tmp_media_dir):
    """Delete with sidecar cleanup."""
    mi = MediaIndex(mock_config)
    download_dir = tmp_media_dir / "downloads"
    main_file = download_dir / "video.mp4"
    sidecar_json = download_dir / "video.json"
    sidecar_webp = download_dir / "video.webp"
    main_file.write_text("fake video")
    sidecar_json.write_text("{}")
    sidecar_webp.write_text("fake image")

    real_path = os.path.realpath(str(main_file))
    mi.index[real_path] = {"path": real_path, "filename": "video.mp4"}

    mi.delete_file(real_path)

    assert not main_file.exists()
    assert not sidecar_json.exists()
    assert not sidecar_webp.exists()
    assert real_path not in mi.index


def test_load_from_file(mock_config, tmp_media_dir):
    """load() reads from existing index file instead of scanning."""
    mi = MediaIndex(mock_config)
    index_path = mock_config["media_index_path"]

    # Write a pre-existing index
    data = {"/fake/path.mp4": {"path": "/fake/path.mp4", "filename": "path.mp4"}}
    with open(index_path, 'w') as f:
        json.dump(data, f)

    mi.load()
    assert len(mi.index) == 1
    assert "/fake/path.mp4" in mi.index


def test_load_triggers_scan_when_no_file(mock_config, tmp_media_dir):
    """load() triggers scan when no index file exists."""
    mi = MediaIndex(mock_config)
    media_dir = tmp_media_dir / "media"
    (media_dir / "song.mp4").write_text("fake")

    mi.load()
    assert len(mi.index) == 1


def test_save_and_load_roundtrip(mock_config, tmp_media_dir):
    """Save index to disk and load it back."""
    mi1 = MediaIndex(mock_config)
    media_dir = tmp_media_dir / "media"
    (media_dir / "track.mp4").write_text("fake")
    mi1.scan()

    mi2 = MediaIndex(mock_config)
    mi2.load()
    assert len(mi2.index) == len(mi1.index)


def test_scan_skips_nonexistent_folder(mock_config, tmp_media_dir):
    """Scan skips media folders that don't exist."""
    mock_config["media_folders"].append("/nonexistent/folder")
    mi = MediaIndex(mock_config)
    mi.scan()
    # Should not crash, still works for valid folders
    assert isinstance(mi.index, dict)


def test_scan_handles_stat_error(mock_config, tmp_media_dir, mocker):
    """Scan skips files that raise OSError on stat."""
    mi = MediaIndex(mock_config)
    media_dir = tmp_media_dir / "media"
    (media_dir / "good.mp4").write_text("fake")
    (media_dir / "bad.mp4").write_text("fake")

    original_stat = os.stat
    def patched_stat(path, *args, **kwargs):
        if "bad.mp4" in str(path):
            raise OSError("Permission denied")
        return original_stat(path, *args, **kwargs)

    mocker.patch('media.os.stat', side_effect=patched_stat)
    mi.scan()
    assert len(mi.index) == 1
    filenames = [e["filename"] for e in mi.index.values()]
    assert "good.mp4" in filenames


def test_scan_parses_youtube_filename(mock_config, tmp_media_dir):
    """Scan correctly parses youtube-format filenames."""
    mi = MediaIndex(mock_config)
    media_dir = tmp_media_dir / "media"
    (media_dir / "dQw4w9WgXcQ__RickAstley__Never Gonna Give You Up.mp4").write_text("fake")

    mi.scan()
    assert len(mi.index) == 1
    entry = list(mi.index.values())[0]
    assert entry["youtube_id"] == "dQw4w9WgXcQ"
    assert entry["channel"] == "RickAstley"
    assert entry["title"] == "Never Gonna Give You Up"
    assert entry["display_name"] == "Never Gonna Give You Up"


def test_list_items_with_youtube_metadata(mock_config, tmp_media_dir):
    """list_items includes youtube fields when present."""
    mi = MediaIndex(mock_config)
    media_dir = tmp_media_dir / "media"
    (media_dir / "dQw4w9WgXcQ__RickAstley__Never Gonna.mp4").write_text("fake")
    mi.scan()

    # Add duration metadata
    path = list(mi.index.keys())[0]
    mi.index[path]["duration"] = 212

    items = mi.list_items()
    assert len(items) == 1
    item = items[0]
    assert item["channel"] == "RickAstley"
    assert item["youtube_id"] == "dQw4w9WgXcQ"
    assert item["duration"] == 212


def test_list_items_youtube_id_from_canonical_slug(tmp_path):
    """A canonical-slug download 'Artist - Title [yt-VID].ext' surfaces
    youtube_id=VID in list_items, even though the legacy __-format parser can't
    read it. The video id lives in the canonical media_id (yt-VID) since the
    v0.54.1 slug rename; the frontend keys its KN->local dedup on youtube_id, so
    without this the on-disk download never matches its Karaoke Nerds row and the
    KJ is offered a redundant re-download.
    """
    from media_library import MediaLibraryStore

    dl = str(tmp_path / "downloads")
    slug = os.path.join(dl, "youtube", "Bastille - Pompeii [yt-3QhmiCHjRHE].mp4")
    os.makedirs(os.path.dirname(slug), exist_ok=True)
    with open(slug, "wb") as f:
        f.write(b"\x00" * 16)

    store = MediaLibraryStore(":memory:")
    mi = MediaIndex({"media_folders": [dl], "download_folder": dl,
                     "media_index_path": str(tmp_path / "i.json")},
                    media_library=store)
    mi.scan()

    item = mi.list_items()[0]
    assert item["media_id"] == "yt-3QhmiCHjRHE"
    assert item["youtube_id"] == "3QhmiCHjRHE"


def test_list_items_youtube_id_prefers_legacy_parse(tmp_path):
    """When the legacy __-format parse already set youtube_id, the media_id
    derivation must not override it (parse wins; both agree anyway)."""
    from media_library import MediaLibraryStore

    dl = str(tmp_path / "downloads")
    legacy = os.path.join(dl, "youtube", "dQw4w9WgXcQ__RickAstley__Never Gonna.mp4")
    os.makedirs(os.path.dirname(legacy), exist_ok=True)
    with open(legacy, "wb") as f:
        f.write(b"\x00" * 16)

    store = MediaLibraryStore(":memory:")
    mi = MediaIndex({"media_folders": [dl], "download_folder": dl,
                     "media_index_path": str(tmp_path / "i.json")},
                    media_library=store)
    mi.scan()

    item = mi.list_items()[0]
    assert item["youtube_id"] == "dQw4w9WgXcQ"


def test_list_items_no_youtube_id_for_non_youtube(tmp_path):
    """Non-YouTube canonical media_ids (up-/nomad-/db-) must not gain a
    youtube_id — only yt- ids carry a real video id."""
    from media_library import MediaLibraryStore

    dl = str(tmp_path / "downloads")
    up = os.path.join(dl, "upload", "Some Artist - Some Song [up-deadbeef00].mp4")
    os.makedirs(os.path.dirname(up), exist_ok=True)
    with open(up, "wb") as f:
        f.write(b"\x00" * 16)

    store = MediaLibraryStore(":memory:")
    mi = MediaIndex({"media_folders": [dl], "download_folder": dl,
                     "media_index_path": str(tmp_path / "i.json")},
                    media_library=store)
    mi.scan()

    item = mi.list_items()[0]
    assert "youtube_id" not in item


def test_save_handles_write_error(mock_config, tmp_media_dir):
    """save() logs error when write fails."""
    mi = MediaIndex(mock_config)
    mi.index = {"key": "value"}
    mock_config["media_index_path"] = "/nonexistent/dir/index.json"
    mi.save()
    # Should not raise, just log error


def test_load_file_handles_corrupt_json(mock_config, tmp_media_dir):
    """_load_file returns empty dict on corrupt JSON."""
    mi = MediaIndex(mock_config)
    index_path = mock_config["media_index_path"]
    with open(index_path, 'w') as f:
        f.write("{corrupt json!!!")

    result = mi._load_file()
    assert result == {}


def test_delete_handles_sidecar_error(mock_config, tmp_media_dir, mocker):
    """delete_file handles errors when deleting sidecars."""
    mi = MediaIndex(mock_config)
    download_dir = tmp_media_dir / "downloads"
    main_file = download_dir / "video.mp4"
    sidecar = download_dir / "video.json"
    main_file.write_text("fake video")
    sidecar.write_text("{}")

    real_path = os.path.realpath(str(main_file))
    mi.index[real_path] = {"path": real_path, "filename": "video.mp4"}

    # Make sidecar removal fail
    original_remove = os.remove
    def patched_remove(path):
        if "video.json" in str(path):
            raise PermissionError("denied")
        return original_remove(path)

    mocker.patch('media.os.remove', side_effect=patched_remove)
    mi.delete_file(real_path)

    # Main file deleted, sidecar still exists due to error
    assert not main_file.exists()
    assert sidecar.exists()


def test_download_video_success(mock_config, tmp_media_dir, mocker):
    """download_video with mocked yt_dlp extracts info and downloads."""
    mi = MediaIndex(mock_config)
    download_dir = tmp_media_dir / "downloads"

    # Mock yt_dlp
    mock_ydl_instance = mocker.MagicMock()
    mock_ydl_instance.extract_info.return_value = {
        'title': 'Test Song',
        'channel': 'TestChannel',
        'id': 'abc12345678',
        'duration': 180,
        'upload_date': '20240101',
    }
    mock_ydl_class = mocker.MagicMock()
    mock_ydl_class.return_value.__enter__ = mocker.MagicMock(return_value=mock_ydl_instance)
    mock_ydl_class.return_value.__exit__ = mocker.MagicMock(return_value=False)

    mock_yt_dlp = mocker.MagicMock()
    mock_yt_dlp.YoutubeDL = mock_ydl_class
    mocker.patch.dict('sys.modules', {'yt_dlp': mock_yt_dlp})

    # Create the expected output file (simulating yt_dlp download)
    expected_file = download_dir / "abc12345678__TestChannel__Test Song.mp4"
    expected_file.write_text("fake video data")

    # Mock the playability gate so a fake file isn't rejected
    mock_gate = mocker.MagicMock()
    mock_gate.verdict = {'overall_ok': True, 'reasons': []}
    mocker.patch('media._gate_playable', return_value=mock_gate)

    file_path, title = mi.download_video("https://youtube.com/watch?v=abc12345678")
    assert title == "Test Song"
    assert file_path is not None
    assert "abc12345678" in file_path
    assert file_path in mi.index
    assert mi.index[file_path]["youtube_id"] == "abc12345678"
    assert mi.index[file_path]["duration"] == 180


def test_download_video_extract_error(mock_config, tmp_media_dir, mocker):
    """download_video returns None on extraction error."""
    mi = MediaIndex(mock_config)

    mock_ydl_instance = mocker.MagicMock()
    mock_ydl_instance.extract_info.side_effect = Exception("Network error")
    mock_ydl_class = mocker.MagicMock()
    mock_ydl_class.return_value.__enter__ = mocker.MagicMock(return_value=mock_ydl_instance)
    mock_ydl_class.return_value.__exit__ = mocker.MagicMock(return_value=False)

    mock_yt_dlp = mocker.MagicMock()
    mock_yt_dlp.YoutubeDL = mock_ydl_class
    mocker.patch.dict('sys.modules', {'yt_dlp': mock_yt_dlp})

    file_path, title = mi.download_video("https://youtube.com/watch?v=bad")
    assert file_path is None
    assert title is None


def test_download_video_file_not_found(mock_config, tmp_media_dir, mocker):
    """download_video returns None when downloaded file can't be located."""
    mi = MediaIndex(mock_config)

    mock_ydl_instance = mocker.MagicMock()
    mock_ydl_instance.extract_info.return_value = {
        'title': 'Test', 'channel': 'Ch', 'id': 'abc12345678',
    }
    mock_ydl_class = mocker.MagicMock()
    mock_ydl_class.return_value.__enter__ = mocker.MagicMock(return_value=mock_ydl_instance)
    mock_ydl_class.return_value.__exit__ = mocker.MagicMock(return_value=False)

    mock_yt_dlp = mocker.MagicMock()
    mock_yt_dlp.YoutubeDL = mock_ydl_class
    mocker.patch.dict('sys.modules', {'yt_dlp': mock_yt_dlp})

    # Don't create any file - simulating download failure
    file_path, title = mi.download_video("https://youtube.com/watch?v=abc12345678")
    assert file_path is None
    assert title is None


# --- _ytdlp_base_opts ---

def test_ytdlp_base_opts_defaults():
    """Base opts include anti-detection settings."""
    opts = _ytdlp_base_opts({})
    assert opts['retries'] == 3
    assert opts['fragment_retries'] == 3
    assert opts['extractor_retries'] == 3
    assert opts['sleep_interval'] == 1
    assert opts['max_sleep_interval'] == 5
    assert opts['sleep_interval_requests'] == 1
    assert opts['quiet'] is True
    assert opts['noplaylist'] is True
    assert 'cookiefile' not in opts


def test_ytdlp_base_opts_with_cookies(tmp_path):
    """Base opts include cookiefile when cookie file exists."""
    cookie_file = tmp_path / 'cookies.txt'
    cookie_file.write_text('cookie data')
    opts = _ytdlp_base_opts({'youtube_cookies_file': str(cookie_file)})
    assert opts['cookiefile'] == str(cookie_file)


def test_ytdlp_base_opts_missing_cookies():
    """Base opts skip cookiefile when file doesn't exist."""
    opts = _ytdlp_base_opts({'youtube_cookies_file': '/nonexistent/cookies.txt'})
    assert 'cookiefile' not in opts


def test_ytdlp_base_opts_empty_cookies_path():
    """Base opts skip cookiefile when path is empty string."""
    opts = _ytdlp_base_opts({'youtube_cookies_file': ''})
    assert 'cookiefile' not in opts
