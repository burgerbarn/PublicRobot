#!/usr/bin/env python3
"""Run the software steps of one lightBot guide chapter in the lightbot-sim container ("test robot").

Usage (on bigbuddy):  ~/llm-train/bin/python ~/lightbot-sim/run_chapter.py [--fresh] [--no-mac] ch10 [ch11 ...]

What it does, per chapter, in document order:
  ```bash @robot      -> run as burgerbarn in one emulated ssh session per chapter (cwd and exported variables
                         carry over from block to block, like one terminal; ROS is NOT sourced for you: the guide
                         says ~/.bashrc does not source ROS, so the chapter's own `source` lines must do it)
  ```<lang> @robot    -> file blocks: written to the path the prose names ("Create `~/x.py`"), appended for
                         "Add ..."/continuation pieces, overwritten by "The complete/whole file", inserted after an
                         anchor for "right after the `...`" edits; other code blocks are SNIPPET (read-only text)
  ```bash/<lang> @mac -> run as user `mac` (emulated laptop: ~/CCode/rosorin-pro = the repo, read-only; ssh/scp/rsync
                         to rosorin / rosorin-wifi are local operations on /home/burgerbarn). --no-mac skips them.
  @bigbuddy/@server   -> SKIPPED-OTHERHOST
  hardware blocks     -> SKIPPED-HW (setup lines before the first hardware line still run)
  disk/boot blocks    -> SKIPPED-DESTRUCTIVE (never run)
The container runs with --network none: it cannot reach the robot, the server or anything else.
Results: ~/lightbot-sim/results/chNN.jsonl and chNN.md

--arch amd64|arm64 (default amd64; without it nothing changes). arm64 runs the same steps in an arm64 container
(image lightbot-sim:humble-arm64, emulated by qemu-user through binfmt, like the Jetson's CPU architecture), on its own
home volume lightbot-sim-home-arm64 and container name lightbot-sim-arm64; results go to results/arm64/. Block timeouts
are 4x (3600 s) there. Build: docker build --platform linux/arm64 -t lightbot-sim:humble-arm64 ~/lightbot-sim

--fake-board: a fake controller board (/opt/fakeboard/fake_board.py, a pty linked as /dev/ttyACM0) is started fresh
before each chapter. Then blocks that were skipped only because of the board run: rrc_read/rrc_send/stty on
/dev/ttyACM0, the foreground `ros2 launch rosorin_base base.launch.py` (as a background session, stopped with SIGINT
to its process group where the guide says Ctrl-C, or at the chapter end), and `ros2 topic/node/...` blocks while that
launch runs. A block followed by "While it runs ..." / "In a second ssh session" runs in the background, the next
block runs concurrently with it. `lsusb` lines are dropped from a block (no USB devices). LiDAR, camera, systemd,
motors-on-the-stand and Jetson steps stay skipped. Results go to results/fakeboard/ (what the board received:
chNN.fakeboard.json, chNN.fakeboard-rx.jsonl).

--fake-lidar: a fake COIN-D6 LiDAR (/opt/fakelidar/fake_lidar.py: a pty linked /dev/ttyUSB0, /dev/lidar -> ttyUSB0,
a static 5 x 4 m room plus the robot's ~52 self-returns at 10.02 revolutions/s) is started fresh before each chapter.
Blocks skipped only for the LiDAR then run (/dev/lidar, stty/cat on it, the hand-started lidar_reader as a background
session stopped with SIGINT before the next block that does not run beside it, `ros2 topic` while it runs), and a
`sudo systemctl stop <unit>` line is dropped (no service runs in the container: the stop is a no-op). Its own
container, home volume and results: lightbot-sim-fakelidar, lightbot-sim-home-fakelidar, results/fakelidar/ (with
the LiDAR's counters in chNN.fakelidar.json). Combine with --fake-board for the base launch.

--fake-systemd: a fake systemd (/opt/fakesystemd/fakesystemd.py: a manager process that runs the unit files the
guide installs, plus systemctl/journalctl clients; see README.md) is booted before each chapter (units enabled in
/etc/systemd/system/*.wants start, then 15 s settle) and shut down after it (KillSignal, ExecStopPost). Blocks skipped
only for systemd then run as printed (service installs, start/stop/restart/kill, is-active, enable, journal reads);
`ros2 topic/node/...` blocks run while rosorin-base is active (with --fake-board); a reboot block stays
SKIPPED-DESTRUCTIVE but the runner reboots the fake systemd there (new boot id, `journalctl -b -1` = the boot before).
The `sudo systemctl stop` drop of --fake-lidar is off. Own container, home and state volumes and results:
lightbot-sim-fakesystemd, lightbot-sim-home-fakesystemd, lightbot-sim-home-fakesystemd-systemd, results/fakesystemd/
(chNN.fakesystemd.json, chNN.journal.txt). plan.json entries with "only"/"unless": "fake-systemd" apply with/without it.

--target sim|robot (default sim; without it nothing changes). Every block gets a safety class (robot_target.py:
never/motion/hands/listing/auto/ros2cli/unguarded/owner/home/file/...; `auto` = every line passes
readonly_gate.check, the only judge of read-only).
  --target robot --dry-run chNN ...  prints the plan (block, host, class, why) and executes ONLY `auto` blocks: @robot
                                     over `ssh -o BatchMode=yes rosorin-wifi bash -lc`, @bigbuddy locally, @mac on
                                     bigbuddy (translation recorded); reads complete files on the robot for a drift
                                     check. Results and the command log: results/robot/.
  --go chNN                          runs the chapter but asks on stdin before every block that is not `auto` and
                                     refuses `never` blocks. With --target sim: in the container (y/n/q). With
                                     --target robot: only from a terminal, the owner types `yes` per block.
"""
import argparse
import difflib
import glob
import json
import os
import re
import shlex
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
# --arch: amd64 = native (the original test robot); arm64 = the Jetson's architecture, emulated by qemu-user (binfmt).
# Each arch has its own image, home volume, container name and results folder, so the two never touch each other.
ARCHES = {
    'amd64': dict(image='lightbot-sim:humble', volume='lightbot-sim-home', box='lightbot-sim',
                  results=os.path.join(HERE, 'results'), timeout_scale=1),
    'arm64': dict(image='lightbot-sim:humble-arm64', volume='lightbot-sim-home-arm64', box='lightbot-sim-arm64',
                  results=os.path.join(HERE, 'results', 'arm64'), timeout_scale=4),   # qemu: ~3-10x slower
}
ARCH = 'amd64'
IMAGE = ARCHES[ARCH]['image']
VOLUME = ARCHES[ARCH]['volume']
BOX = ARCHES[ARCH]['box']
BLOCK_TIMEOUT = 900       # seconds per block (scaled by timeout_scale)
REPO = os.environ.get('LIGHTBOT_REPO', os.path.join(HERE, 'repo'))
CHAPTERS = os.path.join(REPO, 'guide', 'chapters')
RESULTS = ARCHES[ARCH]['results']
VENDOR = os.path.expanduser('~/rosorin-vendor')     # bigbuddy's camera driver archive, mounted read-only (chapter 15)
HOMES = {'robot': ('burgerbarn', '/home/burgerbarn'), 'mac': ('mac', '/home/mac')}
FILE_LANGS = {'python', 'py', 'yaml', 'yml', 'xml', 'ini', 'json', 'cfg', 'toml', 'sh', 'cmake', 'service', 'conf'}
NONCODE_LANGS = {'mermaid', 'text', 'console', 'output', ''}
OTHER_HOSTS = re.compile(r'\b(bigbuddy|192\.168\.1\.122|server)\b')

