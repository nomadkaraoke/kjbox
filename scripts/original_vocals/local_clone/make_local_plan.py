#!/usr/bin/env python3
"""Turn the classifier manifest into a local-clone fetch plan:

    <brand>\t<local_dropbox_path>\t<dest_filename>

for every auto-fetchable (HIGH/MED/LOW) row, sorted by NOMAD number (earliest
first). `local_dropbox_path` points into the Mac's local Dropbox clone; feed the
plan to fetch_local.sh.

Usage:
  make_local_plan.py --manifest ../data/manifest.csv \
      --dropbox-root "/Users/andrew/AB Dropbox/Andrew Beveridge/MediaUnsynced/Karaoke/Tracks-Organized" \
      > local_fetch.tsv
"""
import argparse
import csv
import re
import sys

BRAND = re.compile(r"^(NOMAD-\d+)\b\s*-?\s*(.*)$")


def dest_name(brand: str, chosen_path: str) -> str:
    folder = chosen_path.split("/", 1)[0]
    m = BRAND.match(folder)
    top = m.group(2).strip() if m else ""
    ext = chosen_path.rsplit(".", 1)[-1]
    base = f"{brand} - {top}".strip().rstrip(" .")
    base = re.sub(r"[/\x00-\x1f]", "_", base)
    return f"{base}.{ext}"


def brand_num(brand: str) -> int:
    m = re.search(r"(\d+)", brand)
    return int(m.group(1)) if m else 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--manifest", required=True)
    ap.add_argument("--dropbox-root", required=True,
                    help="local path to the Tracks-Organized folder in the Mac Dropbox clone")
    args = ap.parse_args(argv)

    root = args.dropbox_root.rstrip("/")
    rows = []
    with open(args.manifest, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            if r["tier"] in ("HIGH", "MED", "LOW") and r["chosen_path"]:
                rows.append((r["brand_code"],
                             f"{root}/{r['chosen_path']}",
                             dest_name(r["brand_code"], r["chosen_path"])))
    rows.sort(key=lambda x: brand_num(x[0]))
    for b, s, d in rows:
        sys.stdout.write(f"{b}\t{s}\t{d}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
