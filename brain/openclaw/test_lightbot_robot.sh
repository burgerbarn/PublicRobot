#!/bin/bash
# Tests of lightbot_robot.py (chapter 35) against test/fake_robot_api.py and test/fake_ssh. Nothing reaches the robot.
#   brain/openclaw/test_lightbot_robot.sh                       "robot" commands run in a local bash
#   LIGHTBOT_TEST_BOX=<container> brain/openclaw/test_lightbot_robot.sh   ... in a running brain/sim test robot
set -u
H=$(cd "$(dirname "$0")" && pwd); W=$(mktemp -d); trap 'kill $(jobs -p) 2>/dev/null; rm -rf "$W"' EXIT
cd "$W" && export HOME="$W/home" && mkdir -p home/.config/rosorin && printf tok123 > home/.config/rosorin/api_token && chmod 600 home/.config/rosorin/api_token
python3 "$H/test/fake_robot_api.py" 9960 tok123 & sleep 1
export LIGHTBOT_ROBOT_API=http://127.0.0.1:9960 LIGHTBOT_ROBOT_SSH="$H/test/fake_ssh" LIGHTBOT_BRAIN_SSH="$H/test/fake_ssh"
L="python3 $H/lightbot_robot.py"; P=0; F=0
ok() { if eval "$2"; then echo "PASS  $1"; P=$((P+1)); else echo "FAIL  $1"; echo "      $o" | head -3; F=$((F+1)); fi; }
o=$($L status 2>&1); rc=$?; ok "status: GET with the token" '[[ $rc == 0 && $o == *battery_v* && $o != *tok123* ]]'
o=$($L frame $W/f.jpg 2>&1); rc=$?; ok "frame: saved" '[[ $rc == 0 && -s $W/f.jpg ]]'
o=$($L stop 2>&1); rc=$?; ok "stop: POST /stop allowed" '[[ $rc == 0 ]] && grep -q "POST /stop 200" api.log'
for m in come home sethome look play enable arm; do o=$($L $m 2>&1); rc=$?; ok "$m: refused, nothing sent" '[[ $rc == 2 && $o == REFUSED* ]] && ! grep -q "/task/$m\|/look\|/play" api.log'; done
for c in "sudo systemctl stop rosorin-base" "ros2 topic pub /cmd_vel geometry_msgs/msg/Twist {}" "rm -rf ~/maps" "sgdisk -c 1:APP /dev/nvme0n1" "reboot" "python3 ~/setup/motion_test.py spin" "ros2 service call /rosorin_board_driver/enable std_srvs/srv/SetBool '{data: true}'" "echo x > /tmp/f"; do
  o=$($L run "$c" 2>&1); rc=$?; ok "run refused: $c" '[[ $rc == 2 && $o == REFUSED* ]] && ! grep -qF "$c" ssh.log 2>/dev/null'; done
o=$($L run "uname -s" 2>&1); rc=$?; ok "run allowed: uname -s" '[[ $rc == 0 && $o == *Linux* ]]'
o=$($L run "systemctl is-active rosorin-base" 2>&1); rc=$?; ok "run allowed: systemctl is-active (output passed through)" '[[ -n $o ]] && grep -q "is-active rosorin-base" ssh.log'
if [ -n "${LIGHTBOT_TEST_BOX:-}" ]; then
  o=$($L run "timeout 6 ros2 topic hz /scan" 2>&1); ok "run on the test robot: /scan rate" '[[ $o == *"average rate"* ]]'
  o=$($L run "timeout 6 ros2 run tf2_ros tf2_echo base_link laser" 2>&1); ok "run on the test robot: tf2_echo" '[[ $o == *"0.048, 0.000, 0.107"* ]]'
fi
rm home/.config/rosorin/api_token; o=$($L status 2>&1); rc=$?; ok "no token file: clear message" '[[ $rc == 1 && $o == "no robot token"* ]]'
printf wrong > home/.config/rosorin/api_token; o=$($L status 2>&1); rc=$?; ok "wrong token: HTTP 401 shown" '[[ $rc == 1 && $o == *"HTTP 401"* ]]'
o=$(LIGHTBOT_ROBOT_API=http://127.0.0.1:9 $L status 2>&1); rc=$?; ok "API down: clear message" '[[ $rc == 1 && $o == *"not reachable"* ]]'
o=$($L dance 2>&1); rc=$?; ok "unknown command refused" '[[ $rc == 2 ]]'
echo "$P passed, $F failed"; [ $F = 0 ]
