# Night-clips carousel for 2026-10-29 — 2026-10-30

**Project:** kjbox (`tools/night-clips`)   **Branch/commit:** main @ 190dda6+pull   **Status:** done (posted)

## Summary
Ran the Friday `night-clips` workflow for Thursday 2026-10-29. Andrew curated the 20-clip draft,
then asked to drop one clip from the singer with the most clips and add a clip of his own that
someone had sent him (it wasn't in Camera Uploads). He published and shared it on Instagram/Facebook.
No code changes.

## What changed
- `uv run night-clips`: 79 clips found and downloaded with no failures. Consent came from `/history/night-media`
  (`consent_source = events`, not the fallback). 75 clips eligible, 4 unknown, 0 excluded, 0 flagged.
- Edited `~/.night-clips/2026-10-29/draft.json` (backup at `draft.json.bak`):
  - Removed Mars W "Whole Being Dead Thing" (`2026-10-29_21.47.02-mp4`). Mars W had 4 clips
    (3 solo plus the Candy Store trio), the most of anyone, and this was their shortest.
  - Added Andrew's "Music Is The Victim" clip after Plant/Thriller, with the caption line
    `@beveradb — Music Is The Victim - Scissor Sisters`.
- Copied `~/Downloads/VID_20261029_232443(1).mp4` into Camera Uploads as
  `2026-10-29 23.20.49.mp4`, so the tool scans it and it now syncs to Dropbox.
- Restarted the review server so it reloaded the draft. The clip matched Andrew's song (eligible,
  offset 129s, no flags). Andrew then published, shared and clicked "Mark as posted".

## Decisions & rationale
- **Named the shared clip after its embedded `creation_time` (23:20:49 local), not its filename (23:24:43).**
  The filename time would have matched Celeste's song. A frame grab showed the "music is the victim"
  lyrics on screen, and Andrew confirmed it.
- **Added the clip by copying it into Camera Uploads with the `YYYY-MM-DD HH.MM.SS.mp4` name.** The tool
  only scans that one directory, and `FILENAME_RE` doesn't accept Android `VID_YYYYMMDD_HHMMSS` names.

## Learnings / gotchas
- **Clips shared from someone else's phone** (re-encoded to 1280x720, `VID_…` name) can have a filename time
  that's off. Check `ffprobe … format_tags=creation_time`, which is in UTC (EDT is UTC−4), and the on-screen lyrics.
- **The `night-clips` summary doesn't show up when it runs in the background.** Python buffers stdout when it
  isn't a terminal, so the log looks empty. Run it with `PYTHONUNBUFFERED=1`, or query `GET http://127.0.0.1:8765/api/state`.
- **The review server only reads `draft.json` at startup.** After editing the file by hand, restart the server
  (it serves on 8765 again).
- **Possible tool improvements (not done):** an `--extra-clip PATH` option, or accepting `VID_YYYYMMDD_HHMMSS`
  filenames, would avoid copying files into Camera Uploads by hand.
- **Caption title typos come straight from the song records,** e.g. "More Then Anything", "(Wbgv)".

## Open threads & next steps
- Andrew to free disk space: Finder → Camera Uploads → Dropbox → Make Online-Only.
- Optional: add support for clips from outside Camera Uploads to night-clips (see above).

## Related docs
- `tools/night-clips/README.md`
- `docs/superpowers/specs/2026-10-14-instagram-night-clips-design.md`
