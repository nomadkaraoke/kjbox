"""
GCS master-catalog rsync + reconcile + /rescan poke.

Keeps the local NOMAD-720p mirror in sync with its GCS source: an additive
`gcloud storage rsync` copies new/changed masters, then a *guarded* reconcile step
removes local masters that no longer exist in GCS, so upstream deletes/renames
propagate to the box (the latest published cut wins). Authed by a read-only
service-account key. On any change (copy or delete), poke local /rescan so the index
updates immediately. Designed for a 5-minute systemd timer; failures are reported,
never raised, so flaky network can't wedge the timer.

SAFETY: the reconcile NEVER deletes on an empty or failed source listing (a transient
auth/network/path failure must not wipe the mirror) and never deletes more than
`master_sync_max_deletes` files in one run (a partial listing would look like a mass
deletion — refuse and stay additive). `master_sync_delete_removed` (default True) is a
kill switch; `master_sync_delete_dry_run` logs the delete set without acting.
"""

import fcntl
import os
import subprocess
import sys
import unicodedata

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


def _reconcile_deletions(config, src, dest, env, gcloud_bin):
    """Remove local masters that no longer exist in the GCS source, so upstream
    deletes/renames propagate to the box. Returns the number of files deleted.

    HARD SAFETY — a transient failure must never wipe the mirror:
      * never deletes if the source listing FAILS (gcloud ls returncode != 0),
      * never deletes on an EMPTY source listing,
      * never deletes more than ``master_sync_max_deletes`` in one run (a partial
        listing would look like a mass deletion — refuse and stay additive),
      * ``master_sync_delete_removed`` (default True) is a kill switch,
      * ``master_sync_delete_dry_run`` logs the would-delete set without acting.

    `gcloud storage ls` returns a complete listing or a non-zero exit, so a successful
    non-empty listing is authoritative — a file present locally but absent from it was
    genuinely removed upstream.
    """
    if not config.get("master_sync_delete_removed", True):
        return 0
    # Flat listing via the `**` wildcard — gcloud's documented form for scripting.
    # It emits one object URL per line with NO per-directory header lines. `--recursive`
    # instead prints `gs://.../prefix/:` header lines (and a header for any pseudo-dir
    # like the truncated "… /" artifacts), whose basename is ":" — that would pollute the
    # source set and, in a header-only listing, defeat the empty-listing guard below.
    listing_target = src.rstrip("/") + "/**"
    try:
        proc = subprocess.run(
            [gcloud_bin, "storage", "ls", listing_target],
            env=env, capture_output=True, text=True, timeout=600,
        )
    except Exception:  # noqa: BLE001
        return 0
    if proc.returncode != 0:
        return 0
    source_names = set()
    for line in proc.stdout.splitlines():
        line = line.strip()
        # Skip blanks and any prefix/"directory" header lines (defensive: `--recursive`
        # headers end in ":", pseudo-dirs end in "/") so they can never enter the set.
        if not line or line.endswith("/") or line.endswith(":"):
            continue
        source_names.add(unicodedata.normalize("NFC", os.path.basename(line)))
    if not source_names:
        return 0  # empty/unparseable listing -> never delete
    # Compare NFC-normalized names: a local file that differs from its GCS object ONLY
    # by unicode normalization (NFD vs NFC of accented chars — common for names like
    # "Curicó", "Beyoncé", "Việt Nhân") must NOT be treated as "removed upstream" and
    # wrongly deleted (then re-downloaded, churning every run). Deletion still uses the
    # real on-disk filename.
    to_delete = [
        name for name in _snapshot(dest)
        if unicodedata.normalize("NFC", name) not in source_names
    ]
    if not to_delete:
        return 0
    cap = config.get("master_sync_max_deletes", 50)
    if len(to_delete) > cap:
        print(
            f"master-sync: {len(to_delete)} local files absent from source exceeds cap "
            f"{cap}; refusing to delete (additive-only this run). Sample: {sorted(to_delete)[:5]}"
        )
        return 0
    if config.get("master_sync_delete_dry_run"):
        print(f"master-sync: DRY RUN would delete {len(to_delete)}: {sorted(to_delete)}")
        return 0
    deleted = 0
    for name in to_delete:
        try:
            os.remove(os.path.join(dest, name))
            deleted += 1
        except OSError:
            pass
    if deleted:
        print(f"master-sync: deleted {deleted} local master(s) removed upstream: {sorted(to_delete)}")
    return deleted


def run_sync(config, *, gcloud_bin="gcloud", requests_lib=requests):
    if not config.get("master_sync_enabled"):
        return {"changed": False, "copied": 0, "deleted": 0, "rescanned": False, "error": "disabled"}

    src = config.get("master_sync_source", "")
    dest = _dest(config)
    key = config.get("master_sync_credentials_file", "")
    try:
        os.makedirs(dest, exist_ok=True)
    except OSError as exc:
        return {"changed": False, "copied": 0, "deleted": 0, "rescanned": False, "error": str(exc)}

    before = _snapshot(dest)

    env = dict(os.environ)
    if key:
        # Use the SA key for THIS invocation only; never mutate global gcloud auth.
        env["CLOUDSDK_AUTH_CREDENTIAL_FILE_OVERRIDE"] = key

    cmd = [gcloud_bin, "storage", "rsync", "--recursive", src, dest]
    try:
        proc = subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=1800)
    except Exception as exc:  # noqa: BLE001
        return {"changed": False, "copied": 0, "deleted": 0, "rescanned": False, "error": str(exc)}

    if proc.returncode != 0:
        return {"changed": False, "copied": 0, "deleted": 0, "rescanned": False,
                "error": (proc.stderr or "gcloud rsync failed").strip()[:500]}

    # Reconcile removals AFTER the additive copy so the local mirror tracks GCS exactly
    # (deletes/renames propagate to the box). Guarded so a transient failure can't wipe it.
    deleted = _reconcile_deletions(config, src, dest, env, gcloud_bin)

    after = _snapshot(dest)
    # Change detection is filesystem-based (new/resized/removed files), NOT parsed from
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
    return {"changed": changed, "copied": copied, "deleted": deleted, "rescanned": rescanned, "error": None}


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
