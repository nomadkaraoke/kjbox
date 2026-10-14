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
