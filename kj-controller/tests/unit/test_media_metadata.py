import os

from media import MediaIndex
from media_library import MediaLibraryStore


def _touch(p):
    with open(p, "wb") as f:
        f.write(b"\x00" * 16)


def test_list_items_joins_media_library_canonical(tmp_path):
    dl = tmp_path / "downloads"
    dl.mkdir()
    _touch(dl / "-UM1XiyBmhM__Sing King__Bella Kay - iloveit (Karaoke Version).mp4")
    store = MediaLibraryStore(":memory:")
    cfg = {"media_folders": [str(dl)], "download_folder": str(dl),
           "media_index_path": str(tmp_path / "i.json")}
    mi = MediaIndex(cfg, media_library=store)
    mi.scan()
    # Give the scanned row a clean canonical identity.
    yt = store.list_records(source="youtube")[0]
    store.set_metadata(yt["media_id"], "Bella Kay", "iloveit")

    items = mi.list_items()
    item = next(i for i in items if i.get("media_id") == yt["media_id"])
    assert item["artist"] == "Bella Kay"
    assert item["title"] == "iloveit"
    assert item["display_name"] == "Bella Kay - iloveit"  # canonical, not raw filename
    assert item["source"] == "youtube"
    assert item["needs_review"] == 0  # cleared by set_metadata


def test_list_items_marks_needs_review_from_library(tmp_path):
    dl = tmp_path / "downloads"
    dl.mkdir()
    _touch(dl / "-UM1XiyBmhM__Unknown__mystery title.mp4")
    store = MediaLibraryStore(":memory:")
    cfg = {"media_folders": [str(dl)], "download_folder": str(dl),
           "media_index_path": str(tmp_path / "i.json")}
    mi = MediaIndex(cfg, media_library=store)
    mi.scan()
    item = next(i for i in mi.list_items() if i.get("media_id", "").startswith("yt-"))
    assert item["needs_review"] == 1  # deterministic YT parse flags review


def test_media_metadata_route(mock_config, tmp_path):
    from app import create_app
    cfg = dict(mock_config)
    cfg["media_db_path"] = str(tmp_path / "media_library.db")
    flask_app = create_app(config=cfg)
    try:
        flask_app.media_library.upsert({
            "media_id": "yt-abc", "source": "youtube", "artist": "x", "title": "y",
            "needs_review": 1, "confidence": 0.4})
        client = flask_app.test_client()

        resp = client.post("/media/metadata",
                           json={"media_id": "yt-abc", "artist": "Queen", "title": "Bohemian Rhapsody"})
        assert resp.status_code == 200
        rec = resp.get_json()["record"]
        assert (rec["artist"], rec["title"]) == ("Queen", "Bohemian Rhapsody")
        assert rec["needs_review"] == 0 and rec["parse_method"] == "manual"

        assert client.post("/media/metadata", json={"artist": "a"}).status_code == 400
        assert client.post("/media/metadata",
                           json={"media_id": "nope", "artist": "a"}).status_code == 404
    finally:
        flask_app.catalog.close()
