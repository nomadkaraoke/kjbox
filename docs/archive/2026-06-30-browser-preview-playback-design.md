# In-Browser File Preview Playback — Design

**Date:** 2026-06-30
**Status:** Design approved, plan pending
**Worktree:** `kjbox-file-preview-playback` · branch `feat/sess-20260630-0020-file-preview-playback`

## Problem

When deciding which version of a song to link in the KJ UI, there is no way to
audition a file. Multiple versions surface in the rotation "Link song" search
(local downloads, KaraokeNerds/YouTube candidates, divebar GCS-mirror files) and
in the "Available Songs" list, but the only way to hear/see one is to play it on
the **primary device output** (HDMI + venue PA) — which is impossible mid-show
while a singer is performing.

We want to preview *any* supported file — including **any video format** and
**CDG zips** (a big part of the extended collection) — **in the browser**, with a
small video render + audio through the browser, with **seek**, from **every
surface that links or plays a file**, and with **zero interference** to the live
primary player/output so it can be used while a singer is singing.

## Goals

- Preview playback rendered **in the KJ's browser** (laptop/phone), never on the
  device's HDMI/PA. Non-interference is structural, not best-effort.
- Cover **all candidate sources**: local files, YouTube candidates, and divebar
  GCS-mirror files (streamed without first downloading).
- Cover **all supported formats**: browser-native video, exotic/non-native video
  (via transcode), CDG zips, and audio.
- **Seek** in every mode.
- **Efficient**: pay transcode compute at most once per file, ever — cache
  generated transcodes on disk and serve cached output on every future replay.
- Reusable from **any** link/play surface via one component.

## Non-goals (YAGNI)

- Multiple simultaneous previews.
- A transcode-quality settings UI.
- Automated cache pre-warming (the cache makes manual pre-warm trivial already).
- Previewing arbitrary non-catalog URLs.

## Constraints from the existing codebase

(From exploration — see `routes.py`, `media.py`, `playability.py`,
`zip_playback.py`, `static/app.js`.)

- **Nothing streams file bytes to the browser today.** This feature adds the
  first byte-range / media-serving routes. The only `send_file` today is the
  wallpaper thumbnail (`routes.py:1162`).
- Local media is identified to the frontend by **absolute `file_path`**, validated
  server-side by `MediaIndex.validate_path` (`media.py:148`) against
  `media_folders` (+ a `/play` fallback allowing `external_media_mount`). This is
  the trust boundary to reuse.
- Remote candidates have **no local file yet**: KaraokeNerds rows carry
  `youtube_url`; divebar rows carry an opaque GCS `file_id` (+ `format`, `in_gcs`).
- Kind classification is `playability.classify_kind` → `video|cdg_zip|audio|unknown`
  (`playability.py:22`).
- CDG zips already have extraction plumbing: `ZipPlayback.extract_and_get_mp3`
  (`zip_playback.py:22`, traversal-guarded, Deflate64 fallback) + `current_cdg_path`
  (`zip_playback.py:77`).
- Frontend is a single 6,645-line vanilla `static/app.js`, no build step. Modals
  follow a `.modal-backdrop`/`.modal-content` pattern (`style.css:638`); dynamic
  content loading example is `openDbStatusModal` (`app.js:3222`). JSON API helper
  is `apiCall` (`app.js:214`); HTML escaping is `escHtml` (`app.js:5939`). No
  `<video>`/`<audio>` elements exist yet.
- Search row renderers: `renderRotLocalRow` (has `match.path`), `renderRotKnRow`
  (has `youtube_url`, downloaded-path, divebar mirror), `renderRotDivebarRow`
  (has `file_id`, `format`). Available Songs rows: `createMediaItemLi`
  (`app.js:688`, has `item.file_path`).

## Approach (chosen)

**Per-source hybrid: cheapest viable delivery per file; transcode only as a
fallback, cached forever.** One reusable frontend preview component; the backend
picks the lightest delivery per candidate:

| Candidate | Delivery mode | Device CPU | Seek |
|---|---|---|---|
| H.264/AAC mp4, VP8/9 webm (local or GCS) | `native_video` — HTTP byte-range → `<video>` | none | native |
| CDG zip (local or GCS) | `cdg` — serve inner `.mp3` + `.cdg` → browser `<canvas>` synced to `<audio>` | none | native (audio scrub) |
| Audio (mp3/wav/flac/ogg) | `native_audio` — byte-range → `<audio>` | none | native |
| mkv/avi/mov/odd-codec mp4 (local or GCS) | `hls` — ffmpeg→HLS, ~480p, capped, **cached** → hls.js | capped, once | coarse live / full once cached |
| YouTube candidate | `youtube` — IFrame embed | none | native |

Rejected alternative: **always transcode everything** — uniform but burns CPU on
the common (native/CDG) cases that need none; worst fit for live-show safety.

## Architecture

### Frontend (new, plain JS — no build step)

