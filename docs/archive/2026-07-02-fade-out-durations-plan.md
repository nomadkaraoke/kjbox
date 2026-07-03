# Fade Out: reliable button + selectable durations (both engines)

**Date:** 2026-07-02
**Worktree:** `kjbox-fade-out-durations`
**Status:** design → implementation

## Problem (two parts)

1. **"Fade Out is only *sometimes* clickable."**
   The button is enabled only when `/status.state` is exactly `'playing'`/`'paused'`
   (`app.js updatePlaybackButtons`). But that state is a **flaky per-poll read**:

   - The live device runs the **VLC renderer** (`renderer.mode: 'vlc'`). VLC's HTTP
     `status.json` reports `state:'stopped'` **transiently for ~5s after each play/seek**.
     The end-of-song *monitor* already guards against this (skips the first 5s so it
     doesn't falsely fire `on_karaoke_end`), but `get_status()` — which the button
     reads — passes VLC's raw `'stopped'` straight through with no guard.
   - `VlcKaraokePlayer._send()` has a 5s timeout and returns `None` → `'stopped'` on
     any HTTP blip under load.
   - mpv is steadier (IPC), but not immune: if both `pause` and `time-pos` IPC reads
     return `None` in one poll, it also reports `'stopped'`.

   Any of these greys the button out for that 2s cycle even though a song is loaded.

2. **Only one hardcoded fade length.** `routes.py` calls `vlc.fadeout(duration_s=3.0)`.
   The KJ wants to choose the fade length per use (3s / 10s / 20s / arbitrary).

## Design goals

- Both engines (mpv + VLC) support fade out **equally and reliably** — availability
  and the fade itself.
- Fix at the source where practical; renderer-agnostic where possible.

## Fix 1 — reliable availability (renderer-agnostic)

**Frontend (primary, deployable without a service restart):**
Gate the fade / restart / stop buttons on **"a song is loaded"** using the stable,
renderer-agnostic signal `current_playing_path` (already in `/status`, already tracked
as the `currentPlayingPath` global). `current_playing_path` is `player.current_path` —
set on play, cleared only by a real stop/fadeout or the guarded monitor — so it does
**not** flicker with transient VLC `'stopped'` reads, on either engine.

- `updatePlaybackButtons`: enable fade presets + Restart + Stop when `currentPlayingPath`
  is set (fade also respects the in-progress `_fadingOut` flag). Pause stays always-enabled
  (existing philosophy). This mirrors the existing "Keep enabled so user can always try"
  intent for Pause.

**Backend (source fix, deploys with the durations change, off-show):**
Add a small guard to `VlcKaraokePlayer.get_status()` mirroring the monitor's existing
5s post-play/seek protection, so VLC's *reported state* is as steady as mpv's:

- `_send()` returns `None` while a song is loaded (`active and current_path`) →
  report `'playing'` (last-known) instead of a spurious `'stopped'`.
- Raw `'stopped'` while loaded **and** within 5s of play/seek → report `'playing'`.

This also stops the now-playing pill flickering "Stopped" mid-song. mpv already reports
reliably; no change needed there. (Fresh/idle player → still `'stopped'`; guard only
triggers when `active and current_path`.)

Belt-and-suspenders: the frontend `current_playing_path` gating alone fixes the button;
the backend guard makes the underlying state trustworthy for every consumer.

## Fix 2 — selectable durations (both engines)

**Backend:** `POST /control` `action:'fadeout'` accepts optional `duration_s`.

- Parse `float(duration_s)`, default `3.0`, **clamp to [0.5, 60]s**.
- `vlc.fadeout(duration_s=<clamped>)` — the polymorphic coordinator already forwards
  to `player.fadeout(duration_s)`, honored identically by mpv and VLC.

**Smoothness (both players):** scale fade steps with duration so long fades aren't
steppy — `steps = clamp(round(duration_s * 8), 20, 200)` (3s→24, 10s→80, 20s→160).
VLC: ≤10 HTTP vol calls/s — fine. mpv: IPC, trivial.

**Frontend UI — preset buttons in their own row** (fastest for live; one tap = fade
starts immediately), plus a small custom field for arbitrary values ("etc"):

```
Fade out:  [3s] [6s] [10s] [20s]   [ __ s ] [Fade]
```

- Each preset calls `fadeOut(seconds)`. Custom field (`min 1`, `max 60`) + Fade button
  calls `fadeOut(value)`.
- `fadeOut(seconds)` posts `{action:'fadeout', duration_s: seconds}`, disables all fade
  buttons + shows "Fading…" on the clicked one.
- **Fixes a latent bug the feature would otherwise trigger:** the current reset timer is
  a hardcoded `3500ms`. Replace with `seconds*1000 + 800` so `_fadingOut` doesn't clear
  (and the button doesn't re-enable) mid-fade on a 10s/20s fade. Final disable is driven
  by `current_playing_path` clearing when the coordinator stops the song, so the flag is
  just for immediate visual feedback.

Presets `3 / 6 / 10 / 20` chosen (the three named + one common middle). Easy to change;
could be config-driven later (out of scope now).

## Testing

- **Backend unit (`test_vlc_karaoke_player.py`):** get_status guard — transient `'stopped'`
  within 5s of play while loaded → `'playing'`; `_send None` while loaded → `'playing'`;
  idle/fresh → still `'stopped'` (existing tests stay green); raw `'paused'` passes through.
- **Backend unit (both players):** `fadeout(duration_s)` uses scaled step count / delay.
- **Backend integration (`test_routes.py`):** `/control fadeout` default → `duration_s=3.0`;
  explicit `duration_s:10` → forwarded; garbage/out-of-range → clamped; update the existing
  `test_control_fadeout_delegates_to_coordinator`.
- **Frontend e2e (local mock harness + Playwright):** with `current_playing_path` set but
  `state:'stopped'`, fade/restart/stop stay enabled; preset click posts correct `duration_s`;
  custom field posts its value; "Fading…" shown; buttons disable when no song loaded.

## Deployment

- Frontend gating (Fix 1 frontend) is deployable without a restart.
- Backend (`get_status` guard + `duration_s` + scaled steps) needs a service restart →
  **off-show only**. **Do not deploy during the live event.** Combined PR ships together
  off-show. No push/deploy without explicit approval.
