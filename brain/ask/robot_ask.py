#!/usr/bin/env python3
"""Ask questions about lightBot and its rebuild.

BM25 search over the build guide, the rebuild results, the fact cards and the Q/A pairs in
~/llm-train/data; the top excerpts go to the model LM Studio serves on :1234 (Buddy's Gemma).

  robot_ask.py "question"           answer + sources
  robot_ask.py --show "question"    also print the retrieved excerpts
  robot_ask.py --reindex            rebuild the index after the data changed
  robot_ask.py --claude "question"  ask Claude with the same excerpts (asks you first; claude_gate.py)

When the local model is not reachable, or its answer says "Not in the guide:", it offers to ask Claude: only in a
terminal (ssh -t), only after you type yes, logged with its cost (~/llm-train/brain/claude_calls.jsonl).

Measured 2026-10-13 (Gemma 4 12B, top 6): 20/22 on a held-out question set. It still gets some
facts wrong: check any partition, boot or other destructive command against the guide before running it.
"""
import json, math, os, re, sys, time, urllib.request
from collections import Counter
from pathlib import Path

# claude_gate.py: repo brain/server/, deployed to ~/llm-train/brain/ (sync_brain.sh)
sys.path[:0] = [str(Path(__file__).resolve().parent.parent / "server"), os.path.expanduser("~/llm-train/brain")]

DATA = Path(os.environ.get("ROBOT_ASK_DATA", os.path.expanduser("~/llm-train/data")))
INDEX = Path(os.environ.get("ROBOT_ASK_INDEX", os.path.expanduser("~/llm-train/ask/index.json")))
URL = os.environ.get("ROBOT_ASK_URL", "http://localhost:1234/v1/chat/completions")
MODEL = os.environ.get("ROBOT_ASK_MODEL", "google/gemma-4-12b")
K = int(os.environ.get("ROBOT_ASK_K", "6"))
CHUNK_CHARS = 2400  # ~600 tokens

PROMPT = """You answer questions about lightBot (a Hiwonder ROSOrin Pro robot on a Jetson) and its rebuild.
Excerpts from the build guide, the rebuild results and fact cards follow.

Rules:
- Use the excerpts first. Quote exact commands, numbers, names and paths from them.
- When excerpts describe two similar things (old vs new partition, two directions of a rename, before vs after a fix), say which one answers the question.
- If the excerpts do not cover the question, answer from general knowledge and begin that part with "Not in the guide:".
- End with the excerpt sources you used, in [brackets].

{ctx}

Question: {q}"""


def terms(s):
    return re.findall(r"[a-z0-9_./-]+", s.lower())


def build():
    chunks = []
    guide = DATA / "code-v2" / "rosorin-pro" / "guide"   # kept fresh by sync_brain.sh / pull_github.sh
    if not guide.is_dir():
        guide = DATA / "rosorin-pro" / "guide"           # older layout
    for p in sorted(guide.rglob("*.md")):
        rel, cur = str(p.relative_to(guide.parent)), ""
        for part in re.split(r"\n(?=#{1,4} )", p.read_text(errors="replace")):
            if cur and len(cur) + len(part) > CHUNK_CHARS:
                chunks.append([rel, cur]); cur = ""
            cur += "\n" + part
        if cur.strip():
            chunks.append([rel, cur])
    qa = DATA / "qa" / "train.jsonl"
    if qa.exists():
        for l in open(qa):
            r = json.loads(l)
            chunks.append([r.get("source", "qa"), "Q: " + r["messages"][0]["content"] + "\nA: " + r["messages"][1]["content"]])
    facts = DATA / "facts" / "facts.jsonl"
    if facts.exists():
        for l in open(facts):
            r = json.loads(l)
            chunks.append([f'{r["id"]} ({r.get("source", "")})', r["text"] + "\nKeywords: " + ", ".join(r.get("keywords", []))])
    INDEX.parent.mkdir(parents=True, exist_ok=True)
    json.dump({"built": time.strftime("%Y-%m-%d %H:%M"), "chunks": chunks}, open(INDEX, "w"))
    return chunks


