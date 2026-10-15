#!/usr/bin/env python3
"""Synthetic judge training cases: realistic FAIL / UNSURE / DIFFERS outputs for the checklist items, so the local
judge learns what real failures look like (the healthy robot rarely produced them). Claude API, Message Batches.

For each labeled real check that is PASS, the model writes N variants of the same command's output for a broken or
changed robot (service inactive, package missing, wrong version, cable unplugged, permission denied, timeout,
partially met, legitimately newer value ...) with the correct verdict and why. Output in the judge's format:
~/llm-train/data/judge/synth_checks.jsonl + synth_labels.jsonl (judge_train.py reads labels*.jsonl and checks*.jsonl).
  synth_fails.py [--per 8] [--limit N] [--model claude-opus-5-5]
"""
import argparse, glob, json, os, sys, time
from pathlib import Path
import anthropic
from anthropic.types.message_create_params import MessageCreateParamsNonStreaming
from anthropic.types.messages.batch_create_params import Request

HOME = Path.home()
KEYFILE = HOME / "llm-train/brain/anthropic_key"
D = HOME / "llm-train/data/judge"

PROMPT = """A robot build-guide checker ran this read-only command for this checklist item and the output passed.
Write {n} realistic variants of the OUTPUT of the same command on a robot where something is broken or different,
as a Jetson/Ubuntu 22.04/ROS 2 Humble system would really print them. Mix: clearly not met (FAIL: inactive service,
missing package/file, wrong value that breaks the step, stopped topic), command-level problems (UNSURE: permission
denied, timeout, wrong path, empty output where content is needed), legitimately different but working (DIFFERS:
newer version, slightly different rate), and look-alike traps (e.g. APP_new vs APP, p1 vs p16, a similar unit name).
For each: the exit code, the output text, the correct verdict for an ideal judge, and why (one sentence quoting the
deciding output). A broken command is UNSURE, never FAIL.

Checklist item ({ch} item {item}): {item_text}
Command (host {host}): {cmd}
Real passing output:
{out}"""

SCHEMA = {"type": "object", "additionalProperties": False, "required": ["variants"],
          "properties": {"variants": {"type": "array", "items": {
              "type": "object", "additionalProperties": False, "required": ["rc", "out", "gold", "why"],
              "properties": {"rc": {"type": "integer"}, "out": {"type": "string"},
                             "gold": {"type": "string", "enum": ["PASS", "DIFFERS", "FAIL", "UNSURE"]},
                             "why": {"type": "string"}}}}}}


def keep_or_replace(tmps, outs, n, bad, what):
    """Results go to *.tmp first: replace the old files only if something succeeded (credit gone = all requests errored
    must not wipe earlier data); exit 1 when any request failed."""
    if n == 0:
        for t in tmps:
            t.unlink(missing_ok=True)
        sys.exit(f"0 {what} ({bad} requests errored, refused or unparseable): kept {', '.join(map(str, outs))} as it was")
    for t, o in zip(tmps, outs):
        os.replace(t, o)
    print(f"wrote {n} {what} -> {', '.join(map(str, outs))}")
    if bad:
        sys.exit(f"{bad} requests errored, refused or unparseable (see above); the {n} {what} that succeeded were written")


def client():
    key = os.environ.get("ANTHROPIC_API_KEY") or (KEYFILE.read_text().strip() if KEYFILE.exists() else None)
    if not key:
        sys.exit(f"no API key in {KEYFILE}")
    return anthropic.Anthropic(api_key=key)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--per", type=int, default=8)
    ap.add_argument("--limit", type=int)
    ap.add_argument("--model", default="claude-opus-5-5")
    a = ap.parse_args()
    checks = {}
    for f in [D / "checks.jsonl", D / "checks_sim.jsonl"]:
        if f.exists():
            checks.update({r["id"]: r for r in map(json.loads, open(f))})
    seen, base = set(), []
    for f in sorted(glob.glob(str(D / "labels*.jsonl"))):
        for lab in map(json.loads, open(f)):
            r = checks.get(lab["id"])
            if r and lab["gold"] == "PASS" and (r["ch"], r["item"], r["cmd"]) not in seen:
                seen.add((r["ch"], r["item"], r["cmd"]))
                base.append(r)
    base = base[: a.limit]
    print(f"{len(base)} passing checks -> {len(base) * a.per} variants", flush=True)
    c = client()
    reqs = [Request(custom_id=r["id"], params=MessageCreateParamsNonStreaming(
        model=a.model, max_tokens=16000, output_config={"effort": "medium", "format": {"type": "json_schema", "schema": SCHEMA}},
        messages=[{"role": "user", "content": PROMPT.format(n=a.per, **{k: r.get(k) for k in ("ch", "item", "item_text", "host", "cmd")},
                                                             out=r.get("out", "")[:2000])}])) for r in base]
    b = c.messages.batches.create(requests=reqs)
    print(f"batch {b.id}", flush=True)
    while c.messages.batches.retrieve(b.id).processing_status != "ended":
        time.sleep(60)
    by_id = {r["id"]: r for r in base}
    nc, bad = 0, 0
    outs = [D / "synth_checks.jsonl", D / "synth_labels.jsonl"]
    tmps = [o.with_suffix(".jsonl.tmp") for o in outs]
    with open(tmps[0], "w") as fc, open(tmps[1], "w") as fl:
        for res in c.messages.batches.results(b.id):
            if res.result.type != "succeeded" or res.result.message.stop_reason == "refusal":
                print(f"  {res.custom_id}: {res.result.type}", file=sys.stderr)
                bad += 1
                continue
            text = next((x.text for x in res.result.message.content if x.type == "text"), "")
            try:
                variants = json.loads(text)["variants"]
            except (ValueError, KeyError):
                print(f"  {res.custom_id}: unparseable", file=sys.stderr)
                bad += 1
                continue
            r = by_id[res.custom_id]
            for i, v in enumerate(variants):
                sid = f"x{res.custom_id}-{i:02d}"
                fc.write(json.dumps({"id": sid, "ch": r["ch"], "item": r["item"], "item_text": r["item_text"], "host": r.get("host"),
                                     "cmd": r["cmd"], "rc": v["rc"], "out": v["out"], "model_verdict": None, "run": "synthetic"}) + "\n")
                fl.write(json.dumps({"id": sid, "gold": v["gold"], "why": v["why"], "model_wrong": False, "teacher": res.result.message.model}) + "\n")
                nc += 1
    print(f"{nc} synthetic checks from the batch")
    keep_or_replace(tmps, outs, nc, bad, "synthetic checks")


if __name__ == "__main__":
    main()
