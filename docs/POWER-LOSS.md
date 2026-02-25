# Power Loss Resilience

NomadPC gets unplugged at the end of gigs without a clean shutdown — someone pulls the power while packing up. This is expected behavior, not an error condition. The system is hardened so that every power loss results in a clean, automatic recovery on the next boot.

## What Happens on Power Loss

When the power cable is yanked:

1. **All running processes terminate instantly** — no shutdown hooks, no cleanup, no flush
2. **Any in-progress disk writes are interrupted** — data in RAM buffers that hasn't been flushed to disk is lost
3. **The ext4 journal protects filesystem integrity** — metadata operations (file creation, deletion, renames) are journaled and will replay on next boot
4. **Application state files survive** — atomic writes ensure config.json and media_index.json are never half-written

What does **not** happen:
- No filesystem corruption (ext4 journal handles this)
- No config file loss (atomic writes protect against partial writes)
- No need to run fsck manually (it runs automatically if needed)
- No need to SSH in and fix anything — the system comes back on its own

## What Happens on Power-Up

When the power cable is plugged back in:

| Time | What Happens |
|------|-------------|
| 0s | BIOS powers on automatically (AC Power Loss → "Power On") |
| ~5s | Linux kernel starts, ext4 journal replays any incomplete operations |
| ~10s | systemd starts services in dependency order |
| ~15s | `kj-autodeploy` starts polling GitHub for updates |
| ~18s | `kj-controller` starts: loads config, launches dual VLC, starts web server |
| ~18s | Filler music begins playing through HDMI |
| ~20s | Web UI available at `https://nomadpc.local` |

Total time from plug-in to fully operational: **~20 seconds**.

If the SSD is not plugged in (forgotten in the bag), the system still boots normally — `nofail` in fstab means the missing drive is skipped. The web UI will work but catalog search and external media playback won't be available until the SSD is connected and a rescan is triggered.

## Five Layers of Protection

### 1. Filesystems: ext4 with journaling

Both the root drive (NVMe) and the media SSD (USB) use ext4 with journaling enabled.

| Drive | Device | Filesystem | Mount Options |
|-------|--------|------------|---------------|
| Root (NVMe) | `/dev/nvme0n1p2` | ext4 | `errors=remount-ro` |
| Media SSD (USB) | `/dev/sda1` | ext4 | `noatime,nofail` |

**Why this matters:** ext4's journal records metadata changes before they happen. If power is lost mid-operation, the journal replays on next boot, restoring the filesystem to a consistent state. This is why the SSD was reformatted from exFAT (no journal) to ext4 on 2026-02-24.

**What `noatime` does:** Normally, Linux updates the "last accessed" timestamp every time a file is read. For a media drive with 413,670 files, this creates unnecessary write operations. `noatime` skips these updates, reducing wear and the amount of journaled metadata.

**What `nofail` does:** If the SSD isn't plugged in at boot, systemd skips it instead of dropping to emergency mode.

### 2. Atomic JSON writes (application code)

Three JSON files are written at runtime:

| File | Written By | When |
|------|-----------|------|
| `config.json` | `config.py:save_config_value()` | Audio device change, display resolution change, brand preferences |
| `media_index.json` | `media.py:MediaIndex.save()` | After media scan, download, or delete |
| `overlays.json` | `overlay.py:OverlayManager._save()` | Any overlay create/update/delete/toggle |

All three use the same atomic write pattern:

```python
# 1. Write to a temporary file in the same directory
fd, tmp_path = tempfile.mkstemp(dir=same_directory, suffix='.json')

# 2. Write data and force it to disk
with os.fdopen(fd, 'w') as f:
    json.dump(data, f, indent=2)
    f.flush()
    os.fsync(f.fileno())  # force kernel buffers to physical disk

# 3. Atomic rename replaces the old file
os.replace(tmp_path, target_path)  # atomic on ext4
```

**Why this matters:** Without atomic writes, `open(path, 'w')` truncates the file to zero bytes before writing new content. If power is lost between the truncate and the write completing, the file is empty or partially written — the app loses its config on next boot.

With the atomic pattern, the original file is untouched until `os.replace()` completes. Since rename is atomic on ext4, the file is either fully old or fully new — never corrupt.

**What if power is lost during the temp file write?** The temp file is orphaned and the original file is untouched. On next boot, the app loads the original file normally. The orphaned temp file is harmless (and can be cleaned up manually if desired).

### 3. Kernel panic auto-reboot

```
# /etc/sysctl.d/99-power-loss.conf
kernel.panic = 10
```

If the kernel panics (rare, but possible after unclean shutdown), the system automatically reboots after 10 seconds instead of hanging forever with a panic message on screen. Without this, a kernel panic at a venue would require someone to physically hold the power button.

### 4. Journal size cap

```
# /etc/systemd/journald.conf.d/size-limit.conf
[Journal]
SystemMaxUse=200M
```

The systemd journal records all service logs. Without a cap, it grows indefinitely (was 890MB before this change). After an unclean shutdown, journald must flush and potentially repair its files on boot — a smaller journal means this completes faster. Capped at 200M which retains several days of logs.

### 5. BIOS: Power On after AC loss

The BIOS is configured to automatically power on when AC power is restored. This means plugging in the power cable boots the system — no need to press the power button.

**Setting:** Advanced → Power Management → AC Power Loss → "Power On"

## What's NOT Protected

These scenarios still require manual intervention:

| Scenario | Impact | Recovery |
|----------|--------|----------|
| SSD hardware failure | Media playback unavailable | Replace SSD, restore from backup |
| NVMe hardware failure | System won't boot | Reinstall OS, follow MINIPC-SETUP.md |
| Corrupted git repo on device | Auto-deploy stops working | `ssh nomadpc 'cd /opt/nomad/kjbox && git reset --hard origin/main'` |
| VLC process wedged after crash | Audio/video stuck | Service auto-restarts (Restart=always, 5s delay) |

## Verification

After any power loss event, you can verify health remotely:

```bash
# Quick check — are services running?
ssh nomadpc 'systemctl is-active kj-controller kj-autodeploy'

# Filesystem state (should say "clean")
ssh nomadpc 'sudo tune2fs -l /dev/sda1 | grep "Filesystem state"'

# Boot errors (filter noise)
ssh nomadpc 'journalctl -b | grep -iE "error|fail|corrupt" | grep -v "rfkill\|Bluetooth\|firmware\|ACPI\|iwlwifi\|mmc0\|raid6"'

# Full startup log
ssh nomadpc 'journalctl -u kj-controller -b --no-pager'
```

## History

| Date | Change |
|------|--------|
| 2026-02-24 | Reformatted 4TB SSD from exFAT to ext4 (PR #16, v0.8.3) |
| 2026-02-24 | Added atomic writes to config.py and media.py |
| 2026-02-24 | Set kernel.panic=10, journal cap 200M, SSD noatime |
| 2026-02-24 | Verified recovery after deliberate power yank — clean boot, all services healthy |
