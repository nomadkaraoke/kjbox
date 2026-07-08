#!/usr/bin/env python3
"""Assemble the verified original audio for every NOMAD release into two places,
consistently named `NOMAD-#### - Artist - Title.<origext>`:
  1) Dropbox Tracks-Audio/Original/         (the clean dataset)
  2) each track's own Tracks-Organized/<folder>/  (in-folder consistent copy)

Source per brand, by priority (final review decision wins):
  review_decisions source action  >  With-Vocals extract  >  oracle confirmed
  winner  >  marker-era manifest pick.  exclude_no_guide / confirm_no_source =>
  no original (skipped).

Resumable (skips dests that already exist). Disk-floor guarded: pauses when free
space drops below FLOOR_GB (Dropbox has no scriptable evict — reclaim via Finder
'Make Online-Only' / Dropbox 'Free Up Space', then re-run).

Usage: assemble_originals.py [--dry-run] [--limit N] [--floor-gb 10]
"""
import argparse, csv, os, re, glob, shutil, subprocess, sys

HERE = os.path.dirname(os.path.abspath(__file__))
TORG = "/Users/andrew/AB Dropbox/Andrew Beveridge/MediaUnsynced/Karaoke/Tracks-Organized"
DEST = "/Users/andrew/AB Dropbox/Andrew Beveridge/MediaUnsynced/Karaoke/Tracks-Audio/Original"
GAPS = "/Users/andrew/Projects/nomadkaraoke/fix-orig-audio-gaps"
MAT = os.path.join(HERE, "local_clone", "materialize")
SRC_ACTIONS = {"source_from_album", "source_via_flacfetch", "set_winner_rel", "set_winner", "approve_pick"}
NO_ACTIONS = {"exclude_no_guide", "confirm_no_source"}


def _num(b):
    m = re.search(r"(\d+)", b); return int(m.group(1)) if m else 0


def _safe(brand, artist, title, ext):
    base = f"{brand} - {artist} - {title}".strip().rstrip(" .")
    base = re.sub(r"[/\x00-\x1f]", "_", base)
    return f"{base}.{ext}"


def _folder_for(brand):
    h = glob.glob(os.path.join(TORG, f"{brand} *")) + glob.glob(os.path.join(TORG, brand))
    return h[0] if h else None


def load():
    man = {r["brand_code"]: r for r in csv.DictReader(open(os.path.join(HERE, "data", "manifest.csv")))}
    orc = {r["brand"]: r for r in csv.DictReader(open(os.path.join(HERE, "data", "oracle_results.csv")))}
    wv = {}
    p = os.path.join(HERE, "data", "withvocals_extracted.csv")
    if os.path.exists(p):
        wv = {r["brand"]: r["out_file"] for r in csv.DictReader(open(p)) if r["status"] == "ok"}
    dec = {}
    for r in csv.DictReader(open(os.path.join(HERE, "data", "review_decisions.tsv")), delimiter="\t"):
        dec[r["brand"]] = (r["action"], r["value"])  # last-wins
    return man, orc, wv, dec


def resolve(brand, man, orc, wv, dec):
    """Return absolute source path (existing) or None."""
    act, val = dec.get(brand, (None, None))
    if act in NO_ACTIONS:
        return None
    if act == "source_from_album":
        return os.path.join(GAPS, val)
    if act == "source_via_flacfetch":
        p = os.path.join(GAPS, val)
        return p if os.path.exists(p) else None
    if act == "set_winner_rel":
        return os.path.join(TORG, val)
    if act == "set_winner":
        fo = _folder_for(brand)
        return os.path.join(fo, val) if fo else None
    if act == "approve_pick" and brand in orc and orc[brand]["winner_rel"]:
        return os.path.join(TORG, orc[brand]["winner_rel"])
    # no source-decision: fall through the priority chain
    if brand in wv:
        return os.path.join(GAPS, "withvocals", wv[brand])
    if brand in orc and orc[brand]["verdict"] == "confirmed" and orc[brand]["winner_rel"]:
        return os.path.join(TORG, orc[brand]["winner_rel"])
    cp = man.get(brand, {}).get("chosen_path")
    if cp:
        return os.path.join(TORG, cp)
    return None


def free_gb():
    # Check the destination volume, not root — DEST (Dropbox) may be on a
    # different filesystem than /.
    st = os.statvfs(DEST if os.path.isdir(DEST) else "/")
    return st.f_bavail * st.f_frsize / 1e9


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--floor-gb", type=float, default=10.0)
    args = ap.parse_args(argv)
    man, orc, wv, dec = load()
    os.makedirs(DEST, exist_ok=True)

    plan = []           # (brand, src, dest_name, folder)
    no_original = []
    for brand in sorted(man, key=_num):
        src = resolve(brand, man, orc, wv, dec)
        if not src:
            no_original.append(brand); continue
        m = man[brand]
        ext = src.rsplit(".", 1)[-1].lower()
        name = _safe(brand, m["artist"], m["title"], ext)
        plan.append((brand, src, name, _folder_for(brand)))

    print(f"catalog: {len(man)}  |  has original: {len(plan)}  |  no original (excluded/gap): {len(no_original)}")
    missing_src = [(b, s) for b, s, n, f in plan if not os.path.exists(s)]
    print(f"plan entries whose source file is MISSING on disk: {len(missing_src)}")
    for b, s in missing_src[:15]:
        print(f"   MISSING {b}: {s}")

    if args.dry_run:
        # sample + rough size of locally-present sources
        import random
        sizes = [os.path.getsize(s) for _, s, _, _ in plan if os.path.exists(s)]
        if sizes:
            avg = sum(sizes) / len(sizes)
            print(f"avg source size (of {len(sizes)} local): {avg/1e6:.1f} MB  ->  est per destination ~{avg*len(plan)/1e9:.1f} GB, both ~{2*avg*len(plan)/1e9:.1f} GB")
        print("\nsample plan:")
        for b, s, n, f in random.Random(1).sample(plan, min(8, len(plan))):
            print(f"   {b}: {os.path.basename(s)}\n        -> {n}")
        return 0

    done1 = done2 = copied = 0
    todo = plan[: args.limit] if args.limit else plan
    for i, (brand, src, name, folder) in enumerate(todo, 1):
        if free_gb() < args.floor_gb:
            print(f"\nDISK FLOOR reached ({free_gb():.1f} GB free < {args.floor_gb}). Reclaim space and re-run (resumable). Stopping.")
            break
        d1 = os.path.join(DEST, name)
        d2 = os.path.join(folder, name) if folder else None
        need = (not os.path.exists(d1)) or (d2 and not os.path.exists(d2))
        if not need:
            done1 += 1; continue
        if not os.path.exists(src):
            print(f"[{i}] {brand} SKIP (source missing: {src})"); continue
        if src.startswith(TORG):
            subprocess.run([MAT, src], capture_output=True)   # materialize Dropbox source
        if not os.path.exists(d1):
            shutil.copy2(src, d1); copied += 1
        if d2 and not os.path.exists(d2):
            shutil.copy2(src, d2)
        if i % 25 == 0:
            print(f"[{i}/{len(todo)}] {brand}  (free {free_gb():.1f} GB)")
    print(f"\nassembled. new copies this run: {copied}; already-present skipped: {done1}")


if __name__ == "__main__":
    raise SystemExit(main())
