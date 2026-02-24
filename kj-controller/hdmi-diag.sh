#!/bin/bash
# hdmi-diag.sh — Quick HDMI video + audio diagnostic for NomadPC
#
# Run from anywhere: ssh nomadpc '/opt/nomad/kjbox/kj-controller/hdmi-diag.sh'
# Or locally:        ./hdmi-diag.sh

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
NC='\033[0m'

pass() { echo -e "  ${GREEN}✓ $1${NC}"; }
fail() { echo -e "  ${RED}✗ $1${NC}"; ERRORS=$((ERRORS + 1)); }
warn() { echo -e "  ${YELLOW}! $1${NC}"; }

ERRORS=0

echo "=== HDMI Diagnostics ==="
echo ""

# --- VIDEO ---
echo "VIDEO:"

connector_status=$(cat /sys/class/drm/card1-HDMI-A-1/status 2>/dev/null)
if [ "$connector_status" = "connected" ]; then
    pass "HDMI-1 connected"
else
    fail "HDMI-1 not connected (status: ${connector_status:-unknown})"
fi

resolution=$(DISPLAY=:0 xrandr 2>/dev/null | grep "^HDMI-1 connected" | grep -oP '\d+x\d+\+')
resolution=${resolution%+}
if [ "$resolution" = "1920x1080" ]; then
    pass "Resolution: 1920x1080"
elif [ -n "$resolution" ]; then
    warn "Resolution: $resolution (expected 1920x1080)"
else
    fail "Could not read resolution"
fi

monitor=$(python3 -c "
edid = open('/sys/class/drm/card1-HDMI-A-1/edid', 'rb').read()
for b in [54, 72, 90, 108]:
    if edid[b]==0 and edid[b+1]==0 and edid[b+3]==0xfc:
        print(edid[b+5:b+18].decode('ascii', errors='replace').strip())
        break
" 2>/dev/null)
if [ -n "$monitor" ]; then
    pass "EDID monitor: $monitor"
else
    fail "Could not read EDID"
fi

echo ""

# --- AUDIO ---
echo "AUDIO:"

# Find active HDMI jack
active_pcm=""
for dev in 3 7 8 9; do
    jack=$(amixer -c 0 contents 2>/dev/null | grep -A2 "HDMI/DP,pcm=$dev Jack" | grep -o 'values=on' || true)
    if [ "$jack" = "values=on" ]; then
        active_pcm="$dev"
        break
    fi
done

if [ -n "$active_pcm" ]; then
    pass "Active HDMI jack: PCM $active_pcm (hw:0,$active_pcm)"
else
    fail "No active HDMI jack found"
fi

# Check asound.conf matches
asound_dev=$(grep -oP 'pcm "hw:0,\K[0-9]+' /etc/asound.conf 2>/dev/null)
if [ "$asound_dev" = "$active_pcm" ]; then
    pass "asound.conf: hw:0,$asound_dev (matches active jack)"
elif [ -n "$asound_dev" ]; then
    fail "asound.conf: hw:0,$asound_dev (active jack is PCM $active_pcm — MISMATCH)"
else
    fail "Could not read asound.conf"
fi

# Check IEC958 switch for active device
if [ -n "$active_pcm" ]; then
    # Map PCM device to IEC958 index: 3→0, 7→1, 8→2, 9→3
    case "$active_pcm" in
        3) iec_idx=0 ;; 7) iec_idx=1 ;; 8) iec_idx=2 ;; 9) iec_idx=3 ;; *) iec_idx="" ;;
    esac
    if [ -n "$iec_idx" ]; then
        iec_val=$(amixer -c 0 cget iface=MIXER,name='IEC958 Playback Switch',index=$iec_idx 2>/dev/null | grep "values=" | grep -o "on\|off")
        if [ "$iec_val" = "on" ]; then
            pass "IEC958 Playback Switch (index $iec_idx): on"
        else
            fail "IEC958 Playback Switch (index $iec_idx): OFF — audio is muted!"
            echo -e "       ${YELLOW}Fix: amixer -c 0 cset iface=MIXER,name='IEC958 Playback Switch',index=$iec_idx on${NC}"
        fi
    fi
fi

# Check PipeWire profile
pw_profile=$(sudo -u nomad XDG_RUNTIME_DIR=/run/user/1000 pactl list cards 2>/dev/null | grep "Active Profile:" | head -1 | sed 's/.*: //')
if echo "$pw_profile" | grep -q "analog-stereo"; then
    pass "PipeWire profile: $pw_profile"
elif [ -n "$pw_profile" ]; then
    fail "PipeWire profile: $pw_profile (should be analog-stereo!)"
    echo -e "       ${YELLOW}Fix: pactl set-card-profile alsa_card.pci-0000_00_1f.3 output:analog-stereo+input:analog-stereo${NC}"
else
    warn "Could not read PipeWire profile"
fi

# Check VLC processes
filler_pid=$(pgrep -f "vlc.*8081.*filler" || true)
karaoke_pid=$(pgrep -f "vlc.*8080.*karaoke" || true)

if [ -n "$filler_pid" ]; then
    # Check if filler is actually playing
    filler_state=$(curl -s -u :filler "http://localhost:8081/requests/status.xml" 2>/dev/null | grep -oP '<state>\K[^<]+')
    pass "VLC filler: PID $filler_pid ($filler_state)"
else
    warn "VLC filler: not running"
fi

if [ -n "$karaoke_pid" ]; then
    pass "VLC karaoke: PID $karaoke_pid"
else
    pass "VLC karaoke: idle (normal)"
fi

# Check if audio device is actually being used
pcm_status=$(cat /proc/asound/card0/pcm${active_pcm}p/sub0/status 2>/dev/null | head -1)
if echo "$pcm_status" | grep -q "RUNNING\|DRAINING"; then
    pass "PCM $active_pcm: streaming audio"
elif echo "$pcm_status" | grep -q "PREPARED"; then
    warn "PCM $active_pcm: prepared but not streaming"
else
    warn "PCM $active_pcm: $pcm_status"
fi

echo ""

# --- ELD (audio capabilities from display) ---
echo "DISPLAY AUDIO CAPS:"
eld_file=""
for f in /proc/asound/card0/eld#2.*; do
    present=$(grep "monitor_present" "$f" 2>/dev/null | awk '{print $2}')
    if [ "$present" = "1" ]; then
        eld_file="$f"
        break
    fi
done

if [ -n "$eld_file" ]; then
    eld_name=$(grep "monitor_name" "$eld_file" | awk '{print $2}')
    eld_type=$(grep "connection_type" "$eld_file" | awk '{print $2}')
    eld_audio=$(grep "sad0_coding_type" "$eld_file" | sed 's/.*\] //')
    eld_ch=$(grep "sad0_channels" "$eld_file" | awk '{print $2}')
    pass "ELD: $eld_name ($eld_type) — $eld_audio ${eld_ch}ch"
else
    fail "No valid ELD found — display may not support HDMI audio"
fi

echo ""

# --- SUMMARY ---
if [ $ERRORS -eq 0 ]; then
    echo -e "${GREEN}All checks passed.${NC}"
else
    echo -e "${RED}$ERRORS issue(s) found.${NC}"
fi

exit $ERRORS
