#!/usr/bin/env python3
"""Recover originals for no_source folders that have a 'With Vocals' karaoke video:
the video's audio track IS the original recording. Materialize the video, extract
the audio stream (copy, no re-encode), stage it as NOMAD-#### - Artist - Title.<ext>.

Tries each 'With Vocals' file in a folder (top-level before lyrics/) until one
yields a valid audio stream (some .mov renders are truncated). Resumable: skips
brands whose output already exists.
"""
import csv, os, re, glob, subprocess

HERE = os.path.dirname(os.path.abspath(__file__))
TORG = "/Users/andrew/AB Dropbox/Andrew Beveridge/MediaUnsynced/Karaoke/Tracks-Organized"
MAT = os.path.join(HERE, "local_clone", "materialize")
FFPROBE = "/opt/homebrew/bin/ffprobe"
FFMPEG = "/opt/homebrew/bin/ffmpeg"
STAGE = "/Users/andrew/Projects/nomadkaraoke/fix-orig-audio-gaps/withvocals"
OUTCSV = os.path.join(HERE, "data", "withvocals_extracted.csv")
FIELDS = ["brand", "source_video", "out_file", "codec", "dur_s", "status"]
CODEC_EXT = {"aac": "m4a", "mp3": "mp3", "flac": "flac", "opus": "opus", "vorbis": "ogg", "alac": "m4a"}


def _num(b):
    m = re.search(r"(\d+)", b); return int(m.group(1)) if m else 0


def _safe(s):
    return re.sub(r"[/\x00-\x1f]", "_", s).strip().rstrip(" .")


def _audio_stream(path):
    r = subprocess.run([FFPROBE, "-v", "error", "-select_streams", "a:0",
                        "-show_entries", "stream=codec_name:format=duration",
                        "-of", "default=noprint_wrappers=1:nokey=0", path],
                       capture_output=True, text=True)
    if r.returncode != 0:
        return None, None
    codec = dur = None
    for line in r.stdout.splitlines():
        if line.startswith("codec_name="): codec = line.split("=", 1)[1].strip()
        if line.startswith("duration="):
            try: dur = float(line.split("=", 1)[1])
            except ValueError: pass
    return codec, dur


def _withvocals_files(folder):
    hits = []
    for root, _, names in os.walk(folder):
        for n in names:
            if "with vocals" in n.lower():
                hits.append(os.path.join(root, n))
    # top-level before lyrics/ subfolder; mp4/mov before mkv
    def rank(p):
        rel = os.path.relpath(p, folder)
        depth = rel.count(os.sep)
        ext = p.rsplit(".", 1)[-1].lower()
        extrank = {"mp4": 0, "mov": 1, "mkv": 2, "mkv": 2}.get(ext, 3)
        return (depth, extrank, p)
    return sorted(hits, key=rank)


def main():
    os.makedirs(STAGE, exist_ok=True)
    man = {r["brand_code"]: r for r in csv.DictReader(open(os.path.join(HERE, "data", "manifest.csv")))}
    ns = [b for b, r in man.items() if r["method"] == "karaoke-sourced"]
    # folders that have a With Vocals file
    def folder_for(b):
        h = glob.glob(os.path.join(TORG, f"{b} *")) + glob.glob(os.path.join(TORG, b))
        return h[0] if h else None
    done = set()
    if os.path.exists(OUTCSV):
        done = {r["brand"] for r in csv.DictReader(open(OUTCSV)) if r["status"] == "ok"}
    todo = []
    for b in sorted(ns, key=_num):
        fo = folder_for(b)
        if fo and _withvocals_files(fo) and b not in done:
            todo.append((b, fo))
    new = not os.path.exists(OUTCSV)
    f = open(OUTCSV, "a", newline=""); w = csv.DictWriter(f, fieldnames=FIELDS)
    if new: w.writeheader()
    print(f"withvocals extract: {len(todo)} folders ({len(done)} already done)")
    for i, (b, fo) in enumerate(todo, 1):
        m = man[b]
        base = _safe(f"{b} - {m['artist']} - {m['title']}")
        row = {"brand": b, "source_video": "", "out_file": "", "codec": "", "dur_s": "", "status": "FAIL"}
        for vid in _withvocals_files(fo):
            subprocess.run([MAT, vid], capture_output=True, timeout=600)
            codec, dur = _audio_stream(vid)
            if not codec:
                continue
            ext = CODEC_EXT.get(codec, "mka")
            out = os.path.join(STAGE, f"{base}.{ext}")
            r = subprocess.run([FFMPEG, "-y", "-v", "error", "-i", vid, "-vn", "-c:a", "copy", out],
                               capture_output=True, text=True)
            if r.returncode != 0 or not os.path.exists(out) or os.path.getsize(out) < 10000:
                # copy failed (container/codec) -> re-encode to flac; drop the
                # invalid copy artifact first so only the good output remains.
                if os.path.exists(out):
                    os.remove(out)
                out = os.path.join(STAGE, f"{base}.flac")
                r = subprocess.run([FFMPEG, "-y", "-v", "error", "-i", vid, "-vn", "-c:a", "flac", out],
                                   capture_output=True, text=True)
                ext = "flac"
            if r.returncode == 0 and os.path.exists(out) and os.path.getsize(out) > 10000:
                row.update(source_video=os.path.relpath(vid, TORG), out_file=os.path.basename(out),
                           codec=codec, dur_s=f"{dur:.1f}" if dur else "", status="ok")
                break
        w.writerow(row); f.flush()
        print(f"[{i}/{len(todo)}] {b} {row['status']} {row['out_file']}")
    f.close()
    print("extract complete")


if __name__ == "__main__":
    main()
