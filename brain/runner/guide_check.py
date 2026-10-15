#!/usr/bin/env python3
"""Phase 2 guide runner: run a chapter's "Before you move on" checks on the live robot, read-only.

For each checklist item the model (through the brain, with retrieval) proposes read-only commands; a deterministic
gate (readonly_gate.py) refuses anything that could change state; allowed commands run on the robot or bigbuddy;
the model judges each output against the item. Nothing the gate refuses is run: it is reported as NEEDS-OWNER.

  guide_check.py ch09 [ch10 ...] [--model lightbot-oss-xps] [--dry]
  guide_check.py --claude-review ~/llm-train/runs/<run>   Claude's second opinion on that run's FAIL/UNSURE checks
Output: ~/llm-train/runs/<time>-<model>/chNN.md and chNN.jsonl
At the end of a run with FAIL or UNSURE checks it offers Claude's second opinion on them (claude_gate.py): only in a
terminal, only after you type yes; Claude's verdict is added next to the local one, never replaces it.
"""
import argparse, json, os, re, shlex, subprocess, sys, time, urllib.request
from pathlib import Path

sys.path[:0] = [str(Path(__file__).parent), str(Path(__file__).resolve().parent.parent / "server")]   # claude_gate.py: repo server/, deployed next to this file
import readonly_gate

