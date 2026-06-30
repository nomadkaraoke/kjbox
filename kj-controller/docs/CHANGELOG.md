# KJ Controller — Changelog

Dated entries, newest first. Each entry notes any required deploy steps.

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
