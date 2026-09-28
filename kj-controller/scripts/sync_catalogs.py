"""
Nightly catalog-mirror sync: download the three remote catalog exports and
rebuild the local search mirror (see catalog_mirror.py).

Sources:
  * Divebar catalog — PUBLIC bucket, plain HTTPS download (no creds).
  * KaraokeNerds full + community — private bucket, fetched via
    ``gcloud storage cp`` using the same read-only SA key the master sync
    uses (``master_sync_credentials_file``); the key override applies to
    THIS invocation only (never mutates global gcloud auth).

Skip logic: if all three downloaded exports hash identically to what the
current mirror was built from, the rebuild is skipped (the divebar export
uses deterministic gzip precisely so this works).

Also refreshes the song-identification index (``song_id.db``, see
docs/SONG-IDENTIFICATION.md): reads gen's manifest ``song-id/latest.json``,
downloads that run's TSV shards, rebuilds via scripts/build_song_id_db.py and
pokes ``/song-id/reload``. Skipped when the manifest's run is the one the
current index was built from (and the normalizer hasn't changed). Independent
of the mirror: one failing never blocks the other.

After a rebuild the app is poked at ``/catalog-mirror/reload`` so the live
process reopens the (atomically replaced) database. Failures are reported,
never raised — a flaky network must not wedge the systemd timer. Designed
for a daily timer + a run shortly after boot (Persistent=true catches up
after the box has been powered off).
"""

import fcntl
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile

import requests

# Allow running as `python -m scripts.sync_catalogs` (systemd) or directly.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import load_config  # noqa: E402
import catalog_mirror  # noqa: E402
import song_identify  # noqa: E402
from scripts import build_song_id_db  # noqa: E402

LOCK_PATH = "/tmp/nomad-catalog-sync.lock"

DIVEBAR_EXPORT_URL = (
    "https://storage.googleapis.com/nomadkaraoke-divebar-files/"
    "exports/divebar-catalog-latest.json.gz")
KN_COMMUNITY_URI = "gs://nomadkaraoke-kn-data/community/community-data-latest.json.gz"
KN_FULL_URI = "gs://nomadkaraoke-kn-data/full/full-data-latest.json.gz"
SONG_ID_MANIFEST_URI = "gs://nomadkaraoke-kn-data/song-id/latest.json"


def _find_gcloud():
    """gcloud may not be on the service PATH — fall back to the SDK install
    location used by the master sync's unit file."""
    found = shutil.which("gcloud")
    if found:
        return found
    fallback = "/opt/nomad/google-cloud-sdk/bin/gcloud"
    return fallback if os.path.exists(fallback) else "gcloud"


def _download_https(url, dest, requests_lib=requests):
    resp = requests_lib.get(url, timeout=300)
    resp.raise_for_status()
    with open(dest, "wb") as f:
        f.write(resp.content)


def _download_gcs(uri, dest, key, gcloud_bin):
    env = dict(os.environ)
    if key:
        env["CLOUDSDK_AUTH_CREDENTIAL_FILE_OVERRIDE"] = key
    proc = subprocess.run(
        [gcloud_bin, "storage", "cp", uri, dest],
        env=env, capture_output=True, text=True, timeout=600)
    if proc.returncode != 0:
        raise RuntimeError(
            f"gcloud cp {uri} failed: {(proc.stderr or '').strip()[:300]}")


def _sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def run_sync(config, *, gcloud_bin=None, requests_lib=requests,
             download_https=None, download_gcs=None):
    """Download exports → hash-compare → rebuild + atomic swap → poke reload.

    Returns a result dict; never raises.
    """
    if not config.get("catalog_mirror_enabled", True):
        return {"changed": False, "skipped": "disabled", "error": None}

    db_path = catalog_mirror._default_db_path(config)
    key = config.get("master_sync_credentials_file", "")
    gcloud_bin = gcloud_bin or _find_gcloud()
    download_https = download_https or _download_https
    download_gcs = download_gcs or _download_gcs

    tmp_dir = tempfile.mkdtemp(prefix="catalog-sync-")
    paths = {
        "divebar": os.path.join(tmp_dir, "divebar.json.gz"),
        "kn_community": os.path.join(tmp_dir, "community.json.gz"),
        "kn_full": os.path.join(tmp_dir, "full.json.gz"),
    }
    try:
        try:
            download_https(DIVEBAR_EXPORT_URL, paths["divebar"],
                           requests_lib=requests_lib)
            download_gcs(KN_COMMUNITY_URI, paths["kn_community"], key, gcloud_bin)
            download_gcs(KN_FULL_URI, paths["kn_full"], key, gcloud_bin)
        except Exception as exc:  # noqa: BLE001
            return {"changed": False, "error": f"download: {exc}"}

        hashes = {name: _sha256(p) for name, p in paths.items()}
        # Unchanged sources only skip the rebuild if the mirror was also built with
        # the current normalizer — after a NORMALIZER_VERSION bump the old mirror
        # is unusable (CatalogMirror.is_usable) and must be rebuilt regardless.
        if os.path.exists(db_path) and \
                catalog_mirror.stored_source_hashes(db_path) == hashes and \
                catalog_mirror.stored_normalizer_version(db_path) == str(catalog_mirror.NORMALIZER_VERSION):
            return {"changed": False, "skipped": "sources unchanged", "error": None}

        try:
            counts = catalog_mirror.build_mirror_db(
                db_path,
                community_path=paths["kn_community"],
                full_path=paths["kn_full"],
                divebar_path=paths["divebar"],
                source_hashes=hashes,
            )
        except Exception as exc:  # noqa: BLE001
            return {"changed": False, "error": f"build: {exc}"}

        reloaded = False
        if requests_lib is not None:
            # Poke the app's INTERNAL bind port (same rationale as the master
            # sync: the public flask_port may be a redirecting proxy).
            reload_url = config.get("catalog_mirror_reload_url") or (
                f"http://127.0.0.1:{config.get('app_bind_port', 5001)}"
                "/catalog-mirror/reload")
            try:
                requests_lib.post(reload_url, timeout=30)
                reloaded = True
            except Exception:  # noqa: BLE001
                reloaded = False  # app picks it up on next restart
        return {"changed": True, "counts": counts, "reloaded": reloaded,
                "error": None}
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def _poke(requests_lib, config, path):
    """POST the app's INTERNAL bind port; False if the app isn't reachable."""
    if requests_lib is None:
        return False
    url = f"http://127.0.0.1:{config.get('app_bind_port', 5001)}{path}"
    try:
        requests_lib.post(url, timeout=30)
        return True
    except Exception:  # noqa: BLE001
        return False


