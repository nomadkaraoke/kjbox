"""KJ routes for adding lyrics to a quick version gen made without any.

``GET /rotation/quick-lyrics/search`` and ``POST /rotation/quick-lyrics``; the
render itself is covered in tests/unit/test_quick_lyrics.py.
"""

from unittest.mock import patch

import pytest

import quick_lyrics

JOB = "job-q"


@pytest.fixture
def quick_entry(sing_app, tmp_path):
    """A rotation entry linked to a 'none'-tier quick version → (entry_id, path)."""
    path = tmp_path / "upload" / "Sombr - My Body Isn't Ready (Live From X) [up-abc].mp4"
    path.parent.mkdir()
    path.write_bytes(b"mp4")
    store = sing_app.rotation.store
    entry = sing_app.rotation.add_entry("Mary", "Sombr - My Body Isn't Ready", file_path=str(path))
    store.set_gen_status(entry["id"], JOB, "processing")
    store.upsert_quick_version(JOB, status="chosen", file_path=str(path), lyrics_tier="none")
    return entry["id"], str(path)


class TestSearch:
    def test_defaults_to_cleaned_title(self, client, quick_entry):
        eid, path = quick_entry
        hits = [{"id": 1, "artist": "sombr", "title": "My Body Isn't Ready", "synced": True,
                 "lyrics": "[00:01.00] x", "duration": 217, "album": "", "preview": "x"}]
        with patch.object(quick_lyrics, "search_lrclib", return_value=hits) as s, \
                patch.object(quick_lyrics, "probe_duration", return_value=224.51):
            body = client.get(f"/rotation/quick-lyrics/search?id={eid}").get_json()
        s.assert_called_once_with("Sombr My Body Isn't Ready")
        assert body["query"] == "Sombr My Body Isn't Ready"
        assert body["artist"] == "Sombr" and body["title"] == "My Body Isn't Ready (Live From X)"
        assert body["video_duration"] == 224.5 and body["lyrics_tier"] == "none"
        assert body["results"] == hits and body["error"] is None and body["file_path"] == path

    def test_custom_query_and_lrclib_down(self, client, quick_entry):
        eid, _ = quick_entry
        with patch.object(quick_lyrics, "search_lrclib", side_effect=OSError("down")) as s, \
                patch.object(quick_lyrics, "probe_duration", side_effect=RuntimeError("no ffprobe")):
            resp = client.get(f"/rotation/quick-lyrics/search?id={eid}&q=my+body")
        body = resp.get_json()
        assert resp.status_code == 200
        s.assert_called_once_with("my body")
        assert body["results"] == [] and "paste" in body["error"] and body["video_duration"] is None

    @pytest.mark.parametrize("qs", ["", "?id=abc", "?id=999"])
    def test_bad_entry(self, client, quick_entry, qs):
        assert client.get(f"/rotation/quick-lyrics/search{qs}").status_code == 404

    def test_entry_without_quick_version(self, client, sing_app):
        entry = sing_app.rotation.add_entry("Bob", "A - B")
        resp = client.get(f"/rotation/quick-lyrics/search?id={entry['id']}")
        assert resp.status_code == 404 and "no quick version" in resp.get_json()["error"]

    def test_missing_file(self, client, quick_entry):
        eid, path = quick_entry
        import os
        os.remove(path)
        resp = client.get(f"/rotation/quick-lyrics/search?id={eid}")
        assert resp.status_code == 404 and "missing" in resp.get_json()["error"]


class TestApply:
    def test_rerenders_and_records_tier(self, client, sing_app, quick_entry):
        eid, path = quick_entry
        with patch.object(quick_lyrics, "rerender_quick_video",
                          return_value={"lyrics_tier": "synced", "line_count": 2, "duration": 224.5}) as r:
            resp = client.post("/rotation/quick-lyrics",
                               json={"id": eid, "lyrics": "[00:01.00] a\n[00:02.00] b", "use_timing": True})
        body = resp.get_json()
        assert resp.status_code == 200 and body["success"]
        args, kwargs = r.call_args
        assert args[0] == path and args[1].kind == "synced"
        assert args[2:] == ("Sombr", "My Body Isn't Ready (Live From X)") and kwargs == {"use_timing": True}
        assert sing_app.rotation.store.get_quick_version(JOB)["lyrics_tier"] == "synced"
        assert body["lyrics_tier"] == "synced" and body["file_path"] == path and body["entry_id"] == eid
        entry = next(e for e in body["entries"] if e["id"] == eid)
        assert entry["quick"] == {"state": "chosen", "lyrics_tier": "synced"}

    def test_use_timing_defaults_true_and_can_be_off(self, client, quick_entry):
        eid, _ = quick_entry
        ok = {"lyrics_tier": "constant", "line_count": 1, "duration": 1.0}
        with patch.object(quick_lyrics, "rerender_quick_video", return_value=ok) as r:
            client.post("/rotation/quick-lyrics", json={"id": eid, "lyrics": "a"})
            assert r.call_args.kwargs == {"use_timing": True}
            client.post("/rotation/quick-lyrics", json={"id": eid, "lyrics": "a", "use_timing": False})
            assert r.call_args.kwargs == {"use_timing": False}

    @pytest.mark.parametrize("lyrics", ["", "   ", None, 42])
    def test_empty_lyrics(self, client, quick_entry, lyrics):
        eid, _ = quick_entry
        resp = client.post("/rotation/quick-lyrics", json={"id": eid, "lyrics": lyrics})
        assert resp.status_code == 400

    def test_bad_entry(self, client, quick_entry):
        assert client.post("/rotation/quick-lyrics", json={"id": "1", "lyrics": "a"}).status_code == 404
        assert client.post("/rotation/quick-lyrics", json={"id": 999, "lyrics": "a"}).status_code == 404

    def test_busy(self, client, quick_entry):
        eid, _ = quick_entry
        with patch.object(quick_lyrics, "rerender_quick_video", side_effect=quick_lyrics.RenderBusy("busy")):
            assert client.post("/rotation/quick-lyrics", json={"id": eid, "lyrics": "a"}).status_code == 409

    def test_render_failure_keeps_tier(self, client, sing_app, quick_entry):
        eid, _ = quick_entry
        with patch.object(quick_lyrics, "rerender_quick_video", side_effect=RuntimeError("ffmpeg died")):
            resp = client.post("/rotation/quick-lyrics", json={"id": eid, "lyrics": "a"})
        assert resp.status_code == 500 and "ffmpeg died" in resp.get_json()["error"]
        assert sing_app.rotation.store.get_quick_version(JOB)["lyrics_tier"] == "none"


def test_rotation_exposes_no_lyrics_tier(client, quick_entry):
    eid, _ = quick_entry
    entries = client.get("/rotation").get_json()["entries"]
    assert next(e for e in entries if e["id"] == eid)["quick"] == {"state": "chosen", "lyrics_tier": "none"}
