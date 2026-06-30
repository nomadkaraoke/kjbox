# Preview Cache Relocation + Content-Addressing — Design (2026-06-30)

## Problem

The KJ browser's "Available Downloads" (YTDOWNLOADS) list was showing phantom rows
labelled `graphics` and `audio`. Diagnosis from live `/media` on NomadPC: of 1181
`is_download` items, 6 were not downloads at all — they were preview-cache artifacts:

```
/opt/nomad/YTDownloads/.preview-cache/cdg/<hash>/graphics.cdg
/opt/nomad/YTDownloads/.preview-cache/cdg/<hash>/audio.mp3
```

i.e. the extracted halves of 3 CDG-zip songs auditioned via the ▶ Preview button.

### Root cause

1. **Location.** The preview cache defaults to `<download_folder>/.preview-cache`
   (`preview.py`), i.e. *inside* the download folder. `MediaIndex.scan()` walks the
   whole download folder and only prunes the playability-quarantine dir, so every
   `.cdg`/`.mp3`/`.mp4` cache artifact gets indexed, flagged `is_download`, and
   rendered as a YTDOWNLOADS row — with a Delete button that would nuke live cache.
2. **Keying (separate latent issue).** Local files are keyed by
   `realpath|size|mtime_ns` (`preview_cache.local_key`). The cache is therefore lost
   whenever a source file is renamed or moved, even though its content is unchanged.

## Goals

- Preview cache lives in a dedicated directory **outside** every indexed path, so
  cache artifacts can never appear in the library/downloads list again.
- Local-file cache entries are addressed by **source content**, so a preview hits the
  existing cache regardless of the source file's path or name.
- No regression to the divebar/GCS preview path (already content-stable via `file_id`).

## Design

### 1. Cache location → sibling of the data root

`preview_cache_dir` (default `""`) resolves to a **sibling** of the download folder
instead of a child:

```python
# config.resolve_preview_cache_dir(config)
configured = config.get("preview_cache_dir")
if configured:
    return configured
download_folder = config.get("download_folder") or "/tmp"
return os.path.join(os.path.dirname(os.path.realpath(download_folder)), "preview-cache")
```

On NomadPC: `dirname(/opt/nomad/YTDownloads)` → **`/opt/nomad/preview-cache`** — the
requested location, with no device-specific path hard-coded. Neither a `media_folder`
nor inside `download_folder`, so `scan()` never walks it. An explicit
`preview_cache_dir` in config still wins. Cache substructure (`transcode/`,
`gcsblob/`, `cdg/`) is unchanged. The resolver is shared by `preview.py` and
`media.py` (lives in `config.py` — both already import from it; no circular import).

### 2. Content-addressed keying (local files only)

New module function in `preview_cache.py`:

```python
_SIG_CHUNK = 1024 * 1024  # 1 MiB

def content_signature(path):
    """sha1(size + sha1(head 1MiB) + sha1(tail 1MiB)). Small (<=2MiB) files hashed whole."""
```

`PreviewCache.local_key()` switches from `realpath|size|mtime_ns` to
`content_signature(real)`. Same bytes → same key → same cached preview regardless of
path/name. Chosen over a full-file hash because the library partly lives on a 4TB SSD
with multi-hundred-MB videos; a full hash would read the entire file on every preview
click. A size + head/tail signature is rename/move-robust at near-zero read cost, and
collision is effectively impossible for real karaoke media (two distinct files would
need identical size **and** identical first **and** last MiB). `gcs_key(file_id)` is
untouched. `PARAMS_VERSION` bumps `"1"→"2"` so any stale-scheme keys are ignored.

### 3. Defensive scan skip (belt-and-suspenders)

Even though relocation already prevents indexing, `MediaIndex.scan()` also prunes the
resolved preview-cache dir during the walk (same mechanism as the quarantine dir), so
a future misconfiguration that points `preview_cache_dir` back inside an indexed path
cannot reintroduce this exact bug.

### 4. No migration code

The existing on-device cache was built 2026-06-29 (one day old) and is fully
regenerable. Rather than ship migration logic, the old
`/opt/nomad/YTDownloads/.preview-cache` is deleted once by hand at deploy time; the 6
phantom index rows vanish on the next media rescan.

## Testing (local pytest — kjbox has no pytest CI)

- `content_signature`: identical content at two paths → equal; one differing byte →
  different; small-file (<2MiB) branch; head/tail-overlap (1–2MiB) safety.
- `local_key` rename-robustness: key a file, rename/move it, re-key → equal (the core
  regression). Also: mtime change alone → unchanged; content change → changed.
- `config.resolve_preview_cache_dir`: empty config → sibling of `download_folder`;
  explicit `preview_cache_dir` respected.
- `scan()` skips a preview-cache dir placed inside a media folder.

## Deployment

Backend (Python) change → requires a `kj-controller` service restart, which interrupts
active playback. Sequence (all need Andrew's go-ahead + show timing):

1. `rm -rf /opt/nomad/YTDownloads/.preview-cache` (one-time).
2. Push to `main` (auto-deploy pulls within ~60s).
3. `sudo systemctl restart kj-controller`.

## Verification

On `https://kjbox.nomadkaraoke.com` after deploy: click ▶ Preview on a local video and
a CDG-zip song; confirm playback works, cache files land under `/opt/nomad/preview-cache`,
and the YTDOWNLOADS list no longer contains any `graphics`/`audio` rows.