def _download_shards(shards, tmp_dir, key, gcloud_bin, download_gcs):
    """All of a run's shards (~100 files). One wildcard copy of the run folder
    when they share one (a gcloud start-up per file is slow), else one by one."""
    folders = {u.rsplit("/", 1)[0] for u in shards}
    shard_dir = os.path.join(tmp_dir, "shards")
    os.makedirs(shard_dir, exist_ok=True)
    if len(folders) == 1:
        try:
            download_gcs(f"{folders.pop()}/*.tsv.gz", shard_dir + os.sep, key, gcloud_bin)
            wanted = {u.rsplit("/", 1)[1] for u in shards}
            got = sorted(f for f in os.listdir(shard_dir) if f in wanted)
            if len(got) == len(wanted):
                return [os.path.join(shard_dir, f) for f in got]
        except Exception:  # noqa: BLE001 — fall back to per-file downloads
            pass
    local = []
    for i, uri in enumerate(shards):
        dest = os.path.join(tmp_dir, f"songs-{i:04d}.tsv.gz")
        download_gcs(uri, dest, key, gcloud_bin)
        local.append(dest)
    return local


def run_song_id_sync(config, *, gcloud_bin=None, requests_lib=requests,
                     download_gcs=None):
    """gen manifest → download this run's shards → build song_id.db → reload.

    Returns a result dict; never raises.
    """
    if not config.get("song_id_enabled", True):
        return {"changed": False, "skipped": "disabled", "error": None}
    db_path = song_identify.default_db_path(config)
    key = config.get("master_sync_credentials_file", "")
    gcloud_bin = gcloud_bin or _find_gcloud()
    download_gcs = download_gcs or _download_gcs
    tmp_dir = tempfile.mkdtemp(prefix="song-id-sync-")
    try:
        try:
            manifest_path = os.path.join(tmp_dir, "latest.json")
            download_gcs(SONG_ID_MANIFEST_URI, manifest_path, key, gcloud_bin)
            with open(manifest_path, encoding="utf-8") as f:
                manifest = json.load(f)
            run, shards = str(manifest["run"]), list(manifest["shards"])
            if not shards:
                raise ValueError("manifest lists no shards")
        except Exception as exc:  # noqa: BLE001
            return {"changed": False, "error": f"manifest: {exc}"}

        meta = build_song_id_db.stored_meta(db_path)
        if meta.get("source_run") == run and \
                meta.get("normalizer_version") == str(song_identify.NORMALIZER_VERSION):
            return {"changed": False, "skipped": "run unchanged", "run": run, "error": None}

        try:
            local = _download_shards(shards, tmp_dir, key, gcloud_bin, download_gcs)
        except Exception as exc:  # noqa: BLE001
            return {"changed": False, "error": f"download: {exc}"}

        try:
            songs, words = build_song_id_db.build(local, db_path, meta={"source_run": run})
        except Exception as exc:  # noqa: BLE001
            return {"changed": False, "error": f"build: {exc}"}
        return {"changed": True, "run": run, "songs": songs, "words": words,
                "reloaded": _poke(requests_lib, config, "/song-id/reload"), "error": None}
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def main():
    lock = open(LOCK_PATH, "w")
    try:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            print("catalog-sync: another run holds the lock; exiting")
            return 0
        config = load_config()
        result = run_sync(config)
        print(f"catalog-sync: {result}")
        song_id = run_song_id_sync(config)
        print(f"song-id-sync: {song_id}")
        return 0 if not (result.get("error") or song_id.get("error")) else 1
    finally:
        lock.close()


if __name__ == "__main__":
    sys.exit(main())
