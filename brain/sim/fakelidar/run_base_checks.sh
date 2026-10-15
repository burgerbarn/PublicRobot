#!/bin/bash
# On bigbuddy, after `run_chapter.py --fresh --fake-board --fake-lidar ch10 ... ch16`: start the base by hand (the
# unit's ExecStart, ch16 form) on the fakelidar home volume with the fake board + fake LiDAR, and run the read parts of
# the LiDAR blocks that the runner skips only for systemd (ch14 Step 7 + Step 9, ch16 EKF + rf2o, ch17 first look).
# A translation, not the guide's blocks: `systemctl restart` -> background launch, `journalctl -u rosorin-base` ->
# the launch log, the USB unbind/bind -> SIGUSR1 to the fake LiDAR (3 s drop). Container: --network none, no
# --privileged; throwaway (--rm). Output: results/fakelidar/base_checks*.
set -u
cd "$(dirname "$0")/.." || exit 1
N=lightbot-sim-fakelidar-check; R=results/fakelidar
docker rm -f $N >/dev/null 2>&1
docker run -d --rm --name $N --network none --hostname rosorin --add-host rosorin:127.0.0.1 \
  -v lightbot-sim-home-fakelidar:/home lightbot-sim:humble sleep 900 >/dev/null || exit 1
docker exec -d -u root $N bash -c 'mkdir -p /tmp/fakeboard && exec python3 /opt/fakeboard/fake_board.py > /tmp/fakeboard/board.out 2>&1'
docker exec -d -u root $N bash -c 'mkdir -p /tmp/fakelidar && exec python3 /opt/fakelidar/fake_lidar.py > /tmp/fakelidar/lidar.out 2>&1'
sleep 2
mkdir -p $R
docker exec -u burgerbarn -w /home/burgerbarn -e HOME=/home/burgerbarn $N bash /opt/fakelidar/base_checks.sh > $R/base_checks.out 2>&1
docker exec -u root $N cat /tmp/base.log > $R/base_checks-base.log
docker exec -u root $N cat /tmp/slam.log > $R/base_checks-slam.log
docker exec -u root $N bash -c 'cat /tmp/fakelidar/status.json /tmp/fakelidar/events.jsonl; cat /tmp/fakeboard/status.json' > $R/base_checks-fakes.json
docker rm -f $N >/dev/null
cat $R/base_checks.out