DESTRUCTIVE = [
    (r'\b(mkfs(\.\w+)?|parted|sgdisk|gdisk|fdisk|wipefs|blkdiscard|e2label|resize2fs)\b', 'partition/filesystem tool'),
    (r'\bdd\s+[^|]*\bof=', 'dd to a device or file'),
    (r'\b(flash\.sh|l4t_initrd_flash\.sh|nvautoflash|nvbootctrl|efibootmgr)\b', 'flashing / boot entries'),
    (r'(^|[;&|(]\s*)(sudo\s+)?(reboot|poweroff|halt|shutdown)\b|systemctl\s+(reboot|poweroff|halt)', 'reboot/shutdown'),
    (r'\bmount\b[^\n]*\s/dev/|\bumount\b', 'mounting a disk partition'),
]
# (pattern, why, tag). With --fake-board the tags in FAKE_BOARD_WAIVES no longer skip a block; 'running_base' is
# waived while this chapter's base launch runs as a background session; 'usb' lines are dropped from the block.
HARDWARE = [
    (r'/dev/ttyACM|rrc_read\.py|rrc_send\.py|/dev/rrc\b', 'controller board /dev/ttyACM0', 'board'),
    (r'/dev/lidar|/dev/ttyUSB', 'LiDAR /dev/lidar', 'lidar'),
    (r'/dev/video|v4l2-ctl', 'camera'),
    (r'\blsusb\b|/sys/bus/usb', 'USB devices', 'usb'),
    (r'nvidia-l4t|nv_tegra_release|tegrastats|jetson_clocks|nvpmodel', 'Jetson/L4T only'),
    (r'motion_test\.py|stop_proof\.py|stand_proof', 'motors (stand tests)'),
    (r'\bsystemctl\b|\bjournalctl\b|install_\w+_service\.sh|rollback_\w+_service\.sh|install_dds_hostid_fix',
     'systemd units of the robot (no systemd in the container; the units drive the board)', 'systemd'),
    (r'ros2\s+launch\s+rosorin_base\s+base\.launch', 'base launch opens /dev/ttyACM0 and /dev/lidar', 'base_launch'),
    (r'ros2\s+(topic|node|service|action|param)\b|tf2_echo|view_frames|pgrep\s+-f\s+"\^/usr/bin/python3',
     'needs the running base service (board driver on the real board)', 'running_base'),
    (r'\bfuser\b', 'serial port owner', 'board'),
    (r'arm_jog_test\.py|moveit_named_move\.py', 'arm servos'),
    (r'lib/rosorin_base/lidar_reader|scan_gate_live\.py', 'LiDAR', 'lidar'),
    (r'aurora930_launch|install_aurora930\.sh|/mnt/factory|/sys/bus/usb', 'camera / factory partition'),
    (r'odom_test\.py|floor_odom_test\.py|odom_truth\.py|stop_proof_floor\.py', 'motors (floor/stand tests)'),
    (r'aarch64\.whl|cuvslam|jetson-ai-lab|nvidia-jetpack|libnvvpi|cuda-\w+-12-6|tensorrt|trtexec|/usr/local/cuda|isaac-ros',
     'NVIDIA Jetson / CUDA / Isaac ROS (arm64 + GPU only)'),
]
FAKE_BOARD_WAIVES = {'board', 'base_launch'}
FAKE_LIDAR_WAIVES = {'lidar'}
FAKE_SYSTEMD_WAIVES = {'systemd'}
ACTIVE_FLAGS = set()          # 'fake-systemd' when --fake-systemd: plan.json entries can say "only"/"unless" it
LIDAR_NODE = re.compile(r'^\s*install/rosorin_base/lib/rosorin_base/lidar_reader\s*$')   # ch14 Step 6, by hand
SVC_STOP = re.compile(r'^\s*sudo\s+systemctl\s+stop\s+[\w@.-]+\s*(2>/dev/null)?\s*(#.*)?$')   # dropped (--fake-lidar)
BASE_LAUNCH = re.compile(r'^\s*ros2\s+launch\s+rosorin_base\s+base\.launch\.py\s*$')
CONCURRENT_NEXT = re.compile(r'\bWhile it runs\b|\bsecond ssh session\b', re.I)   # prose of the NEXT block
CTRL_C = re.compile(r'\bCtrl-C\b')
HARDWARE_VIA_SSH = [   # laptop blocks: only what they make the robot do through ssh
    (r'ssh\s+\S+\s+[\'"]?[^|]*\b(systemctl|journalctl)\b', 'systemd units of the robot (via ssh)', 'systemd'),
    (r'ssh\s+\S+\s+[\'"]?[^|]*\bros2\s+(topic|node|service|launch)', 'needs the running base service (via ssh)',
     'running_base'),
    (r'scripts/ops/deploy\.sh', 'deploy.sh restarts robot services'),
]
_SETUP_PART = re.compile(r'^(mkdir -p \S+( \S+)*|cd \S+|source \S+|export \w+=\S*|touch \S+( \S+)*|ls( \S+)*|'
                         r'colcon build[^|]*(\| ?tail -\d+)?)$')


def is_setup_line(l):
    """A line made only of setup commands (mkdir/cd/source/export/touch/ls/colcon build), no quotes."""
    l = l.split(' #')[0].strip()
    if not l or '"' in l or "'" in l or l.endswith('\\'):
        return False
    return all(_SETUP_PART.match(p.strip()) for p in re.split(r'&&|;', l) if p.strip())

WRAPPER = r'''
S="${SIM_STATE:-$HOME/.sim_state}"; mkdir -p "$S"
[ -f "$S/env" ] && source "$S/env" 2>/dev/null
cd "$(cat "$S/pwd" 2>/dev/null || echo "$HOME")" 2>/dev/null || cd "$HOME"
case ":$PATH:" in *":/opt/fakebin:"*) ;; *) [ "$(id -un)" = mac ] && export PATH="/opt/fakebin:$PATH";; esac
trap 'echo "[sim-err rc=$?] $BASH_COMMAND" >&2' ERR
source "$1"
__rc=$?
trap - ERR
pwd > "$S/pwd"
export -p | grep -vE '^declare -[a-zA-Z-]* (BASHOPTS|SHELLOPTS|BASH_VERSINFO|EUID|PPID|UID|_|OLDPWD|PWD|SHLVL)=' > "$S/env"
exit $__rc
'''


def sh(cmd, inp=None, timeout=900):
    t0 = time.time()
    try:
        p = subprocess.run(cmd, input=inp, capture_output=True, text=True, timeout=timeout)
        return p.returncode, p.stdout + p.stderr, time.time() - t0
    except subprocess.TimeoutExpired as e:
        out = (e.stdout or b'') + (e.stderr or b'')
        return 124, (out.decode() if isinstance(out, bytes) else out) + f'\n[sim] TIMEOUT after {timeout}s', timeout


