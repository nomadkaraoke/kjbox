"""One-off: seed media_library rows for SSD tracks referenced by rotation history.

Design D2 (docs/archive/2026-07-02-ssd-library-media-identity-design.md):
rotation-scoped import (~400 files, ~2.9 GB of hashing — minutes), NOT the
whole 415K-row catalog. Dry-run by default (no hashing, no writes). Run
on-device off-show AFTER relocating media_db_path (design D5), BEFORE the
play-stats backfill:

  /opt/nomad/kjbox/kj-controller/venv/bin/python -m scripts.import_rotation_ssd_tracks \
      --rotation-db /home/nomad/kjdata/rotation.db \
      --media-db /opt/nomad/data/media_library.db \
      --catalog-db /opt/nomad/kjbox/kj-controller/external_media.db \
      --mount /media/nomad/Nomad4TBOne          # add --execute after review
"""
import argparse
import os
import sqlite3
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from catalog import ExternalCatalog  # noqa: E402
from library_media import ensure_library_row  # noqa: E402
from media_library import MediaLibraryStore  # noqa: E402


def rotation_ssd_paths(rotation_db, mount):
    """Distinct SSD file_paths across active + archive rotation tables."""
    conn = sqlite3.connect(f"file:{rotation_db}?mode=ro", uri=True)
    like = mount.rstrip("/") + "/%"
    paths = set()
    for table in ("rotation_entries", "rotation_archive"):
        try:
            rows = conn.execute(
                f"SELECT DISTINCT file_path FROM {table} WHERE file_path LIKE ?",
                (like,)).fetchall()
        except sqlite3.OperationalError:
            continue  # table absent in older DBs
        paths.update(fp for (fp,) in rows if fp)
    conn.close()
    return sorted(paths)


def run(rotation_db, media_db, catalog_db, mount, execute=False):
    ml = MediaLibraryStore(media_db)
    catalog = ExternalCatalog({"external_catalog_db": catalog_db})
    counts = {"already": 0, "imported": 0, "catalog_miss": 0,
              "missing": 0, "failed": 0}
    missing, catalog_misses = [], []
    for path in rotation_ssd_paths(rotation_db, mount):
        if ml.get_by_path(path):
            counts["already"] += 1
            continue
        if not os.path.isfile(path):
            counts["missing"] += 1
            missing.append(path)
            continue
        if catalog.get_by_path(path) is None:
            # counted within 'imported' too — these land with a deterministic
            # filename parse + needs_review=1 (fix later via the ✎ editor)
            counts["catalog_miss"] += 1
            catalog_misses.append(path)
        if not execute:
            counts["imported"] += 1  # would import
            continue
        try:
            row = ensure_library_row(path, catalog, ml)
        except Exception as exc:
            # One corrupt/unreadable file must not abort the whole batch.
            print(f"  FAILED ({exc}): {path}")
            row = None
        if row:
            counts["imported"] += 1
        else:
            counts["failed"] += 1
    return counts, missing, catalog_misses


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--rotation-db", required=True)
    ap.add_argument("--media-db", required=True)
    ap.add_argument("--catalog-db", required=True)
    ap.add_argument("--mount", required=True)
    ap.add_argument("--execute", action="store_true",
                    help="hash + write rows (default: dry-run report only)")
    args = ap.parse_args()
    counts, missing, catalog_misses = run(
        args.rotation_db, args.media_db, args.catalog_db, args.mount,
        execute=args.execute)
    mode = "EXECUTED" if args.execute else "DRY-RUN (no writes, no hashing)"
    print(f"{mode}: {counts}")
    for p in missing:
        print(f"  MISSING (skipped): {p}")
    for p in catalog_misses:
        print(f"  CATALOG MISS (deterministic parse, needs_review=1): {p}")


if __name__ == "__main__":
    main()
