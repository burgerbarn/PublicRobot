#!/usr/bin/env python3
"""Compare a guide_check.py run with the Claude rebuild session's results (guide/hw/results/chNN.md).

The results files record "Before you move on" per chapter (a RESULT line and a step-table row), not per item, so
this prints both side by side and counts bigbuddy's verdicts; disagreements are then read by hand.
  compare_runs.py ~/llm-train/runs/<run>
"""
import json, re, sys
from collections import Counter
from pathlib import Path

RESULTS = Path.home() / "llm-train/data/code-v2/rosorin-pro/guide/hw/results"
run = Path(sys.argv[1]).expanduser()
total = Counter()
print(f"| ch | bigbuddy items | Claude's record (RESULT line / Before-you-move-on row) |")
print("|---|---|---|")
for f in sorted(run.glob("ch*.jsonl")):
    ch = f.stem
    rows = [json.loads(l) for l in open(f)]
    c = Counter(r["verdict"] for r in rows)
    total.update(c)
    rec = ""
    rp = RESULTS / f"{ch}.md"
    if rp.exists():
        t = rp.read_text()
        m = re.search(r"^RESULT:\s*(.+)$", t, re.M)
        b = re.search(r"^\|[^|]*\|\s*Before you move on\s*\|[^|]*\|\s*([^|]+)\|", t, re.M)
        rec = ((m.group(1)[:110] if m else "-") + (" / " + b.group(1).strip()[:40] if b else ""))
    print(f"| {ch} | {', '.join(f'{k} {v}' for k, v in sorted(c.items()))} | {rec.replace('|', '/')} |")
print(f"\nTOTAL items: {sum(total.values())}: " + ", ".join(f"{k} {v}" for k, v in sorted(total.items())))
fails = [(f.stem, r) for f in sorted(run.glob("ch*.jsonl")) for r in map(json.loads, open(f)) if r["verdict"] in ("FAIL", "UNSURE", "DIFFERS")]
for ch, r in fails:
    print(f"\n{ch} item {r['item']} {r['verdict']}: {r['text'][:140]}")
    for c in r["checks"]:
        print(f"   {c['host']} {c['verdict']} | {c['cmd'][:110]}\n      out: {c.get('out', '')[:160]!r}\n      why: {c.get('why', '')[:200]}")


# ---- score against the per-item answer key (reference_items.jsonl) ----
REF = Path(__file__).with_name("reference_items.jsonl")
if REF.exists():
    ref = {(r["ch"], r["item"]): r for r in map(json.loads, open(REF))}
    got = {(f.stem, r["item"]): r for f in sorted(run.glob("ch*.jsonl")) for r in map(json.loads, open(f))}
    s = Counter()
    wrong = []
    for k, rr in ref.items():
        if k not in got:
            continue
        v, e = got[k]["verdict"], rr["expected"]
        s["items"] += 1
        if e == "PASS" and rr["checkable_readonly"]:
            s["pass_checkable"] += 1
            if v in ("PASS", "DIFFERS"): s["confirmed"] += 1
            elif v == "PASS-PARTIAL": s["partial"] += 1
            elif v == "FAIL": s["false_fail"] += 1; wrong.append((k, e, v))
            else: s["no_call"] += 1
        if e in ("FAIL", "NOT-RUN") and v in ("PASS", "PASS-PARTIAL"):
            s["false_pass"] += 1; wrong.append((k, e, v))
    print(f"\nSCORE vs answer key ({s['items']} items present): read-only-checkable PASS items {s['pass_checkable']}: "
          f"confirmed {s['confirmed']}, partly confirmed {s['partial']}, false FAIL {s['false_fail']}, no call {s['no_call']}; "
          f"false PASS (recorded FAIL/NOT-RUN judged PASS) {s['false_pass']}")
    for (ch, i), e, v in wrong:
        print(f"   {ch} item {i}: expected {e}, got {v}")
