set -e
R=/work/Linux_for_Tegra/rootfs
cd $R
mount --bind /proc proc; mount --bind /sys sys; mount --bind /dev dev
trap 'umount dev sys proc || true' EXIT
LC_ALL=C chroot . groupadd -rf weston-launch
id burgerbarn >/dev/null 2>&1 || LC_ALL=C chroot . useradd -d /home/burgerbarn -m -G sudo,video,audio,adm,render,weston-launch,dialout -s /bin/bash burgerbarn
rm -f etc/systemd/system/default.target
mkdir -p etc/nv && touch etc/nv/nvautoconfig
echo rosorin > etc/hostname
grep -qxF "127.0.1.1 rosorin" etc/hosts || echo "127.0.1.1 rosorin" >> etc/hosts
LC_ALL=C chroot . ssh-keygen -A
install -d -m 700 -o $(stat -c %u home/burgerbarn) -g $(stat -c %g home/burgerbarn) home/burgerbarn/.ssh
cat /key.pub > home/burgerbarn/.ssh/authorized_keys
chown $(stat -c %u:%g home/burgerbarn) home/burgerbarn/.ssh/authorized_keys; chmod 600 home/burgerbarn/.ssh/authorized_keys
printf 'PasswordAuthentication no\nKbdInteractiveAuthentication no\nPermitRootLogin no\n' > etc/ssh/sshd_config.d/10-local.conf
echo "== verify"
grep burgerbarn etc/passwd; LC_ALL=C chroot . id burgerbarn
cat etc/hostname; ls etc/ssh/ssh_host_*_key.pub; cat etc/ssh/sshd_config.d/10-local.conf
ls -la etc/systemd/system/default.target 2>&1 | tail -1; ls -la home/burgerbarn/.ssh/
mkdir -p run/sshd; LC_ALL=C chroot . sshd -t && echo "sshd config OK"; rmdir run/sshd
