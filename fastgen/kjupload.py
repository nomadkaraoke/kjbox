#!/usr/bin/env python3
"""kjupload — upload a media file to the nomadpc kjbox, as if via the KJ UI.

POSTs the file to the kjbox `/upload` endpoint, which runs the SAME server-side
import pipeline as the web UI's "Choose file to upload…" (playability gate +
content-addressed `media_library` row), so the file shows up in the library
exactly like a manual upload. Reaches the device over its Cloudflare-Access-gated
tunnel using a service token.

Usage:
    kjupload FILE [FILE ...]

Environment:
    KJBOX_BASE_URL                 default https://kjbox.nomadkaraoke.com
    KJBOX_CF_ACCESS_CLIENT_ID      Cloudflare Access service-token id     (workspace .envrc)
    KJBOX_CF_ACCESS_CLIENT_SECRET  Cloudflare Access service-token secret (workspace .envrc)
"""

from __future__ import annotations

import argparse
import mimetypes
import os
import sys

import requests

DEFAULT_BASE = "https://kjbox.nomadkaraoke.com"


def _cf_headers() -> dict:
    cid = os.environ.get("KJBOX_CF_ACCESS_CLIENT_ID")
    sec = os.environ.get("KJBOX_CF_ACCESS_CLIENT_SECRET")
    if cid and sec:
        return {"CF-Access-Client-Id": cid, "CF-Access-Client-Secret": sec}
    return {}


def upload_to_kjbox(
    path: str, base_url: "str | None" = None, timeout: int = 600,
    label: "str | None" = None, note: "str | None" = None,
    artist: "str | None" = None, title: "str | None" = None,
) -> dict:
    """Upload one file to the kjbox `/upload` endpoint. Returns the JSON reply.

    If `label`/`note` is given, tag the resulting media with it via `/media/note`
    — a label survives the device's title normalization (unlike a filename
    marker), so it's a reliable, UI-visible way to flag e.g. FASTGEN drafts.
    """
    base_url = (base_url or os.environ.get("KJBOX_BASE_URL", DEFAULT_BASE)).rstrip("/")
    headers = _cf_headers()
    name = os.path.basename(path)
    ctype = mimetypes.guess_type(name)[0] or "application/octet-stream"
    with open(path, "rb") as fh:
        resp = requests.post(
            f"{base_url}/upload",
            files={"file": (name, fh, ctype)},
            headers=headers, timeout=timeout,
        )
    if resp.status_code >= 400:
        raise RuntimeError(f"kjbox returned {resp.status_code}: {resp.text[:300]}")
    try:
        result = resp.json()
    except ValueError:
        result = {"status": resp.status_code}

    media_id = result.get("media_id") if isinstance(result, dict) else None
    if media_id and (label or note):
        nr = requests.post(
            f"{base_url}/media/note",
            json={"media_id": media_id, "label": label or "", "note": note or "",
                  "artist": artist, "title": title},
            headers=headers, timeout=30,
        )
        result["label_applied"] = label if nr.ok else f"FAILED ({nr.status_code})"
    return result


def main(argv: list) -> int:
    ap = argparse.ArgumentParser(description="Upload media file(s) to the nomadpc kjbox")
    ap.add_argument("files", nargs="+", help="Media file(s) to upload")
    ap.add_argument("--base-url", default=None, help=f"kjbox base URL (default {DEFAULT_BASE})")
    ap.add_argument("--label", default=None, help="Tag the uploaded media with this UI label (e.g. FASTGEN)")
    ap.add_argument("--note", default=None, help="Attach this note to the uploaded media")
    args = ap.parse_args(argv)

    rc = 0
    for path in args.files:
        if not os.path.exists(path):
            print(f"[kjupload] not found: {path}", file=sys.stderr)
            rc = 2
            continue
        try:
            print(f"[kjupload] uploading {os.path.basename(path)} → kjbox …", flush=True)
            result = upload_to_kjbox(path, args.base_url, label=args.label, note=args.note)
            print(f"[kjupload] ✓ {result}")
        except Exception as exc:
            print(f"[kjupload] ✗ {type(exc).__name__}: {exc}", file=sys.stderr)
            rc = 1
    return rc


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
