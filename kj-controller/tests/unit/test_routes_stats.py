import routes


class _FakeStats:
    def __init__(self):
        self.plays = []
    def record_play(self, media_id, **kw):
        self.plays.append((media_id, kw)); return True


class _FakeML:
    def __init__(self, by_path):
        self._by_path = by_path
    def get_by_path(self, p):
        return self._by_path.get(p)


class _FakeStore:
    def get_entry(self, eid):
        return {"id": eid, "singer": "Celeste", "song_artist": "ABBA - SOS"}


class _FakeRotation:
    store = _FakeStore()


def test_record_play_stat_resolves_and_records(app_ctx):
    # app_ctx: a pushed Flask app context with current_app.stats/media_library/rotation set
    from flask import current_app
    current_app.stats = _FakeStats()
    current_app.media_library = _FakeML(
        {"/opt/nomad/downloads/x.mp4": {"media_id": "yt-abc", "artist": "ABBA", "title": "SOS"}})
    current_app.rotation = _FakeRotation()
    routes._record_play_stat("/opt/nomad/downloads/x.mp4", 42)
    assert current_app.stats.plays[0][0] == "yt-abc"
    kw = current_app.stats.plays[0][1]
    assert kw["entry_id"] == 42 and kw["singer"] == "Celeste"


def test_record_play_stat_unresolved_is_noop(app_ctx):
    from flask import current_app
    current_app.stats = _FakeStats()
    current_app.media_library = _FakeML({})   # path not known, no [media_id] in name
    current_app.rotation = None
    routes._record_play_stat("/opt/nomad/downloads/plain name.mp4", None)
    assert current_app.stats.plays == []


def test_record_play_stat_swallows_store_errors(app_ctx):
    from flask import current_app

    class _BoomStats:
        def record_play(self, *a, **k):
            raise RuntimeError("db down")

    current_app.stats = _BoomStats()
    current_app.media_library = _FakeML(
        {"/opt/nomad/downloads/x.mp4": {"media_id": "yt-abc", "artist": "A", "title": "T"}})
    current_app.rotation = None
    # Must NOT raise — a stats failure can never break /play on a live rig.
    routes._record_play_stat("/opt/nomad/downloads/x.mp4", None)


def test_youtube_id_extraction():
    # routes._youtube_id was removed (deduped onto the shared naming.youtube_id_from_url,
    # imported into this module) — exercise it via the routes-module binding.
    assert routes.youtube_id_from_url("https://www.youtube.com/watch?v=dQw4w9WgXcQ") == "dQw4w9WgXcQ"
    assert routes.youtube_id_from_url("https://youtu.be/dQw4w9WgXcQ") == "dQw4w9WgXcQ"
    assert routes.youtube_id_from_url("not a url") is None


class _FakePreviewStats:
    def __init__(self):
        self.previews = []
    def record_preview(self, media_id, **kw):
        self.previews.append((media_id, kw)); return True


def test_record_preview_stat_youtube(app_ctx):
    from flask import current_app
    current_app.stats = _FakePreviewStats()
    routes._record_preview_stat(
        {"source": "youtube", "youtube_url": "https://youtu.be/dQw4w9WgXcQ",
         "title": "Rick Astley - Never Gonna Give You Up"})
    assert current_app.stats.previews[0][0] == "yt-dQw4w9WgXcQ"


def test_record_preview_stat_local(app_ctx):
    from flask import current_app
    current_app.stats = _FakePreviewStats()
    current_app.media_library = _FakeML(
        {"/opt/nomad/downloads/x.mp4": {"media_id": "gen-abcd1234",
                                        "artist": "A", "title": "T"}})
    routes._record_preview_stat({"source": "local", "file_path": "/opt/nomad/downloads/x.mp4"})
    assert current_app.stats.previews[0][0] == "gen-abcd1234"


def test_media_note_upsert_and_labels(flask_test_client):
    r = flask_test_client.post("/media/note", json={
        "media_id": "yt-abc", "note": "censored version", "label": "censored"})
    assert r.status_code == 200
    assert r.get_json()["note"]["note"] == "censored version"
    r2 = flask_test_client.get("/media/note-labels")
    assert "censored" in r2.get_json()["labels"]


def test_media_note_requires_media_id(flask_test_client):
    r = flask_test_client.post("/media/note", json={"note": "x"})
    assert r.status_code == 400


def test_stats_endpoints(flask_test_client):
    # Record on the SAME app the client serves, so the endpoint sees it.
    flask_test_client.application.stats.record_play(
        "yt-a", entry_id=101, singer="Celeste", artist="ABBA", title="SOS", song_key="abba sos")
    r = flask_test_client.get("/stats/top-songs?limit=5")
    assert r.status_code == 200
    assert r.get_json()["songs"][0]["song_key"] == "abba sos"
    r2 = flask_test_client.get("/stats/singers")
    assert any(s["singer"] == "Celeste" for s in r2.get_json()["singers"])
    r3 = flask_test_client.get("/stats/top-songs?singer=celeste")
    assert r3.get_json()["songs"][0]["song_key"] == "abba sos"


