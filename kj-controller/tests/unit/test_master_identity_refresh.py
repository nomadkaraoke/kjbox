"""Regression: a recycled/renamed Nomad master brand code must not keep the old song's identity.

Incident 2026-10-08: gen recycled NOMAD-1754 ("Eli - The Comeback", job deleted)
for "Amy Macdonald - Poison Prince". The rescan matched the new file to the
existing nomad-1754 row and only refreshed file_path, so KJ search labelled the
Poison Prince file "Eli - The Comeback" and the rotation entry got that name.
"""
import os

from media import MediaIndex
from media_library import MediaLibraryStore
from stats_store import StatsStore

OLD = "NOMAD-1754 - Eli - The Comeback.mp4"
NEW = "NOMAD-1754 - Amy Macdonald - Poison Prince.mp4"


def _touch(p, mtime=None):
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "wb") as f:
        f.write(b"\x00" * 16)
    if mtime is not None:
        os.utime(p, (mtime, mtime))


def _setup(tmp_path):
    masters = str(tmp_path / "NOMAD-720p")
    db = str(tmp_path / "media.db")
    store = MediaLibraryStore(db)
    stats = StatsStore(db)
    mi = MediaIndex({"media_folders": [masters], "download_folder": str(tmp_path / "dl"),
                     "media_index_path": str(tmp_path / "i.json")},
                    media_library=store)
    return masters, store, stats, mi


def test_recycled_brand_code_takes_new_identity_and_retires_stats(tmp_path):
    masters, store, stats, mi = _setup(tmp_path)
    _touch(os.path.join(masters, OLD))
    mi.scan()
    assert store.get("nomad-1754")["artist"] == "Eli"
    stats.record_play("nomad-1754", entry_id=1, singer="Bob", artist="Eli", title="The Comeback")
    stats.record_preview("nomad-1754", artist="Eli", title="The Comeback")
    stats.upsert_note("nomad-1754", "great", "usual", artist="Eli", title="The Comeback")

    # gen deletes the Eli job, recycles 1754; master sync swaps the file.
    os.remove(os.path.join(masters, OLD))
    _touch(os.path.join(masters, NEW))
    mi.scan()

    row = store.get("nomad-1754")
    assert (row["artist"], row["title"]) == ("Amy Macdonald", "Poison Prince")
    assert row["raw_original_name"] == NEW
    assert row["file_path"].endswith(NEW)
    conn = stats._get_conn()
    for table in ("play_events", "preview_events", "version_notes"):
        live = conn.execute(f"SELECT COUNT(*) FROM {table} WHERE media_id='nomad-1754'").fetchone()[0]
        retired = conn.execute(
            f"SELECT COUNT(*) FROM {table} WHERE media_id LIKE 'nomad-1754~retired-%'").fetchone()[0]
        assert (live, retired) == (0, 1), table


def test_corrected_artist_same_song_keeps_stats(tmp_path):
    masters, store, stats, mi = _setup(tmp_path)
    _touch(os.path.join(masters, "NOMAD-1681 - noname - Song.mp4"))
    mi.scan()
    stats.record_play("nomad-1681", entry_id=7, singer="Ann", artist="noname", title="Song")

    os.remove(os.path.join(masters, "NOMAD-1681 - noname - Song.mp4"))
    _touch(os.path.join(masters, "NOMAD-1681 - Real Band - Song.mp4"))
    mi.scan()

    row = store.get("nomad-1681")
    assert (row["artist"], row["title"]) == ("Real Band", "Song")
    n = stats._get_conn().execute(
        "SELECT COUNT(*) FROM play_events WHERE media_id='nomad-1681'").fetchone()[0]
    assert n == 1


def test_unchanged_master_keeps_manual_edit(tmp_path):
    masters, store, stats, mi = _setup(tmp_path)
    _touch(os.path.join(masters, OLD))
    mi.scan()
    store.set_metadata("nomad-1754", "ELI", "The Comeback (Live)")
    mi.scan()
    row = store.get("nomad-1754")
    assert (row["artist"], row["title"]) == ("ELI", "The Comeback (Live)")
    assert row["parse_method"] == "manual"


def test_old_and_new_file_present_newest_wins(tmp_path):
    masters, store, stats, mi = _setup(tmp_path)
    _touch(os.path.join(masters, OLD), mtime=1_000_000)
    mi.scan()
    # Mid-sync: replacement copied, old not yet reconciled away.
    _touch(os.path.join(masters, NEW), mtime=2_000_000)
    mi.scan()
    assert store.get("nomad-1754")["title"] == "Poison Prince"
    mi.scan()  # stable: does not flip back
    assert store.get("nomad-1754")["title"] == "Poison Prince"


def test_replace_identity_without_stats_tables():
    store = MediaLibraryStore(":memory:")
    store.upsert({"media_id": "nomad-1", "source": "master", "artist": "A", "title": "B",
                  "raw_original_name": "NOMAD-0001 - A - B.mp4", "file_path": "/x"})
    moved = store.replace_identity({"media_id": "nomad-1", "source": "master", "artist": "C",
                                    "title": "D", "raw_original_name": "NOMAD-0001 - C - D.mp4",
                                    "file_path": "/y"}, retire_stats=True, retired_suffix="t")
    assert moved == 0
    assert store.get("nomad-1")["artist"] == "C"
