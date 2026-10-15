#!/bin/bash
# Isaac ROS 3.2 (JetPack 6.2 / Humble) from NVIDIA's apt repo: nvblox (GPU 3D map) + nvblox_nav2 + cuVSLAM.
# The 2026-10-01 plan: wheels stage = cuVSLAM + nvblox. Rollback: sudo bash ~/setup/rollback_isaac_ros.sh
set -euo pipefail
[ "$(id -u)" = 0 ] || { echo "run with sudo"; exit 1; }
curl -fsSL https://isaac.download.nvidia.com/isaac-ros/repos.key | gpg --dearmor -o /usr/share/keyrings/isaac-ros.gpg
echo "deb [signed-by=/usr/share/keyrings/isaac-ros.gpg] https://isaac.download.nvidia.com/isaac-ros/release-3 jammy release-3.0" \
  > /etc/apt/sources.list.d/isaac-ros.list
apt-get update -o Dir::Etc::sourcelist=sources.list.d/isaac-ros.list -o Dir::Etc::sourceparts=- -o APT::Get::List-Cleanup=0
apt-get install -y libnvvpi3 cuda-nvtx-12-6 ros-humble-isaac-ros-nvblox ros-humble-nvblox-nav2 ros-humble-isaac-ros-visual-slam ros-humble-isaac-ros-occupancy-grid-localizer ros-humble-isaac-ros-pointcloud-utils   # libnvvpi3 (VPI) + cuda-nvtx-12-6 (libnvToolsExt): JetPack libs nvblox_node needs, missing on the rebuilt image
dpkg -l | grep -E "isaac-ros-(nvblox|visual-slam)|nvblox-nav2" | awk '{print $2, $3}'

# cuVSLAM v17 (RGB-D mode for the Aurora; the apt visual_slam package bundles v12 = stereo only). User install.
W=cuvslam-17.0.0+cu12-cp310-cp310-manylinux_2_35_aarch64.whl
sudo -u burgerbarn mkdir -p /home/burgerbarn/setup/wheels
[ -f /home/burgerbarn/setup/wheels/$W ] || sudo -u burgerbarn curl -sfL -o /home/burgerbarn/setup/wheels/$W \
  "https://github.com/nvidia-isaac/cuVSLAM/releases/download/v17.0.0/cuvslam-17.0.0%2Bcu12-cp310-cp310-manylinux_2_35_aarch64.whl"
echo "658250d97c96c5db0d61ed68d8c0e99514c130574339367f0d917ad60f05d315  /home/burgerbarn/setup/wheels/$W" | sha256sum -c
sudo -u burgerbarn pip3 install --user /home/burgerbarn/setup/wheels/$W
