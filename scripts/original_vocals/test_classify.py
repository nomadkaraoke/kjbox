"""Unit tests for the original-vocals phase-1 classifier.

Run:  python -m pytest scripts/original_vocals/test_classify.py -q
"""
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

import classify as C  # noqa: E402


def af(name, ext=None, size=1000, folder="NOMAD-0001 - Artist - Title"):
    ext = ext if ext is not None else (name.rsplit(".", 1)[-1].lower() if "." in name else "")
    return C.AudioFile(size=size, path=f"{folder}/{name}", name=name, ext=ext)


# --- exclusion -------------------------------------------------------------

def test_exclusions_cover_derived_artifacts():
    for n in [
        "Song (Karaoke).mp3", "Song (Instrumental Custom).flac",
        "Song (Vocals model_bs_roformer).flac", "Song (Final Karaoke Lossy 720p).mp4",
        "Song (Title).mp4", "Song (With Vocals).mkv",
        "Song (Instrumental +BV mel_band_roformer).flac", "Song - Instr. short Version.wav",
        "Song (Backing Vocals).flac", "Song (a cappella).flac", "Song (Acapella).mp3",
        "Song (Lead Vocals mel_band).flac", "Song (Karaoke Version).mp3",
    ]:
        assert C.is_excluded(n.lower()), n


def test_bare_karaoke_suffix_excluded():
    # "01 Am I Wry  No - Karaoke.mp3" (NOMAD-0079) slipped through paren-only rules
    assert C.is_excluded("01 am i wry  no - karaoke.mp3")


def test_name_match_normalises_apostrophes():
    # folder uses curly apostrophe, file uses straight (NOMAD-0195 real case)
    assert C.name_matches_title("hi i'm case - smoke damage.wav", "hi i’m case - smoke damage")


def test_original_mix_not_excluded():
    for n in ["Artist - Title.flac", "Artist - Title (Original).flac",
              "Artist - Title (flacfetch).flac", "Artist - Title (Local).wav",
              "Artist - Title (Youtube abc123).wav", "06 Title.mp3"]:
        assert not C.is_excluded(n.lower()), n


# --- markers + name match --------------------------------------------------

def test_marker_scores():
    assert C.marker_score("x (original).flac")[0] == 100
    assert C.marker_score("x (flacfetch).flac")[0] == 100
    assert C.marker_score("x (uploaded).flac")[0] == 100
    assert C.marker_score("x (youtube abc).wav")[0] == 90
    assert C.marker_score("x (local).flac")[0] == 90
    assert C.marker_score("plain.flac")[0] == 0


def test_name_match_strips_track_number():
    assert C.name_matches_title("06 don't wake me up.mp3", "don't wake me up")
    assert C.name_matches_title("03 - prettier.flac", "prettier")
    assert C.name_matches_title("this is me smiling - prettier.mp3",
                                "this is me smiling - prettier")
    assert not C.name_matches_title("something else.mp3", "prettier")


# --- classify_folder tiers -------------------------------------------------

def test_high_via_marker():
    r = C.classify_folder("NOMAD-0900", "Ska Daddyz - Hotel California", [
        af("Ska Daddyz - Hotel California (Karaoke).mp3"),
        af("Ska Daddyz - Hotel California (Instrumental).flac"),
        af("Ska Daddyz - Hotel California (Original).flac"),
        af("Ska Daddyz - Hotel California (Original).wav"),
    ])
    assert r.tier == "HIGH"
    assert r.method == "original"
    assert r.chosen_ext == "flac"  # flac preferred over wav for same marker


def test_high_via_name_match():
    r = C.classify_folder("NOMAD-0038", "The Hush Sound - Where We Went Wrong", [
        af("The Hush Sound - Where We Went Wrong.mp3"),
        af("The Hush Sound - Where We Went Wrong (Karaoke).mp3"),
    ])
    assert r.tier == "HIGH"
    assert "name-match" in r.method


def test_flacfetch_prefers_flac_over_webm():
    r = C.classify_folder("NOMAD-1360", "Ayesha Erotica - Where You At", [
        af("Ayesha Erotica - Where You At (flacfetch).webm"),
        af("Ayesha Erotica - Where You At (flacfetch).flac"),
    ])
    assert r.chosen_ext == "flac"
    assert r.tier == "HIGH"