class Box:
    extra_args = []           # --fake-systemd: --init (reaps orphans like PID 1) + the state volume

    def __init__(self, name):
        self.name = name

    def start(self):
        sh(['docker', 'rm', '-f', self.name])
        vend = (['-v', f'{VENDOR}:/bigbuddy/rosorin-vendor:ro'] if os.path.isdir(VENDOR) else [])
        rc, out, _ = sh(['docker', 'run', '-d', '--rm', '--name', self.name, '--platform', f'linux/{ARCH}',
                         '--network', 'none',
                         '--hostname', 'rosorin', '--add-host', 'rosorin:127.0.0.1', '-v', f'{VOLUME}:/home',
                         '-v', f'{REPO}:/repo:ro'] + vend + self.extra_args + [IMAGE, 'sleep', 'infinity'])
        if rc:
            sys.exit('docker run failed: ' + out)
        # the laptop's clone: a writable copy of the repo (with .git), refreshed from the read-only /repo at every start
        self.root('mkdir -p /home/mac/CCode && { [ -L /home/mac/CCode/rosorin-pro ] && rm /home/mac/CCode/rosorin-pro; true; }'
                  ' && rsync -a --delete /repo/ /home/mac/CCode/rosorin-pro/ && chown -R mac:mac /home/mac/CCode'
                  ' && rm -rf /home/burgerbarn/.sim_state /home/mac/.sim_state && mkdir -p /tmp/sim && chmod 1777 /tmp/sim')

    def stop(self):
        sh(['docker', 'stop', '-t', '2', self.name])

    def root(self, cmd):
        return sh(['docker', 'exec', '-u', 'root', self.name, 'bash', '-c', cmd])

    def run_block(self, who, code, n, timeout=None):
        timeout = timeout or BLOCK_TIMEOUT
        user, home = HOMES[who]
        path = f'/tmp/sim/{user}-{n}.sh'
        rc, out, _ = sh(['docker', 'exec', '-i', '-u', user, self.name, 'bash', '-c', f'rm -f {path}; cat > {path}'],
                        inp=code + '\n')
        if rc:
            return 125, f'[sim] could not write the block file {path}: {out}', 0.0
        rc, out, dt = sh(['docker', 'exec', '-u', user, '-w', home, '-e', f'HOME={home}', self.name,
                          'bash', '-lc', WRAPPER, 'wrap', path], inp='', timeout=timeout)
        if rc == 124:
            self.root(f'pkill -u {user} -f {path} || true')
        return rc, out, dt

    # ---- background sessions: a block that runs while the next ones run ("in a second ssh session")
    def start_bg(self, who, code, n):
        """Start a block in its own session and process group (setsid). Its shell state is a copy of the chapter's
        (cd, exports) kept apart, like a second terminal. -> session dict."""
        user, home = HOMES[who]
        path, base = f'/tmp/sim/{user}-{n}.sh', f'/tmp/sim/bg-{user}-{n}'
        sh(['docker', 'exec', '-i', '-u', 'root', self.name, 'bash', '-c',
            'cat > /tmp/sim/wrap.sh && chmod 644 /tmp/sim/wrap.sh'], inp=WRAPPER)
        run = (f'export SIM_STATE="$HOME/.sim_state_bg_{n}"; rm -rf "$SIM_STATE"; mkdir -p "$SIM_STATE"\n'
               f'cp -a "$HOME/.sim_state/." "$SIM_STATE/" 2>/dev/null\n'
               f'rm -f {base}.rc {base}.pid\n'
               f"setsid -w bash -c 'echo $$ > {base}.pid; exec bash -l /tmp/sim/wrap.sh {path}' > {base}.log 2>&1 < /dev/null\n"
               f'echo $? > {base}.rc\n')
        for p, content in ((path, code + '\n'), (base + '.run', run)):
            rc, out, _ = sh(['docker', 'exec', '-i', '-u', user, self.name, 'bash', '-c', f'cat > {p}'], inp=content)
            if rc:
                return None
        sh(['docker', 'exec', '-d', '-u', user, '-w', home, '-e', f'HOME={home}', self.name, 'bash', base + '.run'])
        return {'base': base, 'who': who, 't0': time.time()}

    def bg_pgid(self, sess):
        for _ in range(50):
            rc, out, _ = self.root(f'cat {sess["base"]}.pid 2>/dev/null')
            if out.strip().isdigit():
                return int(out.strip())
            time.sleep(0.1)
        return None

    def bg_done(self, sess):
        rc, out, _ = self.root(f'cat {sess["base"]}.rc 2>/dev/null')
        return int(out.strip()) if out.strip().lstrip('-').isdigit() else None

    def bg_log(self, sess):
        rc, out, _ = self.root(f'cat {sess["base"]}.log 2>/dev/null')
        return out

    def wait_bg(self, sess, timeout):
        end = time.time() + timeout
        while time.time() < end:
            rc = self.bg_done(sess)
            if rc is not None:
                return rc
            time.sleep(0.5)
        return None

    def stop_bg(self, sess, sig='INT', wait=20):
        """Ctrl-C emulation: the signal goes to the whole process group, as from a terminal. Then TERM, KILL."""
        pg = self.bg_pgid(sess)
        for s_, w in ((sig, wait), ('TERM', 5), ('KILL', 5)):
            if pg:
                self.root(f'kill -{s_} -- -{pg} 2>/dev/null')
            rc = self.wait_bg(sess, w)
            if rc is not None:
                return rc, s_
        return None, 'KILL'

    # ---- the fake controller board (--fake-board)
    def start_fake_board(self):
        self.stop_fake_board()
        self.root('rm -rf /tmp/fakeboard')
        sh(['docker', 'exec', '-d', '-u', 'root', self.name, 'bash', '-c',
            'mkdir -p /tmp/fakeboard && exec python3 /opt/fakeboard/fake_board.py > /tmp/fakeboard/board.out 2>&1'])
        for _ in range(50):
            rc, _, _ = self.root('test -c /dev/ttyACM0')
            if rc == 0:
                return True
            time.sleep(0.1)
        return False

    def stop_fake_board(self):
        self.root("pkill -TERM -f '^python3 /opt/fakeboard/fake_board.py' ; sleep 0.5; "
                  "pkill -KILL -f '^python3 /opt/fakeboard/fake_board.py'; true")

    def fake_board_report(self):
        """What the board saw: status counters + received frames grouped (from /tmp/fakeboard)."""
        rc, out, _ = self.root('cat /tmp/fakeboard/status.json 2>/dev/null')
        try:
            st = json.loads(out)
        except ValueError:
            return {'error': 'no status.json', 'board_out': self.root('cat /tmp/fakeboard/board.out')[1][-2000:]}
        rc, rx, _ = self.root('cat /tmp/fakeboard/rx.jsonl 2>/dev/null')
        rc, ev, _ = self.root('cat /tmp/fakeboard/events.jsonl 2>/dev/null')
        st['_rx_jsonl'], st['_events_jsonl'] = rx, ev
        frames = [json.loads(l) for l in rx.splitlines() if l.strip()]
        st['rx_samples'] = {}
        for f in frames:                      # first 3 distinct frames per function, for the report
            lst = st['rx_samples'].setdefault(f['name'], [])
            if len(lst) < 3 and f['hex'] not in [x['hex'] for x in lst]:
                lst.append(f)
        st['events'] = [json.loads(l) for l in ev.splitlines() if l.strip()][-60:]
        return st

    def read(self, who, path):
        user, _ = HOMES[who]
        p = subprocess.run(['docker', 'exec', '-u', user, self.name, 'cat', path], capture_output=True, text=True)
        return p.stdout if p.returncode == 0 else None

    def write(self, who, path, content):
        user, _ = HOMES[who]
        p = subprocess.run(['docker', 'exec', '-i', '-u', user, self.name, 'bash', '-c',
                            'd=$(dirname "$1"); [ -d "$d" ] || { echo "directory $d does not exist"; exit 3; }; '
                            'cat > "$1"', 'w', path], input=content, capture_output=True, text=True)
        return p.returncode, p.stdout + p.stderr


# ---------------------------------------------------------------- parsing

FENCE = re.compile(r'^```([\w+-]*)\s*(@\w+)?\s*$')


def parse(md_path):
    """-> list of blocks: dict(line, lang, host, code, prose, section, check)"""
    lines = open(md_path).read().split('\n')
    blocks, prose, h2, h3, i = [], [], '', '', 0
    in_adm = False
    while i < len(lines):
        ln = lines[i]
        m = FENCE.match(ln)
        if m:
            j = i + 1
            while j < len(lines) and not lines[j].startswith('```'):
                j += 1
            blk = dict(line=i + 1, lang=m.group(1).lower(), host=(m.group(2) or '')[1:], code='\n'.join(lines[i + 1:j]),
                       prose=' '.join(prose), section=(h2 + (' / ' + h3 if h3 else '')).strip(),
                       check=extract_check(lines, j + 1))
            blocks.append(blk)
            prose, in_adm, i = [], False, j + 1
            continue
        if ln.startswith('#'):
            lvl = len(ln) - len(ln.lstrip('#'))
            title = ln.lstrip('#').strip()
            if lvl == 2:
                h2, h3 = title, ''
            elif lvl == 3:
                h3 = title
            prose.append(f'[H{lvl}] {title}')
            in_adm = False
        elif ln.startswith('!!!'):
            in_adm = True
        elif in_adm and (ln.startswith('    ') or not ln.strip()):
            pass
        else:
            in_adm = False
            if ln.strip():
                prose.append(ln.strip())
        i += 1
    return blocks


