#!/bin/bash
# Drive the robot to its saved home pose (~/maps/home_pose.json) on the kept map. Navigation is always on
# (rosorin-nav.service); this only waits until it says ready (placed on the map, servers active) and sends one goal.
# Wheels are disabled on every exit path (nav_goal.py). Usage: go_home.sh [goal timeout s, default 120]
source /opt/ros/humble/setup.bash
source /home/burgerbarn/ros2_ws/install/setup.bash
for u in rosorin-explore rosorin-practice "rosorin-task@home" "rosorin-task@come"; do
  systemctl is-active --quiet "$u" && { echo "$u is running (the robot's own skill) - not now"; exit 2; }
done
systemctl is-active --quiet rosorin-nav || sudo systemctl start rosorin-nav
ros2 run rosorin_base nav_wait ${WAIT:-240} || { echo "navigation is not ready - not driving"; exit 1; }
read X Y YAW < <(python3 -c "import json,math;h=json.load(open('/home/burgerbarn/maps/home_pose.json'));print(h['x'],h['y'],math.degrees(h['yaw']))")
echo "home pose on the kept map: $X $Y $YAW deg"
python3 /home/burgerbarn/setup/nav_goal.py $X $Y $YAW ${1:-120} | tee /tmp/go_home_last.log
# home and not on the charger: say so (selfcare/say.py queue, Buddy speaks it; owner 2026-10-05 "have Buddy ask to be plugged in")
grep -q "SUCCEEDED" /tmp/go_home_last.log && python3 - <<'PY'
import json, os, sys
sys.path.insert(0, os.path.expanduser('~/selfcare')); from say import enqueue
try: plugged = json.load(open(os.path.expanduser('~/battery/plugged.json'))).get('plugged')
except Exception: plugged = None
if not plugged:
    enqueue('plug_me', 'I am back home. Please plug me in when you can.', 'normal', repeat_s=1800)
PY
