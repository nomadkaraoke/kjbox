"""Community YouTube releases we already hold on the SSD fold into the library file.

2026-09-26: KN's KARAR "The Strokes - The Adults Are Talking" (YouTube,
channel karaokear) is the same video as our
``KARAR-093 - The Strokes - The Adults Are Talking.mp4`` — it showed as a
separate version, out-ranked our file, and would have been re-downloaded.
"""
import copy

import routes

LOCAL = {"path": "/ssd/karaokear MP4/KARAR-093 - The Strokes - The Adults Are Talking.mp4",
         "filename": "KARAR-093 - The Strokes - The Adults Are Talking.mp4",
         "artist": "The Strokes", "title": "The Adults Are Talking", "disc_id": "KARAR-093",
         "format": "mp4"}
KARAR = {"brand_code": "KARAR", "brand_name": "KARAR", "is_community": True,
         "youtube_url": "https://youtu.be/HC0WggzIF_s"}
SC = {"brand_code": "SC", "brand_name": "Sound Choice", "is_community": False,
      "youtube_url": "https://youtu.be/sc123"}


def _kn(*tracks, artist="The Strokes", title="The Adults Are Talking"):
    return [{"artist": artist, "title": title, "tracks": [copy.deepcopy(t) for t in tracks]}]


def test_same_brand_same_song_folds_into_the_library_file():
    local = [dict(LOCAL)]
    kn = _kn(KARAR, SC)
    routes._fold_community_into_local(local, kn)
    assert [t["brand_code"] for t in kn[0]["tracks"]] == ["SC"]    # commercial untouched
    assert local[0]["is_community"] is True
    assert local[0]["alt_youtube_url"] == "https://youtu.be/HC0WggzIF_s"


def test_song_with_only_the_folded_track_is_dropped():
    local = [dict(LOCAL)]
    kn = _kn(KARAR)
    routes._fold_community_into_local(local, kn)
    assert kn == []


def test_grouped_search_shows_one_karar_version_ranked_as_community():
    local = [dict(LOCAL)]
    kn = _kn(KARAR, SC)
    routes._fold_community_into_local(local, kn)
    groups = routes._group_search_results(local, kn)
    assert len(groups) == 1
    versions = groups[0]["versions"]
    assert [v["source"] for v in versions] == ["local", "kn"]      # our file first, then SC
    assert versions[0]["priority_class"] == "community"


def test_different_brand_is_not_folded():
    local = [dict(LOCAL, disc_id="FBK-001", filename="FBK-001 - The Strokes - The Adults Are Talking.mp4")]
    kn = _kn(KARAR)
    routes._fold_community_into_local(local, kn)
    assert len(kn[0]["tracks"]) == 1 and "is_community" not in local[0]


def test_different_song_is_not_folded():
    local = [dict(LOCAL)]
    kn = _kn(KARAR, title="Reptilia")
    routes._fold_community_into_local(local, kn)
    assert len(kn[0]["tracks"]) == 1


def test_cdg_of_the_same_brand_is_a_different_release():
    local = [dict(LOCAL, filename="KARAR-093 - The Strokes - The Adults Are Talking.zip",
                  path="/ssd/x.zip", format="zip")]
    kn = _kn(KARAR)
    routes._fold_community_into_local(local, kn)
    assert len(kn[0]["tracks"]) == 1


def test_two_candidate_files_are_ambiguous_so_nothing_folds():
    local = [dict(LOCAL), dict(LOCAL, path="/ssd/other/KARAR-093b.mp4",
                               filename="KARAR-093b - The Strokes - The Adults Are Talking.mp4")]
    kn = _kn(KARAR)
    routes._fold_community_into_local(local, kn)
    assert len(kn[0]["tracks"]) == 1


def test_commercial_track_never_folds():
    local = [dict(LOCAL, disc_id="SC-8001", filename="SC-8001 - The Strokes - The Adults Are Talking.mp4")]
    kn = _kn(SC)
    routes._fold_community_into_local(local, kn)
    assert len(kn[0]["tracks"]) == 1


def test_curated_row_without_disc_id_still_folds_via_filename_prefix():
    # _build_local_media_row drops parsed disc ids for curated (media_library) rows.
    local = [dict(LOCAL, disc_id=None)]
    kn = _kn(KARAR)
    routes._fold_community_into_local(local, kn)
    assert kn == [] and local[0]["is_community"] is True


def test_hyphenated_title_is_not_mistaken_for_a_brand():
    local = [dict(LOCAL, disc_id=None, filename="Jay-Z - Empire State Of Mind.mp4",
                  artist="Jay-Z", title="Empire State Of Mind")]
    kn = _kn({**KARAR, "brand_code": "JAY"}, artist="Jay-Z", title="Empire State Of Mind")
    routes._fold_community_into_local(local, kn)
    assert len(kn[0]["tracks"]) == 1


def test_two_uploads_of_the_same_brand_are_both_kept():
    local = [dict(LOCAL)]
    kn = _kn(KARAR, {**KARAR, "youtube_url": "https://youtu.be/other"})
    routes._fold_community_into_local(local, kn)
    assert len(kn[0]["tracks"]) == 2 and "is_community" not in local[0]
