#!/usr/bin/env python3
"""Audit and clean up redundant / leftover files in the KJ download folder.

Two classes of cruft accumulate in ``download_folder`` (``/opt/nomad/YTDownloads``):

1. **Litter** — yt-dlp leftovers: ``.webp`` thumbnails and ``.part`` / ``.fNNN.*``
   stream fragments. Safe to delete *only* when a completed playable for the same
   YouTube id exists (matched by **video id**, since a re-download attempt can carry
   a different ``__channel__`` string than the finished file). A litter file with no
   completed companion is an **orphan** (a failed download) — flagged, never deleted.

2. **Twins** — a YouTube re-download of a song we already produced. When a
   ``NOMAD-#### - Artist - Title`` master exists in ``MP4-720p``, the YTDownloads copy
   is redundant; it is **quarantined** (moved, reversible), never deleted.

Nothing is removed if it is currently linked by a rotation entry (live or archived).

This runs on a LIVE production device. Default mode is **dry-run** (read-only): it
prints a report and writes CSV/MD. Pass ``--execute`` to actually delete litter and
quarantine twins — only after reviewing the dry-run, and only between shows.

Usage:
    python3 cleanup_redundant_downloads.py [--config PATH] [--execute]
                                           [--report-dir DIR] [--no-rescan]
"""

import argparse
import csv
import json
import os
import re
import shutil
import sqlite3
import sys
import unicodedata

# kj-controller modules (this script lives in kj-controller/scripts/).
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from text_normalize import normalize  # noqa: E402
from catalog import parse_karaoke_filename  # noqa: E402
from utils import parse_youtube_filename  # noqa: E402

PLAYABLE_EXTS = (".mp4", ".webm", ".mkv", ".zip", ".avi", ".mov")
QUARANTINE_DIRNAME = "_redundant_quarantine"

_VID_RE = re.compile(r"^([A-Za-z0-9_-]{11})__")
_FRAGMENT_RE = re.compile(r"\.f\d+\.(?:webm|mp4|m4a|mkv)(\.part)?$", re.IGNORECASE)
# Strip karaoke/instrumental tag noise so "X (Karaoke)" keys equal to "X".
_KARAOKE_TAG_RE = re.compile(
    r"\([^)]*\b(?:karaoke|instrumental)\b[^)]*\)"
    r"|\[[^\]]*\b(?:karaoke|instrumental)\b[^\]]*\]"
    r"|\bkaraoke\s+version\b"
    r"|\b(?:karaoke|instrumental)\b",
    re.IGNORECASE,
)


# --- pure decision functions (unit-tested) -------------------------------

def extract_video_id(filename):
    """Return the leading 11-char YouTube id from ``id__channel__title`` names."""
    m = _VID_RE.match(filename)
    return m.group(1) if m else None


def is_playable_filename(filename):
    """True for a completed playable media file (not a fragment/partial)."""
    low = filename.lower()
    if low.endswith(".part"):
        return False
    if _FRAGMENT_RE.search(low):
        return False
    return low.endswith(PLAYABLE_EXTS)


def is_litter_filename(filename):
    """True for a yt-dlp leftover: thumbnail, partial, or stream fragment."""
    low = filename.lower()
    if low.endswith(".webp") or low.endswith(".part"):
        return True
    return bool(_FRAGMENT_RE.search(low))


def plan_litter_removals(filenames):
    """Split litter into (to_delete, orphans).

    A litter file is deletable only when a completed playable for the **same
    video id** exists. Litter with no id, or no playable companion, is an orphan.
    """
    by_id = {}
    for fn in filenames:
        by_id.setdefault(extract_video_id(fn), []).append(fn)
    to_delete, orphans = [], []
    for vid, group in by_id.items():
        has_playable = vid is not None and any(
            is_playable_filename(f) for f in group)
        for f in group:
            if is_litter_filename(f):
                (to_delete if has_playable else orphans).append(f)
    return to_delete, orphans


def song_key(text):
    """Canonical match key for a song title, ignoring karaoke-tag noise."""
    if not text:
        return ""
    return normalize(_KARAOKE_TAG_RE.sub(" ", text))


