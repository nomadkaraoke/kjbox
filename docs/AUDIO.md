# Audio Configuration

Audio setup for both NomadPi (Raspberry Pi 4) and NomadPC (Intel Mini PC).

---

## NomadPC (Intel Mini PC)

### Overview

The mini PC has an Intel HDA sound card with multiple HDMI outputs. **PipeWire** runs as the audio server but is **not used for HDMI audio** — VLC bypasses PipeWire and talks directly to ALSA `hw:0,7`.

**Why bypass PipeWire:** PipeWire 1.0.5 on this hardware fails to output audio through its HDMI sink even when properly configured (sink shows RUNNING, audio flows through pw-top, but no sound at the TV). Direct ALSA works reliably.

### ALSA Configuration

File: `/etc/asound.conf`
```
# HDMI audio output via Intel HDA HDMI 1 (connected to TV)
pcm.hdmiout {
    type plug
    slave {
        pcm "hw:0,7"
    }
}

ctl.hdmiout {
    type hw
    card 0
}
```

**Note:** PipeWire installs `/etc/alsa/conf.d/99-pipewire-default.conf` which redirects `pcm.!default` to PipeWire. We do NOT override `pcm.!default` — instead, VLC is configured to use the named `hdmiout` device explicitly, bypassing PipeWire entirely.

### Audio Devices

| Device | Name | Type | ALSA Device |
|--------|------|------|-------------|
| hw:0,0 | SN6140 Analog | Headphone/speaker jack | `default` (via PipeWire) |
| hw:0,3 | HDMI 0 | HDMI port (disconnected) | `hw:0,3` |
| hw:0,7 | HDMI 1 | HDMI port (TV "Z3") | `hdmiout` or `hw:0,7` |
| hw:0,8 | HDMI 2 | HDMI port (disconnected) | `hw:0,8` |
| hw:0,9 | HDMI 3 | HDMI port (disconnected) | `hw:0,9` |

### KJ Controller Config

In `config.json`:
```json
{
  "default_audio_device": "hdmiout",
  "audio_devices": {
    "hdmiout": "HDMI Output (TV)"
  }
}
```

VLC launches with `--aout alsa --alsa-audio-device hdmiout`.

### PipeWire Coexistence

PipeWire must stay on the **analog stereo profile** so it doesn't lock the HDMI device:
```bash
# Verify PipeWire isn't holding HDMI (should show analog profile active)
sudo -u nomad XDG_RUNTIME_DIR=/run/user/1000 pactl list cards | grep "Active Profile"
# Expected: output:analog-stereo+input:analog-stereo

# If PipeWire grabbed HDMI (e.g., after plugging in a new display), switch back:
sudo -u nomad XDG_RUNTIME_DIR=/run/user/1000 pactl set-card-profile alsa_card.pci-0000_00_1f.3 "output:analog-stereo+input:analog-stereo"
```

### Testing Audio

```bash
# Test HDMI (direct ALSA, bypasses PipeWire)
ssh nomadpc 'speaker-test -D hdmiout -c 2 -t sine -f 440 -l 1'

# Test default (goes through PipeWire → analog jack)
ssh nomadpc 'speaker-test -c 2 -t sine -f 440 -l 1'
```

### Troubleshooting: No HDMI Audio After Reboot

If HDMI audio stops working after reboot:

1. **Check if PipeWire grabbed the HDMI device:**
   ```bash
   ssh nomadpc 'sudo -u nomad XDG_RUNTIME_DIR=/run/user/1000 pactl list cards | grep "Active Profile"'
   ```
   If it shows `hdmi-stereo-extra1`, PipeWire has the device locked. Switch it back to analog (see above) and restart kj-controller.

2. **Check if VLC is using the right device:**
   ```bash
   ssh nomadpc 'ps aux | grep vlc'
   # Should show: --alsa-audio-device hdmiout
   ```

3. **Test the ALSA device directly:**
   ```bash
   ssh nomadpc 'speaker-test -D hdmiout -c 2 -t sine -f 440 -l 1'
   ```
   If this fails with "Device or resource busy", PipeWire has the device — see step 1.

### Karaoke Renderer Toggle

Karaoke playback uses one of two engines at runtime, swappable from the AV Output modal:

| Engine | Path | Pitch shift | Notes |
|---|---|---|---|
| **mpv** (default) | `MpvKaraokePlayer` | ±6 semitones (rubberband) | IPC socket `/tmp/mpv-karaoke.sock` |
| **VLC** | `VlcKaraokePlayer` | none | Dedicated VLC on :8080, fullscreen |

