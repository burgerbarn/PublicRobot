#!/usr/bin/env python3
"""The real-robot target of run_chapter.py, and the safety classes every guide block gets.

Used by run_chapter.py (`--target robot`, `--go chNN`); not run by itself except for `--selftest`.

Safety classes (one per block, decided by code, first match wins):
  never    refused outright, also with --go: erase/mkfs, partition-table or partition-name change, boot entries,
           flashing, reboot/poweroff, package removal (apt remove/purge/autoremove), the golden sets
           (~/golden, ~/rosorin-golden), anything on the server (192.168.1.122) - the owner's stop rules
  motion   wheels or arm can move (stand/floor tests, cmd_vel/joy publishers, arm moves, enable, goals): owner at the robot
  hands    a hands-on step (stand, gamepad, cables, buttons, power, looking at or listening to the robot), or a
           foreground program that runs until Ctrl-C in a terminal
  listing  a script listing shown for reading (first line #!..., or a plan.json LISTING/PATH entry): not run
  auto     every logical line AND the whole block pass readonly_gate.check (the only judge of "read-only"),
           and no known side effect (below)
  unguarded  passes the gate, but a laptop/bigbuddy block runs git/python/ros2 on the robot through ssh, where the
           write guard (below) does not reach. Not run.
  ros2cli  passes the gate, but runs the ros2 CLI (topic/node/service/param/action/doctor/run/launch, tf2_echo):
           measured 2026-10-14 in the container, `ros2 topic list` writes ~/.ros/log/python3_<pid>_<ms>.log and
           spawns the ros2 daemon (a process that stays). Not run on the robot without the owner.
  owner    changes the system (guide_plan.OWNER: sudo, apt, systemctl start/stop, /etc, ...)
  home     writes below the user's home (builds, files, scripts)
  file     a file block the runner would write (Create/Add/complete file)
  snippet  code shown for reading (no file named)
  server   an @server block
  text     no host or a non-code block (@any, @board, text, mermaid)

--target robot --dry-run executes ONLY class `auto` blocks (robot blocks over ssh, bigbuddy blocks locally, laptop
blocks on bigbuddy with the translation recorded) and reads the robot's copies of complete files for a drift check
(`cat`, gate-checked). Every executed command is appended to results/robot/commands.log. The remote shell gets
PYTHONDONTWRITEBYTECODE=1, GIT_OPTIONAL_LOCKS=0 and an unwritable ROS_LOG_DIR, so a python import, a git status or a
ros2 command that slipped through cannot write a .pyc, the git index or a ROS log (the ros2 command fails instead).
--go chNN asks on stdin before every block that is not `auto` and refuses `never`; on the robot it needs a terminal
and the word `yes`.
"""
import hashlib
import json
import os
import re
import shlex
import subprocess
import sys
import time

import run_chapter as rc

HERE = rc.HERE


def _gate_dir():
    for d in (os.environ.get('LIGHTBOT_GATE_DIR'), os.path.join(HERE, '..', 'runner'),
              os.path.join(rc.REPO, 'brain', 'runner')):
        if d and os.path.isfile(os.path.join(d, 'readonly_gate.py')) and os.path.isfile(os.path.join(d, 'guide_plan.py')):
            return os.path.abspath(d)
    sys.exit('readonly_gate.py / guide_plan.py not found (set LIGHTBOT_GATE_DIR)')


GATE_DIR = _gate_dir()
sys.path.insert(0, GATE_DIR)
import readonly_gate   # noqa: E402  the only judge of "read-only"
import guide_plan      # noqa: E402  OWNER / NEVER lists

GATE_SHA = hashlib.sha256(open(os.path.join(GATE_DIR, 'readonly_gate.py'), 'rb').read()).hexdigest()[:16]

ROBOT_SSH = 'rosorin-wifi'
SERVER_IP = '192.168.1.122'
GUARD_ENV = 'export PYTHONDONTWRITEBYTECODE=1 GIT_OPTIONAL_LOCKS=0 ROS_LOG_DIR=/proc/lightbot-dry-run/no-ros-logs'