HOME = Path.home()
GUIDE = HOME / "llm-train/data/code-v2/rosorin-pro/guide/chapters"
BRAIN = "http://127.0.0.1:8300/v1/chat/completions"
KEY = (HOME / "llm-train/brain/key").read_text().strip()
HOSTS = {"robot": ["ssh", "-n", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", "rosorin-wifi"], "bigbuddy": None}

PLAN_SCHEMA = {"type": "object", "properties": {
    "checks": {"type": "array", "items": {"type": "object", "properties": {
        "host": {"type": "string", "enum": ["robot", "bigbuddy", "hands", "later", "owner"]},
        "cmd": {"type": "string"}, "expect": {"type": "string"}}, "required": ["host", "cmd", "expect"]}},
    "note": {"type": "string"}}, "required": ["checks", "note"]}
JUDGE_SCHEMA = {"type": "object", "properties": {
    "verdict": {"type": "string", "enum": ["PASS", "DIFFERS", "FAIL", "UNSURE"]}, "why": {"type": "string"}},
    "required": ["verdict", "why"]}

PLAN_PROMPT = """You check one item of the lightBot build guide's "Before you move on" list for chapter {ch} on the live robot.
Item:
{item}

Turn it into the shell commands that check it, and what their output must show.
Where things are: the robot is 192.168.1.108, user burgerbarn. For host "robot" give the BARE command: the runner
connects to the robot itself and has already sourced /opt/ros/humble and ~/ros2_ws/install (do not add ssh or source).
The repository is NOT on the robot; the robot has ~/ros2_ws, ~/setup, ~/golden, ~/maps, ~/vision, ~/battery.
The repository's reference copy is on bigbuddy in ~/llm-train/data/code-v2/rosorin-pro (same layout as the repo,
e.g. ros2/rosorin_base/...). To compare a robot file with it, use host "bigbuddy":
`ssh -n -o BatchMode=yes rosorin-wifi cat <robot path> | diff - ~/llm-train/data/code-v2/rosorin-pro/<repo path> && echo same`.
Chapter 4 prepares the robot's root filesystem ON BIGBUDDY (in ~/jetson-flash, staged rootfs at
~/jetson-flash/work/Linux_for_Tegra/rootfs, docker image jetson-flash:r36.4.3): its checks are about bigbuddy and that
staged rootfs, not the running robot, which later chapters changed. Chapter 5 copies it onto the robot's p1.
- host "robot": runs on the robot (Jetson Orin NX, Ubuntu 22.04, user burgerbarn, passwordless sudo, ROS 2 Humble in /opt/ros/humble).
- host "bigbuddy": runs on bigbuddy (Fedora PC on the same network) - only for checks the guide runs from a PC (curl, ping, ssh tests).
- host "hands": the item needs a person (look, press, plug, listen). cmd = what the person does.
- host "later": the item depends on a later chapter or a past event that cannot be checked now. cmd = why.
- host "owner": the only way to check it changes state (publishing a test message, starting or restarting a service,
  writing a file). cmd = the guide's command for it; the owner decides whether to run it.
Rules: commands must be READ-ONLY (no install, no service start/stop/restart, no writes, no reboot, no ros2 pub/call).
Files only root can read (block devices for blkid, /etc/shadow, another user's ~/.ssh, the staged rootfs's home dirs):
use `sudo -n`. Give ros2 commands a `timeout 15` (e.g. `timeout 15 ros2 topic hz /scan`); use `ros2 topic echo --once`.
For journal checks use the current boot (`journalctl -b -u <unit> --no-pager`), not short --since windows.
Items about what the reader knows, can explain, sees or hears ("you know", "you can say", "looks like") are host "hands".
Use the guide's own commands from the excerpts where it gives them. One item may need several checks.
Keep commands simple and show raw output: no awk/cut column guessing, `lsblk -l` (flat, not the tree), plain grep.
`ros2 topic echo --once --flow-style` prints lists (like camera_info k) on one line; `ls -d dir/*/` lists folders only.
"expect" = the item's own criterion in words (what must be true), not exact numbers, dates or log lines copied from
records - unless the item itself states that exact value.
Answer as JSON."""

JUDGE_PROMPT = """Judge one check of the lightBot build guide, chapter {ch}.
Checklist item (THE criterion): {item}
Command (on {host}): {cmd}
Planner's hint (may be too strict; the checklist item decides): {expect}
Exit code: {rc}
Output:
{out}

PASS if the output shows what the item requires.
DIFFERS if the thing works but a number, version or output format differs from the guide's text (e.g. a rate of 13 Hz
where the guide says about 9, or a newer tool printing different column names): that is a finding for the guide.
FAIL only if the output clearly shows the item's criterion is not met (missing, inactive, a value the item rules out).
Incidental details are never a FAIL: a different number of seconds, timestamps, build dates, an extra or missing network
interface when the item does not require it.
UNSURE if the output cannot decide it (the command failed, wrong path or column, syntax error, empty output, timeout).
Use ONLY the output above as evidence - no records, no memory of earlier runs. Empty output proves nothing unless
the item says the command must print nothing.
This command may check only PART of the item (other commands check the rest): judge only the part it can show. The guide sometimes allows later chapters to change a value
(for example "45 (46 after chapter 20)"): the robot has chapters 4-21 done, so a value from a later chapter can be right.
Answer as JSON with a one-sentence why quoting the deciding output."""


GUIDE_CMDS = False   # --guide-cmds: run the item's own backticked commands; the model plans only when there are none
EFFORT = None   # --effort: passed as reasoning_effort (gpt-oss: low/medium/high; Gemma in LM Studio: none/low/...)


def llm(model, prompt, schema, query, retrieve=True):
    body = {"model": model, "temperature": 0, "max_tokens": 8000, "lightbot_query": query, "lightbot_retrieve": retrieve,
            **({"reasoning_effort": EFFORT} if EFFORT else {}),
            "messages": [{"role": "user", "content": prompt}],
            "response_format": {"type": "json_schema", "json_schema": {"name": "out", "strict": True, "schema": schema}}}
    req = urllib.request.Request(BRAIN, json.dumps(body).encode(),
                                 {"Content-Type": "application/json", "Authorization": "Bearer " + KEY})
    t = time.time()
    txt = ""
    for attempt in range(3):          # an empty answer (thinking ran out of tokens) or a server error: retry, then give up
        try:
            r = json.load(urllib.request.urlopen(req, timeout=1200))
            txt = r["choices"][0]["message"]["content"] or ""
        except (OSError, ValueError, KeyError) as e:
            txt = ""
            print(f"    model request failed ({e}); retry {attempt + 1}", flush=True)
            time.sleep(10)
            continue
        if txt.strip():
            break
    m = re.search(r"\{.*\}", txt, re.S)
    try:
        return json.loads(m.group(0) if m else txt), time.time() - t
    except ValueError:
        return {"_raw": txt[:500]}, time.time() - t


def items(ch):
    path = next(GUIDE.glob(f"{ch}-*.md"))
    text = path.read_text()
    m = re.search(r"^## Before you move on\s*\n(.*?)(?=^## |^!!! source|\Z)", text, re.S | re.M)
    if not m:
        return path.name, []
    out, cur = [], None
    for line in m.group(1).splitlines():
        if line.startswith("- "):
            if cur: out.append(cur.strip())
            cur = line[2:]
        elif cur is not None and line.startswith("  "):
            cur += " " + line.strip()
        elif cur is not None and not line.strip():
            continue
    if cur: out.append(cur.strip())
    return path.name, out


ROBOT_SSH = re.compile(r"^\s*ssh\s+(?:-o\s+\S+\s+|-\w\s+)*(?:\w+@)?(?:rosorin(?:-wifi|-wired)?|192\.168\.1\.10[89])\s+(.*)$", re.S)
# ros2 CLI writes a log file per command and spawns a daemon: logs go to RAM (deleted after the run), list/info
# verbs get --no-daemon, and the daemon is stopped at the end if it was not running before (found 2026-10-14:
# earlier runs left ~134 python3_*.log files in the robot's ~/.ros/log and a running ros2 daemon).
ROS_LOGS = "/dev/shm/lightbot-check-ros"
ROS_ENV = (f"export ROS_LOG_DIR={ROS_LOGS} PYTHONDONTWRITEBYTECODE=1; source /opt/ros/humble/setup.bash; "
           "[ -f ~/ros2_ws/install/setup.bash ] && source ~/ros2_ws/install/setup.bash; ")


def normalize(host, cmd):
    """The model sometimes wraps robot commands in ssh or puts `source` under `timeout`: undo both."""
    m = ROBOT_SSH.match(cmd)
    if m and host in ("robot", "bigbuddy"):
        host, inner = "robot", m.group(1).strip()
        try:
            parts = shlex.split(inner)
            cmd = parts[0] if len(parts) == 1 else inner
        except ValueError:
            cmd = inner
    cmd = re.sub(r"timeout\s+(\d+[smh]?)\s+(source|\.)\s+(\S+)\s*(&&|;)\s*", r"\2 \3 \4 timeout \1 ", cmd)
    if host == "robot":
        cmd = re.sub(r"(?:^|(?<=&&)|(?<=;))\s*(?:source|\.)\s+/opt/ros/humble/setup\.bash\s*(?:&&|;)\s*", " ", cmd).strip()
        cmd = re.sub(r"\bros2 (topic|node|service|action|param|interface) (list|info|find|type)\b(?![^|;&]*--no-daemon)",
                     r"ros2 \1 \2 --no-daemon", cmd)
    return host, cmd


def robot_footprint(start):
    """Before a robot run: remember whether a ros2 daemon runs. After: delete the RAM logs, stop a daemon we started."""
    if SIM["on"]:
        return None
    ssh = HOSTS["robot"]
    if start:
        p = subprocess.run(ssh + ["bash -lc 'source /opt/ros/humble/setup.bash; ros2 daemon status'"],
                           capture_output=True, text=True, timeout=60)
        return "is running" in p.stdout
    subprocess.run(ssh + [f"rm -rf {ROS_LOGS}"], capture_output=True, timeout=60)


CMD_PROGS = readonly_gate.ALLOWED - {"for", "done", "do", "if", "then", "else", "fi", "true", "false", "echo",
                                     "printf", "test", "[", "cd", "sleep", "source", ".", "seq"}


NEEDS_OPERAND = {"bzip2", "sha256sum", "sha1sum", "md5sum", "cat", "grep", "diff", "cmp", "tar", "head", "tail",
                 "wc", "stat", "file", "readlink", "realpath", "zcat", "xxd", "od", "strings", "jq", "sort"}


def guide_commands(ch, item):
    """The commands the item itself states in backticks (e.g. `apt-mark showhold | grep -c nvidia-l4t`).
    Values (`45`, `/dev/nvme0n1p1 ext4`, `data: hello`) and commands with placeholders (`<set>`) are not commands."""
    out = []
    spans = re.findall(r"`([^`]+)`", item)
    for k, span in enumerate(spans):
        span = span.strip()
        if "<" in span and ">" in span:
            continue
        try:
            words = shlex.split(span)
        except ValueError:
            continue
        w = [x for x in words if x not in ("sudo", "-n")]
        if not w or w[0] not in CMD_PROGS or (len(w) == 1 and w[0] not in ("hostname", "uptime", "lsusb", "lsblk")):
            continue
        if w[0] in NEEDS_OPERAND and not [x for x in w[1:] if not x.startswith("-")]:
            continue                       # a fragment like `bzip2 -t` names a tool, not a runnable command
        host = "bigbuddy" if ch == "ch04" else "robot"
        cmd = span
        if re.match(r"^ros2 topic echo\b", cmd) and "--once" not in cmd:
            cmd = cmd.replace("ros2 topic echo", "ros2 topic echo --once", 1)
        if re.match(r"^ros2 ", cmd) and not cmd.startswith("timeout"):
            cmd = "timeout 15 " + cmd
        host, cmd = normalize(host, cmd)
        # literal expected output: the backticked text(s) right after the command, joined by prints/says/shows/lists
        lits = []
        after = item.split("`" + span + "`", 1)[1] if ("`" + span + "`") in item else ""
        for m in re.finditer(r"^\s*(?:still\s+|then\s+|now\s+)?(?:prints|says|shows|lists|reads|reports|ends with|gives|returns)\s+`([^`]+)`"
                             r"(?:\s*(?:and|,)\s*`([^`]+)`)?", after):
            lits += [x for x in m.groups() if x]
        out.append({"host": host, "cmd": cmd, "expect": "(the checklist item)", "literal": lits, "span": span})
    return out


BROKEN = re.compile(r"No such file or directory|Not a directory|cannot access|command not found|syntax error|"
                    r"does not appear to be published|Could not determine the type|unrecognized option|invalid option|"
                    r"Permission denied|Is a directory|unexpected EOF|timed out after", re.I)


def broken_command(rc, out):
    """A command that failed with a typical error proves nothing about the item: never a FAIL, at most UNSURE."""
    return rc not in (0, None) and bool(BROKEN.search(out or ""))


def uncovered_values(item, checks):
    """Backticked values in the item that are neither one of its commands nor a literal expected output of one."""
    used = {c["cmd"] for c in checks} | {c.get("span", "") for c in checks} | {l for c in checks for l in c.get("literal", [])}
    spans = [x.strip() for x in re.findall(r"`([^`]+)`", item)]
    return [x for x in spans if x not in used and not any(x in u for u in used) and not x.startswith(("~", "/"))]


def literal_pass(lits, out):
    """Deterministic PASS when every literal the item names appears in the output (whitespace-normalized).
    Never a deterministic FAIL: a missing literal goes to the model, which can accept legitimate differences."""
    if not lits:
        return False
    norm = lambda x: re.sub(r"\s+", " ", x).strip().lower()
    return all(norm(l) in norm(out) for l in lits)


RUN_TIMEOUT = 180   # archive checks, checksums of a golden set and the arm's 120 s auto-tuck need more than 90 s


SIM = {"on": False, "name": "lightbot-check", "image": "lightbot-sim:humble", "volume": "lightbot-sim-home"}


def sim_start():
    """--target sim: robot commands run in the test robot (container from brain/sim, its home volume, no network)."""
    subprocess.run(["docker", "rm", "-f", SIM["name"]], capture_output=True)
    p = subprocess.run(["docker", "run", "-d", "--rm", "--name", SIM["name"], "--network", "none", "--hostname", "rosorin",
                        "-v", f"{SIM['volume']}:/home", SIM["image"], "sleep", "infinity"], capture_output=True, text=True)
    if p.returncode:
        sys.exit("test robot container did not start: " + p.stderr)


def sim_stop():
    subprocess.run(["docker", "stop", "-t", "2", SIM["name"]], capture_output=True)


def run(host, cmd):
    if host == "robot" and SIM["on"]:
        argv = ["docker", "exec", "-u", "burgerbarn", "-w", "/home/burgerbarn", "-e", "HOME=/home/burgerbarn",
                SIM["name"], "bash", "-lc", ROS_ENV + cmd]
    elif host == "robot":
        argv = HOSTS["robot"] + [f"bash -lc {shlex.quote(ROS_ENV + cmd)}"]   # single quotes: nothing expands before the robot's shell
    else:
        argv = ["bash", "-lc", cmd]
    try:
        p = subprocess.run(argv, capture_output=True, text=True, timeout=RUN_TIMEOUT)
        return p.returncode, (p.stdout + p.stderr)[-4000:]
    except subprocess.TimeoutExpired:
        return 124, f"(timed out after {RUN_TIMEOUT} s)"


def check_chapter(ch, model, dry, outdir):
    fname, its = items(ch)
    rows = []
    for n, item in enumerate(its, 1):
        checks = guide_commands(ch, item) if GUIDE_CMDS else []
        plan, t_plan = {"note": "guide's own commands"}, 0
        if checks and uncovered_values(item, checks):
            extra, t_plan = llm(model, PLAN_PROMPT.format(ch=ch, item=item) +
                                "\nThese commands already run: " + "; ".join(c["cmd"] for c in checks) +
                                ". Plan checks only for the parts they do not show.", PLAN_SCHEMA, f"{ch} {item}")
            checks = checks + (extra.get("checks", []) if isinstance(extra, dict) else [])
        if not checks:
            plan, t_plan = llm(model, PLAN_PROMPT.format(ch=ch, item=item), PLAN_SCHEMA, f"{ch} {item}")
            checks = plan.get("checks", []) if isinstance(plan, dict) else []
        if not checks:
            rows.append({"item": n, "text": item, "verdict": "UNSURE", "why": f"no plan from model: {plan}", "checks": []})
            continue
        results = []
        for c in checks:
            host, cmd, expect = c.get("host"), c.get("cmd", ""), c.get("expect", "")
            if host in ("robot", "bigbuddy"):
                host, cmd = normalize(host, cmd)
            r = {"host": host, "cmd": cmd, "expect": expect}
            if host in ("hands", "later", "owner"):
                r.update(verdict={"hands": "NEEDS-HANDS", "later": "LATER", "owner": "NEEDS-OWNER"}[host], why=cmd)
            else:
                ok, why = readonly_gate.check(cmd)
                if not ok:
                    r.update(verdict="NEEDS-OWNER", why=f"gate refused: {why}")
                elif dry:
                    r.update(verdict="DRY", why="not run (--dry)")
                else:
                    rc, out = run(host, cmd)
                    if literal_pass(c.get("literal"), out):
                        r.update(rc=rc, out=out, verdict="PASS", why=f"literal match: {c['literal']}", judge_s=0)
                        results.append(r)
                        continue
                    j, t_j = llm(model, JUDGE_PROMPT.format(ch=ch, item=item, host=host, cmd=cmd, expect=expect,
                                                            rc=rc, out=out), JUDGE_SCHEMA, f"{ch} {item}", retrieve=False)
                    v = j.get("verdict", "UNSURE")
                    why = j.get("why", str(j)[:300])
                    if v == "FAIL" and broken_command(rc, out):
                        v, why = "UNSURE", "command error, not evidence (FAIL downgraded by code): " + why
                    r.update(rc=rc, out=out, verdict=v, why=why, judge_s=round(t_j))
            results.append(r)
        ran = [r for r in results if r["verdict"] in ("PASS", "DIFFERS", "FAIL", "UNSURE")]
        rest = [r for r in results if r not in ran and r.get("cmd", "").strip()]
        vs = [r["verdict"] for r in (ran if ran else rest or results)]
        verdict = ("FAIL" if "FAIL" in vs else "NEEDS-OWNER" if "NEEDS-OWNER" in vs
                   else "PASS-PARTIAL" if "UNSURE" in vs and ("PASS" in vs or "DIFFERS" in vs)
                   else "UNSURE" if "UNSURE" in vs
                   else "DIFFERS" if "DIFFERS" in vs
                   else "NEEDS-HANDS" if "NEEDS-HANDS" in vs else "LATER" if all(v == "LATER" for v in vs)
                   else "DRY" if "DRY" in vs else "PASS")
        rows.append({"item": n, "text": item, "verdict": verdict, "plan_s": round(t_plan), "checks": results,
                     "source": "guide" if plan.get("note") == "guide's own commands" else "model",
                     "note": plan.get("note", "")})
        print(f"  {ch} item {n}: {verdict}", flush=True)
    for r in rows:
        r["file"] = fname
    write_results(outdir, ch, model, rows)
    return rows


def write_results(outdir, ch, model, rows):
    with open(outdir / f"{ch}.jsonl", "w") as f:
        for r in rows: f.write(json.dumps(r) + "\n")
    md = [f"# {ch} ({rows[0].get('file', '') if rows else ''}): Before you move on, checked by bigbuddy ({model})", "",
          "| # | Item | bigbuddy | Commands (host) | Why |", "|---|---|---|---|---|"]
    for r in rows:
        cmds = "<br>".join(f"`{c['cmd'][:90]}` ({c['host']}): {c['verdict']}" +
                           (f" / Claude: {c['claude_verdict']}" if c.get("claude_verdict") else "") for c in r["checks"])
        why = "; ".join(c.get("why", "")[:160] + (f" [Claude: {c['claude_why'][:160]}]" if c.get("claude_why") else "")
                        for c in r["checks"]).replace("|", "/").replace("\n", " ")
        md.append(f"| {r['item']} | {r['text'][:120].replace('|', '/')} | {r['verdict']} | {cmds.replace('|', '/')} | {why} |")
    (outdir / f"{ch}.md").write_text("\n".join(md) + "\n")


def claude_review(outdir):
    """Claude's second opinion on a run's FAIL/UNSURE checks, only after the owner's yes (claude_gate.permission).
    Adds claude_verdict/claude_why to chNN.jsonl and chNN.md; the local verdicts stay as they are."""
    try:
        import claude_gate
    except ImportError:
        print("Claude not available: claude_gate.py not found next to guide_check.py", file=sys.stderr)
        return 1
    runs, todo = {}, []
    for p in sorted(outdir.glob("ch*.jsonl")):
        rows = [json.loads(l) for l in open(p)]
        runs[p.stem] = rows
        for r in rows:
            for c in r["checks"]:
                if c.get("verdict") in ("FAIL", "UNSURE") and "out" in c and not c.get("claude_verdict"):
                    todo.append((p.stem, r, c, JUDGE_PROMPT.format(ch=p.stem, item=r["text"], host=c["host"], cmd=c["cmd"],
                                                                  expect=c.get("expect", ""), rc=c.get("rc"), out=c["out"])))
    if not todo:
        print(f"no FAIL/UNSURE checks to review in {outdir}")
        return 0
    chars = sum(len(t[3]) for t in todo)
    if not claude_gate.permission(f"{len(todo)} check(s) are FAIL or UNSURE in {outdir.name} "
                                  f"({', '.join(sorted({t[0] for t in todo}))}).",
                                  f"those {len(todo)} checklist items, commands and outputs, one request each",
                                  chars, max_out=8000 * len(todo)):
        print(f"Not sent. Later, in a terminal: guide_check.py --claude-review {outdir}", file=sys.stderr)
        return 1
    schema = {**JUDGE_SCHEMA, "additionalProperties": False}
    for ch, r, c, prompt in todo:
        try:
            j = json.loads(claude_gate.ask("You judge command output against a robot build guide's checklist item.",
                                           prompt, "guide_check", schema=schema))
        except (claude_gate.ClaudeError, ValueError) as e:
            print(f"  Claude failed on {ch} item {r['item']}: {e}; stopping", file=sys.stderr)
            break
        c["claude_verdict"], c["claude_why"] = j["verdict"], j["why"]
        print(f"  {ch} item {r['item']}: local {c['verdict']}, Claude {j['verdict']}: {j['why'][:150]}", flush=True)
    model = outdir.name.split("-", 1)[1] if "-" in outdir.name else ""
    for ch, rows in runs.items():
        write_results(outdir, ch, model, rows)
    return 0


if __name__ == "__main__":
    T0 = time.time()
    ap = argparse.ArgumentParser()
    ap.add_argument("chapters", nargs="*")
    ap.add_argument("--claude-review", metavar="RUNDIR", help="Claude's second opinion on a run's FAIL/UNSURE checks")
    ap.add_argument("--model", default="lightbot-oss-xps")   # owner 2026-10-15: runner models on the XPS, Buddy stays on
    ap.add_argument("--dry", action="store_true")
    ap.add_argument("--effort")
    ap.add_argument("--guide-cmds", action="store_true")
    ap.add_argument("--target", choices=["robot", "sim"], default="robot")
    a = ap.parse_args()
    if a.claude_review:
        sys.exit(claude_review(Path(a.claude_review).expanduser()))
    if not a.chapters:
        ap.error("give one or more chapters, e.g. ch09")
    EFFORT = a.effort
    GUIDE_CMDS = a.guide_cmds
    SIM["on"] = a.target == "sim"
    if SIM["on"]:
        sim_start()
    DAEMON_BEFORE = robot_footprint(True)
    outdir = HOME / "llm-train/runs" / f"{time.strftime('%Y%m%dT%H%M')}-{a.model}{'-' + a.effort if a.effort else ''}{'-gc' if a.guide_cmds else ''}{'-sim' if a.target == 'sim' else ''}{'-dry' if a.dry else ''}"
    outdir.mkdir(parents=True, exist_ok=True)
    print(f"out: {outdir}", flush=True)
    for ch in a.chapters:
        rows = check_chapter(ch, a.model, a.dry, outdir)
        print(f"{ch}: " + ", ".join(f"{r['item']}={r['verdict']}" for r in rows), flush=True)
    if SIM["on"]:
        sim_stop()
    else:
        robot_footprint(False)
        if DAEMON_BEFORE is False:
            subprocess.run(HOSTS["robot"] + ["bash -lc 'source /opt/ros/humble/setup.bash; ros2 daemon stop'"],
                           capture_output=True, timeout=60)
    print(f"done in {(time.time() - T0) / 60:.0f} min", flush=True)
    if not a.dry and any(c.get("verdict") in ("FAIL", "UNSURE") for p in outdir.glob("ch*.jsonl")
                         for l in open(p) for c in json.loads(l)["checks"]):
        claude_review(outdir)