def extract_check(lines, k):
    """Expected output lines of the first '!!! check' admonition after a block (before the next top-level fence)."""
    while k < len(lines) and not lines[k].startswith('```'):
        if lines[k].startswith('!!! check'):
            exp, k, inner = [], k + 1, False
            while k < len(lines) and (lines[k].startswith('    ') or not lines[k].strip()):
                s = lines[k]
                if s.strip().startswith('```'):
                    inner = not inner
                elif inner or s.startswith('        '):
                    if s.strip():
                        exp.append(s.strip())
                k += 1
            return exp
        k += 1
    return []


# ---------------------------------------------------------------- classification

def first_match(rules, code):
    for rule in rules:
        m = re.search(rule[0], code, re.M)
        if m:
            return rule[1], m.group(0).strip()
    return None


def all_matches(rules, code):
    """-> [(why, matched text, tag)] for every rule that matches (tag None for untagged rules)."""
    out = []
    for rule in rules:
        m = re.search(rule[0], code, re.M)
        if m:
            out.append((rule[1], m.group(0).strip(), rule[2] if len(rule) > 2 else None))
    return out


def home_paths(text, home):
    return [p.replace('~', home, 1) if p.startswith('~') else p
            for p in re.findall(r'`((?:~|/home/burgerbarn)/[^`\s]+)`', text)]


class FileState:
    def __init__(self):
        self.building = {'robot': None, 'mac': None}
        self.pieces = {}            # path -> text accumulated from pieces (for the "complete file" comparison)
        self.last_edit = {'robot': None, 'mac': None}
        self.prev_action = {}


def join_piece(existing, piece):
    if existing is None:
        return piece + '\n'
    sep = '\n' if piece[:1] in (' ', '\t') else '\n\n'
    return existing.rstrip('\n') + '\n' + sep + piece + '\n'


_REL = None


def repo_relpaths():
    """basename -> path relative to the package, from the repo's final rosorin_base (for "`lidar_reader.py`")."""
    global _REL
    if _REL is None:
        _REL, root = {}, os.path.join(REPO, 'ros2', 'rosorin_base')
        for d, _, fs_ in os.walk(root):
            for f in fs_:
                rel = os.path.relpath(os.path.join(d, f), root)
                _REL.setdefault(f, rel)
                _REL.setdefault(rel, rel)
    return _REL


def named_robot_files(P, who):
    """Robot files a sentence names: home paths, or package files by name (`lidar_reader.py`, `launch/base.launch.py`)."""
    home = HOMES[who][1]
    out = home_paths(P, home)
    if who == 'robot':
        rel = repo_relpaths()
        for n in re.findall(r'`([\w./-]+\.(?:py|yaml|json|xml|service|urdf))`', P):
            n = n[len('ros2/rosorin_base/'):] if n.startswith('ros2/rosorin_base/') else n
            if n in rel:
                out.append(f'{home}/ros2_ws/src/rosorin_base/{rel[n]}')
        if re.search(r'\byour launch file\b|\bthe launch file\b', P) and not out:
            out.append(f'{home}/ros2_ws/src/rosorin_base/launch/base.launch.py')
    return out


def file_action(blk, who, fs):
    """Decide what a file-language block means. -> (action, path, info)"""
    P, code = blk['prose'], blk['code']
    home = HOMES[who][1]
    paths = home_paths(P, home)
    building = fs.building[who]
    prev = fs.prev_action.get(who)
    # Create `path` (partial when the text says it comes in pieces)
    m = re.search(r'Create\s+`((?:~|/home/burgerbarn)/[^`\s]+)`', P)
    if m:
        path = m.group(1).replace('~', home, 1)
        partial = re.search(r'with its header|\bfirst\b|starting with|\bstart with\b|build it in|'
                            r'in (?:\w+ )?(?:parts|pieces)', P[m.end():], re.I)
        return ('CREATE-PART' if partial else 'CREATE'), path, 'Create named in the text'
    # the complete / whole file
    if re.search(r'\b(the complete|the whole file|this is the complete|complete file)\b', P, re.I):
        if paths:
            return 'OVERWRITE', paths[-1], 'complete file named in the text'
        if building and (os.path.basename(building) in P or re.search(r'The complete file|The whole file', P)):
            return 'OVERWRITE', building, 'complete file of the one being built'
        return 'SNIPPET', None, 'complete file of the repository, for reading (no robot path named)'
    # edits with an anchor: "Add ... right after the `X`", "goes right after the `X` line", "after `self.pub`"
    m = None
    for sent in re.split(r'(?<=[.!?])\s+', P):
        mm = re.search(r'\bafter (?:the )?(?:two-line |one-line )?`([^`]+)`', sent)
        if mm and (re.search(r'\b([Aa]dd|goes|[Ii]nsert|[Pp]ut)\b', sent) or prev == 'INSERT-AFTER'):
            m = mm
    if m:
        named = named_robot_files(P, who)
        target = named[-1] if named else fs.last_edit[who]
        if target:
            return 'INSERT-AFTER', target, m.group(1)
    # Build it up in `path` / Build `path` in parts
    m = (re.search(r'Build it up in\s*`((?:~|/home/burgerbarn)/[^`\s]+)`', P)
         or re.search(r'Build\s+`((?:~|/home/burgerbarn)/[^`\s]+)`\s+in (?:\w+ )?(?:parts|pieces)', P))
    if m:
        return 'CREATE-PART', m.group(1).replace('~', home, 1), 'first piece of the file being built up'
    # Add ... / continuation pieces of the file being built (unless the text names another robot file)
    if building:
        if [p for p in paths if p != building]:
            return 'SNIPPET', None, 'text names another file'
        why = 'Add ... to the file being built' if re.search(r'\bAdd\b', P) else 'continuation piece of the file being built'
        return 'APPEND', building, why
    return 'SNIPPET', None, 'code shown for reading (no Create/Add for a robot file)'


def decide_file(blk, who, fs, override):
    if override:
        act = override['action']
        path = override.get('path', '').replace('~', HOMES[who][1], 1) or None
        res = (act, path, override['reason'] + ' [plan.json]')
    else:
        res = file_action(blk, who, fs)
    act, path, _ = res
    if act == 'CREATE-PART':
        fs.building[who] = path
    if act == 'OVERWRITE' and fs.building[who] == path:
        fs.building[who] = None
    if act == 'INSERT-AFTER':
        fs.last_edit[who] = path
    fs.prev_action[who] = act
    return res


def apply_file(box, blk, who, fs, override=None):
    action, path, info = decide_file(blk, who, fs, override)
    code = blk['code']
    rec = dict(action=action, path=path, info=info)
    if action == 'SNIPPET':
        return 'SNIPPET', rec
    cur = box.read(who, path)
    if action in ('CREATE', 'CREATE-PART'):
        new = code + '\n'
        fs.pieces[path] = new
    elif action == 'APPEND':
        new = join_piece(cur, code)
        fs.pieces[path] = new
    elif action == 'OVERWRITE':
        new = code + '\n'
        prev = fs.pieces.get(path)
        if prev is not None:
            same = prev.strip('\n') == new.strip('\n')
            rec['pieces_equal_complete'] = same
            nb = lambda t: [l.rstrip() for l in t.split('\n') if l.strip()]
            rec['pieces_equal_ignoring_blank_lines'] = nb(prev) == nb(new)
            if not same:
                rec['pieces_diff'] = ''.join(list(difflib.unified_diff(
                    prev.splitlines(True), new.splitlines(True), 'pieces', 'complete', n=1))[:60])
        fs.pieces.pop(path, None)
    elif action == 'INSERT-AFTER':
        anchor = info
        if cur is None:
            return 'FAIL', dict(rec, error=f'{path} does not exist')
        lines = cur.split('\n')
        key = anchor.split('...')[0].strip()
        idx = next((k for k, l in enumerate(lines) if key in l), None)
        if idx is None:
            return 'FAIL', dict(rec, error=f'anchor {anchor!r} not found in {path}')
        depth = lines[idx].count('(') - lines[idx].count(')') + lines[idx].count('[') - lines[idx].count(']')
        while depth > 0 and idx + 1 < len(lines):
            idx += 1
            depth += lines[idx].count('(') - lines[idx].count(')') + lines[idx].count('[') - lines[idx].count(']')
        lines[idx + 1:idx + 1] = code.split('\n')
        new = '\n'.join(lines)
        rec['inserted_after_line'] = idx + 1
    rc, out = box.write(who, path, new)
    if rc:
        return 'FAIL', dict(rec, error=out.strip())
    return 'OK', rec


