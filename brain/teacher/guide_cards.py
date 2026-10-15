#!/usr/bin/env python3
"""Fact cards from every guide chapter for the local brain's index (Claude API, Message Batches).

One request per chapter: the model reads the whole chapter and writes self-contained cards (commands with their
host, expected outputs, values, pitfalls and their fixes, stop points). Output: ~/llm-train/data/facts/guide_cards.jsonl
(brain_index.py indexes every file in data/facts). Rebuild the index afterwards.
  guide_cards.py [--model claude-opus-5-5] [--chapters ch05 ch11 ...] [--collect BATCH_ID]
"""
import argparse, json, os, sys, time
from pathlib import Path
import anthropic
from anthropic.types.message_create_params import MessageCreateParamsNonStreaming
from anthropic.types.messages.batch_create_params import Request

HOME = Path.home()
KEYFILE = HOME / "llm-train/brain/anthropic_key"
GUIDE = HOME / "llm-train/data/code-v2/rosorin-pro/guide/chapters"
OUT = HOME / "llm-train/data/facts/guide_cards.jsonl"

PROMPT = """Below is one chapter of the lightBot build guide (a Hiwonder ROSOrin Pro robot on a Jetson Orin NX, rebuilt
by its owner; code blocks say where they run: @robot, @bigbuddy (Fedora build PC), @mac, @server).
Write fact cards for a small local model that will follow this guide and answer questions about it. One fact per
card, self-contained (repeat the chapter and the component it is about), exact (commands verbatim with their host,
paths, numbers, expected output lines, versions), covering: what each step does and where it runs, its check and
the expected output, known failures and their fixes, values that legitimately differ and why, stop points that need
the owner (erase, partitions, reboot, first motion, hands-on), and dependencies on other chapters. 20 to 80 cards,
no invented facts. Keywords: the words someone would search with."""

SCHEMA = {"type": "object", "additionalProperties": False, "required": ["cards"],
          "properties": {"cards": {"type": "array", "items": {
              "type": "object", "additionalProperties": False, "required": ["text", "keywords", "section"],
              "properties": {"text": {"type": "string"}, "keywords": {"type": "array", "items": {"type": "string"}},
                             "section": {"type": "string"}}}}}}


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


def collect(c, batch_id):
    n, bad, tmp = 0, 0, OUT.with_suffix(".jsonl.tmp")
    with open(tmp, "w") as f:
        for res in c.messages.batches.results(batch_id):
            if res.result.type != "succeeded" or res.result.message.stop_reason == "refusal":
                print(f"  {res.custom_id}: {res.result.type}", file=sys.stderr)
                bad += 1
                continue
            text = next((b.text for b in res.result.message.content if b.type == "text"), "")
            try:
                cards = json.loads(text)["cards"]
            except (ValueError, KeyError):
                print(f"  {res.custom_id}: unparseable", file=sys.stderr)
                bad += 1
                continue
            for i, card in enumerate(cards):
                f.write(json.dumps({"id": f"g-{res.custom_id}-{i:03d}", "text": card["text"],
                                    "keywords": card["keywords"], "source": f"guide/chapters/{res.custom_id} ({card['section']})"}) + "\n")
                n += 1
    print(f"{n} cards from the batch")
    keep_or_replace([tmp], [OUT], n, bad, "cards")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="claude-opus-5-5")
    ap.add_argument("--chapters", nargs="*")
    ap.add_argument("--collect")
    a = ap.parse_args()
    c = client()
    if a.collect:
        return collect(c, a.collect)
    files = sorted(GUIDE.glob("ch*.md"))
    if a.chapters:
        files = [f for f in files if f.name[:4] in a.chapters]
    reqs = [Request(custom_id=f.stem[:60], params=MessageCreateParamsNonStreaming(
        model=a.model, max_tokens=32000, output_config={"effort": "medium", "format": {"type": "json_schema", "schema": SCHEMA}},
        messages=[{"role": "user", "content": PROMPT + "\n\n" + f.read_text()}])) for f in files]
    b = c.messages.batches.create(requests=reqs)
    print(f"batch {b.id}: {len(reqs)} chapters", flush=True)
    while c.messages.batches.retrieve(b.id).processing_status != "ended":
        time.sleep(60)
    collect(c, b.id)


if __name__ == "__main__":
    main()
