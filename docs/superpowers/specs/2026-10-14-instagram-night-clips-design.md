# Instagram night clips + per-song media consent — design

Date: 2026-10-14 · Status: approved in brainstorming, pending spec review

## Goal

During the weekly Thursday show Andrew films short (~20s) portrait clips of performers on his phone
(auto-uploaded to Dropbox `Camera Uploads`). The next day he posts up to 20 of them as one Instagram
carousel on @nomadkaraoke, aiming to include at least one clip of every singer who consented.

Semi-automate this: find the night's clips, attribute each to the singer/song on the kjbox at that
moment, honour each singer's media consent **per song**, and produce a reviewed draft that is
published to Instagram with minimal manual work.

### Success criteria

- Running one command on Friday produces a review page with every clip from the night attributed
  to singer + song, consent resolved, a pre-selected set of ≤20 clips and a pre-filled caption.
- No clip is ever pre-selected unless every singer on that song has effective consent `yes`.
- Publishing uploads the selected clips as one carousel with the caption, stopping before "Share"
  for Andrew's final check.
- Singers can say "no to this one song" without revoking consent for the rest of their night.

### Decisions made (with Andrew)

| Topic | Decision |
|---|---|
| Automation level | Review-then-publish (tool drafts, Andrew approves, tool posts) |
| Where review happens | Local tool on Andrew's Mac with a local review web page |
| Posting mechanism | Playwright driving instagram.com (Graph API caps carousels at 10 items; app allows 20) |
| Singer credit | Optional Instagram handle collected on the singer page; fall back to name |
| Consent granularity | Night default + per-song overrides set by singer or KJ |
| Reminder | Weekly Friday Google Calendar event with the command |

## Verified facts (feasibility check, 2026-10-14)

- Camera Uploads filenames are the local (EDT) recording start time: `2026-10-08 22.13.56.mp4`.
- kjbox `GET /history/night?night=YYYY-MM-DD` (port 5001, via `ssh nomadpctunnel`) returns entries
  with `sung_at` in the same local time.
- Oct 8 night: all 23 clips matched to the song in progress at clip start, offsets 44–253s.
- Dropbox files are online-only placeholders; read them only after `dbx-materialize`.
- Instagram Graph API: 10 children per carousel; app allows 20. Collaborators settable on the
  carousel container only; per-child user tags undocumented.
- `singer_photo_consent` holds one row per folded name, overwritten each night, read
  night-scoped by `updated_at >= night_started_at`. Rotation `entry_id`s are recycled across
  nights, so anything keyed on an entry must also carry the night.

## Delivery: two PRs

1. **PR 1 — kjbox consent expansion** (kj-controller, deployed to NomadPC). Independently useful;
   ship before the next Thursday show so that night is recorded with per-song precision.
2. **PR 2 — `night-clips` Mac tool** (`kjbox/tools/night-clips/`). Works for older nights with a
   degraded consent fallback.

Each PR gets its own implementation plan.

---

## PR 1 — Per-song media consent + Instagram handle

### Consent model

Two layers, resolved per performer per song:

1. **Night default** — existing `singer_photo_consent`: `yes` / `no` / unknown, per person, tonight.
2. **Per-song override** — new: `yes` / `no` for one performance, keyed `(night, entry_id)`.

**Effective consent** for a person on a song = per-song override if set → else night default →
else `no`. A song is postable only if **every** listed singer resolves to `yes`. Duet/group
partners with no device of their own fall back to their night default (usually unknown = no).

Overrides set by the requesting singer apply to that singer only. A KJ override can be set for the
whole song (all performers) or for one named performer.

### Storage (rotation/sing DB, additive migrations)

- `singer_photo_consent`: add nullable `instagram TEXT` (normalised: strip `@`, lowercase,
  validated `^[a-z0-9._]{1,30}$`).
- New `song_media_consent`:
  `night TEXT, entry_id INTEGER, name_key TEXT, display_name TEXT, consent TEXT ('yes'|'no'),
  source TEXT ('singer'|'kj'), updated_at TEXT`, primary key `(night, entry_id, name_key)`.
  `name_key = '*'` means a KJ override for every performer on that song.
- New append-only `photo_consent_events`:
  `id, night, scope ('night'|'song'), name_key, display_name, entry_id NULL, consent, instagram NULL,
  source, created_at`. Written on every night-default, override and handle change. Past nights are
  resolved from this log (last event per person/scope wins within the night), so later nights
  overwriting `singer_photo_consent` never change a past night's answer.
- Renames/merges (`carry_photo_consent` paths) also carry tonight's per-song overrides and handle,
  and write events. "No" still wins a merge conflict.

### Singer UI (`/sing`, My songs / done screen)

- Each song card (upcoming **and** sung) gets a compact 📷 control: "OK to post" / "Don't post this
  one". It displays the inherited night default until changed. Only shown when
  `ask_photo_consent` is on and the card is the singer's own (not a friend's song).
- When the singer flips the night toggle **yes → no** and has already sung at least one song
  tonight, show a choice: **"Just one song"** (reveals/highlights the per-song controls on their
  sung list; night default stays `yes`) or **"Everything tonight"** (current behaviour).
- When the night default is `yes`, show an optional "Instagram handle (so we can tag you)" field
  beside the consent choice. Stored in `localStorage: sing_instagram` and re-sent like consent.
- Ownership: per-song endpoint uses the same `edit_token` proof as `/sing/photo-consent`, and the
  request must belong to the current night.
