FROM ubuntu:22.04
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
    sudo bzip2 lbzip2 tar python3 python3-yaml qemu-user-static binfmt-support \
    usbutils iproute2 iputils-ping openssh-client udev kmod nfs-kernel-server \
    abootimg cpio device-tree-compiler dosfstools libxml2-utils lz4 openssl \
    rsync sshpass uuid-runtime xxd zstd whiptail gdisk parted curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /work
