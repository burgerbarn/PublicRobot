#!/bin/bash
# Tier 1. Put the reSpeaker XVF3800's levels where the record says (hardware.md 2026-10-06), on whichever machine it is
# plugged into. Usage: audio_levels.sh robot|bigbuddy [mic_gain=135] [leds: 0 off|1 breath|2 rainbow|3 colour|4 doa|5 ring, default 2 (owner 2026-10-07)]
H=${1:-robot}; G=${2:-135}; L=${3:-2}
[ "$H" = robot ] && T=rosorin-wifi && TOOL='sudo ~/tools/reSpeaker_XVF3800_USB_4MIC_ARRAY/host_control/jetson/xvf_host' || { T=bigbuddy; TOOL='~/tools/reSpeaker_XVF3800_USB_4MIC_ARRAY/host_control/linux_x86_64/xvf_host'; }
ssh -o ConnectTimeout=8 $T 'bash -s' <<EOS
C=\$(grep -i Array /proc/asound/cards | head -1 | awk '{print \$1}')
[ -z "\$C" ] && { echo "no array on $H"; exit 1; }
amixer -c \$C cset numid=5 60,60 >/dev/null; amixer -c \$C cset numid=6 60 >/dev/null; sudo -n alsactl store 2>/dev/null
$TOOL AUDIO_MGR_MIC_GAIN $G >/dev/null 2>&1; $TOOL LED_EFFECT $L >/dev/null 2>&1
P=~/tools/xvfenv/bin/python; cd ~/tools/reSpeaker_XVF3800_USB_4MIC_ARRAY/python_control 2>/dev/null && { [ "$H" = robot ] && S=sudo || S=; \$S \$P xvf_host.py AIC3104_HP_LEVEL --values 9 >/dev/null 2>&1; \$S \$P xvf_host.py AIC3104_LINEOUT_LEVEL --values 9 >/dev/null 2>&1; \$S \$P xvf_host.py SAVE_CONFIGURATION --values 1 >/dev/null 2>&1; }
echo "array on $H: fw \$($TOOL VERSION 2>/dev/null | tail -1) | alsa \$(amixer -c \$C cget numid=5 | grep -o 'values=[0-9,]*' | tail -1) \$(amixer -c \$C cget numid=6 | grep -o 'values=[0-9,]*' | tail -1) | mic gain \$($TOOL AUDIO_MGR_MIC_GAIN 2>/dev/null | tail -1) | LED \$($TOOL LED_EFFECT 2>/dev/null | tail -1) | usb speed \$(cat /sys/bus/usb/devices/*/speed 2>/dev/null | sort -u | paste -sd,) (12 = re-plug it)"
EOS
