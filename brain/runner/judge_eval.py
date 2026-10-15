#!/usr/bin/env python3
"""Offline judge comparison: replay stored check outputs (real robot runs) through a model's judge prompt and score
against the gold labels. No robot needed. Test split = chapters from --test-from on (default ch16).
  judge_eval.py lightbot-oss-xps lightbot-mac [--n 150] [--test-from ch16]
Prints per model: agreement, false FAIL (gold not FAIL, model FAIL), false PASS (gold FAIL/UNSURE, model PASS), seconds.
"""
import argparse, glob, json, os, random, sys, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import guide_check as g

D = os.path.expanduser("~/llm-train/data/judge")
ap = argparse.ArgumentParser()
ap.add_argument("models", nargs="+")
ap.add_argument("--n", type=int, default=150)
ap.add_argument("--test-from", default="ch16")
ap.add_argument("--effort", default="medium")
a = ap.parse_args()
g.EFFORT = a.effort

checks = {}
for f in sorted(glob.glob(f"{D}/checks*.jsonl")):
    checks.update({r["id"]: r for r in map(json.loads, open(f))})
gold = {}
for f in sorted(glob.glob(f"{D}/labels*.jsonl")):
    if "synth" in f:
        continue
    gold.update({r["id"]: r["gold"] for r in map(json.loads, open(f))})
rows = [dict(checks[i], gold=v) for i, v in gold.items() if i in checks and checks[i]["ch"] >= a.test_from]
random.Random(0).shuffle(rows)
rows = rows[: a.n]
print(f"{len(rows)} real checks from {a.test_from} on; gold " +
      ", ".join(f"{k} {sum(r['gold'] == k for r in rows)}" for k in ("PASS", "DIFFERS", "FAIL", "UNSURE")), flush=True)

for m in a.models:
    agree = ff = fp = 0
    t0 = time.time()
    for r in rows:
        j, _ = g.llm(m, g.JUDGE_PROMPT.format(ch=r["ch"], item=r["item_text"], host=r.get("host") or "robot",
                                             cmd=r["cmd"], expect="(the checklist item)", rc=r.get("rc"), out=r.get("out", "")),
                     g.JUDGE_SCHEMA, "", retrieve=False)
        v = j.get("verdict", "UNSURE") if isinstance(j, dict) else "UNSURE"
        if v == "FAIL" and g.broken_command(r.get("rc"), r.get("out", "")):
            v = "UNSURE"
        ok = v == r["gold"] or (v in ("PASS", "DIFFERS") and r["gold"] in ("PASS", "DIFFERS"))
        agree += ok
        ff += v == "FAIL" and r["gold"] != "FAIL"
        fp += v in ("PASS", "DIFFERS") and r["gold"] in ("FAIL", "UNSURE")
    dt = time.time() - t0
    print(f"{m:22} agree {agree}/{len(rows)} ({100 * agree // max(len(rows), 1)}%)  false FAIL {ff}  false PASS {fp}  "
          f"{dt / max(len(rows), 1):.1f} s/check", flush=True)