# ---------------------------------------------------------------- main loop

def tail(text, n=25):
    ls = [l for l in text.rstrip('\n').split('\n')]
    return '\n'.join(ls[-n:])


def check_match(expected, out):
    if not expected:
        return None
    outl = {l.strip() for l in out.split('\n')}
    hit = [e for e in expected if e in outl]
    return f'{len(hit)}/{len(expected)}'


def load_plan():
    """plan.json: hand decisions for blocks the text gives no machine-readable signal for (script excerpts, the
    path not taken, ...). {"chNN": [{"section": substr, "first_line": prefix, "contains": substr, "host": "robot",
    "action": ..., "reason": ...}, ...]}; every given key must match; the first matching entry wins."""
    try:
        return json.load(open(os.path.join(HERE, 'plan.json')))
    except FileNotFoundError:
        return {}


def plan_override(plan, ch, blk, first):
    is_file = blk['lang'] in FILE_LANGS
    for e in plan.get(ch, []):
        if (e['action'] in BASH_ACTIONS) == is_file:
            continue
        if 'section' in e and e['section'] not in blk['section']:
            continue
        if 'first_line' in e and not first.strip().startswith(e['first_line']):
            continue
        if 'contains' in e and e['contains'] not in blk['code']:
            continue
        if 'host' in e and e['host'] != blk['host']:
            continue
        if ('only' in e and e['only'] not in ACTIVE_FLAGS) or ('unless' in e and e['unless'] in ACTIVE_FLAGS):
            continue
        e['_used'] = e.get('_used', 0) + 1
        return e
    return None


BASH_ACTIONS = {'RUN': 'RUN', 'LISTING': 'SKIPPED-LISTING', 'PATH': 'SKIPPED-PATH', 'HW': 'SKIPPED-HW',
                'DESTRUCTIVE': 'SKIPPED-DESTRUCTIVE', 'OTHERHOST': 'SKIPPED-OTHERHOST', 'NETWORK': 'SKIPPED-NETWORK'}
FOREGROUND = re.compile(r'^\s*(ros2\s+(launch|run)|install/\S+/lib/\S+)\b(?!.*&\s*$)', re.M)
GO_STATUS = {'skip': 'SKIPPED-OWNER', 'refuse': 'REFUSED-NEVER', 'quit': 'SKIPPED-OWNER-QUIT'}   # --go verdicts


class Ctx:
    """Per-run options and per-chapter background sessions."""
    def __init__(self, fake_board=False, approve=None, fake_lidar=False, fake_systemd=False):
        self.fake_board = fake_board
        self.fake_lidar = fake_lidar
        self.fake_systemd = fake_systemd
        self.sysd = None            # --fake-systemd: the hooks module (boot/shutdown/reboot)
        self.sysd_boots = []        # boots of this chapter (for the report)
        self.sessions = []          # dicts: sess, rec, kind ('fg' = until Ctrl-C, 'self' = ends by itself), base
        self.approve = approve      # --go: callable(ch, blk, who, kind, code) -> (verdict, class, why); None = no asking

    def base_running(self, box):
        if any(s['kind'] in ('fg', 'node') and box.bg_done(s['sess']) is None for s in self.sessions):
            return True
        # --fake-systemd: the base service runs (it drives the board, so only with the fake board)
        return bool(self.fake_systemd and self.fake_board and
                    box.root('systemctl is-active --quiet rosorin-base')[0] == 0)


def classify_bash(blk, who, first, override, ctx=None, box=None, next_prose=''):
    """-> (action, reason, extra); action in BASH_ACTIONS. extra: drop_lines (fake board: lsusb lines removed),
    background ('fg' = foreground session until Ctrl-C, 'self' = runs while the next block runs)."""
    code, extra = blk['code'], {}
    if override:
        return override['action'], override['reason'] + ' [plan.json]', extra
    if first.startswith('#!'):
        return 'LISTING', 'script listing (shown for reading; the guide runs the script file itself)', extra
    d = first_match(DESTRUCTIVE, code)
    if d:
        return 'DESTRUCTIVE', f'{d[0]} ({d[1]})', extra
    if who == 'mac' and OTHER_HOSTS.search(code):
        return 'OTHERHOST', 'laptop block talks to a host other than the robot', extra
    fake = bool(ctx and (ctx.fake_board or ctx.fake_lidar or ctx.fake_systemd) and who == 'robot')
    if who == 'mac' and ctx and ctx.fake_systemd:
        waived = set(FAKE_SYSTEMD_WAIVES)
        if box is not None and ctx.base_running(box):
            waived.add('running_base')
        hits = [h for h in all_matches(HARDWARE_VIA_SSH, code) if h[2] not in waived]
        if hits:
            return 'HW', f'{hits[0][0]} ({hits[0][1]})', extra
    elif not fake:
        hw = first_match(HARDWARE if who == 'robot' else HARDWARE_VIA_SSH, code)
        if hw:
            return 'HW', f'{hw[0]} ({hw[1]})', extra
    else:
        waived = (set(FAKE_BOARD_WAIVES) if ctx.fake_board else set()) | (FAKE_LIDAR_WAIVES if ctx.fake_lidar else set())
        if ctx.fake_systemd:
            waived |= FAKE_SYSTEMD_WAIVES
        if box is not None and ctx.base_running(box):
            waived.add('running_base')
        svc = [l for l in code.split('\n') if ctx.fake_lidar and not ctx.fake_systemd and SVC_STOP.match(l)]
        if svc:
            extra['drop_lines'] = svc
        hits = [h for h in all_matches(HARDWARE, '\n'.join(l for l in code.split('\n') if l not in svc))
                if h[2] not in waived]
        if hits and all(h[2] == 'usb' for h in hits):
            # lines that only list USB devices are dropped (the container has none); the rest of the block runs
            lines = code.split('\n')
            usb = [i for i, l in enumerate(lines) if re.search(HARDWARE[3][0], l)]
            simple = all(not lines[i].rstrip().endswith('\\') and (i == 0 or not lines[i - 1].rstrip().endswith('\\'))
                         for i in usb) and not re.search(r'^\s*(for|while|if|do|then)\b', code, re.M)
            if simple:
                extra['drop_lines'] = extra.get('drop_lines', []) + [lines[i] for i in usb]
                hits = []
        if hits:
            return 'HW', f'{hits[0][0]} ({hits[0][1]})', extra
    fg = [l for l in code.split('\n') if FOREGROUND.match(l) and not l.lstrip().startswith('timeout')]
    if fg:
        if fake and all(BASE_LAUNCH.match(l) for l in fg):
            extra['background'] = 'fg'
            return 'RUN', 'foreground base launch: runs as a background session until the guide says Ctrl-C', extra
        if fake and ctx.fake_lidar and all(LIDAR_NODE.match(l) for l in fg):
            extra['background'] = 'node'
            return 'RUN', ('hand-started LiDAR reader: a background session, stopped with SIGINT (Ctrl-C) before the '
                           'next block that does not run beside it'), extra
        return 'HW', f'foreground node session that runs until Ctrl-C, for hands-on use ({fg[0].strip()[:60]})', extra
    if fake and CONCURRENT_NEXT.search(next_prose):
        extra['background'] = 'self'
        return 'RUN', 'runs in the background: the next block runs while it runs (second ssh session)', extra
    return 'RUN', '', extra


