#!/bin/bash
# Run ON THE ROBOT. Back to the apt build of nav2_behaviors (removes the overlay made by install_nav2_behaviors_fix.sh).
rm -rf $HOME/ros2_ws/src/nav2_behaviors $HOME/ros2_ws/build/nav2_behaviors $HOME/ros2_ws/install/nav2_behaviors
source /opt/ros/humble/setup.bash; source $HOME/ros2_ws/install/setup.bash
echo "nav2_behaviors now from: $(ros2 pkg prefix nav2_behaviors)"
