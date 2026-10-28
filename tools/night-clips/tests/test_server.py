"""Smoke test the review server over real HTTP with fake clip files in a temp dir."""

import json
import threading
import urllib.error
import urllib.request

import pytest

from nightclips import match
from nightclips.clips import Clip, clip_id, parse_clip_time
from nightclips.config import DEFAULTS
from nightclips.server import ReviewState, make_server


@pytest.fixture
def served(tmp_path, clip_names, songs_media):
    clips = []
    for n in clip_names:
        p = tmp_path / n
        p.write_bytes(bytes(range(256)) * 4)  # 1 KiB fake "video"
        thumb = tmp_path / f"{clip_id(n)}.jpg"
        thumb.write_bytes(b"\xff\xd8fakejpeg")
        clips.append(Clip(id=clip_id(n), filename=n, path=str(p), start=parse_clip_time(n),
                          local=True, duration=20.0, thumb=str(thumb)))
    # one clip with an explicit near-change flag scenario is covered in test_match; here add an unassigned one
    early = "2026-10-08 20.59.00.mp4"
    (tmp_path / early).write_bytes(b"x")
    clips.insert(0, Clip(id=clip_id(early), filename=early, path=str(tmp_path / early),
                         start=parse_clip_time(early), local=True))
    published = []
    cfg = {**DEFAULTS, "max_clips": 20}
    work = tmp_path / "work"
    state = ReviewState("2026-10-08", songs_media, clips, match.match_clips(clips, songs_media), cfg, work,
                        consent_source="legacy",
                        publisher=lambda files, cap, auto: published.append((files, cap, auto)) or {"started": True})
    srv = make_server(state, 0)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    yield base, state, published, work
    srv.shutdown()
    srv.server_close()


def get(url, headers=None):
    req = urllib.request.Request(url, headers=headers or {})
    with urllib.request.urlopen(req) as r:
        return r.status, dict(r.headers), r.read()


def post(url, body):
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def test_page_and_assets(served):
    base, *_ = served
    status, headers, body = get(base + "/")
    assert status == 200 and b"night clips" in body
    for f in ("app.js", "style.css"):
        assert get(f"{base}/static/{f}")[0] == 200
    with pytest.raises(urllib.error.HTTPError):
        get(base + "/static/../server.py")


def test_state_shape(served):
    base, state, *_ = served
    _, _, body = get(base + "/api/state")
    st = json.loads(body)
    assert st["night"] == "2026-10-08"
    assert len(st["clips"]) == 24
    assert st["clips"][0]["status"] == "unassigned"
    assert 0 < len(st["selected"]) <= 20
    assert st["caption"].startswith("Thursday night karaoke 🎤 October 8")
    assert st["uncovered"] == []
    assert st["posted"] is None


def test_media_range_and_thumbs(served):
    base, state, *_ = served
    cid = state.items[1]["id"]
    status, headers, body = get(f"{base}/media/{cid}", {"Range": "bytes=10-19"})
    assert status == 206 and headers["Content-Range"] == "bytes 10-19/1024" and body == bytes(range(10, 20))
    status, headers, body = get(f"{base}/media/{cid}")
    assert status == 200 and len(body) == 1024 and headers["Content-Type"] == "video/mp4"
    status, headers, body = get(f"{base}/thumbs/{cid}.jpg")
    assert status == 200 and body.startswith(b"\xff\xd8")
    with pytest.raises(urllib.error.HTTPError):
        get(f"{base}/media/nope")


def test_reassign_reevaluates_and_validates(served):
    base, state, *_ = served
    item = state.items[2]
    other = [i for i in item["candidates"] if i != item["song_idx"]][0]
    code, res = post(base + "/api/reassign", {"clip_id": item["id"], "song_idx": other})
    assert code == 200
    clip = next(c for c in res["state"]["clips"] if c["id"] == item["id"])
    assert clip["song_idx"] == other and clip["reviewed"]
    code, res = post(base + "/api/reassign", {"clip_id": item["id"], "song_idx": 0})
    assert code == 400  # not adjacent
    code, res = post(base + "/api/reassign", {"clip_id": item["id"], "song_idx": None})
    assert code == 200


