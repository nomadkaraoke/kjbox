# Findings: mpv crashes on AV1 video, no auto-recovery (2026-07-05)

Investigation that motivates the "player crash detection + notification + auto-recovery"
work, plus a research track to make mpv play AV1 correctly.

## TL;DR

- **A single AV1-encoded video crashes the mpv karaoke player** (mpv 0.37.0) when played
  through the app's real video-output path. mpv dies (becomes a `<defunct>` zombie) within
  ~4 seconds.
- **There is no auto-recovery.** Once mpv is dead its IPC socket refuses connections, so
  **every subsequent song fails** ("mpv failed to load") until someone clicks **Fix**
  (`/fix_audio` → `restart_instances()`) or the service restarts. On stage this reads as
  "the box broke."
- This was the **real cause of the 2026-07-02 live-show incident** — layered under the
  now-fixed false-positive "audio device issue" banner (that fix shipped as v0.66.1, PR #159,
  merged; verified working on-device on 2026-07-05).
- **~25% of recent YouTube downloads are AV1** → each is a live grenade.
- The user does **not** want to change download formats (VLC plays AV1 fine). Two workstreams:
  1. **Robustness (build):** detect a dead player engine (mpv *or* vlc), notify the KJ in the
     web UI with what happened, and auto-restart the engine.
  2. **Research (explore):** get mpv itself to play AV1 correctly (crash diagnostics, mpv /
     dav1d / ffmpeg upgrades, `mpv.conf` options like `--hwdec`).

## How the two 07-02 bugs relate

The 07-02 show hit **two stacked bugs**:

1. **False-positive banner** on a *good* song (ABBA, H.264) — a single 3s `time-pos` sample +
   a race-prone `_send_ipc` misread a slow start as "audio device issue." **FIXED** in v0.66.1
   (`request_id` reply-matching + poll-based `_verify_playback_progress`). Verified 2026-07-05.
2. **mpv crash** on an *AV1* song (Foo Fighters – My Hero) — mpv died and never recovered, so
   "Fix" only worked until the KJ re-queued the AV1 song, which killed mpv again. **NOT fixed.**
   This document is about #2.

The v0.66.1 verify change *improves diagnosis* of #2: a genuinely dead player now reports an
honest "mpv not progressing after 10s" instead of a misleading 3s "audio device issue." It
cannot restart the dead player, though — that's this work.

## Reproduction (deterministic, on NomadPC)

Device reached read-only/test-only via `ssh nomadpctunnel` (Cloudflare tunnel). App runs on
`127.0.0.1:5001` (Caddy fronts :80). Renderer was already `mpv`.

Known-good control file (**H.264**): `ABBA - Hole in Your Soul [yt-rHpSV0du_5Y].mp4`
Crashing file (**AV1**): `Foo Fighters - My Hero [yt-sUApSglzhtQ].mp4`
Both in `/opt/nomad/downloads/youtube/`.

```
B=http://127.0.0.1:5001
mpvstate(){ ps -o pid=,stat=,comm= -p "$(pgrep -x mpv | head -1)"; }   # STAT: SLsl=running, Zs=zombie
play(){ curl -s -X POST $B/play -H 'Content-Type: application/json' \
        -d "$(python3 -c 'import json,sys;print(json.dumps({"file_path":sys.argv[1]}))' "$1")"; }

# 0. fresh mpv (SLsl)                                   -> 428384 SLsl mpv
# 1. play ABBA (H.264)  -> plays, mpv survives          -> 428384 SLsl mpv, state=playing time=3
# 2. play My Hero (AV1) -> mpv ZOMBIES within ~4s        -> 428384 Zs  mpv  (defunct)
#    (verify then logs "mpv not progressing after 10s", audio_error=True)
# 3. play ABBA again    -> fails, mpv still Zs, audio_error stays True   (whole player is DOWN)
# 4. POST /fix_audio    -> relaunches mpv (429110 SLsl), audio_error=False   (recovered)
```

