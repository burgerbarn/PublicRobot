#!/bin/bash
# Does the robot place itself on a KEPT map from where it stands (NVIDIA's Occupancy Grid Localizer -> AMCL), and how well
# does the live scan fit that map? WHEELS STAY DISABLED (nothing here enables them or sends a goal).
# Usage: kept_map_localize_check.sh [/home/burgerbarn/maps/room_20260928.yaml]   (picks up <map>_keepout.yaml if present)
source /opt/ros/humble/setup.bash; source /home/burgerbarn/ros2_ws/install/setup.bash
MAP=${1:-/home/burgerbarn/maps/room_live.yaml}
systemctl is-active --quiet rosorin-nav && { echo "navigation already running"; exit 2; }
S=$(timeout 5 ros2 topic echo --once --field data /rosorin_board_driver/state 2>/dev/null | grep -o '"enabled": [a-z]*'); [ "$S" = '"enabled": false' ] || { echo "wheels enabled - not now"; exit 2; }
finish() { bash /home/burgerbarn/setup/nav_stop.sh >/dev/null 2>&1; pkill -INT -f occupancy_grid_localizer_container; }
trap finish EXIT
ros2 launch rosorin_base nav.launch.py map:=$MAP > /tmp/localize_check_nav.log 2>&1 &
for i in $(seq 1 60); do ros2 action list 2>/dev/null | grep -q '^/navigate_to_pose$' && break; sleep 1; done
sleep 8
echo "keepout mask in use: $(ls ${MAP%.yaml}_keepout.yaml 2>/dev/null || echo none)"
for TRY in 1 2 3; do
  timeout 40 ros2 service call /trigger_grid_search_localization std_srvs/srv/Empty "{}" 2>&1 | tail -2 | cut -c1-160
  sleep 4
  P=$(timeout 4 ros2 run tf2_ros tf2_echo map base_link 2>/dev/null | grep -m1 -A1 Translation | tr '\n' ' ')
  echo "try $TRY: $P"
  [ -n "$P" ] && break
done
cd ~/vision && . venv/bin/activate && python /home/burgerbarn/setup/scan_overlay.py 2>&1 | tail -2
grep -ciE "error" /tmp/localize_check_nav.log | sed 's/^/error lines in nav log: /'
