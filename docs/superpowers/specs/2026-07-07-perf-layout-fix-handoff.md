# Handoff: KJ Controller 4K-playback performance — remaining work

**Date:** 2026-07-07
**For:** a fresh Claude session continuing this work with no prior context.
**Read first:** repo `CLAUDE.md` (device access + deploy safety), and the design spec
[`2026-07-06-perf-monitoring-design.md`](2026-07-06-perf-monitoring-design.md).

---

## 1. TL;DR

The goal was to diagnose and fix subtle **4K video frame-drops** on NomadPC (the live karaoke
mini-PC, Intel N97 iGPU). Three things have shipped and are live in prod; **two pieces remain**:

- **Remaining A (PRIMARY — Andrew chose to build this):** move the scrolling ticker **off** the
  video into a **reserved top strip**, shrinking the video below it. Must be **engine-agnostic**
  (works for both mpv and VLC — Andrew wants to keep both usable as a per-file fallback).
- **Remaining B (optional, cheaper lever):** mpv always runs the `rubberband` pitch filter even at
  pitch 0 — investigate whether it's a meaningful CPU cost and make it conditional.

## 2. What already shipped (all merged to main + deployed + verified)

| Version | What | Key files |
|---|---|---|
| **v0.69.0** | Performance monitor: 1 Hz `PerfSampler` + ring buffer, `GET /perf/stream`, collapsible "Performance" panel, A/B toggles (overlay / compositor / GPU-clock / VNC), VNC auto-pause during playback | `kj-controller/perf_sampler.py`, `routes.py` (`/perf/*`), `static/app.js`, `templates/index.html`, `set-gpu-clock.sh` |
| **v0.70.0** | Perf **recording** (labeled JSONL sessions + summaries + download), **CDG-aware health** (don't flag benign 300fps→60Hz decimation as red), panel reorg | `kj-controller/perf_recorder.py`, `routes.py` (`/perf/record/*`) |
| **v0.73.0** | **GPU auto-pin**: pin iGPU min-clock to max while a song plays, unpin when idle (drops track the GPU stalling below its 1200 MHz ceiling) | `perf_sampler.py` (`_maybe_autopin`), `config.py` (`auto_pin_gpu_during_playback`) |

Device-side one-time artifacts already applied on NomadPC: `/etc/sudoers.d/kj-perf` (systemctl
overlay-display + set-gpu-clock.sh) and `chmod +x set-gpu-clock.sh`. See MINIPC-SETUP.md §5.7.

## 3. Root-cause findings (the important part)

Measured live + from Andrew's own recorded sessions (in
`/Users/andrew/Projects/nomadkaraoke/kjbox-performance-data-recordings/`, 9 labeled `.jsonl` files):

- **720p is completely fine** (0 drops, all green). **4K VP9 is right at the edge** of the N97 iGPU
  (GPU pegged 88–99%).
- **Drops track the GPU clock, not heat.** In the recording that dropped 168 frames the iGPU
  averaged **~1031 MHz**; the clean run averaged **~1115 MHz** (ceiling is 1200). Temps were a cool
  **64–69 °C** throughout — **not thermal throttling**. → hence the GPU auto-pin (v0.73.0).
- **Overlay-over-video is a real lever.** Andrew's controlled A/B, same session, same temp:
  `4K + overlays + VNC` = **mpv 122 % CPU, 168 drops**; `4K + VNC, no overlays` = **mpv 74 %,
  3 drops**. The full-screen animated transparent ticker composited over the changing 4K video is
  the cost.
- **BUT it's a tight-margin / intermittent effect.** On a *fresh* device I could **not** reproduce
  the 122 % blowup — same file, same overlay + VNC → mpv 74 %, 0 drops, GPU boosted to 1147. So the
  overlay is "the straw", and whether it tips depends on marginal GPU-boost behaviour.
- **VLC is not a 4K option** — software-decodes 4K at ~15 fps (choppy), worse than mpv. But Andrew
  wants both engines available per-file, which is why the fix must be engine-agnostic.
- **mpv-overlay-add works but is mpv-only.** Drawing the ticker *inside* mpv (via `overlay-add`
  IPC) keeps the cheap page-flip path (74 %/0 drops, validated live). Rejected because it can't work
  for VLC.

