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

## Prerequisites

- [ ] 4TB portable HDD connected to NomadPC
- [ ] `gcloud` CLI installed on NomadPC
- [ ] Authenticated to `nomadkaraoke` GCP project
- [ ] `tmux` installed (for resumable sessions)

---

## Phase 1: Setup

### 1.1 Install gcloud CLI

```bash
# On NomadPC
curl -fsSL https://dl.google.com/dl/cloudsdk/channels/rapid/downloads/google-cloud-cli-linux-x86_64.tar.gz -o /tmp/gcloud.tar.gz
tar -xf /tmp/gcloud.tar.gz -C /opt/nomad/
/opt/nomad/google-cloud-sdk/install.sh --quiet --path-update true
source ~/.zshrc
```

### 1.2 Authenticate

```bash
gcloud auth login
gcloud config set project nomadkaraoke
```

### 1.3 Create GCS bucket

```bash
gcloud storage buckets create gs://nomad-usb-backup \
  --location=us-central1 \
  --storage-class=STANDARD \
  --uniform-bucket-level-access
```

> Standard storage at $0.020/GB/month. 3.3 TB ≈ $66/month.
> Switch to Nearline ($0.010/GB) or Coldline ($0.004/GB) after reformat if keeping long-term.

### 1.4 Install tmux (if needed)

```bash
sudo apt install -y tmux
```

---

## Phase 2: Cloud Backup (GCS Upload)

All commands run inside a tmux session so they survive SSH disconnections.

### 2.1 Start tmux session

```bash
tmux new-session -s gcs-upload
```

To reattach later: `tmux attach -t gcs-upload`

### 2.2 Upload to GCS

```bash
gcloud storage rsync \
  /media/nomad/Nomad4TBOne \
  gs://nomad-usb-backup \
  --recursive \
  --checksums-only \
  --no-clobber \
  -v \
  2>&1 | tee /opt/nomad/gcs-upload.log
```

**Key flags:**
- `--recursive` — all subdirectories
- `--checksums-only` — skip files that already exist with matching checksums (enables resume)
- `--no-clobber` — never overwrite existing files (safe resume)
- `-v` — verbose progress

**Resuming after interruption:** Just re-run the same command. It skips already-uploaded files automatically.

**Estimated time:** ~6 days at 50 Mbps upload. Monitor with:

```bash
# Check progress (file count in bucket)
gcloud storage ls gs://nomad-usb-backup --recursive 2>/dev/null | wc -l

# Check bucket size
gcloud storage du gs://nomad-usb-backup --summarize

# Tail the log
tail -f /opt/nomad/gcs-upload.log
```

### 2.3 Verify cloud backup

```bash
# Compare file counts
LOCAL_COUNT=$(find /media/nomad/Nomad4TBOne -type f | wc -l)
CLOUD_COUNT=$(gcloud storage ls gs://nomad-usb-backup --recursive | grep -v '/$' | wc -l)
echo "Local: $LOCAL_COUNT  Cloud: $CLOUD_COUNT"

# Dry-run rsync to find any differences
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
# Identify the drive
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

**Resuming:** Re-run the same `rsync` command — it skips matching files.

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

### 6.2 Downgrade GCS storage class (optional, for long-term archive)

```bash
# Move to Coldline ($0.004/GB/month ≈ $13/month for 3.3TB, vs $66 Standard)
gcloud storage buckets update gs://nomad-usb-backup \
  --default-storage-class=COLDLINE
```

### 6.3 Clean up logs

```bash
rm /opt/nomad/gcs-upload.log /opt/nomad/hdd-copy.log /opt/nomad/restore.log
```

---

## Quick Reference: Resume Any Step

| Phase | Resume command |
|-------|---------------|
| GCS upload | `tmux attach -t gcs-upload` — if dead, re-run the rsync command |
| HDD copy | `tmux attach -t hdd-copy` — if dead, re-run the rsync command |
| Restore | `tmux attach -t restore` — if dead, re-run the rsync command |

All rsync commands are idempotent. Re-running skips completed files.
