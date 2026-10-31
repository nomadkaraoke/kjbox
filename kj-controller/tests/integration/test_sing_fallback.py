"""Integration tests for auto-fallback in the download worker.

Drives ``_download_worker`` directly with a crafted sing-request-backed queue
item and a mocked ``download_video`` that fails on chosen URLs (setting
``media._last_error`` the way the real one does). Covers the three behaviours
that keep singers from mismanaged expectations: advance on unavailable, retry
on transient, and terminal ❌ only when nothing is playable.
"""

import time

import routes
import sing_resolve
from routes import _download_worker, approve_sing_request


def _make_request(app, source_ref, versions=None):
    return app.sing_store.create_request(
        singer_name="Tester", phone="", song_artist="Beetlejuice",
        song_title="Say My Name", source_type="youtube",
        source_ref=source_ref, source_meta={"versions": versions or []},
    )


def _queue_item(req, candidates, url):
    return {
        "id": "dl1", "url": url, "title": "Say My Name", "source": "youtube",
        "source_detail": None, "status": "queued", "error": None,
        "rotation_entry_id": None,  # keep rotation out of the fallback assertions
        "request_id": req["id"], "candidates": candidates,
        "candidate_index": 0, "transient_attempts": 0,
    }


def test_fallback_advances_to_next_candidate_on_unavailable(flask_app, mocker):
    """A private-video first pick auto-resolves to the next working candidate."""
    app = flask_app
    req = _make_request(app, "https://youtu.be/DEAD")
    candidates = [
        {"url": "https://youtu.be/DEAD", "source_type": "youtube", "source_meta": None},
        {"url": "https://youtu.be/GOOD", "source_type": "youtube",
         "source_meta": {"brand_code": "KV"}},
    ]

    def fake_dl(url):
        if "DEAD" in url:
            app.media._last_error = "ERROR: [youtube] x: Private video"
            return (None, None)
        app.media._last_error = None
        return ("/videos/good.mp4", "Good KV")

    mocker.patch.object(app.media, "download_video", side_effect=fake_dl)

    item = _queue_item(req, candidates, "https://youtu.be/DEAD")
    app.download_queue["items"] = [item]
    _download_worker(app)

    assert item["status"] == "completed"
    assert item["candidate_index"] == 1
    assert item["url"] == "https://youtu.be/GOOD"
    # The request is rebound so /my-requests reflects the version that landed.
    updated = app.sing_store.get_request(req["id"])
    assert updated["source_ref"] == "https://youtu.be/GOOD"


def test_fallback_terminal_when_all_candidates_unavailable(flask_app, mocker):
    """When every candidate is a dead video, the item ends in a terminal error."""
    app = flask_app
    req = _make_request(app, "https://youtu.be/DEAD1")
    candidates = [
        {"url": "https://youtu.be/DEAD1", "source_type": "youtube", "source_meta": None},
        {"url": "https://youtu.be/DEAD2", "source_type": "youtube", "source_meta": None},
    ]

    def fake_dl(url):
        app.media._last_error = "Video unavailable. This video has been removed"
        return (None, None)

    mocker.patch.object(app.media, "download_video", side_effect=fake_dl)

    item = _queue_item(req, candidates, "https://youtu.be/DEAD1")
    app.download_queue["items"] = [item]
    _download_worker(app)

    assert item["status"] == "error"
    # Advanced through both candidates before giving up.
    assert item["candidate_index"] == 1


def test_transient_error_retries_same_candidate(flask_app, mocker):
    """A transient blip retries the SAME candidate and never consumes the list."""
    app = flask_app
    req = _make_request(app, "https://youtu.be/ONLY")
    candidates = [
        {"url": "https://youtu.be/ONLY", "source_type": "youtube", "source_meta": None},
    ]
    calls = []

    def fake_dl(url):
        calls.append(url)
        app.media._last_error = "ERROR: Unable to download webpage: read operation timed out"
        return (None, None)

    mocker.patch.object(app.media, "download_video", side_effect=fake_dl)

    item = _queue_item(req, candidates, "https://youtu.be/ONLY")
    app.download_queue["items"] = [item]
    _download_worker(app)

    # Same URL retried MAX_TRANSIENT_RETRIES times, then a final terminal attempt.
    assert calls == ["https://youtu.be/ONLY"] * (sing_resolve.MAX_TRANSIENT_RETRIES + 1)
    assert item["candidate_index"] == 0  # never advanced — only one candidate
    assert item["status"] == "error"


