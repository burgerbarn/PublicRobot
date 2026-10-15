# Simulated rebuild, chapters 25-28: a freshly installed Fedora 44 bigbuddy (no desktop: the KDE install itself and
# the display are SKIP-NO-SIM). The first user is uid 1000 as on any fresh install (the owner's bigbuddy is 1001 by
# accident, chapter 25), login shell bash, sudo with a password (wheel). x86_64: no emulation, setuid sudo works.
FROM fedora:44
RUN dnf -y install systemd sudo passwd shadow-utils procps-ng psmisc iproute iputils hostname which findutils \
      curl wget git tar xz unzip bzip2 zstd nano less python3 python3-pip openssh-server openssh-clients \
      pciutils usbutils util-linux dnf-plugins-core file && dnf clean all
RUN useradd -m -G wheel -s /bin/bash burgerbarn && systemctl enable sshd \
 && printf 'PasswordAuthentication no\nPermitRootLogin no\n' > /etc/ssh/sshd_config.d/10-local.conf \
 && echo bigbuddy > /etc/hostname
CMD ["/sbin/init"]