# ---------------------------------------------------------------- stop rules (the owner's, enforced here)
_MOTION_IN_GP_NEVER = {r"\bros2\s+topic\s+pub\b.*cmd_vel", r"arm/(enable|move)|/enable\b.*true"}
NEVER = [(p, 'guide_plan.NEVER ' + p) for p in guide_plan.NEVER if p not in _MOTION_IN_GP_NEVER] + [
    (r'\b(mkfs(\.\w+)?|mkswap|wipefs|blkdiscard|e2label|tune2fs|resize2fs|sfdisk|cfdisk|partprobe|cryptsetup)\b',
     'erase / filesystem / partition tool'),
    (r'\bsgdisk\b[^\n|;&]*\s-(c|n|d|t|o|Z|z|g|e|x|A|C|G|N|U|u|r|s|j|m|M|R|S|T)\b|\bsgdisk\b[^\n|;&]*--(change-name|new|delete|zap)',
     'partition table / partition name change'),
    (r'\bdd\b[^|;\n]*\bof=', 'dd writing a device or file'),
    (r'\b(flash\.sh|l4t_initrd_flash\.sh|nvautoflash|nvbootctrl|flash_erase)\b', 'flashing / boot slots'),
    (r'\befibootmgr\b[^\n|;&]*\s-(c|b|B|o|n|N|a|A|L|l|d|p|t|T|D|w)\b|\bextlinux\b|/boot/', 'boot entry'),
    (r'(^|[;&|(!{]\s*|\bsudo\s+|\bthen\s+|\bdo\s+)(reboot|poweroff|halt|shutdown|kexec|telinit|init\s+[06])\b'
     r'|\bsystemctl\b[^\n|;&]*\b(reboot|poweroff|halt|kexec|isolate|rescue|emergency|soft-reboot)\b', 'reboot / poweroff'),
    (r'\bapt(-get)?\b[^\n|;&]*\b(remove|purge|autoremove|autopurge)\b|\bdpkg\b[^\n|;&]*\s(-r|-P|--remove|--purge)\b',
     'package removal (apt remove/purge/autoremove)'),
    (r'(~|\$HOME|/home/burgerbarn)/golden\b|rosorin-golden', 'a golden set (never touched)'),
    (r'\b(mount|umount)\b[^\n]*/dev/|\bumount\b', 'mounting / unmounting a disk'),
    (r'192\.168\.1\.122|\bssh\s+(-\S+\s+)*server\b|\bwiki_sync\.sh\b', 'the server (only the wiki copy, outside this runner)'),
]
_RUNS = r'(?:^\s*|[;&|(]\s*|\btimeout\s+\d+[smh]?\s+|\bpython3\s+(?:-\S+\s+)*|\bbash\s+|\bros2\s+(?:run|launch)\s+\S+\s+)\S*'
MOTION_CODE = [   # executions only (copying motion_test.py is not motion)
    (r'\bros2\s+topic\s+pub\b[^\n]*(cmd_vel|/joy\b|arm|servo|motor|enable|wheel|goal|estop|e_stop)', 'publishes a motion/arm/enable topic'),
    (r'\bros2\s+service\s+call\b[^\n]*(enable|arm/(?!read_state)|servo|motor|move|drive|recover|home|tuck)', 'calls a motion/arm service'),
    (r'\bros2\s+action\s+send_goal\b', 'sends a navigation/arm goal'),
    (_RUNS + r'(motion_test|stop_proof|stop_proof_floor|stand_proof|odom_test|floor_odom_test|odom_truth)(\.py)?\b',
     'stand / floor motion test'),
    (_RUNS + r'(arm_jog_test|moveit_named_move)(\.py)?\b', 'arm motion'),
    (_RUNS + r'rrc_send\.py\b', 'sends frames to the board (motors, servos, buzzer)'),
    (_RUNS + r'(explore_run\.sh|nav_supervisor|explore_lite|explore\.launch|\w*teleop\w*)', 'autonomous driving / teleoperation'),
    (r'\bcurl\b[^\n]*/(move|drive|arm|enable|skill|goto|go_to|nav|cmd)\b', 'robot API call that can move it'),
]
MOTION_PROSE = re.compile(r'\bwheels? (turn|turns|spin|spins|move|moves|drive)\b|\bthe arm (moves|will move)\b|'
                          r'\bfirst (wheel|arm) motion\b|\bon the floor\b', re.I)
HANDS_PROSE = re.compile(   # instructions to a person, in the sentences right before the block
    r'\b(put|place|set|lift|carry|turn) the robot\b|\bon (the|its) stand\b|\b[Pp]ress (the|a|any|and hold|Enter|it|its|'
    r'START|SELECT|MODE|[LR]\d)\b|\b(un|re)?plug (the|it|in|a|an)\b|\b(dis)?connect the [\w -]{0,20}cable\b|'
    r'\b(move|push|tilt) the (left |right )?(stick|joystick)\b|\bon the gamepad\b|\bgamepad (on|off)\b|'
    r'\bpower switch\b|\b(switch|turn) (it|the robot|the board|the gamepad) (on|off)\b|'
    r'\b(look|looking) at the (robot|wheels|arm|LED|screen)\b|\bwatch the (robot|wheels|arm|LED|screen)\b|'
    r'\blisten for\b|\bunder the robot\b', re.I)
HANDS_SECTION = re.compile(r'\bon the stand\b|\bon the floor\b|\bgamepad\b|\bfirst enable\b', re.I)
HANDS_CODE = [(r'\bjstest\b|\bevtest\b|/dev/input/', 'reads the gamepad (someone presses it)'),
              (r'^\s*read\s+(-\w+\s+)*', 'waits for someone to press Enter')]
ROS2CLI = re.compile(r'\bros2\s+(topic|node|service|param|action|lifecycle|component|doctor|run|launch|bag|wtf)\b|tf2_echo|view_frames')
SSH_TO_ROBOT = re.compile(r'\b(ssh|scp|rsync)\b[^\n]*\b(rosorin(-wifi|-wired)?|192\.168\.1\.10[89])\b')
UNGUARDED_REMOTE = re.compile(r'\b(git|python3?|ros2)\b')   # writers the guard env neutralises, but not on an ssh hop


def lead_in(prose, n=260):
    """The sentence(s) right before a block (the end of the prose since the previous block)."""
    return prose[-n:]


