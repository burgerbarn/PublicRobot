# lightbot-sim: a test robot for the lightBot build guide

A Docker container on bigbuddy that has the robot's software state after guide chapters 6-9 (Ubuntu 22.04,
ROS 2 Humble, user `burgerbarn`, chapter 9's packages). It runs the guide's software steps automatically, without
the real robot. It is amd64 and has no hardware: no controller board, LiDAR, camera, motors, systemd, Jetson GPU or
NVIDIA L4T packages. Steps that need those are skipped and recorded as skipped. The tests this gives are: files
written exactly as printed, builds, unit tests, scripts that run without hardware, and the guide's printed outputs.

Safety: chapter runs use `--network none` (the container cannot reach the robot, the server or anything else),
no `--privileged`, no GPU, no sudo on bigbuddy. Blocks that flash, partition, mount disks or reboot are never run.

## Run it

```bash
cd ~/lightbot-sim
docker build -t lightbot-sim:humble .                       # once, about 2-3 min (needs network; mirrors git repos)
~/llm-train/bin/python run_chapter.py --fresh ch10 ch11 ch12 ch13 ch14 ch15 ch16 ch17 ch18 ch19 ch20 ch21 ch22 ch23 ch24
./verify_run.sh > results/verify_all.txt                   # workspace vs repo, repo tests, compile check, launch load
~/llm-train/bin/python snippet_check.py ch10 ch11 ...      # code the guide shows for reading vs the repo
~/llm-train/bin/python run_chapter.py --dry-run ch13        # print the decision for every block, run nothing
~/llm-train/bin/python run_chapter.py --fresh --fake-board ch10 ch11   # with the fake controller board (below)
~/llm-train/bin/python run_chapter.py --fresh --fake-board --fake-lidar ch10 ch11 ch12 ch13 ch14   # + the fake LiDAR
```

- `--fresh` deletes the home volume `lightbot-sim-home` and starts from an empty robot. Without it a run continues
  on the home the previous run left. Steps are not idempotent (a second `git clone` into the same folder fails, an
  edit is inserted twice), so for clean results always run `--fresh` from ch10 up to the chapter you test.
- `--no-mac` skips `@mac` blocks instead of emulating the laptop.
- A full run of ch10-ch24 takes about 400 s (most of it: slam_toolbox 40 s in ch17, the isolated Nav2 checks
  170 s in ch18, two self-check rounds 125 s in ch23).

## Results

`results/chNN.jsonl` (one record per block) and `results/chNN.md` (readable): chapter, section, first line, decision
and why, exit code, failing commands inside the block (`[sim-err rc=N] command`), the last lines of output, and
`check k/n` = how many lines of the guide's "Check" box appeared verbatim in the output (timings and dates differ,
so k < n is not by itself a failure). `results/run_*.log` is the console log.

| Status | Meaning |
|---|---|
| `OK` / `FAIL` | the block ran; FAIL = nonzero exit or a failing command inside it. Read the output before calling it a guide error |
| `FILE-OK` | a file block was written: `CREATE` (Create `~/x`), `CREATE-PART`/`APPEND` (a file built in pieces), `OVERWRITE` (the complete file), `INSERT-AFTER` (an edit "right after the `X`" line). When a complete file follows pieces, `pieces_equal_complete` says whether the pieces add up to it |
| `SNIPPET` | code shown for reading (no Create/Add for a robot file); not written |
| `SKIPPED-HW` | needs hardware, systemd, a running base, the Jetson GPU or the real robot's data; setup lines before the first hardware line (`mkdir`, `cd`, `source`, `colcon build`) still run |
| `SKIPPED-DESTRUCTIVE` | flash, partition, mount, reboot: never run |
| `SKIPPED-LISTING` | a script listing or excerpt (`#!/bin/bash ...`), shown for reading |
| `SKIPPED-PATH` | the chapter's other path (for example ch15's factory partition) |
| `SKIPPED-OTHERHOST` | `@bigbuddy`/`@server` blocks, or a block that needs Buddy/the brain on bigbuddy |

## The fake controller board (`--fake-board`)

