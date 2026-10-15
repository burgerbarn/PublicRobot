# Phase 4: bigbuddy rebuilds the robot from scratch - plan and real state (2026-10-15)

Not started. Needs the owner's "go". This file states what is measured, what is not, and the stop points.

## What "from scratch" means here

Erase the robot's p1 again (the system the 2026-10-12 rebuild put there), build it back from the guide with bigbuddy
running the steps, and end with the robot doing what it does today. p16 holds the previous system as the fallback,
as in the 2026-10-12 rebuild; going back = swap the partition names back (chapter 5).

## Measured so far (2026-10-14)

| Part | How it was tested | Result |
|---|---|---|
| ch04 (bigbuddy build host) | run for real on bigbuddy in a test folder | 10 min unattended, 6/6 checks PASS, 7 guide findings fixed |
| ch10-24 software (files, builds, tests) | test robot (container, x86, no hardware) | 400 s unattended; the workspace ends identical to the repo; 1 guide error fixed (ch17) |
| ch10-11 board steps | test robot with the fake controller board | 9 more steps run, all PASS |
| ch25-28 (Buddy on bigbuddy) | Fedora container | 150 s; every printed file equals live bigbuddy; 6 guide findings fixed |
| Checking a chapter on the real robot | read-only checker, gpt-oss-20b | 37 + 4 partly of 59 items confirmed, 0 false PASS, ~7 false FAIL (each one is a stop for the owner) |
| Plan of a real run | build runner dry run, ch05-24, read-only on the robot | 711-ish blocks classified; nothing written on the robot |

## Not measured (the real difficulty)

- **Chapters 5-9 on hardware by bigbuddy**: erasing p1, copying the rootfs over the network, the boot entry, the name
  swap and the reboots. Done once by the Claude session with the owner (2026-10-12); never by bigbuddy's runner.
- **Any state-changing step by the runner on the real robot**: `--go` was only tested in the container.
- **The model as the judge during a build** (not after it): a false FAIL mid-chapter stops the build until the owner
  looks; how often that happens in a full run is unknown (estimate from the checker: about 1 item in 8).
- **Hardware-dependent checks**: LiDAR, camera, motors, IMU on the stand and the floor tests need the real robot and
  the owner's hands; the fakes cover only the board's serial protocol (and the LiDAR, in progress).
- **Jetson-only software**: nvblox, cuVSLAM, TensorRT (ch16, ch18, ch20) never ran outside the robot.

## Stop points (from the dry run of ch05-24; the runner enforces them in code)

| Class | Blocks | Who |
|---|---|---|
| never (disks, partitions, boot, reboot, golden sets) | ~50 (ch05 16, ch08 20) | owner runs or approves each one; erase and name swap = the CLAUDE.md stop points |
| owner (sudo, apt, services, /etc) | ~78 | owner "yes" per block (could be batched per chapter later) |
| motion (first wheel/arm moves, floor tests) | ~27 | owner present, robot on the stand or the floor as the chapter says |
| hands (stand, gamepad, cables, buttons, looking) | ~15 | owner |
| file / home / auto | the rest | runner alone |

So a run from scratch is not unattended: about 170 decisions or hands-on steps, concentrated in ch05-ch09 (system)
and ch11/13/16-19 (motion). The runner removes the typing, the file writing and the checking in between.

## Risks

- The erase in ch05 removes the current working system on p1. Mitigation: p16 fallback (tested once, 2026-10-12)
  and the golden sets (robot ~/golden, bigbuddy ~/rosorin-golden).
- A model misreading a partition step (seen twice in tests: `APP_new` taken for `APP`, the p1/p16 rename direction):
  the runner never lets a model decide a destructive step; those are code-classified and owner-approved.
- Hardware state today: the depth camera dropped off USB 9 times on 2026-10-14 (10:05-10:39); unexplained.

## Order if the owner says go

1. Dry-run each chapter again right before running it (plan + read-only state).
2. ch05-ch09 with the owner at every never/owner block (the 2026-10-12 sequence, now run by bigbuddy).
3. ch10-ch24: the runner writes files and builds alone; the owner approves owner blocks and does hands/motion steps.
4. After each chapter: its "Before you move on" through the checker; any FAIL stops the run.
5. Chapter 29 read-back at the end; compare with today's robot (golden set + the answer key).
