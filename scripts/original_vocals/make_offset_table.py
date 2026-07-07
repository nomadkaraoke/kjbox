#!/usr/bin/env python3
"""Turn the phase-2 sync report into the offset table the playback feature reads.

Output JSON: { "NOMAD-0001": {"offset_s": 5.02, "verdict": "confirmed", "peak": 0.85}, ... }

Phase 4 offers the guide only for `confirmed` brands and pads the guide by
`offset_s` (mpv adelay). `needs-review`/`error` brands are included with their
verdict so the app can withhold the guide (and so we have one file listing what
still needs a fixed input).

Usage: make_offset_table.py <sync_report.csv> -o offsets.json
"""
import argparse
import csv
import json


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("report")
    ap.add_argument("-o", "--out", default="offsets.json")
    args = ap.parse_args(argv)

    table, counts = {}, {"confirmed": 0, "needs-review": 0, "error": 0}
    for r in csv.DictReader(open(args.report)):
        brand = r["brand_code"]
        verdict = r.get("verdict", "")
        counts[verdict] = counts.get(verdict, 0) + 1
        entry = {"verdict": verdict}
        try:
            entry["offset_s"] = round(float(r["offset_s"]), 3)
            entry["peak"] = round(float(r["peak"]), 3)
        except (TypeError, ValueError):
            pass
        table[brand] = entry

    with open(args.out, "w") as f:
        json.dump(table, f, indent=0, sort_keys=True)
    print(f"wrote {len(table)} entries -> {args.out}")
    print("  " + "  ".join(f"{k}={v}" for k, v in counts.items()))
    review = sorted(b for b, e in table.items() if e["verdict"] != "confirmed")
    if review:
        print(f"\nnot confirmed ({len(review)}) — need a re-checked input before they can offer a guide:")
        print("  " + " ".join(review))


if __name__ == "__main__":
    main()
