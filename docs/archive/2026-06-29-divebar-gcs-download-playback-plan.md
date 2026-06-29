# Plan: Divebar GCS-mirror content — download + playback across mp4/zip/cdg

_Written 2026-06-29. Implements the handoff `2026-06-29-divebar-gcs-download-playback-handoff.md`._

## Objective (user's words)

Searching, downloading, playing, and linking (to singers in rotation) all **surface GCS-mirror
files prominently** and work **fully as expected** across every format (mp4, zip=CDG+MP3, bare cdg).
Start with the confirmed zip-download bug, then zip extraction + playback.

## Root cause (confirmed against code + live data)

The download fails for ZIP/CDG mirror files because the on-disk extension is **hardcoded to `.mp4`**.

- `utils.build_divebar_filename(brand_code, artist, title, ext=".mp4")` — `ext` defaults to `.mp4`
  and **no caller ever overrides it**. Three enqueue sites all build the name this way and then fall
  back to `f"divebar-{file_id}.mp4"`:
  - `routes.divebar_download` — `routes.py:1516` (direct Divebar search panel)
  - `routes.download_and_link_rotation` — `routes.py:3603` (rotation add / link)
  - `routes.approve_sing_request` — `routes.py:3955` (singer-facing `/sing` approval)
- The download worker passes that name as `filename` to `media.download_from_url`, which derives the
  extension from it (`media.py:342`) → a 6 MB CDG zip is written as `divebar__….mp4`.
- `_gate_playable` (`media.py:14`) → `PlayabilityChecker.check`. `classify_kind` is **purely
  extension-based** (`playability.py:22`): `.mp4` → `kind="video"` → ffprobe integrity fails on zip
  bytes → `overall_ok=False` → `download_from_url` deletes the file and returns `(None, None)` → worker
  reports "Download failed". This is the toast the handoff observed.

**Key consequence:** the entire `cdg_zip` validation path already exists and works
(`playability.check_cdg`, zip extraction, CDG+audio decode). Once the file lands with the correct
`.zip` extension, `_gate_playable` (called with `renderers=()`) reduces to `base_ok = zip extracts +
cdg decodes + audio decodes` — **no display/Xvfb needed**, and it passes. So fixing the extension is
the whole download fix; **no `_gate_playable` change is required.**

### Empirical facts gathered
- Catalog `format` values are clean and map 1:1 to extensions: `mp4 (20764), zip (20410), cdg (4052),
  mp3 (4023), avi (664), mkv (111), mov (19), webm (9)`. ~99% of mp4/zip are in GCS.
- The GCS `download_url` path **always carries the real extension** (URL-encoded), e.g.
  `…/CKK%20-%20Incubus%20-%20Admiration.zip`. Drive URLs do **not** carry an extension.
- Device `DEFAULT_RENDER_MODE = mpv` (`config.py:16`). (Persisted device value to be confirmed on-box.)

## Design

### Part A — Download extension (the confirmed bug)

Derive the extension **server-side** from the resolved download URL, which all three sites already
have before building the filename. This needs zero frontend trust and directly covers the GCS-mirror
case (the whole point).

1. New helper in `utils.py`:
   ```python
   def divebar_ext(url=None, fmt=None):
       """Pick the on-disk extension for a Divebar download.
       Prefer the real extension in the (GCS) URL path; fall back to the catalog
       `format`; default '.mp4'. Only ever returns a known media extension."""
   ```
   - Parse `urlparse(unquote(url)).path`, take `os.path.splitext`, accept iff in the known media-ext
     set (mirror of `config.MEDIA_EXTENSIONS`).
   - Else map `fmt.lower()` → `"." + fmt` when it's a known media format.
   - Else `.mp4`.
2. At each of the 3 sites: compute `ext = divebar_ext(download_url, fmt)` and pass it through to
   `build_divebar_filename(..., ext=ext)`; make the `divebar-{file_id}` fallback use `ext` too.
