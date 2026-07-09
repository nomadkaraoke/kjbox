"""Render review clips: the first `--dur` seconds of each master video from t=0,
with the original-vocal guide mixed in delayed by the candidate offset (adelay).
This mirrors the emit (silence[offset]+guide) exactly, so the clip's alignment IS
the emitted alignment — shown from the title card onward, which is easy to reason
about. Run ON the device (ffmpeg). Pure selection/naming/cmd are unit-tested."""
import argparse, csv, hashlib, os, subprocess, sys
from align_core import read_offsets, variant_offsets

FFMPEG = "ffmpeg"


def _stable_rank(brand, seed):
    return int(hashlib.sha1(f"{seed}:{brand}".encode()).hexdigest(), 16)


def select_review(rows, spot_frac=0.07, seed=1):
    flagged = [b for b, r in rows.items() if r.status == "active" and r.verdict == "needs-review"]
    confirmed = [b for b, r in rows.items() if r.status == "active" and r.verdict == "confirmed"]
    k = max(1, int(round(len(confirmed) * spot_frac))) if confirmed else 0
    spot = sorted(confirmed, key=lambda b: _stable_rank(b, seed))[:k]
    return sorted(flagged), sorted(spot)


def clip_name(brand, artist_title, candidate_s):
    safe = artist_title.replace("/", "_")
    return f"{brand} - {safe}__off={candidate_s:.3f}s.mp4"


def ffmpeg_clip_cmd(video, guide, offset_s, dur, out, gain=0.65):
    """First `dur`s of the video from t=0 with the guide mixed in, delayed by
    offset_s (adelay). Mirrors emit_padded (silence[offset]+guide). Video is
    stream-copied (fast); only the mixed audio is re-encoded."""
    delay_ms = int(round(offset_s * 1000))
    return [FFMPEG, "-y", "-v", "error",
            "-t", f"{dur:.3f}", "-i", video,
            "-i", guide,
            "-filter_complex",
            f"[1:a]adelay={delay_ms}:all=1,volume={gain}[g];"
            f"[0:a][g]amix=inputs=2:normalize=0:duration=first[a]",
            "-map", "0:v", "-map", "[a]", "-t", f"{dur:.3f}",
            "-c:v", "copy", "-c:a", "aac", out]


def _title_from(name):
    base = os.path.splitext(os.path.basename(name))[0]
    return base.split(" - ", 1)[1] if " - " in base else base


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--offsets", default="align_offsets.csv")
    ap.add_argument("--video-dir", required=True)
    ap.add_argument("--guide-dir", required=True)
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--dur", type=float, default=60.0, help="clip length from t=0 (default 60s)")
    ap.add_argument("--fine", action="store_true", help="fine comb [0,±25,±50]ms (needs-finer round)")
    ap.add_argument("--only", nargs="*", help="restrict to these brands (variant round)")
    a = ap.parse_args(argv)
    import glob
    import verify_sync as vs
    rows = read_offsets(a.offsets)
    vidx = vs._index_by_brand(glob.glob(os.path.join(a.video_dir, "*")))
    gidx = vs._index_by_brand(glob.glob(os.path.join(a.guide_dir, "*")))
    os.makedirs(a.out_dir, exist_ok=True)
    # Default first pass: one clip per reviewed track at the measured offset.
    # A variant comb is only rendered for an explicit follow-up round (--only / --fine).
    do_variants = bool(a.only or a.fine)
    steps = (0, -25, 25, -50, 50) if a.fine else (0, -100, 100, -200, 200)
    flagged, spot = select_review(rows)
    review = (a.only if a.only else flagged + spot)
    dec_rows = []
    for b in review:
        r = rows.get(b)
        if not r or b not in vidx or b not in gidx:
            print(f"WARN {b}: skipped (missing offsets row or video/guide file)", file=sys.stderr)
            continue
        cands = variant_offsets(r.offset_s, steps) if do_variants else [r.offset_s]
        ok = 0
        for cand in cands:
            out = os.path.join(a.out_dir, clip_name(b, _title_from(vidx[b]), cand))
            try:
                rc = subprocess.run(ffmpeg_clip_cmd(vidx[b], gidx[b], cand, a.dur, out),
                                    timeout=180).returncode
            except subprocess.TimeoutExpired:
                rc = -1
                print(f"WARN {b}: clip render timed out (offset {cand:.3f}s)", file=sys.stderr)
            if rc == 0:
                ok += 1
            else:
                print(f"WARN {b}: clip render failed (offset {cand:.3f}s, ffmpeg rc={rc})", file=sys.stderr)
        dec_rows.append({"brand": b, "verdict": r.verdict, "measured_offset_s": r.offset_s,
                         "decision": ""})
        print(f"{b}: {ok}/{len(cands)} clip(s)")
    with open(os.path.join(a.out_dir, "align_decisions.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["brand", "verdict", "measured_offset_s", "decision"])
        w.writeheader(); w.writerows(dec_rows)
    print(f"\n{len(dec_rows)} tracks to review -> {a.out_dir} (fill 'decision': confirm|offset_ms=<n>|exclude|needs-finer)")


if __name__ == "__main__":
    main()
