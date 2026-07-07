"""Review report: sort oracle results uncertain-first, generate waveforms + index.html."""

import argparse
import csv
import os
import subprocess

HERE = os.path.dirname(os.path.abspath(__file__))
RESULTS = os.path.join(HERE, "data", "oracle_results.csv")
REVIEW_CSV = os.path.join(HERE, "data", "review_picks.csv")
REVIEW_DIR = os.path.join(HERE, "data", "review")
TRACKS_ORG = "/Users/andrew/AB Dropbox/Andrew Beveridge/MediaUnsynced/Karaoke/Tracks-Organized"


def _rank(key):
    """Return (order, margin) tuple for sorting: uncertain first, by margin ascending."""
    verdict = key.get("verdict", "")
    confidence = key.get("confidence", "none")

    # Determine priority: no_source always first, then by confidence
    if verdict == "no_source":
        order = 0
    elif confidence == "low":
        order = 1
    elif confidence == "none":
        order = 2
    else:  # high or other
        order = 3

    # Extract margin, default to 1e9 if missing/invalid
    try:
        margin = float(key.get("margin_db") or 1e9)
    except ValueError:
        margin = 1e9

    return (order, margin)


def sort_for_review(rows: list[dict]) -> list[dict]:
    """Sort rows uncertain-first: no_source, then low-confidence by margin, then high."""
    return sorted(rows, key=_rank)


def _waveform_png(src: str, dst: str) -> bool:
    """Generate waveform PNG using ffmpeg showwavespic.

    Returns:
        True if PNG was generated successfully, False otherwise.
    """
    try:
        r = subprocess.run(
            [
                "ffmpeg", "-y", "-v", "error", "-i", src,
                "-filter_complex", "showwavespic=s=640x120",
                "-frames:v", "1", dst
            ],
            capture_output=True, timeout=60
        )
    except subprocess.TimeoutExpired:
        print(f"  WARN waveform timed out for {src}")
        return False
    if r.returncode != 0:
        print(f"  WARN waveform failed for {src}")
        return False
    return True


def main(argv=None) -> int:
    """Read oracle_results.csv, write sorted review_picks.csv, generate waveforms + index."""
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--results", default=RESULTS)
    args = ap.parse_args(argv)

    # Read and sort results
    with open(args.results, newline="") as f:
        rows = sort_for_review(list(csv.DictReader(f)))

    if not rows:
        print("no rows in results CSV; nothing to review")
        return 0

    # Write sorted CSV
    with open(REVIEW_CSV, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=rows[0].keys())
        w.writeheader()
        w.writerows(rows)

    # Create review directory
    os.makedirs(REVIEW_DIR, exist_ok=True)

    # Generate waveforms and HTML cards for flagged subset
    flagged = [r for r in rows if r["verdict"] == "no_source" or r["confidence"] == "low"]
    cards = []

    for r in flagged:
        png = os.path.join(REVIEW_DIR, f"{r['brand']}.png")
        made = False
        if r["winner_rel"]:
            made = _waveform_png(os.path.join(TRACKS_ORG, r["winner_rel"]), png)
        img_html = (f"<img src='{r['brand']}.png' style='max-width:640px;max-height:120px'>"
                    if made else "<i>(no waveform)</i>")
        cards.append(
            f"<div style='margin:8px;font-family:sans-serif'>"
            f"<b>{r['brand']}</b> [{r['verdict']}/{r['confidence']}] "
            f"win={r['winner_db']}dB runnerup={r['runnerup_db']}dB margin={r['margin_db']}<br>"
            f"{img_html}</div>"
        )

    # Write index.html
    html = (
        "<!DOCTYPE html><html><head><meta charset='utf-8'>"
        "<title>Review: Uncertain Originals</title></head><body>"
        f"<h1>Review: Uncertain Originals ({len(cards)} items)</h1>"
        + "".join(cards) +
        "</body></html>"
    )
    with open(os.path.join(REVIEW_DIR, "index.html"), "w") as f:
        f.write(html)

    return 0


if __name__ == "__main__":
    exit(main())