def test_draft_saved_and_reloaded(served, songs_media):
    base, state, _, work = served
    sel_ids = list(reversed(state.selected))
    code, res = post(base + "/api/draft", {"selected": sel_ids, "caption": "hello"})
    assert code == 200
    d = json.loads((work / "draft.json").read_text())
    assert d["selected"] == sel_ids and d["caption"] == "hello" and len(d["files"]) == len(sel_ids)
    # a fresh state picks the draft back up
    fresh = ReviewState("2026-10-08", songs_media, list(state.clips.values()),
                        match.match_clips(list(state.clips.values()), songs_media), state.cfg, work,
                        publisher=lambda *a: {})
    assert fresh.load_draft() and fresh.selected == sel_ids and fresh.caption == "hello"


def test_publish_validation_and_posted(served):
    base, state, published, work = served
    excluded = next(i["id"] for i in state.items if i["status"] == "excluded")
    unknown = next(i["id"] for i in state.items if i["status"] == "unknown")
    unassigned = state.items[0]["id"]
    eligible = list(state.selected)

    for bad in ([excluded], [unassigned], [], [state.selected[0]] * 2):
        code, res = post(base + "/api/publish", {"selected": bad, "caption": "c"})
        assert code == 400 and not res["ok"], bad
    code, res = post(base + "/api/publish", {"selected": [unknown], "caption": "c"})
    assert code == 400 and "unknown" in res["errors"][0]
    code, res = post(base + "/api/publish", {"selected": [unknown], "caption": "c", "allow_unknown": True})
    assert code == 200 and res["ok"]
    assert len(published) == 1 and published[0][1] == "c"

    too_many = [i["id"] for i in state.items if i["status"] in ("eligible", "unknown")][:21]
    if len(too_many) == 21:
        code, res = post(base + "/api/publish", {"selected": too_many, "caption": "c", "allow_unknown": True})
        assert code == 400

    code, res = post(base + "/api/posted", {})
    assert code == 200 and (work / "posted.json").exists()
    # once posted, publishing again needs force
    code, res = post(base + "/api/publish", {"selected": eligible, "caption": "c"})
    assert code == 400 and any("already marked as posted" in e for e in res["errors"])
    code, res = post(base + "/api/publish", {"selected": eligible, "caption": "c", "force": True})
    assert code == 200


def test_caption_endpoint(served):
    base, state, *_ = served
    code, res = post(base + "/api/caption", {"selected": state.selected[:1]})
    assert code == 200 and res["caption"].count("\n") == 4  # header, blank, 1 line, blank, footer


