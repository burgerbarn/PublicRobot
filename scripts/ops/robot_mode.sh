#!/bin/bash
# Tier 1. Robot display mode (owner 2026-10-07). Usage: robot_mode.sh light|face
#   light: audio/voice only, no screen - the face service off and disabled (survives reboot), console back on tty1
#   face:  Buddy's face on the robot's touchscreen (rosorin-face, enabled at boot)
# Audio, voice, mind, navigation are the same in both modes.
M=${1:?light|face}; R=${ROBOT:-rosorin-wifi}
case "$M" in
  light) ssh -o ConnectTimeout=8 $R 'sudo systemctl disable --now rosorin-face >/dev/null 2>&1; sudo chvt 1; echo 1 | sudo tee /sys/class/graphics/fbcon/cursor_blink >/dev/null' ;;
  face)  ssh -o ConnectTimeout=8 $R 'sudo systemctl enable --now rosorin-face >/dev/null 2>&1' ;;
  *) echo "usage: $0 light|face"; exit 2 ;;
esac
ssh -o ConnectTimeout=8 $R 'echo "mode: face service $(systemctl is-active rosorin-face) / $(systemctl is-enabled rosorin-face) | voice $(systemctl is-active rosorin-api) | mind $(systemctl is-active rosorin-mind)"'
