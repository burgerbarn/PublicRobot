#!/usr/bin/env bash
# Simulated rebuild, chapter 5 path B as NVIDIA's Workflow 12 (README_initrd_flash.txt): write a blank NVMe from
# bigbuddy with no Jetson connected (offline board IDs), here into a disk-image FILE instead of a real drive.
# On bigbuddy: ~/lbsim/bin/flash_direct_sim.sh      Result: ~/lbsim/nvme.img (GPT, NVIDIA's external layout).
#
# Safety: the flash container needs bigbuddy's /dev (loop partitions appear there), so before NVIDIA's tool runs this
# script proves the target is a loop device backed by ~/lbsim/nvme.img and nothing else. The file is 96 GiB sparse
# (not 512 GB like the robot's Micron 2400): if the tool falls back to writing zeros it cannot fill bigbuddy's disk.
set -euo pipefail
IMG=~/lbsim/nvme.img
W=~/lbsim/home/jetson-flash/work
SIZE=96G
APP=64GiB
rm -f "$IMG"; truncate -s $SIZE "$IMG"
run() { docker run --rm --privileged -v /dev:/dev -v ~/lbsim:/lbsim:z -v $W:/work:z -w /work/Linux_for_Tegra \
          -e BOARDID=3767 -e BOARDSKU=0000 -e FAB=300 -e BOARDREV= lbsim-jetson-flash:r36.4.3 bash -c "$1"; }
LOOP=$(run 'losetup -fP --show /lbsim/nvme.img')
LOOP=${LOOP#/dev/}
trap 'run "[ \"\$(losetup -n -O BACK-INO /dev/$LOOP | tr -d \" \")\" = \"\$(stat -c %i /lbsim/nvme.img)\" ] && losetup -d /dev/$LOOP" || true' EXIT
# the guard compares inodes: the kernel reports the backing path relative to the container's mount
ino=$(run "echo \$(losetup -n -O BACK-INO /dev/$LOOP | tr -d ' ') \$(stat -c %i /lbsim/nvme.img)")
set -- $ino
case "$LOOP" in loop[0-9]*) ;; *) echo "REFUSING: '$LOOP' is not a loop device"; exit 1;; esac
[ -n "${1:-}" ] && [ "$1" = "${2:-x}" ] || { echo "REFUSING: /dev/$LOOP backing inode '${1:-}' is not the image file '${2:-}'"; exit 1; }
echo "target /dev/$LOOP backs ~/lbsim/nvme.img (inode $1)"
echo "== prerequisites + Workflow 12 stage 1: build the images offline (BOARDID=3767 BOARDSKU=0000 FAB=300)"
# --no-flash skips the tool's NFS/USB-network setup, which a container on Fedora cannot satisfy (no nfsd in the
# kernel) and which direct mode never uses. Stage 2 is the tool's own flash_direct step, run as the tool runs it.
run "./tools/l4t_flash_prerequisites.sh >/dev/null 2>&1; \
     sudo EXT_NUM_SECTORS=$(( 96 * 1024 * 1024 * 2 )) BOARDID=3767 BOARDSKU=0000 FAB=300 BOARDREV= ./tools/kernel_flash/l4t_initrd_flash.sh --no-flash --direct $LOOP -c tools/kernel_flash/flash_l4t_external.xml \
       --external-device nvme0n1p1 -S $APP jetson-orin-nano-devkit-super external" > ~/lbsim/flash_direct.log 2>&1 \
  || { tail -30 ~/lbsim/flash_direct.log; exit 1; }
tail -5 ~/lbsim/flash_direct.log
echo "== Workflow 12 stage 2: write the images to /dev/$LOOP (tools/kernel_flash/images/l4t_flash_from_kernel.sh --direct)"
run "cd tools/kernel_flash/images && sudo EXTDEV_ON_HOST=$LOOP ./l4t_flash_from_kernel.sh --direct" > ~/lbsim/flash_direct_write.log 2>&1 \
  || { tail -30 ~/lbsim/flash_direct_write.log; exit 1; }
tail -8 ~/lbsim/flash_direct_write.log
echo "== result"
run "sgdisk -p /dev/$LOOP | tail -n +1 | head -30; blkid /dev/${LOOP}p1"
du -h --apparent-size "$IMG"; du -h "$IMG"
