#!/bin/bash
# NOT APPLIED (2026-10-06). Candidate for the "navigation servers deaf to AMCL's transform" fault (docs/status.md
# 2026-10-06): keep the robot's DDS on the loopback interface only. The robot talks to bigbuddy over HTTP, never DDS
# (docs/decisions.md), so nothing needs DDS on WiFi; the WiFi driver resets its firmware ~2x/h (hardware.md) and the
# Fast DDS host id is built from the IPv4 addresses (the 10-05 split). With ROS_LOCALHOST_ONLY=1 both are out of the
# picture. Reversible: remove the drop-ins and the .bashrc line, daemon-reload, restart the services.
# Run on the robot as burgerbarn. Verify afterwards: all services active, /nav/status ready, mind mode, selfcheck E2.
set -e
for u in rosorin-api rosorin-base rosorin-camera rosorin-contact rosorin-explore rosorin-mind rosorin-nav \
         rosorin-owner-faces rosorin-practice rosorin-reappear rosorin-selfcheck rosorin-task@ rosorin-vslam; do
  sudo mkdir -p /etc/systemd/system/$u.service.d
  printf '[Service]\nEnvironment=ROS_LOCALHOST_ONLY=1\n' | sudo tee /etc/systemd/system/$u.service.d/20-localhost-only.conf >/dev/null
done
grep -q ROS_LOCALHOST_ONLY ~/.bashrc || echo 'export ROS_LOCALHOST_ONLY=1   # robot DDS stays on loopback (scripts/install_dds_localhost_only.sh)' >> ~/.bashrc
sudo systemctl daemon-reload
echo "drop-ins written. Restart in this order and read back each: rosorin-base, rosorin-camera, rosorin-vslam, rosorin-nav, rosorin-contact, rosorin-api, rosorin-mind"
