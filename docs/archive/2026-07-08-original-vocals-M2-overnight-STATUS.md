# Original-Vocals Guide — overnight autonomous run STATUS (2026-07-08)

Good morning! Here's exactly where things stand and what's left. Written ~03:15,
**updated ~05:55** while work continued autonomously.

## ⭐ UPDATE ~05:55 — FEATURE IS LIVE + SMOKE-TESTED (9/9 PASS)
- Dataset **built (1,488)** and **synced to the device** clean 1:1 (stale wrong-input vocals removed).
- Feature **merged to main (#181, v0.75.0)** → autodeploy **restarted the idle device** cleanly (no errors).
- **On-device smoke test PASSED 9/9**: playing a padded master sets the `lavfi-complex` amix
  (`volume=0.0000` at start), raising the slider rebuilds it (`volume=0.3008` at 30%), `/status`
  reflects it, and pitch works while the guide is active. Device left clean (stopped, vol 223).
- 🔄 **Padding** on device (~545/1,488, ETA ~06:15). 🔄 **Originals refresh** to NOMAD-audio running.
- ⏭ Final duration-verify runs once padding + originals-refresh finish.
- **You can test now** on any already-padded song: play a NOMAD master (mpv) → the "Original
  Vocals" slider appears → raise it → the original vocal comes in under the karaoke; pitch shifts both.
- Note: `/control action=stop` is the correct stop (raw mpv stop gets auto-replayed by crash-recovery).

## ✅ ~06:12 — FULLY COMPLETE, ALL VERIFIED
- Padding **1,488/1,488** (0 fail); `NOMAD-audio` refreshed to **1,488** M1 originals.
- Final duration-verify: **0 mismatches** — raw≈original (0), padded=raw+10 (0), none unmatched/unpadded → **VERDICT OK**.
- Device dirs all **1,488** (NOMAD-audio / NOMAD-vocals / NOMAD-vocals-padded); device idle, karaoke_volume restored (223).
- All 6 tasks done. Nothing left running. Feature ready to use across the full catalogue.
- Remaining future work (not tonight): karaoke-gen write-path so new renders auto-emit original+vocals; optional upgrade from fixed-5s to measured per-track offsets.

---


## The goal (recap)
Layer the ORIGINAL singer's isolated vocals under a NOMAD karaoke master during
playback, at an adjustable "Original Vocals" slider (default 0%/off; you raise it
~30%) as a sing-along guide. Pitch shifts both streams together.

## Decisions you made before bed
1. Reuse existing in-folder `(Vocals)` stems where safe; **2_HP-UVR** for the rest / anything unsure.
2. **Full/main vocals** — karaoke-model stems (KARA_2, *_karaoke) count (they output the main vocal).
3. **Fixed 5 s** lead+trail padding (MVP) — a few tracks with ~10 s intros will be ~5 s off; upgradeable later.
4. **Refresh** device `NOMAD-audio` with the M1 originals (non-critical for the test).
5. **Deploy + restart** the idle device tonight so it's testable in the morning.

## ✅ DONE
- **Feature code written, tested, committed** (branch `feat/sess-20260708-vocals-guide-playback`,
  commit `7fcb62d`, v0.75.0). 113 unit tests green; full unit suite clean.
  - mpv amix of guide under karaoke via `--lavfi-complex`; pitch stays shared (rubberband in `--af=@rb`).
  - 3rd "Original Vocals" slider (hidden until the current master has a guide).
  - CodeRabbit reviewed the **feature clean** (all 7 findings were in the M2/M1 *scripts*, since fixed).
- **mpv architecture VERIFIED on the real device (mpv 0.37)** via isolated `--ao=null` tests:
  - runtime `set lavfi-complex` amix + `af-command rb set-pitch` while guide active → both work.
  - **Critical fix**: clear `lavfi-complex` BEFORE `loadfile` (not after) — else `af-command rb`
    breaks and pitch would fail on the next normal song. Verified the fix restores pitch.
- **Disk unblocked** by your Tracks-Organized eviction → Mac ~137 GB free.

## 🔄 RUNNING (autonomous)
- **Vocals build** (`scripts/original_vocals/build_vocals.py`, pid tracked, `caffeinate`):
  router = reuse gated stem (duration-matched) else 2_HP-UVR fresh. Split **1,120 reuse / 368 fresh**.
  Log: `scratchpad/vocals_build.log`. ETA ~05:30 (early era is fresh-heavy/slow; speeds up at markers).
- **Vocals→device uploader** (`scratchpad/upload_vocals_loop.sh`): incremental rsync to
  `nomadpctunnel:/opt/nomad/downloads/NOMAD-vocals` while the build runs; final `--delete` sync
  (drops the 289 stale wrong-input vocals) when the build exits. Tunnel ~6 MB/s → ~1.8 h for ~40 GB.

## ⏭ REMAINING (autonomous, gated on build completion)
1. Final vocals sync to device (uploader auto-does this at build exit).
2. **Pad on device**: `scripts/original_vocals/device/pad_vocals.sh` → `NOMAD-vocals-padded/`
   (5 s lead + 5 s trail; runs on kjbox, not the Mac).
3. **Verify durations**: `scripts/original_vocals/device/verify_durations.py` (raw≈original, padded≈raw+10).
4. **Deploy**: push branch → PR (`@coderabbitai ignore`) → merge → autodeploy restarts the IDLE device
   (re-checks `/status` show state first) → **smoke-test** the vocal mix on-device.
5. Refresh `NOMAD-audio` with M1 originals (deferred; non-critical).
6. Housekeeping PR: `build_vocals.py` + M1 tools (`assemble_originals`, `withvocals_extract`,
   `oracle_vocals_only_sweep`) + device scripts + CodeRabbit fixes.

## How to check status
- Build: `tail scratchpad/vocals_build.log`; device count `ssh nomadpctunnel 'ls /opt/nomad/downloads/NOMAD-vocals|wc -l'`
- Feature deploy: `ssh nomadpctunnel 'curl -s http://localhost:5001/status'` → look for `original_vocals_volume` / `has_vocals_track`.
- To test: play a NOMAD master on mpv → the "Original Vocals" slider appears → raise it → you hear the original vocal under the karaoke; pitch buttons shift both.

## Known caveats
- Fixed 5 s pad: ~6 tracks with ~10 s intros will have the guide ~5 s early. Test on normal songs.
- The `--af`+`lavfi-complex` interaction is verified on the device, but the final on-device
  smoke test is the real confirmation of the audible mix + shared pitch.
