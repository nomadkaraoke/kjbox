#!/usr/bin/env python3
"""Copy verified originals into the Dropbox source-of-truth folder, renamed
NOMAD-#### - Artist - Title.<ext>. Eligible = confirmed AND (high-confidence OR
human-approved in the results CSV `approved` column). Resumable: skips dests
that already exist. Materializes each source first (Dropbox online-only).

Usage (from scripts/original_vocals/):
  python oracle_assemble.py            # copy eligible picks
  python oracle_assemble.py --dry-run  # print the copy plan only
"""
from __future__ import annotations
import argparse
import csv
import os
import shutil
import subprocess

from classify import safe_dst_name

HERE = os.path.dirname(os.path.abspath(__file__))
TRACKS_ORG = "/Users/andrew/AB Dropbox/Andrew Beveridge/MediaUnsynced/Karaoke/Tracks-Organized"
DEST_DIR = "/Users/andrew/AB Dropbox/Andrew Beveridge/MediaUnsynced/Karaoke/Tracks-Audio/Original"
MATERIALIZE = os.path.join(HERE, "local_clone", "materialize")


def _eligible(r: dict) -> bool:
    return r["verdict"] == "confirmed" and bool(r["winner_rel"]) and (
        r["confidence"] == "high" or (r.get("approved") or "").strip().lower() in ("y", "yes", "1"))


def plan_copies(results_rows: list[dict], manifest_map: dict[str, dict],
                dest_dir: str) -> list[tuple[str, str]]:
    plan: list[tuple[str, str]] = []
    for r in results_rows:
        if not _eligible(r):
            continue
        m = manifest_map.get(r["brand"])
        if not m:
            continue
        dst_name = safe_dst_name(r["brand"], f"{m['artist']} - {m['title']}", r["winner_ext"])
        plan.append((os.path.join(TRACKS_ORG, r["winner_rel"]),
                     os.path.join(dest_dir, dst_name)))
    return plan


def _load(path: str) -> list[dict]:
    with open(path, newline="") as f:
        return list(csv.DictReader(f))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--results", default=os.path.join(HERE, "data", "oracle_results.csv"))
    ap.add_argument("--manifest", default=os.path.join(HERE, "data", "manifest.csv"))
    ap.add_argument("--dest", default=DEST_DIR)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    manifest_map = {r["brand_code"]: r for r in _load(args.manifest)}
    plan = plan_copies(_load(args.results), manifest_map, args.dest)
    os.makedirs(args.dest, exist_ok=True)
    copied = skipped = 0
    for src, dst in plan:
        if os.path.exists(dst):
            skipped += 1
            continue
        print(("DRY " if args.dry_run else "COPY ") + f"{os.path.basename(dst)}")
        if not args.dry_run:
            subprocess.run([MATERIALIZE, src], capture_output=True)
            shutil.copy2(src, dst)
            copied += 1
    print(f"assemble: {copied} copied, {skipped} already present, {len(plan)} eligible")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