- **`static/preview.js`**
  - `openPreview(descriptor)` — opens the modal, calls `POST /preview/resolve`,
    mounts the player matching the returned `mode`, wires seek + the in-modal
    action button, and handles teardown.
  - `previewButtonHtml(descriptor)` / a small button factory — lets any row add a
    `▶︎` preview affordance in one line.
  - Teardown on close/replace: stop & detach player, `POST /preview/close` (kills
    ffmpeg, releases the transcode slot; cache dir is kept).
- **`static/cdg.js`** — vendored, dependency-free CD+G parser/renderer (~300 lines).
- **`static/vendor/hls.min.js`** — vendored; loaded lazily, only when an `hls`
  preview actually starts.
- **`static/style.css`** — preview-modal styles appended, reusing
  `.modal-backdrop`/`.modal-content`.
- **`templates/index.html`** — one preview-modal `<div>` + `<script>` includes for
  `preview.js` and `cdg.js`.

The **descriptor** is the uniform interface, one of:

```js
{ source: 'local',   file_path }                 // Available Songs, downloaded rows
{ source: 'youtube', youtube_url }               // KaraokeNerds rows
{ source: 'divebar', file_id, format }           // divebar GCS rows
```

Plus optional UX context: `{ title, link_context: {entry_id, ...} }` so the modal
footer can offer the right action ("Link to <singer>" / "Download & Link" / none).

### Backend (new `preview.py` + `/preview/*` routes)

- `POST /preview/resolve` — descriptor → `{mode, token, stream_url(s), title,
  duration, reason?}`. Validates path (`validate_path` + `external_media_mount`)
  or `file_id` (divebar lookup). Chooses mode (cache-aware). Creates a
  `PreviewSession`. Accepts optional `prefer_transcode: true` so the client can
  re-resolve a GCS-video candidate as `hls` after an optimistic-native decode
  failure.
- `GET /preview/stream/<token>` — HTTP 206 byte-range serving for `native_video`
  / `native_audio`, local **or** proxied from GCS (GCS supports range GETs).
- `GET /preview/cdg/<token>/audio` and `.../graphics` — serve the extracted inner
  `.mp3` / `.cdg` (reuse `ZipPlayback`, traversal-guarded).
- `GET /preview/hls/<token>/index.m3u8` and `.../seg-<n>.ts` — the capped, cached
  transcode output.
- `POST /preview/close` — tear down the active session (kill ffmpeg, release slot).

**`PreviewSession` manager** — one active session; opaque tokens map to the
validated path/file_id server-side (raw absolute paths are never round-tripped
through the browser); TTL cleanup; kills ffmpeg + wipes *session temp* (not cache)
on close/replace. Isolated from rotation/playback code — it only *reads* files and
reuses `validate_path`, `classify_kind`, `ZipPlayback`. Nothing it does can touch
the live player.

## Data flow

**Resolve is the single entry point.** Per source:

- **Local** → `validate_path` → `classify_kind`:
  - `video` → ffprobe codecs (cheap). Browser-native (H.264/AAC, VP8/9) →
    `native_video`. Else → `hls`.
  - `cdg_zip` → extract via `ZipPlayback` (cached) → `cdg`.
  - `audio` → `native_audio`.
  - `unknown`/missing/corrupt → `unavailable` + reason.
- **Divebar GCS** → look up `file_id`:
  - video container → **optimistic `native_video`**: proxy byte-range from GCS
    directly (no pre-download, no remote ffprobe). If the browser `<video>` fails
    to decode it, the client re-resolves with `prefer_transcode: true`, which pulls
    the blob to cache once → `hls`. (Local video can ffprobe cheaply and decides
    upfront; GCS video can't, so it's optimistic-then-fallback — avoiding a
    blocking full download just to read codecs.)
  - CDG zip → pull blob to cache, extract → `cdg`.
- **YouTube** → `youtube` with `youtube_url`; client mounts the IFrame player; no
  backend streaming.

## On-disk caching (pay transcode once, ever)

Content-addressed, write-once, never recompute:

```
<preview_cache_root>/
  transcode/<key>/index.m3u8, seg-*.ts, .done   # HLS transcodes
  gcsblob/<file_id>/<name>                        # downloaded GCS originals
  cdg/<key>/audio.mp3, graphics.cdg               # extracted CDG (local + GCS)
```

- **Key** = `sha1(realpath + size + mtime + PARAMS_VERSION)` for local;
  `sha1(file_id + PARAMS_VERSION)` for GCS (objects immutable per id).
  `PARAMS_VERSION` bumps when ffmpeg settings change → auto-invalidate stale
  entries.
- **`.done` sentinel** written only after ffmpeg exits 0 (the lesson from the
  playability work — never serve a truncated transcode). A cache dir without
  `.done` is treated as absent and regenerated.
- **Resolve checks cache first.** `transcode/<key>/.done` present → return its
  `index.m3u8` immediately, **zero CPU**. A file transcodes at most once until it
  changes on disk. KJs can pre-warm tonight's songs between shows.
- GCS originals and extracted CDGs cache the same way → re-previewing a GCS
  candidate skips re-download/re-extract.
