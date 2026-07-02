# Master-catalog GCS auto-sync — setup runbook

## One-time GCP setup (dedicated read-only SA)
1. Create SA `nomad-master-sync@nomadkaraoke.iam.gserviceaccount.com`.
2. Grant it `roles/storage.objectViewer` on bucket `nomadkaraoke-divebar-files` ONLY
   (bucket-scoped IAM binding, not project-wide).
3. Create a JSON key; copy to the device at `/opt/nomad/secrets/nomad-master-sync.json`
   (mode 600, owner nomad). Prefer managing the SA + binding via Pulumi if available.

## One-time device restructure (OFF-SHOW, after backups)
1. Back up: `cp media_index.json media_index.json.bak-<date>`,
   `cp rotation.db rotation.db.bak-<date>`.
2. Stop the service: `sudo systemctl stop kj-controller`.
3. Rename the download root:
   `sudo mv /opt/nomad/YTDownloads /opt/nomad/downloads`.
4. Move masters under it (seeds the mirror so rsync only pulls the ~104 new):
   `sudo mv /opt/nomad/MP4-720p /opt/nomad/downloads/NOMAD-720p`.
5. Update `config.json`:
   - `download_folder`: `/opt/nomad/downloads`
   - `media_folders`: `["/opt/nomad/downloads"]`  (NOMAD-720p is a child → auto-indexed)
   - `media_db_path`: `/opt/nomad/kjbox/kj-controller/media_library.db`
   - `master_sync_enabled`: `true`
   - `master_sync_credentials_file`: `/opt/nomad/secrets/nomad-master-sync.json`
6. Audit stale references to the old path: `grep -rn YTDownloads /opt/nomad/playability-run`,
   any systemd drop-ins, `preview_cache_dir` (leave empty → re-derives to
   `/opt/nomad/preview-cache`, still correct as a sibling of the new root).
7. Start the service: `sudo systemctl start kj-controller`; hit `/rescan` once to populate
   `media_library` for the existing library.

## Verify GCS auth BEFORE installing the timer
Run once as the nomad user:
`CLOUDSDK_AUTH_CREDENTIAL_FILE_OVERRIDE=/opt/nomad/secrets/nomad-master-sync.json \
  /opt/nomad/google-cloud-sdk/bin/gcloud storage ls "gs://nomadkaraoke-divebar-files/files/Nomad Karaoke/MP4-720p/" | head`
Expected: object listing (confirms the SA can read). If `gcloud` is not on PATH in the unit,
set an absolute `ExecStart` python that calls the full gcloud path, or add the SDK bin to the
service `Environment=PATH=...`.

## Install the timer
1. `sudo cp deploy/nomad-master-sync.{service,timer} /etc/systemd/system/`
2. `sudo systemctl daemon-reload`
3. First manual run (watch the ~104-file backfill): `sudo systemctl start nomad-master-sync.service`
   then `journalctl -u nomad-master-sync -f`.
4. Enable the timer: `sudo systemctl enable --now nomad-master-sync.timer`
5. Confirm cadence: `systemctl list-timers nomad-master-sync.timer`.

## Reconcile / deletions (v0.60.0+)
Each run does an additive `gcloud storage rsync` (copy new/changed), **then a guarded
reconcile** that deletes local masters no longer present in GCS — so upstream deletes/renames
(from the gen delete side) land on the box; the latest published cut wins. A change (copy OR
delete) pokes `/rescan` so the index updates immediately.

**Safety guards (a transient failure must never wipe the mirror):**
- Never deletes if the source listing FAILS (`gcloud storage ls` non-zero exit).
- Never deletes on an EMPTY parsed listing.
- The listing uses the `**` flat form (no per-directory header lines); `:`/`/`-terminated
  lines are skipped defensively so a header-only listing parses as empty → no deletion.
- Compares **NFC-normalized** names, so a local file differing from its GCS object only by
  unicode normalization (NFD-on-disk vs NFC-in-GCS for accented titles) is NOT wrongly deleted.
- Refuses to delete more than `master_sync_max_deletes` (default **50**) in one run — a partial
  listing looks like a mass deletion, so it stays additive that run and logs a warning.

**Config knobs (all optional, in `config.json`):**
- `master_sync_delete_removed` (default `true`) — kill switch for the reconcile step.
- `master_sync_delete_dry_run` (default `false`) — logs the would-delete set, deletes nothing.
  Use for a cautious first rollout: set `true`, watch `journalctl -u nomad-master-sync`, confirm
  the delete set is sane, then remove it.
- `master_sync_max_deletes` (default `50`).

- First post-move run may re-pull a few masters if local mtimes differ; subsequent runs are tiny.