def test_build_candidates_extracts_and_dedups_youtube(flask_app):
    """Candidate builder ranks YT versions, keeps current first, dedups, skips non-YT."""
    app = flask_app
    versions = [
        {"source": "kn", "kn": {"youtube_url": "https://youtu.be/A", "brand_code": "KV"}},
        {"source": "kn", "kn": {"youtube_url": "https://youtu.be/DEAD", "brand_code": "X"}},
        {"source": "local", "local": {"path": "/media/song.mp4"}},
    ]
    req = {"id": 1, "source_meta": {"versions": versions}}

    cands = routes._build_sing_fallback_candidates(
        req, "https://youtu.be/DEAD", None, app.kj_config)
    urls = [c["url"] for c in cands]

    assert urls[0] == "https://youtu.be/DEAD"          # current attempt first
    assert "https://youtu.be/A" in urls                # other YT candidate included
    assert "/media/song.mp4" not in urls               # local skipped (YT-only in v1)
    assert urls.count("https://youtu.be/DEAD") == 1    # deduped against current


def test_build_candidates_dedups_across_url_forms(flask_app):
    """The same video in youtu.be vs watch?v= form must not both become candidates."""
    app = flask_app
    versions = [
        # Same video id as the current pick, different URL form → must dedup out.
        {"source": "kn", "kn": {"youtube_url": "https://youtu.be/DEADvideo11"}},
        {"source": "kn", "kn": {"youtube_url": "https://www.youtube.com/watch?v=GOODvideo22"}},
    ]
    req = {"id": 1, "source_meta": {"versions": versions}}

    cands = routes._build_sing_fallback_candidates(
        req, "https://www.youtube.com/watch?v=DEADvideo11", None, app.kj_config)
    urls = [c["url"] for c in cands]

    assert urls[0] == "https://www.youtube.com/watch?v=DEADvideo11"  # current first
    # The youtu.be form of the same video is deduped away; only the good alt remains.
    assert not any("DEADvideo11" in u for u in urls[1:])
    assert "https://www.youtube.com/watch?v=GOODvideo22" in urls


def test_preserve_versions_meta_keeps_snapshot():
    """Binding a kj_pick version must not drop the versions snapshot."""
    versions = [{"source": "kn", "kn": {"youtube_url": "https://youtu.be/A"}}]
    req = {"source_meta": {"versions": versions}}

    merged = routes._preserve_versions_meta(req, {"brand_code": "KV"})
    assert merged["brand_code"] == "KV"
    assert merged["versions"] == versions

    # No snapshot present → source_meta returned unchanged (including None).
    assert routes._preserve_versions_meta({"source_meta": {}}, {"x": 1}) == {"x": 1}
    assert routes._preserve_versions_meta({"source_meta": None}, None) is None


def test_incident_reproduction_approve_then_autofallback(flask_app, mocker):
    """End-to-end: a multi-version approval whose best pick is private auto-heals.

    Mirrors the 2026-07-09 live incident — first YouTube version is a private
    video, a KaraFun alternate exists — and asserts the singer ends up linked to
    the working version with no manual KJ intervention.
    """
    app = flask_app
    versions = [
        {"source": "kn", "kn": {"youtube_url": "https://youtu.be/DEAD", "brand_code": "KV"}},
        {"source": "kn", "kn": {"youtube_url": "https://youtu.be/GOOD", "brand_code": "KV"}},
    ]
    req = app.sing_store.create_request(
        singer_name="Lulu", phone="", song_artist="Beetlejuice",
        song_title="Say My Name", source_type="youtube",
        source_ref="https://youtu.be/DEAD", source_meta={"versions": versions},
    )

    def fake_dl(url):
        if "DEAD" in url:
            app.media._last_error = "ERROR: [youtube] x: Private video"
            return (None, None)
        app.media._last_error = None
        return ("/videos/good.mp4", "Good KV")

    mocker.patch.object(app.media, "download_video", side_effect=fake_dl)

    approve_sing_request(app, req)  # enqueues + auto-starts the worker thread

    items = []
    for _ in range(200):
        with app._download_lock:
            items = [i for i in app.download_queue["items"] if i.get("request_id") == req["id"]]
            if items and items[0]["status"] in ("completed", "error"):
                break
        time.sleep(0.02)

    assert items and items[0]["status"] == "completed"
    assert items[0]["url"] == "https://youtu.be/GOOD"
    updated = app.sing_store.get_request(req["id"])
    assert updated["source_ref"] == "https://youtu.be/GOOD"


