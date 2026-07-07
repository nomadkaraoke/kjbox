#!/usr/bin/env python3
"""Flag tracks whose separated "vocals" are nearly silent — a strong sign the
wrong input file was picked in phase 1 (an already-separated INSTRUMENTAL rather
than the original full mix), so the separator had no vocals to extract.

Reads vocals_diagnostics.csv (written by separate_vocals.sh) and ranks tracks by
how weak their vocals are, using two independent signals:
  - vocals_mean_db : mean loudness of the vocals stem. A real vocal has mean loud
    (~ -3 to -20 dB); an instrumental's residual bleed has mean quiet (< ~ -35 dB).
  - bytes-per-second: a vocals stem is mostly silence between phrases so it
    flac-compresses small, but an essentially-empty stem is *tiny*.

Neither is perfect alone (a genuinely quiet mix can look weak), so we flag on the
combination and sort worst-first for a human to re-check the input file.

Usage: flag_weak_vocals.py [--diag vocals_diagnostics.csv] [--mean-db -35] [--bps 1500]
"""
import argparse
import csv
import os


def fnum(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def main(argv=None):
    here = os.path.dirname(__file__)
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--diag", default=os.path.join(here, "vocals_diagnostics.csv"))
    ap.add_argument("--mean-db", type=float, default=-35.0,
                    help="flag if vocals mean is quieter than this (default -35 dB)")
    ap.add_argument("--bps", type=float, default=1500.0,
                    help="flag if vocals flac bytes/sec is below this (default 1500)")
    ap.add_argument("--out", default=os.path.join(here, "weak_vocals_review.csv"))
    args = ap.parse_args(argv)

    if not os.path.exists(args.diag):
        raise SystemExit(f"no diagnostics yet at {args.diag} (run separate_vocals.sh first)")

    rows = list(csv.DictReader(open(args.diag)))
    flagged = []
    for r in rows:
        vmean = fnum(r.get("vocals_mean_db"))
        vbytes = fnum(r.get("vocals_bytes")) or 0
        dur = fnum(r.get("dur_s")) or 0
        bps = (vbytes / dur) if dur else 0
        low_mean = vmean is not None and vmean < args.mean_db
        tiny = dur and bps < args.bps
        if low_mean or tiny:
            reasons = []
            if low_mean:
                reasons.append(f"mean {vmean:.1f}dB")
            if tiny:
                reasons.append(f"{bps:.0f} B/s")
            # severity: how far below the mean threshold + how tiny
            sev = (args.mean_db - (vmean if vmean is not None else args.mean_db)) + \
                  max(0, (args.bps - bps) / args.bps) * 10
            flagged.append((sev, r["brand"], r.get("dest", ""), vmean, bps, "; ".join(reasons)))

    flagged.sort(reverse=True)
    with open(args.out, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["brand", "dest", "vocals_mean_db", "vocals_bytes_per_s", "why"])
        for _, brand, dest, vmean, bps, why in flagged:
            w.writerow([brand, dest, f"{vmean:.1f}" if vmean is not None else "", f"{bps:.0f}", why])

    print(f"analysed {len(rows)} separated tracks")
    print(f"flagged {len(flagged)} with minimal vocals (likely wrong input) -> {args.out}")
    print("\nworst 15 (re-check the phase-1 input file for these):")
    for _, brand, dest, vmean, bps, why in flagged[:15]:
        print(f"  {brand:12} {why}")


if __name__ == "__main__":
    main()
