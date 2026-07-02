"""
GCS master-catalog rsync + /rescan poke.

Syncs local NOMAD-720p mirror. Additive `gcloud storage rsync` (never deletes local files),
authed by read-only service-account key. On run copied anything, poke local /rescan so new
masters index immediately. Designed 5-minute systemd timer; failures reported, never raised,
so flaky network can't wedge timer.
"""

import fcntl
import os
import subprocess
import sys

import requests

# Allow running module (systemd) script.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import load_config  # noqa: E402

LOCK_PATH = "/tmp/nomad-master-sync.lock"


def _dest(config):
    dest = config.get("master_sync_dest") or ""
    if dest:
        return dest
    return os.path.join(config.get("download_folder", ""), "NOMAD-720p")


def _snapshot(dest):
    """Map of filename -> size for files directly in dest. Filesystem-based
    change detection, independent of gcloud's rsync output format."""
    snap = {}
    try:
        for entry in os.scandir(dest):
            if entry.is_file():
                try:
                    snap[entry.name] = entry.stat().st_size
                except OSError:
                    pass
    except OSError:
        pass
    return snap


def run_sync(config, *, gcloud_bin="gcloud", requests_lib=requests):
    if not config.get("master_sync_enabled"):
        return {"changed": False, "copied": 0, "rescanned": False, "error": "disabled"}

    src = config.get("master_sync_source", "")
    dest = _dest(config)
    key = config.get("master_sync_credentials_file", "")
    try:
        os.makedirs(dest, exist_ok=True)
    except OSError as exc:
        return {"changed": False, "copied": 0, "rescanned": False, "error": str(exc)}

    before = _snapshot(dest)

    env = dict(os.environ)
    if key:
        # Use the SA key for THIS invocation only; never mutate global gcloud auth.
        env["CLOUDSDK_AUTH_CREDENTIAL_FILE_OVERRIDE"] = key

    cmd = [gcloud_bin, "storage", "rsync", "--recursive", src, dest]
    try:
        proc = subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=1800)
    except Exception as exc:  # noqa: BLE001
        return {"changed": False, "copied": 0, "rescanned": False, "error": str(exc)}

    if proc.returncode != 0:
        return {"changed": False, "copied": 0, "rescanned": False,
                "error": (proc.stderr or "gcloud rsync failed").strip()[:500]}

    after = _snapshot(dest)
    # Change detection is filesystem-based (new or resized files), NOT parsed from
    # gcloud stdout/stderr (whose format is not a stable contract).
    copied = len(set(after) - set(before))
    changed = after != before
    rescanned = False
    if changed and requests_lib is not None:
        # Poke the app's INTERNAL bind port (app_bind_port, default 5001), NOT the
        # public flask_port — on Caddy-fronted devices flask_port is the proxy (80),
        # which 301-redirects and never reaches the app's /rescan.
        rescan_url = config.get("master_sync_rescan_url") or (
            f"http://127.0.0.1:{config.get('app_bind_port', 5001)}/rescan"
        )
        try:
            requests_lib.post(rescan_url, timeout=30)
            rescanned = True
        except Exception:  # noqa: BLE001
            rescanned = False  # rescan will happen on the next natural scan anyway
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
