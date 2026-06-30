# Bare-.cdg guard + file-type display — plan

**Date:** 2026-06-30 · **Branch:** `feat/sess-20260630-0230-cdg-noaudio-guard-filetype` · target v0.46.0

## Problem
A bare `.cdg` (graphics, no audio — e.g. `divebar__SDK - ABBA - Dancing Queen.cdg`) is a
first-class indexed media type, so it shows in Available Songs and is link/play-eligible. But
playing it on the main screen is **silent** (embarrassing mid-show). The existing playability
gate *allows* it (classify→`unknown`→ffmpeg cdgraphics exposes a video stream→`has_video` true→
verdict ok), and `/play` doesn't gate at all. Separately, the UI shows no file type/extension,
so a KJ can't tell a `cdg-zip` from a bare `.cdg` from an `.mp4` before clicking.

## Design

### 1. Bare-`.cdg` is playable only with a same-stem sibling audio (one source of truth)
`playability.py`:
- `classify_kind`: `.cdg` → new kind `"cdg_bare"`.
- `sibling_cdg_audio(cdg_path)` → path to a same-stem file whose ext ∈ `CDG_AUDIO_EXTS`, else None.
- `check()`: `cdg_bare` branch sets `res.cdg = {has_cdg, has_audio, sibling_audio, ok, error}`
  where `ok = sibling exists` and `error = "graphics-only .cdg — no audio track"` when not.
- `compute_verdict`: fold `cdg_bare` into the `cdg_zip` branch (`base_ok = result.cdg.ok`).
  → automatically rejects audioless bare `.cdg` at `/rotation/link` **and** `media.py` downloads.

`routes.py /play`: add an `elif validated.endswith('.cdg')` branch (mirror of the `.zip` branch):
no sibling → `400` with a clear message; sibling present → mpv plays the `.cdg` + `audio_file=sibling`,
VLC plays the sibling (auto-discovers the `.cdg`). Closes the only ungated playback path.

### 2. File type + extension everywhere
- `utils.media_type_label(name_or_ext)` → `cdg-zip` / `cdg` / `mp4` / `mkv` / `mp3` / …
- `media.list_items()`: add `ext`, `media_kind` (label), and `cdg_no_audio` (bare `.cdg` w/o
  sibling, per-folder-cached lookup).
- `static/app.js` `createMediaItemLi`: muted `media_kind · .ext` badge; bare-`.cdg`-no-audio gets a
  red "no audio" tag and its row click is short-circuited (so it can't trigger a silent `/play`).
- `preview.py /preview/resolve`: every response carries `format` + `ext`; `static/preview.js` shows
  them in the modal header. A `cdg_bare` with sibling previews as CDG (raw `.cdg` + sibling audio);
  without one → `unavailable` "Graphics-only .cdg — no audio track".

## Tests
playability: classify `.cdg`→cdg_bare; `sibling_cdg_audio` finds/ignores by stem+ext; verdict ok
with sibling, fails without (+reason). routes: `/play` 400 on audioless `.cdg`, plays w/ sibling
(vlc vs mpv path). media: `list_items` ext/media_kind/cdg_no_audio. utils: label mapping. preview:
resolve cdg_bare (sibling→cdg, none→unavailable) + format/ext present. Frontend: node syntax +
manual.

## Out of scope
Symmetric bare-`.mp3`-without-lyrics block (only `.cdg` per request). Auto-zipping bare pairs.
