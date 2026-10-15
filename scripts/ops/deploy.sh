#!/bin/bash
# Tier 2. Deploy one part from this repo to the robot and read it back. Usage: deploy.sh mind|api|driver|nav|explore|selfcare
# - mind:    vision/mind.py (+ world.py etc.)        -> ~/vision, restart rosorin-mind (arm released and re-taken, ~100 s)
# - api:     buddy_link/robot_api.py                 -> ~/buddy_link, SIGKILL+restart rosorin-api (an open /audio stream hangs a plain stop)
# - driver:  ros2/rosorin_base/rosorin_base/*.py + config -> colcon build, restart rosorin-base THEN rosorin-mind (the mind hangs when the driver restarts under it)
# - nav:     ros2 package (supervisor, nav.launch, nav2.yaml) -> colcon build, restart rosorin-nav, wait for ready
# - explore: behavior/*.py -> ~/behavior   - selfcare: selfcare/*.py -> ~/selfcare
set -e; R=${ROBOT:-rosorin-wifi}; cd "$(dirname "$0")/../.."
ssh $R 'mkdir -p ~/retired/$(date +%Y%m%d)'
case "$1" in
  mind)    scp -q vision/mind.py vision/world.py vision/reappear.py vision/look_learner.py $R:vision/; ssh $R 'cd ~/vision && python3 -m py_compile mind.py && sudo systemctl restart rosorin-mind' ;;
  api)     scp -q buddy_link/robot_api.py $R:buddy_link/; ssh $R 'python3 -m py_compile ~/buddy_link/robot_api.py && sudo systemctl kill -s SIGKILL rosorin-api 2>/dev/null; sudo systemctl restart rosorin-api' ;;
  driver)  scp -q ros2/rosorin_base/rosorin_base/*.py $R:ros2_ws/src/rosorin_base/rosorin_base/; scp -q ros2/rosorin_base/config/*.yaml $R:ros2_ws/src/rosorin_base/config/
           ssh $R 'source /opt/ros/humble/setup.bash && cd ~/ros2_ws && colcon build --packages-select rosorin_base 2>&1 | tail -1 && sudo systemctl stop rosorin-mind && sudo systemctl restart rosorin-base && sleep 10 && sudo systemctl start rosorin-mind' ;;
  nav)     scp -q ros2/rosorin_base/rosorin_base/*.py $R:ros2_ws/src/rosorin_base/rosorin_base/; scp -q ros2/rosorin_base/launch/*.py $R:ros2_ws/src/rosorin_base/launch/; scp -q ros2/rosorin_base/config/*.yaml $R:ros2_ws/src/rosorin_base/config/
           ssh $R 'source /opt/ros/humble/setup.bash && cd ~/ros2_ws && colcon build --packages-select rosorin_base 2>&1 | tail -1 && sudo systemctl restart rosorin-nav' ;;
  explore) scp -q behavior/*.py $R:behavior/ ;;
  selfcare) scp -q selfcare/*.py $R:selfcare/ ;;
  *) echo "usage: $0 mind|api|driver|nav|explore|selfcare"; exit 2 ;;
esac
echo "deployed $1; reading back in 20 s"; sleep 20; bash "$(dirname "$0")/readback.sh"
