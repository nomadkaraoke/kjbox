"""Smoke test the review server over real HTTP with fake clip files in a temp dir."""

import json
import threading
import tomllib
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
    state.trim = lambda path, dur, out_dir, start=0.0, end=None: (calls.append((path, dur, out_dir)) or
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
    state.trim = lambda path, dur, out_dir, start=0.0, end=None: path  # fake clip isn't probeable
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
    assert state.by_id[cid]["trim_start"] == 99.0  # any start up to 1s before the end (100 - 1)
    post(base + "/api/trim", {"clip_id": cid, "start": -5})
    assert state.by_id[cid]["trim_start"] == 0.0
    post(base + "/api/trim", {"clip_id": cid, "start": 12.34})
    calls = []
    state.trim = lambda path, dur, out_dir, start=0.0, end=None: (calls.append(start) or path)
    code, _ = post(base + "/api/publish", {"selected": [cid], "caption": "c"})
    assert code == 200 and calls == [12.3]
    assert post(base + "/api/trim", {"clip_id": "nope", "start": 1})[0] == 400


def test_publish_stages_facebook_only_when_asked(served):
    base, state, published, _ = served
    fb = []
    state.facebook_publisher = lambda files, cap: fb.append((files, cap)) or {"started": True}
    state.trim = lambda path, dur, out_dir, start=0.0, end=None: path
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
    state.trim = lambda path, dur, out_dir, start=0.0, end=None: path
    code, res = post(base + "/api/publish", {"selected": state.selected[:1], "caption": "c", "facebook": True})
    assert code == 200 and res["ok"] and len(published) == 1
    assert res["facebook"]["started"] is False and "no chromium" in res["facebook"]["error"]


def _performers_by_consent(state):
    """{consent: performer name} from tonight's songs, first seen."""
    out = {}
    for s in state.songs:
        for p in s["performers"]:
            out.setdefault(p["consent"], p["name"])
    return out


def _eligible_selected(state):
    return next(state.by_id[i] for i in state.selected if state.by_id[i]["status"] == "eligible")


def test_edit_performers_to_someone_new_is_consent_unknown_and_tagged_from_handles(served):
    base, state, *_ = served
    state.cfg = {**state.cfg, "handles": {"zed q": "zed.sings"}}
    it = _eligible_selected(state)
    code, res = post(base + "/api/performers", {"clip_id": it["id"], "names": "Zed Q"})
    assert code == 200
    clip = next(c for c in res["state"]["clips"] if c["id"] == it["id"])
    assert clip["status"] == "unknown" and clip["reviewed"]
    assert clip["performers"] == [{"name": "Zed Q", "consent": "unknown", "reason": "edited", "instagram": "zed.sings"}]
    assert it["id"] in state.selected  # unknown stays selected; publish asks to confirm
    assert "@zed.sings" in state.default_caption([it["id"]])
    assert any("consent unknown" in e for e in state.validate_selection([it["id"]], allow_unknown=False))


def test_edit_performers_uses_tonights_consent(served):
    base, state, *_ = served
    names = _performers_by_consent(state)
    it = _eligible_selected(state)
    post(base + "/api/performers", {"clip_id": it["id"], "names": names["no"]})
    assert it["status"] == "excluded" and it["id"] not in state.selected
    post(base + "/api/performers", {"clip_id": it["id"], "names": f"{names['yes']} & {names['yes'].upper()}"})
    assert it["status"] == "eligible" and [p["name"] for p in it["performers"]] == [names["yes"]]


def test_edit_performers_empty_or_same_goes_back_to_log_and_reassign_clears(served):
    base, state, *_ = served
    it = _eligible_selected(state)
    logged = " & ".join(p["name"] for p in state.songs[it["song_idx"]]["performers"])
    post(base + "/api/performers", {"clip_id": it["id"], "names": "Zed Q"})
    post(base + "/api/performers", {"clip_id": it["id"], "names": ""})
    assert it["performers"] is None and it["status"] == "eligible"
    post(base + "/api/performers", {"clip_id": it["id"], "names": logged.lower()})
    assert it["performers"] is None
    post(base + "/api/performers", {"clip_id": it["id"], "names": "Zed Q"})
    post(base + "/api/reassign", {"clip_id": it["id"], "song_idx": it["song_idx"]})
    assert it["performers"] is None


def test_edit_performers_needs_a_song_and_survives_draft_reload(served):
    base, state, _, _ = served
    code, _res = post(base + "/api/performers", {"clip_id": _unassigned(state)["id"], "names": "Zed Q"})
    assert code == 400
    it = _eligible_selected(state)
    post(base + "/api/performers", {"clip_id": it["id"], "names": "Zed Q, Ana"})
    post(base + "/api/draft", {"selected": [it["id"]], "caption": "c"})
    it["performers"] = None
    state._restatus(it)
    assert state.load_draft()
    assert [p["name"] for p in it["performers"]] == ["Zed Q", "Ana"] and it["status"] == "unknown"


def test_trim_end_limits_the_cut_and_survives_the_draft(served):
    base, state, published, _ = served
    cid = state.selected[0]
    state.clips[cid].duration = state.by_id[cid]["duration"] = 30.0
    code, _ = post(base + "/api/trim", {"clip_id": cid, "start": 3, "end": 13})
    assert code == 200 and (state.by_id[cid]["trim_start"], state.by_id[cid]["trim_end"]) == (3.0, 13.0)
    post(base + "/api/trim", {"clip_id": cid, "start": 3, "end": 3.2})  # at least 1s long
    assert state.by_id[cid]["trim_end"] == 4.0
    post(base + "/api/trim", {"clip_id": cid, "start": 3, "end": 30})  # to the end = no limit
    assert state.by_id[cid]["trim_end"] is None
    post(base + "/api/trim", {"clip_id": cid, "start": 3, "end": 13})
    post(base + "/api/trim", {"clip_id": cid, "start": 5})  # start-only (old page) keeps the end
    assert state.by_id[cid]["trim_end"] == 13.0
    calls = []
    state.trim = lambda path, dur, out_dir, start=0.0, end=None: (calls.append((start, end)) or path)
    code, _ = post(base + "/api/publish", {"selected": [cid], "caption": "c"})
    assert code == 200 and calls == [(5.0, 13.0)]
    state.by_id[cid].update(trim_start=0.0, trim_end=None)
    assert state.load_draft()  # publish saved the draft
    assert (state.by_id[cid]["trim_start"], state.by_id[cid]["trim_end"]) == (5.0, 13.0)


def _handleless_yes(state):
    """A consenting singer with no handle who is in at least one clip."""
    clipped = {i["song_idx"] for i in state.items if i["song_idx"] is not None}
    for idx, s in enumerate(state.songs):
        for p in s["performers"]:
            if idx in clipped and p["consent"] == "yes" and not p.get("instagram"):
                return p["name"]
    raise AssertionError("fixture has no consenting singer without a handle")


def test_set_handle_saves_config_and_tags_the_caption(served, tmp_path):
    base, state, *_ = served
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text('[handles]\n# mine\n')
    state.cfg["path"] = str(cfg_path)
    name = _handleless_yes(state)
    code, res = post(base + "/api/handle", {"name": name.upper(), "handle": "@New.Handle"})
    assert code == 200 and res["handle"] == "new.handle" and res["tagged"] >= 1
    assert tomllib.loads(cfg_path.read_text())["handles"][name.upper()] == "new.handle"
    perfs = [p for s in res["state"]["songs"] for p in s["performers"] if p["name"] == name]
    assert perfs and all(p["instagram"] == "new.handle" for p in perfs)
    ids = [c["id"] for c in res["state"]["clips"]
           if c["song_idx"] is not None and any(p["name"] == name for p in state.songs[c["song_idx"]]["performers"])]
    assert ids, "fixture should have a clip of this singer"
    assert "@new.handle" in post(base + "/api/caption", {"selected": ids[:1]})[1]["caption"]
    # forget it again
    code, res = post(base + "/api/handle", {"name": name, "handle": ""})
    assert code == 200 and not any(p.get("instagram") for s in state.songs for p in s["performers"] if p["name"] == name)


def test_set_handle_never_tags_someone_who_said_no(served):
    base, state, *_ = served
    no = _performers_by_consent(state)["no"]
    code, res = post(base + "/api/handle", {"name": no, "handle": "nope_nope"})
    assert code == 200 and res["tagged"] == 0
    assert not any(p.get("instagram") == "nope_nope" for s in state.songs for p in s["performers"])


def test_set_handle_rejects_bad_handles(served):
    base, *_ = served
    assert post(base + "/api/handle", {"name": "X", "handle": "not a handle!"})[0] == 400
    assert post(base + "/api/handle", {"name": " ", "handle": "ok"})[0] == 400
