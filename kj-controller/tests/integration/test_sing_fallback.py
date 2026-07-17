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
