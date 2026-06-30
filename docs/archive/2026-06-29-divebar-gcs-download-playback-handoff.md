# Handoff: Divebar GCS-mirror content in kjbox — download + playback (search is DONE)

_Written 2026-06-29. Picks up after the karaoke-gen divebar index fix shipped + verified._

## TL;DR

The upstream **Divebar index is now clean and correct** (karaoke-gen PR #857, deployed +
verified in prod). In the kjbox UI, **search works end-to-end** — previously-garbled mirror files
now surface correctly. But **download fails for ZIP-format mirror files** (a kjbox bug, root-caused
below), and **playback of mirrored content is unverified**. Your job: make search → download →
playback work reliably for GCS-mirrored Divebar content across all formats (mp4, zip/CDG+MP3, cdg).

**No kjbox code was changed in the previous session** — only the upstream index (a different repo).
This worktree contains only this handoff doc.

## Overall objective (user's words)

> Get to the point where **searching for, downloading, playing, and linking (to singers in the
> rotation)** all **surface GCS-mirror files prominently where available**, and work **fully as
> expected to download and play** those.

So this is broader than just "fix the zip download": the end state is that across every KJ flow —
the search panels, the Add-to-rotation flow, the per-entry **🔗 link** flow (`routes.py` link/
`download_and_link_rotation` paths at ~3603/3955), and the singer-facing `/sing` search — a GCS-
mirrored version is shown prominently when one exists, and selecting it reliably downloads (correct
format/extension) and plays (incl. zip=CDG+MP3 extraction + CDG playback). Treat the zip-download
bug below as the first concrete defect, but verify the whole surface.

---

## What just shipped (context — karaoke-gen, already done & deployed)

karaoke-gen PR #857 (merged to main, `pulumi up` applied, nightly re-index verified 2026-06-29):
- Rewrote the Divebar Drive→BigQuery filename parser to be folder-brand-aware. Result on the live
  `karaoke_decide.divebar_catalog`: null `brand_code` **18,437 → 1,619**; mis-split artists / brand-
  polluted titles fixed (e.g. `CKK - Incubus - Admiration.zip` was parsed artist=`CKK`,
  title=`Incubus - Admiration`; now artist=`Incubus`, title=`Admiration`, brand_code=`CKK`).
- Fixed the KN↔Divebar xref normalization asymmetry + removed a buggy `brand_match` Cartesian branch.
  Files cross-referenced: **23,329 → 37,916**; `brand_match` rows now 0.
- Full details: workspace memory `project_divebar_index_parsing.md` and karaoke-gen
  `docs/archive/2026-06-28-divebar-index-parsing-plan.md`.

**Net effect for kjbox:** the `divebar-lookup` Cloud Function now returns correct artist/title/
brand_code and correct kn_id→file xref. kjbox needed no change to benefit for SEARCH.

---

## Verification status (what's been checked)

| Path | Status | Evidence |
|---|---|---|
| `divebar-lookup` `search` action | ✅ correct | live: `Incubus Admiration` → `Admiration — Incubus`, Cereal Killer Karaoke, GCS |
| `divebar-lookup` `lookup` (kn_ids→xref) | ✅ correct | kn 399745→Cereal Killer; kn 985752→Rock Solid |
| GCS object + public URL | ✅ healthy | `https://storage.googleapis.com/nomadkaraoke-divebar-files/files/Cereal Killer Karaoke/CKK - Incubus - Admiration.zip` → **HTTP 200, valid 6 MB zip** |
| **kjbox UI — Divebar SEARCH** | ✅ works | searched in live KJ UI; result + GCS badge + Download button rendered correctly |
| **kjbox — DOWNLOAD (zip)** | ❌ **FAILS** | clicked Download → toast `GCS ❌ CKK - Incubus - Admiration.mp4`; log `Download failed: Download failed`. **Root cause below.** |
| **kjbox — DOWNLOAD (mp4)** | ❓ untested | likely works (no extension/playability mismatch) — verify first |
| **kjbox — PLAYBACK of mirrored content** | ❓ untested | esp. zip = CDG+MP3 needs extract + CDG playback |

---

## Root cause of the download failure (ZIP files)

The mirror has ~40% ZIP files (CDG+MP3 archives), ~40% MP4, plus some raw CDG. The download path
assumes MP4 and rejects anything that isn't a playable video:

1. **`build_divebar_filename(brand_code, artist, title, ext=".mp4")`** — `kj-controller/utils.py:30`.
   Hardcodes `ext=".mp4"`. **All three call sites pass no format**: `routes.py:1516`, `routes.py:3603`,
   `routes.py:3955`. So a `.zip` file is named `CKK - Incubus - Admiration.mp4`.
   - The frontend POST to `/divebar/download` (`routes.py:1494`) sends only
     `{file_id, artist, title, brand_code}` — **no `format`/extension** — so the backend can't
     derive the right extension even though the search result HAS `format: "zip"`.
2. `MediaIndex.download_from_url(url, filename)` — `kj-controller/media.py:332`. Takes `ext` from the
   (wrong, `.mp4`) filename (`media.py:342`), HTTP-GETs the GCS zip bytes (works — `_http_download`
   at `media.py:417` uses plain `requests.get`, returns 200), writes `divebar__….mp4`.
