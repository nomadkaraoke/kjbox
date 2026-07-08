"""Measure per-track offset (via verify_sync) + guide first-vocal-onset -> align_offsets.csv.
Run ON the device (numpy required)."""
import argparse, glob, os, sys
import verify_sync as vs
from align_core import OffsetRow, first_vocal_onset, write_offsets


def build_offset_row(sr, onset_s):
    return OffsetRow(sr.brand_code, round(sr.offset_s, 3), round(sr.peak, 3), sr.verdict,
                     round(sr.video_dur, 3), round(sr.audio_dur, 3), round(onset_s, 3),
                     "measured", "active")


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--audio-dir", required=True)
    ap.add_argument("--video-dir", required=True)
    ap.add_argument("--guide-dir", required=True)          # NOMAD-vocals (raw)
    ap.add_argument("--out", default="align_offsets.csv")
    ap.add_argument("--only", default=None)
    a = ap.parse_args(argv)
    aidx = vs._index_by_brand(glob.glob(os.path.join(a.audio_dir, "*")))
    vidx = vs._index_by_brand(glob.glob(os.path.join(a.video_dir, "*")))
    gidx = vs._index_by_brand(glob.glob(os.path.join(a.guide_dir, "*")))
    brands = sorted(set(aidx) & set(vidx) & set(gidx))
    if a.only:
        brands = [b for b in brands if b.upper() == a.only.upper()]
    rows = {}
    for b in brands:
        try:
            sr = vs.verify_pair(vidx[b], aidx[b])
            g = vs.decode_mono(gidx[b], dur=90)
            onset = first_vocal_onset(g, vs.ANALYSIS_SR)
            rows[b] = build_offset_row(sr, onset)
            print(f"{b}: {sr.verdict:12s} off={sr.offset_s:.3f}s peak={sr.peak:.2f} onset={onset:.2f}s")
        except Exception as e:
            print(f"{b}: ERROR {e}", file=sys.stderr)
    write_offsets(a.out, rows)
    conf = sum(1 for r in rows.values() if r.verdict == "confirmed")
    print(f"\n{conf}/{len(rows)} confirmed -> {a.out}")


if __name__ == "__main__":
    main()
