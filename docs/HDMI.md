# HDMI Output Guide — NomadPC

> Living document for understanding, configuring, and troubleshooting HDMI video and audio output from the NomadPC karaoke controller.

**Last updated:** 2026-02-23

---

## Table of Contents

- [1. Intended Setup](#1-intended-setup)
- [2. Hardware Inventory](#2-hardware-inventory)
- [3. How HDMI Works](#3-how-hdmi-works)
- [4. How HDMI Audio Works](#4-how-hdmi-audio-works)
- [5. How Linux Routes Video Output](#5-how-linux-routes-video-output)
- [6. How Linux Routes Audio Output](#6-how-linux-routes-audio-output)
- [7. HDMI Splitters, EDID Emulators, and Adapters](#7-hdmi-splitters-edid-emulators-and-adapters)
- [8. Current NomadPC State](#8-current-nomadpc-state)
- [9. Testing Log](#9-testing-log)
- [10. Known Issues and Open Questions](#10-known-issues-and-open-questions)

---

## 1. Intended Setup

### Signal Chain

```
NomadPC (Intel N97, HDMI-1)
  │
  ▼
4-Port HDMI Splitter (1-in, 4-out)
  ├── Output 1 → 50ft HDMI cable → Denon AVR-3311CI → Projector + Venue Speakers
  │                                  (AV receiver)      (PRIMARY audience display + audio)
  ├── Output 2 → Wireless HDMI TX → [air] → Wireless HDMI RX → 32" monitor
  │                                                              (Singer lyrics monitor)
  ├── Output 3 → 7" touchscreen (mounted in equipment box)
  │                (monitoring/testing — least important output)
  └── Output 4 → (unused)
```

### Requirements

| Output | Video | Audio | Priority |
|--------|-------|-------|----------|
| Denon AVR → Projector | 1920x1080 | Yes — primary audio path to venue speakers | **Critical** |
| Wireless HDMI → 32" monitor | 1920x1080 | Not needed (singer just reads lyrics) | High |
| 7" touchscreen | 1024x600 native (accepts 1080p) | Tiny built-in speakers, nice-to-have | Low |

**The splitter clones one signal to all outputs**, so the NomadPC must output 1920x1080. The 7" touchscreen can downscale internally.

### At-Home Testing (Current)

Only the 7" touchscreen is connected via HDMI-1. Tomorrow (2026-02-24) the full splitter setup will be tested at the venue.

---

## 2. Hardware Inventory

### Source Device

| Item | Details |
|------|---------|
| **NomadPC** | ORIGIMAGIC "Comet Series" Mini PC |
| CPU | Intel N97 (Alder Lake-N), 4 cores, 3.6GHz, x86_64 |
| GPU | Intel Alder Lake-N UHD Graphics (integrated) |
| RAM | 16GB |
| Storage | 476GB NVMe SSD |
| OS | Linux Mint (Ubuntu-based), kernel 6.8.0-100-generic |
| Desktop | XFCE4 |
| Video ports | **2x HDMI** + **2x DisplayPort** (4 outputs, 3 CRTCs) |
| Audio chip | HDA Intel PCH — Conexant SN6140 (analog) + Intel Alderlake-P HDMI (digital) |
| Audio server | PipeWire 1.0.5 (with PulseAudio compatibility) |
| i915 driver | modesetting provider, display version 13 |
| DMI system | `ORIGIMAGIC / Comet Series / DNB20` |

### Output Devices

#### 7" Touchscreen (Currently connected)

| Item | Details |
|------|---------|
| **Monitor name** | Z3 |
| **Manufacturer** | DZX (Integrated Tech Express Inc) — EDID manufacturer code |
| **Physical size** | 15cm × 9cm (~7" diagonal) |
| **Native resolution** | 1024x600 @ 60Hz (preferred mode) |
| **Supported modes** | 1024x600, 1920x1080, 1280x1024, 1280x720, 800x600, 640x480, others |
| **EDID** | 256 bytes (128-byte base + 128-byte CEA extension), both checksums valid |
| **HDMI VSDB** | Present (IEEE OUI 0x000C03) — device identifies as HDMI, not DVI |
| **Audio (EDID)** | LPCM 2ch, 32/44.1/48kHz, 16/20/24-bit |
| **Built-in speakers** | Yes, tiny — audio confirmed working when connected to MacBook |
| **Touch** | Capacitive USB touch (separate USB cable) |
| **Notes** | Originally designed for Raspberry Pi. Mounted inside cardboard equipment box. |

#### Denon AVR-3311CI (Not currently connected — at venue)

| Item | Details |
|------|---------|
| **Type** | 7.1-channel AV receiver |
| **HDMI inputs** | Multiple HDMI inputs (one will be used) |
| **HDMI output** | HDMI out to projector |
| **Audio** | Decodes HDMI audio → amplifies → venue speakers |
| **EDID** | TODO: capture when connected |
| **Notes** | Connected via 50ft HDMI cable from splitter Output 1 |

#### Wireless HDMI Transmitter/Receiver

| Item | Details |
|------|---------|
| **Type** | Wireless HDMI TX/RX pair |
| **EDID behavior** | **Pass-through** — presents the downstream display's EDID to the source. When RX is on the Hisense TV, source sees "HISENSE". |
| **Receiver end** | Currently plugged into 55" home Hisense TV; will be moved to 32" venue monitor |
| **Notes** | TX plugs into splitter Output 4; RX plugs into display. When used behind the OREI splitter in STD mode, the pass-through EDID is irrelevant (splitter overrides it). |

#### Hisense 55" TV (Home — wireless HDMI RX connected here)

| Item | Details |
|------|---------|
| **EDID manufacturer** | HEC (Hisense) |
| **EDID product code** | 0x81C8 |
| **EDID monitor name** | HISENSE |
| **Screen size (EDID)** | 62cm × 34cm |
| **Preferred resolution** | 1920x1080 @ 60Hz |
| **Other modes** | 1080p (50/59.94/24/23.98), 1280x1024, 1280x960, 720p, 1024x768, 800x600, 640x480 |
| **HDMI VSDB** | Yes, IEEE OUI 0x000C03, max TMDS clock 320MHz |
| **Audio (EDID)** | LPCM 2ch, 32/44.1/48kHz, 16/20/24-bit |
| **Speaker allocation** | 0xFFFF (all channels claimed — likely a quirk of the TV's EDID) |
| **Notes** | This TV is only used for at-home testing. At the venue, the wireless RX moves to a 32" monitor. |

#### 4-Port HDMI Splitter (Primary)

| Item | Details |
|------|---------|
| **Brand / Model** | OREI UHDS-104 (1×4 HDMI 18Gbps Splitter) |
| **HDMI version** | HDMI 2.0b, HDCP 2.2 compliant |
| **Max bandwidth** | 18 Gbps (600 MHz) |
| **Max resolution** | 4K2K@60Hz (4:4:4), 1080p@60Hz |
| **Audio support** | LPCM 7.1ch, Dolby TrueHD, DTS-HD Master Audio |
| **Ports** | 1× HDMI Type A input, 4× HDMI Type A output |
| **Power** | 5V/1A DC, 2W consumption |
| **EDID toggle** | Physical switch: **STD** / **TV** |
| **EDID STD mode** | Built-in fixed EDID: 1080p@60Hz, LPCM 2ch stereo. Ignores downstream EDIDs. **Currently selected.** |
| **EDID TV mode** | Copies EDID from Output 1 (pass-through). Supports downscaler: if Out 1 is 1080p, all outputs get 1080p; if Out 1 is 4K, 1080p outputs get downscaled. |
| **EDID presented to source (STD)** | Manufacturer: HDC, Product: 0x0100, Name: "HDMI Splitter", Screen: 51cm×29cm. CEA: LPCM 2ch 32/44.1/48kHz 16/20/24-bit, HDMI VSDB present. |
| **Tested** | Confirmed working with MacBook and NomadPC |
| **Notes** | This is the hub of the signal chain. OUT LEDs light up to indicate which outputs have active displays connected. |

### Accessory Devices (Available but not in primary signal chain)

| Device | Details | Potential Use |
|--------|---------|---------------|
| **HDMI EDID Emulator Passthrough** | Presents a fixed built-in EDID ("Mi TV", XMD manufacturer) to the source regardless of what's downstream. See details below. | Force consistent resolution/audio caps when display is unplugged or EDID is unreliable |
| **OREI HDS-102 (1-in, 2-out)** | HDMI 1.4 splitter, 2.97Gbps, EDID switch (Auto/Copy). See details below. | Backup, daisy-chaining |
| **avedio links 1×2 HDMI Splitter** | Cheap 4K 1080p@60Hz splitter, no EDID switch, pure pass-through. See details below. | Backup |
| **HDMI → Composite/RCA adapter** | Macrosilicon-based converter. Has built-in EDID (720p preferred, LPCM up to 192kHz). See details below. | Legacy TV/monitor connection, emergency audio extraction via RCA |
| **HDMI Audio Extractor** | Has built-in EDID ("HDMI SPLITTER", HDC, 1080p preferred). HDMI in + pass-through out + 3.5mm + optical. See details below. | Extract audio without AV receiver; venue fallback |

#### HDMI Audio Extractor — Details

| Item | Details |
|------|---------|
| **Brand / Model** | J-Tech Digital JTD-3193 (JTECH-AE4KA), 4K HDMI Audio Extractor with ARC |
| **Product page** | https://www.jtechdigital.com/blogs/product-guides/setup-guide-jtech-ae4ka-jtd-3193-4k-hdmi-audio-extractor-with-arc |
| **EDID manufacturer** | HDC (same chip vendor as OREI UHDS-104) |
| **EDID product code** | 0x1000 |
| **EDID monitor name** | HDMI SPLITTER |
| **Screen size (EDID)** | 160cm × 90cm (~72" — fake, it's not a display) |
| **Preferred resolution** | 1920x1080 @ 60Hz |
| **All modes** | 1080p (60/50/59.94/30/25/24), 720p, 1024x768, 800x600, 576p, 480p, VGA |
| **HDMI VSDB** | Yes, IEEE OUI 0x000C03 |
| **Audio (EDID)** | LPCM 2ch, 32/44.1/48kHz, 16/20/24-bit |
| **Speaker allocation** | FL/FR |
| **Ports** | HDMI in, HDMI pass-through out, 3.5mm analog out, optical/TOSLINK out |
| **Audio toggle** | **2CH** / PASS / 5.1CH — currently set to **2CH** (stereo downmix to 3.5mm + optical) |
| **ARC support** | Yes — can receive audio back from TV via HDMI ARC and output to 3.5mm/optical |
| **Behavior** | Has its own built-in EDID — always presents "HDMI SPLITTER" 1080p regardless of HDMI output connection. Extracts audio to 3.5mm and optical simultaneously with HDMI pass-through. Works even with HDMI output disconnected. |
| **Notes** | Useful as a venue fallback: sits inline in the HDMI chain, passes video through, and provides a separate analog audio output. Could go between the OREI splitter and the 50ft cable to the Denon AVR as insurance. |

#### avedio links 1×2 HDMI Splitter — Details

| Item | Details |
|------|---------|
| **Brand / Model** | avedio links, "4K 1x2 HDMI Splitter 1 to 2 for 3D 1080P@60Hz" |
| **EDID switch** | **None** |
| **EDID behavior (no outputs)** | Disconnected — no EDID, source sees no display |
| **EDID behavior (1 output)** | Pure pass-through of the connected display's EDID |
| **Built-in EDID?** | **No** |
| **Notes** | Cheapest/simplest splitter. No manual, no EDID control. Behaves identically to a direct connection from the source's perspective. |

#### OREI HDS-102 (1×2 HDMI 1.4 Splitter) — Details

| Item | Details |
|------|---------|
| **Brand / Model** | OREI HDS-102 |
| **HDMI version** | HDMI 1.4, HDCP 1.4 compliant |
| **Max bandwidth** | 2.97 Gbps (297MHz TMDS) |
| **Max resolution** | 4K×2K @ 30Hz, 1080p @ 120Hz, 1080p 3D @ 60Hz |
| **Audio support** | DTS-HD, Dolby TrueHD, LPCM 7.1, DTS, Dolby AC3, DSD |
| **Ports** | 1× HDMI input, 2× HDMI output |
| **Power** | 5V/1A DC, 5W max |
| **EDID switch** | **Auto**: compares Output 1 and 2 EDIDs. **Copy**: copies Output 2 EDID to input port. |
| **EDID behavior (no outputs)** | **Disconnected** — presents no EDID, source sees no display |
| **EDID behavior (Auto, 1 output)** | Pass-through — presents the connected display's EDID directly |
| **EDID behavior (Copy, Out 2 empty)** | Falls back to Output 1's EDID |
| **Built-in EDID?** | **No** — unlike the UHDS-104, this splitter has no fixed internal EDID |
| **Notes** | Less useful for our setup than the UHDS-104 since it doesn't provide a consistent EDID. No output LEDs. Max 4K@30Hz (HDMI 1.4 limitation). |

#### HDMI → Composite/RCA Adapter — Details

| Item | Details |
|------|---------|
| **Chip** | Macrosilicon (HJW manufacturer ID) |
| **EDID product code** | 0x1836 |
| **EDID monitor name** | MACROSILICON |
| **Screen size (EDID)** | 0cm × 0cm (not a display) |
| **Preferred resolution** | 1280x720 @ 60Hz |
| **All modes** | 1920x1080, 1600x1200, 1680x1050, 1400x1050, 1280x1024, 1440x900, 1280x960, 1280x800, 1280x720, 1024x768, 800x600, 720x576, 720x480, 640x480 |
| **HDMI VSDB** | Yes, IEEE OUI 0x000C03 |
| **Audio (EDID)** | LPCM 2ch, 32/44.1/48/88.2/96/176.4/192kHz, 16/20/24-bit (unusually wide sample rate range) |
| **Speaker allocation** | FL/FR |
| **Outputs** | Composite video (yellow RCA), stereo audio (red/white RCA) |
| **Behavior** | Has its own built-in EDID — always presents "MACROSILICON" 720p regardless of what's connected to its output. Converts HDMI to analog composite (480i) and extracts stereo audio to RCA. |
| **Notes** | Actually works as a crude audio extractor via RCA → 3.5mm. Audio confirmed working through headphones. Composite video output is standard-def only (480i/576i). |

#### HDMI EDID Emulator Passthrough — Details

| Item | Details |
|------|---------|
| **EDID manufacturer** | XMD (Xiaomi) |
| **EDID product code** | 0x009A |
| **EDID monitor name** | Mi TV |
| **Screen size (EDID)** | 60cm × 34cm |
| **Preferred resolution** | 1920x1080 @ 60Hz |
| **Max resolution** | 4096x2160 @ 60Hz (4K) |
| **All modes** | 4096x2160, 3840x2160, 3440x1440, 2560x1600, 2560x1440, 2560x1080, 1920x1200, 1920x1080 (incl 120Hz), 1600x1200, 1680x1050, 1600x900, 1280x1024, 1440x900, 1280x800, 720p, 1024x768, 800x600, 576p, 480p, VGA |
| **HDMI VSDB** | Yes, IEEE OUI 0x000C03, max TMDS clock 300MHz |
| **Audio (EDID)** | 3 SADs: LPCM 2ch (32/44.1/48kHz), AC-3 2ch (32-96kHz, 640kbps max), E-AC-3/DD+ 2ch (32/44.1/48kHz) |
| **Speaker allocation** | FL/FR |
| **Behavior** | Presents its built-in fixed EDID regardless of whether anything is connected downstream. The source always "sees" a 4K-capable Mi TV. |
| **Notes** | Useful as a fallback if the source needs a stable EDID when no display is connected (e.g., headless operation, VNC-only). Less useful for our setup since the OREI splitter in STD mode already provides a stable EDID. |

---

## 3. How HDMI Works

### The Basics

HDMI (High-Definition Multimedia Interface) carries **video**, **audio**, and **control data** over a single cable. Key concepts:

#### TMDS (Transition-Minimized Differential Signaling)
The physical layer. HDMI 1.x/2.0 uses 3 TMDS data channels + 1 clock channel. Each channel is a differential pair (2 wires). The 3 data channels carry pixel data (R, G, B) during the active video period.

#### HDMI vs DVI
HDMI is backwards-compatible with DVI-D (same TMDS signaling for video). The critical difference: **HDMI adds audio and control data in the blanking intervals** between video frames. A connection is treated as "HDMI mode" or "DVI mode" — and this distinction determines whether audio packets are sent.

#### How "HDMI mode" is determined
The source device reads the sink's EDID. If the EDID contains an **HDMI Vendor Specific Data Block (VSDB)** with IEEE OUI `0x000C03`, the source enables HDMI mode (audio + InfoFrames). Without it, the source falls back to DVI mode (video only, no audio).

#### Hot Plug Detect (HPD)
Pin 19 on the HDMI connector. When a sink is connected, it pulls this pin high (via 5V on pin 18 → pull-up resistor in the sink). The source detects the voltage change and initiates EDID reading. **This is why plugging/unplugging causes reconfiguration** — the HPD signal triggers a full re-detection cycle.

#### HDMI Versions (relevant subset)

| Version | Max Resolution | Max Bandwidth | Audio | Notes |
|---------|---------------|---------------|-------|-------|
| 1.4 | 4K@30Hz | 10.2 Gbps | 8ch LPCM, Dolby/DTS | Most common in older gear |
| 2.0 | 4K@60Hz | 18 Gbps | 32ch LPCM | |
| 2.1 | 8K@60Hz | 48 Gbps | eARC | Not relevant for our setup |

Our entire chain (Mini PC, splitter, all sinks) likely operates at HDMI 1.4 or 2.0 levels. 1080p@60Hz requires ~4.5 Gbps, well within any version.

---

### EDID (Extended Display Identification Data)

EDID is a data structure stored in a small EEPROM inside every display. The source reads it over the DDC (Display Data Channel) — which is I2C running on HDMI pins 15 (SCL) and 16 (SDA).

#### EDID Structure

```
Block 0 (128 bytes): Base EDID
  ├── Header (8 bytes): 00 FF FF FF FF FF FF 00
  ├── Manufacturer ID (2 bytes): 3-letter PNP ID
  ├── Product code (2 bytes)
  ├── Serial number (4 bytes)
  ├── Week/Year of manufacture
  ├── EDID version (usually 1.3 or 1.4)
  ├── Basic display parameters (digital/analog, screen size, gamma)
  ├── Chromaticity coordinates
  ├── Established/Standard timings
  ├── Detailed Timing Descriptors (up to 4: resolution, refresh, blanking)
  └── Extension count + checksum

Block 1 (128 bytes): CEA-861 Extension (for HDMI)
  ├── Tag (0x02 = CEA)
  ├── Revision
  ├── DTD offset
  ├── Capabilities (underscan, audio, YCbCr)
  ├── Data Block Collection:
  │   ├── Video Data Block: supported video modes (CEA VICs)
  │   ├── Audio Data Block: SADs (Short Audio Descriptors)
  │   ├── Speaker Allocation Data Block
  │   └── Vendor Specific Data Block (VSDB): HDMI capabilities
  ├── Detailed Timing Descriptors
  └── Checksum
```

#### Why EDID Matters So Much

The source device's **entire configuration** — resolution, refresh rate, color depth, audio format — is determined by what the EDID says. If the EDID is:
- **Missing**: Source may output nothing, or use a safe fallback (640x480)
- **Corrupt**: Unpredictable behavior
- **From the wrong device** (e.g., splitter passes through display A's EDID but you want display B's capabilities): Wrong resolution or missing audio

#### EDID and HDMI Splitters

When a splitter has multiple outputs, it must decide **which downstream EDID to present** to the source. Common strategies:
- **Lowest common denominator**: Parse all connected EDIDs, present one that represents capabilities all sinks support
- **Pass-through of output 1**: Simply copy the EDID from output port 1 and present it upstream
- **Fixed EDID**: Ignore downstream EDIDs entirely, present a built-in "safe" EDID (usually 1080p)
- **Last connected**: Whatever was most recently hot-plugged

This is a **major source of inconsistency** — see [section 7](#7-hdmi-splitters-edid-emulators-and-adapters).

---

## 4. How HDMI Audio Works

### Audio Transport in HDMI

Audio data is sent in **Audio Sample Packets** during the horizontal and vertical blanking intervals of the video signal. This means:
- Audio is interleaved with video at the signal level
- The audio clock is derived from the video clock (via Audio Clock Regeneration packets — N/CTS values)
- **No separate audio connection is needed** — it's all in one cable

### Audio Formats

HDMI supports two categories of audio:

| Type | Examples | How it's sent |
|------|----------|--------------|
| **LPCM** (Linear PCM) | Uncompressed stereo or multichannel | Directly in Audio Sample Packets, up to 8 channels |
| **Compressed** | Dolby Digital (AC3), DTS, Dolby TrueHD, DTS-HD MA | Wrapped in IEC 61937 frames within Audio Sample Packets |

For karaoke, we only need **2-channel LPCM at 48kHz** — the simplest possible case.

### Audio InfoFrame

The source sends an **Audio InfoFrame** packet that tells the sink how to interpret the audio data (channel count, sample rate, bit depth, speaker mapping). This is separate from the audio data itself.

### ELD (EDID-Like Data)

When the Linux kernel reads a sink's EDID and finds audio capabilities, it creates an **ELD** (EDID-Like Data) structure. This is an internal kernel data structure that makes the EDID audio information available to ALSA. The ELD includes:
- Monitor name
- Connection type (HDMI vs DisplayPort)
- Audio capabilities (from SADs in the EDID)

You can read ELD data from `/proc/asound/card0/eld#*` files.

### Why HDMI Audio Fails

Common reasons audio doesn't come out of an HDMI display/receiver:

1. **DVI mode**: No VSDB in EDID → source treats connection as DVI → no audio packets sent
2. **Wrong audio device**: Linux has multiple HDMI PCM devices; audio is going to the wrong one
3. **PipeWire/PulseAudio grabbed the device**: Audio server locked the ALSA device on a different profile
4. **Sink doesn't support the format**: Source sending format the sink can't decode
5. **EDID doesn't advertise audio**: No Audio Data Block in the CEA extension
6. **Splitter strips audio**: Some cheap splitters don't reliably pass audio capabilities in EDID
7. **Cable issue**: Long cables (50ft) can have signal integrity problems; audio is more sensitive than video since it uses the same TMDS channels during blanking

---

## 5. How Linux Routes Video Output

### The Video Stack

```
┌─────────────────────────────────────────────────┐
│ Desktop Environment (XFCE4)                     │
│   xfce4-display-settings / xfconf                │
│   Saves per-monitor profiles (by EDID hash)      │
├─────────────────────────────────────────────────┤
│ X11 / Xorg                                       │
│   xrandr — user-space display configuration      │
│   Manages: outputs, modes, CRTCs, transforms     │
├─────────────────────────────────────────────────┤
│ DRM/KMS (Direct Rendering Manager / Kernel       │
│   Mode Setting)                                  │
│   i915 driver (Intel GPUs)                       │
│   Manages: connectors, encoders, CRTCs           │
│   Reads EDID, enumerates modes                   │
├─────────────────────────────────────────────────┤
│ Hardware                                         │
│   Intel Alder Lake-N UHD Graphics               │
│   HDMI-1, HDMI-2, DP-1, DP-2 connectors        │
│   3 CRTCs (can drive 3 displays simultaneously)  │
└─────────────────────────────────────────────────┘
```

### How a Display Gets Configured (Boot Sequence)

1. **Kernel boot**: i915 driver initializes, scans connectors for HPD
2. **EDID read**: For each connected output, reads EDID via DDC/I2C
3. **Mode enumeration**: Parses EDID, builds list of supported modes
4. **fbcon**: Kernel framebuffer console picks a mode (usually the preferred/native mode from EDID)
5. **Xorg starts**: Uses modesetting driver, reads DRM state
6. **XFCE starts**: `xfce4-display-settings` checks saved profiles
   - If the connected monitor's EDID hash matches a saved profile → applies saved resolution
   - If no match → uses the monitor's preferred mode (first mode with `+` flag in xrandr)
7. **Desktop renders**: Wallpaper, panels, conky, etc. render at the configured resolution

### xrandr

The primary tool for display management:

```bash
# Show current state
xrandr

# Set resolution
xrandr --output HDMI-1 --mode 1920x1080 --rate 60

# Clone display (mirror HDMI-1 to HDMI-2)
xrandr --output HDMI-2 --same-as HDMI-1 --auto

# Extend desktop
xrandr --output HDMI-2 --right-of HDMI-1 --auto

# Force a mode even if display doesn't advertise it
xrandr --output HDMI-1 --mode 1920x1080  # only works if mode exists in EDID

# Add a custom mode (if needed)
xrandr --newmode "1920x1080_60" ...  # gtf/cvt to generate modeline
xrandr --addmode HDMI-1 "1920x1080_60"
```

### XFCE Display Profiles

XFCE saves display configurations in:
`~/.config/xfce4/xfconf/xfce-perchannel-xml/displays.xml`

Each entry is keyed by **output name** (e.g., `HDMI-1`) and an **EDID hash**. When a new display is plugged in, XFCE tries to match it. The current NomadPC has saved profiles for:
- `HDMI-1` / "Integrated Tech Express Inc 7"" (touchscreen) → 1920x1080
- `HDMI-2` / "UGD 13"" → 1280x720
- `DP-1` / "Elo TouchSystems Inc 13"" → 1920x1080

**Problem**: The saved profile for HDMI-1 says 1920x1080, but the touchscreen's *preferred mode* is 1024x600. It's unclear whether XFCE is actually applying the saved profile or whether the preferred mode is winning. Current state shows 1024x600 active.

### CRTCs and Multi-Display

The Intel Alder Lake-N GPU has **3 CRTCs** (display pipelines). Each CRTC can drive one output at one resolution/refresh. With a splitter, only **1 CRTC** is used (the splitter handles duplication in hardware). Without a splitter, you can drive up to 3 independent displays.

---

## 6. How Linux Routes Audio Output

### The Audio Stack (NomadPC)

```
┌─────────────────────────────────────────────────┐
│ Application (VLC)                                │
│   --aout alsa --alsa-audio-device hdmiout        │
│   Talks directly to ALSA, bypasses PipeWire      │
├─────────────────────────────────────────────────┤
│ ALSA User-Space (libasound)                      │
│   Reads /etc/asound.conf                         │
│   "hdmiout" → plug → hw:0,N (auto-detected)     │
├─────────────────────────────────────────────────┤
│ PipeWire 1.0.5 (audio server)                    │  ← NOT used for VLC audio
│   Provides PulseAudio API compatibility          │
│   Manages card profiles (analog vs HDMI)         │
│   Default sink: analog-stereo                    │
├─────────────────────────────────────────────────┤
│ ALSA Kernel (snd_hda_intel)                      │
│   Card 0: HDA Intel PCH                         │
│   Codec 0: Conexant SN6140 (analog headphone/   │
│            speaker/mic — PCM device 0)           │
│   Codec 2: Intel Alderlake-P HDMI (digital —    │
│            PCM devices 3, 7, 8, 9)              │
├─────────────────────────────────────────────────┤
│ Hardware                                         │
│   4 HDMI/DP audio outputs on Intel HDA           │
│   Pin 0x04 → PCM 3 ("HDMI 0")                   │
│   Pin 0x06 → PCM 7 ("HDMI 1")  ← wired, but    │
│   Pin 0x08 → PCM 8 ("HDMI 2")    mapping        │
│   Pin 0x0a → PCM 9 ("HDMI 3")    shuffles!      │
└─────────────────────────────────────────────────┘
```

### The Pin-to-PCM Shuffling Problem

Intel HDA has internal "pins" (codec nodes) that connect to physical HDMI/DP ports. The mapping between **pin node** and **ALSA PCM device number** is not fixed — it can change between boots, kernel versions, or even between plug/unplug cycles.

Currently (with touchscreen on HDMI-1):

| ALSA PCM | HDA Codec Name | Jack Status | ELD Monitor | Connected? |
|----------|---------------|-------------|-------------|------------|
| `hw:0,3` | HDMI 0 | **on** | Z3 (touchscreen) | **Yes — active** |
| `hw:0,7` | HDMI 1 | off | — | No |
| `hw:0,8` | HDMI 2 | off | — | No |
| `hw:0,9` | HDMI 3 | off | — | No |

Previously (different boot, different display): `hw:0,7` was the active device. **This is why `fix-hdmi-audio.sh` exists** — it auto-detects which PCM device has an active jack.

### IEC958 Playback Switch (Digital Audio Enable)

Each HDMI PCM device has an `IEC958 Playback Switch` in the ALSA mixer. This is a **digital audio enable/disable** at the HDA codec level — if it's `off`, the converter won't send audio packets even though the PCM stream appears to be running normally. Everything else looks correct (stream RUNNING, pin enabled, ELD valid) but no audio reaches the sink.

```bash
# Check the switch for all HDMI devices (numid 24, 30, 36, 42 = indices 0-3)
amixer -c 0 cget numid=24   # HDMI 0 (PCM 3)
amixer -c 0 cget numid=30   # HDMI 1 (PCM 7)
amixer -c 0 cget numid=36   # HDMI 2 (PCM 8)
amixer -c 0 cget numid=42   # HDMI 3 (PCM 9)

# Enable it
amixer -c 0 cset numid=24 on
```

**2026-02-23 Root Cause**: HDMI audio was silent because this switch was `off` for HDMI 0 (the active device). All other configuration was correct. Likely toggled during a previous debugging session. Turning it `on` immediately restored audio.

**This switch does NOT persist across reboots** — `fix-hdmi-audio.sh` must ensure it's enabled at startup.

### PipeWire vs Direct ALSA

PipeWire sits between applications and ALSA, providing mixing, routing, and device management. However:

- PipeWire 1.0.5 has known issues routing HDMI audio on this hardware (audio flows through `pw-top` but no sound at the sink)
- VLC is configured to **bypass PipeWire** and talk to ALSA directly via the `hdmiout` device
- PipeWire must stay on the **analog stereo** profile so it doesn't lock the HDMI ALSA device

```bash
# Check PipeWire isn't grabbing HDMI
pactl list cards | grep "Active Profile"
# Expected: output:analog-stereo+input:analog-stereo

# If PipeWire switched to HDMI profile, force it back:
pactl set-card-profile alsa_card.pci-0000_00_1f.3 "output:analog-stereo+input:analog-stereo"
```

### /etc/asound.conf (ALSA Configuration)

Written dynamically by `fix-hdmi-audio.sh` at service start:

```ini
# HDMI audio output — auto-detected by fix-hdmi-audio.sh
# Active device: hw:0,3
pcm.hdmiout {
    type plug
    slave {
        pcm "hw:0,3"
    }
}

ctl.hdmiout {
    type hw
    card 0
}
```

The `plug` plugin handles sample rate and format conversion between what VLC outputs and what the hardware accepts.

### Testing Audio

```bash
# Test the configured hdmiout device
speaker-test -D hdmiout -c 2 -t sine -f 440 -l 1

# Test a specific hardware device directly
speaker-test -D hw:0,3 -c 2 -t sine -f 440 -l 1

# If "Device or resource busy" — VLC filler is holding the device
# Stop filler first, or test a different device
```

---

## 7. HDMI Splitters, EDID Emulators, and Adapters

### How HDMI Splitters Work

A 1-in, 4-out splitter:
1. Receives HDMI signal on input
2. Buffers and re-amplifies the signal
3. Drives 4 copies to the output ports
4. **EDID handling is the critical variable** (see below)

#### Splitter EDID Strategies

The source device needs to read *one* EDID to decide what to output. But there are 4 downstream devices, potentially with different capabilities. The splitter must choose:

| Strategy | How it works | Pros | Cons |
|----------|-------------|------|------|
| **Lowest common denominator** | Parses all connected EDIDs, creates a synthetic EDID with only the capabilities all sinks share | Guarantees all outputs work | May lose audio if one sink doesn't support it; lowest resolution wins |
| **Copy from Output 1** | Passes Output 1's EDID directly to the source | Simple, predictable | Output 1 must be the most capable device; other outputs may not support the selected mode |
| **Built-in fixed EDID** | Ignores downstream EDIDs, presents a standard 1080p EDID | Most predictable | May not match actual sink capabilities; audio caps may be wrong |
| **Configurable** | DIP switches or software to select strategy | Flexible | Requires manual setup |

**For our setup**: The OREI UHDS-104 has a physical STD/TV toggle:
- **STD** (selected): Built-in fixed EDID — 1080p@60Hz, LPCM 2ch. The NomadPC always sees a consistent "HDMI Splitter" monitor regardless of what's on the outputs. **This is what we want.**
- **TV**: Copies EDID from Output 1. Useful if Output 1 has a 4K display and you want 4K passthrough, but makes the source dependent on what's plugged into Output 1.

### EDID Emulator / Passthrough

An EDID emulator sits inline on an HDMI cable and presents a **fixed, known-good EDID** to the source device, regardless of what (if anything) is actually connected downstream. Use cases:

- **Source loses configuration when display is unplugged**: The emulator keeps the source "thinking" a display is always connected
- **Downstream device has bad/corrupt EDID**: The emulator overrides it
- **Splitter EDID behavior is unpredictable**: Put the emulator between the source and splitter to force specific capabilities

**Potential use for our setup**: If the splitter's EDID handling causes problems (wrong resolution, missing audio), put the EDID emulator between the NomadPC and the splitter to force a consistent 1080p + audio EDID.

### HDMI Audio Extractor

Splits the HDMI signal into:
- **HDMI pass-through**: Full signal continues to the next device
- **Audio out**: 3.5mm analog or optical (SPDIF/TOSLINK)

**Potential use for our setup**: If HDMI audio to the Denon AVR proves unreliable, the audio extractor could sit in the chain and provide a separate analog or optical audio feed. This would be a fallback, not ideal.

### Signal Quality and Cable Length

HDMI is a high-bandwidth digital signal. At 1080p@60Hz:
- **Bandwidth**: ~4.5 Gbps
- **Max reliable passive cable length**: ~15-25 feet (depends on cable quality)
- **50 feet**: At the edge of reliability for passive cables. **Active cables** or **HDMI extenders** may be needed for reliable 50ft runs.

**Important for our setup**: The 50ft HDMI cable to the Denon AVR is a risk point. Symptoms of signal degradation: sparkles, blackouts, color artifacts, or complete loss of signal. Audio is carried on the same TMDS channels and can be affected too.

---

## 8. Current NomadPC State

*Captured 2026-02-23, touchscreen on HDMI-1 only.*

### Video

```
Screen 0: current 1024x600
HDMI-1 connected 1024x600+0+0 (152mm x 86mm)
   1024x600      60.00*+        ← current mode, preferred
   1920x1080     60.00 60.00 59.94
   ...
HDMI-2 disconnected
DP-1 disconnected
DP-2 disconnected
```

- Resolution is 1024x600 (touchscreen native/preferred mode)
- XFCE has a saved profile for this EDID hash at 1920x1080, but it's not being applied (possibly because the hash changed, or XFCE fallback behavior)

### Audio

```
Active HDMI jack:     hw:0,3 (PCM 3 = "HDMI 0")
asound.conf:          pcm.hdmiout → hw:0,3  (correctly auto-detected)
VLC filler:           PID 2069, streaming on pcmC0D3p (hw:0,3) — RUNNING
PipeWire profile:     output:analog-stereo+input:analog-stereo (correct)
PipeWire HDMI port:   hdmi-output-0 → "Z3" (available but not active)
```

- VLC filler is streaming audio to `hw:0,3`
- **Audio is NOT audible** from the touchscreen speakers despite theoretically correct routing
- The `speaker-test` on `hdmiout` returned "Device or resource busy" (VLC filler holds it)
- `speaker-test` on `hw:0,7` succeeded (ran for 3 seconds) but that device is disconnected, so no sound

### EDID Details (Touchscreen "Z3")

```
Manufacturer:    DZX (Integrated Tech Express Inc)
Product code:    0x0700
Monitor name:    Z3
Screen size:     15cm × 9cm (6.9" diagonal)
EDID version:    1.3
CEA extension:   Yes (CEA-861-B/C/D)

CEA Data Blocks:
  Video: VICs 16(1080p), 4(720p), 5(1080i), 7, 3, 14
  Audio: LPCM 2ch, 32/44.1/48kHz, 16/20/24-bit  (SAD: 09 07 07)
  Speaker: FL/FR
  VSDB: IEEE 0x000C03 (HDMI), max TMDS clock 80MHz

Kernel ELD for this connection:
  monitor_present:  1
  eld_valid:        1
  monitor_name:     Z3
  connection_type:  HDMI
  codec_pin_nid:    0x6
  codec_cvt_nid:    0x3  (converter node → PCM device 3)
```

### Service Configuration

```bash
# kj-controller.service
ExecStartPre=/opt/nomad/kjbox/kj-controller/fix-hdmi-audio.sh
ExecStart=python /opt/nomad/kjbox/kj-controller/app.py

# VLC karaoke instance
vlc --aout alsa --alsa-audio-device hdmiout --fullscreen

# VLC filler instance
vlc --aout alsa --alsa-audio-device hdmiout /opt/nomad/FillerMusic/wii.mp3 --loop
```

### Wallpaper/Desktop Issue

The desktop background (`nomad-kjbox-desktop-background-4k.jpg`) is a 4K image. XFCE's backdrop is configured with `image-style: 5` (zoomed fill). At 1024x600, the 4K image is scaled to fill the screen. However, user reports seeing "only the top-left quarter" of the desktop — this may indicate:
- The wallpaper is rendering at a previous resolution (1920x1080) but the display is now at 1024x600
- XFCE's xfdesktop didn't re-render after the resolution change from a cable reconnect
- Conky overlay is also running and may be sized for a different resolution

---

## 9. Testing Log

### Test Matrix Template

For each device connected to the NomadPC, fill in:

| Test | Device | Port | Result | Notes |
|------|--------|------|--------|-------|
| Video output detected? | | | | |
| Resolution used? | | | | |
| xrandr output name? | | | | |
| EDID monitor name? | | | | |
| EDID has VSDB (HDMI mode)? | | | | |
| EDID has Audio Data Block? | | | | |
| Which ALSA PCM device? | | | | |
| Audio audible? | | | | |
| speaker-test passes? | | | | |
| VLC filler audible? | | | | |

### Test: 7" Touchscreen Direct → HDMI-1 (2026-02-23)

| Test | Result | Notes |
|------|--------|-------|
| Video output detected? | Yes | HDMI-1 connected |
| Resolution used? | 1024x600 | Touchscreen preferred mode; XFCE saved profile (1080p) not applied |
| xrandr output name? | HDMI-1 | |
| EDID monitor name? | Z3 | DZX manufacturer |
| EDID has VSDB? | Yes | IEEE OUI 0x000C03 ✓ |
| EDID has Audio Data Block? | Yes | LPCM 2ch, 32/44.1/48kHz |
| Which ALSA PCM device? | hw:0,3 | Detected by fix-hdmi-audio.sh via jack status |
| Audio audible? | **No → Yes** | Fixed: `IEC958 Playback Switch` (numid=24) was `off` |
| speaker-test passes? | **Yes** (after fix) | 440Hz sine tone audible from touchscreen speakers |
| VLC filler audible? | **Yes** (after fix) | Filler music playing through touchscreen speakers |

#### Audio Fix (2026-02-23)

**Root cause**: `IEC958 Playback Switch` (numid=24, index 0) was set to `off`. This is the digital audio enable at the HDA codec level for HDMI 0 (PCM device 3). With this switch off, the PCM stream runs normally (state: RUNNING, data flowing) but no audio packets are sent over HDMI. All other configuration was correct.

**Fix**: `amixer -c 0 cset numid=24 on`

**How it broke**: Likely toggled during a previous debugging session. This switch does not persist across reboots — it needs to be set at startup.

### Test: OREI Splitter (STD) → Touchscreen on Out 1 (2026-02-23)

**Setup**: NomadPC HDMI-1 → OREI UHDS-104 input (EDID=STD) → Out 1 → 7" touchscreen. No other outputs connected.

| Test | Result | Notes |
|------|--------|-------|
| Video output detected? | Yes | HDMI-1 connected |
| Resolution used? | **1920x1080** | Splitter's built-in EDID preferred mode (not 1024x600 like direct) |
| xrandr output name? | HDMI-1 | |
| EDID monitor name? | **HDMI Splitter** | Splitter's own EDID, not the touchscreen's |
| EDID manufacturer | HDC, product 0x0100, screen 51cm×29cm | |
| EDID has VSDB? | Yes | IEEE OUI 0x000C03, max TMDS 80MHz ✓ |
| EDID has Audio Data Block? | Yes | LPCM 2ch, 32/44.1/48kHz, 16/20/24-bit |
| EDID video modes | 1080p (60/50/59.94), 1920x1200, 1600x1200, 1280x800, 720p, 1024x768, 800x600, 576p, 480p, VGA | |
| Which ALSA PCM device? | hw:0,3 | Same as direct — pin mapping unchanged by splitter |
| ELD monitor name? | "HDMI Splitter" | |
| Audio audible? | **Yes** | Filler music playing through touchscreen speakers |
| Desktop rendering? | **Correct** | Full desktop visible on touchscreen (1080p downscaled by touchscreen internally) |

**Key observations:**
1. The splitter in STD mode completely replaces the downstream EDID. The NomadPC sees "HDMI Splitter" not "Z3".
2. Resolution jumped from 1024x600 (touchscreen native) to 1920x1080 (splitter EDID preferred). The touchscreen downscales internally — full desktop is visible and correct.
3. ALSA PCM device stayed at hw:0,3 — the splitter didn't cause a pin reassignment.
4. Audio works through the splitter → touchscreen chain.
5. The desktop rendering issue (only top-left quarter visible) from the direct connection is gone — likely the touchscreen handles 1080p input better than its native 1024x600 mode from the PC's perspective.

**Also confirmed**: Moving the touchscreen from splitter Out 4 to Out 1 changed nothing — the splitter in STD mode is output-port-agnostic.

### Test: OREI Splitter (STD) → Touchscreen (Out 1) + Wireless HDMI (Out 4) (2026-02-23)

**Setup**: NomadPC HDMI-1 → OREI UHDS-104 (EDID=STD) → Out 1: 7" touchscreen, Out 4: Wireless HDMI TX → RX → 55" home TV

| Test | Result | Notes |
|------|--------|-------|
| NomadPC EDID changed? | **No** | Still "HDMI Splitter", 1920x1080 |
| NomadPC resolution changed? | **No** | Still 1920x1080@60Hz |
| ALSA PCM device changed? | **No** | Still hw:0,3 |
| Touchscreen video? | Yes | Same as before |
| Touchscreen audio? | Yes | Same as before |
| Wireless HDMI → TV video? | **Yes** | Full 1080p picture on 55" TV |
| Wireless HDMI → TV audio? | **Yes** | Audio playing through TV speakers |

**Key observation**: Adding a second output device to the splitter had **zero impact** on the NomadPC's configuration. The STD mode EDID is fixed regardless of what's downstream. Both outputs receive identical 1080p video + stereo audio.

### Test: Wireless HDMI TX Direct → HDMI-1 (2026-02-23)

**Setup**: NomadPC HDMI-1 → Wireless HDMI TX → [air] → Wireless HDMI RX → Hisense 55" TV

| Test | Result | Notes |
|------|--------|-------|
| Video output detected? | Yes | HDMI-1 connected |
| Resolution used? | 1920x1080 @ 60Hz | TV's preferred mode |
| EDID monitor name? | **HISENSE** | Wireless TX passes through TV's EDID |
| EDID manufacturer | HEC, product 0x81C8, screen 62cm×34cm | |
| EDID has VSDB? | Yes | IEEE OUI 0x000C03, max TMDS 320MHz |
| EDID has Audio Data Block? | Yes | LPCM 2ch, 32/44.1/48kHz |
| Which ALSA PCM device? | hw:0,3 | Unchanged |
| Video on TV? | **Yes** | Full 1080p |
| Audio on TV? | **Yes** | Playing through TV speakers |

**Key observation**: The wireless HDMI TX/RX pair is transparent — it passes through the downstream display's EDID to the source. The NomadPC sees "HISENSE" not the transmitter. This means if you swap the RX to a different display, the NomadPC would see that display's EDID. Behind the OREI splitter in STD mode, this doesn't matter.

### Test: EDID Emulator Direct → HDMI-1, Nothing Downstream (2026-02-23)

**Setup**: NomadPC HDMI-1 → EDID Emulator (output open/nothing connected)

| Test | Result | Notes |
|------|--------|-------|
| Video output detected? | Yes | HDMI-1 connected — emulator provides HPD even with no display |
| Resolution used? | 1920x1080 @ 60Hz | Emulator's built-in preferred mode |
| EDID monitor name? | **Mi TV** | XMD (Xiaomi) — built into the emulator |
| EDID has VSDB? | Yes | HDMI mode, max TMDS 300MHz |
| EDID has Audio Data Block? | Yes | LPCM 2ch + AC-3 + E-AC-3 |
| Which ALSA PCM device? | hw:0,3 | Unchanged |
| Available modes | Up to 4096x2160@60Hz | Very permissive EDID |

**Key observation**: The EDID emulator always presents the same "Mi TV" EDID regardless of downstream state. It could be useful for headless operation, but the OREI splitter in STD mode fills this role better for our setup (1080p-focused EDID with audio).

### Test: OREI HDS-102 (Auto) Direct → HDMI-1, No Outputs (2026-02-24)

**Setup**: NomadPC HDMI-1 → HDS-102 input (EDID=Auto) → no outputs connected

| Test | Result | Notes |
|------|--------|-------|
| Video output detected? | **No** | HDMI-1 shows disconnected |
| EDID? | **None** | 0 bytes — splitter presents nothing without a downstream display |
| HDMI jack status | All off | No audio device detected |

### Test: OREI HDS-102 (Auto) → Touchscreen on Out 1 (2026-02-24)

**Setup**: NomadPC HDMI-1 → HDS-102 input (EDID=Auto) → Out 1: 7" touchscreen

| Test | Result | Notes |
|------|--------|-------|
| Video output detected? | Yes | HDMI-1 connected |
| Resolution used? | **1024x600** | Touchscreen's native preferred mode — splitter passes EDID through |
| EDID monitor name? | **Z3** | Touchscreen EDID, not splitter's |
| ALSA PCM device? | hw:0,3 | |
| Audio audible? | Yes | Filler music from touchscreen speakers |
| Desktop rendering? | **Top-left quarter only** | Same issue as touchscreen direct — 1024x600 output |

**Also tested Copy mode** (switch flipped, nothing on Out 2): identical results — falls back to Out 1 EDID.

### Test: OREI HDS-102 (Auto) → Wireless HDMI TX (Out 1) + Touchscreen (Out 2) (2026-02-24)

**Setup**: NomadPC HDMI-1 → HDS-102 (EDID=Auto) → Out 1: Wireless HDMI TX → Hisense TV, Out 2: 7" touchscreen

| Test | Result | Notes |
|------|--------|-------|
| Resolution used? | **1024x600** | Lowest common denominator — touchscreen's native mode wins |
| EDID monitor name? | **HDMI Splitter** | Synthetic EDID from the splitter chip (ITE manufacturer) |
| EDID screen size? | 15cm × 9cm | Took the touchscreen's (smaller) size |
| EDID video modes? | Only VIC 16 (1080p) + 1024x600 preferred | Stripped to common modes |
| EDID audio? | LPCM 2ch | Common to both displays |
| EDID VSDB? | Yes, max TMDS 320MHz | Took the higher value (Hisense) |
| ALSA PCM device? | hw:0,3 | |

**Key finding**: Auto mode creates a **lowest-common-denominator synthetic EDID**. With the touchscreen in the mix, 1024x600 becomes the preferred mode, dragging the entire output down. This makes the HDS-102 in Auto mode **unsuitable** for our mixed-display setup.

**Comparison with UHDS-104**: The HDS-102 in Auto mode is limited by the least capable display. The UHDS-104 in STD mode always presents a fixed 1080p EDID regardless of downstream devices — clearly the better choice for our setup.

### Test: avedio links Splitter Direct → HDMI-1, No Outputs (2026-02-24)

**Setup**: NomadPC HDMI-1 → avedio links input → no outputs

| Test | Result | Notes |
|------|--------|-------|
| Video output detected? | **No** | Disconnected, no EDID |

### Test: avedio links Splitter → Touchscreen (2026-02-24)

**Setup**: NomadPC HDMI-1 → avedio links → Out: 7" touchscreen

| Test | Result | Notes |
|------|--------|-------|
| Resolution used? | 1024x600 | Pure pass-through of touchscreen EDID |
| EDID monitor name? | **Z3** | Touchscreen EDID, product 0x0700 (vs 0x0000 from HDS-102) |
| Audio audible? | Yes | |
| Desktop rendering? | Top-left quarter only | Same issue as touchscreen direct |

### Test: avedio links Splitter → Touchscreen + Wireless HDMI TX (2026-02-24)

**Setup**: NomadPC HDMI-1 → avedio links → Out 1: Wireless HDMI TX → Hisense TV, Out 2: 7" touchscreen

| Test | Result | Notes |
|------|--------|-------|
| Resolution used? | **1024x600** | Still touchscreen EDID — no merging or comparison |
| EDID monitor name? | **Z3** | Identical to single-output test — second display ignored |
| Both displays showing? | Yes | Both at 1024x600 — TV shows top-left quarter only |
| Audio? | Yes | Both playing audio |

**Key observation**: Despite having no EDID switch, this splitter does perform lowest-common-denominator selection — the wireless HDMI TX (Hisense EDID, 1080p preferred) was connected first, but the touchscreen's 1024x600 EDID still won when it was plugged in second. However, unlike the HDS-102 (which creates a synthetic "HDMI Splitter" EDID), this one presents the lower-capability display's raw EDID directly (manufacturer DZX, name "Z3").

### Test: HDMI → Composite/RCA Adapter Direct → HDMI-1 (2026-02-24)

**Setup**: NomadPC HDMI-1 → HDMI-to-Composite adapter → RCA → 3.5mm → headphones

| Test | Result | Notes |
|------|--------|-------|
| Video output detected? | Yes | HDMI-1 connected |
| Resolution used? | **1280x720** | Adapter's built-in EDID preferred mode |
| EDID monitor name? | **MACROSILICON** | Built-in EDID from the adapter's chip |
| EDID has VSDB? | Yes | HDMI mode |
| EDID has Audio Data Block? | Yes | LPCM 2ch, up to 192kHz |
| ALSA PCM device? | hw:0,3 | |
| Audio audible? | **Yes** | Filler music through headphones via RCA-to-3.5mm |

**Key observation**: This adapter has its own built-in EDID and works as a basic audio extractor in a pinch. Could be useful behind the OREI splitter as an emergency audio output if HDMI audio to the Denon AVR fails at the venue.

### Test: HDMI Audio Extractor Direct → HDMI-1, HDMI Out Disconnected (2026-02-24)

**Setup**: NomadPC HDMI-1 → Audio Extractor (HDMI out disconnected) → 3.5mm → headphones

| Test | Result | Notes |
|------|--------|-------|
| Video output detected? | Yes | HDMI-1 connected — built-in EDID provides HPD |
| Resolution used? | **1920x1080** | Extractor's built-in EDID preferred mode |
| EDID monitor name? | **HDMI SPLITTER** | HDC manufacturer, same chip family as UHDS-104 |
| EDID has VSDB? | Yes | HDMI mode |
| EDID has Audio Data Block? | Yes | LPCM 2ch, 32/44.1/48kHz |
| ALSA PCM device? | hw:0,3 | |
| Audio via 3.5mm? | **Yes** | Filler music confirmed through headphones |

**Key observation**: Works as a standalone audio extractor even with no HDMI output connected. Built-in 1080p EDID means the NomadPC always outputs at full resolution. This is the best fallback device for venue audio — sits inline, passes video through, and provides a separate audio feed if HDMI audio to the Denon AVR fails.

### Test: Full Chain at Venue (TODO — 2026-02-24)

---

## 10. Known Issues and Open Questions

### Active Issues

1. **No audio from touchscreen** (2026-02-23)
   - VLC streams to correct PCM device, device shows RUNNING, but no sound
   - Need to rule out: touchscreen speaker wiring, ALSA format mismatch, broken config from previous session

2. **Desktop shows only top-left quarter** (2026-02-23)
   - Happened after HDMI cable reconnect
   - Wallpaper is 4K, display is 1024x600
   - XFCE's xfdesktop may not have re-rendered
   - May self-resolve on reboot, or may need `xfdesktop --reload` or similar

3. **Resolution not matching XFCE saved profile**
   - XFCE displays.xml has HDMI-1 saved at 1920x1080
   - But current resolution is 1024x600 (touchscreen preferred)
   - EDID hash in saved profile may not match current touchscreen

### Concerns for Full Setup

4. **Splitter EDID behavior unknown**
   - Which EDID does the splitter present to the NomadPC?
   - Does it change based on which outputs are connected? In what order?
   - Will it include audio capabilities?

5. **50ft HDMI cable signal integrity**
   - At the edge of reliable passive HDMI range for 1080p
   - Need to test: clean picture? audio? any dropouts?

6. **Pin-to-PCM shuffling with splitter**
   - Currently `hw:0,3` with touchscreen direct
   - May change when splitter is in the chain (different EDID → different pin assignment)
   - `fix-hdmi-audio.sh` should handle this, but need to verify

7. **What happens when devices are connected/disconnected from splitter?**
   - HPD signal behavior through splitter is unknown
   - Does disconnecting one output from the splitter trigger an HPD event at the source?
   - Or does the splitter maintain HPD as long as at least one output is connected?

### Open Questions

- Does the touchscreen's HDMI audio actually work with Linux, or only with macOS/Windows?
- Does the EDID emulator need to be used with the splitter?
- Can we force the NomadPC to always output 1920x1080 regardless of what's connected? (Kernel parameter `video=HDMI-A-1:1920x1080@60e` or similar)
- Should we create a custom EDID (like we did on the NomadPi) to guarantee consistent behavior?

---

## Appendix A: Useful Commands

```bash
# === VIDEO ===
# Current display state
DISPLAY=:0 xrandr

# Verbose (includes EDID hex dump)
DISPLAY=:0 xrandr --verbose

# Force resolution
DISPLAY=:0 xrandr --output HDMI-1 --mode 1920x1080

# DRM connector status (kernel level)
cat /sys/class/drm/card1-HDMI-A-1/status
cat /sys/class/drm/card1-HDMI-A-1/modes

# Read raw EDID
cat /sys/class/drm/card1-HDMI-A-1/edid | xxd | head -20

# === AUDIO ===
# List playback devices
aplay -l

# Check HDMI jack status
for dev in 3 7 8 9; do
  echo -n "PCM $dev: "
  amixer -c 0 contents | grep -A2 "HDMI/DP,pcm=$dev Jack" | grep "values="
done

# Check ELD (EDID-Like Data for audio)
cat /proc/asound/card0/eld#2.4   # (number varies)

# Test audio output
speaker-test -D hdmiout -c 2 -t sine -f 440 -l 1

# Check what's using a PCM device
fuser /dev/snd/pcmC0D3p

# PipeWire card profile
pactl list cards | grep "Active Profile"

# Force PipeWire to analog (keep off HDMI)
pactl set-card-profile alsa_card.pci-0000_00_1f.3 "output:analog-stereo+input:analog-stereo"

# === SERVICE ===
systemctl status kj-controller
journalctl -u kj-controller -f
systemctl restart kj-controller
```

## Appendix B: File Locations

| File | Purpose |
|------|---------|
| `/etc/asound.conf` | ALSA config — written by fix-hdmi-audio.sh |
| `/opt/nomad/kjbox/kj-controller/fix-hdmi-audio.sh` | Auto-detect HDMI audio device at boot |
| `/opt/nomad/kjbox/kj-controller/config.json` | KJ Controller config (audio_devices, default_audio_device) |
| `/etc/systemd/system/kj-controller.service` | Systemd service unit |
| `~/.config/xfce4/xfconf/xfce-perchannel-xml/displays.xml` | XFCE saved display profiles |
| `/sys/class/drm/card1-HDMI-A-*/` | DRM connector sysfs entries |
| `/proc/asound/card0/codec#2` | HDA HDMI codec topology |
| `/proc/asound/card0/eld#*` | ELD data for audio capabilities |