def logical_lines(code):
    """Non-empty, non-comment lines, with backslash continuations joined (what bash runs as one line)."""
    out, cur = [], ''
    for l in code.split('\n'):
        if cur:
            cur += ' ' + l.strip()
        else:
            cur = l
        if cur.rstrip().endswith('\\'):
            cur = cur.rstrip()[:-1]
            continue
        s = cur.strip()
        if s and not s.startswith('#'):
            out.append(s)
        cur = ''
    if cur.strip() and not cur.strip().startswith('#'):
        out.append(cur.strip())
    return out


def gate_block(code):
    """-> (ok, why, per_line) where ok only if every logical line AND the whole block pass readonly_gate.check."""
    lines = logical_lines(code)
    per = [(l, *readonly_gate.check(l)) for l in lines]
    if not lines:
        return False, 'no command lines', per
    bad = next((p for p in per if not p[1]), None)
    if bad:
        return False, f'gate refuses line `{bad[0][:80]}`: {bad[2]}', per
    ok, why = readonly_gate.check('\n'.join(lines))      # the whole block, continuations joined as bash joins them
    if not ok:
        return False, f'gate refuses the whole block: {why}', per
    return True, 'every line passes readonly_gate.check', per


def _first(rules, text):
    for pat, why in rules:
        m = re.search(pat, text, re.M)
        if m:
            return why, m.group(0).strip()[:60]
    return None


def classify(blk, code=None, plan_action=None):
    """-> (cls, why). blk: a run_chapter.parse() block. code: the code that would run (after host translation).
    plan_action: a plan.json action for this block (LISTING/PATH make it a listing; HW/RUN are sim-only, ignored)."""
    host, lang = blk['host'], blk['lang']
    code = blk['code'] if code is None else code
    if lang in rc.NONCODE_LANGS or host in ('', 'board', 'any'):
        return 'text', f'@{host or "-"} {lang or "-"} block (no host to run it on)'
    if host == 'server':
        return 'server', '@server block: outside this runner (owner rule: nothing on the server except the wiki copy)'
    if lang in rc.FILE_LANGS:
        return 'file', 'file block'
    if lang not in ('bash', 'sh', 'shell'):
        return 'text', f'language {lang}'
    text = code + '\n' + blk['code']
    n = _first(NEVER, text)
    if n:
        return 'never', f'{n[0]} ({n[1]})'
    first = next((l for l in code.split('\n') if l.strip()), '')
    if plan_action in ('LISTING', 'PATH'):
        return 'listing', f'plan.json {plan_action}'
    if first.startswith('#!'):
        return 'listing', 'script listing (shown for reading; the guide runs the script file)'
    m = _first(MOTION_CODE, '\n'.join(logical_lines(code) + logical_lines(blk['code'])))
    if m:
        return 'motion', f'{m[0]} ({m[1]})'
    li = lead_in(blk['prose'])
    mp = MOTION_PROSE.search(li)
    if mp and not gate_block(code)[0]:
        return 'motion', f'the text before it says the robot moves ({mp.group(0)})'
    h = _first(HANDS_CODE, code)
    if h:
        return 'hands', f'{h[0]} ({h[1]})'
    hp = HANDS_PROSE.search(li)
    if hp:
        return 'hands', f'hands-on step: the text before it says `{hp.group(0)}`'
    if HANDS_SECTION.search(blk['section']):
        return 'hands', f'hands-on section ({blk["section"][:60]})'
    fg = [l for l in code.split('\n') if rc.FOREGROUND.match(l) and not l.lstrip().startswith('timeout')]
    if fg:
        return 'hands', f'runs in the foreground until Ctrl-C in a terminal ({fg[0].strip()[:50]})'
    ok, why, _ = gate_block(code)
    if ok:
        if ROS2CLI.search(code):
            return 'ros2cli', ('passes the gate, but the ros2 CLI writes ~/.ros/log/*.log and spawns the ros2 daemon '
                               '(measured 2026-10-14): not run on the robot without the owner')
        if SSH_TO_ROBOT.search(code) and host != 'robot' and UNGUARDED_REMOTE.search(code):
            return 'unguarded', 'passes the gate, but runs git/python/ros2 on the robot through ssh without the write guard'
        return 'auto', why
    body = blk['code']
    if any(re.search(p, body) for p in guide_plan.OWNER):
        return 'owner', 'changes the system (guide_plan.OWNER); ' + why
    return 'home', 'writes in the home folder or runs programs; ' + why


# ---------------------------------------------------------------- host translation
def translate(host, code):
    """-> (exec_host, code_to_run, translation note). exec_host: robot | bigbuddy | None."""
    if host == 'robot':
        return 'robot', code, f'@robot -> ssh -o BatchMode=yes {ROBOT_SSH} bash -lc'
    if host == 'bigbuddy':
        return 'bigbuddy', code, '@bigbuddy -> local bash -lc on bigbuddy'
    if host == 'mac':
        new = code.replace('~/CCode/rosorin-pro', rc.REPO).replace('$HOME/CCode/rosorin-pro', rc.REPO)
        new = re.sub(r'\b(ssh|scp|rsync)\b([^\n]*)', lambda m: m.group(1) + re.sub(
            r'(?<![\w.-])(rosorin-wired|rosorin|192\.168\.1\.109)(?=[\s:\'"]|$)', ROBOT_SSH, m.group(2)), new)
        notes = ['@mac -> local bash -lc on bigbuddy']
        if '~/CCode/rosorin-pro' in code or '$HOME/CCode/rosorin-pro' in code:
            notes.append(f'~/CCode/rosorin-pro -> {rc.REPO}')
        if new != code and SSH_TO_ROBOT.search(code):
            notes.append(f'ssh/scp/rsync host rosorin -> {ROBOT_SSH}')
        return 'bigbuddy', new, '; '.join(notes)
    return None, code, f'@{host}: never run'


