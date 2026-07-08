"""Render pre-rendered A/V review clips (guide mixed into video at candidate offsets)
+ a decisions template. Run ON the device (ffmpeg). Pure selection/命名/cmd are unit-tested."""
import argparse, csv, hashlib, os, subprocess
from align_core import read_offsets, variant_offsets, clip_cut

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


def ffmpeg_clip_cmd(video, guide, video_start, guide_start, dur, out, gain=0.65):
    return [FFMPEG, "-y", "-v", "error",
            "-ss", f"{video_start:.3f}", "-t", f"{dur:.3f}", "-i", video,
            "-ss", f"{guide_start:.3f}", "-t", f"{dur:.3f}", "-i", guide,
            "-filter_complex",
            f"[1:a]volume={gain}[g];[0:a][g]amix=inputs=2:normalize=0[a]",
            "-map", "0:v", "-map", "[a]", "-shortest",
            "-c:v", "libx264", "-preset", "veryfast", "-c:a", "aac", out]


def _title_from(name):
    base = os.path.splitext(os.path.basename(name))[0]
    return base.split(" - ", 1)[1] if " - " in base else base


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--offsets", default="align_offsets.csv")
    ap.add_argument("--video-dir", required=True)
    ap.add_argument("--guide-dir", required=True)
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--fine", action="store_true", help="fine comb [0,±25,±50]ms (for needs-finer round)")
    ap.add_argument("--only", nargs="*", help="restrict to these brands (fine round)")
    a = ap.parse_args(argv)
    import glob
    import verify_sync as vs
    rows = read_offsets(a.offsets)
    vidx = vs._index_by_brand(glob.glob(os.path.join(a.video_dir, "*")))
    gidx = vs._index_by_brand(glob.glob(os.path.join(a.guide_dir, "*")))
    os.makedirs(a.out_dir, exist_ok=True)
    steps = (0, -25, 25, -50, 50) if a.fine else (0, -100, 100, -200, 200)
    flagged, spot = select_review(rows)
    review = (a.only if a.only else flagged + spot)
    dec_rows = []
    for b in review:
        r = rows.get(b)
        if not r or b not in vidx or b not in gidx:
            continue
        cands = variant_offsets(r.offset_s, steps) if (r.verdict == "needs-review" or a.fine) else [r.offset_s]
        for cand in cands:
            vstart, gstart, dur = clip_cut(r.offset_s, r.onset_s, cand)
            out = os.path.join(a.out_dir, clip_name(b, _title_from(vidx[b]), cand))
            subprocess.run(ffmpeg_clip_cmd(vidx[b], gidx[b], vstart, gstart, dur, out),
                           check=False)
        dec_rows.append({"brand": b, "verdict": r.verdict, "measured_offset_s": r.offset_s,
                         "decision": ""})
        print(f"{b}: {len(cands)} clip(s)")
    with open(os.path.join(a.out_dir, "align_decisions.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["brand", "verdict", "measured_offset_s", "decision"])
        w.writeheader(); w.writerows(dec_rows)
    print(f"\n{len(dec_rows)} tracks to review -> {a.out_dir} (fill 'decision': confirm|offset_ms=<n>|exclude|needs-finer)")


if __name__ == "__main__":
    main()