SETTLE_LAUNCH_S = 8.0     # after `ros2 launch` printed its log folder: discovery + the driver's 3 s gyro calibration
SETTLE_SELF_S = 2.0       # a self-ending background block: time to open its port before the next block starts
LAUNCH_STARTED = 'All log files can be found below'
NODE_STARTED = re.compile(r'reading /dev/\S+ \(read-only\)')   # the hand-started lidar_reader is up


def timeout_ok(err_line):
    """`timeout N cmd` ending with 124 is the guide's way to bound a read (rrc capture, topic hz): not an error.
    With --fake-systemd also `sudo systemctl stop|start <unit> 2>/dev/null` failing: the guide silences it on purpose
    for a unit that may not be installed yet ("If rosorin-mind is installed ..."), the robot's shell shows nothing."""
    if 'fake-systemd' in ACTIVE_FLAGS and re.match(r'\[sim-err rc=5\]\s+sudo systemctl (stop|start) [\w@.-]+ '
                                                   r'2> ?/dev/null\s*$', err_line):
        return True
    # `n=$(... | grep -c X)` exits 1 when it counts 0: the guide wants the 0 (ch11's clean-stop loop)
    if 'fake-systemd' in ACTIVE_FLAGS and re.match(r'\[sim-err rc=1\]\s+\w+=\$\(.*\|\s*grep -c[E ].*\)\s*$', err_line):
        return True
    return bool(re.match(r'\[sim-err rc=124\]\s+timeout\b', err_line))


def finish_bg(box, s, ended_by, rc):
    out = box.bg_log(s['sess'])
    rec = s['rec']
    errs = [e for e in re.findall(r'\[sim-err rc=\d+\][^\n]*', out) if not timeout_ok(e)]
    if ended_by != 'itself':
        # the runner stopped it (Ctrl-C emulation): the interrupted command's own error line is expected
        errs = [e for e in errs if not FOREGROUND.search(e.split('] ', 1)[-1])]
    lines = out.rstrip('\n').split('\n')
    rec.update(status='OK' if (rc == 0 or ended_by != 'itself') and not errs else 'FAIL', exit=rc,
               ended_by=ended_by, seconds=round(time.time() - s['sess']['t0'], 1), inner_errors=errs[:10],
               output_tail='\n'.join(lines[:15] + (['[... %d lines ...]' % (len(lines) - 40)] if len(lines) > 40 else [])
                                     + lines[max(15, len(lines) - 25):]),
               check=check_match(s['expected'], out), expected=s['expected'][:15], log_lines=len(lines))
    rec['_full_log'] = out
    print(f'{rec["chapter"]}:{rec["line"]:<5} bash  {rec["status"]:<8} background ended ({ended_by}) rc={rc}', flush=True)


def end_sessions(box, ctx, kinds, how):
    for s in list(ctx.sessions):
        if s['kind'] not in kinds:
            continue
        if s['kind'] in ('fg', 'node'):
            rc, sig = box.stop_bg(s['sess'], 'INT')
            finish_bg(box, s, f'SIG{sig} to the process group ({how})', rc)
        else:
            rc = box.wait_bg(s['sess'], BLOCK_TIMEOUT)
            if rc is None:
                rc, sig = box.stop_bg(s['sess'], 'INT')
                finish_bg(box, s, f'SIG{sig} after {BLOCK_TIMEOUT} s', rc)
            else:
                finish_bg(box, s, 'itself', rc)
        ctx.sessions.remove(s)


def run_chapter(ch, box, use_mac, fs, plan, dry=False, ctx=None):
    ctx = ctx or Ctx()
    md = glob.glob(os.path.join(CHAPTERS, f'{ch}-*.md'))
    if not md:
        sys.exit(f'no chapter {ch}')
    blocks = parse(md[0])
    recs, t_ch = [], time.time()
    partner = False               # this block is the one that runs while the previous one runs
    for bi, blk in enumerate(blocks):
        host, lang = blk['host'], blk['lang']
        first = next((l for l in blk['code'].split('\n') if l.strip()), '')
        base = dict(chapter=ch, line=blk['line'], section=blk['section'], host=host or '-', lang=lang or '-',
                    first_line=first[:120])
        if lang in NONCODE_LANGS or host in ('', 'board', 'any'):
            continue
        who = 'robot' if host == 'robot' else 'mac' if host == 'mac' else None
        override = plan_override(plan, ch, blk, first)
        if who is None or (who == 'mac' and not use_mac):
            recs.append(dict(base, kind='skip', status='SKIPPED-OTHERHOST', reason=f'@{host} block'))
            continue
        if lang in FILE_LANGS:
            if dry:
                act, path, info = decide_file(blk, who, fs, override)
                recs.append(dict(base, kind='file', status='DRY', action=act, path=path, info=info))
                print(f'{ch}:{blk["line"]:<5} file  {act:<12} {path or ""} | {info} || {blk["prose"][-90:]}', flush=True)
                continue
            if ctx.approve is not None:
                preview = override['action'] if override else file_action(blk, who, fs)[0]
                if preview != 'SNIPPET':
                    verdict, cls, why = ctx.approve(ch, blk, who, 'file', preview)
                    if verdict != 'run':
                        act, path, info = decide_file(blk, who, fs, override)
                        recs.append(dict(base, kind='file', status=GO_STATUS[verdict], action=act, path=path, info=info,
                                         safety_class=cls, reason=why))
                        print(f'{ch}:{blk["line"]:<5} file  {GO_STATUS[verdict]} {act} {path or ""}', flush=True)
                        if verdict == 'quit':
                            break
                        continue
            st, rec = apply_file(box, blk, who, fs, override)
            recs.append(dict(base, kind='file', status=st, **rec))
            print(f'{ch}:{blk["line"]:<5} file  {st:<8} {rec.get("action")} {rec.get("path") or ""}', flush=True)
            continue
        if lang not in ('bash', 'sh', 'shell'):
            recs.append(dict(base, kind='skip', status='SKIPPED-OTHER', reason=f'language {lang}'))
            continue
        code = blk['code']
        if not dry and ctx.sessions:
            if CTRL_C.search(blk['prose']):                  # "Stop the launch with Ctrl-C ..."
                end_sessions(box, ctx, ('fg', 'node'), 'the guide says Ctrl-C')
            if not partner:                                   # a self-ending background block finishes first
                end_sessions(box, ctx, ('self',), 'next block')
                end_sessions(box, ctx, ('node',), 'next block (the guide stops the node with Ctrl-C)')
        nxt = next((b for b in blocks[bi + 1:] if b['lang'] not in NONCODE_LANGS and b['host'] not in ('', 'board', 'any')),
                   None)
        action, reason, extra = classify_bash(blk, who, first, override, ctx, None if dry else box,
                                              nxt['prose'] if nxt else '')
        partner = False
        rec = dict(base, kind='bash', who=who)
        if dry:
            print(f'{ch}:{blk["line"]:<5} bash  {BASH_ACTIONS[action]:<20} {"[plan] " if override else ""}'
                  f'{(reason + " " + str(extra or "")).strip()[:90]} | {first[:60]}', flush=True)
            continue
        if action != 'RUN':
            rec.update(status=BASH_ACTIONS[action], reason=reason)
            if action == 'DESTRUCTIVE' and reason.startswith('reboot') and ctx.fake_systemd and ctx.sysd:
                # the block is not run; the fake systemd reboots instead (what the next blocks read afterwards)
                end_sessions(box, ctx, ('self', 'fg', 'node'), 'reboot')
                rb = ctx.sysd.reboot(box)
                rb['boot']['why'] = f'reboot block line {blk["line"]}'
                ctx.sysd_boots.append(rb['boot'])
                box.root('rm -rf /home/burgerbarn/.sim_state')          # the ssh session ended with the reboot
                rec['sim_reboot'] = (f'fake systemd rebooted: {rb["shutdown"]["seconds"]} s shutdown, new boot '
                                     f'{rb["boot"]["boot"]}, started {", ".join(rb["boot"]["started"]) or "nothing"}; '
                                     f'robot shell state reset (new ssh session)')
                print(f'{ch}:{blk["line"]:<5} bash  {rec["status"]} + fake systemd reboot', flush=True)
                recs.append(rec)
                continue
            prefix = []
            if action == 'HW' and not override:      # setup lines before the first hardware line still run
                for l in code.split('\n'):
                    if first_match(HARDWARE, l) or FOREGROUND.match(l) or not is_setup_line(l):
                        break
                    prefix.append(l)
            if prefix and ctx.approve is not None:
                verdict, cls, why = ctx.approve(ch, blk, who, 'bash', '\n'.join(prefix))
                if verdict != 'run':
                    rec.update(prefix_not_run=prefix, prefix_verdict=GO_STATUS[verdict])
                    prefix = []
                    if verdict == 'quit':
                        recs.append(rec)
                        break
            if prefix:
                rc, out, dt = box.run_block(who, '\n'.join(prefix), f'{ch}-{blk["line"]}-prefix')
                rec.update(prefix_ran=prefix, prefix_exit=rc, output_tail=tail(out), seconds=round(dt, 1))
            recs.append(rec)
            print(f'{ch}:{blk["line"]:<5} bash  {rec["status"]} {reason[:70]}'
                  + (f' (prefix rc={rec["prefix_exit"]})' if prefix else ''), flush=True)
            continue
        if reason:
            rec['reason'] = reason
        if extra.get('drop_lines'):
            rec['hw_lines_dropped'] = extra['drop_lines']
            code = '\n'.join(('# [sim] dropped (no USB devices / no services in the container): ' + l)
                             if l in extra['drop_lines'] else l
                             for l in code.split('\n'))
        if ctx.approve is not None:
            verdict, cls, why = ctx.approve(ch, blk, who, 'bash', code)
            rec['safety_class'] = cls
            if verdict != 'run':
                rec.update(status=GO_STATUS[verdict], reason=why)
                recs.append(rec)
                print(f'{ch}:{blk["line"]:<5} bash  {rec["status"]} {why[:70]}', flush=True)
                if verdict == 'quit':
                    break
                continue
        if extra.get('background'):
            sess = box.start_bg(who, code, f'{ch}-{blk["line"]}')
            if sess is None:
                recs.append(dict(rec, status='FAIL', error='could not start the background session'))
                continue
            rec.update(status='RUNNING', background=extra['background'])
            recs.append(rec)
            s = {'sess': sess, 'rec': rec, 'kind': extra['background'], 'expected': blk['check'],
                 'base_launch': extra['background'] == 'fg'}
            ctx.sessions.append(s)
            if extra['background'] in ('fg', 'node'):   # wait for the launch/node to start (builds first), then settle
                end = time.time() + 300
                while (time.time() < end and LAUNCH_STARTED not in box.bg_log(sess)
                       and not NODE_STARTED.search(box.bg_log(sess)) and box.bg_done(sess) is None):
                    time.sleep(0.5)
                time.sleep(SETTLE_LAUNCH_S)
            else:
                time.sleep(SETTLE_SELF_S)
            print(f'{ch}:{blk["line"]:<5} bash  BACKGROUND ({extra["background"]}) {first[:60]}', flush=True)
            if box.bg_done(sess) is not None:        # ended already (failed to start?)
                end_sessions(box, ctx, (extra['background'],), 'ended early')
            else:
                partner = True
            continue
        rc, out, dt = box.run_block(who, code, f'{ch}-{blk["line"]}')
        errs = [e for e in re.findall(r'\[sim-err rc=\d+\][^\n]*', out) if not timeout_ok(e)]
        rec.update(status='OK' if (rc == 0 or (rc == 124 and timeout_ok(f'[sim-err rc=124] {code.strip().splitlines()[-1]}')))
                   and not errs else 'FAIL', exit=rc, seconds=round(dt, 1),
                   inner_errors=errs[:10], output_tail=tail(out, 40), check=check_match(blk['check'], out),
                   expected=blk['check'][:15])
        recs.append(rec)
        print(f'{ch}:{blk["line"]:<5} bash  {rec["status"]:<8} rc={rc} {dt:5.1f}s check={rec["check"]} {first[:60]}', flush=True)
    if not dry and ctx.sessions:
        end_sessions(box, ctx, ('self',), 'chapter end')
        end_sessions(box, ctx, ('fg', 'node'), 'chapter end')
    return recs, time.time() - t_ch


