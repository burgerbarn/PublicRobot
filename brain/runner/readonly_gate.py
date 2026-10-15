"""Deterministic read-only gate for commands the guide runner may execute without the owner.

A command passes only if every simple command in it (split on | ; && ||, inside $( ), ` ` and <( )) is an allowed
program in a read-only form: per-program option lists, exact forms for a few ros2/pytest commands, no output
redirection except to /dev/null or fd duplication, no here-docs, no device nodes opened by data readers, comment-only
lines dropped. Anything else is refused and the runner reports NEEDS-OWNER. The model never decides this.
Hardened 2026-10-14 after a review found 62 of 72 state-changing test commands passing the first version
(e.g. "systemctl --no-pager reboot", "sgdisk -p -c 1:APP"); the tests below keep them refused.
"""

import re, shlex

ALLOWED = {
    # files and text
    "cat", "head", "tail", "grep", "egrep", "zgrep", "ls", "stat", "test", "[", "wc", "sort", "uniq", "cut", "tr",
    "awk", "sed", "find", "readlink", "realpath", "basename", "dirname", "file", "diff", "cmp", "md5sum",
    "sha1sum", "sha256sum", "xxd", "od", "strings", "jq", "column", "nl", "comm", "true", "false", "echo", "printf",
    "seq", "tac", "rev", "zcat", "base64",
    # system state
    "uname", "hostname", "whoami", "id", "uptime", "date", "df", "du", "free", "lsblk", "blkid", "findmnt", "mount",
    "lsusb", "lspci", "lsmod", "modinfo", "dmesg", "ip", "iw", "nmcli", "ss", "ping", "ps", "pgrep", "nproc",
    "nvidia-smi", "tegrastats", "printenv", "getent", "timedatectl", "hostnamectl", "loginctl", "udevadm",
    "efibootmgr", "sgdisk", "gdisk", "parted", "fdisk", "dpkg", "dpkg-query", "apt-mark", "apt-cache", "apt",
    "systemctl", "journalctl", "curl", "ros2", "source", ".", "timeout", "sleep", "sudo", "cd", "which",
    "type", "python3", "v4l2-ctl", "i2cdetect", "git", "bzip2", "tar", "docker", "ssh",
    "for", "done", "do", "if", "then", "else", "fi", "passwd", "chage",
    "systemd-analyze", "hciconfig", "fuser",          # PROPOSED A2-A4
}
# Per-program: allowed first sub-words / required read-only forms; anything else refused.
SUB_ALLOW = {
    "systemctl": {"is-active", "is-enabled", "is-failed", "status", "show", "list-units", "list-unit-files",
                  "list-timers", "cat", "--failed", "get-default"},   # PROPOSED A1 (+T1: options no longer count as the verb)
    "apt-mark": {"showhold", "showauto", "showmanual"},
    "apt": {"list", "policy", "show"},
    "apt-cache": {"policy", "show", "madison", "search", "depends", "rdepends"},
    "dpkg": {"-l", "-L", "-s", "-S", "--list", "--status", "--search", "--listfiles", "--print-architecture"},
    "ros2": {"topic", "node", "service", "param", "interface", "pkg", "doctor", "action", "--help"},
    "git": {"log", "status", "rev-parse", "show", "diff", "branch", "describe", "ls-files", "remote", "-C"},
    "sgdisk": {"-p", "--print", "-i", "--info"},
    "gdisk": {"-l"},
    "fdisk": {"-l"},
    "parted": {"-l", "print"},
    "efibootmgr": {"-v", "--verbose"},
    "nmcli": {"-t", "-f", "dev", "device", "con", "connection", "general", "radio", "show", "status", "list"},
    "ip": {"a", "addr", "address", "link", "route", "r", "neigh", "-br", "-4", "-6", "-s"},
    "udevadm": {"info"},
    "systemd-analyze": {"cat-config"},                # PROPOSED A2
    "hostnamectl": {"status"}, "timedatectl": {"status", "show", "show-timesync", "timesync-status"},   # T4
    "loginctl": {"show-user", "list-sessions", "show-session"},
    "bzip2": {"-t", "--test", "-tv", "-vt"},
    "passwd": {"-S", "--status"},
    "chage": {"-l", "--list"},
    "tar": {"-t", "-tf", "-tjf", "-tzf", "-tvf", "-tvjf", "--list", "tf", "tjf", "tzf", "tvf"},
    "docker": {"images", "image", "ps", "inspect", "version", "info", "run"},   # run: only the read-only form below
}
ROS2_VERBS = {"list", "echo", "info", "hz", "type", "find", "get", "describe", "show", "prefix", "executables", "bw"}
BLOCKED_ANYWHERE = [r"\bsed\b.*\s-i", r"\bfind\b.*\s-(delete|exec|execdir|ok|fprint)", r"\bcurl\b.*\s(-X|--request|-d|--data|-F|--form|-T|--upload|-o|-O)\b",
                    r"\bawk\b.*\bsystem\s*\(", r"\bmount\b\s+(-o|/dev|-t)", r"\bdate\b\s+(-s|--set)",
                    r"\bpython3\b(?!\s+-c\s)", r"\bpython3\s+-c\s.*\b(open\(.*['\"][wa]|os\.(remove|unlink|system|rename)|subprocess|shutil)",
                    r"\bdmesg\b.*\s-(c|C|D|E)\b", r"\bsystemctl\b.*\b(start|stop|restart|reload|enable|disable|mask|unmask|kill|daemon-reload|reset-failed|edit|set-property)\b",
                    r"\bjournalctl\b.*\s--(vacuum|rotate|flush)", r"\bgit\b.*\b(push|pull|fetch|commit|checkout|reset|clean|stash|merge|rebase|config)\b",
                    r"\bros2\b.*\b(pub|call|set|load|send_goal|run|launch|kill|daemon)\b", r"\bip\b.*\b(add|del|set|flush|change|replace)\b",
                    r"\biw\b.*\b(set|connect|disconnect)\b", r"\bnmcli\b.*\b(up|down|modify|add|delete|connect|disconnect|on|off)\b",
                    r"\bv4l2-ctl\b.*\s(-c|--set)", r"\bdocker\s+(image\s+)?(rm|rmi|prune|tag|push|pull|build|load|import|save)\b",
                    r"\btar\b.*\s-?-?(x|c|r|u|A|delete|extract|create|append)"]