def master_song_key(filename):
    """Song key for a ``NOMAD-#### - Artist - Title`` master, else None."""
    disc_id, artist, title = parse_karaoke_filename(filename)
    if not disc_id.upper().startswith("NOMAD"):
        return None
    key = song_key(f"{artist} {title}")
    return key or None


def ytdl_song_key(filename):
    """Song key for a ``id__channel__title`` YouTube download, else None."""
    parsed = parse_youtube_filename(filename)
    if not parsed:
        return None
    _id, _channel, title = parsed
    return song_key(title) or None


def plan_twin_quarantines(yt_filenames, master_filenames):
    """Pair each YT download with a NOMAD master of the same song.

    Returns a list of ``(yt_filename, master_filename)`` for redundant twins.
    """
    master_by_key = {}
    for fn in master_filenames:
        k = master_song_key(fn)
        if k:
            master_by_key.setdefault(k, fn)
    pairs = []
    for fn in yt_filenames:
        k = ytdl_song_key(fn)
        if k and k in master_by_key:
            pairs.append((fn, master_by_key[k]))
    return pairs


def referenced_file_paths(db_path):
    """Union of file_path values across rotation_entries + rotation_archive.

    Fails safe to an empty set if the DB is missing/unreadable. Never raises.
    """
    refs = set()
    if not os.path.exists(db_path):
        return refs
    try:
        con = sqlite3.connect(db_path)
    except sqlite3.Error:
        return refs
    try:
        for tbl in ("rotation_entries", "rotation_archive"):
            try:
                for (p,) in con.execute(f"SELECT file_path FROM {tbl}"):
                    if p:
                        refs.add(p)
            except sqlite3.OperationalError:
                continue
    finally:
        con.close()
    return refs


def is_referenced(path, refs):
    """True if ``path`` matches any referenced path, tolerant of NFC/NFD forms."""
    target = unicodedata.normalize("NFC", path)
    return any(unicodedata.normalize("NFC", r) == target for r in refs)


def relink_references(db_path, remap):
    """Re-point rotation rows from old paths to new (master) paths.

    ``remap`` is ``{old_path: new_path}``. Updates rotation_entries and
    rotation_archive, matching stored paths tolerant of NFC/NFD. Returns the
    number of rows updated. Lets a twin be quarantined without orphaning a
    queued/archived song that was linked to it.
    """
    if not remap or not os.path.exists(db_path):
        return 0
    nfc = lambda p: unicodedata.normalize("NFC", p)
    remap_nfc = {nfc(k): v for k, v in remap.items()}
    updated = 0
    try:
        con = sqlite3.connect(db_path)
    except sqlite3.Error:
        return 0
    try:
        for tbl in ("rotation_entries", "rotation_archive"):
            try:
                rows = list(con.execute(f"SELECT rowid, file_path FROM {tbl}"))
            except sqlite3.OperationalError:
                continue
            for rowid, fp in rows:
                if not fp:
                    continue
                new = remap_nfc.get(nfc(fp))
                if new and new != fp:
                    con.execute(
                        f"UPDATE {tbl} SET file_path = ? WHERE rowid = ?", (new, rowid))
                    updated += 1
        con.commit()
    finally:
        con.close()
    return updated


# --- I/O orchestration (exercised via dry-run, not unit-tested) ----------

def _load_config(config_path):
    """Resolve the kj-controller settings the cleanup needs."""
    candidates = [config_path] if config_path else [
        os.path.join(os.path.dirname(__file__), "..", "config.json"),
        "/opt/nomad/kjbox/kj-controller/config.json",
    ]
    for cand in candidates:
        if cand and os.path.exists(cand):
            with open(cand, encoding="utf-8") as fh:
                cfg = json.load(fh)
            cfg["_config_path"] = cand
            return cfg
    raise SystemExit(f"Could not find config.json (looked in: {candidates})")


def _rotation_db_path(cfg):
    p = cfg.get("rotation_db_path")
    return p if p else os.path.expanduser("~/kjdata/rotation.db")


