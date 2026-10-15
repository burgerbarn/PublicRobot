#!/bin/bash
# Tier 2. Where Buddy hears and speaks. Usage: buddy_ears.sh robot|bigbuddy  (status.md 2026-10-06 evening)
# robot:    array on the robot; bigbuddy pulls its stream (robot-mic.service), Buddy's voice goes to the robot (/play)
# bigbuddy: array on bigbuddy; local xvf_asr, Buddy's voice on the Evo
set -e; M=${1:?robot|bigbuddy}
ssh -o ConnectTimeout=8 bigbuddy 'bash -s' <<EOS
set -e; cd ~/.config/pipewire/pipewire.conf.d
if [ "$M" = robot ]; then cp 60-buddy-echo-cancel.conf.robotmic-20261006 60-buddy-echo-cancel.conf; rm -f ~/.config/systemd/user/buddy-voice.service.d/30-mouth.conf; systemctl --user enable robot-mic.service >/dev/null 2>&1
else cp 60-buddy-echo-cancel.conf.bak-20261006-robotmic 60-buddy-echo-cancel.conf; mkdir -p ~/.config/systemd/user/buddy-voice.service.d; printf '[Service]\nEnvironment=BUDDY_MOUTH=evo\n' > ~/.config/systemd/user/buddy-voice.service.d/30-mouth.conf; systemctl --user disable --now robot-mic.service >/dev/null 2>&1 || true; fi
systemctl --user daemon-reload; systemctl --user restart pipewire pipewire-pulse wireplumber; sleep 4
[ "$M" = robot ] && systemctl --user restart robot-mic.service; systemctl --user restart buddy-voice.service; sleep 9
echo "ears on $M: \$(pactl list short sources | grep -i 'robot_mic\|xvf_asr\|buddy_aec_source' | awk -F'\t' '{printf "%s(%s) ", \$2, \$5}')"
journalctl --user -u buddy-voice --since '-20 s' --no-pager -o cat | grep -v -i token | grep ready | tail -1 | cut -c1-110
EOS
