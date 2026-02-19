# KJ Controller Admin UI - UX Review

**Date:** 2026-02-19
**Method:** Live testing via Playwright against running NomadPC instance at https://nomadpc.local, plus full code review of index.html, app.js, style.css, and routes.py.
**Viewports tested:** Desktop (1400x900), Tablet (768x1024), Mobile (375x812)

---

## Findings & Recommendations

### 1. No "Now Playing" Banner / Persistent Playback Awareness

**Problem:** When a song is playing, the only indication is buried in the status bar at the bottom of the left column (often below the fold). The song title in the status bar gets truncated and wraps awkwardly. You have to scroll down past Screen Preview and System to see what's playing and the time.

**Recommendation:** Add a persistent "now playing" bar at the top of the page (or pinned to the top of the left column) that shows the current song, elapsed/total time, and quick pause/stop controls. This is the most critical piece of information during a live show and it should never be out of view.

### 2. Volume Sliders Have No Numeric Value Display

**Problem:** The Karaoke Volume and Filler Volume sliders show no current value. The range is 0-256 (VLC's native scale) which is meaningless to users. You can't tell what 78% vs 80% looks like, and there's no way to set a precise value.

**Recommendation:** Show the current value next to each slider (like the overlay form already does for Speed and BG Opacity). Consider normalizing to a percentage (0-100%) for user-friendliness.

### 3. Playback Buttons Lack Visual State

**Problem:** Pause/Resume, Restart, and Stop all look identical regardless of state. When paused, the "Pause / Resume" button doesn't indicate that pressing it will resume. When nothing is playing, all three buttons are still enabled and clickable (Stop/Restart do nothing useful).

**Recommendation:**
- Change the Pause/Resume button label dynamically ("Pause" when playing, "Resume" when paused)
- Disable Restart and Stop when nothing is playing
- Consider adding a visual play/pause icon for faster recognition

### 4. Dangerous Actions Too Easy to Hit (Mobile Especially)

**Problem:** On mobile at 768px, the System buttons stretch full-width and "Reboot" and "Shut Down" are prominent large touch targets right next to "Rebuild Catalog" and "Restart App." The only guard is a `confirm()` dialog which is easy to tap through on mobile. During a live show with music playing, accidentally tapping Shut Down would be catastrophic.

**Recommendation:** Move destructive system actions (Reboot, Shut Down) behind an expandable section or require a two-step confirmation (e.g., hold to confirm, or a dedicated "are you really sure" modal instead of the browser's confirm dialog).

### 5. Song List - No Indication of What Will Happen When Clicking Catalog Results

**Problem:** Clicking a local library song plays it immediately (no confirmation). Clicking a catalog result (from the external USB drive) also plays it immediately. But the catalog items look different (they show file paths like `/media/nomad/...`) and a user might not realize clicking will attempt playback from the USB drive. There's no visual affordance distinguishing "click to play" from informational display.

**Recommendation:** Add a play icon/button to each row, or at minimum add hover text. For catalog results that require external media to be mounted, consider showing the play action more explicitly since failure is possible.

### 6. Delete Button Placement and Accidental Deletion Risk

**Problem:** The red "Delete" buttons appear inline next to song titles. On the playing-state screenshot, the Delete button is right next to the currently-playing song highlighted in yellow. One mis-tap and you delete the song you're currently performing. The only guard is a `confirm()` dialog.

**Recommendation:** Consider hiding Delete behind a swipe gesture or a "..." overflow menu. At minimum, prevent deletion of the currently-playing song.

### 7. Status Bar Information Overload / Poor Hierarchy

**Problem:** The status bar crams Status, Playing, Filler, and Time all on one line separated by pipes. The playing song title often wraps and becomes hard to read. On narrow screens it's nearly illegible (seen in the tablet screenshot where it wraps to 3 lines).

**Recommendation:** Break the status bar into structured rows or a small grid. Song title should truncate with ellipsis. Consider separating "now playing" info from system status. (Largely addressed by issue #1 - if now-playing moves to a top banner, the status bar can be simplified.)

### 8. Search UX - No Keyboard Shortcut, No Empty State Guidance

**Problem:** There's no keyboard shortcut to jump to search (e.g., `/` or `Ctrl+K`). When search returns no results (as tested with "bohemiana"), the empty state just says "No results" with a "Clear search" link - no suggestions for typo correction or alternative queries.

**Recommendation:** Add a keyboard shortcut for search focus. Consider fuzzy matching or "did you mean" suggestions for near-misses.

### 9. Download Song - No Progress Indicator

**Problem:** When downloading, the only feedback is a "Downloading... please wait." text. There's no progress bar, no estimated time, no indication of which stage (fetching, converting, etc.). Downloads can take 30+ seconds for longer videos.

**Recommendation:** Show a progress indicator or at least stages ("Downloading video...", "Converting..."). Consider showing the video title once resolved from YouTube.

### 10. Overlay Form Opens Inline and Pushes Content Down

**Problem:** When you click "+ Add" overlay, the form expands inline and pushes the Screen Preview and System sections way down the page. The form is long (Name, Type, Text, Speed, Position, Font Size, Colors, Opacity, checkboxes, actions). You lose sight of the rest of the controls.

**Recommendation:** Consider opening the overlay form as a modal/drawer instead of inline. This would keep the rest of the page accessible. Alternatively, collapse other sections when the form is open.

### 11. Two-Column Layout - Left Column Much Longer Than Right

**Problem:** The left column has 4 sections (Playback, Overlays, Screen Preview, System) while the right has 2 (Download, Available Songs). The left column extends far below the right, creating a lot of wasted space. The song list (the thing you interact with most during a show) is constrained to `max-height: 60vh` while the left column has no height constraint.

**Recommendation:** Consider reordering sections so the most-used controls are at the top of both columns. The song search/library should arguably be the most prominent element during a show.

### 12. Filler Music / Audio Output Dropdowns - No Feedback on Change

**Problem:** When you change the Filler Music dropdown, it fires immediately with no visual confirmation that it changed successfully. Same for Audio Output (which triggers a VLC restart). The only feedback is a log entry at the bottom of the System section.

**Recommendation:** Show a brief toast/notification or flash the dropdown border green on success. For Audio Output especially, warn that "VLC will restart" before executing the change.

### 13. VNC Screen Preview - Password UX

**Problem:** The VNC password field is always visible even after connecting. The "Forget Password" button is a rare action given equal weight to "Disconnect." The password is stored in localStorage in plain text.

**Recommendation:** Minor, but the password form could auto-hide more cleanly after connection, and "Forget Password" could be tucked into a less prominent spot.

### 14. Mobile Layout - Wrong Section Order

**Problem:** On mobile (single column), the order is: Playback Controls, Overlays, Screen Preview, System, Download Song, Available Songs. The song library ends up at the very bottom - below System controls and VNC preview. During a live show on a phone, you'd have to scroll past everything to find and play the next song.

**Recommendation:** On mobile, reorder to put the song library higher (right after Playback Controls), since that's the primary interaction during a show.

---

## Priority Summary

| Priority | Issues | Impact |
|----------|--------|--------|
| **Critical** | #1 Now Playing bar, #3 Button states, #14 Mobile order | Core show workflow |
| **High** | #2 Volume values, #4 Dangerous actions, #6 Delete safety, #7 Status bar | Safety + usability |
| **Medium** | #5 Play affordance, #8 Search UX, #11 Layout balance, #12 Feedback | Polish |
| **Low** | #9 Download progress, #10 Overlay form, #13 VNC password | Nice to have |
