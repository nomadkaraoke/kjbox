"""KN panel + singer search share one song-grouped composition.

Regression for the "fall out boy dance dance" report (2026-09-24): the KN
panel rendered library files in a flat "In your library" list, split one song
across KN's "Dance, Dance" (community) and "Dance Dance" (full catalog)
spellings, and parked a Funbox GCS-mirror file in its own section — while the
singer UI silently dropped that mirror-only file altogether.
"""
from unittest.mock import patch

import routes

_LOCAL_ROWS = [
    {"path": "/lib/SC/SC3453-06 - Fall Out Boy - Dance Dance.zip",
     "filename": "SC3453-06 - Fall Out Boy - Dance Dance.zip",
     "artist": "Fall Out Boy", "title": "Dance Dance",
     "format": "cdg+mp3", "disc_id": "SC3453-06"},
    {"path": "/lib/CB/CBD-106432 - Fall Out Boy - Dance, Dance.zip",
     "filename": "CBD-106432 - Fall Out Boy - Dance, Dance.zip",
     "artist": "Fall Out Boy (MPX)", "title": "Dance, Dance",
     "format": "cdg+mp3", "disc_id": "CBD-106432"},
]

_KN_SONGS = [
    # KN community spelling.
    {"artist": "Fall Out Boy", "title": "Dance, Dance", "tracks": [
        {"brand_code": "JSK", "brand_name": "JSK", "is_community": True,
         "youtube_url": "https://www.youtube.com/watch?v=jskjskjskjs"},
    ]},
    # KN full-catalog spelling: disc-only brands, one we hold (SC), one we
    # don't (KV).
    {"artist": "Fall Out Boy", "title": "Dance Dance", "tracks": [
        {"brand_code": "SC", "brand_name": "Sound Choice",
         "is_community": False, "youtube_url": None},
        {"brand_code": "KV", "brand_name": "Karaoke Version",
         "is_community": False, "youtube_url": None},
    ]},
]

_DIVEBAR_SONGS = [
    {"artist": "Fall Out Boy", "title": "Dance, Dance", "tracks": [
        {"file_id": "fbk1", "brand": "Funbox Karaoke", "brand_code": "FBK",
         "format": "cdg", "file_size": 1308432, "in_gcs": True},
    ]},
]


def _patched(app):
    return (
        patch.object(app.catalog, "is_available", return_value=True),
        patch.object(app.catalog, "search",
                     side_effect=lambda *a, **kw: [dict(r) for r in _LOCAL_ROWS]),
        patch("routes.karaoke_nerds.search",
              side_effect=lambda *a, **kw: [
                  {**s, "tracks": [dict(t) for t in s["tracks"]]}
                  for s in _KN_SONGS]),
        patch("routes.divebar.search",
              side_effect=lambda *a, **kw: [
                  {**s, "tracks": [dict(t) for t in s["tracks"]]}
                  for s in _DIVEBAR_SONGS]),
    )


def _kn_panel(app, client):
    p1, p2, p3, p4 = _patched(app)
    with p1, p2, p3, p4:
        resp = client.post("/karaoke-nerds/search",
                           json={"query": "fall out boy dance dance"})
    assert resp.status_code == 200
    return resp.get_json()


def _brands(group):
    out = []
    for v in group["versions"]:
        if v["source"] == "local":
            out.append(("local", v["local"]["disc_id"]))
        else:
            out.append(("kn", v["kn"]["brand_code"]))
    return out


