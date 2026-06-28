"""Library-wide playability batch: walk roots, probe each file, stream results
to JSONL (resumable via mtime/size manifest), then aggregate a report."""
from __future__ import annotations

import json
import os
import time

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
    for path in iter_media_files(roots, DEFAULT_EXTS):
        if is_unchanged(path, manifest):
            if not (recheck_failed and manifest[path].get("overall_ok") is False):
                continue
        try:
            result = checker.check(path, depth=depth)
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
