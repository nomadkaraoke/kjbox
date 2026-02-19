#!/usr/bin/env python3
"""Migrate legacy KJ videos to new naming convention.

Legacy format: {internal_id}.mp4 + {internal_id}.json
New format:    {youtube_id}__{channel}__{safe_title}.mp4

The JSON contains: id, title, original_url, download_date
YouTube ID is extracted from original_url.
Channel is set to "Unknown" (not stored in legacy data).

Usage:
    python3 migrate_legacy_videos.py [--dry-run]

Edit SRC_DIR and DST_DIR below to match your paths.
"""

import glob
import json
import os
import re
import shutil
import sys
from urllib.parse import parse_qs, urlparse

SRC_DIR = "/home/nomad/kjdata/videos"
DST_DIR = "/opt/nomad/YTDownloads"

DRY_RUN = "--dry-run" in sys.argv


def sanitize_filename_part(text):
    """Match kj-controller/utils.py sanitize_filename_part exactly."""
    text = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', text)
    text = re.sub(r'_{2,}', '_', text)
    text = re.sub(r'\s+', ' ', text).strip()
    text = text[:100]
    return text


def extract_youtube_id(url):
    """Extract YouTube video ID from URL."""
    if not url:
        return None
    # Add scheme if missing
    if not url.startswith("http"):
        url = "https://" + url
    parsed = urlparse(url)
    hostname = parsed.hostname or ""
    if "youtube.com" in hostname:
        qs = parse_qs(parsed.query)
        vid = qs.get("v", [None])[0]
        if vid:
            return vid
    elif "youtu.be" in hostname:
        return parsed.path.lstrip("/").split("/")[0]
    return None


def main():
    os.makedirs(DST_DIR, exist_ok=True)

    json_files = sorted(glob.glob(os.path.join(SRC_DIR, "*.json")))
    print(f"Found {len(json_files)} JSON files in {SRC_DIR}")

    copied = 0
    skipped_no_mp4 = 0
    skipped_no_ytid = 0
    skipped_exists = 0
    errors = 0

    for jf in json_files:
        base = os.path.splitext(os.path.basename(jf))[0]
        mp4_src = os.path.join(SRC_DIR, base + ".mp4")

        if not os.path.isfile(mp4_src):
            skipped_no_mp4 += 1
            continue

        try:
            with open(jf) as f:
                meta = json.load(f)
        except Exception as e:
            print(f"  ERROR reading {jf}: {e}")
            errors += 1
            continue

        url = meta.get("original_url", "")
        title = meta.get("title", base)

        yt_id = extract_youtube_id(url)
        if not yt_id or len(yt_id) != 11:
            print(f"  SKIP {base}: no valid YouTube ID from URL: {url}")
            skipped_no_ytid += 1
            continue

        safe_title = sanitize_filename_part(title)
        new_name = f"{yt_id}__Unknown__{safe_title}.mp4"
        dst_path = os.path.join(DST_DIR, new_name)

        if os.path.exists(dst_path):
            skipped_exists += 1
            continue

        if DRY_RUN:
            print(f"  WOULD COPY: {base}.mp4 -> {new_name}")
        else:
            shutil.copy2(mp4_src, dst_path)

        copied += 1

    print()
    print(f"{'Would copy' if DRY_RUN else 'Copied'}: {copied}")
    print(f"Skipped (no mp4): {skipped_no_mp4}")
    print(f"Skipped (no YouTube ID): {skipped_no_ytid}")
    print(f"Skipped (already exists): {skipped_exists}")
    print(f"Errors: {errors}")


if __name__ == "__main__":
    main()
