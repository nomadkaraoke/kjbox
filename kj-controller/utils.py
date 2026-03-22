"""Logging and filename utility functions."""

import os
import re
import sys
import time


def log_message(message, config=None):
    """Appends a message to the log file and prints to stderr."""
    log_file = config.get('log_file') if config else None
    if log_file:
        try:
            with open(log_file, "a") as f:
                f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - {message}\n")
        except Exception:
            pass
    print(message, file=sys.stderr, flush=True)


def sanitize_filename_part(text):
    """Replace filesystem-unsafe chars and __ (our separator) with _. Truncate to 100 chars."""
    text = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', text)
    text = re.sub(r'_{2,}', '_', text)
    text = re.sub(r'\s+', ' ', text).strip()
    text = text[:100]
    return text


def parse_youtube_filename(filename):
    """Parse youtube_id, channel, title from filename like {id}__{channel}__{title}.ext.
    Returns (youtube_id, channel, title) or None if not in this format."""
    stem = os.path.splitext(filename)[0]
    parts = stem.split('__', 2)
    if len(parts) == 3 and len(parts[0]) == 11:
        return (parts[0], parts[1], parts[2])
    return None
