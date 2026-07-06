# Plan: NomadPC full system / kernel upgrade (hygiene) — DEFERRED until physical access

**Created:** 2026-07-05
**Status:** DEFERRED — do this later in the week **when the NomadPC is physically in hand**.
**Why deferred:** It requires a reboot. Done remotely with **no physical console access**, a
bad upgrade (broken networking, display stack, or `cloudflared`) would lock us out of a live
production box. Nothing here is urgent — the AV1 crash is already fixed (see
[CHANGELOG.md](../CHANGELOG.md) 2026-07-05); this is periodic hygiene.

> **Precondition — do NOT start without all of these:**
> 1. **Physical access** to the NomadPC (monitor + keyboard, or at minimum the ability to
>    power-cycle and boot a previous kernel from the GRUB menu).
> 2. **No event on** and no event imminent (device idle).
> 3. A verified rollback path (Timeshift snapshot + prior kernels in GRUB — see §3).

## 1. Goal & scope

Bring the box current after ~6 months of unapplied updates. This is **hygiene**, not a bug fix.

- **In scope:** `sudo apt full-upgrade` of everything in the Ubuntu/Mint repos — **372 pending
  updates** as of 2026-07-05, including kernel `6.8.0-100 → 6.8.0-134`, **mesa `24.2 → 25.2`**,
  `libva`/`intel-media-va-driver` point bumps, plus a reboot.
- **NOT in scope / NOT needed:** upgrading mpv/ffmpeg/dav1d. Ubuntu noble ships nothing newer
  (mpv 0.37, ffmpeg 6.1.1, dav1d 1.4.1 are the repo latest), and they are **irrelevant to the
  AV1 crash** — that was the free VA driver, already fixed with `intel-media-va-driver-non-free`.
  Only pursue a source build / PPA for those if a *future* need arises; it is not part of this
  hygiene pass.
- **Distribution release upgrade (Mint 22.1 → 22.x / 23):** out of scope. This plan is
  *within-release* updates only. A release upgrade is a separate, bigger decision.

## 2. Baseline (measured 2026-07-05 — record again before upgrading)

| Component | Current |
|---|---|
| OS | Linux Mint 22.1 (Ubuntu 24.04 "noble" base) |
| Kernel (running) | 6.8.0-100-generic; installed also: 6.8.0-71, 6.8.0-51 |
| Kernel (candidate) | 6.8.0-134 (`linux-generic` 6.8.0-134.134) |
| mesa (`mesa-va-drivers`) | 24.2.8 → candidate 25.2.8 |
| libva2 | 2.20.0-2ubuntu0.1 → ...0.2 |
| VA driver | **`intel-media-va-driver-non-free` 24.1.0+ds1-1** (the AV1 fix — must survive) |
| mpv / ffmpeg / dav1d | 0.37.0 / 6.1.1 / 1.4.1 (repo latest; unchanged by this upgrade) |
| GPU | Intel N97 (Alder Lake-N), iHD driver, `/dev/dri/renderD128` |
| Root FS | `/dev/nvme0n1p2`, 468G, ~358G free |
| Remote access (all survive reboot) | `cloudflared` (enabled), `tailscaled` (enabled, 100.82.90.111), LAN/mDNS `nomadpc.local` |

Re-capture live before starting:
```bash
ssh nomadpc 'uname -r; apt list --upgradable 2>/dev/null | wc -l; \
  dpkg -l "linux-image-*" intel-media-va-driver-non-free mesa-va-drivers libva2 | grep ^ii'
```

## 3. Pre-flight & rollback prep (do BEFORE touching apt)

1. **Timeshift snapshot** (installed but never configured — configure it now):
   ```bash
   # Configure RSYNC snapshots to the internal nvme (root has ~358G free), then snapshot:
   sudo timeshift --create --comments "pre full-upgrade 2026-07-05" --tags D
   sudo timeshift --list        # confirm the snapshot exists before proceeding
   ```
   (If Timeshift's target defaults to `/dev/sda1`, point it at the root nvme or an external disk
   with room for the ~87G used root — check the GUI or `/etc/timeshift/timeshift.json`.)
2. **Prior kernels stay in GRUB.** 6.8.0-71 and 6.8.0-51 remain installed → if 6.8.0-134 won't
   boot, pick a previous kernel from **GRUB → Advanced options for Linux Mint** (needs the
   physical console). Confirm they're still installed before upgrading.
3. **Record exact versions** for targeted downgrade if needed:
   ```bash
   ssh nomadpc 'dpkg -l | grep ^ii > /tmp/pkgs-pre-upgrade-2026-07-05.txt'
   ```
4. **Confirm all three remote paths work right now** (so at least the non-display ones return
   after reboot): `ssh nomadpc` (LAN), `ssh nomadpcts` (Tailscale), `ssh nomadpctunnel` (tunnel).
