"""Library-wide playability batch: walk roots, probe each file, stream results
to JSONL (resumable via mtime/size manifest), then aggregate a report."""
from __future__ import annotations

import argparse
import csv
import json
import os
import time

from playability_render import XvfbDisplay

DEFAULT_EXTS = {".mp4", ".mkv", ".avi", ".webm", ".mov", ".zip"}


def iter_media_files(roots, exts):
    for root in roots:
        for dirpath, _dirs, files in os.walk(root):
            for name in sorted(files):
                if os.path.splitext(name)[1].lower() in exts:
                    yield os.path.join(dirpath, name)


def append_jsonl(jsonl_path, result_dict):
    os.makedirs(os.path.dirname(os.path.abspath(jsonl_path)) or ".", exist_ok=True)
    with open(jsonl_path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(result_dict) + "\n")


def load_manifest(jsonl_path):
    manifest = {}
    if not os.path.isfile(jsonl_path):
        return manifest
    with open(jsonl_path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                continue
            if d.get("path"):
                manifest[d["path"]] = {
                    "mtime": d.get("mtime"), "size": d.get("size"),
                    "overall_ok": (d.get("verdict") or {}).get("overall_ok"),
                }
    return manifest


def is_unchanged(path, manifest):
    prev = manifest.get(path)
    if not prev:
        return False
    try:
        st = os.stat(path)
    except OSError:
        return False
    return prev.get("size") == st.st_size and prev.get("mtime") == st.st_mtime


def run_batch(checker, roots, jsonl_path, throttle=0.0, depth="deep",
              recheck_failed=False, limit=None, log=print):
    manifest = load_manifest(jsonl_path)
    checked = 0
    # Start ONE off-screen X display for the whole run and share it across every
    # VLC render (the design's intent). If Xvfb can't start, fail loud — the
    # operator must install it for the VLC render path to work at all.
    with XvfbDisplay() as _xvfb:
        for path in iter_media_files(roots, DEFAULT_EXTS):
            if is_unchanged(path, manifest):
                if not (recheck_failed and manifest[path].get("overall_ok") is False):
                    continue
            try:
                result = checker.check(path, depth=depth, display=_xvfb.display)
                append_jsonl(jsonl_path, result.to_dict())
                ok = result.verdict.get("overall_ok")
                log(f"[{'OK ' if ok else 'BAD'}] {path}")
            except Exception as exc:  # never let one file kill the batch
                append_jsonl(jsonl_path, {"path": path, "kind": "unknown",
                                          "verdict": {"overall_ok": False, "reasons": [f"checker crashed: {exc}"]}})
                log(f"[ERR] {path}: {exc}")
            checked += 1
            if limit and checked >= limit:
                break
            if throttle:
                time.sleep(throttle)
    return checked


def _read_results(jsonl_path):
    out = []
    if not os.path.isfile(jsonl_path):
        return out
    with open(jsonl_path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    # de-dup by path, keeping the last occurrence (re-checks supersede)
    by_path = {}
    for d in out:
        if d.get("path"):
            by_path[d["path"]] = d
    return list(by_path.values())


def aggregate(jsonl_path):
    rows = _read_results(jsonl_path)
    agg = {"total": len(rows), "ok": [], "unplayable": [],
           "mpv_not_vlc": [], "vlc_not_mpv": [], "cdg_problems": []}
    for d in rows:
        v = d.get("verdict", {})
        vlc, mpv = v.get("vlc_playable"), v.get("mpv_playable")
        p = d["path"]
        if v.get("overall_ok"):
            agg["ok"].append(p)
        if not vlc and not mpv:
            agg["unplayable"].append(p)
        elif mpv and not vlc:
            agg["mpv_not_vlc"].append(p)
        elif vlc and not mpv:
            agg["vlc_not_mpv"].append(p)
        if d.get("kind") == "cdg_zip" and not (d.get("cdg") or {}).get("ok", True):
            agg["cdg_problems"].append(p)
    return agg


def write_reports(jsonl_path, csv_path, md_path):
    rows = _read_results(jsonl_path)
    with open(csv_path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["path", "kind", "vlc", "mpv", "vcodec", "acodec", "reasons"])
        for d in rows:
            v = d.get("verdict", {})
            integ = d.get("integrity", {})
            w.writerow([
                d.get("path"), d.get("kind"),
                "OK" if v.get("vlc_playable") else "FAIL",
                "OK" if v.get("mpv_playable") else "FAIL",
                integ.get("vcodec", ""), integ.get("acodec", ""),
                "; ".join(v.get("reasons", [])),
            ])
    agg = aggregate(jsonl_path)
    with open(md_path, "w", encoding="utf-8") as fh:
        fh.write("# Playability Report\n\n")
        fh.write(f"- Total: {agg['total']}\n")
        fh.write(f"- Fully OK: {len(agg['ok'])}\n")
        fh.write(f"- Totally unplayable: {len(agg['unplayable'])}\n")
        fh.write(f"- Plays in mpv but NOT VLC: {len(agg['mpv_not_vlc'])}\n")
        fh.write(f"- Plays in VLC but NOT mpv: {len(agg['vlc_not_mpv'])}\n")
        fh.write(f"- CDG problems: {len(agg['cdg_problems'])}\n\n")
        for title, key in [("Totally unplayable", "unplayable"),
                           ("Plays in mpv but NOT VLC", "mpv_not_vlc"),
                           ("Plays in VLC but NOT mpv", "vlc_not_mpv"),
                           ("CDG problems", "cdg_problems")]:
            if agg[key]:
                fh.write(f"## {title}\n\n")
                for p in sorted(agg[key]):
                    fh.write(f"- {p}\n")
                fh.write("\n")
    return agg


DEFAULT_ROOTS = ["/opt/nomad/YTDownloads", "/opt/nomad/MP4-720p", "/media/nomad/Nomad4TBOne"]


def build_arg_parser():
    p = argparse.ArgumentParser(description="Check playability of a karaoke library.")
    p.add_argument("--roots", nargs="+", default=list(DEFAULT_ROOTS),
                   help="Directories to scan (default: box media folders + 4TB SSD).")
    p.add_argument("--jsonl", default="playability_results.jsonl",
                   help="JSONL output path (default: playability_results.jsonl in cwd).")
    p.add_argument("--csv", default="playability_report.csv",
                   help="CSV report output path.")
    p.add_argument("--md", default="playability_report.md",
                   help="Markdown report output path.")
    p.add_argument("--throttle", type=float, default=0.2,
                   help="Seconds to sleep between files (default: 0.2).")
    p.add_argument("--depth", choices=["deep", "quick"], default="deep",
                   help="Probe depth (default: deep).")
    p.add_argument("--limit", type=int, default=None,
                   help="Stop after N files (default: no limit).")
    p.add_argument("--recheck-failed", action="store_true",
                   help="Re-probe files previously marked unplayable.")
    return p


def main(argv=None):
    args = build_arg_parser().parse_args(argv)
    from playability import PlayabilityChecker

    checker = PlayabilityChecker(config={})
    n = run_batch(checker, args.roots, args.jsonl, throttle=args.throttle,
                  depth=args.depth, recheck_failed=args.recheck_failed, limit=args.limit)
    agg = write_reports(args.jsonl, args.csv, args.md)
    print(f"Checked {n} new/changed files. Total {agg['total']}: "
          f"{len(agg['ok'])} OK, {len(agg['unplayable'])} unplayable, "
          f"{len(agg['mpv_not_vlc'])} mpv-only, {len(agg['vlc_not_mpv'])} vlc-only.")
    print(f"Reports: {args.csv}, {args.md}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