# ---------------------------------------------------------------- executors
class Approval:
    """Given by an ask() for one exact block; RobotHost.run(mode='approved') checks it."""
    def __init__(self, code):
        self.sha = hashlib.sha256(code.encode()).hexdigest()


class ShellState:
    def __init__(self):
        self.pwd, self.env = None, ''


MARK = '__LIGHTBOT_STATE_7f3a__'
_ENV_SKIP = re.compile(r'^declare -[a-zA-Z-]* (BASHOPTS|SHELLOPTS|BASH_VERSINFO|EUID|PPID|UID|_|OLDPWD|PWD|SHLVL|'
                       r'SSH_\w+|PYTHONDONTWRITEBYTECODE|GIT_OPTIONAL_LOCKS|ROS_LOG_DIR)=')


def build_script(code, st, guard):
    parts = []
    if st.env:
        parts.append(st.env)
    if st.pwd:
        parts.append(f'cd {shlex.quote(st.pwd)} 2>/dev/null || cd "$HOME"')
    if guard:
        parts.append(GUARD_ENV)
    parts += ["trap 'echo \"[err rc=$?] $BASH_COMMAND\" >&2' ERR",
              '__code=' + shlex.quote(code), 'eval "$__code"', '__rc=$?', 'trap - ERR',
              f'echo; echo {MARK}; pwd; export -p', 'exit $__rc']
    return '\n'.join(parts)


class Executor:
    """Runs blocks on the robot (ssh) or on bigbuddy (local bash). In dry-run mode it refuses anything that is not
    read-only by the gate; in go mode it needs an Approval for every non-read-only block. Logs every command."""

    def __init__(self, log_path, allow_approved=False, ssh_cmd=None, local_cmd=None):
        self.log_path = log_path
        self.allow_approved = allow_approved
        self.ssh_cmd = ssh_cmd or ['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=10', '-T', ROBOT_SSH]
        self.local_cmd = local_cmd or ['bash', '-c']
        self.state = {'robot': ShellState(), 'bigbuddy': ShellState()}
        self.sent = []
        self.file_cache = {}        # dry-run drift reads: nothing on the robot changes, read each file once

    def log(self, **kw):
        kw['t'] = time.strftime('%Y-%m-%dT%H:%M:%S%z')
        self.sent.append(kw)
        with open(self.log_path, 'a') as f:
            f.write(json.dumps(kw) + '\n')

    def _argv(self, where, script):
        if where == 'robot':
            return self.ssh_cmd + ['bash -lc ' + shlex.quote(script)]
        return self.local_cmd + ['exec bash -lc ' + shlex.quote(script)]

    def run(self, where, code, mode, tag, approval=None, timeout=300):
        """mode 'readonly': the code must pass the gate (re-checked here) and not be ros2cli.
        mode 'approved': go mode only, with an Approval for exactly this code."""
        if mode == 'readonly':
            ok, why, _ = gate_block(code)
            if not ok or ROS2CLI.search(code):
                raise RuntimeError(f'refused to run a non-read-only block on {where}: {why}')
            guard = True
        elif mode == 'approved':
            if not self.allow_approved or approval is None or approval.sha != hashlib.sha256(code.encode()).hexdigest():
                raise RuntimeError('refused: no approval for this exact block')
            guard = False
        else:
            raise RuntimeError(f'unknown mode {mode}')
        st = self.state[where]
        argv = self._argv(where, build_script(code, st, guard))
        self.log(where=where, mode=mode, tag=tag, code=code, argv0=argv[:len(argv) - 1])
        t0 = time.time()
        try:
            p = subprocess.run(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                               text=True, timeout=timeout)
            out, rcode = p.stdout, p.returncode
        except subprocess.TimeoutExpired as e:
            out = (e.stdout.decode() if isinstance(e.stdout, bytes) else (e.stdout or '')) + f'\n[runner] TIMEOUT after {timeout}s'
            rcode = 124
        dt = time.time() - t0
        if MARK in out:
            out, state = out.split(MARK, 1)
            out = out[:-1] if out.endswith('\n') else out
            sl = state.strip('\n').split('\n')
            st.pwd = sl[0] if sl else st.pwd
            st.env = '\n'.join(l for l in sl[1:] if l.startswith('declare -') and not _ENV_SKIP.match(l))
        self.log(where=where, tag=tag, result_rc=rcode, seconds=round(dt, 1))
        return rcode, out, dt

    def read_file(self, path, tag):
        """`cat` of one robot file for the drift check (gate-checked like any read-only block)."""
        if path in self.file_cache and not self.allow_approved:
            return self.file_cache[path]
        cmd = 'cat ' + shlex.quote(path)
        ok, why, _ = gate_block(cmd)
        if not ok:
            return None, why
        code, out, _ = self.run('robot', cmd, 'readonly', tag, timeout=60)
        res = (out if code == 0 else None), ('' if code == 0 else out.strip()[-200:])
        self.file_cache[path] = res
        return res

    # the Box interface apply_file() uses (go mode only: writes need an approval given just before)
    def go_box(self, approval_for):
        ex = self

        class _RobotBox:
            def read(self, who, path):
                return ex.read_file(path, 'go-file-read')[0]

            def write(self, who, path, content):
                cmd = (f'd=$(dirname {shlex.quote(path)}); [ -d "$d" ] || {{ echo "directory $d does not exist"; exit 3; }}; '
                       f'printf %s {shlex.quote(content)} > {shlex.quote(path)}')
                code, out, _ = ex.run('robot', cmd, 'approved', 'go-file-write', approval=approval_for(cmd))
                return code, out
        return _RobotBox()