def _split(cmd):
    """Quote-aware split on | ; && || -> list of word lists."""
    lex = shlex.shlex(cmd, posix=True, punctuation_chars=";&|")
    lex.whitespace_split = True
    lex.commenters = ""                                   # T18
    out, cur = [], []
    for tok in lex:
        if tok and set(tok) <= set(";&|"):
            if cur: out.append(cur)
            cur = []
        else:
            cur.append(tok)
    if cur: out.append(cur)
    return out


def _segments(cmd):
    inner = re.findall(r"\$\(([^()]*)\)", cmd) + re.findall(r"`([^`]*)`", cmd)
    flat = re.sub(r"\$\([^()]*\)|`[^`]*`", "X", cmd).replace("\n", ";")
    segs = _split(flat)
    for i in inner:
        segs += _split(i)
    return segs



BLOCKED = BLOCKED_ANYWHERE + [
    r"\bdate\b.*\s(-s|--set)\b",                                         # T13 date -u -s
    r"\bjournalctl\b.*\s--(setup-keys|relinquish-var|smart-relinquish-var|update-catalog)",
    r"\bdmesg\b.*\s(-n|--console-level|--clear|--read-clear|--console-off|--console-on)\b",
    r"\bss\b.*\s(-K|--kill)\b",
    r"\bfind\b.*\s-(fls|fprint0|fprintf)\b",
    r"\bsort\b.*\s(-o|--output)\b",
    r"\bfile\b.*\s-C\b",
    r"\bcurl\b.*\s(--json|-K|--config|--output-dir|--upload-file)\b",
    r"\bxxd\b.*\s-r\b",
    r"\btegrastats\b.*\s--(logfile|start|stop)\b",
    r"\biw\b.*\b(del|scan|add|ibss|mesh|offchannel|switch)\b",
]
DEV_DATA = re.compile(r"^/dev/(tty|lidar|video|media|hidraw|input/|i2c-|spidev|gpiochip|snd/|bus/usb/)")
DEV_OK_PROGS = {"ls", "stat", "readlink", "realpath", "test", "[", "udevadm", "fuser", "lsblk", "blkid", "findmnt",
                "sgdisk", "gdisk", "fdisk", "parted", "file", "basename", "dirname", "echo", "printf", "grep", "v4l2-ctl"}