Key observations:
- ABBA (H.264) plays clean: `state=playing`, time advances, `audio_error=False`, 0 "not
  progressing" warnings, and **0 null `time` values across 40 rapid `/status` reads** (confirms
  the v0.66.1 `_send_ipc` fix under load).
- My Hero (AV1) → mpv PID goes `SLsl` → `Zs` (zombie) in ~4s. Stale socket
  `/tmp/mpv-karaoke.sock` remains on disk but **connections are refused** (mpv is dead).
- Recovery only via `/fix_audio` (`restart_instances()`), manual service restart, or the
  build work below.

### The crash: SIGSEGV inside FFmpeg's libavcodec (decode path)

The 5 crashes left kernel segfault records **and** systemd coredumps — the signature is
consistent and decisive:

```
kernel: mpv[…]: segfault at 19 ip …03af error 4 in libavcodec.so.60.31.102[…+aaa000]
```

**Identical fault offset across all 5 crashes** (07-02 show ×3, 07-05 tests ×2) → deterministic.
`coredumpctl info` on the latest core (`/var/lib/systemd/coredump/…`, SIGSEGV, 14.6 MB) gives the
crashing-thread backtrace:

```
#0  libavcodec.so.60 + 0x1d03af          (AV1 decode internal, no symbol)
#1  libavcodec.so.60 + 0x2a9812
#2  avcodec_send_packet (libavcodec.so.60 + 0x2aa044)   ← feeding a packet to the decoder
#3  mpv + 0x103cae  (decode loop) …
```

So the fault is a **null-ish pointer deref (addr 0x19) inside libavcodec 6.1.1's video decode**,
reached from `avcodec_send_packet`. The other threads (rubberband audio filter, libgallium/GPU)
are just idle-waiting — **this is NOT the vo/GPU path and NOT the audio filter.** (An earlier
hypothesis that it was the real `--vo=gpu` path was wrong; a standalone `--vo=null --length=4`
decode didn't crash, but that only meant `--length=4` stopped before the offending packet /
under different decode threading — the coredump is authoritative.)

Because the launch command uses `--really-quiet`, `mpv-karaoke.log` has no message; the evidence
lives in the kernel log + coredumps (`sudo dmesg -T | grep mpv`, `coredumpctl list`,
`coredumpctl info <pid>`).

This is almost certainly a **known-and-since-fixed FFmpeg/AV1 decoder bug** in 6.1.1 (late 2023).
FFmpeg exposes several AV1 decoders (native `av1`, `libdav1d`, `libaom`); if libavcodec is
routing to the native `av1` decoder rather than `libdav1d`, `--vd=lavc:libdav1d` may sidestep it.
**Decision (2026-07-05): rather than rely on a config workaround, upgrade the whole media stack**
(mpv 0.37→0.41, FFmpeg 6.1.1→latest, dav1d 1.4.1→1.5.3) so AV1 decodes correctly at the source —
see the plan's Track B.

### Decoder probe — the crash needs the real GPU output path (hwdec), not sw decode

Testing decoder selection on the AV1 file (`mpv --vo=null --ao=null --untimed --msg-level=all=v`):

- mpv's **default AV1 decoder is already `libdav1d` 1.4.1** (`Selected codec: dav1d AV1 decoder`),
  not FFmpeg's native `av1`. Forcing `--vd=lavc:libdav1d` changes nothing.
- **Software decode to null output does NOT crash** — all three variants (default, force-libdav1d,
  force-native) ran to 34% (`AV: 00:01:29 / 00:04:22`) with **no segfault** (killed by the 90s
  test timeout, not a crash).

So the segfault is **not** reproduced by plain software decode. It needs the app's real playback
path — `--vo=gpu` + `--fs`, which enables hwdec auto-probing and real A/V-synced decode threading.
The crash is in `avcodec_send_packet` but only under those conditions → most likely
**hardware-accelerated AV1 decode (Intel QSV / `av1_qsv`) or a vo↔decode-threading interaction**
that plain `--vo=null` sidesteps.

