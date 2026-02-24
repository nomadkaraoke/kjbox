#!/bin/bash
# fix-hdmi-audio.sh — Detect active HDMI audio device and configure ALSA
#
# Intel HDA shuffles HDMI pin-to-PCM-device assignments between boots.
# This script finds which PCM device (3, 7, 8, or 9) has a connected
# monitor and writes /etc/asound.conf to point 'hdmiout' at it.
#
# Run as: ExecStartPre in kj-controller.service (before VLC launches)

set -e

HDMI_DEVICES="3 7 8 9"
ACTIVE_DEV=""

# Find the HDMI device with a connected jack
for dev in $HDMI_DEVICES; do
    jack_state=$(amixer -c 0 contents 2>/dev/null | grep -A2 "HDMI/DP,pcm=$dev Jack" | grep -o 'values=on' || true)
    if [ "$jack_state" = "values=on" ]; then
        ACTIVE_DEV="$dev"
        break
    fi
done

if [ -z "$ACTIVE_DEV" ]; then
    echo "fix-hdmi-audio: WARNING — no active HDMI jack found, defaulting to hw:0,3"
    ACTIVE_DEV="3"
fi

echo "fix-hdmi-audio: Active HDMI audio device is hw:0,$ACTIVE_DEV"

# Write /etc/asound.conf
cat > /etc/asound.conf << EOF
# HDMI audio output — auto-detected by fix-hdmi-audio.sh
# Active device: hw:0,$ACTIVE_DEV
pcm.hdmiout {
    type plug
    slave {
        pcm "hw:0,$ACTIVE_DEV"
    }
}

ctl.hdmiout {
    type hw
    card 0
}
EOF

echo "fix-hdmi-audio: Updated /etc/asound.conf with hw:0,$ACTIVE_DEV"

# Enable IEC958 Playback Switch for all HDMI devices.
# This is the digital audio enable at the HDA codec level — if off, the PCM
# stream runs but no audio packets are sent over HDMI. We enable all four
# rather than just the active one, since numids can shift between boots.
for idx in 0 1 2 3; do
    amixer -c 0 cset iface=MIXER,name='IEC958 Playback Switch',index=$idx on 2>/dev/null || \
        echo "fix-hdmi-audio: WARNING — could not enable IEC958 index $idx"
done
echo "fix-hdmi-audio: Enabled IEC958 Playback Switch for all HDMI devices"
