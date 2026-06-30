"""Unit tests for scripts/cleanup_redundant_downloads.py pure logic.

These cover the *decision* functions only (what to delete / quarantine / skip);
the destructive file I/O is exercised separately and kept thin. The script
operates on a LIVE production device, so the pairing/normalization/rotation-safety
rules are the part that must never be wrong.
"""

import os
import sqlite3
import sys

import pytest

# scripts/ is not a package; add it to the path so we can import the module.
_SCRIPTS = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "scripts"))
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)

import cleanup_redundant_downloads as clean  # noqa: E402


# --- video id extraction -------------------------------------------------

def test_extract_video_id_from_prefixed_name():
    assert clean.extract_video_id(
        "RlBlAKxyqZw__Unknown__Maxïmo Park - Books from Boxes (Karaoke).mp4"
    ) == "RlBlAKxyqZw"


def test_extract_video_id_none_for_unprefixed_name():
    assert clean.extract_video_id(
        "NOMAD-0729 - Maxïmo Park - Books from Boxes.mp4") is None
    assert clean.extract_video_id(
        "ASTN - Happier Than Ever (Final Karaoke Lossy 4k).mp4") is None


# --- litter classification ----------------------------------------------

def test_litter_with_same_id_playable_is_deleted():
    # The thumbnail's channel string differs from the completed file's
    # (Nomad Karaoke vs Unknown) — must still pair by video id, not basename.
    files = [
        "RlBlAKxyqZw__Unknown__Maxïmo Park - Books from Boxes (Karaoke).mp4",
        "RlBlAKxyqZw__Nomad Karaoke__Maxïmo Park - Books from Boxes (Karaoke).webp",
        "RlBlAKxyqZw__Nomad Karaoke__Maxïmo Park - Books from Boxes (Karaoke).f313.webm.part",
    ]
    to_delete, orphans = clean.plan_litter_removals(files)
    assert set(to_delete) == {
        "RlBlAKxyqZw__Nomad Karaoke__Maxïmo Park - Books from Boxes (Karaoke).webp",
        "RlBlAKxyqZw__Nomad Karaoke__Maxïmo Park - Books from Boxes (Karaoke).f313.webm.part",
    }
    assert orphans == []


def test_litter_orphan_without_playable_is_flagged_not_deleted():
    files = [
        "FarxCYSvVtg__CerealKiller__CKK - Twenty One Pilots - Migraine (Karaoke).webp",
    ]
    to_delete, orphans = clean.plan_litter_removals(files)
    assert to_delete == []
    assert orphans == [
        "FarxCYSvVtg__CerealKiller__CKK - Twenty One Pilots - Migraine (Karaoke).webp",
    ]


def test_completed_playable_is_never_litter():
    files = ["RlBlAKxyqZw__Unknown__Song (Karaoke).mp4"]
    to_delete, orphans = clean.plan_litter_removals(files)
    assert to_delete == []
    assert orphans == []


# --- song-key normalization (twin matching) -----------------------------

def test_song_key_ignores_karaoke_suffix_and_diacritics():
    yt = clean.ytdl_song_key(
        "RlBlAKxyqZw__Unknown__Maxïmo Park - Books from Boxes (Karaoke).mp4")
    master = clean.master_song_key(
        "NOMAD-0729 - Maxïmo Park - Books from Boxes.mp4")
    assert yt == master
    assert yt  # non-empty


def test_song_key_folds_ampersand_and_and():
    a = clean.song_key("Bob Marley & the Wailers - Kaya")
    b = clean.song_key("Bob Marley and the Wailers - Kaya")
    assert a == b


def test_master_song_key_requires_nomad_prefix():
    # A non-NOMAD library file is not a master candidate for twinning.
    assert clean.master_song_key(
        "VEVO-2392 - Maximo Park - Books From Boxes.mp4") is None


# --- twin planning ------------------------------------------------------

def test_plan_twin_quarantines_matches_master():
    yt = ["RlBlAKxyqZw__Unknown__Maxïmo Park - Books from Boxes (Karaoke).mp4"]
    masters = ["NOMAD-0729 - Maxïmo Park - Books from Boxes.mp4"]
    pairs = clean.plan_twin_quarantines(yt, masters)
    assert pairs == [(
        "RlBlAKxyqZw__Unknown__Maxïmo Park - Books from Boxes (Karaoke).mp4",
        "NOMAD-0729 - Maxïmo Park - Books from Boxes.mp4",
    )]