def _index_entries(media_index_path):
    """Yield (path, filename, folder, is_download) from media_index.json."""
    if not os.path.exists(media_index_path):
        return []
    with open(media_index_path, encoding="utf-8") as fh:
        idx = json.load(fh)
    out = []
    for path, entry in idx.items():
        if not isinstance(entry, dict):
            continue
        out.append((
            path,
            entry.get("filename") or os.path.basename(path),
            entry.get("folder") or os.path.dirname(path),
            bool(entry.get("is_download")),
        ))
    return out


def _quarantine(path, reason, qdir):
    """Move a file and its same-stem sidecars into qdir; write a .reason.txt."""
    parent = os.path.dirname(path)
    base_no_ext = os.path.splitext(path)[0]
    os.makedirs(qdir, exist_ok=True)
    moved_main = None
    try:
        siblings = os.listdir(parent)
    except OSError:
        siblings = [os.path.basename(path)]
    for fname in siblings:
        full = os.path.join(parent, fname)
        if not os.path.isfile(full) or os.path.splitext(full)[0] != base_no_ext:
            continue
        dest = os.path.join(qdir, fname)
        shutil.move(full, dest)
        if os.path.abspath(full) == os.path.abspath(path):
            moved_main = dest
    if moved_main:
        with open(moved_main + ".reason.txt", "w", encoding="utf-8") as fh:
            fh.write((reason or "redundant") + "\n")
    return moved_main


def _write_reports(report_dir, rows):
    os.makedirs(report_dir, exist_ok=True)
    csv_path = os.path.join(report_dir, "cleanup_report.csv")
    md_path = os.path.join(report_dir, "cleanup_report.md")
    with open(csv_path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["action", "file", "detail"])
        w.writerows(rows)
    with open(md_path, "w", encoding="utf-8") as fh:
        fh.write("# Redundant-download cleanup report\n\n")
        fh.write("| action | file | detail |\n|---|---|---|\n")
        for action, f, detail in rows:
            fh.write(f"| {action} | {f} | {detail} |\n")
    return csv_path, md_path


