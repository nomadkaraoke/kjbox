# KJ Controller Performance Monitor — Design (Phase 1)

**Date:** 2026-07-06
**Status:** Approved (design) — pending implementation plan
**Author:** Andrew + Claude
**Scope:** Phase 1 = live performance measurement + A/B controls. Phase 2 (separate spec) = the structural compositing fix, validated with this tool.

---

## 1. Goal

Give the KJ a lightweight, always-capturing performance monitor in the web UI that shows — live and retroactively — **how close video playback is to the frame-drop threshold, and which stacked load to shed** — plus A/B controls to prove cause and effect on demand.

The trigger for this work: suspected subtle video/CDG frame-skipping that was hard to confirm ("could just be poorly-made tracks"). We needed a way to confirm or disprove suspects (overlay system, noVNC monitor) with confidence, and to keep watching during real shows. The monitor itself must be lightweight so it doesn't perturb the thing it measures.

## 2. Investigation findings that motivate this design

Measured live on NomadPC (Intel N97, 4 cores, Intel Alder Lake-N iGPU, X11/XFCE, mpv `hwdec=vaapi`). Heavy test file: 15 Mbps 4K H.264 30fps. Sampled 18–25s windows via `/proc` deltas, mpv IPC, VLC http status, and i915 sysfs.

**mpv, single loads — all absorbed, zero dropped frames:**

| Condition | vo-drops | Cost |
|---|---|---|
| 4K playing (normal: overlay + compositor, no VNC) | 0 | GPU 97–99%, mpv ~76% of one core |
| overlay-display stopped | 0 | frees ~11% of one core (Xorg+xfwm4+overlay) |
| xfwm4 compositor disabled (mpv page-flips) | 0 | frees ~15% of one core (Xorg 14→3%) |
| + simulated VNC client connected | 0 | +~35% of one core (x11vnc 17%, Xorg +14%) |

**mpv, everything stacked (scrolling ticker over video + 2-tab KJ polling + VNC preview):** **2 dropped frames / 18s (0.36%)**, GPU pegged 99.7% and stuck at 1000 MHz (never boosting to its 1200 MHz ceiling).

**Engine comparison, full stacked load, same file:**

| Engine | Dropped | CPU | Mechanism |
|---|---|---|---|
| mpv (hwdec=vaapi) | 2 / 18s | ~78% of one core | GPU-decoded → **GPU-bound**, tips at 99% |
| VLC (legacy, software decode) | 0 | ~206% (≈2 cores) | CPU-decoded → dodges the pegged GPU, but 3× heat/power |

**Conclusions:**
1. The stutter is real, marginal, and **additive** — it takes the whole stack at once to drop a couple of frames on mpv. Matches the "subtle, hard to tell" report.
2. **No single villain.** Overlay ≈11%, compositor ≈15%, VNC ≈35% of one core. Each is fine alone; together they cross mpv's GPU-bound threshold. VNC is the biggest single lever and the one that tipped it over.
3. **mpv is GPU-bound and the iGPU isn't boosting to its ceiling** under sustained load (1000 vs 1200 MHz) — a power/thermal cap; a likely direct fixable cause.
4. **VLC never drops but burns 3× the CPU** (no hwdec in the legacy path) — robust to GPU pressure but would thermal-throttle sooner and lose to a concurrent download. Not a free win.
5. **Content is not the cause** — the library is ~all 30fps; even 15 Mbps 4K decodes fine via hwdec.

These findings determine exactly which metrics the monitor surfaces.

## 3. Architecture (Approach A: sampler thread + on-demand panel)