Practical consequences for the plan:
- **`--hwdec=no`** (force the software libdav1d path we just proved stable) is a cheap, reversible
  mitigation worth testing on-device with the real vo — it may stop the crash today, before any
  upgrade. Keep it in reserve even after upgrading.
- **Definitive repro** for validating any fix = play the AV1 file **through the app** (real vo),
  not `mpv --vo=null` standalone, and check `pgrep -x mpv` STAT stays `SLsl` (not `Zs`) +
  `dmesg`/`coredumpctl` gains no new mpv SIGSEGV.

## Severity: AV1 prevalence

12 most-recent YouTube downloads, by video codec:

```
h264 x8, vp9 x1, av1 x3
av1: Olivia Rodrigo - Drop Dead, Afroman - Because I Got High, Justin Bieber feat. Ludacris - Baby
```

~25% AV1. yt-dlp has recently started selecting AV1 for a meaningful fraction of videos, which
is why "it worked a week ago." (User prefers to keep AV1 rather than force H.264, since VLC
plays it fine — hence the research track to fix mpv instead.)

## Device baseline (for the research/upgrade track)

- **mpv:** 0.37.0 (libplacebo v6.338.2) — latest upstream is v0.41.0
- **FFmpeg:** 6.1.1-3ubuntu5 (libavcodec 60.31.102, libavutil 58.29.100)
- **dav1d:** libdav1d7 1.4.1-1build1 — latest is 1.5.3
- **GPU:** Intel Alder Lake-N UHD Graphics (iGPU) — supports AV1 hw decode (QSV) in principle
- **mpv.conf:** none (`~/.config/mpv/` empty) — app passes flags on the command line
- **OS:** Ubuntu (gcc 13.2)

### Current mpv launch command (`mpv_manager.py`)

```
mpv --idle --fs --ao=alsa --audio-device=alsa/hw:0,0 --af=@rb:rubberband \
    --input-ipc-server=/tmp/mpv-karaoke.sock --really-quiet --keep-open=no \
    --no-input-default-bindings --no-osc
```

No `--vo` (defaults to `gpu`), no `--hwdec` (defaults to `no` = software decode). Child spawned
via `subprocess.Popen(..., stdout=mpv_log, stderr=mpv_log, start_new_session=True)`; `mpv_log`
is `mpv-karaoke.log` (currently useless because of `--really-quiet`).

### Research questions / options to explore
1. **Capture the crash.** Drop `--really-quiet` (or add `--msg-level=all=debug`, `--log-file`),
   run with the real `--vo=gpu`/`--fs`, play an AV1 file, capture the crash (dmesg/coredump for
   a segfault; mpv log for a vo/GPU error).
2. **mpv.conf / flags:** try `--hwdec=no` (force sw), `--hwdec=auto-safe`, `--vo=gpu-next`,
   `--vo=x11`, `--gpu-api` variants, `--gpu-context` — find a combination that plays AV1 without
   crashing. Cheapest first.
3. **Upgrade dav1d** 1.4.1 → 1.5.3 (and libavcodec/ffmpeg) — if the fault is in the decode→vo
   frame handoff.
4. **Upgrade mpv** 0.37.0 → 0.41.0 — 0.37 is from 2023; a vo/AV1 crash may be fixed upstream.
   Prefer a self-contained build/appimage to avoid disturbing the system.

## Files & endpoints referenced
- `kj-controller/mpv_manager.py` — `MpvKaraokePlayer`: launch cmd, `_monitor_via_events`
  (detects socket EOF, falls back to polling — **does not relaunch**), `restart_instances`.
- `kj-controller/vlc.py` — `VLCManager` (other engine; needs equivalent death detection).
- `kj-controller/playback.py` — `PlaybackCoordinator` (owns active player + filler).
- `kj-controller/routes.py` — `/play`, `/status` (`audio_error`), `/fix_audio`, `/renderer`.
- Recovery today: `POST /fix_audio` → `restart_instances()`.
