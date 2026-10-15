#!/bin/bash
# Tier 1. Bring the robot home the safe way: stop its own wheel skills, pause the mind, one home goal, mind back on.
R=${ROBOT:-rosorin-wifi}
ssh -o ConnectTimeout=8 $R 'source /opt/ros/humble/setup.bash; sudo systemctl stop rosorin-explore rosorin-task@come rosorin-task@home 2>/dev/null; sleep 2
timeout 10 ros2 service call /rosorin_mind/enable std_srvs/srv/SetBool "{data: false}" >/dev/null 2>&1; sleep 3
timeout 200 bash ~/setup/go_home.sh 150 2>&1 | grep -i "SUCCEEDED\|ABORTED\|TIMEOUT\|not ready\|not now\|amcl pose" | tail -2
timeout 10 ros2 service call /rosorin_mind/enable std_srvs/srv/SetBool "{data: true}" >/dev/null 2>&1; sleep 2
echo "wheels: $(timeout 5 ros2 topic echo --once /rosorin_board_driver/state --field data 2>/dev/null | grep -o "\"enabled\": [a-z]*") | mind: $(timeout 5 ros2 topic echo --once /rosorin_mind/mode --field data 2>/dev/null | grep -o "\"enabled\": [a-z]*")"'