def test_med_single_leftover():
    r = C.classify_folder("NOMAD-0004", "Hush Sound - Don't Wake Me Up", [
        af("06 Don't Wake Me Up.mp3"),
        af("06 Don't Wake Me Up (Karaoke).mp3"),
    ])
    # "06 Don't Wake Me Up" doesn't match title "Hush Sound - Don't Wake Me Up"
    # exactly, so no marker/name score -> single leftover -> MED.
    assert r.tier == "MED"
    assert r.method == "leftover-only"


def test_low_ambiguous():
    r = C.classify_folder("NOMAD-0500", "X - Y", [
        af("random cover live.wav"),
        af("another take.mp3"),
    ])
    assert r.tier == "LOW"
    assert r.method == "leftover-ambiguous"
    assert r.alt_candidates  # at least one alternative recorded


def test_no_source_when_karaoke_sourced():
    r = C.classify_folder("NOMAD-0271", "Frightened Rabbit - Music Now", [
        af("Frightened Rabbit - Music Now (Karaoke) [_2kUE3zhlzM].webm"),
    ])
    assert r.tier == "NO_SOURCE"
    assert r.method == "karaoke-sourced"
    assert r.chosen_path is None
    assert not r.auto_fetch


def test_gap_when_no_audio():
    r = C.classify_folder("NOMAD-9999", "Nobody - Nothing", [])
    assert r.tier == "GAP"
    assert not r.auto_fetch


def test_short_version_note():
    r = C.classify_folder("NOMAD-0224", "Queen - Crazy Little Thing (Short Version)", [
        af("Queen - Crazy Little Thing (Original).flac"),
    ])
    assert r.note == "short-version-edit"


# --- auto_fetch policy -----------------------------------------------------

def test_auto_fetch_tiers():
    def mk(tier):
        return C.ClassifierResult("NOMAD-0001", "a", "b", tier, "m",
                                  "NOMAD-0001 - a - b/x.flac", "flac", 1, 1)
    assert mk("HIGH").auto_fetch
    assert mk("MED").auto_fetch
    assert mk("LOW").auto_fetch
    r = C.ClassifierResult("NOMAD-0001", "a", "b", "NO_SOURCE", "m", None, None, 1, 0)
    assert not r.auto_fetch
    r2 = C.ClassifierResult("NOMAD-0001", "a", "b", "GAP", "m", None, None, 0, 0)
    assert not r2.auto_fetch


# --- listing parse + helpers ----------------------------------------------

def test_parse_listing_groups_audio_only():
    lines = [
        "1000||NOMAD-0001 - Artist - Title/Artist - Title.flac",
        "2000||NOMAD-0001 - Artist - Title/Artist - Title (Title).png",
        "3000||NOMAD-0001 - Artist - Title/notes.txt",
        "4000||NOMAD-0002 - Other - Song/Other - Song (Original).wav",
        "garbage line without separator",
    ]
    folders = C.parse_listing(lines)
    assert set(folders) == {"NOMAD-0001", "NOMAD-0002"}
    title, files = folders["NOMAD-0001"]
    assert title == "Artist - Title"
    assert [f.ext for f in files] == ["flac"]  # png/txt excluded as non-audio


def test_build_manifest_sorted_by_number():
    lines = [
        "1||NOMAD-0010 - A - B/A - B (Original).flac",
        "1||NOMAD-0002 - C - D/C - D (Original).flac",
    ]
    res = C.build_manifest(lines)
    assert [r.brand_code for r in res] == ["NOMAD-0002", "NOMAD-0010"]


def test_safe_dst_name_mirrors_master_naming():
    assert C.safe_dst_name("NOMAD-0900", "Ska Daddyz - Hotel California", "flac") == \
        "NOMAD-0900 - Ska Daddyz - Hotel California.flac"
    # brand regex resolves it
    assert C._BRAND_RE.match("NOMAD-0900 - Ska Daddyz - Hotel California.flac")


def test_fetch_plan_excludes_no_source(tmp_path):
    results = [
        C.ClassifierResult("NOMAD-0001", "A", "B", "HIGH", "original",
                           "NOMAD-0001 - A - B/A - B (Original).flac", "flac", 3, 1),
        C.ClassifierResult("NOMAD-0002", "C", "D", "NO_SOURCE", "karaoke-sourced",
                           None, None, 1, 0),
    ]
    p = tmp_path / "fetch.tsv"
    n = C.write_fetch_plan(results, str(p), C.DROPBOX_ROOT)
    assert n == 1
    body = p.read_text()
    assert "NOMAD-0001" in body and "NOMAD-0002" not in body
    assert body.startswith("NOMAD-0001\t")