def fake_board_md(fb):
    """Short summary of what the fake board sent and received during a chapter."""
    if not fb or fb.get('error'):
        return f'\n## Fake board\n\n{(fb or {}).get("error", "not running")}\n'
    out = ['', '## Fake board', '',
           f'- port {fb.get("link")} -> {fb.get("pts")}; opened {fb.get("opens")} times, closed {fb.get("closes")}',
           f'- sent: {fb.get("tx_frames")} ({fb.get("tx_bytes")} bytes, {fb.get("tx_dropped_bytes")} dropped)',
           f'- received: {fb.get("rx_frames")}, CRC errors {fb.get("rx_crc_errors")}, unknown {fb.get("unknown_frames")}',
           f'- motor frames {fb.get("motor_frames")}, with a nonzero speed {fb.get("motor_nonzero_frames")}, '
           f'max |rps| {fb.get("motor_max_abs_rps")}, last {fb.get("last_motor_rps")}',
           f'- buzzer frames: {fb.get("buzzer_frames")}',
           f'- servo: moves {fb.get("servo_moves")}, holds {fb.get("servo_holds")}, torque off {fb.get("servo_torque_off")}, '
           f'reads {fb.get("servo_reads")}; positions now {fb.get("servo_pos")}']
    for name, lst in (fb.get('rx_samples') or {}).items():
        for x in lst:
            out.append(f'- rx sample {name}: `{x["hex"]}`')
    return '\n'.join(out) + '\n'


