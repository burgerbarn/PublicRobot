#!/bin/bash
# Runs before the first ROS process (rosorin-base ExecStartPre). Fast DDS 2.6 gives each process a host id = MD5 of the
# machine's IPv4 addresses AT PROCESS START (src/cpp/utils/Host.hpp); processes with different ids do not use shared
# memory with each other and fall back to UDP on loopback. 2026-10-05: base started 4 s before Wi-Fi had its address
# -> two groups -> 65,000 packets/s, 5,900 kernel receive-buffer overflows/s, load average 30.
# Waits until the address list has been the same for 6 s (at most 45 s; the robot must also start without Wi-Fi).
prev=""; same=0
for i in $(seq 1 45); do
  cur=$(ip -4 -o addr show | awk '$2!="lo"{print $2"="$4}' | sort | paste -sd,)
  if [ -n "$cur" ] && [ "$cur" = "$prev" ]; then same=$((same+1)); else same=0; fi
  [ $same -ge 6 ] && { echo "addresses stable after ${i}s: $cur"; exit 0; }
  prev=$cur; sleep 1
done
echo "addresses not stable after 45 s (now: ${cur:-none}) - starting anyway"; exit 0
