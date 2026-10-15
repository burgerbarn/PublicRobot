#!/bin/bash
# Tier 1. The robot and bigbuddy, read back in one screen. Run from the Mac. Changes nothing.
R=${ROBOT:-rosorin-wifi}; B=${BIGBUDDY:-bigbuddy}
ssh -o ConnectTimeout=8 $R 'source /opt/ros/humble/setup.bash 2>/dev/null
echo "== robot $(hostname) $(uptime -p) load $(cut -d" " -f1-3 /proc/loadavg)"
echo "services: $(systemctl is-active rosorin-base rosorin-nav rosorin-mind rosorin-camera rosorin-vslam rosorin-api rosorin-contact | tr "\n" " ")"
echo "tasks:    explore $(systemctl is-active rosorin-explore) home $(systemctl is-active rosorin-task@home) come $(systemctl is-active rosorin-task@come)"
echo "driver:   $(timeout 5 ros2 topic echo --once /rosorin_board_driver/state --field data 2>/dev/null | grep -o "\"plugged\": [a-z]*\|\"enabled\": [a-z]*\|\"estop\": [a-z]*\|\"arm_enabled\": [a-z]*\|\"servo_silent\": \[[0-9, ]*\]" | tr "\n" " ")"
echo "nav:      $(timeout 5 ros2 topic echo --once /nav/status --field data 2>/dev/null | head -1 | cut -c1-150)"
echo "mind:     $(timeout 5 ros2 topic echo --once /rosorin_mind/mode --field data 2>/dev/null | head -1 | cut -c1-120)"
echo "devices:  array usb $(lsusb | grep -c Seeed) card $(grep -c Array /proc/asound/cards) | camera $(lsusb | grep -c Aurora) | lidar $(ls /dev/lidar 2>/dev/null | wc -l) | screen $(cat /sys/class/drm/card1-DP-1/status 2>/dev/null)"
C=$(grep -i Array /proc/asound/cards | head -1 | awk "{print \$1}")
[ -n "$C" ] && echo "array:    alsa $(amixer -c $C cget numid=5 2>/dev/null | grep -o "values=[0-9,]*" | tail -1) $(amixer -c $C cget numid=6 2>/dev/null | grep -o "values=[0-9,]*" | tail -1) | fw $(sudo ~/tools/reSpeaker_XVF3800_USB_4MIC_ARRAY/host_control/jetson/xvf_host VERSION 2>/dev/null | tail -1) | mic gain $(sudo ~/tools/reSpeaker_XVF3800_USB_4MIC_ARRAY/host_control/jetson/xvf_host AUDIO_MGR_MIC_GAIN 2>/dev/null | tail -1) | usb speed $(cat /sys/bus/usb/devices/1-*/speed 2>/dev/null | paste -sd, )"
echo "voice:    api gain stage $(grep -o "ROBOT_SPEECH_GAIN_DB., .[0-9]*" ~/buddy_link/robot_api.py | grep -o "[0-9]*$") dB"'
ssh -o ConnectTimeout=8 $B 'bash -s' <<'B'
echo "== bigbuddy: buddy-voice $(systemctl --user is-active buddy-voice) | robot-mic $(systemctl --user is-active robot-mic) | pipewire $(systemctl --user is-active pipewire pipewire-pulse wireplumber | tr '\n' ' ')"
echo "ears:     $(pactl list short sources 2>/dev/null | grep -i 'robot_mic\|xvf_asr\|buddy_aec_source' | awk -F'\t' '{printf "%s(%s) ", $2, $5}')"
echo "mouth:    $(grep -h BUDDY_MOUTH ~/.config/systemd/user/buddy-voice.service.d/*.conf 2>/dev/null | cut -d= -f3 || echo robot) | wake th $(grep -h BUDDY_WAKE_TH ~/.config/systemd/user/buddy-voice.service.d/*.conf 2>/dev/null | cut -d= -f3)"
journalctl --user -u buddy-voice --since '-10 min' --no-pager -o cat 2>/dev/null | grep -v -i token | grep -i 'WAKE\|heard\|reply' | tail -3 | cut -c1-110
B
