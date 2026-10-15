#!/usr/bin/env bash
# Simulated rebuild, runs INSIDE lbsim-robot as root (from ~/lbsim/bin/hw_up.sh): (re)start the emulated hardware.
# A file, not `bash -c '<script>'`: a pkill -f for the emulator paths would match an inline script's own command line.
pkill -f '[/]opt/lbsim/fake_' || true
for i in $(seq 1 50); do pgrep -f '[/]opt/lbsim/fake_' >/dev/null || break; sleep 0.2; done
nohup python3 /opt/lbsim/fake_board.py --link /dev/ttyACM0 --state /run/fake_board.json --twist-udp 172.30.0.20:5602 >/run/fake_board.log 2>&1 </dev/null &
nohup python3 /opt/lbsim/fake_lidar.py --link /dev/lidar --listen 0.0.0.0:5601 >/run/fake_lidar.log 2>&1 </dev/null &
for i in $(seq 1 50); do [ -e /dev/ttyACM0 ] && [ -e /dev/lidar ] && [ "$(readlink /dev/lidar)" != "$(readlink /dev/ttyACM0)" ] && grep -q '^fake board' /run/fake_board.log 2>/dev/null && grep -q '^fake lidar' /run/fake_lidar.log 2>/dev/null && break; sleep 0.2; done
ls -l /dev/ttyACM0 /dev/lidar; head -1 /run/fake_board.log /run/fake_lidar.log
pgrep -fc '[/]opt/lbsim/fake_' | sed 's/^/emulators running: /'