```
overlay_engine.py ──writes──> /tmp/kj-overlay-perf.json (real FPS, raster ms)   [1/sec]
                                        │
Flask app ── PerfSampler (daemon thread, 1 Hz) ──────────┐
   ├─ mpv IPC  (frame-drop-count, vf-fps, hwdec, …) ──────┤
   ├─ VLC http (displayed/lostpictures) ──────────────────┤──> in-memory ring
   ├─ /proc    (per-proc CPU deltas) ─────────────────────┤    buffer (~300 samples
   ├─ i915 sysfs (GPU busy / act-vs-max freq) ────────────┤     = 5 min @ 1 Hz)
   ├─ ss :5900 (VNC client connected?) ───────────────────┤
   └─ sensors  (package temp) ────────────────────────────┘
                                        │
   GET  /perf/stream                ── returns ring buffer (polled only when panel open)
   POST /perf/toggle/{overlay|vnc|compositor|gpu-clock}  ── A/B controls
                                        │
   Frontend: collapsible "Performance" panel (polls /perf/stream ~1 Hz WHEN OPEN)
```

The sampler is started once from `app.py`. Every source it reads was validated as cheap during the investigation.

## 4. Metrics collected

| Metric | Source | Rationale |
|---|---|---|
| Dropped frames (mpv `frame-drop-count`, `vo-delayed-frame-count`; VLC `lostpictures`) | mpv IPC / VLC http | The smoothness signal; engine-agnostic |
| Render FPS vs target (mpv `estimated-vf-fps`; VLC displayed-fps) | IPC / http | Holding 30? |
| GPU busy% + act-freq vs max-freq | i915 sysfs (`rps_act_freq_mhz`, `rps_max_freq_mhz`, `rc6_residency_ms`) | The real bottleneck; caught the 1000-vs-1200 cap |
| hwdec active (`hwdec-current`) | mpv IPC | Confirms GPU decode (VLC path = software) |
| Per-process CPU%: mpv/vlc, Xorg, x11vnc, overlay_engine, xfwm4 | `/proc/<pid>/stat` jiffy deltas | Shows what is stacking |
| Overlay real FPS + per-frame raster ms | `/tmp/kj-overlay-perf.json` (self-reported) | Currently uninstrumented |
| VNC client connected (bool) | `ss -tnH state established '( sport = :5900 )'` | The biggest lever |
| Package temp | `sensors` / thermal sysfs | Thermal headroom over a long show |
| `/status` poll latency | Flask `after_request` timing hook | Controller-side contention |
| Sampler self-CPU | `/proc` (own pid) | Proves the monitor is negligible |

**Derived health indicator** (green / amber / red) from GPU%, drop-rate, and temp thresholds. Thresholds chosen from the investigation (e.g. amber when GPU act-freq < max under load or drop-rate > 0; red on sustained drops or temp near throttle).

## 5. Backend components

- **`perf_sampler.py`** (new) — `PerfSampler` daemon thread + ring buffer + collector functions. Keeps one previous snapshot to compute CPU/GPU/frame deltas each tick. Fully degrades: any collector that fails returns `None` for its fields without stopping the sampler.
- **`mpv_manager.py`** — add `get_perf()` returning the perf properties in a **single batched IPC pass** (not N separate round-trips), reusing `_get_property`/`_send_ipc`. Must not add lock contention beyond one short call/sec.
- **`vlc.py`** — add `get_perf()` reading the existing `localhost:8080/requests/status.json` (displayed/lost/decoded pictures).
- **`routes.py`** — `GET /perf/stream` (returns ring buffer as JSON), `POST /perf/toggle/<control>`.
- **`overlay_engine.py`** — write `/tmp/kj-overlay-perf.json` once/sec from the per-frame `dt` it already computes; wrap `_on_draw` in `time.perf_counter()` for raster-ms. ~15 lines, no behaviour change.
- **Quick win folded in:** eliminate the `pgrep` subprocess spawned on every `/status` poll (cache browser-mode state) — an avoidable per-poll fork/exec found during the investigation.

## 6. Frontend