Filler music is always a single shared VLC on :8081 — it keeps playing while the KJ flips the toggle. `POST /renderer {mode: "mpv" | "vlc"}` switches engines; **rejected with HTTP 409 during active karaoke playback** (stop first).

Render mode persists to `config.json` (`render_mode: mpv`) and survives service restarts. See `docs/ARCHITECTURE.md § PlaybackCoordinator + KaraokePlayer Protocol` for the code-side architecture.

Use the toggle as an escape hatch if a specific file (or environmental quirk) misbehaves on one engine.

**CDG+MP3 (`.zip`) playback differs between the engines** — both render it, but they must be fed differently, and kjbox does this automatically in `/play`:

- **VLC** is handed the extracted `.mp3` and natively auto-discovers the sibling `.cdg` in the same directory for graphics. Nothing extra needed.
- **mpv** renders no graphics from the `.mp3` (audio only) and a bare `.cdg` has no audio. So mpv is handed the `.cdg` for graphics with the `.mp3` attached as an external audio track via the IPC command `audio-add <mp3> select` (after `loadfile <cdg>`). `audio-add` is used rather than a `loadfile` option because the `loadfile` options-arg position changed between mpv 0.37 (the device) and 0.38+; `audio-add` is version-stable. If the audio fails to attach, mpv playback is aborted rather than starting a silent video.

This was verified live on the device (mpv 0.37): a downloaded CKK CDG zip rendered graphics on the HDMI display with synced mp3 audio (track-list `video=cdgraphics` + external `audio=mp3`, time-pos advancing). The code-side detail lives in `docs/ARCHITECTURE.md § CDG+MP3 ZIP Playback`. (`KaraokePlayer.supports_cdg` advertises the capability; both backends report `True`.)

### Filler Audio Handoff (karaoke engine ↔ VLC filler)

The KJ Controller always has **two audio clients** taking turns on the same exclusive HDMI ALSA device (`hdmiout` → `hw:0,N`):

- **Karaoke engine** — either mpv (`--ao=alsa`, rubberband pitch shift) or VLC on :8080 (`--aout alsa`, fullscreen)
- **Filler VLC** — always present, plays filler music between songs (`--aout alsa --alsa-audio-device hdmiout`)

Only one can hold `hw:0,N` at a time. The handoff on every track:

```
filler playing → user hits Play →
  filler.fade_out: fade volume to 0 → pl_stop → filler releases ALSA
  → player.play → karaoke engine opens ALSA, plays karaoke
  → karaoke ends → engine fires EOF
  → player.ensure_released → engine fully idle, ALSA released
  → filler.fade_in → filler re-opens ALSA, fades up
```

The specifics of `ensure_released` depend on the active engine:
- **mpv:** sends `stop` over IPC and polls `idle-active` because mpv emits the `end-file` event ~350ms before it actually closes ALSA. Without this the filler races into "Device or resource busy" and dies silently. (See the next section.)
- **VLC:** sends `pl_stop` + `pl_empty`, polls state=stopped.

#### The mpv → VLC race (fixed 2026-04-16)

**Symptom:** After any karaoke track finishes, filler music appears to be playing (VLC HTTP reports `state=playing`, decoder runs, `time` advances) but is **silent**.

**Diagnostic signature:**
```bash
curl -s "http://:filler@localhost:8081/requests/status.json" | jq '.state, .stats'
# state: "playing"
# playedabuffers: 0          ← smoking gun
# decodedaudio:   12000+     ← decoder producing
# lostabuffers:   6000+      ← output module dead
```
```bash
cat /opt/nomad/kjbox/kj-controller/vlc-filler.log | tail
# alsa audio output error: cannot open ALSA device "hdmiout": Device or resource busy
# main audio output error: module not functional
# main decoder error: failed to create audio output
```
`playedabuffers=0` while `decodedaudio` grows means VLC's ALSA output module is permanently dead — audio is decoded into `/dev/null`. VLC never retries `aout` init on its own once it's marked non-functional; the only way to recover is to force a re-init (`pl_stop` then `pl_play`) or restart the VLC process.

**Root cause:** mpv emits its `end-file` IPC event **~350ms before** it actually closes the ALSA device. Measured on NomadPC:
```
t+   0ms  end-file event emitted
t+ 252ms  pcm still RUNNING (mpv draining)
t+ 303ms  pcm XRUN (transitioning)
t+ 353ms  pcm closed
```
The old end-of-track handler received `end-file` and called `fade_in_filler` → `pl_play` within 0.6ms, while mpv was still draining the ALSA device. VLC's `snd_pcm_open` hit "Device or resource busy" and its `aout` module entered the permanent broken state above.