# A5-A9: exact forms, checked per simple command after VAR=/sudo/timeout are stripped
EXACT = [
    r"ros2 run tf2_ros tf2_echo [A-Za-z0-9_/]+ [A-Za-z0-9_/]+",                                   # A5
    r"ros2 run rosorin_base nav_wait( [0-9]+(\.[0-9]+)?)?",                                       # A6
    r"ros2 service call /rosorin_board_driver/arm/read_state std_srvs/srv/Trigger( \{\})?",        # A7
    r"python3 -B -m pytest -q -p no:cacheprovider (test/|test/test_(rrc_protocol|arm|coin_d6|motion|gyro_bias|gamepad_teleop)\.py)",  # A8
]
PINNED = [   # A9: only together with a runner-side sha256 pin of the file (the gate cannot see the file)
    r"python3 (~|/home/burgerbarn)/learn/scan_probe\.py",
    r"python3 scripts/tests/check_nav2_config\.py( [\w./~-]+\.yaml)?",
]
ALLOW_PINNED = False   # A9 needs a runner-side sha256 pin of the script file; not built yet
VALUE_OPTS = {"systemctl": {"-p", "--property", "-t", "--type", "--state", "-n", "--lines", "-o", "--output"},
              "git": {"-C"}, "nmcli": {"-f", "--fields", "-g", "--get-values", "-m", "--mode", "-c", "--colors"}}
OPTS = {   # T1: every option of these programs must be in this set (None = any option not starting a write is ok)
    "systemctl": {"--user", "--no-pager", "-q", "--quiet", "--failed", "--all", "-a", "--no-legend", "-l", "--full",
                  "--plain", "--value", "-p", "--property", "-t", "--type", "--state", "-n", "--lines", "-o", "--output"},
    "sgdisk": {"-p", "--print", "-i", "--info"}, "gdisk": {"-l"}, "fdisk": {"-l", "--list"},
    "parted": {"-l", "--list", "-s", "--script", "-m", "--machine"}, "efibootmgr": {"-v", "--verbose"},
    "bzip2": {"-t", "--test", "-tv", "-vt", "-v"}, "passwd": {"-S", "--status"}, "chage": {"-l", "--list"},
    "apt": {"--installed", "--upgradable", "-a", "--all-versions"}, "loginctl": {"-p", "--property", "--no-pager", "--value"},
    "dpkg": {"-l", "-L", "-s", "-S", "--list", "--status", "--search", "--listfiles", "--print-architecture"},
    "udevadm": {"-q", "--query", "-p", "--path", "-n", "--name", "-a", "--attribute-walk", "-r", "--root", "-e", "--export-db", "-x", "--export"},
    "hciconfig": {"-a"}, "fuser": {"-v"}, "mount": {"-l"}, "hostname": {"-I", "-i", "-f", "-s", "-d", "-A", "--all-ip-addresses"},
    "i2cdetect": {"-l", "-F"}, "nvidia-smi": {"-L", "-q", "--query-gpu", "--format"},
    "v4l2-ctl": {"--list-devices", "-d", "--device", "-D", "--info", "--all", "--list-formats", "--list-formats-ext",
                 "--list-ctrls", "--list-ctrls-menus", "-l", "-L", "--get-fmt-video", "-V", "--get-parm", "-P"},
    "ssh": {"-n", "-T", "-q", "-o", "-i", "-p", "-l"},
}
SSH_O = re.compile(r"^(BatchMode=yes|ConnectTimeout=\d+)$")
WORD_ARG_PROGS = {"sgdisk": re.compile(r"^(\d+|/dev/[\w/.-]+)$"), "efibootmgr": re.compile(r"^$"),
                  "bzip2": re.compile(r"^[^-].*"), "mount": re.compile(r"^$"), "hciconfig": re.compile(r"^hci\d$"),
                  "fuser": re.compile(r"^/[\w/.-]+$"), "i2cdetect": re.compile(r"^\d+$"), "hostname": re.compile(r"^$"),
                  "gdisk": re.compile(r"^/dev/[\w/.-]+$"), "fdisk": re.compile(r"^/dev/[\w/.-]+$"),
                  "parted": re.compile(r"^(/dev/[\w/.-]+|print|unit|s|B|MiB|GiB|free)$")}
