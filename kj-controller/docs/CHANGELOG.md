# KJ Controller — Changelog

Dated entries, newest first. Each entry notes any required deploy steps.

---

## 2026-07-03 - SMS delivery status on rotation rows + up-next nudge (v0.65.0)

**Why:** A live-show SMS outage (the sending number was never linked to the
approved 10DLC campaign → every send hard-rejected with carrier error `40010`)
went unnoticed for ~2 weeks because the rotation row showed an optimistic
"sent 11:39 PM" marker whenever an SMS was *accepted* by Telnyx (HTTP-2xx =
queued). The real outcome arrives later via the Telnyx delivery-receipt (DLR)
webhook, which writes `delivered` / `delivery_failed` to `sms_log.status` — but
the frontend only styled the send-time `failed` string, never the DLR
`delivery_failed`, so carrier rejections rendered as a plain neutral "sent".

**What:**
- `routes.py` (`_add_sms_status`): add `last_error` to each row's `sms` block so
  the failure tooltip can explain *why* (e.g. `40010 Not 10DLC registered`,
  opted-out, carrier reject). Docstring updated for the DLR statuses.
- `static/app.js`: `smsDeliveryState()` collapses the raw status into
  delivered / failed / pending (the failure set now includes the DLR statuses).
  Marker renders ✓ delivered (green) / ✗ failed (red) / ⋯ sent (amber); the SMS
  button becomes a red **Retry** on failure.
- Up-next nudge: when the up-next singer has a phone but no SMS has been sent
  yet, the SMS button pulses (subtle brand-pink glow; respects
  `prefers-reduced-motion`).
- Tests: `/rotation` surfaces `last_status` + `last_error` for delivered and
  `delivery_failed` DLRs.

**Deploy:** frontend + a tiny additive `routes.py` field. The ✓/✗/⋯ colours are
frontend — they appear on the next browser load once `app.js?v=` busts (version
read at startup → new query param after a restart, or a hard refresh). The
`last_error` tooltip text needs the next service **restart** to take effect.
`kj-autodeploy.service` is currently inactive → deploy is a manual `git pull` on
the device; **do the restart between shows, never mid-song.**

---

## 2026-07-03 - Version renumber: 0.64.2 (avoid collision with concurrent #154)

**Why:** PR #155 (the source-id token fix below) and PR #154 (fade-out controls on
one compact line — CSS only, no changelog entry) both merged as `v0.64.1` from
concurrent branches. Renumber HEAD to `0.64.2` so the deployed version is unique and
strictly later. No code change — version string only. The `v0.64.1` label now refers
to the #154 CSS tweak; the token fix ships as `0.64.2`.

---

## 2026-07-03 - Fix: source-id token leaking into rotation song names (v0.64.2, was v0.64.1)

**Why:** During a live show, some rotation entries displayed the raw media-id slug
token in the song name, e.g. `Vienna [yt-I8wu3lLbB0k] - Billy Joel` and
`Sabrina Carpenter - Go Go Juice [yt-q0rHs_xFuSk]`. Root cause: the P1–P4
download-naming rename (v0.54.1) renamed downloaded files to the canonical slug
`Artist - Title [media_id].ext`, but `unified_search()` still derived a search
result's artist/title by calling `parse_karaoke_filename()` on the raw on-disk
filename. That parser splits on ` - ` and never strips the trailing `[media_id]`
token, so the token stuck to the **title**. The polluted title then flowed into
both the singer-facing pick (`_format_song_text` → `Title - Artist`) and the
KJ-side add/link (`Artist - Title`), which is why the same bug appeared in two
orderings. Pre-rename YouTube files (`VIDEOID__…`) never hit this, and SSD-library
results (pre-parsed via external catalog) were unaffected. Note the `media_library`
rows themselves were always clean — this was purely a display-build defect.

**What:**
- `unified_search()` downloaded-media branch now prefers the clean, curated
  `media_library` identity (looked up by path) for a file's artist/title. The scan
  already resolves the real artist/title into `media_library`, so search reuses it.
- When no `media_library` row exists, it falls back to
  `parse_karaoke_filename(naming.strip_media_id_token(filename))` so the token is
  stripped before the deterministic parse. The `display_name` fallback is likewise
  token-stripped.
- Fixes the source that feeds both the singer `/sing/search` and KJ
  `/rotation/search` result sets, so no polluted names reach either consumer.

**Deploy:** Backend change (`routes.py`) — requires a `kj-controller` restart to take
effect. Safe to deploy between singers / after the show. Existing polluted rotation
rows (`song_artist` free-text) are display-only and can be corrected in-app or left to
age out; this fix prevents new ones.

