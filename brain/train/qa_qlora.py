# QLoRA on a code model: repo raw text (completion) + Q/A pairs (loss on answers only),
# then base-vs-tuned eval: held-out raw-text loss and fact recall on eval.jsonl questions.
import os
os.environ.setdefault("PYTORCH_CUDA_ALLOC_CONF", "expandable_segments:True")
import time, math, zlib, json, random, torch
from pathlib import Path
from datasets import Dataset
from transformers import (AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig, Trainer,
                          TrainingArguments, DataCollatorForSeq2Seq)
from peft import LoraConfig, get_peft_model

MODEL = os.environ.get("MODEL", "Qwen/Qwen2.5-Coder-7B-Instruct")
DATA = Path(os.environ.get("DATA", os.path.expanduser("~/llm-train/data/rosorin-pro")))
QA = Path(os.environ.get("QA", os.path.expanduser("~/llm-train/data/qa")))
OUT = os.environ.get("OUT", "out-coder7b-qa1")
CTX = int(os.environ.get("CTX", "2048"))
RAW_EPOCHS = int(os.environ.get("RAW_EPOCHS", "1"))
QA_EPOCHS = int(os.environ.get("QA_EPOCHS", "4"))
os.makedirs(OUT, exist_ok=True)
random.seed(0)

tok = AutoTokenizer.from_pretrained(MODEL)

