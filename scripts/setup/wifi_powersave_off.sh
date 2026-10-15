#!/bin/bash
# Robot: WiFi power save off (2026-10-07: rtw89_8852ce firmware hung leaving power save, robot fell off the network).
# Run on the robot. --undo restores Ubuntu's default (power save on).
F=/etc/NetworkManager/conf.d/zz-wifi-powersave-off.conf    # must sort after default-wifi-powersave-on.conf to win
IF=$(iw dev | awk '/Interface/{print $2; exit}')
if [ "$1" = --undo ]; then
  sudo rm -f "$F"; sudo nmcli general reload conf; sudo iw dev "$IF" set power_save on
else
  printf '# 2026-10-07: rtw89_8852ce firmware hung leaving power save; 2 = disable\n[connection]\nwifi.powersave = 2\n' | sudo tee "$F" >/dev/null
  sudo nmcli general reload conf; sudo iw dev "$IF" set power_save off
fi
echo "config: $(NetworkManager --print-config 2>/dev/null | grep '^wifi\.powersave') | live: $(iw dev "$IF" get power_save)"
