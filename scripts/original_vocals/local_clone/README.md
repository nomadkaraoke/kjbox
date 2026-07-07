# Local-clone fetch (macOS fallback)

Fetches original-mix audio to the KJ device **via the Mac's local Dropbox clone**,
for when the rclone/API path can't download (the `andrewdropboxfull` Dropbox app
lacks the `files.content.read` scope — it can *list* filenames but not fetch
content). This routes bytes through the logged-in Dropbox **desktop** client
instead of the API.

## Why a special "materialize" step is needed

The Tracks-Organized files are Dropbox **online-only placeholders** — 0-byte stubs
carrying a `com.dropbox.placeholder` xattr. A plain `cat`/`cp` returns 0 bytes and
does **not** trigger a download. The only thing that faults them in is an
`NSFileCoordinator` *coordinated read* (what apps do when they open a file), so
`materialize.swift` performs exactly that and blocks until the file is fully local.

## The catch: no programmatic eviction

This Mac runs Dropbox **legacy Smart Sync** (not the modern macOS File Provider —
`NSFileProviderManager.domains()` is empty). So a materialized file can't be
dehydrated back to online-only from code (`evict.swift` documents the File
Provider API that *would* work on a File-Provider Dropbox, but returns "file
doesn't exist" here). Reclaim space manually: Finder → select `Tracks-Organized`
→ right-click → Dropbox → **"Make Online-Only."**

Because of that, `fetch_local.sh` is **disk-bounded** (stops at `FLOOR_GB` free)
and you pull the catalog in waves, freeing space between them. For the full set,
the diskless rclone path (enable the Dropbox scope) is far less work.

## Build & run

```bash
cd scripts/original_vocals/local_clone
swiftc -O materialize.swift -o materialize          # one-time build

# 1. generate the local fetch plan from the classifier manifest
python3 make_local_plan.py --manifest ../data/manifest.csv \
  --dropbox-root "/Users/andrew/AB Dropbox/Andrew Beveridge/MediaUnsynced/Karaoke/Tracks-Organized" \
  > local_fetch.tsv

# 2. fetch (resumable: skips files already on the device; 5-way parallel)
FLOOR_GB=5 bash fetch_local.sh local_fetch.tsv 5
```

Overridable env: `DEST_HOST` (default `nomadpctunnel`), `DEST_DIR`
(`/opt/nomad/downloads/NOMAD-audio`), `FLOOR_GB`, `MATERIALIZE_BIN`.

## Files

| File | Purpose |
|------|---------|
| `materialize.swift` | force-download one online-only file (coordinated read); blocks until local |
| `evict.swift` | dehydrate one file (File-Provider Dropbox only — no-op on legacy Smart Sync) |
| `make_local_plan.py` | manifest → `<brand>\t<local_path>\t<dest_name>` plan |
| `fetch_local.sh` | materialize → validate → scp per file, parallel + resumable + disk-bounded |

Build artifacts (`materialize`, `evict`, `local_fetch.tsv`) are git-ignored.
