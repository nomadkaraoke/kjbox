#!/usr/bin/env python3
"""Phase B — gentle integrity-only sweep of the 4TB SSD archive (mostly CDG zips).

No VLC/mpv render => no Xvfb, minimal CPU/IO. Catches corrupt / undecodable files
(bad zip, missing .cdg, undecodable audio/video) — the goal for the bulk archive.
Reuses the deployed batch helpers so it stays in lock-step with the engine, and
shares the same resumable JSONL-manifest format (skip already-checked, unchanged
files; durable append per file; crash rows carry mtime/size so they resume too).
"""
import os
import sys
import time

sys.path.insert(0, "/opt/nomad/kjbox/kj-controller")
from playability import PlayabilityChecker
from playability_batch import (
    iter_media_files, append_jsonl, load_manifest, is_unchanged, DEFAULT_EXTS,
)

JSONL = "/opt/nomad/playability-run/ssd_results.jsonl"
THROTTLE = 0.3

roots = sys.argv[1:]
manifest = load_manifest(JSONL)
checker = PlayabilityChecker(config={})

for path in iter_media_files(roots, DEFAULT_EXTS):
    if is_unchanged(path, manifest):
        continue
    try:
        # renderers=() => integrity + decode only, no render (no Xvfb needed).
        r = checker.check(path, depth="deep", renderers=())
        append_jsonl(JSONL, r.to_dict())
        print(("[OK ] " if r.verdict.get("overall_ok") else "[BAD] ") + path, flush=True)
    except Exception as exc:  # never let one file kill the sweep
        try:
            st = os.stat(path)
            meta = {"mtime": st.st_mtime, "size": st.st_size}
        except OSError:
            meta = {"mtime": None, "size": None}
        append_jsonl(JSONL, {"path": path, "kind": "unknown", **meta,
                             "verdict": {"overall_ok": False,
                                         "reasons": ["checker crashed: " + str(exc)]}})
        print("[ERR] " + path + ": " + str(exc), flush=True)
    if THROTTLE:
        time.sleep(THROTTLE)