- New collapsible **"Performance"** panel in the KJ UI (`templates/index.html`, `static/app.js`, `static/style.css`), styled like the existing system-stats widget.
- When expanded, polls `GET /perf/stream` ~1 Hz and renders: the health indicator, current numbers, and compact sparklines (last ~5 min) for dropped-frames, GPU%, and per-process CPU.
- **Polls only when expanded** — zero cost when collapsed. Reuses the existing 2 s `/status` signal to know play state (for VNC auto-pause and labelling).

## 7. A/B controls

- **VNC auto-pause during playback** *(a fix, not just a toggle)* — frontend auto-disconnects the noVNC stream (existing `disconnectVnc`) when `/status` shows a song actively playing, and reconnects (`connectVnc`) when idle/between songs. Config flag, default **on**. Pure frontend, low risk. Directly removes the single biggest stacked load with no operator effort.
- **Manual overlay + VNC toggles** — panel buttons. VNC uses the existing client-side `connect/disconnectVnc`. Overlay calls `POST /perf/toggle/overlay` → `sudo systemctl start|stop overlay-display` (new `/etc/sudoers.d` entry scoped to that unit, following the `fix-hdmi-audio.sh` pattern).
- **Compositor + GPU-clock controls** *(labelled experimental)* — `POST /perf/toggle/compositor` runs `xfconf-query -c xfwm4 -p /general/use_compositing -s <bool>` (Flask already runs as `nomad` with `DISPLAY=:0`); `POST /perf/toggle/gpu-clock` raises `rps_min_freq_mhz` to pin the iGPU at its 1200 MHz ceiling (needs root → a small sudo helper script, like `fix-hdmi-audio.sh`). The panel shows current state for each and notes that compositor toggling briefly flickers the screen and that GPU-clock/compositor changes are not persisted across reboot.

## 8. Staying lightweight (non-perturbing)

1 Hz sampling; batched mpv IPC (one round-trip, not five); `/proc` + sysfs reads are sub-millisecond; **UI polls only when the panel is open**; ring buffer is memory-only (no disk writes on the production box). The sampler's own CPU is surfaced in the panel so its overhead is visible and auditable.

## 9. Deployment & rollout

- Backend changes require a `kj-controller` restart (interrupts active playback); the overlay-FPS change requires an `overlay-display` restart (brief overlay flicker, no playback hit). Both acceptable in the current no-event window.
- **Staged:** (1) sampler + metrics + read-only panel first (safe); (2) the A/B toggle endpoints + sudoers/helper; (3) VNC auto-pause. Each independently shippable.
- New device-side artifacts: one sudoers entry for `systemctl … overlay-display`, one root helper script for the GPU-clock write. Document in `docs/MINIPC-SETUP.md` and add a `docs/CHANGELOG.md` entry.

## 10. Testing

- Unit tests for each collector with mocked `/proc`, sysfs, mpv IPC, VLC http, and `ss` output; ring-buffer add/evict behaviour; graceful degradation when a source is absent (parsing is the main risk surface). Target the repo's 70%+ coverage bar.
- Manual live verification on-device: replay the investigation's A/B sequence and confirm the panel reports the same deltas measured over SSH (dropped-frames, GPU%, per-proc CPU).

## 11. Explicitly out of scope (Phase 2, separate spec)

- The structural fix itself: routing over-video overlays through mpv (`overlay-add`) so mpv becomes the sole fullscreen window and the compositor can unredirect / the video can page-flip.
- Any *permanent* change to VNC encoding parameters, the compositor setting, or the GPU power limit — those are decided *after* this tool provides before/after proof.

## 12. Risks / open questions

- Batched mpv IPC must genuinely be one call; if mpv doesn't support multi-property get in one command, fall back to a minimal fixed set (≤3 properties) to bound lock contention.
- i915 sysfs paths differ by card index (`card0` vs `card1`); collector must auto-detect (as the investigation script did).
- GPU "busy%" via rc6 residency is a rough proxy — pair it with act-vs-max frequency (the more reliable bottleneck signal observed).
- `sensors` may be absent on some devices → temp collector must degrade to thermal-zone sysfs or `None`.
