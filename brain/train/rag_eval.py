# Retrieval test: answer eval.jsonl with the top BM25 chunks of the guide + rebuild results in the prompt.
# Scores base and (optionally) a tuned adapter the same way as qa_qlora.py.
import os
os.environ.setdefault("PYTORCH_CUDA_ALLOC_CONF", "expandable_segments:True")
import re, math, json, sys, torch
from collections import Counter
from pathlib import Path
from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig

MODEL = os.environ.get("MODEL", "Qwen/Qwen2.5-Coder-7B-Instruct")
DOCS = Path(os.environ.get("DOCS", os.path.expanduser("~/llm-train/data/rosorin-pro/guide")))
QA = Path(os.environ.get("QA", os.path.expanduser("~/llm-train/data/qa")))
ADAPTER = os.environ.get("ADAPTER", "out-coder7b-qa1/adapter")
OUT = os.environ.get("OUT", "out-rag1")
K = int(os.environ.get("K", "6"))
os.makedirs(OUT, exist_ok=True)

tok = AutoTokenizer.from_pretrained(MODEL)

# Chunks: markdown files split on headings, then packed to ~600 tokens.
chunks = []
for p in sorted(DOCS.rglob("*.md")):
    rel = str(p.relative_to(DOCS.parent))
    cur = ""
    for part in re.split(r"\n(?=#{1,4} )", p.read_text(errors="replace")):
        if cur and len(tok(cur + part)["input_ids"]) > 600:
            chunks.append((rel, cur)); cur = ""
        cur += "\n" + part
    if cur.strip(): chunks.append((rel, cur))
# The Q/A training pairs are the written-down facts: index each pair as its own chunk.
for l in open(QA / "train.jsonl"):
    r = json.loads(l)
    chunks.append((r.get("source", "qa"), "Q: " + r["messages"][0]["content"] + "\nA: " + r["messages"][1]["content"]))

def terms(s): return re.findall(r"[a-z0-9_./-]+", s.lower())
docs = [Counter(terms(c)) for _, c in chunks]
avg = sum(sum(d.values()) for d in docs) / len(docs)
df = Counter(t for d in docs for t in d)
def bm25(q, d, k1=1.5, b=0.75):
    n = sum(d.values())
    return sum(math.log(1 + (len(docs) - df[t] + .5) / (df[t] + .5)) * d[t] * (k1 + 1) /
               (d[t] + k1 * (1 - b + b * n / avg)) for t in set(terms(q)) if t in d)

qa_eval = [json.loads(l) for l in open(QA / "eval.jsonl")]
def prompt(q):
    top = sorted(range(len(chunks)), key=lambda i: -bm25(q, docs[i]))[:K]
    ctx = "\n\n".join(f"[{chunks[i][0]}]\n{chunks[i][1].strip()}" for i in top)
    msg = (f"Answer from these excerpts of the lightBot guide and rebuild results. "
           f"Quote exact commands, numbers and names from them.\n\n{ctx}\n\nQuestion: {q}")
    enc = tok.apply_chat_template([{"role": "user", "content": msg}], add_generation_prompt=True)
    return list(enc["input_ids"] if hasattr(enc, "keys") else enc), top

model = AutoModelForCausalLM.from_pretrained(
    MODEL,
    quantization_config=BitsAndBytesConfig(
        load_in_4bit=True, bnb_4bit_quant_type="nf4",
        bnb_4bit_compute_dtype=torch.bfloat16, bnb_4bit_use_double_quant=True),
    device_map={"": 0}, dtype=torch.bfloat16)
model.eval()

prompts = [prompt(r["question"]) for r in qa_eval]
def covered(r, top):  # some retrieved chunk holds every expected fact
    return any(all(f.lower() in chunks[i][1].lower() for f in r["expected_facts"]) for i in top)
src_hit = sum(covered(r, t) for r, (_, t) in zip(qa_eval, prompts) if r.get("source") != "control")
print(f"DATA chunks={len(chunks)} k={K} facts_in_retrieved={src_hit}/"
      f"{sum(r.get('source') != 'control' for r in qa_eval)}", flush=True)

@torch.no_grad()
def answers(m, bs=1):
    tok.padding_side = "left"
    outs = []
    for i in range(0, len(prompts), bs):
        enc = tok.pad({"input_ids": [p for p, _ in prompts[i:i + bs]]}, return_tensors="pt").to(0)
        gen = m.generate(**enc, max_new_tokens=200, do_sample=False, pad_token_id=tok.pad_token_id)
        outs += [tok.decode(g[enc["input_ids"].shape[1]:], skip_special_tokens=True).strip() for g in gen]
    return outs

def hits(a, facts): return [f for f in facts if f.lower() in a.lower()]
def score(ans):
    return (sum(len(hits(a, r["expected_facts"])) == len(r["expected_facts"]) for a, r in zip(ans, qa_eval)),
            sum(len(hits(a, r["expected_facts"])) for a, r in zip(ans, qa_eval)))
nf = sum(len(r["expected_facts"]) for r in qa_eval)

base = answers(model)
print(f"RAG base qa_full={score(base)[0]}/{len(qa_eval)} facts={score(base)[1]}/{nf}", flush=True)
tuned = None
if ADAPTER and os.path.isdir(ADAPTER):
    from peft import PeftModel
    model = PeftModel.from_pretrained(model, ADAPTER); model.eval()
    tuned = answers(model)
    print(f"RAG tuned qa_full={score(tuned)[0]}/{len(qa_eval)} facts={score(tuned)[1]}/{nf}", flush=True)

with open(OUT + "/eval_answers.jsonl", "w") as f:
    for j, r in enumerate(qa_eval):
        row = {"id": r["id"], "source": r.get("source"), "retrieved": [chunks[i][0] for i in prompts[j][1]],
               "facts_in_retrieved": covered(r, prompts[j][1]),
               "expected_facts": r["expected_facts"], "rag_base_hits": hits(base[j], r["expected_facts"]),
               "rag_base_answer": base[j]}
        if tuned: row.update(rag_tuned_hits=hits(tuned[j], r["expected_facts"]), rag_tuned_answer=tuned[j])
        f.write(json.dumps(row) + "\n")
json.dump({"chunks": len(chunks), "k": K, "facts_in_retrieved": src_hit,
           "rag_base": score(base), "rag_tuned": score(tuned) if tuned else None, "facts_total": nf,
           "qa_eval": len(qa_eval)}, open(OUT + "/result.json", "w"), indent=1)
print("SAVED", OUT)