# ---------------------------------------------------------------- the robot loop
def norm(t):
    return '\n'.join(l.rstrip() for l in t.strip('\n').split('\n'))


def contains_in_order(live, piece):
    """All non-blank lines of piece appear in live, in order (stripped)."""
    want = [l.strip() for l in piece.split('\n') if l.strip()]
    have = [l.strip() for l in live.split('\n')]
    i = 0
    for l in have:
        if i < len(want) and l == want[i]:
            i += 1
    return i == len(want), i, len(want)


def drift(ex, path, code, action, tag):
    live, err = ex.read_file(path, tag)
    if live is None:
        return {'drift': 'missing' if 'No such file' in (err or '') else 'unreadable', 'drift_error': err}
    if action in ('CREATE', 'OVERWRITE', 'LISTING-FILE'):
        if norm(live) == norm(code):
            return {'drift': 'same'}
        ok, k, n = contains_in_order(live, code)
        return {'drift': 'differs (all lines present, in order)' if ok else f'differs ({k}/{n} lines found in order)',
                'drift_diff': ''.join(list(__import__('difflib').unified_diff(
                    norm(code).splitlines(True), norm(live).splitlines(True), 'guide', 'robot', n=1))[:80])}
    ok, k, n = contains_in_order(live, code)
    return {'drift': 'piece present' if ok else f'piece not found ({k}/{n} lines in order)'}


def run_robot_chapter(ch, ex, plan, mode='dry', ask=None, fs=None, use_mac=True):
    """mode 'dry': plan + run class auto + drift reads. mode 'go': ask before every non-auto block, refuse never."""
    md = __import__('glob').glob(os.path.join(rc.CHAPTERS, f'{ch}-*.md'))
    if not md:
        sys.exit(f'no chapter {ch}')
    blocks = rc.parse(md[0])
    fs = fs or rc.FileState()
    recs, t_ch = [], time.time()
    for blk in blocks:
        host, lang = blk['host'], blk['lang']
        first = next((l for l in blk['code'].split('\n') if l.strip()), '')
        rec = dict(chapter=ch, line=blk['line'], section=blk['section'], host=host or '-', lang=lang or '-',
                   first_line=first[:120])
        override = rc.plan_override(plan, ch, blk, first) if host in ('robot', 'mac') else None
        # files
        if lang in rc.FILE_LANGS and host in ('robot', 'mac'):
            who = host
            act, path, info = rc.decide_file(blk, who, fs, override)
            cls = 'snippet' if act == 'SNIPPET' else 'file'
            rec.update(cls=cls, why=info, action=act, path=path, exec_host='robot' if who == 'robot' else None)
            if cls == 'file' and who == 'robot':
                if mode == 'dry':
                    rec['status'] = 'NOT-RUN'
                    rec.update(drift(ex, path, blk['code'], act, f'{ch}:{blk["line"]}'))
                else:
                    rec['status'] = go_file(ex, blk, who, fs, act, path, ask, rec)
            elif cls == 'file':
                rec['status'] = 'NOT-RUN'
                rec['why'] += '; @mac file: the laptop is not part of the robot target'
            else:
                rec['status'] = 'NOT-RUN'
            recs.append(rec)
            print(f'{ch}:{blk["line"]:<5} @{host:<8} {cls:<8} {rec["status"]:<9} {act} {path or ""} {rec.get("drift", "")}',
                  flush=True)
            continue
        exec_host, code, note = translate(host, blk['code'])
        cls, why = classify(blk, code if exec_host else None, override['action'] if override else None)
        if override and override['action'] in ('LISTING', 'PATH'):
            why += ' [plan.json: ' + override['reason'] + ']'
        rec.update(cls=cls, why=why, exec_host=exec_host if cls not in ('text', 'server', 'never') else None,
                   translation=note if exec_host and cls not in ('text', 'server') else None)
        if cls == 'listing' and host == 'robot':
            named = rc.home_paths(blk['prose'][-200:], '/home/burgerbarn')
            if named and re.search(r'complete file|whole file', blk['prose'][-200:], re.I) and mode == 'dry':
                rec['path'] = named[-1]
                rec.update(drift(ex, named[-1], blk['code'], 'LISTING-FILE', f'{ch}:{blk["line"]}'))
        if host == 'mac' and not use_mac:
            rec.update(status='NOT-RUN', why=why + '; --no-mac')
        elif cls in ('text', 'server', 'never', 'listing', 'snippet'):
            rec['status'] = 'REFUSED' if cls == 'never' else 'NOT-RUN'
        elif cls == 'auto' and exec_host:
            rc_, out, dt = ex.run(exec_host, code, 'readonly', f'{ch}:{blk["line"]}')
            errs = [e for e in re.findall(r'\[err rc=\d+\][^\n]*', out) if not re.match(r'\[err rc=124\]\s+timeout\b', e)]
            rec.update(status='RAN-OK' if rc_ == 0 and not errs else 'RAN-FAIL', exit=rc_, seconds=round(dt, 1),
                       inner_errors=errs[:10], output_tail=rc.tail(out, 40), check=rc.check_match(blk['check'], out),
                       expected=blk['check'][:15])
        elif mode == 'go' and exec_host:
            rec['status'] = go_block(ex, exec_host, code, cls, why, rec, blk, ask)
            if rec['status'] == 'QUIT':
                recs.append(rec)
                break
        else:
            rec['status'] = 'NOT-RUN'
        recs.append(rec)
        print(f'{ch}:{blk["line"]:<5} @{host or "-":<8} {cls:<8} {rec["status"]:<9} {why[:70]} | {first[:50]}', flush=True)
    return recs, time.time() - t_ch


