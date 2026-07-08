#!/usr/bin/env python3
"""M2: build the isolated-vocals guide dataset -> Tracks-Audio/Vocals.

Router per addressable NOMAD brand:
  - REUSE an in-folder full-vocals stem IFF: brand not source-corrected AND a
    full-vocals stem exists AND its duration matches the M1 original (+/-0.5s).
  - else SEPARATE FRESH from the M1 original (Tracks-Audio/Original) with 2_HP-UVR.pth.

Main-vocals stems (user decision): karaoke models (KARA_2, *_karaoke) still output
the MAIN vocal (backing left in the instrumental) so they're accepted; ranked by
quality bs_roformer > mel_band > MDX23C > htdemucs > UVR/MDX-NET > filtered. Only
backing-only / (No Vocals) / instrumental / non-audio are rejected.

Disk-floor guarded (waits for user eviction) + resumable (skips existing dests).
Mac-side only; device upload is a separate rsync pass.
"""
import argparse, csv, os, re, shutil, subprocess, sys, time, glob, tempfile

KAR = "/Users/andrew/AB Dropbox/Andrew Beveridge/MediaUnsynced/Karaoke"
ORG = f"{KAR}/Tracks-Organized"
ORIG = f"{KAR}/Tracks-Audio/Original"
VOCALS = f"{KAR}/Tracks-Audio/Vocals"
HERE = os.path.dirname(os.path.abspath(__file__))
MAT = f"{HERE}/local_clone/materialize"
MODEL_DIR = "/Volumes/AndrewMacSD/python-audio-separator-models-repo"
FFPROBE = "/opt/homebrew/bin/ffprobe"
SEP = "/Users/andrew/miniforge3/envs/nomadkaraoke/bin/audio-separator"
SCRATCH = os.path.join(tempfile.gettempdir(), "nomad_vocals_sep")
RESULTS = f"{HERE}/data/vocals_build.csv"
AUDIO = {'.mp3', '.flac', '.wav', '.m4a', '.aac', '.opus', '.ogg', '.webm'}
EXCLUDED = {f"NOMAD-{n}" for n in
            "0197 0199 0200 0201 0202 0203 0205 0208 0209 0210 0211 0213 0214 0216 0217 0218 0234".split()}


def free_gb(path="/"):
    st = os.statvfs(path); return st.f_bavail * st.f_frsize / 1e9


def wait_for_disk(floor):
    warned = False
    while free_gb() < floor:
        if not warned:
            log(f"WAITING for disk: {free_gb():.1f}GB < floor {floor}GB — evict Tracks-Organized to continue")
            warned = True
        time.sleep(60)


def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def dur(path):
    try:
        r = subprocess.run([FFPROBE, "-v", "error", "-show_entries", "format=duration",
                            "-of", "csv=p=0", path], timeout=60, capture_output=True, text=True)
        return float(r.stdout.strip())
    except Exception:
        return None


def materialize(path):
    try:
        subprocess.run([MAT, path], timeout=180, capture_output=True)
        return os.path.getsize(path) > 0
    except Exception:
        return False


def fullvocals_score(fn):
    """(-1 = not full-vocals) else higher = preferred full-vocals model."""
    low = fn.lower()
    if os.path.splitext(low)[1] not in AUDIO:
        return -1
    # must be a main/lead vocals stem; exclude backing-only / no-vocals / instrumental
    if '(no vocals' in low or '(back vocals' in low or '(backing vocals' in low or '(instrumental' in low:
        return -1
    if not ('(vocals' in low or '(lead vocals' in low or '(filtered vocals' in low):
        return -1
    # karaoke models (KARA_2, *_karaoke) still output the MAIN vocal (backing left in
    # the instrumental) — fine for a guide (user confirmed). Just prefer higher quality.
    if 'model_bs_roformer' in low or 'bs-roformer' in low:
        return 100
    if 'mel_band_roformer' in low:
        return 90
    if 'mdx23c' in low:
        return 88
    if 'htdemucs' in low:
        return 80
    if 'uvr' in low or 'mdxnet' in low or 'mdx-net' in low:
        return 70
    if 'mdx v2.1' in low or 'filtered vocals' in low:
        return 60
    return 50


def load_danger():
    danger = set()
    dfp = f"{HERE}/data/review_decisions.tsv"
    with open(dfp) as f:
        for r in csv.reader(f, delimiter='\t'):
            if r and r[0] != 'brand' and r[1] in (
                    'source_from_album', 'source_via_flacfetch', 'set_winner', 'set_winner_rel'):
                danger.add(r[0])
    with open(f"{HERE}/data/withvocals_extracted.csv") as f:
        for r in csv.DictReader(f):
            danger.add(r['brand'])
    return danger


def scan_stems():
    """brand -> list of (score, path) full-vocals stems in its Tracks-Organized folder."""
    by = {}
    folders = {}
    for d in os.listdir(ORG):
        m = re.match(r'(NOMAD-\d{4})', d)
        if m:
            folders.setdefault(m.group(1), []).append(d)
    for b, ds in folders.items():
        cands = []
        for d in ds:
            for dp, _, fns in os.walk(os.path.join(ORG, d)):
                for fn in fns:
                    s = fullvocals_score(fn)
                    if s >= 0:
                        cands.append((s, os.path.join(dp, fn)))
        if cands:
            cands.sort(reverse=True)
            by[b] = cands
    return by


