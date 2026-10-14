# Instagram night clips: implementation plan

Spec: `docs/superpowers/specs/2026-10-14-instagram-night-clips-design.md`

There are two PRs, built in parallel. The only coupling between them is the
`/history/night-media` JSON contract below.

## Shared contract: `GET /history/night-media?night=YYYY-MM-DD`

```json
{
  "night": "2026-10-08",
  "consent_source": "events" | "legacy",
  "songs": [
    {
      "entry_id": 1389,
      "song": "Do Ya Think I'm Sexy - Rod Stewart",
      "singer": "Jude",
      "sung_at": "2026-10-08 21:04:32",
      "added_at": "2026-10-08 20:57:51",
      "performers": [
        {"name": "Jude", "consent": "yes" | "no" | "unknown",
         "reason": "song_override_singer" | "song_override_kj" | "night_default" | "unknown",
         "instagram": "jude.sings" | null}
      ],
      "postable": true
    }
  ]
}
```

- `songs` holds only sung entries (non-null `sung_at`), sorted by `sung_at`.
- Performers come from `singers` (duet list) when it's present, otherwise `[singer]`. Composite names
  like "A & B" that have no `singers` list are kept as a single performer. Consent lookup tries the
  whole string first. If that's unknown and the name contains ` & `, `, ` or ` and `, the parts are
  resolved individually, and the song counts as yes only if every part is yes.
- `postable` is true only when every performer's `consent` is `"yes"`.
- `consent_source` is `"legacy"` when the night predates the consent event log. Those nights are
  resolved from `singer_photo_consent` rows whose `updated_at` falls inside the night.

## PR 1: kjbox per-song consent (branch `feat/sess-20261014-1344-instagram-night-clips`)

Resolution rule (`media_consent.py`, pure functions, unit tested). For each performer on a song:
1. Collect explicit song overrides for that person: the singer's own request override (applies only
   when the request's `singer_name` folds to this performer), KJ overrides for this name, and KJ
   overrides for `*`. If any of them says `no`, the result is `no`. Otherwise, if any says `yes`,
   the result is `yes`. The reason is `song_override_kj` when a KJ override decided it, otherwise
   `song_override_singer`.
2. Otherwise use the night default (the last `scope='night'` event for that name that night).
3. Otherwise `unknown`.

Tasks:
1. **Storage (sing_store.py):** additive migrations.
   - `singer_photo_consent.instagram TEXT`.
   - `sing_requests.media_consent TEXT` (`yes`, `no` or NULL): the singer's per-song override.
   - `song_media_consent(entry_id, entry_created_at, name_key, display_name, consent, source,
     updated_at, PK(entry_id, entry_created_at, name_key))`: KJ overrides. `(entry_id, created_at)`
     is unique even though entry ids get recycled.
   - `photo_consent_events(id, night, scope, name_key, display_name, entry_id, request_id,
     consent, instagram, source, created_at)`.
   - Methods:
     - `set_photo_consent(..., instagram=None)` also writes an event.
     - `normalize_instagram()`.
     - `set_request_media_consent(request_id, consent)`.
     - `set_song_media_consent(entry_id, entry_created_at, name, consent, source='kj')`.
     - `get_song_media_consents()`.
     - `night_consent_events(night)`.
     - `carry_photo_consent` also carries the instagram handle.
2. **media_consent.py:** `resolve_song(entry, request_row, kj_overrides, night_defaults)` returns the
   performers list and `postable`, plus `split_performers(entry)`.
3. **Endpoints:**
   - `POST /sing/song-consent` with body `{consent: yes|no|null, item: {id, edit_token}}`. It checks
     ownership, current night and the rate limit, rejects friend songs, and sets the request
     override.
   - `POST /sing/photo-consent` and `POST /sing/submit` accept an optional `instagram`, returning 400
     when it's invalid.
   - `POST /rotation/entry/media-consent` with `{entry_id, consent, name?}`. The name defaults to
     `*`.
   - `GET /history/night-media`.
   - `/sing/my-requests` items expose `media_consent`.
   - `_add_photo_consent` adds `song_consent = {name: yes|no}` to rotation entries (merged
     overrides) so the KJ marker can show song-level state.
4. **Singer UI (sing.js, sing.css, messages):**
   - Each own (non-friend) song card gets a per-song 📷 toggle, shown when `ask_photo_consent` is
     on. Its default label is the night setting.
   - Flipping the night setting from yes to no, with at least one sung song, triggers the "Just one
     song or everything tonight?" prompt.
   - When consent is yes, an optional Instagram handle input appears. It's stored in localStorage
     `sing_instagram`, sent on submit and on the consent change, and translated into all 33
     languages.
5. **KJ UI (app.js, style.css):** clicking the 📷 marker opens a small menu with these options:
   OK all night / No all night / No for THIS song only / OK for this song only / Clear song
   override. The marker gets a small "song" badge when an override is active. The Night Log modal
   is unchanged, because review happens in the night-clips tool.
6. **Tests:** store, resolution, endpoints, night-media and i18n completeness.
7. **Docs:** ARCHITECTURE API entries, CHANGELOG, and a version bump to 0.130.0.

## PR 2: `tools/night-clips` (branch `feat/sess-20261014-1420-night-clips-tool`)

The package is `kjbox/tools/night-clips/` (Python 3.11+, `uv`). Its modules:
- `nightclips/kjbox.py`: fetches night-media over `ssh nomadpctunnel curl -s localhost:5001/...`.
  When the endpoint returns 404 (PR 1 not deployed yet), it falls back to `/history/night`
  combined with consent `unknown` for everyone, and warns.
- `nightclips/clips.py`: scans Camera Uploads, applies the time window, materializes the files
  (`dbx-materialize -j 8 -q <files>`), probes them with ffprobe and makes thumbnails with ffmpeg.
- `nightclips/match.py`: matches clips to songs, flags edge cases and handles reassignment.
- `nightclips/select.py`: pre-selects clips (one per consenting singer, then a spread, capped at
  20) and renders the caption.
- `nightclips/server.py`: the localhost review page (stdlib `http.server` plus a single static
  HTML/JS page) and its JSON API. It serves clips and thumbnails and saves drafts.
- `nightclips/publish.py`: uses the Playwright persistent context at `~/.night-clips/ig-profile`
  and stops before Share unless `--auto-share` is set.
- `nightclips/cli.py`: the `night-clips` entry point.

The tests use an Oct 8 fixture (the real night-media shape built from the night log, with consent
synthesized) and cover the matching, flags, selection and caption.