PY_C = re.compile(r"^(import [\w.]+( as \w+)?(, ?[\w.]+)*; ?)+print\(([\w.,' ():%\"-]|__version__)*\)$")
SED_WRITE = re.compile(r"(^|[;{}\n]|/[gpiImMe0-9]*)\s*[wWe](\s|$)")


def _strip_fd(cmd):
    return re.sub(r"\s*(\d*>&\d+|&>\s*/dev/null|\d*>\s*/dev/null)", " ", cmd)


def _segments(cmd):
    inner, flat = [], cmd
    pat = re.compile(r"(\$|<)\(([^()]*)\)")
    while pat.search(flat):
        inner += [m.group(2) for m in pat.finditer(flat)]
        flat = pat.sub("X", flat)
    flat = re.sub(r"`([^`]*)`", lambda m: inner.append(m.group(1)) or "X", flat).replace("\n", ";")
    segs = _split(flat)
    for i in inner:
        segs += _segments(i)
    return segs


def _unquoted(cmd):
    return re.sub(r"'[^']*'|\"(?:[^\"\\]|\\.)*\"", "''", cmd)


def check(cmd):
    if not cmd.strip():
        return False, "empty command"                                       # T14
    # T18: whole-line comments are dropped; any other '#' stays a word (shlex must not swallow the rest of the line)
    c0 = _strip_fd("\n".join(l for l in cmd.splitlines() if not l.strip().startswith("#")))
    if not c0.strip():
        return False, "only comments"
    if ">(" in c0:
        return False, "process substitution >( )"
    if re.search(r"(^|[^0-9&])>{1,2}", _unquoted(c0)):
        return False, "output redirection"
    if "<<" in c0:
        return False, "here-doc"
    try:
        segs = _segments(c0)
    except ValueError as e:
        return False, f"unparseable ({e})"
    for words in segs:
        while words and re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", words[0]):
            words = words[1:]
        if words and words[0] == "sudo":                     # T17: only sudo's own leading -n/-E, not every -n/-E
            words = words[1:]
            while words and words[0] in ("-n", "-E"):
                words = words[1:]
        if words and words[0] == "timeout":
            words = words[1:]
            while words and (words[0].startswith("-") or re.match(r"^\d+[smh]?$", words[0])):
                words = words[1:]
        if not words:
            continue
        if words[0] in ("do", "then", "else", "if"):
            words = words[1:]
            if not words:
                continue
        line = " ".join(words)
        if any(re.fullmatch(p, line) for p in EXACT) or (ALLOW_PINNED and any(re.fullmatch(p, line) for p in PINNED)):
            continue
        for p in BLOCKED:
            if re.search(p, " ".join(words)):
                return False, f"blocked pattern {p}"
        prog = words[0].split("/")[-1]
        args = words[1:]
        if prog not in ALLOWED:
            return False, f"program not on the read-only list: {prog}"
        if prog in ("for", "done", "fi"):
            continue
        if prog not in DEV_OK_PROGS and any(DEV_DATA.match(w) for w in args):
            return False, f"{prog} would open a device node"                 # T10
        if prog == "python3":
            if args[:1] != ["-c"] or len(args) != 2 or not PY_C.match(args[1]) or "/dev/" in args[1]:
                return False, "python3 -c is limited to 'import X; print(...)'"          # T9
            continue
        if prog == "awk" and any(re.search(r"\||getline|system|>", a) for a in args if not a.startswith("-")):
            return False, "awk program with a pipe, getline, system or redirection"      # T8
        if prog == "sed" and any(SED_WRITE.search(a) for a in args if not a.startswith("-")):
            return False, "sed script with a w/W/e command"                               # T8
        if prog in ("uniq", "xxd") and len([a for a in args if not a.startswith("-")]) > 1:
            return False, f"{prog} with an output file"                                    # T8
        if prog == "git":
            rest, i = [], 0
            while i < len(args):
                if args[i] == "-C": i += 2; continue
                if args[i] == "--no-pager": i += 1; continue
                rest = args[i:]; break
            if not rest or rest[0] not in {"log", "status", "rev-parse", "show", "diff", "describe", "ls-files", "branch", "remote"}:
                return False, "git verb is not a read-only form"
            if any(a.startswith("--output") or a in ("--ext-diff", "-c") for a in rest):
                return False, "git option that writes or runs a command"
            if rest[0] == "branch" and not set(rest[1:]) <= {"--show-current", "-a", "-v", "-vv", "-r", "--list"}:
                return False, "git branch with arguments (create/delete/rename)"
            if rest[0] == "remote" and not (rest[1:] in ([], ["-v"]) or (len(rest) == 3 and rest[1] == "get-url")):
                return False, "git remote with arguments (add/remove/set-url)"
            continue
        if prog == "nmcli":                                                                # T5
            rest, i = [], 0
            while i < len(args):
                if args[i] in VALUE_OPTS["nmcli"]: i += 2; continue
                if args[i] in ("-t", "--terse", "-p", "--pretty"): i += 1; continue
                rest = args[i:]; break
            obj, verb = (rest + [None, None])[:2]
            ok = (obj in ("general", "g") and rest[1:] in ([], ["status"], ["permissions"], ["hostname"])) or \
                 (obj in ("radio", "r") and len(rest) <= 2 and set(rest[1:]) <= {"all", "wifi", "wwan"}) or \
                 (obj in ("device", "dev", "d") and (rest[1:] in ([], ["status"]) or verb == "show" and len(rest) <= 3
                                                     or rest[1:] == ["wifi", "list", "--rescan", "no"])) or \
                 (obj in ("connection", "con", "c") and (rest[1:] in ([], ["show"], ["show", "--active"]) or verb == "show" and len(rest) == 3))
            if not ok:
                return False, "nmcli form is not on the read-only list"
            continue
        if prog == "iw":
            if not (args in (["dev"], ["list"], ["phy"]) or (len(args) >= 3 and args[0] == "dev" and args[2] in ("info", "link", "get", "station", "survey"))
                    and (args[2] != "station" or args[3:] == ["dump"]) and (args[2] != "survey" or args[3:] == ["dump"])):
                return False, "iw form is not on the read-only list"
            continue
        if prog in ("hostnamectl", "timedatectl"):
            if args and args[0] not in SUB_ALLOW[prog]:
                return False, f"{prog} {args[0]} is not a read-only form"
            continue
        if prog in OPTS:                                                                   # T1
            vopts = VALUE_OPTS.get(prog, set())
            pos, i = [], 0
            while i < len(args):
                a = args[i]
                if a.startswith("-"):
                    base = a.split("=")[0]
                    if base not in OPTS[prog] and not (prog == "sgdisk" and re.match(r"^-i\d+$", a)):
                        return False, f"{prog} option {a} is not on the read-only list"
                    if prog == "ssh" and a == "-o":
                        if i + 1 >= len(args) or not SSH_O.match(args[i + 1]):
                            return False, "ssh -o option other than BatchMode/ConnectTimeout"
                        i += 2; continue
                    if a in vopts or (prog == "ssh" and a in ("-i", "-p", "-l")):
                        i += 2; continue
                    i += 1; continue
                pos.append(a); i += 1
                if prog == "ssh":
                    pos += args[i:]; break
            if prog in WORD_ARG_PROGS and not all(WORD_ARG_PROGS[prog].match(p) for p in pos):
                return False, f"{prog} argument not on the read-only list"
            if prog == "ssh":
                if len(pos) < 2:
                    return False, "ssh without a remote command"
                ok, why = check(" ".join(pos[1:]))
                if not ok:
                    return False, f"ssh remote command: {why}"
                continue
            if prog in SUB_ALLOW and prog not in ("dpkg", "sgdisk", "gdisk", "fdisk", "parted", "efibootmgr", "bzip2", "passwd", "chage"):
                if prog == "systemctl" and not pos and "--failed" in args:
                    continue
                if not pos or pos[0] not in SUB_ALLOW[prog]:
                    return False, f"{prog} {pos[0] if pos else ''} is not a read-only form"
            if prog in ("passwd", "chage") and not any(a in OPTS[prog] for a in args):
                return False, f"{prog} without its read-only option"
            if prog == "dpkg" and not any(a in SUB_ALLOW["dpkg"] for a in args):
                return False, "dpkg without a read-only action"
            continue
        if prog == "docker" and len(words) > 1 and words[1] == "run":
            if any(w.startswith("--entrypoint") for w in words):
                return False, "docker run --entrypoint"
            if "--rm" not in words or any(w in ("--privileged", "-d", "--detach") for w in words):
                return False, "docker run must be --rm, not detached, not privileged"
            img = next((j for j, w in enumerate(words[2:], 2) if re.match(r"^[\w./-]+:[\w.-]+$", w)), None)
            if img is None:
                return False, "docker run without an image:tag"
            inner = words[img + 1:]
            if inner[:1] == ["chroot"] and len(inner) > 2:
                inner = inner[2:]
            if inner[:2] in (["bash", "-c"], ["sh", "-c"]) and len(inner) == 3:
                ok, why = check(inner[2])
            else:
                ok, why = check(" ".join(shlex.quote(w) for w in inner))
            if not ok:
                return False, f"docker run inner command: {why}"
            continue
        if prog in ("source", "."):
            if not (args and re.match(r"^(/opt/ros/humble|~/|\$HOME/|/home/burgerbarn/)[\w./-]*setup\.(bash|sh)$", args[0])):
                return False, "source of something other than a ROS setup file"
        if prog in SUB_ALLOW:
            if prog == "ros2":
                if len(words) < 3 and "--help" not in words:
                    return False, "ros2 without a read verb"
                if len(words) >= 3 and words[1] != "--help" and words[2] not in ROS2_VERBS:
                    return False, f"ros2 {words[1]} {words[2]} is not a read verb"
                continue
            if prog in ("tar", "docker"):      # these take the read form as an option word (tar -tf) or verb: original rule
                subs = [w for w in args if not w.startswith("/") and "=" not in w][:1]
            else:
                subs = [w for w in args if not w.startswith("-") and not w.startswith("/") and "=" not in w][:1]
            if not subs or subs[0] not in SUB_ALLOW[prog]:
                return False, f"{prog} {subs[0] if subs else ''} is not a read-only form"
            if prog == "ip" and any(a in ("-b", "-batch", "-force") for a in args):
                return False, "ip -batch/-force"
    return True, "read-only"