def go_block(ex, where, code, cls, why, rec, blk, ask):
    if cls == 'never':
        return 'REFUSED'
    ans = ask(dict(chapter=rec['chapter'], line=rec['line'], host=rec['host'], exec_host=where, cls=cls, why=why,
                   code=code, section=rec['section']))
    if ans == 'quit':
        return 'QUIT'
    if ans != 'yes':
        return 'SKIPPED-OWNER'
    if cls in ('hands',) and any(rc.FOREGROUND.match(l) for l in code.split('\n')):
        return 'MANUAL'          # a foreground program: the owner runs it in his own terminal
    rc_, out, dt = ex.run(where, code, 'approved', f'{rec["chapter"]}:{rec["line"]}', approval=Approval(code))
    rec.update(exit=rc_, seconds=round(dt, 1), output_tail=rc.tail(out, 40), check=rc.check_match(blk['check'], out))
    if re.search(r'\bsystemctl\b[^\n]*\b(stop|start|restart)\b', code):
        rec['note'] = 'a service was stopped/started: read the state back (chapter 29 readback.sh)'
    return 'RAN-OK' if rc_ == 0 else 'RAN-FAIL'


def go_file(ex, blk, who, fs, act, path, ask, rec):
    ans = ask(dict(chapter=rec['chapter'], line=rec['line'], host=rec['host'], exec_host='robot', cls='file',
                   why=f'{act} {path}', code=blk['code'], section=rec['section']))
    if ans == 'quit':
        return 'QUIT'
    if ans != 'yes':
        return 'SKIPPED-OWNER'
    approvals = {}
    box = ex.go_box(lambda cmd: approvals.setdefault(cmd, Approval(cmd)))
    # decide_file already ran for this block: replay the write with the decided action
    st, r = rc.apply_file(box, blk, who, rc.FileState(), {'action': act, 'path': path, 'reason': 'go'})
    rec.update({k: v for k, v in r.items() if k not in ('action', 'path')})
    return 'WROTE' if st == 'OK' else 'FAIL'


# ---------------------------------------------------------------- asking the owner
def make_ask(robot):
    """stdin prompt. Robot: needs a terminal and the exact word `yes`. Sim: y/yes runs, n skips, q quits."""
    def ask(b):
        warn = {'motion': 'WHEELS OR ARM CAN MOVE: the owner is at the robot, the robot is on the stand or the floor is clear.',
                'hands': 'HANDS-ON: the owner does the physical part (stand, gamepad, cable, button, looking).',
                'ros2cli': 'read-only, but the ros2 CLI writes a log file and may start the ros2 daemon.'}.get(b['cls'], '')
        sys.stdout.write(f'\n=== {b["chapter"]} line {b["line"]} @{b["host"]} -> {b["exec_host"]}  class {b["cls"]}\n'
                         f'    {b["why"]}\n' + (f'    {warn}\n' if warn else '') + '--- block ---\n' + b['code'] + '\n---\n'
                         + ('Type yes to run it on the ROBOT, no to skip, quit to stop: ' if robot
                            else 'Run it in the container? [y/n/q] '))
        sys.stdout.flush()
        a = sys.stdin.readline()
        if not a:
            return 'quit'
        a = a.strip().lower()
        if a in ('q', 'quit'):
            return 'quit'
        if robot:
            return 'yes' if a == 'yes' else 'no'
        return 'yes' if a in ('y', 'yes') else 'no'
    return ask


