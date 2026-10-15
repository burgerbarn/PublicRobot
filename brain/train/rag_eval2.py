# Retrieval eval, round 2: index with and without fact cards, on eval.jsonl and eval2.jsonl.
# Base model only (round 1: the fine-tuned adapter added nothing on top of retrieval). Format-tolerant scoring.
import os
os.environ.setdefault("PYTORCH_CUDA_ALLOC_CONF", "expandable_segments:True")
import re, math, json, torch
from collections import Counter
from pathlib import Path
from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig

MODEL = os.environ.get("MODEL", "Qwen/Qwen2.5-Coder-7B-Instruct")
D = Path(os.path.expanduser("~/llm-train/data"))
OUT = os.environ.get("OUT", "out-rag2")
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
for l in open(D / "facts/facts.jsonl"):
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

model = AutoModelForCausalLM.from_pretrained(
    MODEL, quantization_config=BitsAndBytesConfig(
        load_in_4bit=True, bnb_4bit_quant_type="nf4",
        bnb_4bit_compute_dtype=torch.bfloat16, bnb_4bit_use_double_quant=True),
    device_map={"": 0}, dtype=torch.bfloat16)
model.eval()

@torch.no_grad()
def answer(ids):
    x = torch.tensor([ids], device=0)
    g = model.generate(input_ids=x, attention_mask=torch.ones_like(x), max_new_tokens=200,
                       do_sample=False, pad_token_id=tok.pad_token_id)
    return tok.decode(g[0][x.shape[1]:], skip_special_tokens=True).strip()

def run(name, index, evalfile):
    rows, summ = [], {"full": 0, "n": 0, "facts_in_retrieved": 0, "control_full": 0, "controls": 0}
    for l in open(evalfile):
        r = json.loads(l)
        top = index.top(r["question"])
        ctx = "\n\n".join(f"[{index.chunks[i][0]}]\n{index.chunks[i][1].strip()}" for i in top)
        msg = (f"Answer from these excerpts of the lightBot guide and rebuild results. "
               f"Quote exact commands, numbers and names from them.\n\n{ctx}\n\nQuestion: {r['question']}")
        enc = tok.apply_chat_template([{"role": "user", "content": msg}], add_generation_prompt=True)
        a = answer(list(enc["input_ids"] if hasattr(enc, "keys") else enc))
        hits = [f for f in r["expected_facts"] if norm(f) in norm(a)]
        cov = any(all(norm(f) in norm(index.chunks[i][1]) for f in r["expected_facts"]) for i in top)
        full = len(hits) == len(r["expected_facts"])
        if r.get("source") == "control":
            summ["controls"] += 1; summ["control_full"] += full
        else:
            summ["n"] += 1; summ["full"] += full; summ["facts_in_retrieved"] += cov
        rows.append({"id": r["id"], "source": r.get("source"), "question": r["question"],
                     "expected_facts": r["expected_facts"], "retrieved": [index.chunks[i][0] for i in top],
                     "facts_in_retrieved": cov, "hits": hits, "full": full, "answer": a})
    with open(f"{OUT}/{name}.jsonl", "w") as f:
        for row in rows: f.write(json.dumps(row) + "\n")
    print(f"RUN {name}: non-control full={summ['full']}/{summ['n']} facts_in_retrieved={summ['facts_in_retrieved']}/{summ['n']} "
          f"controls={summ['control_full']}/{summ['controls']}", flush=True)
    return summ

old, new = Index(base_chunks), Index(base_chunks + cards)
print(f"DATA base_chunks={len(base_chunks)} cards={len(cards)} k={K}", flush=True)
res = {n: run(n, idx, D / "qa" / f)
       for n, idx, f in [("eval2_old", old, "eval2.jsonl"), ("eval2_cards", new, "eval2.jsonl"),
                         ("eval_cards", new, "eval.jsonl")]}
json.dump(res, open(f"{OUT}/result.json", "w"), indent=1)
print("SAVED", OUT)
