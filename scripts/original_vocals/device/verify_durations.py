#!/usr/bin/env python3
"""Run ON kjbox. Verify the vocals guide dataset against the originals:
  - raw NOMAD-vocals duration ≈ NOMAD-audio original duration (same song), ±0.6s
  - padded NOMAD-vocals-padded duration ≈ raw + 10s (5s lead + 5s trail), ±0.6s
Reports mismatches + coverage. Read-only.
"""
import os, re, subprocess

AUD = "/opt/nomad/downloads/NOMAD-audio"
RAW = "/opt/nomad/downloads/NOMAD-vocals"
PAD = "/opt/nomad/downloads/NOMAD-vocals-padded"
TOL = 0.6


def dur(path):
    try:
        r = subprocess.run(["ffprobe", "-v", "error", "-show_entries",
                            "format=duration", "-of", "csv=p=0", path],
                           capture_output=True, text=True, timeout=60)
        return float(r.stdout.strip())
    except Exception:
        return None


def index(d):
    idx = {}
    if not os.path.isdir(d):
        return idx
    for fn in sorted(os.listdir(d)):
        m = re.match(r"(NOMAD-\d{4})", fn)
        if m and not fn.endswith(".part"):
            idx.setdefault(m.group(1), fn)
    return idx


def main():
    aud, raw, pad = index(AUD), index(RAW), index(PAD)
    bad_raw, no_orig, bad_pad, no_pad = [], [], [], []
    for b, rfn in raw.items():
        rd = dur(os.path.join(RAW, rfn))
        afn = aud.get(b)
        if afn:
            ad = dur(os.path.join(AUD, afn))
            if rd and ad and abs(rd - ad) > TOL:
                bad_raw.append((b, round(rd, 1), round(ad, 1)))
        else:
            no_orig.append(b)
        pfn = pad.get(b)
        if pfn:
            pd = dur(os.path.join(PAD, pfn))
            if rd and pd and abs(pd - (rd + 10)) > TOL:
                bad_pad.append((b, round(pd, 1), round(rd, 1)))
        else:
            no_pad.append(b)
    print(f"originals={len(aud)}  raw_vocals={len(raw)}  padded={len(pad)}")
    print(f"raw-vs-original duration mismatches (>|{TOL}s|): {len(bad_raw)}")
    for x in bad_raw[:25]:
        print("   MISMATCH brand=%s raw=%.1f orig=%.1f" % x)
    print(f"raw vocals with NO matching original: {len(no_orig)}")
    for b in no_orig[:25]:
        print("   NO-ORIG", b)
    print(f"padded != raw+10s (>|{TOL}s|): {len(bad_pad)}")
    for x in bad_pad[:25]:
        print("   BADPAD brand=%s padded=%.1f raw=%.1f" % x)
    print(f"raw vocals not yet padded: {len(no_pad)}")
    ok = not (bad_raw or bad_pad)
    print("VERDICT:", "OK" if ok else "REVIEW NEEDED")


if __name__ == "__main__":
    main()