def sim_approver(ask):
    """For run_chapter.run_chapter(--target sim --go): -> callable(ch, blk, who, kind, code) -> 'run'|'skip'|'refuse'|'quit'."""
    def approve(ch, blk, who, kind, code):
        if kind == 'file':
            cls, why = 'file', code
            code = blk['code']
        else:
            cls, why = classify(blk, code)
        if cls == 'never':
            print(f'{ch}:{blk["line"]:<5} REFUSED (never): {why}', flush=True)
            return 'refuse', cls, why
        if cls == 'auto':
            return 'run', cls, why
        a = ask(dict(chapter=ch, line=blk['line'], host=blk['host'], exec_host='container', cls=cls, why=why, code=code,
                     section=blk['section']))
        return {'yes': 'run', 'quit': 'quit'}.get(a, 'skip'), cls, why
    return approve


# ---------------------------------------------------------------- reports
def write_robot_reports(results, ch, recs, secs, mode):
    os.makedirs(results, exist_ok=True)
    with open(os.path.join(results, f'{ch}.jsonl'), 'w') as f:
        for r in recs:
            f.write(json.dumps(r) + '\n')
    cnt, st = {}, {}
    for r in recs:
        cnt[r['cls']] = cnt.get(r['cls'], 0) + 1
        st[r['status']] = st.get(r['status'], 0) + 1
    with open(os.path.join(results, f'{ch}.md'), 'w') as f:
        f.write(f'# {ch} on the real robot ({mode})\n\nRun {time.strftime("%Y-%m-%d %H:%M:%S %Z")}, {secs:.0f} s. '
                f'Gate {GATE_DIR}/readonly_gate.py sha256 {GATE_SHA}.\n\nClasses: '
                + ', '.join(f'{k} {v}' for k, v in sorted(cnt.items())) + '. Status: '
                + ', '.join(f'{k} {v}' for k, v in sorted(st.items())) + '\n\n')
        f.write('| line | host | class | status | first line | why |\n|---|---|---|---|---|---|\n')
        for r in recs:
            f.write(f'| {r["line"]} | @{r["host"]} | {r["cls"]} | {r["status"]}{" " + r["drift"] if r.get("drift") else ""} | '
                    f'`{r["first_line"][:50].replace("|", "/")}` | {r["why"][:110].replace("|", "/")} |\n')
        f.write('\n')
        for r in recs:
            if not (r['status'].startswith('RAN') or r.get('drift_diff') or r.get('drift') in ('missing',)):
                continue
            f.write(f'## line {r["line"]}: {r["status"]} {r.get("drift", "")} ({r["cls"]} @{r["host"]})\n\n'
                    f'- section: {r["section"]}\n- first line: `{r["first_line"]}`\n')
            for k in ('translation', 'path', 'exit', 'seconds', 'check', 'note'):
                if r.get(k) is not None:
                    f.write(f'- {k}: {r[k]}\n')
            if r.get('inner_errors'):
                f.write('- failing commands inside the block:\n' + ''.join(f'  - `{e}`\n' for e in r['inner_errors']))
            if r.get('output_tail'):
                f.write('\n```text\n' + r['output_tail'] + '\n```\n')
            if r.get('expected') and r['status'].startswith('RAN'):
                f.write('\nexpected (guide check):\n\n```text\n' + '\n'.join(r['expected']) + '\n```\n')
            if r.get('drift_diff'):
                f.write('\n```diff\n' + r['drift_diff'] + '```\n')
            f.write('\n')
    return cnt, st


def main_robot(a, plan):
    if a.go and not sys.stdin.isatty():
        sys.exit('--target robot --go needs the owner at a terminal (stdin is not a tty)')
    if not a.go and not a.dry_run:
        sys.exit('--target robot runs only with --dry-run (read-only blocks) or --go chNN (the owner at a terminal)')
    results = a.results or os.path.join(HERE, 'results', 'robot')
    os.makedirs(results, exist_ok=True)
    log = os.path.join(results, 'commands.log')
    if a.go:
        chapters = [a.go]
        mode, ex = 'go', Executor(log, allow_approved=True)
        ask = make_ask(robot=True)
    else:
        chapters, mode, ex, ask = a.chapters, 'dry', Executor(log, allow_approved=False), None
    print(f'== target robot ({ROBOT_SSH}), mode {mode}, gate {GATE_DIR} sha256 {GATE_SHA}, log {log}', flush=True)
    fs = rc.FileState()
    for ch in chapters:
        ex.state = {'robot': ShellState(), 'bigbuddy': ShellState()}       # new ssh sessions per chapter
        recs, secs = run_robot_chapter(ch, ex, plan, mode, ask, fs, not a.no_mac)
        cnt, st = write_robot_reports(results, ch, recs, secs, mode)
        print(f'== {ch}: {secs:.0f} s, classes ' + ', '.join(f'{k} {v}' for k, v in sorted(cnt.items()))
              + ' | status ' + ', '.join(f'{k} {v}' for k, v in sorted(st.items())), flush=True)


