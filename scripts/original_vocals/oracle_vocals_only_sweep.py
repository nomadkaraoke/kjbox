#!/usr/bin/env python3
"""Flag vocals-only false-positive picks: re-separate each risk-bucket winner and
measure its INSTRUMENTAL stem. A real mixed original has a loud instrumental; a
vocals-only stem separates to a near-silent instrumental -> the pick was wrong.

Risk buckets (full-oracle zone only): confirmed AND (confidence==high OR
single-candidate low, i.e. blank margin). Close-margin lows were ear-reviewed
already; format-dups (~0 margin) are verified same-recording mix pairs.

Resumable: skips brands already in data/vocals_only_sweep.csv.
"""
import csv, os, re, glob, shutil, subprocess

HERE = os.path.dirname(os.path.abspath(__file__))
TORG = "/Users/andrew/AB Dropbox/Andrew Beveridge/MediaUnsynced/Karaoke/Tracks-Organized"
SEP = "/Users/andrew/miniforge3/envs/nomadkaraoke/bin/audio-separator"
MODEL = "2_HP-UVR.pth"
MAT = os.path.join(HERE, "local_clone", "materialize")
WORK = "/tmp/ov_sweep_work"
SNAP = os.path.join(HERE, "data", "oracle_results_snapshot.csv")
OUT = os.path.join(HERE, "data", "vocals_only_sweep.csv")
FIELDS = ["brand", "winner_name", "vocals_db", "instrumental_db", "flag"]
DEAD_INSTRUMENTAL = -40.0   # instrumental quieter than this => pick is vocals-only


def _mean_db(path):
    try:
        r = subprocess.run(["ffmpeg", "-hide_banner", "-i", path, "-filter:a",
                            "volumedetect", "-f", "null", "/dev/null"],
                           capture_output=True, text=True, timeout=120)
    except subprocess.TimeoutExpired:
        return None
    m = re.search(r"mean_volume:\s*([-0-9.]+)\s*dB", r.stderr)
    return float(m.group(1)) if m else None


def _stems(audio):
    out = os.path.join(WORK, "out")
    shutil.rmtree(out, ignore_errors=True); os.makedirs(out, exist_ok=True)
    env = dict(os.environ)
    env.setdefault("AUDIO_SEPARATOR_MODEL_DIR",
                   "/Volumes/AndrewMacSD/python-audio-separator-models-repo")
    try:
        r = subprocess.run([SEP, audio, "--model_filename", MODEL,
                            "--output_dir", out, "--output_format", "flac"],
                           capture_output=True, text=True, env=env, timeout=300)
        if r.returncode != 0:
            return None, None
        voc = inst = None
        for f in glob.glob(os.path.join(out, "*.flac")):
            b = os.path.basename(f).lower()
            if "(vocals)" in b: voc = f
            elif "(instrumental)" in b: inst = f
        return (_mean_db(voc) if voc else None), (_mean_db(inst) if inst else None)
    finally:
        shutil.rmtree(out, ignore_errors=True)


def _num(b):
    m = re.search(r"(\d+)", b); return int(m.group(1)) if m else 0


def _is_risk(r):
    if r["verdict"] != "confirmed":
        return False
    if r["confidence"] == "high":
        return True
    return r["confidence"] == "low" and not (r["margin_db"] or "").strip()


def main():
    rows = [r for r in csv.DictReader(open(SNAP)) if _num(r["brand"]) < 449]
    risk = [r for r in rows if _is_risk(r)]
    done = set()
    if os.path.exists(OUT):
        done = {x["brand"] for x in csv.DictReader(open(OUT))}
    todo = [r for r in risk if r["brand"] not in done]
    os.makedirs(WORK, exist_ok=True)
    new = not os.path.exists(OUT) or os.path.getsize(OUT) == 0
    f = open(OUT, "a", newline=""); w = csv.DictWriter(f, fieldnames=FIELDS)
    if new: w.writeheader()
    print(f"sweep: {len(todo)} risk winners to check ({len(done)} already done)")
    for i, r in enumerate(todo, 1):
        src = os.path.join(TORG, r["winner_rel"])
        subprocess.run([MAT, src], capture_output=True)
        vd, idb = _stems(src)
        flag = "VOCALS_ONLY" if (idb is not None and idb < DEAD_INSTRUMENTAL) else ("?" if idb is None else "ok")
        w.writerow({"brand": r["brand"], "winner_name": r["winner_name"],
                    "vocals_db": f"{vd:.1f}" if vd is not None else "",
                    "instrumental_db": f"{idb:.1f}" if idb is not None else "",
                    "flag": flag}); f.flush()
        print(f"[{i}/{len(todo)}] {r['brand']} inst={idb} vox={vd} -> {flag}")
    f.close()
    print("sweep complete")


if __name__ == "__main__":
    main()
