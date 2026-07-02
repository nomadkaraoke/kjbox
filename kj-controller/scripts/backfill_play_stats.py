"""One-off: backfill historic play_events from rotation_archive 'Done' rows.

Dry-run by default; --execute wipes prior backfill rows then re-inserts (idempotent).
Run on device off-show:
    python scripts/backfill_play_stats.py --rotation-db ~/kjdata/rotation.db \
        --media-db ~/kjdata/media_library.db            # dry-run report
    python scripts/backfill_play_stats.py ... --execute
"""

import argparse
import os
import sqlite3
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from media_library import MediaLibraryStore   # noqa: E402
from stats_store import StatsStore             # noqa: E402
from naming import extract_media_id            # noqa: E402
from text_normalize import group_key           # noqa: E402


def _resolve_media_id(ml, file_path):
    if not file_path:
        return None, None, None
    row = ml.get_by_path(file_path) or {}
    mid = row.get("media_id") or extract_media_id(os.path.basename(file_path))
    return mid, row.get("artist"), row.get("title")


def _split_artist_title(song_artist):
    if song_artist and " - " in song_artist:
        a, t = song_artist.split(" - ", 1)
        return a.strip(), t.strip()
    return None, (song_artist or None)


def backfill(rotation_db_path, media_db_path, *, execute=False):
    ml = MediaLibraryStore(media_db_path)
    stats = StatsStore(media_db_path)
    conn = sqlite3.connect(rotation_db_path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT night_date, singer, song_artist, file_path "
        "FROM rotation_archive WHERE status='Done'").fetchall()
    conn.close()

    if execute:
        c = stats._get_conn()
        with stats._lock():
            c.execute("DELETE FROM play_events WHERE source='backfill'")
            c.commit()

    attributed = skipped = 0
    for r in rows:
        mid, a, t = _resolve_media_id(ml, r["file_path"])
        if not mid:
            skipped += 1
            continue
        if not (a or t):
            a, t = _split_artist_title(r["song_artist"])
        if execute:
            stats.record_play(
                mid, singer=r["singer"], artist=a, title=t,
                song_key=group_key(a, t),
                played_at=r["night_date"], night_date=r["night_date"],
                source="backfill")
        attributed += 1
    return {"attributed": attributed, "skipped": skipped}


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--rotation-db", default=os.path.expanduser("~/kjdata/rotation.db"))
    p.add_argument("--media-db", default=os.path.expanduser("~/kjdata/media_library.db"))
    p.add_argument("--execute", action="store_true")
    args = p.parse_args()
    for label, path in (("--rotation-db", args.rotation_db), ("--media-db", args.media_db)):
        if not os.path.exists(path):
            sys.exit(f"error: {label} path does not exist: {path}")
    res = backfill(args.rotation_db, args.media_db, execute=args.execute)
    mode = "EXECUTED" if args.execute else "DRY-RUN"
    print(f"[{mode}] attributed={res['attributed']} skipped={res['skipped']}")


if __name__ == "__main__":
    main()