# ---------------------------------------------------------------- self test (no robot, no container)
def selftest():
    """Offline checks: classification of known commands, the executor's refusals, and the go loop with a fake ssh."""
    import tempfile
    B = lambda code, host='robot', prose='', section='', lang='bash': dict(line=1, lang=lang, host=host, code=code,
                                                                          prose=prose, section=section, check=[])
    cases = [
        (B('sudo mkfs.ext4 /dev/nvme0n1p1'), 'never'), (B('sudo sgdisk -c 1:APP /dev/nvme0n1'), 'never'),
        (B('sudo sgdisk -p /dev/nvme0n1'), 'auto'), (B('sudo efibootmgr -v'), 'auto'), (B('sudo efibootmgr -o 0001,0000'), 'never'),
        (B('sudo systemctl reboot'), 'never'), (B('sudo reboot'), 'never'), (B('sudo apt-get -y -q autoremove'), 'never'),
        (B('sudo apt autoremove --purge'), 'never'), (B('ls ~/golden'), 'never'), (B('du -sh ~/rosorin-golden', 'bigbuddy'), 'never'),
        (B('curl -s http://192.168.1.122/wiki'), 'never'), (B('ls', 'server'), 'server'),
        (B("ros2 topic pub --once /cmd_vel geometry_msgs/msg/Twist '{}'"), 'motion'),
        (B('python3 ~/setup/motion_test.py all'), 'motion'), (B('rsync -a scripts/motion_test.py rosorin-wifi:setup/', 'mac'), 'home'),
        (B('timeout 240 python3 ~/setup/stop_proof.py prove'), 'motion'), (B('python3 -m pytest -q test/test_motion.py', prose='- gamepad exempt, off by default.'), 'home'), (B('ls -l /dev/lidar'), 'auto'),
        (B('ls -l /dev/lidar', prose='Put the robot on the stand and press the button.'), 'hands'),
        (B('source /opt/ros/humble/setup.bash; ros2 node list'), 'ros2cli'),
        (B('systemctl is-active rosorin-base'), 'auto'), (B('sudo systemctl restart rosorin-base'), 'owner'),
        (B('cd ~/ros2_ws && colcon build'), 'home'), (B('#!/bin/bash\nset -e\nls'), 'listing'), (B('#!/bin/bash\npython3 ~/setup/motion_test.py'), 'listing'),
        (B('ros2 launch rosorin_base base.launch.py'), 'hands'), (B('x = 1', lang='python'), 'file'),
        (B('ls \\\n  /dev/lidar'), 'auto'), (B('ls\necho hi > ~/x'), 'home'),
        (B("ssh rosorin 'git -C ~/ros2_ws log -1'", 'mac'), 'unguarded'),
    ]
    bad = []
    for blk, want in cases:
        ex_host, code, _ = translate(blk['host'], blk['code'])
        got = classify(blk, code)[0]
        if got != want:
            bad.append((blk['code'], want, got))
    # the executor never sends a non-read-only block in dry mode, and needs an exact approval in go mode
    tmp = tempfile.mkdtemp()
    fake = os.path.join(tmp, 'fake_ssh.sh')
    open(fake, 'w').write('#!/bin/bash\necho "$@" >> ' + os.path.join(tmp, 'sent.txt') + '\necho ok\n')
    os.chmod(fake, 0o755)
    ex = Executor(os.path.join(tmp, 'log.jsonl'), allow_approved=False, ssh_cmd=[fake])
    for code in ('rm -rf ~/x', 'sudo systemctl stop rosorin-base', 'ros2 topic list', 'echo x > ~/y'):
        try:
            ex.run('robot', code, 'readonly', 't')
            bad.append((code, 'refused', 'sent'))
        except RuntimeError:
            pass
    try:
        ex.run('robot', 'sudo systemctl stop x', 'approved', 't', approval=Approval('sudo systemctl stop x'))
        bad.append(('approved in dry executor', 'refused', 'sent'))
    except RuntimeError:
        pass
    ex2 = Executor(os.path.join(tmp, 'log2.jsonl'), allow_approved=True, ssh_cmd=[fake])
    try:
        ex2.run('robot', 'sudo systemctl stop x', 'approved', 't', approval=Approval('sudo systemctl stop y'))
        bad.append(('approval for another block', 'refused', 'sent'))
    except RuntimeError:
        pass
    sent = open(os.path.join(tmp, 'sent.txt')).read() if os.path.exists(os.path.join(tmp, 'sent.txt')) else ''
    if sent:
        bad.append(('fake ssh received', '', sent[:200]))
    rc_, out, _ = ex.run('robot', 'ls -l /dev/lidar', 'readonly', 't')
    if 'ok' not in out or 'ls -l /dev/lidar' not in open(os.path.join(tmp, 'sent.txt')).read():
        bad.append(('read-only block not sent', 'sent', out))
    # the sim --go approver: never refused without asking, auto runs without asking, the rest is asked
    asked = []
    appr = sim_approver(lambda b: asked.append(b['line']) or 'no')
    for blk, want_verdict, want_asked in ((B('sudo reboot'), 'refuse', False), (B('ls -l /dev/lidar'), 'run', False),
                                          (B('mkdir -p ~/x'), 'skip', True)):
        n0 = len(asked)
        v = appr('chXX', blk, 'robot', 'bash', blk['code'])[0]
        if v != want_verdict or (len(asked) > n0) != want_asked:
            bad.append(('sim approver', blk['code'], want_verdict, v))
    for b in bad:
        print('SELFTEST MISMATCH', b)
    print(f'selftest: {len(cases) + 11 - len(bad)}/{len(cases) + 11} pass')
    return not bad


if __name__ == '__main__':
    if '--selftest' in sys.argv:
        sys.exit(0 if selftest() else 1)
    print(__doc__)
