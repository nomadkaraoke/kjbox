# Plan: Stop downloading/playing loose CDGs without their audio

**Date:** 2026-06-30
**Repo:** kjbox (`kj-controller/`)
**Status:** Download-pairing (Fix #1) implemented + tested — shipping. Fix #2/#3 SUPERSEDED.
**Worktree:** `kjbox-standalone-cdg-investigation`

## ⚠️ Reconciliation with PR #124 (merged mid-session, 2026-06-30)

While this was in flight, **PR #124 "Block silent bare .cdg + show file type/extension
(v0.46.0)"** merged to main. It independently implemented the **block/refuse** layers:
`classify_kind → 'cdg_bare'`, `playability.sibling_cdg_audio()`, the `compute_verdict`
rejection, the `/play` guard, plus UI badges + preview. That is exactly **Fix #2 + Fix #3**
below — so those were **dropped** from this branch to avoid duplication/conflict.

What #124 does NOT do is **make loose CDGs playable** — it only blocks them. This branch
ships **only Fix #1 (download-time pairing)**: fetch the sibling MP3 and package a zip, so a
loose SDK-style CDG **that has a sibling MP3** becomes downloadable + playable instead of
merely refused. A genuinely orphaned CDG (no sibling audio in the mirror) still fails fast.

## Implementation summary (what ships in this branch — Fix #1 only)

TDD-first (Fix #1 tests only; Fix #2/#3 tests dropped — covered by #124):
- **divebar.py** — `find_sibling_audio()` resolves a loose CDG's companion audio
  (re-search → match brand + drive_path basename, ext-swapped).
- **media.py** — `download_cdg_pair()` downloads the .cdg + sibling audio, packages
  them into one `divebar__<…>.zip` (ZIP_STORED), runs the existing gate, quarantines
  on reject. Staged in a system temp dir so scan() never sees loose members.
- **routes.py** — `_resolve_divebar_spec()` centralises the cdg→pair decision; all
  three divebar enqueue sites (`/divebar/download`, `/rotation/download-and-link`,
  `approve_sing_request`) use it; the worker dispatches `pair` items to
  `download_cdg_pair`. No sibling found → fail fast (422 / RuntimeError), no file.
- **routes.py** — `handle_play` bare-`.cdg` branch: attach sibling `.mp3` (mpv) /
  play the `.mp3` (VLC), or **refuse** (400) — never silent.
- **playability.py** — gate now rejects a bare `.cdg` with no sibling audio
  ("CDG file has no audio track (needs cdg+mp3 zip)"); the zip path already
  required both members. This is the backstop + satisfies "downloads must be
  playable". Shared checker verified against batch/tier2/upload/link/fixture suites.

Remaining: optional frontend polish (collapse loose cdg/mp3 search rows) and the
karaoke-gen index-side grouping (§4) are still follow-ups. Deploy is manual /
off-show (backend change → service restart; autodeploy OFF).

---

## 1. The incident (root cause)

A standalone, audioless `divebar__SDK - ABBA - Dancing Queen.cdg` (1,735,968 B) appeared in
`/opt/nomad/YTDownloads`. Investigation (device logs + live divebar API) showed:

- **Not** a Claude test leftover; **nothing extracted a zip.** A KJ downloaded a **raw `.cdg`**.
- Device log timeline (Jun 29):
  - `23:24:40` search "abba dancing"
  - `23:25:02` `POST /rotation/download-and-link` on the Sandell Karaoke (SDK) row
  - `23:25:03` "Successfully downloaded 'SDK - ABBA - Dancing Queen' from Divebar"
  - `23:25:19` & `23:26:39` played twice — **graphics only, silent** (the feared scenario, realised)
- The orphan's size exactly matches the SDK `.cdg` track in the divebar index.

### Why it was audioless

The divebar index stores **each Drive file as its own track row**. Brands package CDGs differently:

| Brand | CDG storage | Index rows | Play path |
|---|---|---|---|
| Nomad Karaoke | single `.zip` (cdg+mp3) | 1 row, `format=zip` | zip → extract mp3 → works |
| **Sandell Karaoke (SDK)** | **two loose sibling files** in the same folder | **2 rows** (`format=cdg`, `format=mp3`), same basename | cdg row → only `.cdg` fetched → **silent** |

The KJ clicked the **cdg row**, so `download-and-link` had only that one `file_id`.
`divebar_ext()` (`utils.py:51`) correctly resolved `.cdg` from the GCS URL and streamed that single
object. **Nothing in the download path knows the sibling `.mp3` exists**, and the **play path only
attaches audio for `.zip` inputs** (`routes.py:492–508`; bare `.cdg` → `audio_file=None`). The
download gate (`media.py:_gate_playable` via `download_from_url`) passed it because a lone CDG's
graphics decode fine — the gate never checks for audio.

### Scope — sizable latent footgun, not a one-off

Live divebar `stats` (2026-06-30): **4,052 bare `.cdg`** + **4,023 bare `.mp3`** tracks
(≈**2,665 loose pairs already mirrored in GCS**). **Every** such cdg row, if clicked, downloads
silent the same way. Only the 20,411 `.zip` CDGs are safe today.

---

## 2. Goal

Loose `cdg`+`mp3` pairs must behave like the existing zip CDGs: a KJ who picks a CDG version always
gets a **playable** file with audio, or a **clear error** — never a silent file.

---

## 3. Design

Three layers; primary fix is #1. #2/#3 are defense-in-depth so a bare audioless CDG can never reach
playback regardless of source.

### Fix #1 — Pair at download, package as zip (primary, kjbox-local)

When `source == "divebar"` and `fmt == "cdg"`, resolve the sibling MP3 and download **both**,
packaging them into a `.zip` so the rest of the pipeline (gate + zip playback) is reused unchanged.

**Sibling resolution — server-side, robust (no karaoke-gen change):**
- New helper `divebar.find_sibling_audio(cdg_track, config) -> {file_id, download_url} | None`.
  - Calls the existing public `divebar.search(f"{artist} {title}")`.
  - Among results, picks the track with the **same `brand_code`** and a **`drive_path` equal to the
    cdg's `drive_path` with `.cdg`→`.mp3`** (case-insensitive ext/compare).
  - Returns its `file_id` + resolved `get_download_url`.
- Optional fast path: the search **group already contains both** cdg & mp3 tracks
  (`_group_results` groups by `(artist, title)`), so the frontend *could* pass an `audio_file_id`
  hint to skip the extra API call. Prefer the hint when present; fall back to server-side resolution.

**Enqueue + worker:**
- `download_and_link_rotation` (`routes.py:3588`) and the sibling enqueue paths
  (`routes.py:~1538`, and the `/api` mirror at `~2772` if it enqueues) build a **paired** queue item:
  `{ 'pair': True, 'cdg_url':…, 'mp3_url':…, 'title': 'divebar__<brand> - <artist> - <title>.zip' }`.
- `_download_worker` (`routes.py:358`): when `item.get('pair')`, call new
  `media.download_cdg_pair(cdg_url, mp3_url, filename)`:
  1. Download both to temp files.
  2. Zip them (store, not deflate — both already compressed) as the `.zip` named above.
  3. Run existing `_gate_playable` on the zip (the gate already understands zip→cdg+mp3).
  4. Index/link the zip; clean up temp cdg/mp3.
- **No play-path change needed for the happy path** — existing zip playback handles
  mpv (cdg + `audio-add` mp3) and VLC (mp3 + auto-discovered sibling cdg).

**If no sibling MP3 can be found** (truly orphaned cdg in the mirror): **fail fast** — do not create a
file. Return a clear KJ-facing error ("Found CDG graphics but no audio track — skipped; try another
version"). Creating no silent file is strictly better than quarantining one.

### Fix #2 — Play-path safety net (defense-in-depth)

In `handle_play` (`routes.py:458`), when `validated` is a **bare `.cdg`**:
- If `render_mode == mpv`: look for sibling `<base>.mp3` in the same folder and attach as
  `audio_file`. If none exists, **refuse to play** with "CDG has no audio" rather than play silent.
- Protects any bare cdg that slips in from legacy files or external media mounts.

### Fix #3 — Gate backstop (defense-in-depth)

Extend the download gate (or a pre-link check) so a **bare `.cdg` with no accompanying audio**
(no sibling mp3, not inside a zip) is treated as **not playable → quarantined**. Must still pass a
bare cdg that *does* have a sibling mp3 (external-media case) — check for the sibling before failing.

### Frontend (minimal)

No change strictly required (backend resolves sibling). Optional polish, deferred:
- Collapse the loose `cdg`+`mp3` rows into a single "CDG" row in the search UI (two rows for one
  logical track is confusing).
- Pass `audio_file_id` hint to save one API call.

---

## 4. Follow-up (karaoke-gen #3 — durable fix, separate PR/repo)

File an issue: have the divebar **indexer / Cloud Function** group a loose `cdg`+`mp3` pair into one
logical CDG track exposing both file_ids (e.g. a `paired_audio_file_id` on the cdg track). Then every
consumer gets both and kjbox can drop the search-based sibling resolution. This is the real cure;
Fix #1 is the fast, self-contained mitigation.

---

## 5. Backfill

The single orphan was deleted from NomadPC during investigation (confirmed GONE). Earlier directory
listing showed it was the **only** bare `.cdg` in `/opt/nomad/YTDownloads`. Add a one-time guard:
scan device download folders for bare `.cdg` (and stray `.mp3`) with no pair → quarantine.

---

## 6. Tests (kjbox `pytest`; NOTE: kjbox has no pytest CI — run locally)

- `test_divebar.py`: `find_sibling_audio` resolves mp3 by `brand_code` + `drive_path` ext-swap;
  returns `None` when absent; case-insensitive.
- `test_download_gate.py` / media: bare audioless cdg → rejected/quarantined; bare cdg **with** sibling
  mp3 → passes; zip & mp4 downloads unchanged (regression).
- worker/route: divebar cdg enqueues a **paired** item; `download_cdg_pair` yields a zip containing
  both files; zip gate passes; the zip is linked.
- play-path: bare cdg + sibling mp3 → mpv gets cdg + `audio-add`; bare cdg, no audio → refused (not silent).

---

## 7. Deploy notes

- Backend change → requires **service restart** (interrupts active playback).
- kjbox **autodeploy is OFF**; deploy manually, **off-show, with explicit permission**.
- Files touched: `kj-controller/divebar.py`, `routes.py`, `media.py` (+ tests). Frontend untouched
  unless optional polish is included.
