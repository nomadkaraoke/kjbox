#!/usr/bin/env python3
"""Resumable oracle driver: orchestrate candidate -> measure -> pick -> results CSV.

Usage: oracle_run.py --zone full/audit/all [--brands CODE,CODE] [--limit N]

Globs Tracks-Organized per brand, enumerates candidates, materializes + separates
each, measures vocals stem volume, picks winner, appends row to data/oracle_results.csv.
Resumable: skips brands already in the CSV (load_done_brands).
"""
import argparse
import csv
import glob
import os
import subprocess
import sys

from classify import _brand_num
from oracle_candidates import enumerate_candidates
from oracle_measure import separate_and_measure
from oracle_pick import Candidate, pick_winner
from oracle_zones import full_oracle_brands, audit_sample

HERE = os.path.dirname(os.path.abspath(__file__))
TRACKS_ORG = "/Users/andrew/AB Dropbox/Andrew Beveridge/MediaUnsynced/Karaoke/Tracks-Organized"
SEP_BIN = "/Users/andrew/miniforge3/envs/nomadkaraoke/bin/audio-separator"
MODEL = "2_HP-UVR.pth"
MATERIALIZE = os.path.join(HERE, "local_clone", "materialize")
RESULTS = os.path.join(HERE, "data", "oracle_results.csv")
WORKDIR = "/tmp/ov_oracle_work"
FIELDS = ["brand", "folder", "verdict", "confidence", "winner_name", "winner_ext",
          "winner_rel", "winner_db", "runnerup_db", "margin_db", "n_candidates", "approved"]


def folder_for_brand(tracks_org: str, brand: str) -> str | None:
    """Glob Tracks-Organized for brand folder; return path or None."""
    hits = sorted(glob.glob(os.path.join(tracks_org, f"{brand} *")) +
                  glob.glob(os.path.join(tracks_org, brand)))
    return hits[0] if hits else None


def load_done_brands(results_csv: str) -> set[str]:
    """Load set of brands already in results CSV."""
    if not os.path.exists(results_csv):
        return set()
    with open(results_csv, newline="") as f:
        return {r["brand"] for r in csv.DictReader(f)}


def _load_manifest_rows() -> list[dict]:
    """Load manifest.csv rows."""
    with open(os.path.join(HERE, "data", "manifest.csv"), newline="") as f:
        return list(csv.DictReader(f))


def _brands_for_zone(zone: str) -> list[str]:
    """Return sorted brand list for zone: full | audit | all."""
    rows = _load_manifest_rows()
    if zone == "full":
        return full_oracle_brands(rows)
    elif zone == "audit":
        return audit_sample(rows)
    elif zone == "all":
        return sorted({r["brand_code"] for r in rows}, key=_brand_num)
    else:
        raise SystemExit(f"unknown zone {zone!r}")


def process_brand(brand: str) -> dict | None:
    """Process one brand: enumerate, measure, pick, return result row or None on error."""
    folder = folder_for_brand(TRACKS_ORG, brand)
    if not folder:
        print(f"  {brand}: NO FOLDER", file=sys.stderr)
        return None

    # Enumerate candidates in the folder
    candidates = enumerate_candidates(folder)
    if not candidates:
        print(f"  {brand}: no candidates")
        return None

    # Separate + measure each
    measured = []
    for i, cand in enumerate(candidates, 1):
        print(f"    [{i}/{len(candidates)}] {cand.name}...", end="", flush=True)
        try:
            mres = subprocess.run([MATERIALIZE, cand.path], capture_output=True, timeout=300)
            if mres.returncode != 0:
                print(f"  WARN materialize failed for {cand.name} (candidate may read as empty)", file=sys.stderr)
        except (subprocess.TimeoutExpired, FileNotFoundError) as e:
            print(f"  WARN materialize error for {cand.name}: {e}", file=sys.stderr)
        mean_db = separate_and_measure(cand.path, WORKDIR, SEP_BIN, MODEL)
        if mean_db is not None:
            measured.append(Candidate(
                path=cand.path, name=cand.name, ext=cand.ext,
                size=cand.size, mean_db=mean_db
            ))
            print(f" {mean_db:.1f}dB")
        else:
            print(f" (failed)")

    if not measured:
        print(f"  {brand}: no successful measurements")
        return None

    # Pick winner
    result = pick_winner(measured)
    if not result.winner:
        return {
            "brand": brand,
            "folder": os.path.basename(folder),
            "verdict": result.verdict,
            "confidence": result.confidence,
            "winner_name": "",
            "winner_ext": "",
            "winner_rel": "",
            "winner_db": "",
            "runnerup_db": "",
            "margin_db": "",
            "n_candidates": len(measured),
            "approved": "",
        }

    # Compute winner_rel: path relative to Tracks-Organized
    winner_rel = os.path.relpath(result.winner.path, TRACKS_ORG)

    return {
        "brand": brand,
        "folder": os.path.basename(folder),
        "verdict": result.verdict,
        "confidence": result.confidence,
        "winner_name": result.winner.name,
        "winner_ext": result.winner.ext,
        "winner_rel": winner_rel,
        "winner_db": f"{result.winner_db:.1f}" if result.winner_db is not None else "",
        "runnerup_db": f"{result.runnerup_db:.1f}" if result.runnerup_db is not None else "",
        "margin_db": f"{result.margin_db:.1f}" if result.margin_db is not None else "",
        "n_candidates": len(measured),
        "approved": "",
    }


def main(argv=None) -> int:
    """Main entry point."""
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--zone", choices=["full", "audit", "all"])
    ap.add_argument("--brands", help="comma-separated codes (overrides --zone)")
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args(argv)

    if args.brands:
        brands = [b.strip() for b in args.brands.split(",") if b.strip()]
    elif args.zone:
        brands = _brands_for_zone(args.zone)
    else:
        raise SystemExit("pass --zone or --brands")

    os.makedirs(os.path.join(HERE, "data"), exist_ok=True)
    os.makedirs(WORKDIR, exist_ok=True)
    done = load_done_brands(RESULTS)
    todo = [b for b in brands if b not in done]
    if args.limit:
        todo = todo[: args.limit]
    print(f"oracle: {len(todo)} folders to process ({len(done)} already done)")

    new = not os.path.exists(RESULTS)
    with open(RESULTS, "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        if new:
            w.writeheader()
        for i, brand in enumerate(todo, 1):
            print(f"[{i}/{len(todo)}] {brand}")
            row = process_brand(brand)
            if row:
                w.writerow(row)
                f.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