`fakeboard/fake_board.py` (in the image at `/opt/fakeboard/`) is started as root in the container before each
chapter and stopped after it. It creates a pseudo-terminal and links it as `/dev/ttyACM0` (`root:dialout 0660`; the
link itself is visible to `ls -l`). It speaks the RRC protocol (`AA 55 | function | length | payload | CRC-8/MAXIM`),
written independently of the guide's `rrc_protocol.py`, which is the code under test:

- sends, only while a program holds the port: IMU (function 7) at 110.5/s, gamepad idle frames (8, seven zero bytes)
  at 20/s, battery (0, `04` + mV) at 1/s. These are the rates in the guide, so 2 s of `cat` gives exactly the
  guide's `6905 bytes`. Values: battery 12670 mV; IMU means = the frame the guide decodes
  (`+0.0403 -0.0581 +0.9437 g`, `-1.9395 -2.4238 +0.0352 deg/s`), gyro noise std 0.052/0.070/0.039 deg/s (the
  driver's calibration log), accel noise 0.002 g, quantised like the real stream.
- answers servo reads (function 5, `[cmd, id]` -> `[id, cmd, ok=0, value]`) for servos 1-5 and 10: 0x05 position,
  0x07 supply 12600 mV, 0x09 temperature, 0x0D torque. Start pose = the tuck read on 2026-09-29
  (`{1: 500, 2: 963, 3: 46, 4: 131, 5: 498, 10: 629}`), torque off. Other ids stay silent.
- records every received frame (motor, buzzer, servo move/hold/torque-off) and never moves anything; servo moves
  only change the simulated read-back position (linear over the commanded time).
- behaves like the real port where the guide depends on it: `TIOCEXCL` gives `EBUSY` to a second opener; when the
  last user closes the port the board clears the exclusive flag (a pty keeps it, a real CDC-ACM port does not) and
  drops what nobody read (opens/closes are counted with inotify on the pty).

What the runner then does (see the docstring of `run_chapter.py`):

- Blocks skipped only for the board run: `rrc_read.py`, `rrc_send.py`, `stty`/`cat` on `/dev/ttyACM0`, `fuser`.
- A foreground `ros2 launch rosorin_base base.launch.py` runs as a background session in its own process group; the
  runner waits for launch's log-folder line + 8 s, runs the next block ("In a second ssh session") with the
  `ros2 topic/node/...` rule waived, and sends SIGINT to the whole process group (= Ctrl-C in a terminal) before the
  block whose text says Ctrl-C, or at the chapter end. Full log: `results/fakeboard/chNN-lineNNN.log`.
- A block followed by "While it runs ..." / "second ssh session" runs in the background and the next block runs
  2 s later, concurrently (ch10's exclusive-lock test). It is awaited before the next bash block.
- `lsusb` lines are dropped from a block (`hw_lines_dropped`); there is no USB device.
- Still skipped: systemd (service install, kill/restart, reboot, `journalctl`), the stand tests (`motion_test.py`,
  `stop_proof.py`: they need the service and the LiDAR scan gate), LiDAR, camera, Jetson.
- General rule (also without the flag): `[sim-err rc=124] timeout ...` is not an error (the guide bounds reads with
  `timeout`). A full ch10-ch24 run without the flag after this change gave the same counts in every chapter.
- `--fake-board` also changes ch17 line 219 (`timeout 40 ros2 launch rosorin_base slam.launch.py`, followed by
  "In a second session while it runs"): it runs in the background, the next block concurrently. Not run yet.

Results go to `results/fakeboard/`: `chNN.md` (with a "Fake board" section: frames sent/received, motor frames and
their largest speed, buzzer frames, servo commands), `chNN.fakeboard.json`, `chNN.fakeboard-rx.jsonl` (every frame
received), `chNN.fakeboard-events.jsonl` (opens/closes), `run.log`.

Reference counts with `--fresh --fake-board ch10 ch11` (2026-10-14): ch10 FILE-OK 13 OK 14 SKIPPED-HW 2 SNIPPET 1
(7 of the 9 skipped steps now run); ch11 FILE-OK 21 OK 12 DESTRUCTIVE 1 HW 10 SNIPPET 14 (the launch and its
second-session checks run; `pgrep` after Ctrl-C prints `all stopped`). Driver: `owning /dev/ttyACM0`, `gyro bias
(deg/s) [-1.938, -2.427, 0.036] from 333 samples`, `imu/data_raw average rate: 110.500`, `voltage: 12.67`,
`"blocked": "disabled"`; the board received only zero-speed motor frames and servo position reads.

Limits: no LiDAR (no `/scan`, no `/rosorin_lidar_reader` in `ros2 node list`, the reader respawns every 2 s in the
launch log, `scan_age` null); `ls -l /dev/ttyACM0` shows a symlink to `/dev/pts/N`, not `166, 0`; values are
fixed means plus noise (battery constant, no charger steps, gamepad never pressed, no key frames); the board has no
stop watchdog and no motors, so nothing about wheel stopping is tested; CRC errors at open (the guide's "1-2 from
the first read") do not happen here because unread bytes are dropped at close.

## The fake LiDAR (`--fake-lidar`)

`fakelidar/fake_lidar.py` (in the image at `/opt/fakelidar/`) is started as root before each chapter and stopped
after it, like the fake board. It links a pseudo-terminal the way the robot's udev rule does: `/dev/ttyUSB0` ->
`/dev/pts/N` (`root:dialout 0660`) and `/dev/lidar` -> `ttyUSB0`, so `ls -l /dev/lidar` prints `/dev/lidar ->
ttyUSB0` as in ch14. It streams the COIN-D6 (YDLIDAR TOF "intensity") packets of ch14 Step 2 at 230400 baud,
written independently of `coin_d6.py` (the code under test), with the structure of the repo's capture
`test/coin_d6_sample.bin`:

- 10.02 revolutions/s; each = a start packet (CT `0xC9`, 1 sample, distance 0, ~358.9 deg) + 16 packets of 25
  samples 0.8965 deg apart from ~359.5 deg, and in one revolution of five a single-sample CT `0x00` packet before the
  start: 1373 bytes per revolution, ~27.5 KB per 2 s. Bytes go out evenly, so a capture starts mid-packet.
- Native angles clockwise, the LiDAR's 180 deg = forward (ch14 Step 8). Scene in the robot frame: a 5 x 4 m room
  (walls x +3.0 / -2.0, y +1.8 / -2.2), a 0.9 m doorway in the front wall with a far wall at x 7.05 (returns up to
  ~7.7 m), a 0.4 m box at (1.2, -1.0); the robot's own body from the capture (native 34.4-57.1 deg at 54-91 mm and
  291.8-315.5 deg at 52-107 mm = base_link bearings 123-146 and 225-248, ~52 per revolution); no return at native
  315.5..360..34.4 and 57.1-62.5 deg, at range edges and 0.3 % at random: ~302 of 400 bins finite. Noise 5 mm.
- Sends only while a program holds the port; when the last user closes it the `TIOCEXCL` flag is cleared and unread
  bytes are dropped (as the fake board does). `kill -USR1` = the USB device drops for 3 s (both links vanish, the pty
  closes, a new one appears): the reader logs `EOF (device gone); reopening`, then `reopened`.

What the runner then does (`--fake-lidar`, combine with `--fake-board` for the base launch):

- Blocks skipped only for the LiDAR run (`/dev/lidar`, `stty`/`cat` on it); a `sudo systemctl stop <unit>` line is
  dropped (`hw_lines_dropped`: nothing runs as a service in the container, so the stop is a no-op).
- ch14 Step 6's hand-started `install/rosorin_base/lib/rosorin_base/lidar_reader` runs as a background session
  (started = its `reading /dev/lidar (read-only)` line + 8 s); the next block (`ros2 topic hz /scan`,
  `scan_probe.py`) runs beside it; SIGINT to its process group before the next block that does not run beside it
  (the guide's Ctrl-C is in a Check box, which the runner does not read). `ros2 topic` is waived while it runs.
- Own container, home volume and results: `lightbot-sim-fakelidar`, `lightbot-sim-home-fakelidar`,
  `results/fakelidar/` (`chNN.fakelidar.json` + a "Fake LiDAR" section in `chNN.md`).
- Still skipped: everything with `systemctl restart/start` or `journalctl` (ch14 Step 7 and Step 9, all /scan blocks
  of ch16-18 start the base with systemd), the USB unbind/bind. For those, `fakelidar/run_base_checks.sh` (after a
  `--fresh --fake-board --fake-lidar ch10 ... ch16` run) starts the base by hand on the fakelidar volume (the unit's
  `ExecStart` in its ch16 form) and runs the read parts of those blocks: a translation (`systemctl restart` ->
  background launch, `journalctl` -> the launch log, unbind/bind -> `kill -USR1`), output `results/fakelidar/base_checks*`.

```bash
cd ~/lightbot-sim
LIGHTBOT_REPO=~/lightbot-sim/repo-fakelidar ~/llm-train/bin/python run_chapter.py --fresh --fake-board --fake-lidar ch10 ch11 ch12 ch13 ch14 ch15 ch16
bash fakelidar/run_base_checks.sh
```

Reference (2026-10-15, repo `9dc8af8` in `repo-fakelidar/`, 135 s): ch10 FILE-OK 13 OK 14 HW 2 SNIPPET 1; ch11
FILE-OK 21 OK 12 DESTRUCTIVE 1 HW 10 SNIPPET 14; ch12 FILE-OK 7 OK 9 DESTRUCTIVE 1 HW 3 SNIPPET 4; ch13 OK 7 HW 8
LISTING 1 SNIPPET 18; ch14 FILE-OK 14 OK 7 HW 3 SNIPPET 3 (without the flag: OK 3 HW 7); ch15 and ch16 unchanged.
Measured: ch14 `27553 bytes in 2s` (guide 27506-27599), first `aa 55` at byte 2; `average rate: 10.033`,
`frame laser bins 400 finite 301 min 0.049 max 7.676 scan_time 0.100`, `below range_min ...: 51`; the hand-started
reader stopped cleanly (exit 0, no traceback). ch11's second session now lists `/rosorin_lidar_reader`,
`scan average rate: 10.022`, `"scan_age": 0.1`.
`run_base_checks.sh`: `/scan` `min: 0.098s max: 0.102s`; USB drop `reopening` -> `reopened` 3.0 s later;
`scan_gate_live.py` `PASS` (scans back after ~2.4 s); EKF `average rate: 50.000`; `/odom_rf2o` 1 publisher,
`/odom_lidar` ~10 Hz; `/scan` 52 beams below `range_min` at 0.054-0.112 m, bearings 123-146 / 225-248, `/scan_rf2o`
0; ch17's standing slam_toolbox gives a 159x107 map at 0.05 m and `map` -> `base_link`.

Limits: a static room and a robot that never moves (the fake board's wheels do not move it, so rf2o, the EKF and
slam see no motion); no reflections, glass, people or dust; intensities and the low bits of S1 are random; the
blind/self-return sectors are those of one capture (2026-09-26), not of every arm pose; timing is the container's
(scan-to-scan 0.098-0.102 s). Pre-change copies: `run_chapter.py.before-fakelidar`, `Dockerfile.before-fakelidar`,
`README.md.before-fakelidar`.

## The real robot (`--target robot`) and `--go`

`robot_target.py` gives every block a safety class, first match wins: `never` (erase/mkfs, partition table or
partition name change, boot entries, flashing, reboot, apt remove/purge/autoremove, the golden sets, the server:
refused outright, also with `--go`), `listing`, `motion` (wheels/arm can move), `hands` (stand, gamepad, cables,
buttons, looking at the robot, or a foreground program until Ctrl-C), `auto` (every logical line and the whole block
pass `readonly_gate.check`, the only judge of read-only), `ros2cli` (passes the gate, but the ros2 CLI writes
`~/.ros/log/python3_<pid>_<ms>.log` and spawns the ros2 daemon: measured 2026-10-14), `unguarded` (git/python/ros2 on
the robot through an ssh hop), `owner`, `home`, `file`, `snippet`, `server`, `text`.

```bash
~/llm-train/bin/python run_chapter.py --target robot --dry-run ch09 ch11 ch14   # plan + read-only blocks on the robot
~/llm-train/bin/python run_chapter.py --fresh --go ch10                         # container: asks y/n/q per non-auto block
~/llm-train/bin/python run_chapter.py --target robot --go ch14                  # the owner, at a terminal: types yes per block
~/llm-train/bin/python robot_target.py --selftest                              # offline: classes, refusals, approvals
```

- `--target robot --dry-run` executes only `auto` blocks: `@robot` over `ssh -o BatchMode=yes rosorin-wifi bash -lc`,
  `@bigbuddy` locally, `@mac` on bigbuddy (`~/CCode/rosorin-pro` -> `~/lightbot-sim/repo`, host `rosorin` ->
  `rosorin-wifi`, recorded per block). The remote shell gets `PYTHONDONTWRITEBYTECODE=1 GIT_OPTIONAL_LOCKS=0` and an
  unwritable `ROS_LOG_DIR`. Complete files and script listings the guide names are read (`cat`) for a drift check
  (`same` / `differs` / `piece present`). cwd and exports carry over between blocks, kept on bigbuddy (nothing is
  stored on the robot). Results: `results/robot/chNN.md|jsonl`; every command sent: `results/robot/commands.log`.
- `--go chNN`: one chapter; `auto` blocks run, `never` blocks are refused, everything else is asked first. Robot:
  stdin must be a terminal and the answer the word `yes`; a foreground program is left to the owner's own terminal;
  a service stop/start gets the note to run chapter 29's `readback.sh`. Container: the sim's own skips stay.
- Not run by the robot dry-run: `ros2cli` blocks, and `never` blocks even when they only read (e.g. a block with
  `command -v sgdisk; sudo sgdisk -p`, a `cat` of `extlinux.conf`).
- Checked 2026-10-14: `--fresh ch10 ch11 ch12` without the new flags gives the reference counts and the same
  line/status/exit for every block; a robot dry-run of ch09/ch11/ch14 changed no file on the robot (before/after
  `find -newermt` from a marker on bigbuddy: only the running services' logs, the journal and auth.log, the same set
  as in a control window with nothing run). `--go` was tested in the container only. Pre-change copy:
  `run_chapter.py.before-target`.

## arm64 (`--arch arm64`): the Jetson's CPU architecture under qemu

```bash
docker build --platform linux/arm64 -t lightbot-sim:humble-arm64 .        # same Dockerfile; 340 s on 2026-10-15
~/llm-train/bin/python run_chapter.py --arch arm64 --fresh ch10 ch11 ... ch24
./verify_run.sh --arch arm64 > results/arm64/verify_all.txt
```

- Own names, so the amd64 setup is untouched: image `lightbot-sim:humble-arm64`, home volume
  `lightbot-sim-home-arm64`, container `lightbot-sim-arm64`, results `results/arm64/` (`fakeboard/` below it with
  `--fake-board`). Without `--arch` everything is amd64 as before. Block timeouts are 4x (3600 s).
- It runs through bigbuddy's binfmt registration of `qemu-aarch64-static` (qemu 10.2.2, flags `F`). No `--privileged`,
  `--network none` as always. Jetson-only steps (L4T, CUDA/TensorRT, Isaac, nvblox) stay SKIPPED-HW: the container
  is plain Ubuntu arm64, not L4T.
- The image built on 2026-10-15 07:58 has no `/opt/fakelidar` (it predates that COPY): rebuild before `--fake-lidar`.

Measured limits (2026-10-15), read these before reading an arm64 FAIL as a guide error:

1. **`sudo` does not work.** binfmt without the `C` flag runs a setuid aarch64 binary with the caller's uid:
   `sudo: effective uid is not 0, is /usr/bin/sudo on a file system with the 'nosuid' option set ...`. Everything
   built on `sudo` fails: the laptop's `ssh`/`scp`/`rsync` to the robot (`fakebin/robotnet.py` switches user with
   `sudo -u`), `sudo apt-get install`, and every later step that needs the copied files. In the 2026-10-15 run that
   cascade is behind every block that changed status against amd64. Two ways out, neither done: the owner
   re-registers `qemu-aarch64` with flags `FC` on bigbuddy (root), or the arm64 image gets a sudo that works without
   the setuid bit (a static x86_64 setuid shim that execs the real sudo: refused by the permission system on
   2026-10-15).
2. **CMake `find_library` fails intermittently under qemu.** `project(t)` + `find_package(ament_cmake)` +
   `find_package(rosidl_default_generators)`: amd64 10/10 configure runs pass, arm64 2/10 (`Package 'rcutils' exports
   the library 'rcutils' which couldn't be found`, sometimes `'dl'`), arm64 with `QEMU_STRACE=1` (slower) 10/10. ch24's
   `explore_lite_msgs` failed this way; the same package built in the same image at other times. Rerun before
   believing a C++ configure failure.
3. **Speed.** CPU-bound steps are 5-10x slower (ch16 rf2o build 9.2 s -> 86.5 s; ch12/ch18 `source` + small builds
   0.5 s -> 6.3 s); ch15's driver build takes 75 s (42.8 s on the Jetson).

ch15 under arm64 (`results/arm64/ch15_arm64.sh`, `ch15_arm64_build.txt`, run outside the runner because of item 1:
the archive stream arrives on stdin, the guide's @robot block runs verbatim): the build that fails on amd64 (no x86_64
SDK) finishes (`Finished <<< deptrum-ros-driver-aurora930 [1min 14s]`, `1 package had stderr output`), library hash
`20de34b7...2551aac`, `0` for the token count; the node starts (`hello aurora 900/930`), and without a camera prints
`failed to initialise libusb: LIBUSB_ERROR_OTHER`, `device_count==========0`, then `wait device insert...` once a second.

## How it emulates the rest of the world

- The laptop: `@mac` blocks run as user `mac`; `~/CCode/rosorin-pro` is a writable copy of the repo (with `.git`),
  refreshed from `~/lightbot-sim/repo` at every start. `ssh`/`scp`/`rsync` to `rosorin`, `rosorin-wifi`,
  192.168.1.108/109 are local operations on `/home/burgerbarn` (`fakebin/robotnet.py`).
- bigbuddy (ch15 only): `ssh bigbuddy` reads a read-only mount of bigbuddy's `~/rosorin-vendor`.
- apt: the image keeps its package lists; `apt-get update` is skipped (no network), `apt-get install` of a package
  that is in the image succeeds as "already the newest version".
- git: the repos the chapters clone (rf2o, navigation2 1.1.20, m-explore-ros2) are mirrored in the image;
  `https://github.com/` is rewritten to the mirror.
- Shell: each chapter is one ssh session per user: `cd` and exported variables carry over from block to block.
  ROS is NOT sourced for you (the guide says `~/.bashrc` does not), so the chapter's own `source` lines must do it.

## Updating it

- The repo copy: from the Mac, `cd ~/CCode/rosorin-pro && git ls-files -z | rsync -a --from0 --files-from=- ./
  bigbuddy:lightbot-sim/repo/ && rsync -a --delete .git/ bigbuddy:lightbot-sim/repo/.git/`, then
  `ssh bigbuddy chmod -R a+rX lightbot-sim/repo`.
- A chapter whose wording the rules misread: first `--dry-run` it and read every SNIPPET/APPEND/OVERWRITE/LISTING
  decision. Extend the rules in `run_chapter.py` (`file_action`, `classify_bash`, `HARDWARE`) when the wording
  gives a signal; otherwise add an entry with a reason to `plan.json` (matched by section, first line, content).
  After any rule change rerun ch10-ch12 `--fresh` and confirm the same counts:
  ch10 FILE-OK 13 OK 7 SKIPPED-HW 9 SNIPPET 1; ch11 FILE-OK 21 OK 10 DESTRUCTIVE 1 HW 12 SNIPPET 14;
  ch12 FILE-OK 7 OK 9 DESTRUCTIVE 1 HW 3 SNIPPET 4.
- The fake board: edit `fakeboard/fake_board.py`, then `docker build` (only its COPY layer changes). Pre-change
  copies: `run_chapter.py.before-fakeboard`, `Dockerfile.before-fakeboard`, `README.md.before-fakeboard`.
- Packages a new chapter installs: add them to the `Dockerfile` (`ros-humble-*` from apt; they exist for amd64 and
  arm64) and rebuild both images (`--platform linux/arm64 -t lightbot-sim:humble-arm64` for the second).

## Files

`Dockerfile`, `robot_target.py` (safety classes, the real-robot target, `--go`), `fakeboard/fake_board.py` (the fake controller board), `fakelidar/` (the fake LiDAR, its runner hooks and `run_base_checks.sh`), `fakebin/robotnet.py` (laptop/bigbuddy emulation), `simbin/apt-get` (offline apt), `run_chapter.py`
(the runner), `plan.json` (hand decisions), `verify.sh` + `verify_run.sh` (after-run checks), `snippet_check.py`,
`repo/` (copy of the guide's repo), `results/`.
