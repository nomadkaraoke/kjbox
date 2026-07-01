"""
GCS master-catalog rsync + /rescan poke.

Syncs local NOMAD-720p mirror. Additive `gcloud storage rsync` (never deletes local files),
authed by read-only service-account key. On run copied anything, poke local /rescan so new
masters index immediately. Designed 5-minute systemd timer; failures reported, never raised,
so flaky network can't wedge timer.
"""

import fcntl
import os
import re
import subprocess
import sys

import requests

# Allow running module (systemd) script.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import load_config  # noqa: E402

_COPIED_RE = re.compile(r"^Copying ", re.MULTILINE)
LOCK_PATH = "/tmp/nomad-master-sync.lock"


def _dest(config):
    dest = config.get("master_sync_dest") or ""
    if dest:
        return dest
    return os.path.join(config.get("download_folder", ""), "NOMAD-720p")


def run_sync(config, *, gcloud_bin="gcloud", requests_lib=requests):
    if not config.get("master_sync_enabled"):
        return {"changed": False, "copied": 0, "rescanned": False, "error": "disabled"}

    src = config.get("master_sync_source", "")
    dest = _dest(config)
    key = config.get("master_sync_credentials_file", "")
    os.makedirs(dest, exist_ok=True)

    env = dict(os.environ)
    if key:
        # Use SA key THIS invocation only; never mutate global gcloud auth.
        env["CLOUDSDK_AUTH_CREDENTIAL_FILE_OVERRIDE"] = key

    cmd = [gcloud_bin, "storage", "rsync", "--recursive", src, dest]
    try:
        proc = subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=1800)
    except Exception as e:
        return {"changed": False, "copied": 0, "rescanned": False, "error": str(e)}

    if proc.returncode != 0:
        return {"changed": False, "copied": 0, "rescanned": False, "error": proc.stderr}

    # Count "Copying" lines in stdout.
    matches = _COPIED_RE.findall(proc.stdout)
    copied = len(matches)
    changed = copied > 0

    rescanned = False
    if changed:
        # Poke /rescan so new masters index immediately.
        try:
            port = config.get("flask_port", 80)
            url = f"http://localhost:{port}/rescan"
            requests_lib.post(url, timeout=30)
            rescanned = True
        except Exception as e:
            return {"changed": True, "copied": copied, "rescanned": False, "error": f"rescan failed: {e}"}

    return {"changed": changed, "copied": copied, "rescanned": rescanned, "error": None}


def main():
    lock = open(LOCK_PATH, "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        print("another sync running; skipping")
        return 0
    result = run_sync(load_config())
    print(result)
    return 0 if not result.get("error") or result["error"] == "disabled" else 1


if __name__ == "__main__":
    raise SystemExit(main())
