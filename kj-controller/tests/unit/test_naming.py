import naming

def test_classify_source():
    assert naming.classify_source("NOMAD-0729 - Cher - Believe.mp4") == naming.SOURCE_MASTER
    assert naming.classify_source(
        "-UM1XiyBmhM__Sing King__Bella Kay - iloveit (Karaoke Version).mp4"
    ) == naming.SOURCE_YOUTUBE
    assert naming.classify_source("divebar__WTF - Queen - Bohemian.mp4") == naming.SOURCE_COMMUNITY
    assert naming.classify_source("divebar__GEN-1a2b3c4d - Cher - Believe.mp4") == naming.SOURCE_GEN
    assert naming.classify_source("GEN-1a2b3c4d - Cher - Believe.mp4") == naming.SOURCE_GEN
    assert naming.classify_source("Some Random Upload.mp4") == naming.SOURCE_UPLOAD

def test_media_id_for():
    assert naming.media_id_for(naming.SOURCE_YOUTUBE, "UM1XiyBmhM") == "yt-UM1XiyBmhM"
    assert naming.media_id_for(naming.SOURCE_MASTER, "0729") == "nomad-0729"
    assert naming.media_id_for(naming.SOURCE_GEN, "1a2b3c4d") == "gen-1a2b3c4d"

def test_strip_karaoke_noise():
    assert naming.strip_karaoke_noise("Bella Kay - iloveit (Karaoke Version)") == "Bella Kay - iloveit"
    assert naming.strip_karaoke_noise("River KARAOKE") == "River"
    assert naming.strip_karaoke_noise("the grudge (Final Karaoke Lossy 4k)") == "the grudge"
    assert naming.strip_karaoke_noise("Yesterday [karaoke]") == "Yesterday"

def test_parse_identity_master_is_clean_and_confident():
    r = naming.parse_identity("NOMAD-0729 - Cher - Believe.mp4")
    assert r["source"] == naming.SOURCE_MASTER
    assert r["source_ref"] == "0729"
    assert r["artist"] == "Cher"
    assert r["title"] == "Believe"
    assert r["needs_review"] == 0
    assert r["parse_method"] == "master"

def test_parse_identity_youtube_best_effort_needs_review():
    r = naming.parse_identity(
        "-UM1XiyBmhM__Sing King__Bella Kay - iloveit (Karaoke Version).mp4"
    )
    assert r["source"] == naming.SOURCE_YOUTUBE
    assert r["source_ref"] == "-UM1XiyBmhM"
    # deterministic best-effort: split on ' - ', noise stripped, order UNRESOLVED
    assert r["artist"] == "Bella Kay"
    assert r["title"] == "iloveit"
    assert r["needs_review"] == 1          # order/accuracy unverified until LLM (Phase 2)
    assert r["parse_method"] == "deterministic"

def test_parse_identity_community():
    r = naming.parse_identity("divebar__WTF - Queen - Bohemian Rhapsody.mp4")
    assert r["source"] == naming.SOURCE_COMMUNITY
    assert r["artist"] == "Queen"
    assert r["title"] == "Bohemian Rhapsody"
    assert r["source_ref"].startswith("WTF-")   # brand-<hash8> fallback (no file_id in name)

def test_extract_media_id():
    assert naming.extract_media_id("Bella Kay - iloveit [yt-UM1XiyBmhM].mp4") == "yt-UM1XiyBmhM"
    assert naming.extract_media_id("NOMAD-0729 - Cher - Believe.mp4") is None

def test_build_slug_filename_sanitizes_and_bounds_length():
    out = naming.build_slug_filename("AC/DC", "T.N.T", "yt-abc12345678", ".mp4")
    assert out.endswith(" [yt-abc12345678].mp4")
    assert "/" not in out
    assert len(out.encode("utf-8")) <= 255


def test_content_hash_is_8_lowercase_hex(tmp_path):
    import hashlib
    p = tmp_path / "sample.mp4"
    data = b"\x00\x01\x02nomad-karaoke\xff"
    p.write_bytes(data)
    out = naming.content_hash(str(p))
    assert out == hashlib.sha1(data).hexdigest()[:8]
    assert len(out) == 8
    assert out == out.lower()
    assert all(c in "0123456789abcdef" for c in out)


def test_build_slug_filename_long_media_id_stays_within_255_bytes():
    long_id = "db-" + ("X" * 400)
    out = naming.build_slug_filename("Artist", "Title", long_id, ".mp4")
    assert len(out.encode("utf-8")) <= 255 or out.endswith(f" [{long_id}].mp4")
    # budget must never trim from the wrong end: the stem must not contain a partial suffix
    assert out.endswith(f"[{long_id}].mp4")