**The fix** (`kj-controller/mpv_manager.py`):

1. **Primary — eliminate the race.** `_wait_for_mpv_idle()` is called **before** `fade_in_filler()` in `_handle_karaoke_ended`. It sends mpv `stop` over IPC and polls `idle-active` until true, then waits an extra 150ms for ALSA to drain:
   ```python
   self._send_ipc(["stop"])
   while time.time() < deadline:
       if self._get_property("idle-active") is True:
           break
       time.sleep(0.02)
   time.sleep(0.15)  # ALSA drains slightly after idle-active flips
   ```

2. **Safety net — auto-heal.** `_verify_filler_playing()` runs as a daemon thread spawned from every `fade_in_filler()` call. 4s after fade-in, it samples VLC stats: if `state=playing` and `playedabuffers=0` while `decodedaudio>100`, it calls `_relaunch_filler()` — which terminates only the VLC process (mpv untouched) and relaunches with the current filler track. Catches other failure modes (e.g., deploy-time startup races where mpv and VLC launch within ms of each other).

**Debugging tools:**
```bash
# Is mpv holding the device right now?
ssh nomadpc 'cat /proc/asound/card0/pcm*p/sub*/status | head'

# Which FDs does each process hold?
ssh nomadpc 'ls -la /proc/$(pgrep -x mpv)/fd /proc/$(pgrep -x vlc)/fd | grep snd'

# VLC aout health (played > 0 and growing = healthy)
ssh nomadpc 'curl -s "http://:filler@localhost:8081/requests/status.json" | \
  python3 -c "import sys,json;d=json.load(sys.stdin);s=d[\"stats\"];\
  print(f\"state={d[\"state\"]} played={s[\"playedabuffers\"]} decoded={s[\"decodedaudio\"]}\")"'
```

If you ever see `played=0` with `decoded>100`, VLC's aout is dead. Hit `/fix_audio` (or wait 4s for the auto-heal) — but prefer the fix: without `_wait_for_mpv_idle` the issue reproduces on every track.

**Why not use dmix?** ALSA `dmix` would let both processes share `hw:0,N` simultaneously and eliminate the handoff entirely. Rejected because (a) adds ~20ms latency, (b) locks the sample rate/format which complicates audio-monitor routing, and (c) doesn't play cleanly with the `iec958` plugin used on NomadPi. The coordination approach above is simpler and deterministic.

**Why not route both through PulseAudio?** PipeWire is pinned to the analog profile so it doesn't lock the HDMI ALSA device (see "PipeWire Coexistence" above). Routing mpv/VLC through the PulseAudio compat layer only works when the audio monitor is active (`audio_backend='pipewire'`) — and even then, PipeWire has to be explicitly flipped to an HDMI profile.

### Remote Audio Monitor

The KJ Controller includes a remote audio monitor for dev/testing. When enabled via the AV Output modal, it:

1. Restarts mpv with `--ao=pulse` and VLC filler with `--aout pulse` (PulseAudio compat, replacing ALSA direct)
2. Switches PipeWire to the HDMI profile (`output:hdmi-stereo+input:analog-stereo`)
3. Discovers the HDMI monitor source name dynamically (PipeWire appends a changing numeric suffix)
4. Runs `parec | ffmpeg` to capture from the monitor source and encode to MP3
5. Serves the stream at `GET /audio-monitor/stream`

**Listen from another machine (requires SSH — Cloudflare Access blocks direct ffplay):**
```bash
ssh nomadpctunnel 'curl -sk https://localhost/audio-monitor/stream' | ffplay -nodisp -f mp3 -i -
```

**Important notes:**
- Enabling/disabling restarts mpv and VLC (~5 second interruption)
- Single client at a time
- State is NOT persisted — after service restart, monitor is off and audio returns to ALSA
- "Reset All" in the AV Output modal stops the monitor and restores ALSA mode

**Technical details (2026-04-15):**
- PipeWire HDMI output works on NomadPC for both mpv and VLC
- Ordering matters: players must restart BEFORE PipeWire profile switches to HDMI,
  otherwise PipeWire can't claim the ALSA device (old mpv still holds it) and falls back to `auto_null`