# --- YouTube breaker + cross-source fallback ------------------------------

def _local_and_yt_versions(local_path):
    return [
        {"source": "kn", "kn": {"youtube_url": "https://youtu.be/ONLY", "brand_code": "KV"}},
        {"source": "local", "local": {"path": local_path}},
    ]


def test_transient_failure_trips_breaker_and_swaps_to_local(flask_app, mocker, tmp_path):
    """yt-dlp failing after retries → breaker opens, singer gets the on-box version."""
    app = flask_app
    local = tmp_path / "say-my-name.mp4"
    local.write_bytes(b"x")
    req = _make_request(app, "https://youtu.be/ONLY", _local_and_yt_versions(str(local)))
    calls = []

    def fake_dl(url):
        calls.append(url)
        app.media._last_error = "ERROR: Sign in to confirm you're not a bot"
        return (None, None)

    mocker.patch.object(app.media, "download_video", side_effect=fake_dl)
    item = _queue_item(req, [{"url": "https://youtu.be/ONLY", "source_type": "youtube",
                              "source_meta": None}], "https://youtu.be/ONLY")
    app.download_queue["items"] = [item]
    _download_worker(app)

    assert len(calls) == sing_resolve.MAX_TRANSIENT_RETRIES + 1  # tried + retried
    assert app.youtube_breaker.is_open()
    assert app.youtube_breaker.status()["probe_url"] == "https://youtu.be/ONLY"
    assert item["status"] == "completed"
    assert item["file_path"] == str(local)
    updated = app.sing_store.get_request(req["id"])
    assert updated["source_type"] == "local"
    assert updated["source_ref"] == str(local)


def test_unavailable_video_does_not_trip_breaker(flask_app, mocker):
    """A private video is that video's problem, not yt-dlp's."""
    app = flask_app
    req = _make_request(app, "https://youtu.be/DEAD")

    def fake_dl(url):
        app.media._last_error = "ERROR: [youtube] x: Private video"
        return (None, None)

    mocker.patch.object(app.media, "download_video", side_effect=fake_dl)
    item = _queue_item(req, [{"url": "https://youtu.be/DEAD", "source_type": "youtube",
                              "source_meta": None}], "https://youtu.be/DEAD")
    app.download_queue["items"] = [item]
    _download_worker(app)

    assert item["status"] == "error"
    assert not app.youtube_breaker.is_open()


def test_open_breaker_skips_youtube_and_downloads_divebar(flask_app, mocker):
    """While YouTube is down a pick with a Divebar version never touches yt-dlp."""
    app = flask_app
    app.youtube_breaker.trip("broken", "https://youtu.be/OLD")
    versions = [
        {"source": "kn", "kn": {"youtube_url": "https://youtu.be/ONLY", "brand_code": "KV"}},
        {"source": "kn", "kn": {"brand_code": "CC", "divebar": {"file_id": "F9"}}},
    ]
    req = _make_request(app, "https://youtu.be/ONLY", versions)
    yt = mocker.patch.object(app.media, "download_video")
    mocker.patch.object(routes, "_resolve_divebar_spec", return_value=({
        "pair": False, "url": "https://drive/F9", "title": "cc.zip", "source": "divebar",
        "source_detail": None, "divebar_file_id": "F9", "brand_code": "CC",
        "song_artist": "Beetlejuice", "song_title": "Say My Name"}, None))
    dl = mocker.patch.object(app.media, "download_from_url",
                             return_value=("/videos/cc.zip", "cc.zip"))
    item = _queue_item(req, [{"url": "https://youtu.be/ONLY", "source_type": "youtube",
                              "source_meta": None}], "https://youtu.be/ONLY")
    app.download_queue["items"] = [item]
    _download_worker(app)

    yt.assert_not_called()
    dl.assert_called_once()
    assert item["status"] == "completed"
    assert item["source"] == "divebar"
    assert app.sing_store.get_request(req["id"])["source_type"] == "divebar"


