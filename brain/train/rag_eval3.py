# Retrieval eval, round 3: same BM25 index as rag_eval2.py, reader = the LM Studio model on :1234 (no extra GPU).
# FACTS picks the card file; temperature 0; only the final answer (not reasoning_content) is scored.
import os
os.environ.setdefault("PYTORCH_CUDA_ALLOC_CONF", "expandable_segments:True")
import re, math, json, time, urllib.request
from collections import Counter
from pathlib import Path
from transformers import AutoTokenizer

MODEL = "Qwen/Qwen2.5-Coder-7B-Instruct"  # tokenizer for chunk packing only, as in round 2
READER = os.environ.get("READER", "google/gemma-4-12b")
FACTS = os.environ.get("FACTS", "facts_v1.jsonl")
RUNS = os.environ.get("RUNS", "eval2,eval").split(",")
D = Path(os.path.expanduser("~/llm-train/data"))
OUT = os.environ.get("OUT", "out-rag3")
K = int(os.environ.get("K", "6"))
os.makedirs(OUT, exist_ok=True)
tok = AutoTokenizer.from_pretrained(MODEL)

def norm(s):
    s = s.lower().replace("–", "-").replace("—", "-").replace("−", "-")
    s = re.sub(r"(?<=\d),(?=\d{3}\b)", "", s)
    s = re.sub(r"\s+%", "%", s)
    return re.sub(r"\s+", " ", s)

# Base index (round 1): guide markdown split on headings and packed to ~600 tokens, plus Q/A train pairs.
base_chunks = []
for p in sorted((D / "rosorin-pro/guide").rglob("*.md")):
    rel, cur = str(p.relative_to(D / "rosorin-pro")), ""
    for part in re.split(r"\n(?=#{1,4} )", p.read_text(errors="replace")):
        if cur and len(tok(cur + part)["input_ids"]) > 600:
            base_chunks.append((rel, cur)); cur = ""
        cur += "\n" + part
    if cur.strip(): base_chunks.append((rel, cur))
for l in open(D / "qa/train.jsonl"):
    r = json.loads(l)
    base_chunks.append((r.get("source", "qa"), "Q: " + r["messages"][0]["content"] + "\nA: " + r["messages"][1]["content"]))
cards = []
for l in open(FACTS):
    r = json.loads(l)
    cards.append((r["id"], r["text"] + "\nKeywords: " + ", ".join(r.get("keywords", []))))

def terms(s): return re.findall(r"[a-z0-9_./-]+", s.lower())
class Index:
    def __init__(self, chunks):
        self.chunks = chunks
        self.docs = [Counter(terms(c)) for _, c in chunks]
        self.avg = sum(sum(d.values()) for d in self.docs) / len(self.docs)
        self.df = Counter(t for d in self.docs for t in d)
    def top(self, q, k1=1.5, b=0.75):
        N, qt = len(self.docs), set(terms(q))
        def s(d):
            n = sum(d.values())
            return sum(math.log(1 + (N - self.df[t] + .5) / (self.df[t] + .5)) * d[t] * (k1 + 1) /
                       (d[t] + k1 * (1 - b + b * n / self.avg)) for t in qt if t in d)
        return sorted(range(N), key=lambda i: -s(self.docs[i]))[:K]

def answer(msg):
    body = json.dumps({"model": READER, "temperature": 0, "max_tokens": int(os.environ.get("MAXTOK", "8000")),
                       "messages": [{"role": "user", "content": msg}]}).encode()
    req = urllib.request.Request("http://localhost:1234/v1/chat/completions", body,
                                 {"Content-Type": "application/json"})
    r = json.load(urllib.request.urlopen(req, timeout=600))
    return r["choices"][0]["message"]["content"].strip(), r["usage"], r["choices"][0]["finish_reason"]

def run(name, index, evalfile):
    rows, summ = [], {"full": 0, "n": 0, "facts_in_retrieved": 0, "control_full": 0, "controls": 0}
    for l in open(evalfile):
        r = json.loads(l)
        top = index.top(r["question"])
        ctx = "\n\n".join(f"[{index.chunks[i][0]}]\n{index.chunks[i][1].strip()}" for i in top)
        msg = (f"Answer from these excerpts of the lightBot guide and rebuild results. "
               f"Quote exact commands, numbers and names from them.\n\n{ctx}\n\nQuestion: {r['question']}")
        a, usage, finish = answer(msg)
        hits = [f for f in r["expected_facts"] if norm(f) in norm(a)]
        cov = any(all(norm(f) in norm(index.chunks[i][1]) for f in r["expected_facts"]) for i in top)
        full = len(hits) == len(r["expected_facts"])
        if r.get("source") == "control":
            summ["controls"] += 1; summ["control_full"] += full
        else:
            summ["n"] += 1; summ["full"] += full; summ["facts_in_retrieved"] += cov
        rows.append({"id": r["id"], "source": r.get("source"), "question": r["question"],
                     "expected_facts": r["expected_facts"], "retrieved": [index.chunks[i][0] for i in top],
                     "facts_in_retrieved": cov, "hits": hits, "full": full, "answer": a,
                     "usage": usage, "finish": finish})
    with open(f"{OUT}/{name}.jsonl", "w") as f:
        for row in rows: f.write(json.dumps(row) + "\n")
    print(f"RUN {name}: non-control full={summ['full']}/{summ['n']} facts_in_retrieved={summ['facts_in_retrieved']}/{summ['n']} "
          f"controls={summ['control_full']}/{summ['controls']}", flush=True)
    return summ

idx = Index(base_chunks + cards)
tag = Path(FACTS).stem
print(f"DATA reader={READER} base_chunks={len(base_chunks)} cards={len(cards)} ({FACTS}) k={K}", flush=True)
res = {f"{e}_{tag}": run(f"{e}_{tag}", idx, D / "qa" / f"{e}.jsonl") for e in RUNS}
json.dump(res, open(f"{OUT}/result_{tag}.json", "w"), indent=1)
print("SAVED", OUT)