- **Eviction:** size-capped LRU (config default ~8 GB) by access time; oldest
  complete entries deleted when over cap. Cache dir defaults to the roomiest
  available mount (4TB SSD if present), config-overridable.

## Transcode mechanics

- `ffmpeg -vf scale=-2:480 -c:v libx264 -preset veryfast -c:a aac -f hls …` into
  the cache dir, wrapped in `nice -n 19 ionice -c3` so it yields to the live
  player.
- **Single global transcode slot** (a lock). A second exotic preview waits or
  bumps the first.
- Seek beyond the generated portion of an in-progress *first* transcode → client
  falls back to "seek within buffered"; once `.done`, full seek is free from cache.
- Closing the modal / starting a new preview kills any running ffmpeg and releases
  the slot. The cache dir is kept for next time.
- If `ffmpeg` is absent → `hls` mode degrades to `unavailable` + reason.

## CDG browser renderer (`static/cdg.js`)

CD+G is a 300-packet/sec stream of 24-byte packets; ~6 instruction types matter
(Memory Preset, Border Preset, Tile Block normal/XOR, Load CLUT lo/hi, Scroll).

- Parse the `.cdg` bytes once into a packet array on load.
- Drive off the `<audio>` element: each `requestAnimationFrame`, compute target
  packet index = `audio.currentTime × 300`, apply packets forward from the
  last-rendered index to a `<canvas>` (300×216 indexed buffer, 288×192 visible,
  scaled up 2–3×, 16-color CLUT).
- **Seek for free:** if `currentTime` jumps backward, reset the buffer and replay
  packets from 0 to the new index (fast array iteration). Native `<audio controls>`
  scrubbing is the transport; graphics follow.
- Vendored, dependency-free, unit-testable against a known `.cdg`.

## Modal UX

- Reuses `.modal-backdrop`/`.modal-content`. Header = title + source/format badge
  + close.
- Body mounts one of: `<video controls>` (native or hls.js), `<audio controls>` +
  `<canvas>` (CDG), or the YouTube iframe. Native controls give seek/scrub/volume.
- Preview audio comes out of **the browser tab only** (laptop/phone), never the
  venue PA.
- "Small render" — video/canvas capped ~560px wide, responsive. Loading spinner
  during resolve/first segment (mirrors `openDbStatusModal`).
- **Footer action** (context-aware): "Link to <singer>" when opened from a rotation
  link-search row (reuses the existing link / download-and-link flow with the same
  descriptor), "Download & Link" for remote candidates, or nothing in pure-browse
  contexts (Available Songs).

## Surfaces wired

- Rotation "Link song" search rows — all three renderers: `renderRotLocalRow`,
  `renderRotKnRow` (YouTube + downloaded + divebar mirror), `renderRotDivebarRow`.
- "Available Songs" list rows (`createMediaItemLi`).
- The reusable `previewButtonHtml(descriptor)` makes any additional surface a
  one-line add (e.g. unified search results) if wanted later.

## Error handling

- Resolve returns `mode:unavailable` + a human reason for: path validation failure,
  missing/corrupt file, zip with no cdg/mp3, GCS fetch failure, ffprobe finds no
  playable streams, transcode slot busy ("another preview is transcoding, try
  again"), ffmpeg absent. The modal shows the reason inline — never a blank player.
- Native `<video>`/`<audio>` `error` events surface a fallback message.
- ffmpeg failures write no `.done` → never cached as success.
- All temp/cache writes are traversal-guarded (reuse `ZipPlayback` guards).

## Testing

kjbox has pytest (CI local-only per repo memory; tests still run locally).

- **Backend unit:** resolve-mode decisions (each source × kind → expected mode);
  cache key stability + `.done` gating (interrupted transcode not served); LRU
  eviction; byte-range correctness (206, ranges, off-by-one); token
  opacity/validation rejects out-of-root paths; GCS passthrough vs
  cache-then-transcode branch.
- **Backend integration:** real tiny fixtures — a 1-sec H.264 mp4 (passthrough), a
  tiny CDG zip (extract → serve), a non-native sample (mkv) driving a real short
  transcode + cache hit on second resolve. Marked/skippable if ffmpeg missing (like
  the existing Xvfb integration test).
- **Frontend:** `cdg.js` parser unit test against a known packet sequence →
  expected canvas pixels; resolve→mount dispatch picks the right player per mode.
  (Use whatever JS harness the repo has; if none, a small node assertion for the
  CDG parser since it's pure logic.)

## Deployment notes (kjbox production safety)

- Backend changes (new routes/module) require a **service restart** → interrupts
  active playback; deploy off-show with explicit permission (per CLAUDE.md).
- Frontend changes (`preview.js`/`cdg.js`/`style.css`/`index.html`) take effect on
  browser refresh, but auto-deploy is OFF — manual `git pull` + restart. Bump
  `pyproject.toml` version in the same PR; note the empty `?v=` cache-bust caveat
  (hard-refresh to load new JS) tracked separately.
- New runtime dep: `ffmpeg`/`ffprobe` (already present on the device per the
  playability work). No new Python packages required beyond stdlib + existing
  `requests`/GCS client.
