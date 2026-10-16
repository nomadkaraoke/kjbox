# night-clips

Turns the short clips Andrew films on his phone during the Thursday show into one Instagram
carousel (up to 20 clips) on @nomadkaraoke. It honours each singer's media consent and leaves him
to review the result before anything is posted.

The tool runs on the Mac only, never on NomadPC. Spec:
`docs/superpowers/specs/2026-10-14-instagram-night-clips-design.md` (PR 2).

## Setup (once)

```bash
cd tools/night-clips
uv sync                              # installs night-clips + Playwright into .venv
uv run playwright install chromium   # browser used by the publisher
```

Requirements: `ssh nomadpctunnel` works, `dbx-materialize` and `ffmpeg`/`ffprobe` are on PATH.

## Friday workflow

```bash
cd tools/night-clips
uv run night-clips              # last Thursday whose show has ended (Friday → yesterday)
uv run night-clips 2026-10-08   # any night (the show's start date)
```

Options:

| Flag | Effect |
|---|---|
| `--no-open` | Don't open the review page in the browser |
| `--auto-share` | Also click **Share** on Instagram. By default it stops before Share |
| `--port N` | Review server port (default 8765; the next free port is used if it's taken) |
| `--skip-materialize` | Don't download Dropbox placeholders; only clips already on disk are usable |

What it does:

1. **Fetches the night** from the kjbox with `ssh nomadpctunnel curl localhost:5001/history/night-media?night=…`.
   If the kjbox can't be reached it exits, because it never uses cached consent.
2. **Finds the clips** in `~/AB Dropbox/Andrew Beveridge/Camera Uploads`. It picks the `.mp4`/`.mov`
   files whose filename timestamp (the phone's local recording start) falls between 10 minutes
   before the first song and 10 minutes after the last one.
3. **Downloads only those clips** with `dbx-materialize -j 8 -q`. Any failures are listed and the
   rest carry on. Afterwards, free the space in Finder (Dropbox → Make Online-Only).
4. **Probes and thumbnails** each clip. Thumbnails go into `~/.night-clips/<night>/thumbs/`.
5. **Matches each clip** to the song whose play started most recently before the clip. The start is
   `started_at`, when the kjbox began playing the song. `sung_at` is when the KJ marked it Done,
   which is the song's *end*, so it's only used as the start for older nights that have no
   `started_at`. Songs marked Done without ever being played are ignored. A clip is flagged
   **check singer** when it starts within 15s after a song change, when the song started more than
   8 minutes before the clip, or when it starts more than 30s after the song was marked Done
   (something unlogged was on stage). Clips from before the first song are left unassigned.
6. **Pre-selects up to 20 clips** in show order. First it takes the earliest unflagged clip for
   each consenting singer, then fills the remaining slots spread across the night (songs not yet
   shown come first).
7. **Prints a summary** and serves the review page at `http://127.0.0.1:8765/`.

On the review page:

- Click a thumbnail to play the clip inline.
- **Select** toggles a clip. You can't select more than 20. Reorder clips by dragging them in the
  Carousel panel, or with ↑ ↓.
- On a flagged clip, use the dropdown to reassign it to an adjacent song, or to "not a
  performance". Its consent is re-evaluated straight away.
- **Singers not yet covered** lists the consenting singers who have a clip but none selected.
- The **Caption** follows the selection until you edit it by hand. **Regenerate** resets it.
- **Save draft** writes `~/.night-clips/<night>/draft.json`. A rerun restores it.
- **Publish to Instagram…** saves the draft and launches the publisher (see below). Instagram
  refuses a multi-video post if any video is over a minute, so clips longer than 59s are posted as
  their first 59s. The cut is a stream copy (no re-encode) saved to `~/.night-clips/<night>/trimmed/`,
  and the original in Camera Uploads is left alone. The page shows these as `1:03 → 0:59`.
- **Mark as posted** writes `posted.json`. If you then rerun or publish that night again, the tool
  warns you and asks you to confirm.

## How consent is applied

The kjbox resolves every performer on every song to `yes`, `no` or `unknown`. It checks for a
per-song override first (set by the singer or the KJ). If there isn't one, it uses the singer's
answer for the night. If there's no answer either, the result is unknown. This tool then sorts
each clip into one of four groups:

| Clip state | Rule | On the page |
|---|---|---|
| **eligible** | every performer is `yes` (`postable`) | normal, and can be pre-selected |
| **unknown** | nobody said no, but at least one performer is unknown | greyed. You can select it after a confirm, but it's never pre-selected |
| **excluded** | any performer said `no` (for the night or for this song) | hidden by default ("Show excluded"), and can't be selected |
| **unassigned** | no song matched | can't be selected until it's reassigned |

Flagged clips are never pre-selected, so an uncertain attribution can't slip a non-consenting
singer into the draft. The server checks the selection again on Publish: no excluded or
unassigned clips, at most 20, and unknown clips only with confirmation.

**Fallback (before the per-song consent PR is deployed):** if `/history/night-media` returns 404,
the tool falls back to the Night Log (`/history/night`). Every singer is then treated as consent
**unknown**, and a loud warning is printed and shown on the page. Nothing is pre-selected. Anything
you select there is your own call.

## Publishing (Playwright)

- The publisher uses a persistent Chromium profile at `~/.night-clips/ig-profile`.
- **Login:** the saved session in the profile normally means you're already logged in. When it
  has expired, the tool logs in by itself using `NOMAD_INSTAGRAM_USERNAME`,
  `NOMAD_INSTAGRAM_PASSWORD` and `NOMAD_INSTAGRAM_2FA_SECRET` (the authenticator-app secret; the
  6-digit code is generated with `nightclips/totp.py`). These are set in the nomadkaraoke
  workspace `.envrc` and loaded by direnv. Nothing is ever printed. If they aren't set, or
  Instagram asks for anything else (a suspicious-login check, a captcha, an emailed code), the
  terminal asks you to finish logging in by hand and waits up to 15 minutes.
- **Steps:** Create → Post → upload the selected files in order → "Original" crop → Next → Next →
  caption. It **stops before Share**. Check the order, crop and caption, then click Share
  yourself.
- If anything unexpected happens, it saves a screenshot as `~/.night-clips/<night>/publish-*.png`,
  prints which step failed and leaves the browser where it stopped so you can finish by hand.
  Nothing is retried silently.
- `--auto-share` also clicks Share and waits for Instagram's "shared" confirmation.
- Instagram's web UI changes often. If a step breaks, update the selectors in
  `nightclips/publish.py`. Each step tries the role/accessible name first and an aria-label or text
  fallback after that.

## Config: `~/.night-clips/config.toml`

The tool creates this file with defaults on its first run:

```toml
camera_uploads = "~/AB Dropbox/Andrew Beveridge/Camera Uploads"
kjbox_host = "nomadpctunnel"
max_clips = 20

[caption]
header = "Thursday night karaoke 🎤 {date_pretty}"   # {date_pretty} {date} {weekday}
line = "{performers} — {song}"                       # @handle, or the name; duets joined with " & "
footer = "#karaoke #nomadkaraoke"
```

## Files

```
~/.night-clips/
  config.toml
  ig-profile/                 # Instagram login (Chromium profile)
  <night>/
    thumbs/*.jpg
    draft.json                # selection, order, caption, manual reassignments
    trimmed/*.mp4             # first-59s copies of clips over a minute
    publish_request.json      # what the publisher was asked to upload
    publish-*.png             # screenshots when a publish step fails
    posted.json               # written by "Mark as posted"
```

## Development

```bash
cd tools/night-clips
uv run pytest
```

The tests use the real 2026-10-08 Night Log (`tests/fixtures/night_2026-10-08.json`) and the 23
Camera Uploads filenames from that night. All 23 clips must match the song in progress at the
moment recording started. The night-media fixture is generated from the Night Log, with a
deterministic yes/no/unknown consent mix.

Modules:

| Module | Job |
|---|---|
| `kjbox.py` | ssh + curl fetch, with the Night Log fallback |
| `clips.py` | scan, window, materialise, probe, thumbnail |
| `match.py` | clip → song matching and flags |
| `select.py` | eligibility, pre-selection, caption |
| `server.py` | review page and JSON API |
| `publish.py` | Playwright publisher |
| `cli.py` | the `night-clips` command |
