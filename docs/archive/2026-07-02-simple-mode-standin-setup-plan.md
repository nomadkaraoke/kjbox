# Simple Mode — Stand-in Setup + Now-Playing in Playback Controls

**Date:** 2026-07-02
**Branch:** `feat/sess-20260702-simple-mode-standin-setup` (off main @ 9f4cb57)
**Status:** Implemented (v0.52.0). Verified via local mock harness in Playwright — advanced/idle, playing (filename/type/engine), simple-mode switch (layout + preview-hide + manual-add + setup macro: config POST `{enabled:true, accept_make_requests:false, simple_mode:true}` + overlay PUTs for the 3 named overlays), no-revert on Advanced, and 760px mobile. **Decision confirmed: no auto-revert** on switching back to Advanced.

Follow-up to the full-width layout (PR #132). Five changes, all **frontend-only** (HTML/CSS/JS) — no backend logic change; the config + overlay endpoints already support everything.

## 1. Screen Preview — hide connection controls in Simple Mode  *(CSS)*

The VNC connection UI (Connected/Disconnected status, password + Connect, Disconnect, Forget password, Interactive toggle) is jargon a stand-in doesn't need. Add to the `body.simple-mode` `display:none` list: `#vnc-status`, `#vnc-password-form`, `#vnc-controls`, `#vnc-interactive-controls`. Keep the thumbnail (`#vnc-screen`) and the size buttons. The device has a saved VNC password and auto-connects, so the preview still shows.

## 2. Allow manual rotation add in Simple Mode  *(CSS)*

Some older singers won't scan the QR and walk up to the booth. Remove `.rotation-add-btn` and `#rotation-add-form` from the `body.simple-mode` hide list so "+ Add" and the add form work in simple mode. Everything else stays hidden (New Rotation, Undo/Redo, Paths, Restore).

## 3. Remove the fixed "Now Playing" bar → show details in Playback Controls  *(HTML + JS + CSS, both modes)*

Delete the fixed top `#now-playing-bar`. Add a now-playing info block **inside** `.playback-controls` (under the `<h2>`), showing everything about the current track:
- **State** pill — Playing / Paused
- **Song name** — `current_playing` (rotation/display name)
- **Filename** — `basename(current_playing_path)`
- **File type** — derived from extension: `mp4`, `cdg+mp3 (zip)` for `.zip`, `cdg`, `mkv`, etc.
- **Engine** badge — `renderer.mode` → `mpv` / `VLC` (reuse existing `#np-renderer-badge`)
- **Time** — `0:49 / 3:55`
- **Pitch** controls — moved from the bar (keep `#np-pitch-group` id so it stays hidden in simple mode and `changePitch`/`updatePitchDisplay` keep working)
- When nothing is playing: a muted "Nothing playing" line.

The existing `#btn-pause/-restart/-fadeout/-stop` in Playback Controls remain the controls (the bar's duplicate `np-pause/np-fadeout/np-stop` buttons are removed). `updateNowPlaying()` is rewritten to populate the in-section elements; `fadeOut()` drops the `np-fadeout` reference.

## 4. Applying stand-in setup when Simple Mode is switched ON  *(JS)*

When the KJ switches **to Simple**, the toggle handler (fires once, on the action — not on every poll) runs a setup macro:
1. `POST /rotation/requests/config { simple_mode:true, enabled:true, accept_make_requests:false }` (one call — "Requests enabled" on, "make it" requests off).
2. `GET /overlays`; for each overlay whose name is **Scan to Sing**, **Rotation ticker**, or **Rotation list** and not already enabled → `PUT /overlays/<id> { enabled:true }` (idempotent; same path the UI uses). Missing overlays are skipped silently.
3. Refresh the overlays UI + requests-modal state.

**Decision to confirm — revert on switching back to Advanced?** Default: **no auto-revert.** Switching to Advanced leaves requests/overlays as-is; the advanced KJ manages them manually. (Alternative: restore the pre-simple values on switch to Advanced — more complex, needs snapshotting.)

## 5. Mode toggle → Simple / Advanced segmented control  *(HTML + CSS + JS)*

Replace the `#kj-mode-section` content (label + slider + description paragraph) with a two-segment pill toggle **[ Simple | Advanced ]**, the active segment clearly highlighted (brand-styled), no description text. Keeps a backing input for state; `applySimpleMode()` updates which segment is active from `/status`. `toggleSimpleMode()` becomes the "switch to simple/advanced" handler that also runs the setup macro (#4) on switch-to-simple.

## Non-goals (still deferred)

Per-row button trim (…/✎/SMS), hiding singer Merge/Split. Not in this batch.

## Verify + deploy

Prototype live via SSH tunnel (inject candidate CSS/JS) before writing, then screenshot both modes at 1512/1120/760. Frontend-only → deploy = `git pull` on device + service restart for the `?v=` cache-bust (safe off-show). Bump `0.51.2 → 0.52.0` (new features).