def test_plan_twin_quarantines_skips_unmatched():
    yt = ["abcdefghijk__Unknown__Some Other Song (Karaoke).mp4"]
    masters = ["NOMAD-0729 - Maxïmo Park - Books from Boxes.mp4"]
    assert clean.plan_twin_quarantines(yt, masters) == []


# --- rotation-link safety ------------------------------------------------

def _make_rotation_db(path, entries=(), archive=()):
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE rotation_entries (id INTEGER PRIMARY KEY, file_path TEXT)")
    con.execute("CREATE TABLE rotation_archive (id INTEGER PRIMARY KEY, file_path TEXT)")
    con.executemany("INSERT INTO rotation_entries (file_path) VALUES (?)",
                    [(p,) for p in entries])
    con.executemany("INSERT INTO rotation_archive (file_path) VALUES (?)",
                    [(p,) for p in archive])
    con.commit()
    con.close()


def test_referenced_file_paths_unions_both_tables(tmp_path):
    db = str(tmp_path / "rotation.db")
    _make_rotation_db(
        db,
        entries=["/opt/nomad/YTDownloads/a.mp4", None],
        archive=["/opt/nomad/YTDownloads/b.mp4"],
    )
    refs = clean.referenced_file_paths(db)
    assert "/opt/nomad/YTDownloads/a.mp4" in refs
    assert "/opt/nomad/YTDownloads/b.mp4" in refs
    assert None not in refs


def test_referenced_file_paths_empty_for_missing_db(tmp_path):
    # A missing/empty DB must fail safe to "nothing referenced" without raising.
    assert clean.referenced_file_paths(str(tmp_path / "nope.db")) == set()


def test_is_referenced_matches_across_unicode_forms():
    import unicodedata
    nfc = unicodedata.normalize("NFC", "/x/Maxïmo.mp4")
    nfd = unicodedata.normalize("NFD", "/x/Maxïmo.mp4")
    refs = {nfd}
    # Path stored NFD, candidate queried NFC (or vice versa) must still match.
    assert clean.is_referenced(nfc, refs) is True
    assert clean.is_referenced("/x/other.mp4", refs) is False


# --- re-linking referenced twins to their masters -----------------------

def test_relink_references_updates_both_tables(tmp_path):
    db = str(tmp_path / "rotation.db")
    yt = "/opt/nomad/YTDownloads/RlBlAKxyqZw__Unknown__Song (Karaoke).mp4"
    master = "/opt/nomad/MP4-720p/NOMAD-0729 - Artist - Song.mp4"
    _make_rotation_db(db, entries=[yt, "/other/keep.mp4"], archive=[yt])
    updated = clean.relink_references(db, {yt: master})
    assert updated == 2  # one live row + one archive row
    con = sqlite3.connect(db)
    live = [r[0] for r in con.execute("SELECT file_path FROM rotation_entries")]
    arch = [r[0] for r in con.execute("SELECT file_path FROM rotation_archive")]
    con.close()
    assert master in live and "/other/keep.mp4" in live and yt not in live
    assert arch == [master]


def test_relink_references_matches_across_unicode_forms(tmp_path):
    import unicodedata
    db = str(tmp_path / "rotation.db")
    stored = unicodedata.normalize("NFD", "/opt/nomad/YTDownloads/Maxïmo (Karaoke).mp4")
    remap_key = unicodedata.normalize("NFC", "/opt/nomad/YTDownloads/Maxïmo (Karaoke).mp4")
    master = "/opt/nomad/MP4-720p/NOMAD-1 - Maxïmo.mp4"
    _make_rotation_db(db, entries=[stored])
    assert clean.relink_references(db, {remap_key: master}) == 1
    con = sqlite3.connect(db)
    assert [r[0] for r in con.execute("SELECT file_path FROM rotation_entries")] == [master]
    con.close()


def test_relink_references_noop_when_no_match(tmp_path):
    db = str(tmp_path / "rotation.db")
    _make_rotation_db(db, entries=["/keep/a.mp4"])
    assert clean.relink_references(db, {"/not/present.mp4": "/m.mp4"}) == 0