- `parec` (PulseAudio compat) is the only reliable capture method — `ffmpeg -f pulse` silently
  fails to capture mpv audio, and `pw-cat`/`pw-record` go silent after repeated profile toggles
- mpv must use `--ao=pulse` (not `--ao=pipewire`) so audio flows through PulseAudio compat
  layer where `parec` can capture it. Native PipeWire output bypasses the PulseAudio monitor.
- The PipeWire monitor source name has a dynamic numeric suffix (e.g. `.3`, `.4`) that changes
  on each profile toggle — discovered at runtime via `pactl list sources short`

---

## NomadPi (Raspberry Pi 4)

### HDMI Audio Setup

HDMI audio requires a custom EDID file because the 7" touchscreen provides corrupt EDID data (bad checksum), which prevents the kernel from detecting HDMI audio capabilities.

**Custom EDID:** `/lib/firmware/edid/nomadpi-hdmi.bin`
- 256-byte EDID (128-byte base + 128-byte CEA extension)
- Declares 1920x1080@60 preferred timing, monitor name "NomadPi"
- **Critical:** Includes HDMI Vendor Specific Data Block (VSDB) with IEEE OUI 0x000C03
  - Without VSDB, kernel treats output as DVI (no audio support)
  - With VSDB, kernel sets `VC4_HDMI_RAM_PACKET_ENABLE` bit for HDMI audio
- Audio: LPCM 2ch (32/44.1/48kHz, 16/20/24bit), Speaker Allocation FL/FR
- Loaded via kernel parameter: `drm.edid_firmware=HDMI-A-2:edid/nomadpi-hdmi.bin`

## ALSA Configuration

File: `/etc/asound.conf`
```
# HDMI audio output via vc4-hdmi-1 (HDMI-A-2 port)
pcm.hdmiout_raw {
    type iec958
    slave {
        pcm "hw:vc4hdmi1,0"
        format IEC958_SUBFRAME_LE
    }
    status [ 0x04 0x00 0x00 0x01 ]
}

pcm.hdmiout {
    type plug
    slave {
        pcm hdmiout_raw
    }
}

# Yamaha MG-XU USB mixer
pcm.usbmixer {
    type plug
    slave {
        pcm "hw:MGXU,0"
    }
}

# Default: HDMI output
pcm.!default {
    type plug
    slave {
        pcm hdmiout_raw
    }
}

ctl.!default {
    type hw
    card vc4hdmi1
}
```

**Why the iec958 plugin is needed:** On kernel 6.12+, the vc4-hdmi MAI PCM device only exposes `IEC958_SUBFRAME_LE` format (raw HDMI audio frames). The `iec958` ALSA plugin handles encoding standard PCM audio (S16_LE etc.) into IEC958 subframes. The `plug` plugin on top handles sample rate and format conversion.

**Note on device sharing:** The HDMI audio device only allows one process at a time (exclusive access). The `dmix` ALSA plugin normally handles sharing, but cannot be used with HDMI because `dmix` only connects to `hw:` plugins directly, not through `iec958`. The KJ Controller handles this by fully stopping filler music (releasing the device) before starting karaoke playback, and restarting filler when karaoke ends.

## Audio Devices

| Card | Name | Type | ALSA Device |
|------|------|------|-------------|
| 0 | MG-XU | Yamaha USB mixer | `usbmixer` or `hw:MGXU,0` |
| 1 | vc4-hdmi-0 | HDMI port 1 (disconnected) | `hw:vc4hdmi0,0` |
| 2 | vc4-hdmi-1 | HDMI port 2 (touchscreen) | `hdmiout` (default) |

## Switching Audio Output

**Per-app (VLC command line):**
```bash
# Play through HDMI (default)
ssh nomadpi '/usr/local/bin/vlc-root-wrapper /path/to/video.mp4'

# Play through HDMI (explicit)
ssh nomadpi '/usr/local/bin/vlc-root-wrapper --aout alsa --alsa-audio-device hdmiout /path/to/video.mp4'

# Play through Yamaha USB mixer
ssh nomadpi '/usr/local/bin/vlc-root-wrapper --aout alsa --alsa-audio-device usbmixer /path/to/video.mp4'
```

**VLC GUI:** Audio > Audio Device menu lets you switch output while playing.

**System-wide default** (changes what all apps use when no device is specified):
Edit `/etc/asound.conf` and change the `pcm.!default` slave from `hdmiout_raw` to `"hw:MGXU,0"` (or vice versa).

## Testing Audio

