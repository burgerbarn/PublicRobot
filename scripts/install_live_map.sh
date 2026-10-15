#!/bin/bash
# The kept map the robot continues on every drive (nav.launch.py slam:=true, default): ~/maps/room.posegraph + .data.
# First time: from the 2026-09-28 pose graph (the map every run has used; the frame of the keepout mask and the home
# pose). Afterwards nav_supervisor.keep_map rewrites it after drives, previous copies in ~/maps/history/.
set -e
M=/home/burgerbarn/maps
[ -f $M/room.posegraph ] && { echo "kept graph exists: $(ls -la $M/room.posegraph | awk '{print $6,$7,$8}')"; exit 0; }
cp -v $M/room_20260928.posegraph $M/room.posegraph
cp -v $M/room_20260928.data $M/room.data
echo "kept graph installed from room_20260928"