class Index:
    def __init__(self, chunks):
        self.chunks = chunks
        self.docs = [Counter(terms(c)) for _, c in chunks]
        self.avg = sum(sum(d.values()) for d in self.docs) / len(self.docs)
        self.df = Counter(t for d in self.docs for t in d)

    def top(self, q, k=K, k1=1.5, b=0.75):
        N, qt = len(self.docs), set(terms(q))
        def score(d):
            n = sum(d.values())
            return sum(math.log(1 + (N - self.df[t] + .5) / (self.df[t] + .5)) * d[t] * (k1 + 1) /
                       (d[t] + k1 * (1 - b + b * n / self.avg)) for t in qt if t in d)
        return sorted(range(N), key=lambda i: -score(self.docs[i]))[:k]


def load():
    if not INDEX.exists():
        return Index(build())
    return Index(json.load(open(INDEX))["chunks"])


def retrieve(q, index=None):
    index = index or load()
    top = [index.chunks[i] for i in index.top(q)]
    return top, PROMPT.format(ctx="\n\n".join(f"[{s}]\n{c.strip()}" for s, c in top), q=q)


def ask(q, index=None, max_tokens=8000):
    """Returns (answer, retrieved [(source, text)], usage)."""
    top, prompt = retrieve(q, index)
    body = json.dumps({"model": MODEL, "temperature": 0, "max_tokens": max_tokens,
                       "messages": [{"role": "user", "content": prompt}]}).encode()
    req = urllib.request.Request(URL, body, {"Content-Type": "application/json"})
    r = json.load(urllib.request.urlopen(req, timeout=900))
    ans = r["choices"][0]["message"]["content"].strip()
    if r["choices"][0].get("finish_reason") == "length" and not ans:
        ans = "(no answer: the model ran out of tokens while thinking)"
    return ans, top, r.get("usage", {})


def offer_claude(q, reason):
    """Claude with the same excerpts, only after the owner's yes (claude_gate.permission). Returns an exit code."""
    try:
        import claude_gate
    except ImportError:
        print("Claude not available: claude_gate.py not found next to the brain (~/llm-train/brain)", file=sys.stderr)
        return 1
    top, prompt = retrieve(q)
    if not claude_gate.permission(reason, f"your question and {len(top)} excerpts of the guide/results", len(prompt)):
        print(f'Not sent. To ask Claude later, in a terminal: robot_ask.py --claude "{q}"', file=sys.stderr)
        return 1
    try:
        ans = claude_gate.ask("Answer from the excerpts; they are the project's own records.", prompt, "robot_ask")
    except claude_gate.ClaudeError as e:
        print(f"Claude failed: {e}", file=sys.stderr)
        return 1
    print("\n[Claude]\n" + ans)
    print(f"\n-- retrieved: {', '.join(s for s, _ in top)}")
    return 0


def main(argv):
    if argv[:1] == ["--reindex"]:
        print(f"indexed {len(build())} chunks -> {INDEX}")
        return 0
    if argv[:1] in (["--help"], ["-h"]):
        print(__doc__); return 0
    if argv[:1] == ["--claude"]:
        q = " ".join(argv[1:]).strip()
        return offer_claude(q, "You asked for Claude.") if q else 2
    show = argv[:1] == ["--show"]
    q = " ".join(argv[1:] if show else argv).strip()
    if not q:
        print(__doc__); return 2
    t = time.time()
    try:
        ans, top, usage = ask(q)
    except OSError as e:
        print(f"LM Studio not reachable at {URL} ({e}). Is Buddy paused for a training run?", file=sys.stderr)
        return offer_claude(q, "The local model is not reachable.")
    print(ans)
    print(f"\n-- retrieved: {', '.join(s for s, _ in top)}  ({time.time() - t:.0f} s)")
    if show:
        for s, c in top:
            print(f"\n==== [{s}]\n{c.strip()}")
    if "not in the guide" in ans.lower():
        offer_claude(q, "The local answer says part of it is not in the guide.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