def write_reports(ch, recs, secs, fb=None):
    os.makedirs(RESULTS, exist_ok=True)
    for r in recs:                               # full logs of background sessions: own files
        log = r.pop('_full_log', None)
        if log is not None:
            p = os.path.join(RESULTS, f'{ch}-line{r["line"]}.log')
            open(p, 'w').write(log)
            r['full_log'] = os.path.relpath(p, HERE)
    if fb is not None:
        for key, suffix in (('_rx_jsonl', 'fakeboard-rx.jsonl'), ('_events_jsonl', 'fakeboard-events.jsonl')):
            if key in fb:
                open(os.path.join(RESULTS, f'{ch}.{suffix}'), 'w').write(fb.pop(key))
        json.dump(fb, open(os.path.join(RESULTS, f'{ch}.fakeboard.json'), 'w'), indent=1)
    with open(os.path.join(RESULTS, f'{ch}.jsonl'), 'w') as f:
        for r in recs:
            f.write(json.dumps(r) + '\n')
    cnt = {}
    for r in recs:
        key = r['status'] if r['kind'] != 'file' else ('FILE-' + r['status'] if r['status'] != 'SNIPPET' else 'SNIPPET')
        cnt[key] = cnt.get(key, 0) + 1
    with open(os.path.join(RESULTS, f'{ch}.md'), 'w') as f:
        f.write(f'# {ch} in lightbot-sim\n\nRun {time.strftime("%Y-%m-%d %H:%M:%S %Z")}, {secs:.0f} s'
                + (', fake board ON' if fb is not None else '') + '. Counts: '
                + ', '.join(f'{k} {v}' for k, v in sorted(cnt.items())) + '\n\n')
        for r in recs:
            f.write(f'## line {r["line"]}: {r["status"]} ({r["kind"]} @{r["host"]})\n\n')
            f.write(f'- section: {r["section"]}\n- first line: `{r["first_line"]}`\n')
            for k in ('reason', 'action', 'path', 'info', 'background', 'ended_by', 'exit', 'seconds', 'check', 'error',
                      'pieces_equal_complete', 'pieces_equal_ignoring_blank_lines', 'inserted_after_line',
                      'prefix_exit', 'hw_lines_dropped', 'full_log', 'sim_reboot'):
                if r.get(k) is not None:
                    f.write(f'- {k}: {r[k]}\n')
            if r.get('inner_errors'):
                f.write('- failing commands inside the block:\n' + ''.join(f'  - `{e}`\n' for e in r['inner_errors']))
            if r.get('pieces_diff'):
                f.write('\n```diff\n' + r['pieces_diff'] + '```\n')
            if r.get('output_tail'):
                f.write('\n```text\n' + r['output_tail'] + '\n```\n')
            if r.get('status') == 'FAIL' and r.get('expected'):
                f.write('\nexpected (guide check):\n\n```text\n' + '\n'.join(r['expected']) + '\n```\n')
            f.write('\n')
        if fb is not None:
            f.write(fake_board_md(fb))
    return cnt


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('chapters', nargs='*')
    ap.add_argument('--target', choices=('sim', 'robot'), default='sim',
                    help='sim = the container (default); robot = the real robot (only --dry-run, or --go chNN)')
    ap.add_argument('--go', metavar='chNN', help='run this one chapter, asking on stdin before every block that is not '
                    'read-only by the gate; never-class blocks are refused')
    ap.add_argument('--fresh', action='store_true', help='start from an empty home volume')
    ap.add_argument('--no-mac', action='store_true', help='skip @mac blocks instead of emulating the laptop')
    ap.add_argument('--dry-run', action='store_true', help='print the decision for every block; run nothing')
    ap.add_argument('--fake-board', action='store_true',
                    help='start the fake controller board (/dev/ttyACM0) before each chapter and run the board steps')
    ap.add_argument('--fake-lidar', action='store_true',
                    help='start the fake LiDAR (/dev/lidar -> ttyUSB0 -> pty) before each chapter and run the LiDAR '
                    'steps; own container/volume (-fakelidar) and results/fakelidar/')
    ap.add_argument('--fake-systemd', action='store_true',
                    help='boot a fake systemd (fakesystemd/) before each chapter and run the systemctl/journalctl '
                    'blocks; own container/volumes (-fakesystemd) and results/fakesystemd/')
    ap.add_argument('--arch', choices=sorted(ARCHES), default='amd64',
                    help='container architecture: amd64 (native; image lightbot-sim:humble, volume lightbot-sim-home, '
                    'results/) or arm64 (the Jetson\'s, emulated by qemu; image lightbot-sim:humble-arm64, volume '
                    'lightbot-sim-home-arm64, results/arm64/, block timeouts x4)')
    ap.add_argument('--results', help='results folder (default results/ for amd64, results/arm64/ for arm64; '
                    'fakeboard/ below it with --fake-board)')
    a = ap.parse_args()
    if a.go:
        if a.dry_run:
            ap.error('--go and --dry-run exclude each other')
        if a.chapters and a.chapters != [a.go]:
            ap.error(f'--go {a.go} runs exactly one chapter; drop the other chapter arguments')
        a.chapters = [a.go]
    if not a.chapters:
        ap.error('name at least one chapter')
    global RESULTS, ARCH, IMAGE, VOLUME, BOX, BLOCK_TIMEOUT
    ARCH = a.arch
    IMAGE, VOLUME, BOX, RESULTS = (ARCHES[ARCH][k] for k in ('image', 'volume', 'box', 'results'))
    BLOCK_TIMEOUT = 900 * ARCHES[ARCH]['timeout_scale']
    plan = load_plan()
    if a.target == 'robot' or a.go:
        sys.modules.setdefault('run_chapter', sys.modules[__name__])   # robot_target imports this module by name
        import robot_target
    if a.target == 'robot':
        if a.fresh or a.fake_board or a.fake_lidar or a.fake_systemd or a.arch != 'amd64':
            ap.error('--fresh / --fake-board / --arch are container options')
        robot_target.main_robot(a, plan)
        return
    RESULTS = a.results or (os.path.join(RESULTS, 'fakesystemd') if a.fake_systemd else
                            os.path.join(RESULTS, 'fakelidar') if a.fake_lidar else
                            os.path.join(RESULTS, 'fakeboard') if a.fake_board else RESULTS)
    if a.fake_lidar:                       # own container + home volume: never touches the other runs
        VOLUME, BOX = VOLUME + '-fakelidar', BOX + '-fakelidar'
        sys.path.insert(0, os.path.join(HERE, 'fakelidar'))
        import runner_hooks as fake_lidar
    sysd = None
    if a.fake_systemd:                     # own container + volumes (also when combined with --fake-lidar)
        VOLUME, BOX = ARCHES[ARCH]['volume'] + '-fakesystemd', ARCHES[ARCH]['box'] + '-fakesystemd'
        import importlib.util
        spec = importlib.util.spec_from_file_location('fakesystemd_hooks',
                                                      os.path.join(HERE, 'fakesystemd', 'runner_hooks.py'))
        sysd = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(sysd)
        ACTIVE_FLAGS.add('fake-systemd')
        Box.extra_args = ['--init', '-v', f'{VOLUME}-systemd:/var/lib/fakesystemd']
    fs = FileState()
    ctx = Ctx(fake_board=a.fake_board, fake_lidar=a.fake_lidar, fake_systemd=a.fake_systemd,
              approve=robot_target.sim_approver(robot_target.make_ask(robot=False)) if a.go else None)
    if a.dry_run:
        for ch in a.chapters:
            run_chapter(ch, None, not a.no_mac, fs, plan, dry=True, ctx=ctx)
        return
    if a.fresh:
        sh(['docker', 'rm', '-f', BOX])
        sh(['docker', 'volume', 'rm', VOLUME])
        if a.fake_systemd:
            sh(['docker', 'volume', 'rm', VOLUME + '-systemd'])
    box = Box(BOX)
    box.start()
    if sysd:
        sysd.setup(box)
        ctx.sysd = sysd
    t0 = time.time()
    try:
        for ch in a.chapters:
            box.root('rm -rf /home/burgerbarn/.sim_state* /home/mac/.sim_state*')   # new ssh sessions per chapter
            fb = None
            if a.fake_board and not box.start_fake_board():
                sys.exit('the fake board did not come up: ' + box.root('cat /tmp/fakeboard/board.out')[1])
            if a.fake_lidar and not fake_lidar.start(box):
                sys.exit('the fake LiDAR did not come up: ' + box.root('cat /tmp/fakelidar/lidar.out')[1])
            if sysd:
                t_sysd = time.time()
                b = sysd.boot(box)
                b['why'] = 'chapter start'
                ctx.sysd_boots = [b]
                print(f'== {ch}: fake systemd boot {b["boot"]}: started {", ".join(b["started"]) or "nothing"}',
                      flush=True)
            recs, secs = run_chapter(ch, box, not a.no_mac, fs, plan, ctx=ctx)
            if sysd:
                sst = dict(boots=ctx.sysd_boots, units_at_end=sysd.snapshot(box))
                sst['shutdown'] = sysd.shutdown(box)
                jtext = sysd.journal(box, t_sysd)
            if a.fake_board:
                time.sleep(1.2)                   # the board writes its status once a second
                fb = box.fake_board_report()
                box.stop_fake_board()
            if a.fake_lidar:
                time.sleep(1.2 if not a.fake_board else 0)
                fl = fake_lidar.report(box)
                fake_lidar.stop(box)
            cnt = write_reports(ch, recs, secs, fb)
            if a.fake_lidar:
                fake_lidar.write(RESULTS, ch, fl)
            if sysd:
                sysd.write(RESULTS, ch, sst, jtext)
            print(f'== {ch}: {secs:.0f} s, ' + ', '.join(f'{k} {v}' for k, v in sorted(cnt.items())), flush=True)
    finally:
        box.stop()
    unused = [(ch, e.get('first_line') or e.get('section')) for ch in a.chapters for e in plan.get(ch, [])
              if not e.get('_used') and not ('only' in e and e['only'] not in ACTIVE_FLAGS)
              and not ('unless' in e and e['unless'] in ACTIVE_FLAGS)]
    if unused:
        print('plan.json entries that matched no block:', unused, flush=True)
    print(f'== total {time.time() - t0:.0f} s', flush=True)


if __name__ == '__main__':
    main()