class TestKnPanelGrouped:
    def test_returns_song_grouped_shape(self, flask_app, flask_test_client):
        data = _kn_panel(flask_app, flask_test_client)
        assert set(data) == {"songs", "karaoke_nerds_timeout"}

    def test_one_group_holds_library_kn_and_mirror_versions(
            self, flask_app, flask_test_client):
        """Punctuation variants, the (MPX) artist tag, local files, KN tracks
        and the standalone mirror file all land in ONE song group."""
        songs = _kn_panel(flask_app, flask_test_client)["songs"]
        assert len(songs) == 1
        brands = _brands(songs[0])
        assert ("local", "SC3453-06") in brands
        assert ("local", "CBD-106432") in brands
        assert ("kn", "JSK") in brands
        assert ("kn", "FBK") in brands

    def test_mirror_only_version_is_downloadable_kn_shape(
            self, flask_app, flask_test_client):
        group = _kn_panel(flask_app, flask_test_client)["songs"][0]
        fbk = next(v for v in group["versions"]
                   if v["source"] == "kn" and v["kn"]["brand_code"] == "FBK")
        assert fbk["kn"]["mirror_only"] is True
        assert fbk["kn"]["divebar"]["file_id"] == "fbk1"
        assert fbk["kn"]["youtube_url"] == ""
        assert fbk["kn"]["is_community"] is True

    def test_disc_only_kept_only_for_brands_we_lack(
            self, flask_app, flask_test_client):
        """KV (not held) stays as information; SC disc-only is dropped because
        our own SC file is already in the group."""
        brands = _brands(_kn_panel(flask_app, flask_test_client)["songs"][0])
        assert ("kn", "KV") in brands
        assert ("kn", "SC") not in brands

    def test_playable_versions_first_then_disc_only(
            self, flask_app, flask_test_client):
        group = _kn_panel(flask_app, flask_test_client)["songs"][0]
        disc_only = [v["source"] == "kn" and not v["kn"].get("youtube_url")
                     and not (v["kn"].get("divebar") or {}).get("file_id")
                     for v in group["versions"]]
        assert disc_only == sorted(disc_only)  # all False before any True
        playable = [v["priority_rank"] for v, d in
                    zip(group["versions"], disc_only) if not d]
        assert playable == sorted(playable)
        assert group["version_count"] == len(group["versions"])

    def test_disc_only_dedup_uses_raw_disc_prefix(
            self, flask_app, flask_test_client):
        """Unregistered brands (no canonical code) still dedup: a local
        "ASK-036498" file hides KN's disc-only "ASK" row."""
        extra = {"path": "/lib/ASK/ASK-036498 - Fall Out Boy - Dance, Dance.zip",
                 "filename": "ASK-036498 - Fall Out Boy - Dance, Dance.zip",
                 "artist": "Fall Out Boy", "title": "Dance, Dance",
                 "format": "cdg+mp3", "disc_id": "ASK-036498"}
        ask = {"brand_code": "ASK", "brand_name": "ASK",
               "is_community": False, "youtube_url": None}
        _LOCAL_ROWS.append(extra)
        _KN_SONGS[1]["tracks"].append(ask)
        try:
            brands = _brands(_kn_panel(flask_app, flask_test_client)["songs"][0])
        finally:
            _LOCAL_ROWS.remove(extra)
            _KN_SONGS[1]["tracks"].remove(ask)
        assert ("local", "ASK-036498") in brands
        assert ("kn", "ASK") not in brands


class TestSingerSearchIncludesMirror:
    def test_mirror_only_version_reaches_singer(self, sing_app, client, token):
        p1, p2, p3, p4 = _patched(sing_app)
        with p1, p2, p3, p4:
            resp = client.get(
                f"/sing/search?q=fall+out+boy+dance+dance&t={token}")
        songs = resp.get_json()["songs"]
        assert len(songs) == 1
        brands = _brands(songs[0])
        assert ("kn", "FBK") in brands
        # Singers never see unplayable disc-only rows.
        assert ("kn", "KV") not in brands
        assert ("kn", "SC") not in brands


def test_kj_pick_binds_mirror_only_version_to_divebar():
    """A kj_pick snapshot holding a mirror-only version resolves to the GCS
    file, exactly like a KN track with a mirror cross-ref."""
    req = {"source_meta": {"versions": [{
        "source": "kn",
        "kn": {"brand_code": "FBK", "youtube_url": "", "mirror_only": True,
               "divebar": {"file_id": "fbk1", "format": "cdg"}},
    }]}}
    src_type, src_ref, _meta = routes._pick_version_from_kj_pick(req, 0)
    assert (src_type, src_ref) == ("divebar", "fbk1")