def test_resolve_row_media_id(app_ctx):
    from flask import current_app
    current_app.media_library = _FakeML({"/opt/nomad/downloads/x.mp4": {"media_id": "gen-abcd1234"}})
    ml = current_app.media_library
    assert routes.resolve_row_media_id({"path": "/opt/nomad/downloads/x.mp4"}, "local", ml) == "gen-abcd1234"
    assert routes.resolve_row_media_id({"youtube_url": "https://youtu.be/dQw4w9WgXcQ"}, "kn", ml) == "yt-dQw4w9WgXcQ"
    assert routes.resolve_row_media_id({"file_id": "F1", "brand": "FBK"}, "divebar", ml) == "db-FBK-F1"


def test_rotation_search_enriches_stats(flask_test_client, monkeypatch):
    app = flask_test_client.application
    app.stats.record_play("gen-abcd1234", entry_id=9001, artist="A", title="T", song_key="a t")
    app.media_library = _FakeML({"/opt/nomad/downloads/x.mp4": {"media_id": "gen-abcd1234"}})
    monkeypatch.setattr(routes, "unified_search", lambda q, a: {
        "local": [{"path": "/opt/nomad/downloads/x.mp4", "artist": "A", "title": "T"}],
        "karaoke_nerds": [], "divebar": [], "karaoke_nerds_timeout": False})
    r = flask_test_client.get("/rotation/search?q=abc")
    row = r.get_json()["local"][0]
    assert row["stats"]["plays"] == 1
    assert row["stats"]["is_usual"] is True
    assert row["media_id"] == "gen-abcd1234"


# --- SSD/library lazy materialization (design D3) ---
import routes as _routes_mod


class _FakeCatalogByPath:
    def __init__(self, rows):
        self.rows = rows
    def get_by_path(self, p):
        return self.rows.get(p)


def _library_setup(tmp_path, monkeypatch):
    """Real store + real file under a fake mount; run_async made synchronous."""
    import os as _os
    from flask import current_app
    from media_library import MediaLibraryStore
    mount = str(tmp_path / "ssd")
    p = str(tmp_path / "ssd" / "Discs" / "SC1 - ABBA - SOS.zip")
    _os.makedirs(_os.path.dirname(p), exist_ok=True)
    with open(p, "wb") as fh:
        fh.write(b"zipbytes")
    current_app.kj_config = {"external_media_mount": mount}
    current_app.media_library = MediaLibraryStore(":memory:")
    current_app.catalog = _FakeCatalogByPath(
        {p: {"artist": "ABBA", "title": "SOS", "disc_id": "SC1"}})
    monkeypatch.setattr(_routes_mod.library_media, "run_async",
                        lambda target, *a: target(*a))
    return p


def test_record_play_stat_materializes_library_row(app_ctx, tmp_path, monkeypatch):
    from flask import current_app
    p = _library_setup(tmp_path, monkeypatch)
    current_app.stats = _FakeStats()
    current_app.rotation = _FakeRotation()
    routes._record_play_stat(p, 42)
    mid, kw = current_app.stats.plays[0]
    assert mid.startswith("lib-")
    assert kw["entry_id"] == 42 and kw["singer"] == "Celeste"
    assert kw["artist"] == "ABBA" and kw["title"] == "SOS"
    row = current_app.media_library.get_by_path(p)
    assert row and row["source"] == "library"


def test_record_play_stat_non_library_unresolved_still_noop(app_ctx, tmp_path, monkeypatch):
    from flask import current_app
    _library_setup(tmp_path, monkeypatch)
    current_app.stats = _FakeStats()
    current_app.rotation = None
    routes._record_play_stat("/opt/nomad/downloads/unknown.mp4", None)
    assert current_app.stats.plays == []


def test_record_preview_stat_materializes_library_row(app_ctx, tmp_path, monkeypatch):
    from flask import current_app
    p = _library_setup(tmp_path, monkeypatch)
    current_app.stats = _FakePreviewStats()
    routes._record_preview_stat({"source": "local", "file_path": p})
    mid, kw = current_app.stats.previews[0]
    assert mid.startswith("lib-") and kw["artist"] == "ABBA"