def test_youtube_success_closes_breaker(flask_app, mocker):
    app = flask_app
    app.youtube_breaker.trip("broken", "https://youtu.be/OLD")
    req = _make_request(app, "https://youtu.be/GOOD")  # no other source → still tried
    mocker.patch.object(app.media, "download_video", return_value=("/videos/g.mp4", "G"))
    item = _queue_item(req, [{"url": "https://youtu.be/GOOD", "source_type": "youtube",
                              "source_meta": None}], "https://youtu.be/GOOD")
    app.download_queue["items"] = [item]
    _download_worker(app)

    assert item["status"] == "completed"
    assert not app.youtube_breaker.is_open()


def test_kj_pick_prefers_non_youtube_while_breaker_open(flask_app, tmp_path):
    app = flask_app
    local = tmp_path / "a.mp4"
    local.write_bytes(b"x")
    versions = [
        {"source": "kn", "kn": {"youtube_url": "https://youtu.be/BEST", "brand_code": "CC",
                                "is_community": True}},
        {"source": "local", "local": {"path": str(local)}},
    ]
    req = app.sing_store.create_request(
        singer_name="T", phone="", song_artist="A", song_title="B",
        source_type="kj_pick", source_ref=None, source_meta={"versions": versions})

    bound = routes.resolve_kj_pick_best(app, req, app.kj_config)
    assert bound["source_type"] == "youtube"  # breaker closed → best-ranked wins

    app.youtube_breaker.trip("broken", "https://youtu.be/X")
    req2 = app.sing_store.create_request(
        singer_name="T2", phone="", song_artist="A", song_title="B",
        source_type="kj_pick", source_ref=None, source_meta={"versions": versions})
    bound2 = routes.resolve_kj_pick_best(app, req2, app.kj_config)
    assert bound2["source_type"] == "local"


def test_cross_source_error_never_stops_the_worker(flask_app, mocker):
    """A crash while swapping sources fails closed; later downloads still run."""
    app = flask_app
    app.youtube_breaker.trip("broken", "https://youtu.be/OLD")
    versions = [
        {"source": "kn", "kn": {"youtube_url": "https://youtu.be/ONLY", "brand_code": "KV"}},
        {"source": "kn", "kn": {"brand_code": "CC", "divebar": {"file_id": "F9"}}},
    ]
    req = _make_request(app, "https://youtu.be/ONLY", versions)
    mocker.patch.object(routes, "_resolve_divebar_spec", side_effect=RuntimeError("boom"))
    mocker.patch.object(app.media, "download_video", return_value=("/videos/y.mp4", "Y"))
    item = _queue_item(req, [{"url": "https://youtu.be/ONLY", "source_type": "youtube",
                              "source_meta": None}], "https://youtu.be/ONLY")
    later = {"id": "dl2", "url": "https://youtu.be/NEXT", "title": "Next", "source": "youtube",
             "status": "queued", "error": None, "rotation_entry_id": None}
    app.download_queue["items"] = [item, later]
    _download_worker(app)

    assert item["status"] == "completed"   # fell back to trying YouTube itself
    assert later["status"] == "completed"
    assert app.download_queue["worker_running"] is False


def test_advance_keeps_versions_snapshot_for_a_later_swap(flask_app, mocker, tmp_path):
    """Unavailable → advance → transient trip still finds the local version."""
    app = flask_app
    local = tmp_path / "a.mp4"
    local.write_bytes(b"x")
    versions = [
        {"source": "kn", "kn": {"youtube_url": "https://youtu.be/DEAD", "brand_code": "KV"}},
        {"source": "kn", "kn": {"youtube_url": "https://youtu.be/FLAKY", "brand_code": "KV"}},
        {"source": "local", "local": {"path": str(local)}},
    ]
    req = _make_request(app, "https://youtu.be/DEAD", versions)

    def fake_dl(url):
        app.media._last_error = ("ERROR: [youtube] x: Private video" if "DEAD" in url
                                 else "Sign in to confirm you're not a bot")
        return (None, None)

    mocker.patch.object(app.media, "download_video", side_effect=fake_dl)
    item = _queue_item(req, routes._build_sing_fallback_candidates(
        app.sing_store.get_request(req["id"]), "https://youtu.be/DEAD", None, app.kj_config),
        "https://youtu.be/DEAD")
    app.download_queue["items"] = [item]
    _download_worker(app)

    assert app.youtube_breaker.is_open()
    assert item["status"] == "completed" and item["file_path"] == str(local)
