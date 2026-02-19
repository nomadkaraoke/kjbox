# Plan: Admin UI UX Improvements (14 Issues)

**Created:** 2026-02-19
**Branch:** TBD (needs `/start`)
**Status:** Draft
**Review:** [docs/archive/2026-02-19-admin-ui-ux-review.md](../../docs/archive/2026-02-19-admin-ui-ux-review.md)

## Overview

Implement all 14 UX improvements identified during live testing of the KJ Controller admin UI. These changes are purely frontend (HTML/CSS/JS) with no backend API changes. The goal is to make the admin UI more intuitive and safer to operate during live karaoke shows, particularly on mobile devices.

## User Decisions

- **Now Playing bar:** Sticky top bar, fixed to viewport, visible only during playback
- **Feedback system:** CSS flash animations on elements (no toast system)
- **Overlay form:** Modal/dialog with backdrop
- **Layout changes:** Mobile reorder only, desktop stays as-is

## Implementation Phases

The 14 issues are grouped into 6 phases, ordered by dependency (each phase builds on the prior). All phases modify the same 3 files, so phases are logical groupings rather than separate PRs.

---

## Phase 1: Core Playback Experience (Issues #1, #2, #3, #7)

These are the highest-impact changes and form the foundation for the rest.

### Issue #1 — Sticky Now Playing Bar

**Files:** `index.html`, `style.css`, `app.js`

**HTML** — Add a new element above `.main-layout`:
```html
<div id="now-playing-bar" class="now-playing-bar hidden">
    <div class="now-playing-info">
        <span class="now-playing-state" id="np-state">Playing</span>
        <span class="now-playing-title" id="np-title"></span>
    </div>
    <div class="now-playing-time">
        <span id="np-time">0:00</span> / <span id="np-length">0:00</span>
    </div>
    <div class="now-playing-controls">
        <button class="np-btn" id="np-pause" onclick="controlPlayback('pause_resume')">Pause</button>
        <button class="np-btn np-btn-stop" id="np-stop" onclick="controlPlayback('stop')">Stop</button>
    </div>
</div>
```

**CSS:**
- `position: sticky; top: 0; z-index: 100` — sticks to top when scrolling
- Dark background with subtle brand accent border-bottom
- Flex layout: title (flex:1, ellipsis truncation) | time | buttons
- Transition: slide-down animation when appearing
- `.hidden` hides it when nothing is playing
- Add `body { padding-top: ... }` compensation when bar is visible (use JS to toggle a body class)

**JS** — Update `updateStatus()`:
- When `data.state === 'playing'` or `data.state === 'paused'`: show the bar, set title/time
- When `data.state === 'stopped'` or no data: hide the bar
- Update `#np-state` text: "Playing" / "Paused"
- Update `#np-pause` label: "Pause" when playing, "Resume" when paused
- Update `#np-title` with the display name (from `data.current_playing`)
- Update `#np-time` / `#np-length` with formatted time

### Issue #2 — Volume Slider Numeric Values

**Files:** `index.html`, `app.js`, `style.css`

**HTML** — Add value labels next to each slider:
```html
<label for="karaoke-volume">Karaoke Volume: <span id="karaoke-volume-label" class="range-label">78%</span></label>
```
Same pattern for filler volume.