---

## 2026-07-02 - Reliable Fade Out on both engines + selectable durations (v0.64.0)

**Why:** The Fade Out button was "only sometimes clickable." It was enabled only when
`/status.state` was exactly `playing`/`paused`, but that live state flickers to
`stopped`: on the VLC renderer, VLC's HTTP status reports `stopped` transiently for ~5s
after each play/seek (the end-of-song monitor guards this, but `get_status()` — read by
the button — did not), and a status HTTP blip (`_send` timeout → `None`) also reads as
`stopped`. mpv is steadier but not immune. Separately, the fade length was hardcoded to
3s; the KJ wants to choose it per song.

**What:**
- **Availability (renderer-agnostic):** Fade / Restart / Stop now gate on whether a song
  is *loaded* (`current_playing_path`, a stable signal set on play and cleared only by a
  real stop/fadeout or the guarded monitor) instead of the flaky live state.
- **VLC state steadied at the source:** `VlcKaraokePlayer.get_status()` now mirrors the
  monitor's 5s post-play/seek guard and treats an HTTP blip while a song is loaded as
  last-known `playing`, so its reported state matches mpv's reliability (also stops the
  now-playing pill flickering "Stopped").
- **Selectable durations:** preset buttons 3 / 6 / 10 / 20s + a custom 1–60s field.
  `POST /control` accepts an optional `duration_s` (default 3.0, clamped `[0.5, 60]`),
  forwarded through the polymorphic `coordinator.fadeout` to both engines. A shared
  `fade_steps()` (`karaoke_player.py`) scales the volume ramp with duration so long fades
  are smooth on both renderers. The frontend's post-fade reset now scales with the chosen
  length (was a fixed 3.5s) so buttons don't re-enable mid-fade.

**Deploy:** Backend change (`routes.py`, `vlc.py`, `mpv_manager.py`, `karaoke_player.py`)
→ requires `sudo systemctl restart kj-controller` (interrupts active playback). Ship
**off-show**. (The frontend gating half alone is a no-restart change, but this ships as
one unit.)

---

## 2026-06-30 - Pair a loose CDG with its sibling MP3 at download (v0.48.0)

