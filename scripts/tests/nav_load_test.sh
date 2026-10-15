#!/bin/bash
# CPU / control-loop test of the running navigation stack with the WHEELS DISABLED (nothing here enables them: the
# driver ignores every command). Sends a goal 1.5 m ahead again and again for DURATION s so that planner and controller
# run for real, then reports per-process CPU, how many times the controller missed its 20 Hz loop, DWB failures.
# Needs navigation up (sudo systemctl start rosorin-nav). Usage: nav_load_test.sh [duration_s] [label]
DURATION=${1:-40}; LABEL=${2:-test}
source /opt/ros/humble/setup.bash; source /home/burgerbarn/ros2_ws/install/setup.bash
systemctl is-active --quiet rosorin-nav || { echo "navigation is not running"; exit 2; }
S=$(timeout 5 ros2 topic echo --once --field data /rosorin_board_driver/state 2>/dev/null | grep -o '"enabled": [a-z]*'); [ "$S" = '"enabled": false' ] || { echo "wheels are enabled ($S) - not now"; exit 2; }
L0=$(journalctl -u rosorin-nav --no-pager -o cat | wc -l)
python3 - $DURATION <<'PY' &
import sys, time, math, rclpy
from rclpy.action import ActionClient
from nav2_msgs.action import NavigateToPose
from tf2_ros import Buffer, TransformListener
from rclpy.time import Time
rclpy.init(); n = rclpy.create_node('nav_load_goal'); buf = Buffer(); TransformListener(buf, n)
ac = ActionClient(n, NavigateToPose, 'navigate_to_pose'); ac.wait_for_server(timeout_sec=10)
end = time.time() + float(sys.argv[1]) + 5
for _ in range(30): rclpy.spin_once(n, timeout_sec=0.1)
while time.time() < end:
    try:
        t = buf.lookup_transform('map', 'base_link', Time()).transform
        yaw = math.atan2(2 * (t.rotation.w * t.rotation.z), 1 - 2 * t.rotation.z ** 2)
        g = NavigateToPose.Goal(); g.pose.header.frame_id = 'map'
        g.pose.pose.position.x = t.translation.x + 1.5 * math.cos(yaw); g.pose.pose.position.y = t.translation.y + 1.5 * math.sin(yaw)
        g.pose.pose.orientation = t.rotation
        f = ac.send_goal_async(g); rclpy.spin_until_future_complete(n, f, timeout_sec=5)
        gh = f.result(); t0 = time.time()
        while gh and gh.accepted and time.time() - t0 < 12 and time.time() < end:
            rclpy.spin_once(n, timeout_sec=0.2)
        if gh and gh.accepted:
            c = gh.cancel_goal_async(); rclpy.spin_until_future_complete(n, c, timeout_sec=3)
    except Exception as e:
        for _ in range(10): rclpy.spin_once(n, timeout_sec=0.05)      # the TF buffer fills only while the node spins
PY
GP=$!
sleep 8
echo "--- $LABEL: per-process CPU % of one core, averaged over $((DURATION-8)) s"
top -b -d 4 -n $(( (DURATION-8)/4 + 1 )) -w 200 | awk '/^top -/{n++} n>=2 && $1 ~ /^[0-9]+$/ {c[$12" "$13]+=$9; m++} END{for(x in c) printf "%7.0f %s\n", c[x]/(n-1), x}' | sort -rn | head -9
wait $GP
journalctl -u rosorin-nav --no-pager -o cat | tail -n +$((L0+1)) | sed 's/\x1b\[[0-9;]*m//g' > /tmp/nav_load_$LABEL.log
echo "--- control loop missed: $(grep -c 'Control loop missed' /tmp/nav_load_$LABEL.log)   no valid trajectories: $(grep -c 'No valid trajectories' /tmp/nav_load_$LABEL.log)   transform too old: $(grep -c 'Transform data too old' /tmp/nav_load_$LABEL.log)   goals aborted: $(grep -c 'Aborting handle' /tmp/nav_load_$LABEL.log)"
grep 'Control loop missed' /tmp/nav_load_$LABEL.log | grep -o 'Current loop rate is [0-9.]* Hz' | sed 's/Current loop rate is //' | sort -n | awk '{a[NR]=$1} END{if(NR) printf "controller loop rate when late: min %s median %s max %s\n", a[1], a[int((NR+1)/2)], a[NR]}'
echo "load: $(uptime | cut -c30-80)"
