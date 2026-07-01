"""Regression test: the app factory must construct a MediaLibraryStore and
wire the SAME instance into MediaIndex, so identity lookups (media_id
derivation, dedup) are backed by one shared SQLite store rather than each
component silently getting its own (or none)."""

from app import create_app
from media_library import MediaLibraryStore


def test_create_app_wires_media_library_into_media_index(mock_config, tmp_path):
    cfg = dict(mock_config)
    cfg["media_db_path"] = str(tmp_path / "media_library.db")

    flask_app = create_app(config=cfg)
    try:
        assert isinstance(flask_app.media_library, MediaLibraryStore)
        assert flask_app.media.media_library is flask_app.media_library
    finally:
        flask_app.catalog.close()
