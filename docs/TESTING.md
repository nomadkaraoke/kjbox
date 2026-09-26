# Testing

## Test Types

### Unit Tests (`tests/unit/`)
Test pure functions and isolated logic with no external dependencies. Mock filesystem access where needed, use `tmp_path` for real filesystem tests.

### Integration Tests (`tests/integration/`)
Test Flask routes via the test client. These exercise the full request/response cycle but mock VLC and external services.

## Running Tests

```bash
cd kj-controller

# All tests
pytest

# Verbose with test names
pytest -v

# Unit tests only
pytest tests/unit/

# Integration tests only
pytest tests/integration/

# With coverage
pytest --cov --cov-report=term-missing

# HTML coverage report
pytest --cov --cov-report=html
# Open htmlcov/index.html
```

## Conventions

### File Naming
- Test files: `test_<module>.py`
- Test functions: `test_<function>_<scenario>()`
- Example: `test_sanitize_filename_part_removes_unsafe_chars()`

### Fixtures
Shared fixtures live in `tests/conftest.py`:
- `tmp_media_dir` - temp directory with `downloads/` and `media/` subdirs
- `mock_config` - test config dict with temp paths (includes catalog config)
- `catalog_db_path` - temporary path for a test catalog database
- `sample_file_list` - sample karaoke file list for catalog building
- `flask_app` - Flask app via `create_app(config=mock_config)` with VLC disabled
- `flask_test_client` - Flask test client for route testing

### Mocking Strategy
- **VLC (integration tests)**: Disabled via `create_app(config=...)` which sets `VLCManager(enabled=False)`
- **VLC (unit tests)**: Enabled with `VLCManager(config, enabled=True)`, mock `send_command` or `requests` to test logic without a real VLC binary
- **Filesystem**: Use `tmp_path` / `tmp_media_dir` fixtures for real file operations
- **Config**: Pass test config dict to `create_app(config=mock_config)`
- **Platform detection**: Mock `vlc.is_pi()` to return False (auto in tests via enabled=False)

## Coverage Targets

- Overall: 70%+
- Utility functions: 90%+
- Config loading: 80%+
- Media index: 90%+
- Routes: 70%+
- Catalog: 90%+
- ZIP playback: 90%+
- VLC logic (HTTP, state machine, orchestration): 70%+
- YouTube health: 90%+
- VLC subprocess launching: excluded (requires real `cvlc` binary)

## Edge cases from real nights

Found by mining ActionRecorder logs (recipe in [NIGHT-RECORDING.md](NIGHT-RECORDING.md)).
Every row is a real failure a singer or the KJ hit. Unit/integration coverage must hold
the "Test" column, and the future E2E night-replay suite must include the scenario.

