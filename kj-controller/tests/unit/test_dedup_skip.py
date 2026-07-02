import types

import routes
from media_library import MediaLibraryStore


def test_prospective_media_id_youtube():
    assert routes._prospective_media_id(
        "youtube", youtube_url="https://youtu.be/dQw4w9WgXcQ") == "yt-dQw4w9WgXcQ"


def test_prospective_media_id_divebar():
    assert routes._prospective_media_id(
        "divebar", file_id="drivefileid123", brand_code="WTF") == "db-WTF-drivefileid123"


def test_prospective_media_id_divebar_default_brand():
    assert routes._prospective_media_id(
        "divebar", file_id="fid", brand_code="") == "db-DB-fid"


def test_prospective_media_id_unknown_returns_none():
    assert routes._prospective_media_id("youtube", youtube_url="garbage") is None
    assert routes._prospective_media_id("divebar", file_id="") is None


def test_existing_media_requires_file_on_disk(tmp_path):
    store = MediaLibraryStore(":memory:")
    f = tmp_path / "x.mp4"
    f.write_bytes(b"0")
    store.upsert({"media_id": "yt-a", "source": "youtube", "file_path": str(f)})
    store.upsert({"media_id": "yt-gone", "source": "youtube",
                  "file_path": str(tmp_path / "missing.mp4")})
    app = types.SimpleNamespace(media_library=store)
    assert routes._existing_media_for(app, "yt-a")["media_id"] == "yt-a"
    assert routes._existing_media_for(app, "yt-gone") is None
    assert routes._existing_media_for(app, None) is None


def test_existing_media_no_store():
    app = types.SimpleNamespace()
    assert routes._existing_media_for(app, "yt-a") is None


def test_handle_download_dedupes_existing_youtube(mock_config, tmp_path):
    """POST /download for a video already on disk returns deduped and enqueues nothing."""
    from app import create_app
    cfg = dict(mock_config)
    cfg["media_db_path"] = str(tmp_path / "media_library.db")
    f = tmp_path / "have.mp4"
    f.write_bytes(b"0")
    flask_app = create_app(config=cfg)
    try:
        flask_app.media_library.upsert({
            "media_id": "yt-dQw4w9WgXcQ", "source": "youtube",
            "file_path": str(f), "artist": "Rick Astley",
            "title": "Never Gonna Give You Up",
        })
        client = flask_app.test_client()
        resp = client.post("/download",
                           json={"url": "https://youtu.be/dQw4w9WgXcQ"})
        assert resp.status_code == 200
        assert resp.get_json().get("deduped") is True
        assert flask_app.download_queue["items"] == []
    finally:
        flask_app.catalog.close()