PINNED_GOOD = ['python3 ~/learn/scan_probe.py']   # refused while ALLOW_PINNED is False


if __name__ == "__main__":
    tests = {
        "apt-mark showhold | grep -c nvidia-l4t": True,
        "head -1 /etc/nv_tegra_release": True,
        "dpkg-query -W nvidia-jetpack": True,
        "systemctl is-active rosorin-base": True,
        "journalctl -u rosorin-base -b --no-pager | grep -c 'addresses stable'": True,
        "source /opt/ros/humble/setup.bash && timeout 10 ros2 topic echo --once /odom": True,
        "sudo sgdisk -p /dev/nvme0n1": True,
        "sudo blkid -s PARTUUID -o value /dev/nvme0n1p1": True,
        "sudo systemctl restart rosorin-base": False,
        "sudo sgdisk -c 1:APP /dev/nvme0n1": False,
        "echo hi > /etc/foo": False,
        "sed -i s/a/b/ f": False,
        "ros2 topic pub /cmd_vel geometry_msgs/msg/Twist '{}'": False,
        "curl -s -X POST http://x/move": False,
        "rm -rf ~/setup": False,
        "sudo reboot": False,
        "cat $(rm -rf /)": False,
        "apt install foo": False,
        "ls ~/setup 2>/dev/null | wc -l": True,
        "find / -name x -delete": False,
        "python3 -c 'import yaml; print(1)'": True,
        "python3 script.py": False,
        "bash -c 'ls'": False,
        "ssh rosorin ls": True,                                 # remote command passes the gate
        "ls | xargs rm": False,
        "ls; sudo rm -rf /": False,
        "grep 'a;b' f && echo 'x | y'": True,
        "eval ls": False,
        "cat f >> /tmp/x": False,
        "sudo -n true": True,
        "systemctl --user is-active buddy-voice": True,
        "ros2 node list": True,
        "git -C ~/x log -1": True,
        "git push": False,
        "passwd -S burgerbarn": True,
        "passwd burgerbarn": False,
        "docker run --rm -v ~/w:/work:z jetson-flash:r36.4.3 chroot /work/rootfs passwd -S burgerbarn": True,
        "docker images jetson-flash": True,
        "docker run --rm -v ~/w:/work:z jetson-flash:r36.4.3 bash -c 'find /work/rootfs -xdev -type f | wc -l'": True,
        "docker run --rm -v ~/w:/work:z jetson-flash:r36.4.3 bash -c 'rm -rf /work'": False,
        "docker run -d img:1 ls": False,
        "docker rm x": False,
        "docker image rm x": False,
        "docker image ls": True,
        "for f in *.tbz2; do bzip2 -t $f && echo OK $f; sha256sum $f; done": True,
        "for f in *; do rm $f; done": False,
        "ssh -o BatchMode=yes rosorin-wifi hostname": True,
        "ssh -o BatchMode=yes rosorin-wifi 'sudo reboot'": False,
        "ssh rosorin-wifi": False,
        "tar -tjf a.tbz2 | head": True,
        "tar -xjf a.tbz2": False,
        "sudo rm -rf /": False,
        "cp a b": False,
        "dd if=/dev/zero of=/dev/nvme0n1": False,
    }

    holes = ["systemctl --no-pager reboot","systemctl -q poweroff","systemctl --user isolate rescue.target","systemctl --no-pager set-default graphical.target",
     "sudo sgdisk -p -c 1:APP /dev/nvme0n1","sudo sgdisk -p -d 16 /dev/nvme0n1","sudo sgdisk -i 1 -c 1:APP /dev/nvme0n1","sudo efibootmgr -v -b 0001 -B","sudo efibootmgr -v -o 0001,0000",
     "sudo parted /dev/nvme0n1 print rm 16","env rm -rf ~/x","command rm -rf ~/x","git -C ~/r branch -D main","git -C ~/r remote remove origin","git -C ~/r rm -r .",
     "git log --output=/home/burgerbarn/.bashrc","hostnamectl set-hostname foo","sudo timedatectl set-ntp false","nmcli -t general hostname foo","nmcli dev set wlP1p1s0 managed no",
     "sudo udevadm info --cleanup-db","sudo udevadm info -c","gunzip ~/maps/a.gz","sort -o ~/.bashrc /etc/hostname","uniq /etc/hostname /home/burgerbarn/.bashrc","xxd /etc/hostname /home/burgerbarn/x",
     "sed -n 'w /home/burgerbarn/x' /etc/hostname","awk 'BEGIN{print \"touch /tmp/pwn\" | \"sh\"}'","awk '{print > \"/tmp/x\"}' f","python3 -c 'import os; os.kill(1234, 9)'",
     "python3 -c 'import pathlib; pathlib.Path(\"/home/burgerbarn/x\").write_text(\"\")'","python3 -c \"import os; os.open('/dev/ttyACM0', os.O_RDONLY)\" 2>&1 | tail -1",
     "curl --json '{}' http://127.0.0.1:8000/move","curl -K /tmp/cfg http://x","sudo dmesg -n 1","sudo ss -K dst 192.168.1.10","timeout 2 cat /dev/ttyACM0","timeout 2 cat /dev/lidar | wc -c",
     "find ~ -name x -fls /home/burgerbarn/out","bzip2 -t -d a.bz2","diff a <(rm -rf ~/x)","cat <(touch /tmp/x)","ssh -o ProxyCommand='touch /tmp/x' rosorin-wifi hostname",
     "ssh -o PermitLocalCommand=yes -o LocalCommand='rm x' rosorin-wifi hostname","file -C -m /tmp/m","sudo mount --bind /a /b","sudo mount -a","sudo mount /mnt/factory","sudo hostname foo",
     "sudo date -u -s '2020-01-01'","sudo iw dev wlP1p1s0 del","sudo iw dev wlP1p1s0 scan","sudo nvidia-smi -pm 1","nvidia-smi -pl 100","sudo i2cdetect -y 1","v4l2-ctl -d /dev/video0 -p 5",
     "v4l2-ctl -d /dev/video0 --stream-mmap --stream-count=10","sudo tegrastats --logfile /tmp/t","sudo tegrastats --stop","sudo journalctl --setup-keys","sudo dpkg --list --purge brltty",
     "sudo passwd -S -d burgerbarn","sudo chage -l -E 0 burgerbarn","hciconfig hci0 down","sudo fuser -k /dev/ttyACM0","systemctl set-default graphical.target","ls >(cat)",
     "ros2 run tf2_ros tf2_echo base_link laser; ros2 topic pub /cmd_vel geometry_msgs/msg/Twist '{}'","ros2 service call /rosorin_board_driver/arm/recover std_srvs/srv/Trigger",
     "ros2 run rosorin_base nav_wait 120 && ros2 run rosorin_base nav_supervisor", "python3 -m pytest -q test/", ""]
    
    good = ["systemctl get-default", "systemd-analyze cat-config systemd/logind.conf | grep '^RemoveIPC'", "hciconfig", "hciconfig hci0 | grep -E 'UP|RUNNING'", "hciconfig -a",
     "sudo -n fuser -v /dev/ttyACM0", "dpkg-query -W -f='${Package} ${db:Status-Abbrev}\\n' brltty 2>&1", "timeout 6 ros2 topic hz /odom_vslam 2>&1 | tail -2",
     "SET=$(basename $(ls -dt ~/golden/*/ | head -1)); cd ~/golden/$SET && ls", "timeout 15 ros2 run tf2_ros tf2_echo base_link laser 2>&1 | grep -m2 -E 'Translation|RPY \\(degree'",
     "timeout 130 ros2 run rosorin_base nav_wait 120", "timeout 15 ros2 service call /rosorin_board_driver/arm/read_state std_srvs/srv/Trigger | grep -o 'message=.*' | cut -c1-600",
     "cd ~/ros2_ws/src/rosorin_base && python3 -B -m pytest -q -p no:cacheprovider test/test_rrc_protocol.py", "python3 ~/learn/scan_probe.py",
     "python3 -c \"import cuvslam as v; print(v.__version__)\"", "~/vision/venv/bin/python3 -c \"import torch; print(torch.cuda.is_available(), torch.__version__)\"",
     "systemctl show nv-l4t-bootloader-config.service -p Result", "systemctl -p Result show nv-l4t-bootloader-config.service", "sudo sgdisk -i 1 /dev/nvme0n1", "sudo efibootmgr -v",
     "sudo parted /dev/nvme0n1 unit MiB print", "nmcli -t -f DEVICE,STATE dev status", "nmcli con show --active", "iw dev wlP1p1s0 get power_save", "iw dev wlP1p1s0 link",
     "git -C ~/vendor_ws/src/deptrum-ros-driver-aurora930-0.2.11 remote", "git -C ~/x log -1", "grep '>> forward' f", "mount | grep ' /mnt/factory '", "timedatectl", "hostname -I",
     "nvidia-smi --query-gpu=name,memory.used --format=csv", "ssh -n -o BatchMode=yes -o ConnectTimeout=10 rosorin-wifi 'systemctl is-active rosorin-base'",
     "diff <(sort a) <(sort b)", "udevadm info -q property -n /dev/ttyACM0", "lsblk -l -o NAME,PARTLABEL,PARTUUID /dev/nvme0n1", "sudo blkid -s PARTUUID -o value /dev/nvme0n1p1",
     "journalctl -b -1 -n 2 --no-pager", "apt list --installed 2>/dev/null | grep -c ros-humble"]
    
    bad = [("unit", c, exp, check(c)) for c, exp in tests.items() if check(c)[0] != exp]
    bad += [("hole", c, False, check(c)) for c in holes if check(c)[0]]
    bad += [("good", c, True, check(c)) for c in good if not check(c)[0] and c not in PINNED_GOOD]
    for kind, c, exp, got in bad:
        print("MISMATCH", kind, exp, got, repr(c))
    n = len(tests) + len(holes) + len(good)
    print(f"{n - len(bad)}/{n} gate tests pass ({len(tests)} unit, {len(holes)} must-refuse, {len(good)} must-allow)")
