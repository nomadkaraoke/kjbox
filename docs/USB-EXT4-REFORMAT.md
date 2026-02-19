# USB Drive Reformat: exFAT → ext4

**Date:** 2026-02-19
**Device:** NomadPC (nomadpc)
**Drive:** 4TB SanDisk USB SSD (`/dev/sda1`), mounted at `/media/nomad/Nomad4TBOne`
**Data:** ~3.3 TB, ~413,670 files across ~21,871 directories

## Why

exFAT has no journal. Unclean shutdowns (power loss at venues) risk silent data corruption.
ext4 has a journal and recovers automatically — critical for a portable KJ rig.

## Strategy

Two independent backups before reformatting:

1. **Physical backup** → 4TB portable HDD (fast, ~5 hours)
2. **Cloud backup** → GCS bucket in `nomadkaraoke` project (slow, ~6 days upload; permanent cloud copy)

Once both backups are verified, reformat USB to ext4 and restore from the physical HDD (fastest).

---

## AFTER A REBOOT: Restart the GCS Upload

The upload runs in a tmux session which does **not** survive reboots. After any reboot:

```bash
# SSH into nomadpc, then:
/opt/nomad/gcs-upload.sh
```

That's it. The script:
- Starts a new tmux session
- Resumes the `gcloud storage rsync` (skips already-uploaded files automatically)
- Logs to `/opt/nomad/gcs-upload.log`

### Other script commands

```bash
/opt/nomad/gcs-upload.sh status   # check progress (log tail + bucket size)
/opt/nomad/gcs-upload.sh attach   # attach to the running tmux session
```

### If the script is missing

If `/opt/nomad/gcs-upload.sh` doesn't exist (e.g. drive got wiped), run manually:

```bash
tmux new-session -s gcs-upload

/opt/nomad/google-cloud-sdk/bin/gcloud storage rsync \
  /media/nomad/Nomad4TBOne \
  gs://nomad-usb-backup \
  --recursive \
  --checksums-only \
  --no-clobber \
  2>&1 | tee -a /opt/nomad/gcs-upload.log
```

---

## Progress Checklist

- [x] `gcloud` CLI installed at `/opt/nomad/google-cloud-sdk/`
- [x] Authenticated as `andrew.d.beveridge@gmail.com` (credentials copied from Mac)
- [x] GCS bucket created: `gs://nomad-usb-backup` (us-central1, Standard)
- [x] Upload script installed: `/opt/nomad/gcs-upload.sh`
- [x] Upload started: 2026-02-19
- [ ] Upload complete (~6 days, ETA ~2026-02-25)
- [ ] Cloud backup verified (file counts match)
- [ ] 4TB portable HDD connected and mounted
- [ ] Physical backup complete (rsync to HDD)
- [ ] Physical backup verified (file counts match)
- [ ] USB drive reformatted to ext4
- [ ] Data restored from HDD
- [ ] fstab updated
- [ ] Services restarted and verified
- [ ] GCS storage class downgraded to Coldline

---

## Phase 1: Setup (DONE)

### 1.1 Install gcloud CLI (done)

```bash
# Already installed at /opt/nomad/google-cloud-sdk/
# Added to PATH via ~/.zshrc
```

### 1.2 Authenticate (done)

Credentials copied from Mac (`~/.config/gcloud/credentials.db`).
Active account: `andrew.d.beveridge@gmail.com`, project: `nomadkaraoke`.

If credentials expire, re-copy from Mac:
```bash
# From Mac:
scp ~/.config/gcloud/credentials.db ~/.config/gcloud/application_default_credentials.json nomadpc:~/.config/gcloud/
# Then on nomadpc:
gcloud config set account andrew.d.beveridge@gmail.com
gcloud config set project nomadkaraoke
```

### 1.3 Create GCS bucket (done)

Bucket: `gs://nomad-usb-backup` (us-central1, Standard, uniform bucket-level access).

> Standard storage at $0.020/GB/month. 3.3 TB ≈ $66/month.
> Switch to Coldline ($0.004/GB) after reformat for long-term archive (~$13/month).

---

## Phase 2: Cloud Backup (GCS Upload) — IN PROGRESS

**Started:** 2026-02-19 17:52 EST
**Estimated completion:** ~2026-02-25 (6 days at 50 Mbps upload)

### Start / resume

```bash
/opt/nomad/gcs-upload.sh           # start or resume (idempotent)
/opt/nomad/gcs-upload.sh status    # check progress
/opt/nomad/gcs-upload.sh attach    # watch live
```

### Monitor

```bash
# Quick: bucket size vs expected 3.3 TB
gcloud storage du gs://nomad-usb-backup --summarize

# Detailed: tail the log
tail -f /opt/nomad/gcs-upload.log
```

### How resume works

