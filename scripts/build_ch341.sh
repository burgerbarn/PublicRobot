#!/bin/bash
# Build mainline ch341.ko (USB CH340/CH341 serial) for the running L4T kernel, from NVIDIA's
# R36.4.3 public sources. Runs as a normal user on the robot (needs nvidia-l4t-kernel-headers, gcc).
# Stock R36.4.3 kernel has CONFIG_USB_SERIAL_CH341 unset. Rebuild after any L4T kernel update.
set -euo pipefail
B=~/build; mkdir -p "$B/ch341"; cd "$B"
URL=https://developer.download.nvidia.com/embedded/L4T/r36_Release_v4.3/sources/public_sources.tbz2
[ -f public_sources.tbz2 ] || { wget -q -O public_sources.tbz2.part "$URL" && mv public_sources.tbz2.part public_sources.tbz2; }   # wget: curl is not installed before chapter 9
echo "2c177804679e3ed650dabec6fa958388579896f170570c6171a1b6c386669216  public_sources.tbz2" | sha256sum -c -
tar xjf public_sources.tbz2 Linux_for_Tegra/source/kernel_src.tbz2 Linux_for_Tegra/source/kernel_src.tbz2.sha1sum
cd Linux_for_Tegra/source
# NVIDIA's .sha1sum references their build path; compare the hash only
[ "$(sha1sum kernel_src.tbz2 | cut -d' ' -f1)" = "$(cut -d' ' -f1 kernel_src.tbz2.sha1sum)" ]
P=kernel/kernel-jammy-src/drivers/usb/serial/ch341.c
tar xjf kernel_src.tbz2 "$P"; cp "$P" "$B/ch341/ch341.c"
cd "$B/ch341"; printf 'obj-m := ch341.o\n' > Makefile
make -C /lib/modules/$(uname -r)/build M="$PWD" modules
modinfo -F vermagic ch341.ko; sha256sum ch341.ko
