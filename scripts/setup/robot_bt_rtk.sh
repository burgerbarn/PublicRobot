#!/bin/bash
# Robot Bluetooth (RTL8852CE BT, USB 0bda:0852) on NVIDIA's rtk_btusb (nvidia-l4t-kernel-oot-modules) instead of the
# in-kernel btusb/btrtl, which on 5.15 does not know the chip ("unknown IC info, lmp subver 8852") and loads no
# firmware (2026-10-07: discovery found 0 devices). Firmware: the linux-firmware files, named the way NVIDIA ships its
# devkit chip's (/lib/firmware/rtl8822cu_fw + rtl8822cu_config at the top level). Run on the robot.
#   robot_bt_rtk.sh          firmware links + blacklist btusb (so rtk_btusb gets the chip at boot) + re-bind now
#   robot_bt_rtk.sh --undo   remove the links and the blacklist, give the device back to btusb
set -u
DEV=$(for d in /sys/bus/usb/devices/*; do [ "$(cat $d/idVendor 2>/dev/null):$(cat $d/idProduct 2>/dev/null)" = 0bda:0852 ] && basename $d; done | head -1)
[ -z "$DEV" ] && { echo "no 0bda:0852 on USB"; exit 1; }
rebind() {   # $1 = from driver, $2 = to driver
  for i in /sys/bus/usb/devices/$DEV/$DEV:1.*; do
    n=$(basename $i); [ -e /sys/bus/usb/drivers/$1/$n ] && echo $n | sudo tee /sys/bus/usb/drivers/$1/unbind >/dev/null
  done
  sleep 1
  echo $DEV:1.0 | sudo tee /sys/bus/usb/drivers/$2/bind >/dev/null 2>&1
  sleep 3
}
if [ "${1:-}" = --undo ]; then
  rebind rtk_btusb btusb
  sudo rm -f /lib/firmware/rtl8852cu_fw /lib/firmware/rtl8852cu_config /etc/modprobe.d/rosorin-bt-rtk.conf
else
  sudo ln -sfn rtl_bt/rtl8852cu_fw.bin /lib/firmware/rtl8852cu_fw
  sudo ln -sfn rtl_bt/rtl8852cu_config.bin /lib/firmware/rtl8852cu_config
  printf '# 2026-10-07: RTL8852CE BT (0bda:0852) belongs to rtk_btusb (NVIDIA oot); btusb/btrtl 5.15 loads no firmware for it\nblacklist btusb\n' | sudo tee /etc/modprobe.d/rosorin-bt-rtk.conf >/dev/null
  sudo modprobe rtk_btusb
  rebind btusb rtk_btusb
fi
for i in /sys/bus/usb/devices/$DEV/$DEV:1.*; do echo "$(basename $i) -> $(basename "$(readlink -f $i/driver 2>/dev/null)")"; done
journalctl -k --since "-20 s" --no-pager -o cat | grep -iE "rtk_btusb|bluetooth|hci" | grep -iE "fw|firmware|patch|version|error|fail|lmp" | tail -8
hciconfig -a 2>/dev/null | grep -E "hci|UP|Manufacturer|LMP" | head -4
