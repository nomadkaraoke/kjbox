# Audio Configuration

> Extracted from [archive/NOMADPI-DETAILS.md](archive/NOMADPI-DETAILS.md). For hardware specs, network, display, and other system config, see that file.

## HDMI Audio Setup

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
