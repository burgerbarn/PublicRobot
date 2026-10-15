#!/bin/bash
# Run inside lightbot-sim as burgerbarn after the chapters (verify_run.sh does that): compare what the guide built
# with the repo's final code and run the repo's own tests against the files the guide had you write.
R=/repo
W=~/ros2_ws/src/rosorin_base
cmpdir() {   # cmpdir <robot dir> <repo dir>: files only in one side, files that differ
  local a=$1 b=$2
  [ -d "$a" ] || { echo "  (no $a on the test robot)"; return; }
  (cd $b && find . -type f ! -path '*__pycache__*' ! -path '*/.pytest_cache/*' | sort) > /tmp/b.lst
  (cd $a && find . -type f ! -path '*__pycache__*' ! -path '*/.pytest_cache/*' | sort) > /tmp/a.lst
  echo "  only in repo:      $(comm -23 /tmp/b.lst /tmp/a.lst | tr '\n' ' ')"
  echo "  only on the robot: $(comm -13 /tmp/b.lst /tmp/a.lst | tr '\n' ' ')"
  echo -n "  differ:            "
  for f in $(comm -12 /tmp/b.lst /tmp/a.lst); do cmp -s $a/$f $b/$f || echo -n "$f "; done; echo
}
echo "### rosorin_base: guide-built ~/ros2_ws/src/rosorin_base vs repo ros2/rosorin_base"; cmpdir $W $R/ros2/rosorin_base
for f in launch/base.launch.py systemd/rosorin-base.service package.xml rosorin_base/lidar_reader.py; do
  cmp -s $W/$f $R/ros2/rosorin_base/$f || { echo "--- diff $f (robot -> repo)"; diff $W/$f $R/ros2/rosorin_base/$f | head -30; }
done
for pair in "vision vision" "selfcare selfcare" "behavior behavior" "buddy_link buddy_link"; do
  set -- $pair; echo "### ~/$1 vs repo $2/"; cmpdir ~/$1 $R/$2
done
echo; echo "### repo's own tests (copied out of the repo) against the workspace modules"
rm -rf /tmp/rt && mkdir -p /tmp/rt && cp -r $R/ros2/rosorin_base/test /tmp/rt/ && cd /tmp/rt
PYTHONPATH=$W python3 -c "import rosorin_base.rrc_protocol as m; print('imports from', m.__file__)"
PYTHONPATH=$W python3 -m pytest -q -p no:cacheprovider test 2>&1 | tail -2
source /opt/ros/humble/setup.bash; [ -f ~/ext_ws/install/setup.bash ] && source ~/ext_ws/install/setup.bash
source ~/ros2_ws/install/setup.bash
echo "### same tests against the colcon install space"; python3 -m pytest -q -p no:cacheprovider test 2>&1 | tail -1
echo; echo "### every Python file the guide put on the robot compiles"
n=0; bad=0
for f in $(find ~/learn ~/vision ~/selfcare ~/behavior ~/buddy_link ~/setup $W/rosorin_base $W/launch -name '*.py' 2>/dev/null); do
  n=$((n+1)); python3 -c "import ast,sys; ast.parse(open(sys.argv[1]).read())" $f 2>/dev/null || { bad=$((bad+1)); echo "  SYNTAX: $f"; }
done; echo "  $n files, $bad with syntax errors"
echo; echo "### ROS-level checks without hardware"
ros2 pkg prefix rosorin_base; ros2 pkg prefix nav2_behaviors; ros2 pkg prefix rf2o_laser_odometry; ros2 pkg prefix explore_lite; ros2 pkg prefix rosorin_moveit
python3 -c "import rosorin_base.board_driver, rosorin_base.lidar_reader, rosorin_base.stop_motors, rosorin_base.rf2o_fix, rosorin_base.nav_supervisor; print('driver, lidar_reader, stop_motors, rf2o_fix, nav_supervisor import OK')"
python3 - <<'PY'
import os, importlib.util
from ament_index_python.packages import get_package_share_directory
for name in ('base.launch.py', 'slam.launch.py'):
    p = os.path.join(get_package_share_directory('rosorin_base'), 'launch', name)
    spec = importlib.util.spec_from_file_location('l', p); m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
    ld = m.generate_launch_description()
    ex = [getattr(e, '_Node__node_executable', None) or type(e).__name__ for e in ld.entities]
    print(f'installed {name}: {len(ex)} entities: {ex}')
PY
echo; echo "### ch17 mask.py on the repo's map (claim: the repo's mask image byte for byte, allowed cells 6093)"
sudo -u mac bash -c 'cd ~/rosorin-maps 2>/dev/null && [ -f mask.py ] && cp ~/CCode/rosorin-pro/maps/room_20260928.pgm ~/CCode/rosorin-pro/maps/room_20260928.yaml . && python3 mask.py && cmp room_20260928_keepout.pgm ~/CCode/rosorin-pro/maps/room_20260928_keepout.pgm && echo "mask image identical to the repo" ; diff room_20260928_keepout.yaml ~/CCode/rosorin-pro/maps/room_20260928_keepout.yaml'