5. **Note the HDMI-audio boot fix** exists (`fix-hdmi-audio.sh`, ExecStartPre of kj-controller)
   — it auto-detects the HDMI PCM device, so a kernel bump that reshuffles Intel HDA pins should
   self-heal, but verify audio after reboot (see §5).

## 4. Upgrade steps (physical console attached, device idle)

```bash
# 1. Refresh + full upgrade
sudo apt-get update
sudo apt-get full-upgrade        # review the list; expect kernel, mesa, libva, ~370 pkgs
# (Mint alternative: use the Update Manager GUI / `mintupdate` if you prefer its staging.)

# 2. Clean up old auto-removable deps (keep at least one prior kernel!)
sudo apt-get autoremove --purge  # VERIFY it is NOT removing 6.8.0-71/-51 before confirming

# 3. Reboot into the new kernel
sudo reboot
```

**Watch the physical screen through POST → GRUB → login.** If it hangs or the display is
broken, reboot and choose a prior kernel from GRUB Advanced options.

## 5. Post-reboot verification checklist

Run each; every one must pass before declaring success:

- [ ] **Boots to desktop** on the physical screen (no black screen / X failure). If X is broken,
      suspect the mesa 24→25 jump — see [TROUBLESHOOTING.md](../TROUBLESHOOTING.md) display
      sections.
- [ ] **Kernel** is `6.8.0-134` (or intended): `uname -r`.
- [ ] **All remote paths** back: `ssh nomadpc`, `ssh nomadpcts`, `ssh nomadpctunnel`.
- [ ] **Services up:** `systemctl is-active kj-controller cloudflared tailscaled` (+ display
      manager / VNC / websockify as applicable).
- [ ] **kj-controller serves:** `curl -s http://127.0.0.1:5001/status` returns JSON with
      `renderer.mode`.
- [ ] **VA driver survived** (critical — the AV1 fix): `dpkg -l intel-media-va-driver-non-free`
      shows `ii`; `LIBVA_DRIVER_NAME=iHD vainfo | grep -i av1` shows `VAProfileAV1Profile0`.
- [ ] **AV1 still hardware-decodes** (new kernel/i915/mesa didn't regress it): play an AV1 file
      through the app and confirm mpv `hwdec-current=vaapi`, no new coredump
      (`coredumpctl list | grep -ci mpv` unchanged), STAT stays `SLsl`. Repro recipe:
      [archive/2026-07-05-mpv-av1-crash-findings.md](2026-07-05-mpv-av1-crash-findings.md).
- [ ] **H.264 control** plays (ABBA).
- [ ] **HDMI audio works** (kernel bump can reshuffle Intel HDA): play a song, confirm audio out;
      if silent, run `ssh nomadpc '/opt/nomad/kjbox/kj-controller/hdmi-diag.sh'` and see
      [HDMI.md](../HDMI.md) / [AUDIO.md](../AUDIO.md).
- [ ] **Rubberband pitch** works: nudge key ±1 in the UI, confirm no glitch.
- [ ] **yt-dlp download** works (system ffmpeg unchanged, but confirm): download one test song.
- [ ] **VLC engine** still works (switch renderer to vlc, play a song, switch back).

## 6. Rollback procedures

- **Won't boot / display broken:** GRUB → *Advanced options for Linux Mint* → previous kernel
  (6.8.0-71). Then investigate; optionally hold the bad kernel.
- **Boots but something's broken:** `sudo timeshift --restore` to the pre-upgrade snapshot
  (reboots into the restored system).
- **A single package regressed** (e.g. mesa): downgrade it specifically, e.g.
  `sudo apt-get install <pkg>=<old-version>` using `/tmp/pkgs-pre-upgrade-2026-07-05.txt`, then
  `apt-mark hold <pkg>`.
- **VA driver got replaced by the free one:** `sudo apt-get install intel-media-va-driver-non-free`.

## 7. Risks

| Risk | Likelihood | Mitigation |
|---|---|---|
| New kernel won't boot | Low (same 6.8 series) | GRUB → prior kernel (needs console) |
| mesa 24→25 breaks X / mpv `--vo=gpu` | Low–med | Timeshift restore; or downgrade mesa |
| HDMI audio device re-shuffles | Med | `fix-hdmi-audio.sh` auto-detects at boot; verify |
| Full-upgrade removes non-free VA driver | Low | It's manually-installed + satisfies `va-driver-all`; re-install if needed |
| Lockout (no console) | N/A here | **This plan requires physical access — that's the whole point of deferring** |

## 8. After a successful upgrade

- Add a dated entry to [CHANGELOG.md](../CHANGELOG.md) (kernel/mesa versions, anything that broke
  + how it was fixed).
- If AV1 HW decode still works on the newer kernel/mesa, note that the non-free driver remains the
  required piece. If a *newer* kernel/driver ever makes the **free** driver decode AV1 correctly,
  that's worth recording — but do not assume it.
