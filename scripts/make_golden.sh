#!/bin/bash
# Golden image of the ROSOrin foundation. Run as: sudo bash ~/golden/make_golden.sh
# Reads the disk; writes only to $OUT (excluded from the rootfs archive).
set -euo pipefail
[ "$(id -u)" = 0 ] || { echo "run with sudo"; exit 1; }
DISK=/dev/nvme0n1
OUT=/home/burgerbarn/golden/$(date -u +%Y%m%dT%H%MZ)
ROOT=$(findmnt -no SOURCE /)   # p16 after chapter 5 path A, p1 after the p1 variant or path B; p2..p15 the same in all
case "$ROOT" in "${DISK}p16"|"${DISK}p1") ;; *) echo "root $ROOT is not ${DISK}p16 or ${DISK}p1, abort"; exit 1;; esac
# 2026-10-12: by name, not number (after the p1 variant p1 is this system and p16 the previous one). The factory
# install has no user burgerbarn.
[ "$(lsblk -no PARTLABEL "$ROOT")" = APP ] && id burgerbarn >/dev/null 2>&1 \
  || { echo "root $ROOT is not the partition named APP with user burgerbarn (factory install?), abort"; exit 1; }
mkdir -p "$OUT"

echo "== GPT"
sgdisk --backup="$OUT/gpt.sgdisk" "$DISK"
sgdisk -p "$DISK" > "$OUT/gpt.txt"
lsblk -bo NAME,SIZE,PARTLABEL,PARTUUID,FSTYPE,UUID "$DISK" > "$OUT/lsblk.txt"

echo "== boot partitions p2..p15 (raw, zstd)"
umount /boot/efi
trap 'mountpoint -q /boot/efi || mount /boot/efi' EXIT
for n in $(seq 2 15); do
  dd if="${DISK}p$n" bs=4M status=none | zstd -q -T0 -19 -o "$OUT/p$n.img.zst"
done
mount /boot/efi

echo "== rootfs (file-level tar, zstd)"
cp /boot/extlinux/extlinux.conf /etc/nv_boot_control.conf /etc/fstab "$OUT/"
tune2fs -l "$ROOT" | grep -E "Filesystem features|Inode size|Block size|Filesystem volume name|Filesystem UUID" > "$OUT/rootfs_tune2fs.txt"
find / -xdev \( -type f -o -type l \) -not -path "/home/burgerbarn/golden/*" \
  -not -path "/tmp/*" -not -path "/var/tmp/*" | wc -l > "$OUT/rootfs_filecount.txt"   # same exclusions as the tar
# GNU tar exits 1 when a live file changes while read (warning); 2 = fatal.
set +e
tar -C / --one-file-system --numeric-owner --xattrs --xattrs-include='*' --acls \
    --exclude=./home/burgerbarn/golden --exclude='./tmp/*' --exclude='./var/tmp/*' \
    -cpf - . 2> "$OUT/tar_warnings.txt" | zstd -q -T0 -10 -o "$OUT/rootfs.tar.zst"
rc=("${PIPESTATUS[@]}"); set -e
echo "tar rc=${rc[0]} zstd rc=${rc[1]}"; cat "$OUT/tar_warnings.txt"
[ "${rc[0]}" -le 1 ] && [ "${rc[1]}" -eq 0 ] || { echo "rootfs archive FAILED"; exit 1; }

echo "== checksums"
( cd "$OUT" && sha256sum -- * > SHA256SUMS )
chown -R burgerbarn:burgerbarn /home/burgerbarn/golden
du -sh "$OUT"; ls -la "$OUT"
echo "GOLDEN_DONE $OUT"