# Raw text, same deterministic file split as code_qlora.py (~10% of files held out by path hash).
files = sorted(p for p in DATA.rglob("*") if p.is_file())
def held_out(p): return zlib.crc32(str(p.relative_to(DATA)).encode()) % 10 == 0
def chunks(paths):
    ids = []
    for p in paths:
        ids += tok(f"# file: {p.relative_to(DATA)}\n" + p.read_text(errors="replace"))["input_ids"]
        ids.append(tok.eos_token_id)
    return [ids[i:i + CTX] for i in range(0, len(ids) - CTX // 4, CTX)]
train_c = chunks([p for p in files if not held_out(p)])
eval_c = chunks([p for p in files if held_out(p)])

def prompt_ids(q):
    enc = tok.apply_chat_template([{"role": "user", "content": q}], add_generation_prompt=True)
    return list(enc["input_ids"] if hasattr(enc, "keys") else enc)  # transformers 5 returns a BatchEncoding

qa_train = [json.loads(l) for l in open(QA / "train.jsonl")]
qa_eval = [json.loads(l) for l in open(QA / "eval.jsonl")]
qa_ex = []
for r in qa_train:
    p = prompt_ids(r["messages"][0]["content"])
    a = tok(r["messages"][1]["content"] + tok.eos_token, add_special_tokens=False)["input_ids"]
    qa_ex.append({"input_ids": p + a, "labels": [-100] * len(p) + a})

examples = [{"input_ids": c, "labels": c} for c in train_c] * RAW_EPOCHS + qa_ex * QA_EPOCHS
random.shuffle(examples)
print(f"DATA files={len(files)} raw_train={len(train_c)} raw_eval={len(eval_c)} qa_train={len(qa_ex)} "
      f"qa_eval={len(qa_eval)} examples={len(examples)}", flush=True)

model = AutoModelForCausalLM.from_pretrained(
    MODEL,
    quantization_config=BitsAndBytesConfig(
        load_in_4bit=True, bnb_4bit_quant_type="nf4",
        bnb_4bit_compute_dtype=torch.bfloat16, bnb_4bit_use_double_quant=True),
    device_map={"": 0}, dtype=torch.bfloat16)
# Not prepare_model_for_kbit_training: it upcasts embed + lm_head to fp32 (~4.4 GB with a 152k vocab) -> OOM at 16 GB.
model.gradient_checkpointing_enable(gradient_checkpointing_kwargs={"use_reentrant": False})
model.enable_input_require_grads()
model = get_peft_model(model, LoraConfig(r=16, lora_alpha=32, lora_dropout=0.05,
                                         target_modules="all-linear", task_type="CAUSAL_LM"))

@torch.no_grad()
def raw_loss():
    model.eval()
    tot, n = 0.0, 0
    for c in eval_c:
        x = torch.tensor([c], device=0)
        tot += model(input_ids=x, labels=x).loss.item() * (len(c) - 1)
        n += len(c) - 1
    model.train()
    return tot / n

@torch.no_grad()
def answers(bs=8):
    model.eval()
    model.config.use_cache = True
    tok.padding_side = "left"
    outs = []
    for i in range(0, len(qa_eval), bs):
        batch = [prompt_ids(r["question"]) for r in qa_eval[i:i + bs]]
        enc = tok.pad({"input_ids": batch}, return_tensors="pt").to(0)
        gen = model.generate(**enc, max_new_tokens=200, do_sample=False, pad_token_id=tok.pad_token_id)
        outs += [tok.decode(g[enc["input_ids"].shape[1]:], skip_special_tokens=True).strip() for g in gen]
    tok.padding_side = "right"
    model.config.use_cache = False
    model.train()
    return outs

def hits(ans, facts): return [f for f in facts if f.lower() in ans.lower()]

def score(ans):
    full = sum(len(hits(a, r["expected_facts"])) == len(r["expected_facts"]) for a, r in zip(ans, qa_eval))
    facts = sum(len(hits(a, r["expected_facts"])) for a, r in zip(ans, qa_eval))
    return full, facts

model.gradient_checkpointing_disable()
with model.disable_adapter():
    base_raw = raw_loss()
    base_ans = answers()
print(f"EVAL base raw_loss={base_raw:.4f} qa_full={score(base_ans)[0]}/{len(qa_eval)} "
      f"facts={score(base_ans)[1]}/{sum(len(r['expected_facts']) for r in qa_eval)}", flush=True)
model.gradient_checkpointing_enable(gradient_checkpointing_kwargs={"use_reentrant": False})

args = TrainingArguments(output_dir=OUT, num_train_epochs=1, per_device_train_batch_size=1,
                         gradient_accumulation_steps=8, learning_rate=1e-4, lr_scheduler_type="cosine",
                         warmup_steps=3, logging_steps=10, bf16=True, save_strategy="no",
                         report_to=[], gradient_checkpointing=True,
                         gradient_checkpointing_kwargs={"use_reentrant": False}, optim="paged_adamw_8bit")
trainer = Trainer(model=model, args=args, train_dataset=Dataset.from_list(examples),
                  data_collator=DataCollatorForSeq2Seq(tok, padding=True))
torch.cuda.reset_peak_memory_stats()
t = time.time()
res = trainer.train()
dt = time.time() - t
print(f"TRAIN loss={res.training_loss:.4f} time={dt/60:.1f}min "
      f"peak_vram={torch.cuda.max_memory_allocated()/2**30:.2f}GiB", flush=True)
model.save_pretrained(OUT + "/adapter")

model.gradient_checkpointing_disable()
tuned_raw = raw_loss()
tuned_ans = answers()
nf = sum(len(r["expected_facts"]) for r in qa_eval)
print(f"EVAL tuned raw_loss={tuned_raw:.4f} qa_full={score(tuned_ans)[0]}/{len(qa_eval)} "
      f"facts={score(tuned_ans)[1]}/{nf}", flush=True)

with open(OUT + "/eval_answers.jsonl", "w") as f:
    for r, b, a in zip(qa_eval, base_ans, tuned_ans):
        f.write(json.dumps({"id": r["id"], "source": r.get("source"), "question": r["question"],
                            "expected_facts": r["expected_facts"],
                            "base_hits": hits(b, r["expected_facts"]), "tuned_hits": hits(a, r["expected_facts"]),
                            "base_answer": b, "tuned_answer": a}) + "\n")
json.dump({"model": MODEL, "base_raw_loss": base_raw, "tuned_raw_loss": tuned_raw,
           "base_qa_full": score(base_ans)[0], "tuned_qa_full": score(tuned_ans)[0],
           "base_facts": score(base_ans)[1], "tuned_facts": score(tuned_ans)[1], "facts_total": nf,
           "qa_eval": len(qa_eval), "train_loss": res.training_loss, "minutes": dt / 60,
           "raw_train": len(train_c), "qa_train": len(qa_ex), "qa_epochs": QA_EPOCHS},
          open(OUT + "/result.json", "w"), indent=1)
print("SAVED", OUT)
