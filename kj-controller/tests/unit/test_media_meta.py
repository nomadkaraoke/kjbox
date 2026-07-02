# kj-controller/tests/unit/test_media_meta.py
"""_add_media_meta: canonical Artist/Title decoration for linked rotation
entries — media_library first, external catalog fallback, never raises."""
import routes


class _ML:
    def __init__(self, rows):
        self.rows = rows

    def get_by_path(self, p):
        return self.rows.get(p)


class _Cat:
    def __init__(self, rows):
        self.rows = rows

    def get_by_path(self, p):
        return self.rows.get(p)


def test_media_meta_from_media_library(app_ctx):
    from flask import current_app
    current_app.media_library = _ML(
        {"/opt/nomad/downloads/youtube/x.mp4": {"artist": "ABBA", "title": "SOS"}})
    current_app.catalog = _Cat({})
    entries = [{"id": 1, "file_path": "/opt/nomad/downloads/youtube/x.mp4"},
               {"id": 2}]  # unlinked
    routes._add_media_meta(entries)
    assert entries[0]["media_meta"] == {"artist": "ABBA", "title": "SOS"}
    assert "media_meta" not in entries[1]


def test_media_meta_catalog_fallback_for_untouched_ssd(app_ctx):
    from flask import current_app
    p = "/media/nomad/Nomad4TBOne/Discs/SC1 - ABBA - SOS.zip"
    current_app.media_library = _ML({})
    current_app.catalog = _Cat({p: {"artist": "ABBA", "title": "SOS"}})
    entries = [{"id": 1, "file_path": p}]
    routes._add_media_meta(entries)
    assert entries[0]["media_meta"] == {"artist": "ABBA", "title": "SOS"}


def test_media_meta_blank_identity_and_errors_skipped(app_ctx):
    from flask import current_app

    class _Boom:
        def get_by_path(self, p):
            raise RuntimeError("db down")

    current_app.media_library = _ML({"/a.mp4": {"artist": "", "title": ""}})
    current_app.catalog = _Cat({})
    entries = [{"id": 1, "file_path": "/a.mp4"}]
    routes._add_media_meta(entries)
    assert "media_meta" not in entries[0]  # blank identity is not decoration
    current_app.media_library = _Boom()
    routes._add_media_meta(entries)       # must not raise


def test_decorate_rotation_entries_includes_media_meta(app_ctx, monkeypatch):
    from flask import current_app
    current_app.media_library = _ML({"/a.mp4": {"artist": "A", "title": "T"}})
    current_app.catalog = _Cat({})
    for name in ("_add_time_estimates", "_add_songs_sung", "_add_last_sang", "_add_sms_status"):
        monkeypatch.setattr(routes, name, lambda *a, **k: None)
    entries = [{"id": 1, "file_path": "/a.mp4"}]
    routes._decorate_rotation_entries(entries, rotation=None)
    assert entries[0]["media_meta"]["artist"] == "A"
