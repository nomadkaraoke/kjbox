import datetime as dt

from nightclips import match
from nightclips import select as sel
from nightclips.clips import Clip
from nightclips.config import DEFAULTS, load_config

from .conftest import make_clips


def items_for(clips, songs):
    m = match.match_clips(clips, songs)
    out = []
    for c in clips:
        idx = m[c.id]["song_idx"]
        out.append({
            "id": c.id, "start": c.start.strftime("%Y-%m-%d %H:%M:%S"), "local": c.local,
            "song_idx": idx, "flags": m[c.id]["flags"],
            "status": sel.song_status(None if idx is None else songs[idx]),
        })
    return out


def test_song_status():
    yes = {"name": "A", "consent": "yes"}
    no = {"name": "B", "consent": "no"}
    unk = {"name": "C", "consent": "unknown"}
    assert sel.song_status(None) == sel.UNASSIGNED
    assert sel.song_status({"postable": True, "performers": [yes]}) == sel.ELIGIBLE
    assert sel.song_status({"postable": False, "performers": [yes, unk]}) == sel.UNKNOWN
    assert sel.song_status({"postable": False, "performers": [yes, no, unk]}) == sel.EXCLUDED
    # postable flag but a non-yes performer -> never eligible
    assert sel.song_status({"postable": True, "performers": [yes, unk]}) == sel.UNKNOWN


def test_oct8_preselection_only_consenting(clip_names, songs_media):
    items = items_for(make_clips(clip_names), songs_media)
    chosen = sel.preselect(items, songs_media, 20)
    by_id = {i["id"]: i for i in items}
    assert chosen, "some clips should be eligible with synthesized consent"
    assert all(by_id[c]["status"] == sel.ELIGIBLE for c in chosen)
    # Mija's per-song "no" on Versace excludes that clip but not Make You Feel My Love
    versace = next(i for i in items if "01.34.10" in i["id"])
    adele = next(i for i in items if "01.48.37" in i["id"])
    assert versace["status"] == sel.EXCLUDED
    assert adele["status"] in (sel.ELIGIBLE, sel.UNKNOWN, sel.EXCLUDED)
    # every consenting performer with a clip is covered
    assert sel.uncovered_performers(items, songs_media, chosen) == []
    # show order
    assert [by_id[c]["start"] for c in chosen] == sorted(by_id[c]["start"] for c in chosen)


def test_oct8_fallback_preselects_nothing(clip_names, songs_unknown):
    items = items_for(make_clips(clip_names), songs_unknown)
    assert sel.preselect(items, songs_unknown, 20) == []
    assert {i["status"] for i in items} == {sel.UNKNOWN}


def _night(n_songs, n_clips_per_song, performers_per_song=None):
    base = dt.datetime(2026, 10, 8, 21, 0, 0)
    songs = []
    for i in range(n_songs):
        name = (performers_per_song or (lambda i: f"S{i}"))(i)
        songs.append({
            "entry_id": i, "song": f"Song {i}", "singer": name,
            "sung_at": (base + dt.timedelta(minutes=4 * i)).strftime("%Y-%m-%d %H:%M:%S"),
            "performers": [{"name": name, "consent": "yes", "reason": "night_default", "instagram": None}],
            "postable": True,
        })
    songs = match.index_songs(songs)
    clips = []
    for i in range(n_songs):
        for k in range(n_clips_per_song):
            t = base + dt.timedelta(minutes=4 * i, seconds=40 + 30 * k)
            n = t.strftime("%Y-%m-%d %H.%M.%S.mp4")
            clips.append(Clip(id=n, filename=n, path=n, start=t, local=True))
    return songs, clips


def test_cap_twenty_and_spread():
    songs, clips = _night(40, 2)  # 40 consenting singers, 80 clips
    items = items_for(clips, songs)
    chosen = sel.preselect(items, songs, 20)
    assert len(chosen) == 20
    assert len({c for c in chosen}) == 20


def test_one_per_singer_first_then_fill():
    # 5 songs by 2 singers alternating, 3 clips each -> 2 coverage picks then fill to 6
    songs, clips = _night(5, 3, performers_per_song=lambda i: "Ana" if i % 2 == 0 else "Bo")
    items = items_for(clips, songs)
    chosen = sel.preselect(items, songs, 6)
    assert len(chosen) == 6
    by_id = {i["id"]: i for i in items}
    first_song = {by_id[c]["song_idx"] for c in chosen}
    assert first_song == {0, 1, 2, 3, 4}  # new songs preferred before repeats
    assert sel.uncovered_performers(items, songs, chosen) == []


def test_flagged_and_not_local_never_preselected():
    songs, clips = _night(3, 1)
    items = items_for(clips, songs)
    items[0]["flags"] = ["near_change"]
    items[1]["local"] = False
    chosen = sel.preselect(items, songs, 20)
    assert chosen == [items[2]["id"]]
    assert sel.uncovered_performers(items, songs, chosen) == ["S0"]


def test_caption_rendering():
    songs = match.index_songs([
        {"entry_id": 1, "song": "Gethsemane - Sleep Token", "singer": "Shylo R.", "sung_at": "2026-10-08 22:12:44",
         "performers": [{"name": "Shylo R.", "consent": "yes", "instagram": "shylo.sings"}], "postable": True},
        {"entry_id": 2, "song": "Stay", "singer": "Fiona & Mija", "sung_at": "2026-10-08 22:20:00",
         "performers": [{"name": "Fiona", "consent": "yes", "instagram": None},
                        {"name": "Mija", "consent": "yes", "instagram": "@mija"}], "postable": True},
    ])
    items = [{"song_idx": 0}, {"song_idx": 0}, {"song_idx": 1}]
    cap = sel.render_caption("2026-10-08", items, songs, DEFAULTS["caption"])
    assert cap == (
        "Thursday night karaoke 🎤 October 8\n\n"
        "@shylo.sings — Gethsemane - Sleep Token\n"
        "Fiona & @mija — Stay\n\n"
        "#karaoke #nomadkaraoke"
    )
    custom = {"header": "{weekday} {date}", "line": "{song} by {performers}", "footer": ""}
    assert sel.render_caption("2026-10-08", items[2:], songs, custom) == "Thursday 2026-10-08\n\nStay by Fiona & @mija"


def test_config_created_with_defaults(tmp_path):
    cfg = load_config(tmp_path)
    assert (tmp_path / "config.toml").exists()
    assert cfg["max_clips"] == 20
    assert cfg["caption"]["header"].startswith("Thursday night karaoke")
    (tmp_path / "config.toml").write_text('max_clips = 50\n[caption]\nfooter = "#x"\n')
    cfg = load_config(tmp_path)
    assert cfg["max_clips"] == 20  # clamped to Instagram's limit
    assert cfg["caption"]["footer"] == "#x"
    assert cfg["caption"]["header"].startswith("Thursday")
