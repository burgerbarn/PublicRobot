#!/usr/bin/env python3
"""Phase 3 groundwork: split each chapter into its runnable steps and classify who may run them.

Each fenced code block tagged with a machine (```bash @robot, @bigbuddy, @mac, @server, @any; ```python @robot =
a file to create) is one step. Classes, decided by code (readonly_gate.py + the lists below), never by a model:
  auto       read-only (passes readonly_gate)
  home       writes only below the build user's home (files, ~/ros2_ws builds) - candidate for automatic runs later
  owner      changes the system (apt install, systemctl enable/restart, /etc, udev, sudo writes) - owner's "go"
  never      destructive (disks, partitions, boot, flashing, reboot, package removal, golden sets, motion) - always owner
  file       a file the chapter asks you to create (```python/yaml/... @host)
  server     anything @server - outside this project's runner
Usage: guide_plan.py [chNN ...]   (default ch04-ch24)   -> table per chapter, --steps lists every step
"""
import re, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import readonly_gate

GUIDE = Path.home() / "llm-train/data/code-v2/rosorin-pro/guide/chapters"
if not GUIDE.exists():
    GUIDE = Path(__file__).resolve().parents[2] / "guide/chapters"

NEVER = [r"\bmkfs", r"\bwipefs\b", r"\bdd\b", r"\bsgdisk\b(?!\s+(-p|--print|-i|--info))", r"\b(gdisk|parted|fdisk)\b(?!\s+-l)",
         r"flash\.sh", r"l4t_initrd_flash", r"extlinux\.conf", r"\befibootmgr\b\s+-(c|b|B|o|n|a|A)", r"\breboot\b",
         r"\bshutdown\b", r"\bpoweroff\b", r"\bapt(-get)?\s+(-y\s+)?(remove|purge|autoremove)", r"\bdpkg\s+-(r|P)\b",
         r"~/golden|rosorin-golden", r"\bros2\s+topic\s+pub\b.*cmd_vel", r"arm/(enable|move)|/enable\b.*true",
         r"\bnvbootctrl\b", r"\bmount\b\s+/dev", r"\bumount\b"]
OWNER = [r"\bsudo\b", r"\bapt(-get)?\b", r"\bsystemctl\b\s+(--user\s+)?(enable|disable|start|stop|restart|daemon-reload|mask)",
         r"/etc/", r"\budevadm\s+(control|trigger)", r"\bmodprobe\b", r"\binsmod\b", r"\bdepmod\b", r"\bpip3?\s+install",
         r"\bros2\s+(service\s+call|topic\s+pub|param\s+set|run|launch)", r"\bdocker\s+(run|build|rm)", r"\bkill(all)?\b",
         r"\bnmcli\b", r"\biw\b.*\bset\b", r"\bchmod\b|\bchown\b"]


def steps(path):
    text = path.read_text()
    out, sec = [], ""
    for m in re.finditer(r"^(#{2,3} [^\n]*)$|^```(\w+)[ \t]+@(\w+)[ \t]*\n(.*?)^```", text, re.M | re.S):
        if m.group(1):
            sec = m.group(1).lstrip("# ").strip()
            continue
        lang, host, body = m.group(2), m.group(3), m.group(4)
        out.append({"section": sec, "lang": lang, "host": host, "body": body.strip()})
    return out


def classify(st):
    if st["host"] == "server":
        return "server"
    if st["lang"] not in ("bash", "sh", "shell", "console"):
        return "file"
    body = st["body"]
    if any(re.search(p, body) for p in NEVER):
        return "never"
    if any(re.search(p, body) for p in OWNER):
        return "owner"
    lines = [l for l in body.splitlines() if l.strip() and not l.strip().startswith("#")]
    if lines and all(readonly_gate.check(l)[0] for l in lines):
        return "auto"
    return "home"


if __name__ == "__main__" and "--drift" not in sys.argv:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    chs = args or [f"ch{n:02d}" for n in range(4, 25)]
    show = "--steps" in sys.argv
    tot = {}
    print("| ch | steps | auto | home | owner | never | file | server |\n|---|---|---|---|---|---|---|---|")
    for ch in chs:
        f = next(GUIDE.glob(f"{ch}-*.md"), None)
        if not f:
            continue
        st = steps(f)
        c = {}
        for s in st:
            k = classify(s)
            c[k] = c.get(k, 0) + 1
            tot[k] = tot.get(k, 0) + 1
            if show:
                print(f"   {ch} [{k:6}] @{s['host']:8} {s['section'][:40]:40} | {s['body'].splitlines()[0][:80]}")
        print(f"| {ch} | {len(st)} | " + " | ".join(str(c.get(k, 0)) for k in ("auto", "home", "owner", "never", "file", "server")) + " |")
    n = sum(tot.values())
    print(f"\nTOTAL {n} steps: " + ", ".join(f"{k} {v} ({100 * v // max(n, 1)}%)" for k, v in sorted(tot.items(), key=lambda x: -x[1])))


# ---- file drift check (read-only): does the file on the machine equal the file the guide prints? ----
def file_target(text_before):
    """The path the sentence before a file block names, e.g. 'Create `~/learn/rrc_read.py`:'."""
    paths = re.findall(r"`((?:~|/)[\w./@+-]+)`", text_before[-400:])
    return paths[-1] if paths else None


def file_steps(path):
    text = path.read_text()
    out = []
    for m in re.finditer(r"^```(\w+)[ \t]+@(\w+)[ \t]*\n(.*?)^```", text, re.M | re.S):
        lang, host, body = m.group(1), m.group(2), m.group(3)
        if lang in ("bash", "sh", "shell", "console", "text", "mermaid"):
            continue
        tgt = file_target(text[:m.start()])
        out.append({"lang": lang, "host": host, "path": tgt, "body": body})
    return out


def drift(chs):
    import subprocess, hashlib
    hostcmd = {"robot": ["ssh", "-n", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", "rosorin-wifi"],
               "bigbuddy": ["bash", "-c"]}
    res = {}
    for ch in chs:
        f = next(GUIDE.glob(f"{ch}-*.md"), None)
        if not f:
            continue
        for st in file_steps(f):
            if st["host"] not in hostcmd or not st["path"]:
                res.setdefault(ch, []).append((st["path"], st["host"], "no-target"))
                continue
            p = st["path"]
            argv = hostcmd[st["host"]] + [f"cat {p} 2>/dev/null"]
            try:
                live = subprocess.run(argv, capture_output=True, text=True, timeout=30).stdout
            except subprocess.TimeoutExpired:
                live = None
            if not live:
                v = "missing"
            else:
                norm = lambda s: "\n".join(l.rstrip() for l in s.strip().splitlines())
                v = "same" if norm(live) == norm(st["body"]) else "differs"
            res.setdefault(ch, []).append((p, st["host"], v))
    return res


if __name__ == "__main__" and "--drift" in sys.argv:
    chs = [a for a in sys.argv[1:] if not a.startswith("--")] or [f"ch{n:02d}" for n in range(4, 25)]
    tot = {}
    for ch, rows in drift(chs).items():
        c = {}
        for p, h, v in rows:
            c[v] = c.get(v, 0) + 1
            tot[v] = tot.get(v, 0) + 1
        print(f"{ch}: " + ", ".join(f"{k} {v}" for k, v in sorted(c.items())))
        for p, h, v in rows:
            if v in ("differs", "missing"):
                print(f"     {v:8} @{h} {p}")
    print("TOTAL " + ", ".join(f"{k} {v}" for k, v in sorted(tot.items())))