```bash
# Test HDMI audio
ssh nomadpi 'speaker-test -D hdmiout -c 2 -t sine -f 440 -l 1'

# Test USB mixer audio
ssh nomadpi 'speaker-test -D usbmixer -c 2 -t sine -f 440 -l 1'

# Test default output (HDMI)
ssh nomadpi 'speaker-test -c 2 -t sine -f 440 -l 1'
```

## Live Event Audio Routing

### Current Setup (as of 2026-02-16)

The karaoke event setup uses two separate audio paths to avoid latency issues:

**Path 1 - Instrumental Playback (Pi > AV Unit > Stereo Speakers):**
- Pi outputs karaoke video + audio via **HDMI** (`hdmiout` ALSA device)
- Single HDMI cable runs to the AV unit (receiver/TV)
- AV unit drives full-size stereo speakers for instrumental/backing tracks
- Filler music between songs also goes through this path

**Path 2 - Vocal/Mic Audio (Mics > Mixer > Monitor Speaker):**
- **Shure SLX-D** wireless microphones connect to **Yamaha MG-XU** USB mixer
- Mixer's main output hard-wired to **Bose S1 Pro** powered monitor speaker
- Provides amplified singer vocals with zero additional latency
- Mixer handles all mic levels, EQ, and effects independently of the Pi

**Why two separate paths:** Karaoke is extremely latency-sensitive for vocals. Singers hearing even slight delay of their own voice causes confusion and poor performance. Keeping the mic audio path fully analog (mixer > speaker) guarantees zero digital latency.

### Equipment List

| Equipment | Role | Connection |
|-----------|------|------------|
| Raspberry Pi 4 | Video + instrumental audio playback | HDMI to AV unit |
| Shure SLX-D (x2) | Wireless microphones | Analog to mixer inputs |
| Yamaha MG-XU | USB mixer for mic audio | USB to Pi (available but unused), analog out to Bose |
| Bose S1 Pro | Amplified monitor speaker for vocals | Analog from mixer main out |
| AV unit + stereo speakers | Instrumental/video playback | HDMI from Pi |
| 7" Touchscreen | KJ Controller UI | HDMI-2 + USB to Pi |

### Abandoned: USB Mixer to HDMI Audio Mirroring

**Goal:** Eliminate the Bose S1 Pro by routing the mixer's mixed output (instrumentals + vocals) back through the Pi to HDMI, so the AV unit/stereo speakers would carry everything.

**What worked:**
- The Yamaha MG-XU sends its full stereo mix back to the Pi via USB capture (`hw:MGXU,0` capture device, S32_LE format, 48kHz)
- `alsaloop` successfully captured the mixer return and played it to HDMI output
- Required `dmix` ALSA plugin for the USB mixer so both VLC instances (karaoke + filler) could share the USB playback device simultaneously
- Audio was audible on the TV via HDMI

**Technical implementation that was working:**
```bash
# alsaloop command (ran as systemd service "audio-mirror")
alsaloop -C hw:MGXU,0 -P hdmiout -t 200000 -f S32_LE -r 48000 -c 2 --sync=0 -T -1 -A 5 -S 3

# dmix ALSA config was needed for USB mixer sharing:
# pcm.usbmixer_dmix { type dmix; ipc_key 1024; slave { pcm "hw:MGXU,0"; rate 48000; channels 2 } }
# pcm.usbmixer { type plug; slave { pcm usbmixer_dmix } }
```

**Why it was abandoned:**
1. **Latency:** ~200ms minimum buffer needed to avoid underruns. Singers would hear their own voice delayed through the stereo speakers, causing confusion/echo effect
2. **Audio dropouts:** With smaller buffers (`-t 50000`), `alsaloop` consumed 12% CPU and produced frequent `underrun for playback hdmiout` errors. The HDMI output path involves CPU-intensive format conversion (S32_LE > IEC958 subframes via the `iec958` ALSA plugin)
3. **Complexity:** Required dmix layer, alsaloop service management, and coordination with audio device switching in the app
4. **Reliability:** Even with tuned 200ms buffers and zero underruns, the additional latency and processing made it unsuitable for live vocal monitoring

**If revisiting in future:**
- A hardware solution (mixer aux send > AV unit analog input) would avoid all digital latency
- A more powerful Pi (or x86 device) might handle the format conversion without underruns at lower latency
- PulseAudio/PipeWire might handle the routing more gracefully than raw ALSA, but adds its own latency
- The fundamental issue is that the HDMI output requires IEC958 subframe encoding in software, which is CPU-intensive on the Pi 4
