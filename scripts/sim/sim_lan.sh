#!/usr/bin/env bash
# Simulated rebuild: a closed copy of the home LAN, so the guide's literal addresses reach the SIMULATED machines.
# The simulated robot answers on 192.168.1.108 (Wi-Fi) and .109 (wired), the simulated bigbuddy on .111; each reaches
# the other's addresses on-link over the lbsim bridge (next hop = the other container, never the gateway), so a packet
# for these addresses cannot leave the simulation. Every other 192.168.x.x stays prohibited (server .122 is not
# simulated). Idempotent; run again after a container restart. On bigbuddy: ~/lbsim/bin/sim_lan.sh
set -euo pipefail
R=lbsim-robot; B=lbsim-bb
docker exec $R bash -c 'ip route replace prohibit 192.168.0.0/16
for a in 192.168.1.108 192.168.1.109; do ip addr show dev eth0 | grep -q "inet $a/32" || ip addr add $a/32 dev eth0; done
ip route replace 192.168.1.111/32 via 172.30.0.30 dev eth0'
docker exec $B bash -c 'ip route replace prohibit 192.168.0.0/16
ip addr show dev eth0 | grep -q "inet 192.168.1.111/32" || ip addr add 192.168.1.111/32 dev eth0
for a in 192.168.1.108 192.168.1.109; do ip route replace $a/32 via 172.30.0.10 dev eth0; done'
echo "robot: $(docker exec $R ip -br -4 addr show dev eth0)"
echo "bigbuddy: $(docker exec $B ip -br -4 addr show dev eth0)"
docker exec $B bash -c 'for a in 192.168.1.108 192.168.1.109; do timeout 3 bash -c "echo > /dev/tcp/$a/22" 2>/dev/null && echo "bigbuddy -> $a:22 open (simulated robot)" || echo "bigbuddy -> $a:22 FAILED"; done'
docker exec $R bash -c 'timeout 3 bash -c "echo > /dev/tcp/192.168.1.111/22" 2>/dev/null && echo "robot -> 192.168.1.111:22 open (simulated bigbuddy)" || echo "robot -> .111:22 FAILED"
timeout 3 bash -c "echo > /dev/tcp/192.168.1.122/22" 2>/dev/null && echo "robot -> server .122 REACHABLE (wrong)" || echo "robot -> server .122 blocked"'
