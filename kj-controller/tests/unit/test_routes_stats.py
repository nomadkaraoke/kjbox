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