def _trigger_rescan():
    try:
        import requests
        requests.post("http://127.0.0.1:5001/rescan", timeout=30)
        return True
    except Exception as exc:  # best-effort; print the manual fallback
        print(f"  (could not trigger /rescan automatically: {exc})")
        print("  Run a rescan from the KJ UI, or POST /rescan, to refresh the index.")
        return False


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--config", help="Path to kj-controller config.json")
    ap.add_argument("--execute", action="store_true",
                    help="Actually delete litter and quarantine twins (default: dry-run)")
    ap.add_argument("--report-dir", default=".",
                    help="Directory for CSV/MD report (default: cwd)")
    ap.add_argument("--no-rescan", action="store_true",
                    help="Skip the post-cleanup media index rescan")
    ap.add_argument("--relink-twins", action="store_true",
                    help="Re-link rotation entries/archive off a referenced twin onto its "
                         "master, then quarantine the twin (instead of skipping it)")
    args = ap.parse_args(argv)

    cfg = _load_config(args.config)
    download_folder = cfg["download_folder"]
    media_index_path = cfg["media_index_path"]
    rotation_db = _rotation_db_path(cfg)

    print(f"config:        {cfg['_config_path']}")
    print(f"download:      {download_folder}")
    print(f"media index:   {media_index_path}")
    print(f"rotation db:   {rotation_db}")
    print(f"mode:          {'EXECUTE' if args.execute else 'DRY-RUN (read-only)'}\n")

    # --- gather inputs ---
    try:
        disk_files = os.listdir(download_folder)
    except OSError as exc:
        raise SystemExit(f"Cannot list download folder: {exc}")
    litter_delete, orphans = plan_litter_removals(disk_files)

    entries = _index_entries(media_index_path)
    yt_playables = [fn for (p, fn, folder, is_dl) in entries
                    if is_dl and is_playable_filename(fn)]
    masters = [fn for (p, fn, folder, is_dl) in entries
               if not is_dl and master_song_key(fn)]
    twin_pairs = plan_twin_quarantines(yt_playables, masters)

    # full path for each YT twin + each master (from the index), for
    # rotation-safety, re-linking, and moves.
    yt_path_by_name = {fn: p for (p, fn, folder, is_dl) in entries}
    master_path_by_name = {fn: p for (p, fn, folder, is_dl) in entries if not is_dl}
    refs = referenced_file_paths(rotation_db)

    # Twins whose YT file is currently linked in a rotation entry/archive, with a
    # resolvable master to re-link onto.
    relink_remap = {}
    for yt_fn, master_fn in twin_pairs:
        yt_full = yt_path_by_name.get(yt_fn)
        master_full = master_path_by_name.get(master_fn)
        if yt_full and master_full and is_referenced(yt_full, refs):
            relink_remap[yt_full] = master_full

    relinked = 0
    if args.relink_twins and args.execute and relink_remap:
        relinked = relink_references(rotation_db, relink_remap)
        refs = referenced_file_paths(rotation_db)  # refresh: those are no longer linked

    twins_to_quarantine, twins_skipped = [], []
    for yt_fn, master_fn in twin_pairs:
        full = yt_path_by_name.get(yt_fn) or os.path.join(download_folder, yt_fn)
        if is_referenced(full, refs):
            twins_skipped.append((yt_fn, master_fn))
        else:
            twins_to_quarantine.append((yt_fn, master_fn, full))

    # --- report ---
    rows = []
    for f in litter_delete:
        rows.append(("delete-litter", f, "has same-id completed playable"))
    for f in orphans:
        rows.append(("orphan-flag", f, "FAILED download — no playable companion; re-download candidate"))
    for yt_fn, master_fn, _ in twins_to_quarantine:
        rows.append(("quarantine-twin", yt_fn, f"duplicate of master {master_fn}"))
    for yt_fn, master_fn in twins_skipped:
        action = "relink-then-quarantine" if args.relink_twins else "skip-referenced"
        detail = ("linked in a rotation entry — will re-link to master then quarantine"
                  if args.relink_twins
                  else "linked in a rotation entry — left in place")
        rows.append((action, yt_fn, detail))

    print(f"litter to delete:        {len(litter_delete)}")
    print(f"orphans (flag only):     {len(orphans)}")
    print(f"twins to quarantine:     {len(twins_to_quarantine)}")
    if args.relink_twins:
        print(f"twins re-linked:         {relinked} rotation row(s) "
              f"({'applied' if args.execute else 'would re-link ' + str(len(relink_remap)) + ' twin(s)'})")
        if not args.execute:
            print(f"twins still 'skipped':   {len(twins_skipped)} (re-link applies only with --execute)")
    else:
        print(f"twins skipped (in use):  {len(twins_skipped)}")
    csv_path, md_path = _write_reports(args.report_dir, rows)
    print(f"\nreport: {csv_path}\n        {md_path}")

    if not args.execute:
        print("\nDRY-RUN complete. Review the report, then re-run with --execute "
              "(between shows) to apply.")
        return 0

    # --- execute ---
    # Quarantine to a SIBLING of the download folder, not a subdir of it:
    # media.scan() only skips `_playability_quarantine`, so a quarantine dir
    # under an indexed media folder would be re-indexed on the next rescan and
    # the twins would reappear in search. A sibling lives outside every
    # media_folders path, so rescan can never pick it up.
    qdir = os.path.join(os.path.dirname(os.path.normpath(download_folder)), QUARANTINE_DIRNAME)
    deleted = moved = 0
    for f in litter_delete:
        full = os.path.join(download_folder, f)
        try:
            os.remove(full)
            deleted += 1
        except OSError as exc:
            print(f"  delete failed {f}: {exc}")
    for yt_fn, master_fn, full in twins_to_quarantine:
        try:
            if _quarantine(full, f"redundant: duplicate of master {master_fn}", qdir):
                moved += 1
        except OSError as exc:
            print(f"  quarantine failed {yt_fn}: {exc}")
    print(f"\nre-linked {relinked} rotation row(s); deleted {deleted} litter, "
          f"quarantined {moved} twins -> {qdir}")
    if not args.no_rescan:
        _trigger_rescan()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
