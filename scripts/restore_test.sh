set -uo pipefail
G=/g/${1:-20260926T2041Z}; T=/g/restore-test
fail=0
echo "== zstd integrity"
for f in $G/*.zst; do zstd -tq "$f" || { echo "BAD $f"; fail=1; }; done; echo "zst files tested: $(ls $G/*.zst | wc -l)"
echo "== GPT restore onto sparse disk-size file"
truncate -s $((1000215216*512)) /tmp/disk.img
sgdisk --load-backup=$G/gpt.sgdisk /tmp/disk.img >/dev/null
sgdisk -p /tmp/disk.img | sed -n "/^Number/,\$p" > /tmp/gpt_restored.txt
sed -n "/^Number/,\$p" $G/gpt.txt > /tmp/gpt_orig.txt
diff /tmp/gpt_orig.txt /tmp/gpt_restored.txt && echo "GPT partition table identical" || fail=1
rm -f /tmp/disk.img
echo "== ESP image fsck"
zstd -dcq $G/p10.img.zst > /tmp/esp.img && fsck.vfat -n /tmp/esp.img | tail -2; rm -f /tmp/esp.img
echo "== rootfs extract + count"
rm -rf $T; mkdir -p $T
zstd -dcq $G/rootfs.tar.zst | tar -C $T --numeric-owner --xattrs --xattrs-include="*" --acls -xpf - || fail=1
got=$(find $T -xdev \( -type f -o -type l \) | wc -l); want=$(cat $G/rootfs_filecount.txt)
echo "restored files+links: $got  robot count: $want  diff: $((want-got))"
for f in boot/Image boot/initrd boot/extlinux/extlinux.conf etc/nv_boot_control.conf etc/hostname home/burgerbarn/.ssh/authorized_keys etc/ssh/sshd_config.d/10-local.conf; do test -e $T/$f && echo "present $f" || { echo "MISSING $f"; fail=1; }; done
stat -c "%u:%g %a %n" $T/home/burgerbarn/.ssh/authorized_keys $T/usr/bin/sudo
grep -c . $T/etc/nv_boot_control.conf
rm -rf $T
echo "RESULT fail=$fail"