## 4. Remaining A — the layout fix (BUILD THIS)

**Design (decided with Andrew):**
- Reserve a **top strip** (~80 px, config-driven) for the scrolling ticker. Render the video in the
  region **below** it. Because the ticker no longer sits over the changing video, the compositor
  never re-blends a full-screen animated layer over it.
- **Only the scrolling ticker moves** to the strip. The "Scan to Sing" QR (small corner) and the
  countdown **stay overlaid** on the (now slightly smaller) video — they're static/occasional and
  cheap, not the perf culprit.
- **Keep** the GPU auto-pin (already shipped) as a complementary margin.
- Won't reach the theoretical minimum (only a fullscreen-video-with-nothing-on-top can page-flip),
  but removes the thing the data implicates and is engine-flexible.

**Already validated (prototype):**
- mpv windowed renders cleanly in the sub-region:
  `mpv --no-border --geometry=1920x1000+0+80 <file>` → `osd-dimensions` w=1920 h=1000, `hwdec=vaapi`
  intact, 0 drops. Screenshot confirmed a clean top strip with the video below. **No window-manager
  gymnastics needed for mpv.**
- `wmctrl` **is** installed on the device; `xdotool` is **not**.

**Build steps (per file):**

1. **`config.py`** — add default `video_top_margin_px` (e.g. `80`). When `0`, keep the old
   fullscreen behaviour (clean rollback path). Also need screen size — the display is 1920×1080
   (`DISPLAY=:0 xrandr`); read from config or detect once.

2. **`mpv_manager.py`** (two launch command variants, ~lines 191-217, one for `pulse` one for
   `alsa`): replace `--fs` with `--no-border --geometry=<W>x<H-margin>+0+<margin>` when
   `video_top_margin_px > 0`. Keep `--fs` when `0`.

3. **`vlc.py`** (~line 144-154): drop `--fullscreen` when margin>0 and **position the VLC window
   after launch with `wmctrl`** (VLC's own geometry flags are unreliable). **This is the fiddly part
   — validate it on the device first** (find the VLC window by title/class, `wmctrl -r <win> -e
   0,0,<margin>,<W>,<H-margin>`, and remove any fullscreen state). If VLC positioning proves
   unreliable, an acceptable fallback is `video_top_margin_px` only affecting mpv, with VLC staying
   fullscreen (documented) — but try to make both work.

4. **`desktop/overlay_engine.py`** — **the key subtlety.** Today it draws a full-screen transparent
   surface and calls `win.queue_draw()` (whole window) when the ticker animates, so the compositor
   re-blends the full screen (including the video region) every ticker frame. Change it so an
   animated overlay **damages only its own bounding box** (e.g. `queue_draw_area(0,0,W,strip_h)` for
   the ticker) instead of the whole surface. Then the ticker's 30 fps animation only recomposites
   the top strip, never the video region — that's what actually removes the over-video cost. (QR /
   countdown then damage only their own small regions when they change.) Position the ticker painter
   in the strip. See `overlay_painters.py:TickerPainter` and `overlay_engine.py:_on_frame/_on_draw`.

5. **Measure before/after** with the recording tool (below): record a 4K session on each engine with
   the ticker, before and after, and compare `render_fps`, real drops, mpv CPU, GPU busy.

**Risks / unknowns:** VLC+wmctrl positioning reliability; the overlay partial-redraw change; the
benefit is margin-dependent so *measure* don't assume. This changes how video displays on a **live
production box** — follow CLAUDE.md deploy safety.

## 5. Remaining B — rubberband lever (optional, cheaper)

The app always launches mpv with `--af=@rb:rubberband` (pitch-shift filter), even at pitch 0
(`mpv_manager.py` both launch commands). A prototype mpv *without* it used ~11 % CPU (not a clean
comparison — it also had `--no-audio`), but continuous rubberband could be a real chunk of mpv's
~70 % CPU. **Investigate:** measure mpv CPU with vs without rubberband, same file, *with* audio,
isolated. If significant, make rubberband **conditional** — only inserted when pitch ≠ 0 (mpv
supports `af add`/`af remove` over IPC, or relaunch). See how pitch is currently applied in
`mpv_manager.py` (`set_pitch` / the `@rb` label). Note: as of 2026-07-06 Andrew appeared to be
poking at this himself on the device — check with him before duplicating.