**JS:**
- Add helper: `function volumePercent(val) { return Math.round(val / 256 * 100) + '%'; }`
- Update labels on `oninput` events
- Update labels from `updateStatus()` if the server reports current volume (currently it doesn't — just set from slider value)
- Initialize labels on page load from slider default values

**CSS:** Use existing `.range-label` class (already styled for overlay sliders).

### Issue #3 — Playback Button States

**Files:** `index.html`, `app.js`, `style.css`

**HTML** — Add IDs to the playback buttons for JS targeting:
```html
<button id="btn-pause" onclick="controlPlayback('pause_resume')">Pause</button>
<button id="btn-restart" onclick="controlPlayback('restart')">Restart</button>
<button id="btn-stop" onclick="controlPlayback('stop')">Stop</button>
```

**JS** — Update `updateStatus()`:
- When `state === 'stopped'`: disable Restart and Stop buttons, set Pause label to "Pause / Resume" (default idle state)
- When `state === 'playing'`: enable all, set Pause label to "Pause"
- When `state === 'paused'`: enable all, set Pause label to "Resume"
- Add `disabled` attribute and CSS class for disabled state

**CSS:**
- `.button-group button:disabled { opacity: 0.4; cursor: not-allowed; }` (mirror existing `.rescan-btn:disabled` pattern)

### Issue #7 — Status Bar Simplification

**Files:** `index.html`, `style.css`

Since Issue #1 moves now-playing info to the sticky bar, simplify the status bar:
- Keep: Status state, Filler track name, Audio device status
- Remove: Playing title and Time (now in the sticky bar)
- Restructure as a clean single-line: `Status: playing | Filler: wii.mp3`
- The full playing info is in the now-playing bar; the status bar becomes a compact system status indicator

**CSS:** Reduce font size slightly, keep the dark card style.

---

## Phase 2: Safety & Protection (Issues #4, #6)

### Issue #4 — Dangerous Action Protection

**Files:** `index.html`, `style.css`, `app.js`

**Approach:** Two-tier system:
1. **Reboot/Shut Down** — Require double-click with visual countdown: first click changes button to "Confirm Reboot? (3s)" with a 3-second countdown, second click within the window executes. After timeout, reverts to normal.
2. **Rebuild Catalog / Restart App** — Keep current `confirm()` dialog (these are non-destructive).

**JS:**
```javascript
function dangerousAction(btn, action, label, extraWarning) {
    if (btn.dataset.armed) {
        // Second click — execute
        clearTimeout(btn._confirmTimer);
        delete btn.dataset.armed;
        btn.textContent = label;
        btn.classList.remove('system-btn-armed');
        systemAction(action, label, null); // skip confirm dialog
        return;
    }
    // First click — arm the button
    btn.dataset.armed = 'true';
    btn.classList.add('system-btn-armed');
    let remaining = 3;
    btn.textContent = `Confirm? (${remaining}s)`;
    btn._confirmTimer = setInterval(() => {
        remaining--;
        if (remaining <= 0) {
            clearInterval(btn._confirmTimer);
            delete btn.dataset.armed;
            btn.textContent = label;
            btn.classList.remove('system-btn-armed');
        } else {
            btn.textContent = `Confirm? (${remaining}s)`;
        }
    }, 1000);
}
```

**CSS:** `.system-btn-armed` — pulsing border animation in the button's danger/warning color.

**HTML:** Wire `onclick` for Reboot/Shut Down to use `dangerousAction(this, ...)`.

### Issue #6 — Delete Button Safety

**Files:** `app.js`

Two improvements:
1. **Prevent deletion of currently-playing song:** In `deleteMedia()`, check if the file path matches the currently-playing path (tracked from status). If so, show an error log message and return early.
2. **Track current playing path in JS:** The status endpoint already returns `current_playing_path`. Store it in a module-level variable during `updateStatus()`.

```javascript
let currentPlayingPath = null;
// In updateStatus():
currentPlayingPath = data.current_playing_path || null;

// In deleteMedia():
if (filePath === currentPlayingPath) {
    log('Cannot delete the currently playing song. Stop it first.', 'error');
    return;
}
```

No CSS changes. The existing `confirm()` dialog stays as additional protection.

---

## Phase 3: Song List UX (Issues #5, #8)

### Issue #5 — Play Affordance

**Files:** `app.js`, `style.css`

**Approach:** Add a play icon (CSS triangle or Unicode) as a hover affordance on song rows.

**CSS:**
```css
#media-list li::before {
    content: '\25B6';  /* ▶ */
    opacity: 0;
    margin-right: 8px;
    color: #ff5bb8;
    font-size: 0.7em;
    transition: opacity 0.15s;
}
#media-list li:hover::before { opacity: 1; }
#media-list li.folder-header::before,
#media-list li.section-header::before { content: none; }
```

This adds a play icon that fades in on hover for all playable rows, without changing any JS.

For catalog items specifically, add a `title` attribute in `renderUnifiedResults()` that says "Click to play from external drive" so users understand what will happen.

### Issue #8 — Search Keyboard Shortcut

**Files:** `app.js`

Add a global keydown handler:
```javascript
document.addEventListener('keydown', (e) => {
    // Focus search on '/' unless already in an input
    if (e.key === '/' && !['INPUT', 'TEXTAREA', 'SELECT'].includes(document.activeElement.tagName)) {
        e.preventDefault();
        document.getElementById('catalog-search').focus();
    }
});
```

Also update the search placeholder to hint at the shortcut: `"Search your library + N catalog songs... (press /)"` — append the hint only on non-touch devices (check `'ontouchstart' in window`).

---

## Phase 4: Feedback & Polish (Issues #9, #12)

### Issue #12 — CSS Flash Feedback

**Files:** `style.css`, `app.js`

**CSS:**
```css
@keyframes flash-success {
    0% { border-color: #22c55e; box-shadow: 0 0 0 2px rgba(34, 197, 94, 0.3); }
    100% { border-color: #2a2a2a; box-shadow: none; }
}
@keyframes flash-error {
    0% { border-color: #ef4444; box-shadow: 0 0 0 2px rgba(239, 68, 68, 0.3); }
    100% { border-color: #2a2a2a; box-shadow: none; }
}
.flash-success { animation: flash-success 1s ease-out; }
.flash-error { animation: flash-error 1s ease-out; }
```

**JS** — Helper function:
```javascript
function flashElement(el, type = 'success') {
    el.classList.remove('flash-success', 'flash-error');
    void el.offsetWidth; // force reflow to restart animation
    el.classList.add(type === 'success' ? 'flash-success' : 'flash-error');
}
```

Apply to:
- **Filler Music dropdown:** flash on successful change in `setFillerMusic()`
- **Audio Output dropdown:** flash on successful change, plus log warning about VLC restart
- **Volume sliders:** flash briefly on change (optional, may be too noisy — skip if distracting)
- **Download success:** flash the URL input green

### Issue #9 — Download Progress Indicator

**Files:** `index.html`, `app.js`, `style.css`

**Approach:** Replace the static "Downloading... please wait." with a staged indicator. The backend doesn't stream progress, so we simulate stages based on elapsed time:

**HTML:**
```html
<div id="download-status" class="hidden">
    <div class="download-progress">
        <span class="download-spinner"></span>
        <span id="download-stage">Downloading...</span>
    </div>
</div>
```

**CSS:** Simple spinner animation (rotating border).

**JS** — In `downloadSong()`:
```javascript
const stages = [
    { time: 0, text: 'Fetching video info...' },
    { time: 3000, text: 'Downloading video...' },
    { time: 15000, text: 'Still downloading (large file)...' },
    { time: 30000, text: 'Almost there...' },
];
let stageTimers = [];
stages.forEach(s => {
    stageTimers.push(setTimeout(() => {
        document.getElementById('download-stage').textContent = s.text;
    }, s.time));
});
// Clear timers when download completes
```

---

## Phase 5: Layout & Responsive (Issues #11, #14)

### Issue #14 — Mobile Section Reorder

**Files:** `index.html`, `style.css`

**Approach:** Use CSS `order` property at the mobile breakpoint to reorder sections. To make this work, switch the mobile layout from two-column grid to a single flex container.

**CSS changes at `@media (max-width: 768px)`:**
```css
@media (max-width: 768px) {
    .main-layout {
        display: flex;
        flex-direction: column;
        gap: 0.5rem;
    }
    /* Reorder for live-show priority */
    #col1 { order: 1; }
    #col2 { order: 2; }

    /* Within col1, move less-used sections down */
    #col1 { display: contents; }
    #col2 { display: contents; }
}
```

The `display: contents` trick unwraps the column divs so all `.container` children participate in a single flex flow. Then assign `order` to individual containers:

```css
@media (max-width: 768px) {
    .main-layout {
        display: flex;
        flex-direction: column;
    }
    #col1, #col2 { display: contents; }

    /* Desired mobile order: */
    .playback-controls { order: 1; }     /* Playback Controls */
    .available-songs   { order: 2; }     /* Song Library + Search */
    .download-section  { order: 3; }     /* Download */
    .overlay-panel     { order: 4; }     /* Overlays */
    #vnc-preview-container { order: 5; } /* Screen Preview */
    .system-controls   { order: 6; }     /* System */
}
```

**HTML** — Add CSS classes to the containers that don't already have them:
- Playback Controls: add class `playback-controls`
- Download Song: add class `download-section`
- The others already have classes (`available-songs`, `overlay-panel`, `system-controls`, `#vnc-preview-container`)

**Note:** The existing e2e test `test_mobile_layout_single_column` already expects `display: flex` at mobile width, so this change is consistent with tests.

### Issue #11 — Column Balance (Minor)

No structural changes (user chose mobile-only). But make one small CSS tweak: increase `#media-list { max-height: 60vh }` to `max-height: 70vh` to give the song list more room since it's the primary interaction surface. On mobile, keep `50vh`.

---

## Phase 6: Modal & VNC (Issues #10, #13)

### Issue #10 — Overlay Form as Modal

**Files:** `index.html`, `style.css`, `app.js`

**HTML** — Move the overlay form out of the overlay-panel container into its own top-level modal:
```html
<div id="overlay-modal" class="modal-backdrop hidden" onclick="if(event.target===this) hideOverlayForm()">
    <div class="modal-content">
        <div class="modal-header">
            <h3 id="overlay-modal-title">Add Overlay</h3>
            <button class="modal-close" onclick="hideOverlayForm()">&times;</button>
        </div>
        <!-- existing overlay form fields (moved here) -->
        <div class="overlay-form">
            ... (all existing form rows)
        </div>
    </div>
</div>
```

**CSS:**
```css
.modal-backdrop {
    position: fixed; inset: 0; z-index: 200;
    background: rgba(0, 0, 0, 0.6);
    display: flex; align-items: center; justify-content: center;
    backdrop-filter: blur(2px);
}
.modal-content {
    background: #1a1a1a; border: 1px solid #2a2a2a;
    border-radius: 12px; padding: 1rem;
    width: 90%; max-width: 480px; max-height: 85vh;
    overflow-y: auto;
}
.modal-header {
    display: flex; justify-content: space-between; align-items: center;
    margin-bottom: 0.75rem;
}
.modal-header h3 { color: #ff7acc; margin: 0; }
.modal-close {
    background: none; border: none; color: #666;
    font-size: 1.5em; cursor: pointer; box-shadow: none;
}
.modal-close:hover { color: #e5e5e5; box-shadow: none; }
```

**JS:**
- `showOverlayForm()` — show the modal (`#overlay-modal.classList.remove('hidden')`)
- `hideOverlayForm()` — hide the modal
- Set title to "Add Overlay" or "Edit Overlay" based on context
- Add Escape key handler to close modal
- Remove the old inline `#overlay-add-btn` hide/show logic (the Add button stays visible since the form is now a modal)

### Issue #13 — VNC Password UX

**Files:** `index.html`, `style.css`

Minor tweaks:
- After connection, the password form already hides and controls show. Just move "Forget Password" to a smaller, less prominent position (e.g., a text link instead of a button).
- Change "Forget Password" button to a text link styled like `.search-meta a`:
```html
<div id="vnc-controls" class="vnc-controls hidden">
    <button class="vnc-btn" onclick="disconnectVnc()">Disconnect</button>
    <a class="vnc-forget-link" onclick="forgetVncPassword()">Forget password</a>
</div>
```

**CSS:**
```css
.vnc-forget-link {
    font-size: 0.7em; color: #666; cursor: pointer; text-decoration: none;
    align-self: center;
}
.vnc-forget-link:hover { color: #ff7acc; text-decoration: underline; }
```

---

## Files to Create/Modify

| File | Action | Description |
|------|--------|-------------|
| `kj-controller/templates/index.html` | Modify | Add now-playing bar, button IDs, modal overlay form, volume labels, download progress, mobile classes, VNC link |
| `kj-controller/static/app.js` | Modify | State-aware buttons, now-playing updates, volume labels, flash feedback, keyboard shortcut, delete protection, dangerous action confirm, download stages, modal show/hide |
| `kj-controller/static/style.css` | Modify | Sticky bar, flash animations, modal styles, mobile reorder, button states, play affordance, spinner, VNC link |
| `kj-controller/tests/e2e/test_frontend.py` | Modify | Update tests for new element IDs, button labels, now-playing bar, modal overlay form. Fix existing `footer-area` test (references a class that doesn't exist). |

## Testing Strategy

**E2e tests (Playwright — already have infrastructure):**
- Test now-playing bar appears/disappears based on mock playback state
- Test button labels change with state (Pause/Resume)
- Test disabled buttons when stopped
- Test overlay modal opens/closes (replace inline form tests)
- Test mobile section order via `getComputedStyle().order`
- Test keyboard shortcut `/` focuses search
- Test delete protection when current_playing_path matches
- Fix existing broken `test_footer_area` test

**Manual testing on live device:**
- SSH to nomadpc, test with real songs playing
- Test on phone browser (mobile layout reorder)
- Test dangerous action countdown (Reboot/Shut Down)
- Verify VNC preview still works with modal overlay nearby

## Implementation Order

1. [ ] Phase 1: Now-playing bar, volume labels, button states, status bar cleanup
2. [ ] Phase 2: Dangerous action protection, delete safety
3. [ ] Phase 3: Play affordance, search keyboard shortcut
4. [ ] Phase 4: CSS flash feedback, download progress
5. [ ] Phase 5: Mobile reorder, column balance
6. [ ] Phase 6: Overlay modal, VNC password UX
7. [ ] Update e2e tests for all changes
8. [ ] Manual test on live NomadPC device

## Rollback Plan

All changes are in 3 static frontend files + 1 test file. Rollback = revert the commit. No database migrations, no config changes, no backend API changes.

## Open Questions

None — all decisions resolved via user input.