def test_resolve_row_media_id_never_hashes_library_paths(app_ctx, tmp_path, monkeypatch):
    """Search enrichment must stay pure — an untouched SSD row resolves to None."""
    from flask import current_app
    p = _library_setup(tmp_path, monkeypatch)

    def boom(_):
        raise AssertionError("search enrichment must not hash")

    import library_media as _lm
    monkeypatch.setattr(_lm, "content_hash", boom)
    assert routes.resolve_row_media_id({"path": p}, "local", current_app.media_library) is None
    assert current_app.media_library.get_by_path(p) is None


# ===== Song Stats section: new /stats/* routes =====

def test_stats_overview_route(flask_test_client):
    flask_test_client.application.stats.record_play(
        "yt-a", entry_id=201, singer="Al", artist="ABBA", title="SOS", song_key="abba sos")
    r = flask_test_client.get("/stats/overview")
    assert r.status_code == 200
    assert r.get_json()["overview"]["total_plays"] == 1


def test_stats_artist_routes(flask_test_client):
    s = flask_test_client.application.stats
    s.record_play("yt-a", entry_id=301, singer="Al", artist="ABBA", title="SOS", song_key="abba sos")
    r = flask_test_client.get("/stats/top-artists")
    assert r.get_json()["artists"][0]["artist"] == "ABBA"
    r2 = flask_test_client.get("/stats/artist-songs?artist=ABBA")
    assert r2.get_json()["songs"][0]["song_key"] == "abba sos"


def test_stats_singer_drilldown_routes(flask_test_client):
    s = flask_test_client.application.stats
    s.record_play("yt-a", entry_id=401, singer="Celeste", artist="ABBA", title="SOS",
                  song_key="abba sos", night_date="2026-06-01")
    r = flask_test_client.get("/stats/singer-songs?singer=Celeste")
    assert r.get_json()["songs"][0]["song_key"] == "abba sos"
    r2 = flask_test_client.get("/stats/singer-song-history?singer=Celeste&song_key=abba sos")
    assert r2.get_json()["history"][0]["night_date"] == "2026-06-01"


def test_stats_song_history_route(flask_test_client):
    s = flask_test_client.application.stats
    s.record_play("yt-a", entry_id=501, singer="Al", song_key="abba sos", night_date="2026-06-01")
    r = flask_test_client.get("/stats/song-history?song_key=abba sos")
    assert r.get_json()["history"][0]["singer"] == "Al"


def test_stats_nights_routes(flask_test_client):
    s = flask_test_client.application.stats
    s.record_play("yt-a", entry_id=601, singer="Al", artist="ABBA", title="SOS",
                  song_key="abba sos", night_date="2026-06-01")
    r = flask_test_client.get("/stats/nights")
    assert r.get_json()["nights"][0]["night_date"] == "2026-06-01"
    r2 = flask_test_client.get("/stats/night-setlist?night_date=2026-06-01")
    assert r2.get_json()["setlist"][0]["song_key"] == "abba sos"


def test_stats_most_repeated_route(flask_test_client):
    s = flask_test_client.application.stats
    for eid in (701, 702):
        s.record_play("yt-a", entry_id=eid, singer="Celeste", artist="Gaga",
                      title="Bad Romance", song_key="gaga bad romance")
    r = flask_test_client.get("/stats/most-repeated")
    assert r.get_json()["repeated"][0]["singer"] == "Celeste"


def test_stats_routes_empty_when_no_store(flask_test_client):
    # When current_app has no stats store, every endpoint returns its empty shape.
    app = flask_test_client.application
    saved = app.stats
    try:
        app.stats = None
        assert flask_test_client.get("/stats/overview").get_json() == {"overview": {}}
        assert flask_test_client.get("/stats/top-artists").get_json() == {"artists": []}
        assert flask_test_client.get("/stats/artist-songs?artist=x").get_json() == {"songs": []}
        assert flask_test_client.get("/stats/singer-songs?singer=x").get_json() == {"songs": []}
        assert flask_test_client.get("/stats/singer-song-history?singer=x&song_key=y").get_json() == {"history": []}
        assert flask_test_client.get("/stats/song-history?song_key=y").get_json() == {"history": []}
        assert flask_test_client.get("/stats/nights").get_json() == {"nights": []}
        assert flask_test_client.get("/stats/night-setlist?night_date=2026-06-01").get_json() == {"setlist": []}
        assert flask_test_client.get("/stats/most-repeated").get_json() == {"repeated": []}
    finally:
        app.stats = saved


def test_stats_routes_clamp_bad_limit(flask_test_client):
    # A non-numeric limit falls back to the default instead of erroring.
    flask_test_client.application.stats.record_play(
        "yt-a", entry_id=801, singer="Al", artist="ABBA", title="SOS", song_key="abba sos")
    r = flask_test_client.get("/stats/top-artists?limit=notanumber")
    assert r.status_code == 200
    assert r.get_json()["artists"][0]["artist"] == "ABBA"
