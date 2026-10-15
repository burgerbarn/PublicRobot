#!/usr/bin/env python3
"""Teacher labels for the local judge, through the Claude API (Message Batches: 50% price, no rush).

Reads checks (one JSON per line: id, ch, item, item_text, cmd, rc, out[, model_verdict]) and writes gold labels
{id, gold, why, model_wrong} in the format judge_train.py reads (~/llm-train/data/judge/labels*.jsonl).
The answer key's row for the item (expected verdict + notes on legitimate differences) goes with every request.
API key: ~/llm-train/brain/anthropic_key (or ANTHROPIC_API_KEY).
  teacher_label.py CHECKS.jsonl OUT_LABELS.jsonl [--model claude-opus-5-5] [--limit N] [--wait]
  teacher_label.py --collect BATCH_ID CHECKS.jsonl OUT_LABELS.jsonl      (results of an earlier batch)
"""
import argparse, json, os, sys, time
from pathlib import Path
import anthropic
from anthropic.types.message_create_params import MessageCreateParamsNonStreaming
from anthropic.types.messages.batch_create_params import Request

HOME = Path.home()
KEYFILE = HOME / "llm-train/brain/anthropic_key"
ANSWER_KEY = HOME / "llm-train/brain/reference_items.jsonl"

RULES = """You label checks for training a small local "judge" model of a robot build-guide checker.
Each check: one checklist item of the lightBot build guide ("Before you move on"), one command that was run, its
exit code and output. Decide the verdict an ideal judge gives for THIS ONE command and output against the item.
A command may check only part of the item: judge only the part it can show.
- PASS: the output shows the item's criterion (or the part this command checks) is met.
- DIFFERS: it works, but a number/version/format differs from the guide's text in a way the notes show is legitimate.
- FAIL: the output clearly shows the criterion is NOT met.
- UNSURE: the output cannot decide it (command error, wrong path/column/user, empty output where content was
  needed, timeout, a check of something the item does not ask, record/knowledge/looking items such as
  "You have written down...", "You know...", "looks like your room").
Rules: decide from the output text; never infer success from records when the output does not show it; a broken
command is UNSURE, never FAIL; empty output or the exit code is PASS where that is the expected answer
(`grep -q` rc 0, `systemctl --failed` printing nothing); watch look-alikes (APP vs APP_new, p1 vs p16, before/after
a chapter). The robot was rebuilt with chapter 5's p1 variant: root on /dev/nvme0n1p1, p1 = APP, p16 = APP_new.
`why`: one sentence quoting the deciding output, at most 200 characters."""

SCHEMA = {"type": "object", "additionalProperties": False, "required": ["gold", "why"],
          "properties": {"gold": {"type": "string", "enum": ["PASS", "DIFFERS", "FAIL", "UNSURE"]},
                         "why": {"type": "string"}}}


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
        sys.exit(f"no API key: put it in {KEYFILE} (chmod 600) or ANTHROPIC_API_KEY")
    return anthropic.Anthropic(api_key=key)


def answer_key():
    if not ANSWER_KEY.exists():
        return {}
    return {(r["ch"], r["item"]): r for r in map(json.loads, open(ANSWER_KEY))}


def user_text(r, ref):
    note = ""
    if ref:
        note = (f"\nRecorded rebuild for this item: expected {ref.get('expected')}; evidence: {ref.get('evidence', '')}"
                f"\nNotes: {ref.get('note', '')}")
    return (f"Chapter {r['ch']}, item {r['item']}: {r['item_text']}{note}\n\nCommand (host {r.get('host')}): {r['cmd']}"
            f"\nExit code: {r.get('rc')}\nOutput:\n{r.get('out', '')}")


def collect(c, batch_id, rows, out_path):
    by_id = {r["id"]: r for r in rows}
    n, bad, tmp = 0, 0, Path(str(out_path) + ".tmp")
    with open(tmp, "w") as f:
        for res in c.messages.batches.results(batch_id):
            if res.result.type != "succeeded":
                print(f"  {res.custom_id}: {res.result.type}", file=sys.stderr)
                bad += 1
                continue
            msg = res.result.message
            if msg.stop_reason == "refusal":
                print(f"  {res.custom_id}: refusal", file=sys.stderr)
                bad += 1
                continue
            text = next((b.text for b in msg.content if b.type == "text"), "")
            try:
                lab = json.loads(text)
            except ValueError:
                print(f"  {res.custom_id}: unparseable", file=sys.stderr)
                bad += 1
                continue
            r = by_id.get(res.custom_id, {})
            mv = r.get("model_verdict")
            lab.update(id=res.custom_id, model_wrong=bool(mv) and mv != lab["gold"], teacher=msg.model)
            f.write(json.dumps(lab) + "\n")
            n += 1
    print(f"{n} labels from the batch")
    keep_or_replace([tmp], [Path(out_path)], n, bad, "labels")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("checks")
    ap.add_argument("out")
    ap.add_argument("--model", default="claude-opus-5-5")
    ap.add_argument("--effort", default="medium")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--wait", action="store_true", help="poll until the batch ends and write the labels")
    ap.add_argument("--collect", metavar="BATCH_ID")
    a = ap.parse_args()
    rows = [json.loads(l) for l in open(a.checks)][: a.limit]
    c = client()
    if a.collect:
        return collect(c, a.collect, rows, a.out)
    refs = answer_key()
    reqs = [Request(custom_id=r["id"], params=MessageCreateParamsNonStreaming(
        model=a.model, max_tokens=4000,
        system=[{"type": "text", "text": RULES, "cache_control": {"type": "ephemeral"}}],
        output_config={"effort": a.effort, "format": {"type": "json_schema", "schema": SCHEMA}},
        messages=[{"role": "user", "content": user_text(r, refs.get((r["ch"], r["item"])))}]))
        for r in rows]
    batch = c.messages.batches.create(requests=reqs)
    print(f"batch {batch.id}: {len(reqs)} requests, model {a.model}")
    Path(a.out + ".batch_id").write_text(batch.id)
    if not a.wait:
        print(f"collect later: teacher_label.py --collect {batch.id} {a.checks} {a.out}")
        return
    while True:
        b = c.messages.batches.retrieve(batch.id)
        if b.processing_status == "ended":
            break
        print(f"  {b.processing_status}: processing {b.request_counts.processing}", flush=True)
        time.sleep(60)
    print(f"ended: succeeded {b.request_counts.succeeded}, errored {b.request_counts.errored}")
    collect(c, batch.id, rows, a.out)


if __name__ == "__main__":
    main()