## 6. How to work on this

**Device access:** `ssh nomadpctunnel` (Cloudflare tunnel, works remotely; may prompt browser auth
once). Web UI past Cloudflare Access: `curl` with `$KJBOX_CF_ACCESS_CLIENT_ID/SECRET` headers (see
CLAUDE.md). Read-only SSH is always safe; **playing videos / restarting services / deploying needs
the "no live event" window** — as of 2026-07-06 there was no event "for a few days"; **re-confirm
with Andrew before testing on the device.**

**Testing perf (no custom scripts needed — it's all shipped):**
- Play a file: `POST /play {"file_path": "..."}`. Stop: `POST /control {"action":"stop"}`.
- Switch engine (while idle): `POST /renderer {"mode":"mpv"|"vlc"}`.
- Read live metrics: `GET /perf/stream` → `.now` has `render_fps`, `fps_target`, `drops`,
  `drops_meaningful`, `gpu.{busy_pct,act_mhz,max_mhz}`, `cpu.{mpv,Xorg,x11vnc,overlay_engine,...}`,
  `video.{codec,w,h,container_fps,hwdec}`, `health`.
- **Record before/after:** `POST /perf/record/start {"label":"..."}` → play → `POST
  /perf/record/stop` → `GET /perf/record/<id>/summary`. Recordings land in
  `/home/nomad/kjdata/perf_recordings/`.
- 4K test file: `/opt/nomad/downloads/youtube/Bastille - Pompeii [yt-3QhmiCHjRHE].mp4` (VP9, 4K,
  25 fps). 720p: `/opt/nomad/downloads/NOMAD-720p/NOMAD-0001 - ....mp4`.
- To simulate a VNC client (load), there may be leftover `/tmp/vncload.py` + `/tmp/vncvenv` on the
  device (ephemeral — regenerate if gone; it's a minimal RFB client that auths against
  `~/.vnc/passwd` and pulls framebuffer updates).

**Deploy flow:** merge to `main` → device auto-deploys within ~60 s (git pull + restart
kj-controller on Python changes; also restarts overlay-display). Use PRs with `@coderabbitai ignore`
in the body; run `coderabbit review --agent --type committed --base main` locally before the PR.
Squash-merge (matches repo convention). `gh pr merge --squash` **without** `--delete-branch` (the
latter fails because `main` is checked out in the sibling clone; delete the remote branch separately
with `git push origin --delete <branch>`).

**Gotchas:**
- The RTK shell wrapper mangles some `&` / piped commands ("parse error near `&`"). Redirect to a
  file and use the Read tool, or prefix with `rtk proxy <cmd>`.
- Flaky test: `tests/e2e/test_rotation_e2e.py::TestMultiSingerPillCreation` — fails intermittently,
  passes on retry, **unrelated** to this work.
- `pgrep -f <word>` matches your own SSH command line (which contains `<word>`) — false positives;
  use `pgrep -x` or `ps -eo pid,args | grep '[m]pv'`.
- Run tests: `cd kj-controller && python -m pytest tests/unit/test_perf_sampler.py
  tests/unit/test_perf_recorder.py tests/integration/test_perf_routes.py -q`.

## 7. Key resources

- Design spec: `docs/superpowers/specs/2026-07-06-perf-monitoring-design.md`
- CHANGELOG: entries for v0.69.0 / v0.70.0 / v0.73.0 (2026-07-06), MINIPC-SETUP §5.7.
- Andrew's recordings (durable data):
  `/Users/andrew/Projects/nomadkaraoke/kjbox-performance-data-recordings/` — 9 sessions covering
  720p/4K × mpv/VLC × overlays/no-overlays × VNC. Load each `.jsonl` (skip the `_meta` first line),
  filter `playing==true`, aggregate `render_fps` / `drops_delta.vo` (only where `drops_meaningful`)
  / `gpu.act_mhz` / `cpu.*` / `temp_c`. (The ad-hoc analysis scripts used this session lived in a
  session-scratchpad and are gone — re-derive trivially from the recordings if needed.)
