import os
import sqlite3

from media_library import MediaLibraryStore
import scripts.normalize_download_library as nm


def _touch(p):
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "wb") as f:
        f.write(b"\x00" * 8)


def _store_with(tmp_path, rows):
    s = MediaLibraryStore(":memory:")
    for r in rows:
        s.upsert(r)
    return s


def test_plan_skips_master_missing_and_already_migrated(tmp_path):
    dl = str(tmp_path / "downloads")
    # youtube file at a legacy flat path -> should be planned
    old_yt = os.path.join(dl, "-abc__chan__Bella Kay - iloveit.mp4")
    _touch(old_yt)
    # a master (exempt) + a youtube file already at its canonical slug path
    master = os.path.join(dl, "NOMAD-720p", "NOMAD-0729 - Cher - Believe.mp4")
    _touch(master)
    canon = os.path.join(dl, "youtube", "A - B [yt-done].mp4")
    _touch(canon)
    store = _store_with(tmp_path, [
        {"media_id": "yt-abc", "source": "youtube", "artist": "Bella Kay",
         "title": "iloveit", "ext": ".mp4", "file_path": old_yt},
        {"media_id": "nomad-0729", "source": "master", "artist": "Cher",
         "title": "Believe", "ext": ".mp4", "file_path": master},
        {"media_id": "yt-done", "source": "youtube", "artist": "A", "title": "B",
         "ext": ".mp4", "file_path": canon},
        {"media_id": "yt-gone", "source": "youtube", "artist": "X", "title": "Y",
         "ext": ".mp4", "file_path": os.path.join(dl, "missing.mp4")},
    ])
    plan = nm.plan_migration(store, dl)
    ids = {p["media_id"] for p in plan}
    assert ids == {"yt-abc"}  # master exempt, canon already-migrated, gone missing
    p = plan[0]
    assert p["new_path"] == os.path.join(dl, "youtube", "Bella Kay - iloveit [yt-abc].mp4")


def test_report_roundtrips_corrections(tmp_path):
    dl = str(tmp_path / "downloads")
    old = os.path.join(dl, "raw.mp4")
    _touch(old)
    store = _store_with(tmp_path, [
        {"media_id": "yt-abc", "source": "youtube", "artist": "wrong",
         "title": "order", "ext": ".mp4", "file_path": old, "needs_review": 1}])
    plan = nm.plan_migration(store, dl)
    csv_path, _md = nm.write_report(str(tmp_path / "rep"), plan)
    # Hand-correct the CSV
    text = open(csv_path).read().replace("wrong", "Sublime").replace("order", "Santeria")
    open(csv_path, "w").write(text)
    corr = nm.load_corrections(csv_path)
    assert corr["yt-abc"] == ("Sublime", "Santeria")


def test_apply_moves_file_updates_library_and_relinks_rotation(tmp_path):
    dl = str(tmp_path / "downloads")
    old = os.path.join(dl, "-abc__chan__Bella Kay - iloveit.mp4")
    _touch(old)
    store = _store_with(tmp_path, [
        {"media_id": "yt-abc", "source": "youtube", "artist": "Bella Kay",
         "title": "iloveit", "ext": ".mp4", "file_path": old}])

    # Minimal rotation.db referencing the old path.
    rot = str(tmp_path / "rotation.db")
    con = sqlite3.connect(rot)
    con.execute("CREATE TABLE rotation_entries (file_path TEXT)")
    con.execute("CREATE TABLE rotation_archive (file_path TEXT)")
    con.execute("INSERT INTO rotation_entries (file_path) VALUES (?)", (old,))
    con.commit()
    con.close()

    plan = nm.plan_migration(store, dl)
    result = nm.apply_migration(store, plan, rot)
    assert result["moved"] == 1 and result["relinked"] == 1 and not result["errors"]

    new_path = os.path.join(dl, "youtube", "Bella Kay - iloveit [yt-abc].mp4")
    assert os.path.exists(new_path) and not os.path.exists(old)
    assert os.path.realpath(store.get("yt-abc")["file_path"]) == os.path.realpath(new_path)
    con = sqlite3.connect(rot)
    linked = con.execute("SELECT file_path FROM rotation_entries").fetchone()[0]
    con.close()
    assert os.path.realpath(linked) == os.path.realpath(new_path)


def test_apply_honors_corrections(tmp_path):
    dl = str(tmp_path / "downloads")
    old = os.path.join(dl, "raw.mp4")
    _touch(old)
    store = _store_with(tmp_path, [
        {"media_id": "yt-abc", "source": "youtube", "artist": "wrong",
         "title": "order", "ext": ".mp4", "file_path": old, "needs_review": 1}])
    corrections = {"yt-abc": ("Sublime", "Santeria")}
    plan = nm.plan_migration(store, dl, corrections)
    nm.apply_migration(store, plan, str(tmp_path / "rotation.db"), corrections)
    row = store.get("yt-abc")
    assert (row["artist"], row["title"]) == ("Sublime", "Santeria")
    assert row["needs_review"] == 0 and row["parse_method"] == "manual"
    assert row["file_path"].endswith("youtube/Sublime - Santeria [yt-abc].mp4")


def test_apply_skips_when_target_exists(tmp_path):
    dl = str(tmp_path / "downloads")
    old = os.path.join(dl, "raw.mp4")
    _touch(old)
    store = _store_with(tmp_path, [
        {"media_id": "yt-abc", "source": "youtube", "artist": "Bella Kay",
         "title": "iloveit", "ext": ".mp4", "file_path": old}])
    plan = nm.plan_migration(store, dl)
    # Pre-create the canonical target -> collision, must be skipped not clobbered.
    _touch(plan[0]["new_path"])
    result = nm.apply_migration(store, plan, str(tmp_path / "rotation.db"))
    assert result["moved"] == 0
    assert result["errors"] and "already exists" in result["errors"][0]
    assert os.path.exists(old)  # original left intact


def test_load_corrections_requires_columns(tmp_path):
    bad = tmp_path / "bad.csv"
    bad.write_text("media_id,notes\nyt-a,hello\n")
    import pytest
    with pytest.raises(SystemExit):
        nm.load_corrections(str(bad))


def test_csv_cells_sanitized_against_formula_injection(tmp_path):
    dl = str(tmp_path / "downloads")
    old = os.path.join(dl, "raw.mp4")
    _touch(old)
    store = _store_with(tmp_path, [
        {"media_id": "yt-abc", "source": "youtube", "artist": "=cmd()",
         "title": "iloveit", "ext": ".mp4", "file_path": old}])
    plan = nm.plan_migration(store, dl)
    csv_path, _ = nm.write_report(str(tmp_path / "rep"), plan)
    text = open(csv_path).read()
    assert "'=cmd()" in text  # leading = neutralized


def test_update_path_media_library():
    s = MediaLibraryStore(":memory:")
    s.upsert({"media_id": "yt-a", "source": "youtube", "file_path": "/old.mp4"})
    assert s.update_path("yt-a", "/new.mp4") is True
    assert s.get("yt-a")["file_path"] == "/new.mp4"
    assert s.update_path("nope", "/x.mp4") is False
