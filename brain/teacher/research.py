#!/usr/bin/env python3
"""Research agent: the Claude API researches a question on the web (and the guide at wiki.matttesch.com), with
the local brain's excerpts as context, and turns the answer into fact cards the local model can retrieve.

Output: ~/llm-train/research/<time>-<slug>.md (report with sources) and new cards appended to
~/llm-train/data/facts/research.jsonl (indexed by brain_index.py; rebuild the index after a run).
API key: ~/llm-train/brain/anthropic_key (or ANTHROPIC_API_KEY).
  research.py "Is there a newer L4T than R36.4.3 for the Orin NX, and what changes for the ch341/rtw89 modules?"
  research.py --file questions.txt          (one question per line)
Limits per question: --searches (web searches, default 8), --fetches (page fetches, default 8), --max-turns (pause_turn
continuations, default 4). Server-tool use is billed per search on top of tokens.
"""
import argparse, datetime, json, os, re, sys, urllib.parse, urllib.request
from pathlib import Path
import anthropic

HOME = Path.home()
KEYFILE = HOME / "llm-train/brain/anthropic_key"
BRAIN_KEY = HOME / "llm-train/brain/key"
OUTDIR = HOME / "llm-train/research"
FACTS = HOME / "llm-train/data/facts/research.jsonl"
WIKI = "https://wiki.matttesch.com/lightbot"

SYSTEM = f"""You research questions for the owner of lightBot, a Hiwonder ROSOrin Pro robot (Jetson Orin NX,
L4T R36.4.3, Ubuntu 22.04, ROS 2 Humble), rebuilt from his own build guide at {WIKI} (read it with web_fetch when the
question touches the build). Excerpts from his repository and rebuild results are in the first message.
Answer with facts you verified in sources, each with its URL; say plainly what you could not verify. Prefer
primary sources (NVIDIA, ROS, vendor docs, release notes, source repositories). Be concise and specific: versions,
commands, file names, dates."""

CARD_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["cards"],
               "properties": {"cards": {"type": "array", "items": {
                   "type": "object", "additionalProperties": False, "required": ["text", "keywords", "source"],
                   "properties": {"text": {"type": "string"}, "keywords": {"type": "array", "items": {"type": "string"}},
                                  "source": {"type": "string"}}}}}}


def client():
    key = os.environ.get("ANTHROPIC_API_KEY") or (KEYFILE.read_text().strip() if KEYFILE.exists() else None)
    if not key:
        sys.exit(f"no API key: put it in {KEYFILE} (chmod 600) or ANTHROPIC_API_KEY")
    return anthropic.Anthropic(api_key=key)


def brain_context(q, k=6):
    """Top excerpts from the local brain's index (bigbuddy 127.0.0.1:8300), so the research starts from what is known."""
    try:
        req = urllib.request.Request(f"http://127.0.0.1:8300/search?q={urllib.parse.quote(q)}&k={k}",
                                     headers={"Authorization": "Bearer " + BRAIN_KEY.read_text().strip()})
        hits = json.load(urllib.request.urlopen(req, timeout=20))["hits"]
        return "\n\n".join(f"[{h['src']}]\n{h['text'][:1500]}" for h in hits)
    except Exception as e:
        return f"(local brain not reachable: {e})"


def research(c, q, a):
    tools = [{"type": "web_search_20260209", "name": "web_search", "max_uses": a.searches},
             {"type": "web_fetch_20260209", "name": "web_fetch", "max_uses": a.fetches}]
    messages = [{"role": "user", "content": f"Known locally (repo and rebuild records):\n{brain_context(q)}\n\n"
                                            f"Question: {q}"}]
    usage = {"input": 0, "output": 0}
    for turn in range(a.max_turns):
        with c.beta.messages.stream(model=a.model, max_tokens=32000, system=SYSTEM, tools=tools, messages=messages,
                                    output_config={"effort": a.effort}, betas=["server-side-fallback-2026-07-01"],
                                    fallbacks="default") as s:
            msg = s.get_final_message()
        usage["input"] += msg.usage.input_tokens
        usage["output"] += msg.usage.output_tokens
        if msg.stop_reason == "refusal":
            return "(refused)", usage
        messages.append({"role": "assistant", "content": msg.content})
        if msg.stop_reason != "pause_turn":
            break
    text = "\n".join(b.text for b in msg.content if b.type == "text")
    return text, usage


def cards(c, q, report, a):
    r = c.messages.create(model=a.model, max_tokens=8000, output_config={
        "effort": "low", "format": {"type": "json_schema", "schema": CARD_SCHEMA}},
        messages=[{"role": "user", "content": "Turn this research report into fact cards for a small local model's "
                   "retrieval index: one verified fact per card, self-contained (names the hardware/software and "
                   "version it is about), with search keywords and the source URL. Skip anything unverified.\n\n"
                   f"Question: {q}\n\nReport:\n{report}"}])
    if r.stop_reason == "refusal":
        return []
    return json.loads(next(b.text for b in r.content if b.type == "text"))["cards"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("question", nargs="?")
    ap.add_argument("--file")
    ap.add_argument("--model", default="claude-opus-5-5")
    ap.add_argument("--effort", default="high")
    ap.add_argument("--searches", type=int, default=8)
    ap.add_argument("--fetches", type=int, default=8)
    ap.add_argument("--max-turns", type=int, default=4)
    a = ap.parse_args()
    qs = [a.question] if a.question else [l.strip() for l in open(a.file) if l.strip() and not l.startswith("#")]
    if not qs:
        sys.exit("no question")
    c = client()
    OUTDIR.mkdir(parents=True, exist_ok=True)
    FACTS.parent.mkdir(parents=True, exist_ok=True)
    for q in qs:
        stamp = datetime.datetime.now().strftime("%Y%m%dT%H%M")
        slug = re.sub(r"[^a-z0-9]+", "-", q.lower())[:50].strip("-")
        report, usage = research(c, q, a)
        path = OUTDIR / f"{stamp}-{slug}.md"
        path.write_text(f"# {q}\n\n{report}\n\n---\n{stamp}, {a.model}, tokens in {usage['input']} out {usage['output']}\n")
        new = cards(c, q, report, a) if report and report != "(refused)" else []
        with open(FACTS, "a") as f:
            for i, card in enumerate(new):
                f.write(json.dumps({"id": f"r{stamp}-{i:02d}", "text": card["text"], "keywords": card["keywords"],
                                    "source": card["source"], "question": q}) + "\n")
        print(f"{path.name}: {len(new)} cards, tokens in {usage['input']} out {usage['output']}")
    print(f"rebuild the index so the local model sees them: ~/llm-train/bin/python ~/llm-train/brain/brain_index.py")


if __name__ == "__main__":
    main()