**Why:** v0.46.0 (#124) *blocks* a silent bare `.cdg`, but for brands that store a
CDG's graphics and audio as two separate Drive files (e.g. Sandell Karaoke), the
divebar index exposes them as independent track rows — so clicking the `cdg` row
downloads a graphics-only file that is then rejected, leaving the song unobtainable.
~2,665 such loose CDG+MP3 pairs are mirrored in GCS.

**What:**
- When a divebar track's `format` is `cdg`, resolve its sibling audio via the divebar
  API (`divebar.find_sibling_audio` — re-search the song, match the same brand and the
  same `drive_path` basename with the extension swapped), download **both** the `.cdg`
  and the `.mp3`, and package them into a single `divebar__*.zip`
  (`media.download_cdg_pair`). The zip passes the existing `cdg_zip` gate and plays
  exactly like any other CDG — no `/play` changes needed.
- A shared resolver (`routes._resolve_divebar_spec`) centralises the single-vs-paired
  decision across all three divebar enqueue sites (`/divebar/download`,
  `/rotation/download-and-link`, `approve_sing_request`); the download worker dispatches
  `pair` items to `download_cdg_pair`. If no sibling audio exists in the mirror, the
  request **fails fast** (422 / clear error) and no file is created.

**Deploy:** Backend change → requires `sudo systemctl restart kj-controller` (interrupts
active playback). Autodeploy is OFF — pull + restart manually, off-show.

---

## 2026-06-30 - Block silent bare .cdg + show file type/extension (v0.46.0)

**Why:** A bare `.cdg` (graphics-only, e.g. `divebar__SDK - ABBA - Dancing Queen.cdg`)
is a first-class indexed media type, so it showed in Available Songs and was
link/play-eligible — but playing it on the main screen is **silent** (embarrassing
mid-show). The old gate even allowed it (ffmpeg's `cdgraphics` demuxer presents a
`.cdg` as a video stream, so the verdict's video-only check passed), and `/play`
didn't gate at all. The UI also showed no file type/extension, so a KJ couldn't tell
a `cdg-zip` from a bare `.cdg` from an `.mp4` before clicking.

**What:**
- `classify_kind` now returns `cdg_bare` for a standalone `.cdg`. A bare `.cdg` is
  playable/linkable **only** when a same-stem audio file sits beside it (`X.cdg` +
  `X.mp3` — `playability.sibling_cdg_audio`). The playability verdict folds `cdg_bare`
  into the `cdg_zip` branch, so `/rotation/link` **and** downloads reject an audioless
  `.cdg` automatically. `/play` gains the matching guard (400 + clear message; when a
  sibling exists, mpv plays the `.cdg`+sibling, VLC plays the sibling and auto-finds
  the `.cdg`).
- Available Songs rows now show a `kind · .ext` badge (`cdg-zip · .zip`, `mp4 · .mp4`,
  …) via `utils.media_type_label`; a no-audio `.cdg` gets a red "no audio" tag, is
  dimmed, and its click no longer attempts a silent play.
- The preview modal header shows the file type + extension; a bare `.cdg` with a
  sibling previews as CDG, without one shows "Graphics-only .cdg — no audio track."

**Deploy steps:** Backend change → requires a service restart (interrupts active
playback); deploy off-show: `git pull` + `sudo systemctl restart kj-controller`, then
hard-refresh the KJ browser. No new dependencies.

---

## 2026-06-30 - Playability checker made deterministic (v0.45.0)

**Why:** A manual review of 166 files flagged by the full-library playability sweep
found **~90% were false positives** (88% of flagged video, 91% of flagged CD+G). Whole
commercial discs were flagged 100% yet played fine. The checker's verdict leaned on
signals that are environment-fragile rather than deterministic, so it could not be
trusted as a delete list — and because the CD+G verdict also feeds the live
link/upload/download gates, the box was false-rejecting playable commercial CD+G discs.

**Root causes (all fixed):**
- **Render frame-capture was a hard gate.** Headless-Xvfb pixel-proof is too
  environment-sensitive (51 video false positives). Now a **diagnostic only** — it never
  gates `overall_ok`; per-renderer "playable" flags are still recorded for the VLC-vs-mpv
  matrix.
- **`ffmpeg -xerror`** aborted on the *recoverable* cdgraphics `tile is out of range`
  warning, failing valid discs (~57 CD+G false positives). Removed; decode `ok` now means
  ffmpeg ran to completion (rc == 0). Recoverable warnings are recorded, never gate.
- **180 s decode timeout** was too short under the `nice -n19 ionice -c3` throttle for long
  files (15 false positives). Deep-decode timeout now scales with duration.
- **CD+G audio detection was `.mp3`-only**, missing `.m4a`/`.wav`/etc. (7 false positives).
  Now accepts all common audio formats.
- **Fragile unzip** crashed on a single corrupt member (`zlib Error -3`) where the good
  `.cdg`/`.mp3` extract fine (6 false positives). Now falls back to system `unzip`, which
  skips the bad member.

**Also:**
- **mpv CD+G render fixed.** The mpv diagnostic now attaches the companion audio
  (`--audio-file=`, mirroring production `loadfile` + `audio-add`) so mpv has a timeline to
  seek into — the earlier "mpv can't render CD+G" finding was a test artifact of handing it
  a bare, timeline-less `.cdg`.
- **Download gate quarantines, never deletes.** A rejected download (and its sidecars) is
  moved to a `_playability_quarantine/` subdir (skipped by `scan()`) instead of being
  deleted — an automated verdict can be wrong, so it must never irreversibly destroy a file.
  Link/upload still hard-block (recoverable 422).
- **Batch is decode-only by default** (fast, no Xvfb). The VLC-vs-mpv render matrix is now
  opt-in via `--render-matrix`. The report's "unplayable" list keys on the deterministic
  verdict.

Validated against the 166-file review set: the fixed checker flags only the genuinely-broken
files (truncated downloads, no-audio/no-stream, corrupt zips) and passes the rest.

**Deploy:** backend change — requires `git pull` + `sudo systemctl restart kj-controller`
(interrupts playback; do off-show). The pending full-library batch should be re-run with the
fixed, decode-only checker.

---

## 2026-06-30 - In-browser file preview playback (v0.44.0)

**Why:** When several versions of a song surface in the rotation "Link song"
search (local downloads, KaraokeNerds/YouTube, divebar GCS mirror), there was no
way to audition them before linking — the only way to hear/see a file was to play
it on the device's HDMI/PA, impossible mid-show.

**What:** A `▶` preview button on every link-search row and Available Songs row
opens a modal that plays the file **in the browser** (small video render + audio,
with seek), without ever touching the primary player or device A/V output — safe to
use while a singer is performing. Delivery is chosen per file:

- H.264/AAC mp4 + webm (local or GCS) → HTTP byte-range to `<video>` (zero CPU).
- CDG zips → inner `.mp3` + `.cdg` rendered in a `<canvas>` synced to `<audio>`
  (zero CPU, perfect seek) via the new dependency-free `static/cdg.js`.
- audio → byte-range `<audio>`.
- mkv/avi/odd-codec mp4 → ffmpeg→HLS transcode (≈480p, niced, single-job),
  **cached on disk** so any file transcodes at most once, ever.
- YouTube candidates → IFrame embed; divebar GCS files → fetched once into the
  cache then handled like a local file.

New modules: `preview.py` (`PreviewService` + `parse_range`), `preview_cache.py`,
`preview_transcode.py`; frontend `static/preview.js`, `static/cdg.js`,
`static/vendor/hls.min.js`. New routes under `/preview/*`. New config:
`preview_cache_dir`, `preview_cache_max_bytes` (8 GiB LRU), `preview_transcode_height`,
`preview_transcode_preset`.

**Deploy steps:** Backend change → requires a service restart (interrupts active
playback); deploy off-show: `git pull` + `sudo systemctl restart kj-controller`.
Frontend assets are served versioned (`?v={{ app_version }}`); hard-refresh the KJ
browser if it had the old page cached. Requires `ffmpeg`/`ffprobe` on the device
(already present). The preview cache directory is created on first use under
`<download_folder>/.preview-cache` unless `preview_cache_dir` is set; point it at the
roomiest mount (e.g. the 4TB SSD) if desired.

---

## 2026-06-29 - Rotation "Link song" search row polish (v0.43.2)

**Why:** Follow-up polish to the v0.43.0 search dropdown. The format pills
(e.g. `cdg+mp3`) and brand codes (e.g. `KV`) didn't line up into clean columns —
local rows sat 8px out of step with Karaoke Nerds / Divebar rows because each row
type used different inter-column spacing. One "Link" button also rendered smaller
than the rest (its font-size wasn't rem-anchored), and the ⭐ trusted-brand marker
had no explanation on hover.

**What:**
- **Aligned tag columns.** Format and brand pills now occupy fixed-width, right-aligned
  slots (`rotTagsHtml`) so they form consistent columns across every row type and group.
  Normalized the row flex spacing in the dropdown (`.kn-track` / `.kn-local-match` share
  one `gap`; the actions column's stray `margin-left` is dropped) so the slots land at the
  same x regardless of source.
- **Uniform controls.** Unified `.kn-play-btn` / `.kn-download-btn` sizing
  (rem-anchored font-size, shared padding/min-width) — all Link / DL & Link buttons now
  match. Fixed the one-off smaller button.
- **Tooltips.** ⭐ now has a "Reliably high-quality brand (KJ-trusted)" title and the
  **Best** pill a "Best available version for this song" title.
- Fixed a stale `test_css_loaded` e2e assertion left by the v0.43.1 cache-bust (the
  stylesheet `href` now carries a `?v=` query string).

**Deploy:** Frontend-only (JS/CSS) + version bump. `git pull` + restart busts the
cache via the new `?v=0.43.2`; takes effect on next browser refresh.

---

## 2026-06-29 - Rotation "Link song" search UX overhaul (v0.43.0)

**Why:** Linking a song to a rotation entry was slow and noisy during live shows:
the initial search didn't fire until you typed a throwaway character; there was no
"searching" feedback so you'd mash space and waste scrapes; clicking anywhere off the
results list wiped them (forcing a multi-second re-scrape); file type and download
source were shown inconsistently; popular tracks buried the good versions under 50+
low-quality commercial cover-band downloads; and the "Unknown" section lumped the
trusted 4TB-SSD library together with messy YouTube downloads.

**What (all in the rotation Link/Add search dropdown):**
- **Instant initial search.** Opening Link now fires the search immediately. Root cause
  of the old lag was a global "close on outside click" handler that fired on the *same*
  click that opened link mode and cleared the pending search timer — that handler is
  removed (#1, #4). The list now persists until Cancel / Esc / a completed link.
- **Dedicated Search button + "Searching…" indicator** beside the song input, so you can
  re-trigger on demand and always see when a search is in flight (#2).
- **File-type badge on every row** (mp4 / cdg+mp3 / …) and a **source badge** (GCS / Drive
  / YouTube) beside every "DL & Link" button so you know what you're downloading (#3).
- **Collapse low-quality commercial noise.** Non-"KJ-stated" commercial *download* options
  fold under a "▸ N more commercial versions to download" expander whenever a good option
  (community, or a stated-commercial brand) is already visible. The trusted set is the
  single source of truth already in `version_priority.py` (`COMMERCIAL_STATED` /
  `COMMUNITY_STATED`); stars now mark stated brands (#5A).
- **Meaningful groups instead of "Unknown."** Unknown-brand local files are split by the
  backend (`local_grouping.py`) into "Library — Karaoke - Digital/Active|Dead|…" (by SSD
  folder) and "From YouTube — community brand" vs "From YouTube — unverified" (by filename
  brand / our GCS-mirror naming / cross-reference against community brands in the same
  search) (#5B). Recognized-brand local files still sort into Community/Commercial.

**Deploy:** kj-autodeploy is OFF — deploy manually (`git pull` + service restart). This
release includes backend changes (`routes.py`, `version_priority.py`, new
`local_grouping.py`), so a **service restart is required** and will interrupt active
playback — restart between singers.

---

## 2026-06-04 — Fix stale results in rotation link search (v0.35.1)

**What changed**
- `doRotationSearch` (`static/app.js`) now bumps `rotSearchGen` at the start of every
  query, so an earlier, slower in-flight `/rotation/search` request supersedes itself
  and is discarded instead of rendered. Fixes the bug where the link autocomplete would
  show results for an *earlier* partial query (e.g. `frank`) after you'd finished typing
  a fuller one (e.g. `frank might`) — caused by out-of-order responses, since the live
  Karaoke Nerds scrape has highly variable latency (broad queries return more, slower).
  The latest query now always wins regardless of which response lands last.
- Input debounce raised 300ms → 700ms. `/rotation/search` does a live Karaoke Nerds
  scrape, so a longer debounce avoids firing intermediate queries on every keystroke
  (fewer wasted scrapes). Correctness is guaranteed by `rotSearchGen`, not the debounce.
- New e2e regression test `TestRotationSearchRace` reproduces the race in a real browser
  (shims `window.fetch` so a broad query resolves after a narrow one) — fails without the
  guard, passes with it.

**Deploy steps**
- Frontend + version-bump only (no Python behavior change). Auto-deploy pulls the code;
  the version bump (0.35.0 → 0.35.1) busts the `app.js?v=` cache so browsers load the fix
  on next refresh. No service restart required.

## 2026-06-04 — Unified song-text normalization + fuzzy matching

**What changed**
- New `text_normalize.py` (+ `static/text_normalize.js` twin, node-parity-tested) is
  the single source of truth for search normalization: `&`↔`and`, numbers↔words,
  diacritics/Unicode, `feat.`, abbreviations (pt/vs), apostrophes/punctuation. Every
  search call site (catalog FTS index+query, the `media_trigram` index,
  `routes.unified_search` local filter, `routes._normalize_song_key` grouping, and the
  frontend `app.js`) now routes through it. Fixes the "Simon and Garfunkel" vs
  "Simon & Garfunkel" link bug.
- rapidfuzz fuzzy fallback for typos (token-overlap gated, bm25-ranked `media_trigram`
  candidates). **New dependency: `rapidfuzz` (in `requirements.txt`).**
- `NORMALIZER_VERSION` stamp + `index_is_stale()` startup warning; `scripts/reindex_catalog.py`;
  recall metrics harness (`scripts/search_metrics.py`); 975 real rotation-history queries
  captured for regression testing. Decision: bracketed qualifiers like `(Live)` are NOT
  stripped, so version grouping is more granular than before.

**DEPLOY PROCEDURE (required for any normalizer change — order matters)**
Auto-deploy only `git pull`s; it does NOT install deps, reindex, or restart. On each device:
1. `git pull` (auto-deploy handles this).
2. **Install new deps in the venv** — `catalog.py` imports `rapidfuzz` at module load,
   so the service will NOT restart without it:
   `/opt/nomad/kjbox/kj-controller/venv/bin/pip install -r requirements.txt`
3. **Reindex** (full rebuild of FTS + trigram over ~398K rows; `reindex_catalog.py` calls
   `init_schema()` first, so it creates `media_trigram`/`catalog_meta` on a pre-existing DB):
   `/opt/nomad/kjbox/kj-controller/venv/bin/python scripts/reindex_catalog.py`
4. **Restart** (interrupts playback): `sudo systemctl restart kj-controller`
   (backend change — must restart to take effect; service runs on 127.0.0.1:5001).
Until reindexed, the query normalizer and the index disagree → degraded search + a stale-index
log warning.

**Deployment status**
- NomadPC: DEPLOYED + verified live 2026-06-04 (398,446 rows reindexed; `/search` for
  "Sound Of Silence - Simon and Garfunkel" returns the "& Garfunkel" rows).
- NomadPi: NOT yet deployed — run the procedure above if it's used for shows.
