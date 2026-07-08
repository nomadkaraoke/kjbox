"""Merge human decisions into align_offsets.csv, then emit aligned guides to the
padded dir (active) and remove guides for excluded tracks. Run ON the device (ffmpeg)."""
import argparse, csv, glob, os, subprocess
from align_core import read_offsets, write_offsets, parse_decision, apply_decision, emit_af

FFMPEG = "ffmpeg"


def merge_decisions(rows, decisions):
    finer = []
    for d in decisions:
        b = d["brand"]; val = d.get("decision", "").strip()
        if not val or b not in rows:
            continue
        kind, off = parse_decision(val)
        if kind == "needs-finer":
            finer.append(b); continue
        rows[b] = apply_decision(rows[b], kind, off)
    return rows, finer


def emit_cmd(guide, out, offset_s, video_dur):
    # -f flac forces the flac muxer regardless of the output filename — main()
    # emits to a "<name>.part" temp file, and ffmpeg would otherwise guess the
    # container from the ".part" extension and fail ("Invalid argument").
    return [FFMPEG, "-v", "error", "-y", "-i", guide,
            "-af", emit_af(offset_s, video_dur), "-c:a", "flac", "-f", "flac", out]


def _index(d):
    import re
    idx = {}
    for p in glob.glob(os.path.join(d, "*")):
        m = re.match(r"(NOMAD-\d{4})", os.path.basename(p))
        if m:
            idx.setdefault(m.group(1), p)
    return idx


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--offsets", default="align_offsets.csv")
    ap.add_argument("--decisions", default=None)
    ap.add_argument("--raw-dir", required=True)       # NOMAD-vocals
    ap.add_argument("--padded-dir", required=True)    # NOMAD-vocals-padded
    a = ap.parse_args(argv)
    rows = read_offsets(a.offsets)
    if a.decisions and os.path.exists(a.decisions):
        with open(a.decisions, encoding="utf-8") as f:
            rows, finer = merge_decisions(rows, list(csv.DictReader(f)))
        write_offsets(a.offsets, rows)
        if finer:
            print("needs-finer:", " ".join(finer))
    raw = _index(a.raw_dir); pad = _index(a.padded_dir)
    os.makedirs(a.padded_dir, exist_ok=True)
    emitted = excluded = 0
    for b, r in rows.items():
        if r.status == "excluded":
            for idx in (raw, pad):
                if b in idx:
                    os.remove(idx[b]); 
            excluded += 1
            print("EXCLUDE", b)
            continue
        if b not in raw:
            continue
        base = os.path.splitext(os.path.basename(raw[b]))[0]
        out = os.path.join(a.padded_dir, base + ".flac")
        subprocess.run(emit_cmd(raw[b], out + ".part", r.offset_s, r.video_dur), check=True)
        os.replace(out + ".part", out)
        emitted += 1
    print(f"emitted {emitted} aligned guides; excluded {excluded}. (Remove excluded from Mac copies too.)")


if __name__ == "__main__":
    main()
