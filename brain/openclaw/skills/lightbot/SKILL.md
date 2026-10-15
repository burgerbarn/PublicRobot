---
name: lightbot
description: Read the lightBot robot (state, battery, task, what it sees, ROS 2 topics, files) and answer questions about its build guide, through one read-only program. Use for any question about the robot or its guide.
metadata:
  {
    "openclaw":
      {
        "requires": { "bins": ["python3", "ssh"] },
      },
  }
---
# lightBot (read-only)

lightBot is a Hiwonder ROSOrin Pro rebuilt from scratch: Jetson Orin NX 16 GB, Ubuntu 22.04, ROS 2 Humble, hostname
`rosorin`, Wi-Fi 192.168.1.108. Its build guide is chapters 1-35 at https://wiki.matttesch.com/lightbot.

Every robot action goes through ONE program. Run it exactly like this (the owner allowlisted only this path):

    ~/CCode/rosorin-pro/brain/openclaw/lightbot_robot.py <command>

| Command | What it does |
|---|---|
| `status` | one-glance summary: battery_v, plugged, wheels, estop, arm, mind, task |
| `state` | the base driver's state (wheels enabled, e-stop, arm, gamepad mode) |
| `task` / `seen` / `objects` / `selfcare` | running wheel task, latest detections, latest object scan, latest self-care round |
| `frame /tmp/lightbot_frame.jpg` | save the newest camera frame |
| `run '<command>'` | one shell command on the robot, ONLY if the read-only gate allows it, e.g. `run 'systemctl is-active rosorin-base'`, `run 'timeout 6 ros2 topic hz /scan'`, `run 'journalctl -b -u rosorin-mind --no-pager -n 30'` |
| `ask '<question>'` | the local brain on bigbuddy answers from the build guide and code, with sources |
| `stop` | stops any wheel task (come, home, sethome, explore); always allowed |

Rules:
- Facts about the robot come from `ask` or from command output. Quote the output; say which command produced it.
  Never invent paths, topics, partitions or values.
- Moving the wheels or the arm, sound, enabling motors, starting or stopping services, installing, editing files,
  partitions, boot and reboot are the owner's. The program refuses them (`REFUSED: ...`, exit 2). Do not look for
  another way (no direct ssh, no curl to the robot API, no `sudo`): tell the owner the guide's command and let him do it.
- If the owner asks the robot to stop, run `stop` at once, then `status`.
- A FAIL or an odd value is a finding, not a fix: report it with the output and the guide chapter (`ask`).
- Claude is not used from here. If the local answer is not good enough, tell the owner he can ask Claude himself:
  `ssh -t bigbuddy '~/llm-train/ask/robot_ask.py --claude "<question>"'` (it asks his permission first).
