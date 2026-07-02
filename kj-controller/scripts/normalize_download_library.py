"""Backlog migration: move + rename existing downloads into the canonical
per-source slug scheme (download-naming Phase 4).

For every media_library row that is a download (source youtube/community/gen/
upload) whose file still sits at a legacy path, compute the canonical
``<download_folder>/<source>/Artist - Title [media_id].ext`` location, move the
file there, repoint the media_library row + live rotation references, and
trigger a rescan.

Masters (``source=master``, the NOMAD-720p GCS mirror) are EXEMPT — they keep
GCS-native ``NOMAD-####`` names so the rsync mirror stays cheap.

**Dry-run by default.** ``--execute`` applies changes after backing up the DBs.
Review the dry-run CSV first; correct Artist/Title in it and pass it back with
``--from-csv`` to have your fixes applied as the files are moved.

    python scripts/normalize_download_library.py                 # dry-run report
    python scripts/normalize_download_library.py --from-csv corrected.csv --execute
"""
import argparse
import csv
import os
import shutil
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from naming import build_slug_filename  # noqa: E402
from media_library import MediaLibraryStore  # noqa: E402
from scripts.cleanup_redundant_downloads import (  # noqa: E402
    _load_config, _rotation_db_path, _trigger_rescan, relink_references,
)

# Downloads we relocate. Masters are intentionally excluded (GCS-native names).
MIGRATABLE_SOURCES = {"youtube", "community", "gen", "upload"}


def plan_migration(store, download_folder, corrections=None):
    """Return a list of planned moves. Each item:
    {media_id, source, old_path, new_path, artist, title, confidence,
     needs_review}. Skips masters, missing files, and rows already at their
    canonical path. ``corrections`` = {media_id: (artist, title)} overrides.
    """
    corrections = corrections or {}
    plan = []
    for row in store.list_records():
        source = row.get("source") or ""
        if source not in MIGRATABLE_SOURCES:
            continue
        old_path = row.get("file_path")
        if not old_path or not os.path.exists(old_path):
            continue
        media_id = row["media_id"]
        artist, title = corrections.get(media_id, (row.get("artist", ""), row.get("title", "")))
        artist = (artist or "").strip()
        title = (title or "").strip()
        ext = row.get("ext") or os.path.splitext(old_path)[1] or ""
        slug = build_slug_filename(artist, title, media_id, ext)
        new_path = os.path.join(download_folder, source, slug)
        if os.path.realpath(old_path) == os.path.realpath(new_path):
            continue  # already migrated
        plan.append({
            "media_id": media_id, "source": source,
            "old_path": old_path, "new_path": new_path,
            "artist": artist, "title": title,
            "confidence": row.get("confidence"),
            "needs_review": row.get("needs_review", 0),
        })
    return plan


def write_report(report_dir, plan):
    os.makedirs(report_dir, exist_ok=True)
    csv_path = os.path.join(report_dir, "normalize_report.csv")
    md_path = os.path.join(report_dir, "normalize_report.md")
    cols = ["media_id", "source", "artist", "title", "confidence",
            "needs_review", "old_path", "new_path"]
    with open(csv_path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        for p in plan:
            w.writerow({k: p.get(k, "") for k in cols})
    with open(md_path, "w", encoding="utf-8") as fh:
        fh.write(f"# Download-library normalization — {len(plan)} files to move\n\n")
        fh.write("Correct the `artist`/`title` columns in the CSV and re-run with "
                 "`--from-csv <file> --execute` to apply your fixes.\n\n")
        fh.write("| source | artist | title | review | new name |\n|---|---|---|---|---|\n")
        for p in plan:
            fh.write(f"| {p['source']} | {p['artist']} | {p['title']} | "
                     f"{'⚠' if p['needs_review'] else ''} | "
                     f"{os.path.basename(p['new_path'])} |\n")
    return csv_path, md_path


def load_corrections(csv_path):
    """Read a (possibly hand-corrected) report CSV → {media_id: (artist, title)}."""
    out = {}
    with open(csv_path, encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            mid = (row.get("media_id") or "").strip()
            if mid:
                out[mid] = ((row.get("artist") or "").strip(),
                            (row.get("title") or "").strip())
    return out


def _backup(path):
    if path and os.path.exists(path):
        dest = f"{path}.bak-{time.strftime('%Y%m%d-%H%M%S')}"
        shutil.copy2(path, dest)
        return dest
    return None


def apply_migration(store, plan, rotation_db, corrections=None):
    """Move each planned file, repoint media_library + rotation refs. Returns
    {moved, relinked, errors}."""
    corrections = corrections or {}
    remap = {}
    moved = 0
    errors = []
    for p in plan:
        old_path, new_path = p["old_path"], p["new_path"]
        try:
            os.makedirs(os.path.dirname(new_path), exist_ok=True)
            os.replace(old_path, new_path)
        except OSError as exc:
            errors.append(f"{p['media_id']}: {exc}")
            continue
        real_new = os.path.realpath(new_path)
        if p["media_id"] in corrections:
            store.set_metadata(p["media_id"], p["artist"], p["title"])
        store.update_path(p["media_id"], real_new)
        remap[old_path] = real_new
        moved += 1
    relinked = relink_references(rotation_db, remap) if remap else 0
    return {"moved": moved, "relinked": relinked, "errors": errors}


def run(config_path=None, *, execute=False, from_csv=None, report_dir=".",
        rescan=True):
    cfg = _load_config(config_path)
    download_folder = cfg.get("download_folder") or os.path.expanduser("~/kjdata/videos")
    store = MediaLibraryStore(cfg.get("media_db_path"))
    corrections = load_corrections(from_csv) if from_csv else {}

    plan = plan_migration(store, download_folder, corrections)
    if not execute:
        csv_path, md_path = write_report(report_dir, plan)
        print(f"DRY-RUN: {len(plan)} files would move. Report: {csv_path} / {md_path}")
        return {"planned": len(plan), "csv": csv_path}

    # Back up before mutating anything.
    for p in (cfg.get("media_db_path"), _rotation_db_path(cfg),
              cfg.get("media_index_path")):
        b = _backup(p)
        if b:
            print(f"  backed up {p} -> {b}")

    result = apply_migration(store, plan, _rotation_db_path(cfg), corrections)
    print(f"EXECUTE: moved {result['moved']}, relinked {result['relinked']} "
          f"rotation refs, {len(result['errors'])} errors")
    for e in result["errors"]:
        print(f"  ERROR {e}")
    if rescan:
        _trigger_rescan()
    return result


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--config", help="Path to kj-controller config.json")
    ap.add_argument("--execute", action="store_true",
                    help="Apply the migration (default: dry-run report only)")
    ap.add_argument("--from-csv", help="A corrected report CSV whose artist/title "
                                       "overrides are applied during --execute")
    ap.add_argument("--report-dir", default=".", help="Where to write the dry-run report")
    ap.add_argument("--no-rescan", action="store_true", help="Skip the post-move rescan")
    args = ap.parse_args(argv)
    run(args.config, execute=args.execute, from_csv=args.from_csv,
        report_dir=args.report_dir, rescan=not args.no_rescan)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
