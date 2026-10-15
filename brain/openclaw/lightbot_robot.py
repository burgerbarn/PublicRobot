#!/usr/bin/env python3
"""lightBot for OpenClaw: the one program OpenClaw's exec tool may run (guide chapter 35). Reads the robot, never moves it.

  lightbot_robot.py status | state | task | seen | objects | selfcare   GET from the robot API (:8296, chapter 22)
  lightbot_robot.py frame [FILE]          save the newest camera frame (default /tmp/lightbot_frame.jpg)
  lightbot_robot.py stop                  POST /stop: stops come/home/sethome/explore (stopping is always allowed)
  lightbot_robot.py run 'COMMAND'         a command on the robot over ssh, only if readonly_gate.py allows it
  lightbot_robot.py ask 'QUESTION'        the local brain on bigbuddy (robot_ask.py, chapter 32)

Everything else is refused (exit 2) with the reason: wheel tasks, arm moves, sound, enable, and any command the
read-only gate refuses are the owner's, typed by hand. Claude is never called from here: robot_ask.py asks before it
uses Claude and finds no terminal under OpenClaw, so it sends nothing.
The token is read from ~/.config/rosorin/api_token (mode 0600) and sent in a header; it is never printed.
Environment: LIGHTBOT_ROBOT_API (http://192.168.1.108:8296), LIGHTBOT_ROBOT_SSH (ssh -n -o BatchMode=yes -o
ConnectTimeout=10 rosorin-wifi), LIGHTBOT_BRAIN_SSH (ssh -n -o BatchMode=yes -o ConnectTimeout=10 bigbuddy).
"""
import json, os, shlex, subprocess, sys, urllib.error, urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "runner"))
import readonly_gate  # noqa: E402

API = os.environ.get("LIGHTBOT_ROBOT_API", "http://192.168.1.108:8296")
ROBOT_SSH = shlex.split(os.environ.get("LIGHTBOT_ROBOT_SSH", "ssh -n -o BatchMode=yes -o ConnectTimeout=10 rosorin-wifi"))
BRAIN_SSH = shlex.split(os.environ.get("LIGHTBOT_BRAIN_SSH", "ssh -n -o BatchMode=yes -o ConnectTimeout=10 bigbuddy"))
TOKEN_FILE = Path.home() / ".config/rosorin/api_token"
GETS = {"status", "state", "task", "seen", "objects", "selfcare"}
OWNERS = {"come": "/task/come", "home": "/task/home", "sethome": "/task/sethome", "look": "/look", "play": "/play",
          "say": "/play", "enable": "the driver's enable service", "arm": "the arm services"}
# same environment as guide_check.py: ROS logs in RAM, the workspace sourced when it exists
ROS_ENV = ("export ROS_LOG_DIR=/dev/shm/lightbot-check-ros PYTHONDONTWRITEBYTECODE=1; source /opt/ros/humble/setup.bash; "
           "[ -f ~/ros2_ws/install/setup.bash ] && source ~/ros2_ws/install/setup.bash; ")
MAX_OUT, TIMEOUT = 4000, 180


def refuse(why):
    print(f"REFUSED: {why}")
    return 2


def api(method, path, out_file=None):
    if not TOKEN_FILE.exists():
        print(f"no robot token: copy it to {TOKEN_FILE} (chapter 35, 'Give it the robot token')")
        return 1
    req = urllib.request.Request(API + path, data=b"" if method == "POST" else None, method=method,
                                 headers={"X-Robot-Token": TOKEN_FILE.read_text().strip()})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            body = r.read()
    except urllib.error.HTTPError as e:
        print(f"HTTP {e.code}: {e.read()[:500].decode(errors='replace')}")
        return 1
    except OSError as e:
        print(f"robot API not reachable at {API} ({e}): robot off, Wi-Fi down, or rosorin-api not running")
        return 1
    if out_file:
        Path(out_file).write_bytes(body)
        print(f"saved {len(body)} bytes to {out_file}")
        return 0
    try:
        print(json.dumps(json.loads(body), indent=1)[:MAX_OUT])
    except ValueError:
        print(body[:MAX_OUT].decode(errors="replace"))
    return 0


def run(argv, label):
    try:
        p = subprocess.run(argv, capture_output=True, text=True, timeout=TIMEOUT)
    except subprocess.TimeoutExpired:
        print(f"(timed out after {TIMEOUT} s)")
        return 124
    out = (p.stdout + p.stderr)[-MAX_OUT:]
    print(out if out.strip() else f"(no output, exit {p.returncode})")
    if p.returncode == 255 and label == "robot":
        print("(ssh failed: robot off, Wi-Fi down, or no key for rosorin-wifi)")
    return p.returncode


def main(a):
    if not a or a[0] in ("-h", "--help", "help"):
        print(__doc__)
        return 0
    cmd, rest = a[0], " ".join(a[1:]).strip()
    if cmd in GETS:
        return api("GET", "/" + cmd)
    if cmd == "frame":
        return api("GET", "/frame.jpg", rest or "/tmp/lightbot_frame.jpg")
    if cmd == "stop":
        return api("POST", "/stop")
    if cmd == "run":
        if not rest:
            return refuse("run needs a command in quotes")
        ok, why = readonly_gate.check(rest)
        if not ok:
            return refuse(f"the read-only gate refuses it ({why}). Commands that change the robot are the owner's.")
        return run(ROBOT_SSH + [f"bash -lc {shlex.quote(ROS_ENV + rest)}"], "robot")
    if cmd == "ask":
        if not rest:
            return refuse("ask needs a question in quotes")
        return run(BRAIN_SSH + [f"~/llm-train/ask/robot_ask.py {shlex.quote(rest)}"], "brain")
    if cmd in OWNERS:
        return refuse(f"'{cmd}' moves the robot or makes sound ({OWNERS[cmd]}): the owner does it, with the robot in "
                      "view (Buddy, the face panel, or the guide's command). This program only reads and stops.")
    return refuse(f"unknown command '{cmd}'; see lightbot_robot.py --help")


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