3. **`_gate_playable(file_path, config)` — `media.py:366`** then probes the file as playable media. A
   CDG+MP3 **zip is not a playable video → gate fails → "Download rejected (not playable)" → returns
   `None, None` → the worker reports "Download failed".** This is the actual failure surfaced.

So even mechanically the download "succeeds" (bytes land on disk) but is rejected by the playability
gate because it's a zip named `.mp4`. And there's a deeper gap: **a CDG+MP3 zip must be extracted and
the CDG played** — kjbox's local library handles CDG/zip somewhere (the catalog has CDG, the UI has a
"CDG/ZIP Only" format filter, MpvManager plays karaoke), but the **divebar download path doesn't wire
that up**.

---

## What to investigate / build (the actual task)

1. **Confirm the format split of the failure.** Test an **MP4** divebar file end-to-end (search →
   download → it lands + is playable). Expect it works — that isolates the bug to zip/cdg. Pick an
   mp4-format brand (e.g. BellySings, Nomad, Funbox have mp4) via a divebar search.
2. **Thread the real format through the download path:**
   - Frontend (`static/app.js`, the divebar result Download handler) should send the file's
     `format` (or full `filename`) in the `/divebar/download` POST. The search result already carries
     `format`, `filename`.
   - `routes.py:1494` `divebar_download` → pass the real extension into `build_divebar_filename(...,
     ext=...)` (and the same for the link-to-rotation paths at `routes.py:3603` / `3955`).
   - Consider just using the divebar catalog `filename`'s real extension as the source of truth.
3. **Handle ZIP = CDG+MP3 for download + playback:**
   - After download, if `.zip`: extract → CDG+MP3 (mirror however the local library does it — find the
     existing CDG/zip handling: `grep -rn "cdg\|zipfile\|extract" kj-controller/`).
   - Ensure `_gate_playable` accepts CDG/zip (or bypass the video gate for these formats and validate
     differently).
   - Verify **playback**: MpvManager (`mpv_manager.py`) playing a CDG (or extracted CDG+MP3). Confirm
     a mirrored CDG actually plays on the device.
4. **Verify raw `.cdg` mirror files too** (some divebar entries are bare CDG).
5. **Re-verify the full chain in the live UI** for each format: search → Download → appears in
   "Available Songs" / links to rotation → plays.

---

## How to verify (live, reproducible)

- **KJ UI**: `https://kjbox.nomadkaraoke.com` (behind **Cloudflare Access** — needs the user's SSO
  session in the browser; an automation Chrome will hit the CF login otherwise). Panels: "Search
  Divebar Karaoke" (direct), "Search Karaoke Nerds" (live scrape + xref annotation).
- **divebar-lookup endpoints** (public, no auth) — the API kjbox calls:
  - `curl -s -X POST https://us-central1-nomadkaraoke.cloudfunctions.net/divebar-lookup -H 'Content-Type: application/json' -d '{"action":"search","query":"Incubus Admiration","limit":5}'`
  - actions: `search`, `lookup` (`{"kn_ids":[...]}`), `download_url` (`{"file_id":"..."}` → returns the GCS/Drive URL), `stats`, `refresh` (token-gated).
- **BigQuery** (read via ADC): `nomadkaraoke.karaoke_decide.divebar_catalog`
  (file_id, filename, format, brand_code, artist, title, gcs_path, in_gcs via gcs_path) and
  `kn_divebar_xref`.
- **Repro file**: file_id `12EElg8Z97DtMC7QAhK7Lwlzjcu3eFLu5` = `CKK - Incubus - Admiration.zip`
  (format zip, 6 MB, in GCS). Its `download_url` returns the public GCS URL above (HTTP 200).

## Device / deploy safety (kjbox CLAUDE.md)

- NomadPC is a **live production device**. `ssh nomadpc 'journalctl -u kj-controller -f'` is safe
  (read-only). **Pushing to `main` auto-deploys within ~60s; backend (Python) changes need a service
  restart that interrupts playback — ask before pushing/restarting.** Frontend (JS/CSS/HTML) changes
  take effect on browser refresh (no restart).
- kj-autodeploy status: confirm current state (was OFF since May 22 per memory; the UI has an
  Auto-Deploy toggle — check it).
- Tests: `cd kj-controller && pytest`. Add tests for the format/extension threading + zip handling.
- There's an active leftover rotation on the device from last Thursday (not a live show) — safe to
  search/download for testing; avoid mutating rotation unnecessarily.

## Auth notes for the agent
- `GOOGLE_APPLICATION_CREDENTIALS` points to a **read-only** SA (`claude-readonly@…`). For writes
  (pulumi/scheduler/secrets) the user reauths `gcloud auth login` (admin@nomadkaraoke.com) and you
  **`unset GOOGLE_APPLICATION_CREDENTIALS` inline per Bash command** (shell state doesn't persist).
  This task is mostly kjbox-repo code + device, so ADC read is usually enough.
