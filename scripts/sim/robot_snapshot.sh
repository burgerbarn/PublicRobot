#!/usr/bin/env bash
# Simulated rebuild: snapshot the robot container's state as an image (after each chapter). Usage: robot_snapshot.sh <tag>
# The commit pauses the container (without the pause it fails when a file grows during the copy: "/var/log/syslog:
# archive/tar: write too long"). The pause froze rf2o for good once ("Waiting for laser_scans" 10/s) until the base
# restarted, so the robot's own services are restarted after every snapshot.
set -euo pipefail
docker commit lbsim-robot "lbsim-robot:$1" >/dev/null && docker image inspect "lbsim-robot:$1" --format '{{.Id}}' | cut -c1-19
for u in rosorin-base rosorin-nav rosorin-contact; do
  if [ "$(docker exec lbsim-robot systemctl is-active $u 2>/dev/null)" = active ]; then docker exec lbsim-robot systemctl restart $u; fi
done