def orig_index():
    idx = {}
    for fn in os.listdir(ORIG):
        m = re.match(r'(NOMAD-\d{4}) - ', fn)
        if m and os.path.splitext(fn)[1].lower() in AUDIO:
            idx[m.group(1)] = os.path.join(ORIG, fn)
    return idx


def separate_fresh(orig_path, dest_noext):
    os.makedirs(SCRATCH, exist_ok=True)
    for f in glob.glob(f"{SCRATCH}/*"):
        try: os.remove(f)
        except OSError: pass
    cmd = [SEP, orig_path, "-m", "2_HP-UVR.pth", "--output_dir", SCRATCH,
           "--output_format", "FLAC", "--model_file_dir", MODEL_DIR, "--single_stem", "Vocals"]
    r = subprocess.run(cmd, timeout=600, capture_output=True, text=True)
    outs = [f for f in glob.glob(f"{SCRATCH}/*") if 'vocals' in f.lower()
            and os.path.splitext(f)[1].lower() in AUDIO]
    if not outs:
        log(f"  SEP FAIL rc={r.returncode}: {r.stderr[-200:].strip()}")
        return None
    src = outs[0]
    dest = dest_noext + os.path.splitext(src)[1].lower()
    shutil.move(src, dest)
    for f in glob.glob(f"{SCRATCH}/*"):
        try: os.remove(f)
        except OSError: pass
    return dest


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--brands", nargs="*")
    ap.add_argument("--floor-gb", type=float, default=8.0)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    os.makedirs(VOCALS, exist_ok=True)
    danger = load_danger()
    log(f"corrected/danger brands: {len(danger)}")
    log("scanning Tracks-Organized for full-vocals stems...")
    stems = scan_stems()
    log(f"brands with a full-vocals stem: {len(stems)}")
    origs = orig_index()
    log(f"M1 originals indexed: {len(origs)}")

    brands = sorted(origs) if not a.brands else a.brands
    brands = [b for b in brands if b not in EXCLUDED]
    if a.limit:
        brands = brands[:a.limit]

    newres = not os.path.exists(RESULTS)
    rf = open(RESULTS, "a", newline="")
    w = csv.writer(rf)
    if newres:
        w.writerow(["brand", "mode", "verdict", "orig_s", "voc_s", "delta_s", "src", "dest"])

    n_reuse = n_fresh = n_skip = n_err = 0
    for b in brands:
        op = origs.get(b)
        if not op:
            log(f"{b}: no M1 original — SKIP"); continue
        base = os.path.splitext(os.path.basename(op))[0]  # NOMAD-#### - Artist - Title
        # resumable: any existing dest with this base?
        existing = glob.glob(glob.escape(os.path.join(VOCALS, base)) + ".*")
        if existing:
            n_skip += 1; continue
        wait_for_disk(a.floor_gb)
        od = dur(op)
        cand = None
        if b not in danger and b in stems:
            for score, sp in stems[b]:
                if a.dry_run:
                    cand = (sp, score); break
                if not materialize(sp):
                    continue
                sd = dur(sp)
                if od and sd and abs(sd - od) < 0.5:
                    cand = (sp, score); break
                else:
                    log(f"{b}: stem dur {sd} vs orig {od} MISMATCH — try next / fresh")
        if cand:
            sp = cand[0]
            dest = os.path.join(VOCALS, base + os.path.splitext(sp)[1].lower())
            if a.dry_run:
                log(f"{b}: REUSE {os.path.basename(sp)} -> {os.path.basename(dest)}")
            else:
                tmp = dest + ".part"
                shutil.copy2(sp, tmp); os.replace(tmp, dest)
                vd = dur(dest)
                w.writerow([b, "reuse", "ok", f"{od:.2f}" if od else "", f"{vd:.2f}" if vd else "", "", os.path.basename(sp), os.path.basename(dest)]); rf.flush()
                log(f"{b}: REUSE ok -> {os.path.basename(dest)}")
            n_reuse += 1
        else:
            if a.dry_run:
                log(f"{b}: FRESH (2_HP-UVR) from {os.path.basename(op)}"); n_fresh += 1; continue
            dest = separate_fresh(op, os.path.join(VOCALS, base))
            if dest:
                vd = dur(dest)
                w.writerow([b, "fresh", "ok", f"{od:.2f}" if od else "", f"{vd:.2f}" if vd else "", "", os.path.basename(op), os.path.basename(dest)]); rf.flush()
                log(f"{b}: FRESH ok -> {os.path.basename(dest)}  ({vd:.1f}s)")
                n_fresh += 1
            else:
                w.writerow([b, "fresh", "ERROR", "", "", "", os.path.basename(op), ""]); rf.flush()
                n_err += 1
    rf.close()
    log(f"DONE: reuse={n_reuse} fresh={n_fresh} skip={n_skip} err={n_err}  free={free_gb():.1f}GB")


if __name__ == "__main__":
    main()