def _raw(url, body, headers, method="POST"):
    req = urllib.request.Request(url, data=body, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code


def test_rejects_cross_site_and_rebinding_requests(served):
    """A page elsewhere in the browser must not be able to drive /api/publish
    (simple text/plain POST, foreign Origin) or read clips via DNS rebinding."""
    base, state, published, _ = served
    port = base.rsplit(":", 1)[1]
    body = json.dumps({"clip_ids": [], "allow_unknown": True, "force": True}).encode()
    assert _raw(base + "/api/publish", body, {"Content-Type": "text/plain"}) == 403
    assert _raw(base + "/api/publish", body, {"Content-Type": "application/json",
                                              "Origin": "https://evil.example"}) == 403
    assert _raw(base + "/api/state", None, {"Host": f"evil.example:{port}"}, method="GET") == 403
    assert published == []
    # Our own page (same Origin) still works.
    assert _raw(base + "/api/caption", json.dumps({"clip_ids": []}).encode(),
                {"Content-Type": "application/json", "Origin": base}) == 200


def test_publish_sends_trimmed_copies_of_long_clips(served):
    base, state, published, work = served
    first, second = state.selected[:2]
    state.clips[first].duration = 63.2
    calls = []
    state.trim = lambda path, dur, out_dir, start=0.0: (calls.append((path, dur, out_dir)) or
                                             (str(out_dir / "cut.mp4") if dur > 59 else path))
    code, res = post(base + "/api/publish", {"selected": [first, second], "caption": "c"})
    assert code == 200 and res["ok"]
    files = published[-1][0]
    assert files == [str(work / "trimmed" / "cut.mp4"), state.clips[second].path]
    assert calls[0][2] == work / "trimmed"


def test_publish_reports_trim_failure(served):
    base, state, published, _ = served

    def fail(*a, **kw):
        raise RuntimeError("ffmpeg trim failed for x.mp4: boom")
    state.trim = fail
    code, res = post(base + "/api/publish", {"selected": state.selected[:1], "caption": "c"})
    assert code == 400 and "trim failed" in res["errors"][0]
    assert published == []


def _unassigned(state):
    return next(i for i in state.items if i["status"] == "unassigned")


def test_other_label_makes_a_clip_selectable_and_a_caption_line(served):
    base, state, published, _ = served
    it = _unassigned(state)
    code, res = post(base + "/api/draft", {"selected": [it["id"]], "caption": "c"})
    assert code == 400  # still unassigned
    code, res = post(base + "/api/reassign", {"clip_id": it["id"], "song_idx": -1})
    assert code == 200
    cur = next(c for c in res["state"]["clips"] if c["id"] == it["id"])
    assert cur["custom"] and cur["status"] == "eligible" and cur["song_idx"] is None
    # needs a label to publish
    code, res = post(base + "/api/publish", {"selected": [it["id"]], "caption": "c"})
    assert code == 400 and "label" in res["errors"][0]
    post(base + "/api/note", {"clip_id": it["id"], "note": "  Costume   contest winners 🏆 "})
    code, res = post(base + "/api/caption", {"selected": [it["id"]]})
    assert "Costume contest winners 🏆" in res["caption"]
    state.trim = lambda path, dur, out_dir, start=0.0: path  # fake clip isn't probeable
    code, res = post(base + "/api/publish", {"selected": [it["id"]], "caption": "c"})
    assert code == 200, res
    assert published


def test_other_is_never_preselected_and_survives_draft_reload(served):
    base, state, _, _ = served
    it = _unassigned(state)
    assert it["id"] not in state.selected
    post(base + "/api/reassign", {"clip_id": it["id"], "song_idx": -1})
    post(base + "/api/note", {"clip_id": it["id"], "note": "Awards"})
    post(base + "/api/trim", {"clip_id": it["id"], "start": 3})
    post(base + "/api/draft", {"selected": [it["id"]], "caption": "mine"})
    it.update(custom=False, note="", song_idx=None, trim_start=0.0)
    state._restatus(it)
    assert state.load_draft()
    assert it["custom"] and it["note"] == "Awards" and it["status"] == "eligible"
    assert state.selected == [it["id"]]


def test_trim_start_is_clamped_and_passed_to_trim(served):
    base, state, published, _ = served
    cid = state.selected[0]
    state.clips[cid].duration = state.by_id[cid]["duration"] = 100.0
    code, res = post(base + "/api/trim", {"clip_id": cid, "start": 999})
    assert state.by_id[cid]["trim_start"] == 41.0  # 100 - 59
    post(base + "/api/trim", {"clip_id": cid, "start": -5})
    assert state.by_id[cid]["trim_start"] == 0.0
    post(base + "/api/trim", {"clip_id": cid, "start": 12.34})
    calls = []
    state.trim = lambda path, dur, out_dir, start=0.0: (calls.append(start) or path)
    code, _ = post(base + "/api/publish", {"selected": [cid], "caption": "c"})
    assert code == 200 and calls == [12.3]
    assert post(base + "/api/trim", {"clip_id": "nope", "start": 1})[0] == 400


def test_publish_stages_facebook_only_when_asked(served):
    base, state, published, _ = served
    fb = []
    state.facebook_publisher = lambda files, cap: fb.append((files, cap)) or {"started": True}
    state.trim = lambda path, dur, out_dir, start=0.0: path
    ids = state.selected[:2]
    code, res = post(base + "/api/publish", {"selected": ids, "caption": "c"})
    assert code == 200 and "facebook" not in res and fb == []
    code, res = post(base + "/api/publish", {"selected": ids, "caption": "c", "force": True, "facebook": True})
    assert code == 200 and res["facebook"] == {"started": True}
    assert fb == [(published[-1][0], "c")]  # same files, same caption, same order as Instagram


def test_facebook_launch_failure_is_reported_without_failing_instagram(served):
    base, state, published, _ = served

    def boom(files, cap):
        raise OSError("no chromium")
    state.facebook_publisher = boom
    state.trim = lambda path, dur, out_dir, start=0.0: path
    code, res = post(base + "/api/publish", {"selected": state.selected[:1], "caption": "c", "facebook": True})
    assert code == 200 and res["ok"] and len(published) == 1
    assert res["facebook"]["started"] is False and "no chromium" in res["facebook"]["error"]