- All new strings go in `messages/en.json` and are translated into all 33 locales.

### KJ UI

- Rotation row and Night Log row get a per-song camera override: whole song, or per performer for
  duets. Same marker language as the existing 📷 (green OK / red struck-through no / dashed unknown);
  an override shows a small "song" badge so it's distinguishable from the night default.

### Endpoints

- `POST /sing/song-consent` — body `{entry_id, consent, items:[{id, edit_token}]}`; sets the
  caller's override for that song. 403 if not owner / not tonight, 400 on bad input, rate limited.
- `POST /sing/photo-consent` — accepts optional `instagram`.
- `POST /sing/submit` — accepts optional `instagram` alongside `photo_consent`.
- `POST /rotation/entry/media-consent` (KJ) — `{entry_id, consent, name?}`; `consent: null`
  clears the override.
- `GET /history/night-media?night=YYYY-MM-DD` (read-only) — Night Log entries with `sung_at`,
  performers split into individuals, and per performer
  `{name, consent: yes|no|unknown, reason: song_override_singer|song_override_kj|night_default|unknown, instagram}`
  plus `postable: bool` for the song. Nights before PR 1 shipped resolve from current
  `singer_photo_consent` only when `updated_at` falls inside that night; otherwise unknown.

### Testing (PR 1)

- pytest: resolution order (override > default > unknown=no); group song requires all; KJ `*`
  override; recycled entry_id on a later night doesn't inherit; rename/merge carries overrides and
  handle; event log reconstructs a past night after the per-name row is overwritten; handle
  validation; endpoint auth (edit_token, current night, rate limit).
- Singer-page tests for the per-song control, the "just one song / everything" prompt and handle
  field; translation completeness CI.

---

## PR 2 — `night-clips` Mac tool

Location `kjbox/tools/night-clips/` (Python, own `pyproject.toml`; deps: Flask or stdlib http
server for the review page, Playwright). Never runs on NomadPC.

### Command

`night-clips [YYYY-MM-DD] [--no-open] [--auto-share]` — default night is the most recent Thursday
(night date = show start date, matching kjbox's noon rollover).

### Pipeline

1. **Fetch night** — `ssh nomadpctunnel curl localhost:5001/history/night-media?night=…`. Unreachable
   → exit with a clear message (no cached consent).
2. **Find clips** — `.mp4`/`.mov` in `~/AB Dropbox/Andrew Beveridge/Camera Uploads` whose filename
   timestamp falls within [first song start − 10 min, last song `sung_at` + 10 min].
   Only that window is ever materialized (`dbx-materialize -j 8 -q`); disk floor kept at default.
   Materialize failures are listed and the rest continue.
3. **Probe** — ffprobe duration/rotation/dimensions; ffmpeg thumbnail at 3s into a per-night work
   dir `~/.night-clips/<night>/`.
4. **Match** — clip start `t` → the song with greatest `sung_at ≤ t` whose next song starts after
   `t`. Flag `check_singer` if `t` is within 15s after a song change or the matched song started
   more than 8 minutes earlier. No match → `unassigned`.
5. **Eligibility** — clip is eligible iff matched song `postable`. Clips of songs with an explicit
   `no` (any reason) are hidden by default (toggle "show excluded"); unknown-consent clips shown
   greyed.
6. **Pre-select** — in show order: first pick one eligible clip per consenting singer (earliest
   non-flagged), then fill remaining slots up to 20 spread across the night. Max 20.
7. **Caption** — template:
   `Thursday night karaoke 🎤 <date>\n\n` + one line per selected singer in order:
   `@handle — Song` or `Name — Song` (duets joined), plus a configurable footer (hashtags, venue).
   Template lives in `~/.night-clips/config.toml`.

### Review page (localhost)

Grid of clips: thumbnail + inline player, time, singer(s), song, consent badge with reason,
`check_singer` flag with a dropdown to reassign to an adjacent song (re-evaluates eligibility).
Toggle/drag to select and reorder (≤20 enforced), editable caption, "singers not yet covered" list
(consenting singers with no selected clip). Buttons: **Publish** and **Save draft**.

### Publisher (Playwright)

- Persistent Chromium profile at `~/.night-clips/ig-profile`; first run asks Andrew to log in.
- Steps: open instagram.com → Create → Post → upload selected files in chosen order → keep
  original (portrait) crop → caption → **stop before Share** (unless `--auto-share`), leaving the
  window open.
- Any unexpected UI state → stop, leave the browser on that step, print what failed. Never retry
  silently.
- After Andrew confirms it was shared (button on review page), write `~/.night-clips/<night>/posted.json`;
  a later run for that night warns before posting again.

### Testing (PR 2)

- Unit: matching using the real Oct 8 night as a fixture (all 23 clips match as in the feasibility
  check), boundary flagging, window filter, pre-selection coverage and 20 cap, caption rendering,
  eligibility per consent reasons.
- Manual: end-to-end dry run on a real night stopping before Share.

### Reminder

After PR 2 lands: recurring Google Calendar event, Fridays 10:00 (America/New_York), titled
"Post Thursday karaoke clips", description containing the exact `night-clips` command.

## Out of scope (v1)

- Face recognition / auto-detecting who is on screen.
- Video editing (trimming, music, text overlays); clips post as recorded.
- Graph API posting; phone-hosted review page; posting from NomadPC.
- Collaborator invites on the post.
