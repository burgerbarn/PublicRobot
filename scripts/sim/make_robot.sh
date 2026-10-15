#!/usr/bin/env bash
# Simulated rebuild: boot the simulation's staged rootfs (what chapter 5 puts on the robot's NVMe) as the robot.
# On bigbuddy: ~/lbsim/bin/make_robot.sh  ->  container lbsim-robot: arm64 (qemu), systemd, hostname rosorin,
# IP 172.30.0.10 on network lbsim, emulated controller board on /dev/ttyACM0 and LiDAR on /dev/lidar.
# Not privileged: the container sees none of bigbuddy's devices, so a disk command in a chapter cannot reach them.
# Emulators live in ~/lbsim/hw (mounted read-only at /opt/lbsim): they are hardware, not part of the build.
set -euo pipefail
W=~/lbsim/home/jetson-flash/work
SYS=$W/Linux_for_Tegra/tools/kernel_flash/images/external/system.img
docker rm -f lbsim-robot >/dev/null 2>&1 || true
if [ -s "$SYS" ]; then
  # path B (Workflow 12) result: system.img is NVIDIA's zstd-compressed tar of the APP partition's files; its writer
  # formats APP and unpacks this archive onto it. The same archive becomes the robot here (checksum first).
  echo "1/3 image from path B's APP archive (images/external/system.img, NVIDIA sha1 checked)"
  docker run --rm -v $W:/work:ro,z lbsim-jetson-flash:r36.4.3 bash -c '
    set -e; S=/work/Linux_for_Tegra/tools/kernel_flash/images/external
    want=$(cut -d" " -f1 $S/system.img.sha1sum); have=$(sha1sum $S/system.img | cut -d" " -f1)
    echo "   sha1 NVIDIA $want, file $have"; [ "$want" = "$have" ]' >&2
  docker run --rm -v $W:/work:ro,z lbsim-jetson-flash:r36.4.3 bash -c '
    apt-get -qq update >/dev/null && apt-get -qq install -y zstd >/dev/null
    zstd -q -dc /work/Linux_for_Tegra/tools/kernel_flash/images/external/system.img' \
    | docker import --platform linux/arm64 - lbsim-robot:fresh >/dev/null
else
  test -d $W/Linux_for_Tegra/rootfs/etc || { echo "no staged rootfs in $W"; exit 1; }
  echo "1/3 image from the staged rootfs (no path B image yet)"
  docker run --rm --privileged -v $W:/work:ro,z lbsim-jetson-flash:r36.4.3 \
    tar -C /work/Linux_for_Tegra/rootfs --numeric-owner -cf - . | docker import --platform linux/arm64 - lbsim-robot:fresh >/dev/null
fi
echo "2/3 boot (systemd)"
docker run -d --name lbsim-robot --hostname rosorin --platform linux/arm64 \
  --cgroupns=host -v /sys/fs/cgroup:/sys/fs/cgroup:rw --security-opt label=disable \
  --tmpfs /run --tmpfs /run/lock --cap-add SYS_ADMIN --cap-add NET_ADMIN \
  --network lbsim --ip 172.30.0.10 -v ~/lbsim/hw:/opt/lbsim:ro,z lbsim-robot:fresh /sbin/init >/dev/null
for i in $(seq 1 60); do
  s=$(docker exec lbsim-robot systemctl is-system-running 2>/dev/null || true)
  case "$s" in running|degraded) break;; esac; sleep 5
done
echo "   systemd: $s after $((i * 5)) s"
echo "3/3 hardware: board + LiDAR emulators"
~/lbsim/bin/hw_up.sh