3. `fmt` reaches `approve_sing_request` already plumbable via `source_meta` (already parsed for
   `brand_code`); for the two HTTP routes it comes from the request body (Part B). URL-parse alone
   already fixes 100% of GCS files; `fmt` only matters for Drive-only files lacking a path extension.

### Part B — Thread `format` from the frontend (defense for Drive URLs)

`format` is already available at render time but dropped from payloads. Add it:
- `downloadDivebarTrack` payload → include `format: track.format` (`app.js:3151`, badge already uses
  `track.format` at 3117).
- Rotation results carry `format`: `renderRotDivebarRow` (`dv.format`, used at 5626) and
  `renderRotKnRow` (`track.divebar.format`) → put `format` on the pushed `result`; `selectRotSearchResult`
  `buildCall` divebar body includes `format` (`app.js:5663`).
- Routes read `data.get('format')` and pass to `divebar_ext`.

### Part C — Playback (the real gap on mpv)

`/play` (`routes.py:485`) already extracts `.zip` → mp3 and hands the **mp3** to the player. That is
correct for **VLC** (auto-discovers the sibling `.cdg`) but on the **default mpv renderer** it plays
**audio only, no CDG graphics** — per playability.py's own note ("mpv must be handed the `.cdg`
directly or it renders no video"). This is a real defect, not just a verification step.

1. **Spike on-device (blocks the fix):** determine the exact mpv `loadfile` invocation that renders
   CDG graphics *with* mp3 audio in sync (sibling auto-discovery? `--external-file`/`--audio-file`?
   cdg demuxer behaviour?). The playability batch only frame-captures `.cdg` via `--vo=image`; it
   does not prove combined A/V playback.
2. **Fix `/play`** to choose the source by `render_mode`:
   - VLC → mp3 (unchanged).
   - mpv → whatever the spike determined (likely the `.cdg` with the mp3 as audio).
   - Keep the extraction in one place; surface a clear error if extraction yields no `.cdg`/`.mp3`.
3. **Bare `.cdg`** (4052 entries): a lone `.cdg` has no audio. Decide + implement behaviour (most
   likely: reject as unplayable with a clear message unless a sibling audio exists). Confirm during
   verification whether any bare-cdg mirror entries are genuinely standalone.
4. Confirm no other play entrypoint bypasses this (only `/play` handles zip today; verify rotation
   play-next funnels through it).

### Part D — Surfacing prominence (objective)

`#114/#115` already surface mirror rows in rotation search with priority ranking. Verify the direct
Divebar panel, rotation add/link, per-entry 🔗 link, and singer `/sing` search each surface mirror
versions prominently and selecting reliably downloads + plays. Fill gaps found.

## Test strategy

- **Unit (pytest, no device):**
  - `divebar_ext`: GCS `.zip`/`.cdg`/`.mp4` URLs, encoded paths, Drive URL + `format` fallback, junk → `.mp4`.
  - `build_divebar_filename(ext=…)` honoured; fallback name uses derived ext.
  - Each of the 3 enqueue routes: a zip URL produces a `.zip` queue title (mock `get_download_url`).
  - `download_from_url`: a `.zip`-named file is classified `cdg_zip` and a valid CDG zip passes the
    gate (reuse existing zip fixtures from the playability tests).
  - `/play` routing: given render_mode mpv vs vlc, asserts the correct source path is handed to the player.
- **Live device (with explicit permission for any restart):** mp4 e2e (isolate bug) → zip e2e
  (download lands `.zip`, passes gate, **plays graphics + audio** on the active renderer) → bare cdg →
  full UI chain incl. link-to-rotation. Confirm device `render_mode`.

## Sequencing

1. Part A + B + unit tests (the confirmed download bug) — ship-ready first.
2. Part C spike on-device → Part C fix + tests.
3. Part D verification + gap fills.
4. Full live verification (task #6), then `/shipit`.

## Risks / notes
- mpv CDG playback invocation is the main unknown → spike before coding Part C.
- Backend changes require a device service restart (interrupts playback) — coordinate with Andrew;
  frontend-only changes take effect on refresh.
- `kj-autodeploy is OFF` — device deploy is manual (git pull + restart in venv).
