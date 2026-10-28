# Manage Filler Music modal — 2026-10-28

**Project:** kjbox   **Branch/commit:** PR #295 squash-merged to main (2a616f4), v0.140.0   **Status:** shipped; on-device YouTube verification pending

## Summary
Andrew had been adding filler tracks by asking another Claude session to download clips with yt-dlp, renaming
them and `scp`-ing them to NomadPC. Built a "Manage Filler Music" modal in the KJ UI (System -> Media & Output ->
Filler Music -> **Manage**) so this is all done in the browser. Mid-session Andrew added: the 5-min clips hard-stop
and restart when VLC loops them, so add smooth looping.

## What changed
- `kj-controller/filler_library.py` (new): labels sidecar `<filler_music_dir>/.filler-labels.json`, upload and YouTube
  import to MP3 (ffmpeg; optional start/length; optional `loudnorm`), loop smoothing, one-at-a-time background `JobManager`,
  stale-temp cleanup at startup (`create_app`).
- Routes in `routes.py`: `GET /filler_music/library`, `/filler_music/file/<name>` (preview), `POST rename|delete|youtube-info|import|upload|smooth`,
  `GET /filler_music/jobs/<id>`. Legacy `GET/POST /filler_music` unchanged.
- UI: modal in `index.html`, `app.js` (`openFillerModal`, `fillerAdd`, ...), `style.css`; dropdown now shows labels.
- Docs: CHANGELOG, ARCHITECTURE. Tests: `tests/unit/test_filler_library.py`, `tests/integration/test_filler_library_routes.py`.
- Live filler dir on NomadPC is `/opt/nomad/FillerMusic/` (`ssh nomadpctunnel`); had kahoot.mp3, wii.mp3, then halloween-1/2.

## Decisions & rationale
- **Rename = display label, not filename**: VLC and the saved current-track state use the filename; renaming under the player is risky.
- **Smooth loop = crossfade the last 3 s into the first 3 s** and start 3 s in (output is 3 s shorter) rather than a plain fade-out, which would still jump to the start. Tracks < 12 s get plain fades. Default on for new tracks; a per-track button saves a `<name> (loop)` copy (never overwrites).
- Upload cap 100 MB (Cloudflare free-plan request limit). YouTube links only (host allowlist). Playing track can't be deleted (409).
- Default YouTube length 5:00, max 60 min. A clip that comes out < 90% of the expected length is rejected.

## Learnings / gotchas
- YouTube section download (`download_ranges`, ffmpeg-based) runs ~1x realtime; a full-audio download of an 80-min video took 16 min (throttled ~90 KB/s) from the dev Mac. Section download is the right default; full download + local trim is only the fallback.
- Old yt-dlp (2025.12) failed with HTTP 403/ffmpeg code 8 on this Mac, even from the plain CLI; 2026.10.26 worked. NomadPC's yt-dlp version/cookies were NOT checked.
- A throttled section download "succeeded" with a 6 s file for a 20 s request, hence `_check_not_truncated`.
- macOS `sed -i` needs `''`; a failed `sed` in an `&&` chain silently skipped later steps.
- I ran `git checkout origin/main -- .` by mistake mid-session, which reverted uncommitted tracked edits; re-applied from context. Don't use it to "compare with main" (use `git stash`-free alternatives like `git worktree`/`git show`).
- kjbox repo has no CI checks on PRs (PR #295 had none); `ssh nomadpctunnel` was slow/hanging at times, so auto-deploy was not confirmed.
- The full pytest suite is slow (minutes); a `test_push_app_filter` failure seen once in a `-k` subset passed alone and in a later unit run.

## Open threads & next steps
- Verify on NomadPC after auto-deploy: Manage modal loads; YouTube import of a short clip works (yt-dlp/cookies there); run **Smooth loop** on halloween-1/2 and listen to the wrap.
- If YouTube import fails on-device: check `yt-dlp --version` there and `youtube_cookies_file`; consider bumping yt-dlp.
- Possible follow-ups not built: filler-only playlists/shuffle, per-track volume offsets, seamless MP3 gapless check (LAME encoder padding could leave a ~50 ms gap at the wrap in VLC).

## Related docs
- docs/ARCHITECTURE.md (filler_library + routes), docs/CHANGELOG.md (2026-10-28), docs/AUDIO.md (filler handoff)
