# Playability Checker — Confidence Run Findings (2026-06-27)

On-device confidence run (plan Task 11) executed on NomadPC (nomadpctunnel). Xvfb
installed; modules staged in `/tmp/kj-play` (NOT the live `/opt/nomad/kjbox` tree); run
with the live venv python (has Pillow). Never touched the live `:0` display or `hw:0,0`.

## What was run

1. **8-file sanity** (4 known-corrupt divebar + good + CDG) — validated the real
   Xvfb+VLC+mpv path works.
2. **20-file diverse mix** — SSD MP4 + SSD CDG, recent YouTube, divebar good/bad, local
   4K/720p, local CDG.
3. **Full night re-check (77 files)** — every file played/attempted on 2026-06-25→26,
   resolved via the local folders + the `external_media.db` catalog (paths under
   `/media/nomad/Nomad4TBOne/HyperMule/Master Karaoke Folder/…`). 1 of 78 unresolved =
   `divebar__NOMAD - Mirah - Gone Sugaring.mp4` (the original truncated file, deleted that
   night).

Frames were saved per renderer per file and assembled into labelled contact sheets for
visual verification — the green cells show real captured karaoke frames (KARAOKE VERSION
cards, title cards, video), confirming the checker actually verifies rendering.

## Verdict accuracy

**True catches (real "would play wrong" files from the night):**
- `divebar__ESK - 3 Doors Down - Kryptonite.mp4` — truncated (`moov atom not found`).
- `divebar__JSK - Savage Garden - To The Moon And Back.mp4` — **audio-only, no video
  stream** (would play sound with a black screen — the exact failure mode the project
  targets).

**False-positive class found AND fixed — CDG black-intro capture.**
Three CDGs (`MM-11259`, `CKO319-14`, `ABBA Ring Ring`) were flagged "no video frame" by
both renderers. Investigation:
- `ffmpeg` `cdgraphics` decode showed all three have graphics almost continuously,
  including at the 40% mark — so the `.cdg` files are fine.
- Zip internals matched (`.cdg`/`.mp3` same basename) — not a pairing issue.
- Root cause: **`check()` passed `duration=None` for CDG, so capture used `start=0`** —
  the first ~3s. **CD+G builds its image incrementally from the start; the opening seconds
  are black** before any graphics are drawn. The checker was photographing the black intro.
- **Fix (committed):** `check_cdg` now records the audio duration; `check()` threads it so
  the CDG capture seeks mid-file (like video). Re-verified: all three now render in VLC
  (`vlc=True`), with real lyric frames captured.

**Real limitation (not a bug) — mpv cannot render CD+G.** mpv consistently fails on `.cdg`
(`exited 2` / no frame). Relevant to the **mpv-primary** goal: if mpv becomes the primary
player, **CD+G songs must still be routed to VLC**.

## Timing data (for the inline-vs-async / link-time tier decision)

Per-stage timings now recorded in every result (`res.timings`). Representative:
- `integrity` (ffprobe): **~0.15s** — trivially cheap.
- `decode` (full ffmpeg decode): **2–21s** — the expensive, highly variable stage.
- `render_vlc`: **~3.5–7s**; `render_mpv`: **~3–24s** (mpv slow on 4K).
- `xvfb_start`: ~0.1s.
- CDG total: ~6–9s.

**Implication:** a synchronous link-time gate should run `integrity` + a **short sampled
decode** (`depth="quick"`, ~1–3s) and defer the full decode + render verification to an
async background pass. A full deep check on a 4K MP4 is ~50–100s — not viable inline.

## Open tuning item (for the gate phase)

For CDG, `mpv_playable=False` is expected (mpv can't do CD+G), so `overall_ok` (which
requires all renderers) is False for every CDG. The **link gate keys on the active renderer
(VLC)**, so it would NOT wrongly block CDGs — but the batch report's "overall_ok" bucket is
noisy for CDGs. Decide during gate wiring: treat mpv-on-CDG as "n/a" rather than a fail in
`compute_verdict`, OR keep it and rely on the active-renderer gate. (The VLC-vs-mpv matrix
already surfaces "CDG: vlc-only" correctly for the mpv-primary decision.)

## Bottom line

The checker is accurate (visually confirmed), catches the real failure modes (truncated,
audio-only), and the one false-positive class (CDG black-intro) was found and fixed before
any hard gate went live — exactly the point of validating against the full library first.
Engine + batch are ready; the hard gates (Tasks 12–13) can proceed with the quick/async
split informed by the timing data above.
