#!/bin/bash
# Run ON THE ROBOT. nav2_behaviors 1.1.20 (the installed apt version) rebuilt from the upstream tag with ONE upstream
# fix: "Fix AssistedTeleop lateral projection sign" (ros-navigation/navigation2 #6535, on main 2026-09-17, tagged for
# backport to Lyrical only - no Humble release has it). Without it AssistedTeleop checks the side the robot strafes
# AWAY from. Measured on this robot 2026-10-02 (scripts/tests/nav2_isolated_checks.sh teleop):
#   wall on the left,  strafe left 0.2 m/s -> 0.20 out (not slowed)   | wall on the right, strafe left -> 0.14 (slowed)
# Result: ~/ros2_ws/install/nav2_behaviors overlays /opt/ros/humble for everything that sources ~/ros2_ws
# (rosorin-nav, explore_run.sh). behavior/practice.py then practises sideways again by itself.
# Check afterwards: bash ~/setup/tests/nav2_isolated_checks.sh teleop  (left wall must slow a left strafe).
# Rollback: scripts/rollback_nav2_behaviors_fix.sh (back to the apt build).
set -e
WANT=1.1.20
V=$(dpkg-query -W -f='${Version}' ros-humble-nav2-behaviors | cut -d- -f1)
[ "$V" = "$WANT" ] || { echo "installed nav2_behaviors is $V; this fix was made for $WANT - check upstream first"; exit 1; }
SRC=$HOME/ros2_ws/src/nav2_behaviors
TMP=$(mktemp -d)
git clone -q --depth 1 --branch $WANT --filter=blob:none --sparse https://github.com/ros-navigation/navigation2.git $TMP/nav2
git -C $TMP/nav2 sparse-checkout set nav2_behaviors
rm -rf $SRC && cp -r $TMP/nav2/nav2_behaviors $SRC && rm -rf $TMP
python3 - $SRC/plugins/assisted_teleop.cpp <<'PY'
import sys
p = sys.argv[1]; s = open(p).read()
old = """  projected_pose.x += projection_time * (
    twist.linear.x * cos(pose.theta) +
    twist.linear.y * sin(pose.theta));

  projected_pose.y += projection_time * (
    twist.linear.x * sin(pose.theta) -
    twist.linear.y * cos(pose.theta));
"""
new = """  // rosorin: upstream fix #6535 (lateral projection sign), not backported to Humble
  projected_pose.x += projection_time * (
    twist.linear.x * cos(pose.theta) -
    twist.linear.y * sin(pose.theta));

  projected_pose.y += projection_time * (
    twist.linear.x * sin(pose.theta) +
    twist.linear.y * cos(pose.theta));
"""
assert s.count(old) == 1, 'assisted_teleop.cpp does not contain the expected projectPose lines'
open(p, 'w').write(s.replace(old, new))
print('patched', p)
PY
source /opt/ros/humble/setup.bash
cd $HOME/ros2_ws
colcon build --packages-select nav2_behaviors --cmake-args -DCMAKE_BUILD_TYPE=Release -DBUILD_TESTING=OFF 2>&1 | tail -5
source install/setup.bash
echo "nav2_behaviors now from: $(ros2 pkg prefix nav2_behaviors)"