| Night | Scenario | What happened | Status | Test |
|---|---|---|---|---|
| 2026-09-24 | Singer taps a **popular song with >50 versions** (Backstreet Boys "I Want It That Way" = 60) → `kj_pick` | 5 × 400 `kj_pick too many versions (60 > 50)`. The singer saw a generic "Couldn't send", and nothing reached the KJ | ✅ v0.114.1: snapshot trimmed to the best 50 by `priority_rank` (only >1000 or non-object entries refused). Change-song trims too | `test_sing_kj_pick.py::test_trims_oversized_snapshot_to_best_ranked`, `::test_accepts_popular_song_over_trim_cap`, `::test_rejects_non_object_version_entries`, `test_sing_public_routes.py::TestChangeSong::test_change_to_popular_kj_pick_is_trimmed_not_refused` |
| 2026-09-24 | Singer **retries a rejected submit** | Each 400 used up a device rate-limit slot, so the 6th try got 429 "You've submitted a lot" | ✅ v0.114.1: a submit 400 refunds that request's exact device slot. The per-IP slot is kept as the flood backstop | `test_sing_ux_i18n.py::test_rejected_submits_do_not_burn_the_device_budget`, `::test_refund_removes_only_this_requests_slot` |
| 2026-09-24 | Singer taps **photo consent** repeatedly (11 × in 10 s) | 6 × 429. `savePhotoConsent` sends no `device_id`, so it only has the **venue-wide per-IP** budget. A busy bar can exhaust it for everyone | ✅ v0.115.0: sends `device_id` (own device budget), buttons disabled while a save is in flight, re-tapping the current answer sends nothing. A static test fails on any new singer POST without `device_id` | `test_sing_ux_i18n.py::TestSingerRateLimit::test_photo_consent_taps_spend_the_device_budget_not_the_venues`, `e2e/test_footer_social_photo_consent.py::TestMySongsConsent::test_rapid_taps_send_at_most_one_save`, `unit/test_sing_js_device_id.py` |
| 2026-09-24 | Singer **without a phone** enables push notifications | 6 × 400 `missing fields` over 27 min. `/sing/push/subscribe` requires `phone`, but phone is optional at signup, so the singer silently never gets pushes | ✅ v0.115.0: subs keyed by phone **or** `device_id` (new nullable column, phone stored as `''` = absent). Ladder and decision pushes match phone-less subs via `sing_requests.device_id`. The client checks `resp.ok` and shows "couldn't be turned on" instead of claiming push is on | `test_sing_push_routes.py::TestSubscribe::test_phoneless_singer_subscribes_by_device`, `::test_adding_a_phone_later_updates_the_same_row`, `test_rotation_push_hook.py::test_phoneless_singer_gets_ladder_push_by_device`, `test_push_dispatcher.py::TestDispatcher::test_phoneless_sub_never_matches_other_phoneless_entries`, `e2e/test_footer_social_photo_consent.py::TestPushSubscribeHonesty` |
| 2026-09-24 | **4TB SSD drops out** ("Medium not present") while the KJ plays a song | `/play` returned "ZIP file does not contain a playable .mp3" and "Invalid or inaccessible file path" for files that are fine. The KJ tried 4 versions | ✅ v0.115.0: when `/play` fails on a path under `external_media_mount`, it checks the drive (monitor alert, else a fresh bounded probe) and returns **503 `library_drive_offline`**. The KJ UI shows a "replug the SSD" toast. A genuinely bad/missing file on a healthy drive is still 400. (v0.114.0 / PR #236 added the banner; [session record](sessions/2026-Q3/2026-09-24-ssd-disconnect-banner.md)) | `integration/test_play_library_drive_offline.py`, `e2e/test_play_drive_offline_e2e.py` |
| 2026-09-24 | Same photo-consent burst: the **first** 5 taps succeeded, the 6th got 429 | The per-IP backstop was really **5 per 5 min**, not 60: `config.py` still defaulted `sing_rate_limit_per_ip` to 5 from the old IP-only limiter, overriding `sing._IP_RATE_DEFAULT`. On venue wifi (one IP), the 6th singer action from *any* phone in 5 min would 429 | ✅ v0.115.0: config defaults are 60 per IP and 8 per device, pinned to the limiter's constants by a test | `test_sing_ux_i18n.py::TestSingerRateLimit::test_shipped_defaults_keep_the_per_ip_budget_venue_sized` |
| 2026-09-24 | Internet **scanner probes** hit the public singer host (`/sing/.env`, `/sing/wp-login.php`, …) | ~300 × 404, harmless, but the ActionRecorder logged them as `actor: kj`, polluting KJ fixtures | ✅ v0.115.0: an unmatched `/sing/...` path is `actor: anonymous` (kept for security visibility) | `integration/test_action_recorder.py::test_scanner_probe_on_unknown_path_is_not_labelled_kj`, `::test_scanner_probe_on_public_host_root_is_anonymous` |

### Rules these imply for new tests and E2E fixtures

- **Use realistic payload sizes.** Fixtures with 2 versions hid the 50-cap for months.
  Include a popular song with **60+ versions** (local copies + KN + mirror-only) in
  search/submit fixtures.
- **Model retries.** Real singers re-tap when they see an error. Any E2E scenario that
  expects a 4xx should replay it 5–10 times and assert that the phone is *not* then
  locked out (429), and that a valid follow-up still succeeds.
- **Model the shared venue IP.** Every phone at the bar shares one public IP (through
  cloudflared). Tests for any endpoint must check which budget it spends: device
  (`body.device_id`) or venue IP.
- **Every mutation must send `device_id`.** Add a test when adding a singer endpoint.
- **Optional fields must really be optional downstream.** If signup allows no phone,
  every later feature (push, SMS, rename) must work or degrade clearly without one.
- **Infra failures must not masquerade as data errors.** Tests around `/play` and
  downloads should simulate a missing mount (`ENOENT`/`EIO`) and assert an
  "offline" error, not "bad file".

## VLC Testing Strategy

VLC code is testable at three levels:

| Level | What to mock | What you're testing |
|-------|-------------|---------------------|
| **HTTP layer** | `vlc.requests.Session` | URL construction, encoding, error handling in `send_command` |
| **Orchestration** | `vm.send_command` (patch on instance) | State transitions in `play_video`, `monitor_karaoke`, `ensure_filler_stopped`, `fade_music` |
| **Process mgmt** | Mock process objects in `vm.processes` | Terminate/wait/kill logic in `restart_instances` |

Only `launch_instance` truly requires a VLC binary (it calls `subprocess.Popen` with `cvlc`). Everything else is testable through mocking.

## Push notifications (manual runbook)

Web Push is partially untestable in CI — service worker lifecycle and
real push delivery require a browser. Run this checklist on at least
one Android device and one iPhone before shipping push changes to prod.

### Setup

1. HTTPS is required for Web Push on mobile. Options:
   - Deploy to staging (if a staging hostname exists).
   - Run `cloudflared tunnel` locally forwarding to your dev server.
   - Use `ngrok http 80` to expose the dev server.
2. Enable the event token in the KJ UI (Requests panel) and toggle auto-approve OFF so you can observe the approve push.

### Android Chrome

- [ ] Open `/sing/?t=<token>` — submit a song.
- [ ] On confirmation, the 🔔 "Notify me when I'm up" button appears after ~2s. Tap → grant permission.
- [ ] Button swaps to "✓ Notifications on".
- [ ] `sqlite3 rotation.db "SELECT phone, endpoint FROM sing_push_subscriptions"` shows the row.
- [ ] In KJ UI, approve the request → phone receives a "You're in!" push.
- [ ] Move the entry to position 3 → phone receives a "You're up in 2" push.
- [ ] Move the entry to position 2 → "You're up NEXT" push.
- [ ] Mark Now Singing → "You're singing now" push.
- [ ] Tap the notification → page reopens/focuses at the singer's status.

### iPhone Safari

- [ ] Open `/sing/?t=<token>` — submit a song → on confirmation see the iOS install card (📱 iPhone? Get tapped when you're up.).
- [ ] Tap Share → Add to Home Screen → reopen from home screen.
- [ ] Submit another song → confirmation now shows the standard 🔔 button.
- [ ] Accept notifications → KJ UI moves entry → push arrives on phone.

### Desktop Chrome

- [ ] Open the sing URL in desktop Chrome — install as a desktop PWA (three-dot menu → Install).
- [ ] Submit a song, grant push permission.
- [ ] Push arrives while the PWA window is backgrounded or the browser closed (on macOS, confirm via Notification Center).

### Offline banner

- [ ] Submit a song while online — no banner visible.
- [ ] Toggle wifi off (or Chrome DevTools → Network → Offline) → banner appears at top within ~30s.
- [ ] Toggle back on → banner clears within one poll cycle.

### Dedup behaviour

- [ ] Two phones subscribed as the same first name ("Alex" + "Alex") → only the correct phone's entry triggers that device's push.
- [ ] Single phone with two songs in the queue → first song Done'd → ladder resets cleanly for the second entry (you'll see a fresh up_next when the second entry reaches position ≤2).

### Rejection

- [ ] Subscribe as a singer, submit a request, KJ rejects with a reason → phone receives a "The KJ needs a word" push.

### Rules footer

- [ ] On every singer screen (code entry, landing, identity, search, confirm, done), the "🎤 House rules" footer is visible with 5 short bullets. Expanding "Read the full rules" shows the full numbered copy.
- [ ] No separate `/sing/rules` page or external link — rules are inline only.

### Event-code entry

- [ ] Visit `https://sing.nomadkaraoke.com` (no `?t=...`). Code-entry form loads with a 4-digit numeric input; mobile keyboard shows digits.
- [ ] Type the wrong 4 digits → "That code didn't match" error; input clears on next keystroke.
- [ ] Type the correct 4 digits → auto-submits on the 4th digit and lands on the singer UI.
- [ ] Hit `POST /sing/validate` with bad codes 11 times from the same IP → 11th returns 429.
- [ ] Visit `https://sing.nomadkaraoke.com/?t=WRONG` → code-entry form renders with an inline "didn't match" error pre-populated.

### What's playing now widget

- [ ] Landing page with no rotation entries → empty-state "Rotation hasn't started yet — you could be the first!".
- [ ] KJ adds an entry and marks it Now Singing → within 15s the widget shows "🎤 Now: {singer} — {song}".
- [ ] Second entry added → within 15s widget updates to include "Up next: {name}".

### Song selection — empty-state triage for punks (Phase C)

- [ ] Search a nonsense query that returns zero hits. The empty-state triage renders: three stacked cards (or two if `accept_make_requests` is off).
- [ ] Card 1 "Paste a YouTube link" has a URL input and "Use this YouTube link →" button. Submit a valid YouTube URL → routes to confirm page with `YouTube: {url}` label.
- [ ] Card 2 "Ask the KJ to make it tonight" shows artist + title inputs and an "Ask the KJ →" button. Tap → confirm dialog warns about time (20 min to 1 hour) and possible decline. OK → submits as `source_type=make`, KJ sees it in the pending queue.
- [ ] Card 3 "Make it yourself" has a "How it works (takes ~5 min if you focus)" collapsible. Expand → six-step recipe appears inline. "Open gen.nomadkaraoke.com →" is a new-tab link.
- [ ] KJ opens Requests settings modal → new "Accept 'make it' requests tonight" checkbox appears (default checked). Uncheck → API confirms update.
- [ ] After flag is off, the singer refreshes search → card 2 is hidden; cards 1 and 3 remain.
- [ ] With flag off, stale client POSTs `source_type=make` → `/sing/submit` returns 400 `make_requests_disabled`. Row does not land in pending queue.
- [ ] Flip the flag back on → card 2 reappears on next search; submit works again.
- [ ] Paste a gen.nomadkaraoke.com-published YouTube URL into card 1 → KJ approves → rotation plays it correctly end-to-end (one-time full integration check).
- [ ] With non-empty search results, no triage shows; the old bottom `<details>` fallbacks are gone (retired in Phase C).

### Song selection — per-version expander (Phase B)

- [ ] Search a multi-version song. Tap "N versions available →" → card expands inline, Primary "Auto-select best version" CTA remains at top, toggle flips to "Hide versions ↑".
- [ ] First expand (cleared localStorage) shows the Commercial vs Community callout with two bullets + "Got it" button.
- [ ] Tap "Got it" → callout disappears; reload → still dismissed. Expand another song → no callout.
- [ ] A local version card shows brand/format (e.g. "EEK-00391 — CDG+MP3"), filename, and a collapsed "show full path ▸" chevron. Tapping reveals the monospace full path with word-break wrapping.
- [ ] A KN+divebar version card shows brand, divebar format + quality, "via Divebar · NN MB", collapsed drive_path.
- [ ] A KN online-only (commercial) card shows brand + "Commercial · YouTube (download required)". No path block.
- [ ] A KN community card shows brand + "Community · YouTube (download required)". No path block.
- [ ] Sections that have zero candidates are not rendered at all (no empty "Online only" header).
- [ ] Tap "Pick this version →" on a local version → confirmation shows `{title} — {artist} ({filename})`. Submit → admin sees a normal `source_type=local` pending row with a green Approve button (no kj_pick picker).
- [ ] Tap "Pick this version →" on a KN+divebar → confirmation shows brand name, submit → admin sees `source_type=divebar`.
- [ ] Tap "Pick this version →" on a KN-only → submit → admin sees `source_type=kn` / `youtube`.
- [ ] After expanding, the primary "Auto-select best version" button still works (submits `kj_pick` as Phase A).
- [ ] Re-tap the toggle → card collapses back. Expand again → no CC explainer (it stays dismissed).

### Song selection — grouped search + KJ picks version (Phase A)

- [ ] Search for a song with multiple versions (e.g. "bohemian rhapsody") → one card per unique `(artist, title)`, not one per version. Card shows "Auto-select best version →" CTA and an inert "N versions available →" hint below it.
- [ ] Search for a song with exactly one version (e.g. an obscure title with only a single KN track) → card shows "Add to queue" CTA (no KJ-picks framing, no "N versions" hint).
- [ ] Tap "Auto-select best version" → confirm page shows "{title} — {artist} (best version auto-selected)".
- [ ] With auto-approve **off**: submit → admin sees a pending row with an amber `kj_pick` badge and inline picker (the "auto-selected" wording describes what the default ⭐ BEST pick will be — the KJ can still override).
- [ ] With auto-approve **on**: submit → request auto-binds to the top-ranked version and lands in the rotation approved; no pending row.
- [ ] Admin picker shows candidates in `rank_version` order (community brands by priority — local/Divebar tie-break first — then commercial by priority, then unknowns; a top community Divebar track legitimately outranks a lesser local file). Each row has its own "Approve with this →" button.
- [ ] Tap "Approve with this" on a local version → no download queued, rotation entry file_path set, request row's `source_type` = `local`.
- [ ] Tap "Approve with this" on a KN+divebar version → download queued with source=divebar. Request row's `source_type` = `divebar`.
- [ ] Tap "Approve with this" on a KN YouTube-only version → download queued with source=youtube. Request row's `source_type` = `youtube`.
- [ ] Reject a `kj_pick` request (no version picked) → request row's `source_type` stays `kj_pick` (unbound), status = `rejected`.
- [ ] Admin sends `POST /rotation/requests/<id>/approve` with no `version_index` on a `kj_pick` → 400 with "version_index required". Row stays pending.

### Simple KJ Mode — stand-in operator UI

Persistent server flag `kj_simple_mode` (in `sing_meta`); UI is CSS-driven via
`body.simple-mode` toggled by the 2s `/status` poll. Defaults to OFF.

**KJ controller side:**

- [ ] System → Mode subsection is visible at the top of the System container in advanced mode (default). Hint text reads "Hides search panels, manual add, and advanced controls. Singers can only request from the local library, Divebar, or Karaoke Nerds."
- [ ] Flip the Simple Mode switch ON → within 2s, body gains `.simple-mode` class. Right column (KN/YT/Divebar search, Upload/Download, Available Songs, Browser Mode) disappears. Overlays panel disappears. Single column centers and caps at ~720px.
- [ ] Rotation header buttons trim: Refresh + Requests + pending-count badge stay; + Add, New Rotation, Restore, Paths, Undo/Redo are hidden.
- [ ] Manual rotation add form (`#rotation-add-form`) cannot be opened — even if visible briefly in DOM via toggle, CSS hides it.
- [ ] System section collapses to just the Mode subsection. Media & Output, Maintenance, Sleep Mode, Power (Restart App / Reboot / Shutdown), Stats are all hidden.
- [ ] Now-playing bar stays visible; pitch buttons (`#np-pitch-group`) inside it are hidden but Pause / Fade Out / Stop remain.
- [ ] Screen Preview (VNC) panel stays visible.
- [ ] A guidance banner appears above the rotation list reading "Simple Mode is ON · Approve incoming requests → tap a row to play → mark done → announce next singer."
- [ ] Flip Simple Mode OFF → everything restores within 2s. Banner disappears.
- [ ] Open the same KJ UI in a second tab. Flip the toggle in tab A → tab B converges within 2s (no manual refresh).

**Singer SPA side:**

- [ ] With Simple Mode ON, reload the singer SPA. `#sing-root` carries `data-simple-mode="1"`.
- [ ] Search for something that returns zero hits → empty-state shows a single header "We don't have that one." + paragraph "Try another search, or talk to the KJ at the front." No paste-YouTube card, no ask-KJ card, no DIY card.
- [ ] Search a multi-version song (e.g. "bohemian rhapsody") → the "Auto-select best version →" CTA is absent. Versions list renders inline (no toggle button). Singer must tap a specific version's "Pick this version →" button.
- [ ] Search a single-version song → "Add to queue" CTA still appears (singer can still pick songs with one version).
- [ ] Singer's confirm screen subtitle reads "If we don't have it, just ask the KJ at the front." (advanced mode shows "you'll get options for how to get it on screen.").
- [ ] Pick a `local` / `divebar` / `kn` version and submit → request lands in pending queue. KJ approves → rotation plays normally.

**Server-side enforcement (defence-in-depth):**

- [ ] With Simple Mode ON, simulate a stale singer client by curl:
  ```bash
  curl -s -X POST "https://sing.nomadkaraoke.com/sing/submit?t=<token>" \
    -H "Content-Type: application/json" \
    -d '{"singer_name":"Test","phone":"+1 555 0100","song_artist":"X","song_title":"Y","source_type":"youtube","source_ref":"https://youtu.be/test"}'
  ```
  Expect HTTP 400 with `{"error": "simple_mode_disabled_source"}`.
- [ ] Same with `source_type=make` → 400 `simple_mode_disabled_source`. (When Simple Mode is on, simple_mode wins over the existing `make_requests_disabled` check.)
- [ ] Same with `source_type=kj_pick` → 400 `simple_mode_disabled_source`.
- [ ] Same with `source_type=local|divebar|kn` (and a real ref) → 200.

**Stale-PWA recovery (no automated test):**

- [ ] Load `/sing/?t=<token>` with Simple Mode OFF. Type a query, find a multi-version song, tap "Auto-select best version →" — but don't submit yet.
- [ ] In another browser, KJ flips Simple Mode ON.
- [ ] Back in the singer browser, complete the confirm screen and submit → server returns 400. Error message in the UI reads "Song requests are currently restricted. Please refresh this page for the updated options." (not the generic "ask the KJ if requests are paused").

**Toggle isolation:**

- [ ] Set `auto_approve=true` and `accept_make_requests=false` via the Requests modal in advanced mode. Then flip Simple Mode ON, then OFF. Confirm via `GET /rotation/requests/config` that `auto_approve` is still true and `accept_make_requests` is still false — only `simple_mode` changed.

**Pre-existing pending requests when flag flips:**

- [ ] In advanced mode, submit a YouTube request from the singer side (stays pending). Now flip Simple Mode ON. The pending YouTube request remains visible in the Pending Requests panel and is still approvable (server only enforces allowlist on NEW submissions, not retroactively).
