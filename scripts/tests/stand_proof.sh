#!/bin/bash
# Stop proof on the STAND (wheels in the air, unplugged), with the robot mind paused so it cannot start a wheel skill
# of its own in the middle. Part 1 with the contact reflex stopped (it would e-stop every spin on the stand), part 2
# = the contact reflex itself. Log: ~/setup/logs/stop_proof_stand_<time>.log. Leaves contact + mind running.
source /opt/ros/humble/setup.bash
source /home/burgerbarn/ext_ws/install/setup.bash
source /home/burgerbarn/ros2_ws/install/setup.bash
systemctl is-active --quiet rosorin-nav && { echo "navigation is running - not now"; exit 2; }
mkdir -p ~/setup/logs; LOG=~/setup/logs/stop_proof_stand_$(date +%Y%m%d_%H%M%S).log
python3 - "$@" 2>&1 <<'PY' | tee $LOG
import subprocess, sys
sys.path.insert(0, '/home/burgerbarn/vision')
from mind_pause import mind_paused
paths = sys.argv[1] if len(sys.argv) > 1 else 'deadman,estop,disable,scanloss,kill,service'
with mind_paused(reason='stand stop proof'):
    subprocess.run('sudo systemctl stop rosorin-contact', shell=True)
    try:
        subprocess.run(['python3', '/home/burgerbarn/setup/stop_proof.py', 'prove', paths])
    finally:
        subprocess.run('sudo systemctl start rosorin-contact', shell=True)
    if len(sys.argv) <= 1:
        import time; time.sleep(8)
        subprocess.run(['python3', '/home/burgerbarn/setup/stop_proof.py', 'prove', 'contact'])
PY
echo "log: $LOG"
