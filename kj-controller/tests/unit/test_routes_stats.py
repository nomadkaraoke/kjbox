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
    assert routes._youtube_id("https://www.youtube.com/watch?v=dQw4w9WgXcQ") == "dQw4w9WgXcQ"
    assert routes._youtube_id("https://youtu.be/dQw4w9WgXcQ") == "dQw4w9WgXcQ"
    assert routes._youtube_id("not a url") is None


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