`gcloud storage rsync` with `--checksums-only --no-clobber`:
- Scans local files and remote bucket (takes ~5 min for 413K files)
- Skips any file already in the bucket with a matching checksum
- Only uploads files that are missing or different
- Safe to kill and re-run at any time

### 2.1 Verify cloud backup (after upload completes)

```bash
# Compare file counts
LOCAL_COUNT=$(find /media/nomad/Nomad4TBOne -type f | wc -l)
CLOUD_COUNT=$(gcloud storage ls gs://nomad-usb-backup --recursive | grep -v '/$' | wc -l)
echo "Local: $LOCAL_COUNT  Cloud: $CLOUD_COUNT"

# Dry-run rsync to confirm nothing left to sync
gcloud storage rsync \
  /media/nomad/Nomad4TBOne \
  gs://nomad-usb-backup \
  --recursive \
  --checksums-only \
  --dry-run
```

Both counts should match and the dry-run should show zero operations.

---

## Phase 3: Physical Backup (HDD)

Can run in parallel with Phase 2, or sequentially.

### 3.1 Mount the portable HDD

```bash
# Identify the drive (will show as /dev/sdX)
lsblk -f

# Mount (adjust device name as needed)
sudo mkdir -p /media/nomad/BackupHDD
sudo mount /dev/sdX1 /media/nomad/BackupHDD
```

### 3.2 Copy data

```bash
tmux new-session -s hdd-copy

rsync -avh --progress \
  /media/nomad/Nomad4TBOne/ \
  /media/nomad/BackupHDD/ \
  2>&1 | tee /opt/nomad/hdd-copy.log
```

**Resuming after reboot:** `tmux new-session -s hdd-copy` then re-run the same rsync command.

### 3.3 Verify physical backup

```bash
LOCAL_COUNT=$(find /media/nomad/Nomad4TBOne -type f | wc -l)
HDD_COUNT=$(find /media/nomad/BackupHDD -type f | wc -l)
echo "Local: $LOCAL_COUNT  HDD: $HDD_COUNT"
```

---

## Phase 4: Reformat

**Only proceed when BOTH backups are verified.**

### 4.1 Stop services that use the drive

```bash
sudo systemctl stop kj-controller
```

### 4.2 Unmount and reformat

```bash
sudo umount /media/nomad/Nomad4TBOne

# Reformat to ext4 with label
sudo mkfs.ext4 -L Nomad4TBOne /dev/sda1

# Get the new UUID
NEW_UUID=$(sudo blkid -s UUID -o value /dev/sda1)
echo "New UUID: $NEW_UUID"
```

### 4.3 Update fstab

```bash
# Edit /etc/fstab — change the Nomad4TBOne line:
#   OLD: UUID=907E-816F /media/nomad/Nomad4TBOne exfat defaults,nofail,uid=1000,gid=1000 0 0
#   NEW: UUID=<new-uuid> /media/nomad/Nomad4TBOne ext4 defaults,nofail 0 2
sudo nano /etc/fstab
```

Note: ext4 doesn't need `uid=1000,gid=1000` — use `chown` after mounting instead.

### 4.4 Mount and set ownership

```bash
sudo mount /media/nomad/Nomad4TBOne
sudo chown -R nomad:nomad /media/nomad/Nomad4TBOne
```

---

## Phase 5: Restore from Physical HDD

Restoring from the local HDD is ~10x faster than downloading from GCS.

```bash
tmux new-session -s restore

rsync -avh --progress \
  /media/nomad/BackupHDD/ \
  /media/nomad/Nomad4TBOne/ \
  2>&1 | tee /opt/nomad/restore.log
```

**Resuming:** Re-run the same `rsync` command.

### 5.1 Verify restore

```bash
HDD_COUNT=$(find /media/nomad/BackupHDD -type f | wc -l)
RESTORED_COUNT=$(find /media/nomad/Nomad4TBOne -type f | wc -l)
echo "HDD: $HDD_COUNT  Restored: $RESTORED_COUNT"
```

### 5.2 Restart services

```bash
sudo systemctl start kj-controller

# Rebuild external catalog if needed
# (happens automatically on kj-controller startup if index is stale)
```

---

## Phase 6: Cleanup

### 6.1 Unmount backup HDD

```bash
sudo umount /media/nomad/BackupHDD
```

### 6.2 Downgrade GCS storage class (for long-term archive)

```bash
# Move to Coldline ($0.004/GB/month ≈ $13/month for 3.3TB, vs $66 Standard)
gcloud storage buckets update gs://nomad-usb-backup \
  --default-storage-class=COLDLINE
```

Note: changing the default class only affects new objects. To move existing objects:
```bash
gcloud storage objects update gs://nomad-usb-backup/** --storage-class=COLDLINE
```

### 6.3 Clean up logs

```bash
rm /opt/nomad/gcs-upload.log /opt/nomad/hdd-copy.log /opt/nomad/restore.log
```
