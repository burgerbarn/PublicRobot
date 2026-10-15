#!/usr/bin/env python3
"""Train a local judge for the guide checker: QLoRA on Qwen3-8B with labeled checks from real robot runs.

Data: ~/llm-train/data/judge/checks.jsonl (every unique check: item, command, output) + labels*.jsonl (gold verdict
and why, one per check id). Split by chapter: train ch04-ch15, test ch16-ch21 (does it generalize to chapters it
never saw?). The prompt is guide_check.JUDGE_PROMPT, the target the JSON the checker parses: {"verdict", "why"}.
Reports base vs tuned vs the stored gpt-oss-20b verdicts on the test chapters: accuracy, false FAIL, false PASS.
  judge_train.py [--epochs 3] [--model Qwen/Qwen3-8B] [--out out-judge-qwen3-8b]
"""
import argparse, glob, json, os, sys, time
os.environ.setdefault("PYTORCH_CUDA_ALLOC_CONF", "expandable_segments:True")
import torch
from datasets import Dataset
from transformers import (AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig, Trainer, TrainingArguments,
                          DataCollatorForSeq2Seq)
from peft import LoraConfig, get_peft_model

HOME = os.path.expanduser("~")
sys.path.insert(0, f"{HOME}/llm-train/brain")
from guide_check import JUDGE_PROMPT          # the checker's own judge prompt

ap = argparse.ArgumentParser()
ap.add_argument("--model", default="Qwen/Qwen3-8B")
ap.add_argument("--out", default="out-judge-qwen3-8b")
ap.add_argument("--epochs", type=float, default=3)
ap.add_argument("--test-from", default="ch16")
ap.add_argument("--all", action="store_true", help="final model: train on every chapter (the test score is then not a held-out score)")
a = ap.parse_args()
os.makedirs(a.out, exist_ok=True)

D = f"{HOME}/llm-train/data/judge"
checks, synth = {}, set()
for f in sorted(glob.glob(f"{D}/checks*.jsonl")) + sorted(glob.glob(f"{D}/synth_checks*.jsonl")):
    rs = {r["id"]: r for r in map(json.loads, open(f))}            # real runs, test robot, synthetic failures
    checks.update(rs)
    if "synth" in os.path.basename(f):
        synth |= set(rs)
gold = {}
for f in sorted(glob.glob(f"{D}/labels*.jsonl")) + sorted(glob.glob(f"{D}/synth_labels*.jsonl")):   # synth_ too (fixed 2026-10-15: missed before, so the judge never learned FAIL)
    for r in map(json.loads, open(f)):
        gold[r["id"]] = r
rows = [dict(checks[i], gold=g["gold"], gold_why=g["why"], run="synthetic" if i in synth else checks[i].get("run", ""))
        for i, g in gold.items() if i in checks]
# test = real checks only from --test-from on; synthetic cases of those chapters stay out of training (held-out chapters)
test = [r for r in rows if r["ch"] >= a.test_from and r["run"] != "synthetic"]
train = rows if a.all else [r for r in rows if r["ch"] < a.test_from]
print(f"DATA labeled {len(rows)} (synthetic {sum(r['run'] == 'synthetic' for r in rows)}) train {len(train)} "
      f"(FAIL {sum(r['gold'] == 'FAIL' for r in train)}) test {len(test)}", flush=True)

tok = AutoTokenizer.from_pretrained(a.model)


def prompt_ids(r):
    msg = JUDGE_PROMPT.format(ch=r["ch"], item=r["item_text"], host=r.get("host") or "robot", cmd=r["cmd"],
                              expect="(the checklist item)", rc=r.get("rc"), out=r["out"]) + \
          '\nAnswer with JSON only: {"verdict": "PASS|DIFFERS|FAIL|UNSURE", "why": "..."}'
    enc = tok.apply_chat_template([{"role": "user", "content": msg}], add_generation_prompt=True, enable_thinking=False)
    return list(enc["input_ids"] if hasattr(enc, "keys") else enc)


ex = []
for r in train:
    p = prompt_ids(r)
    t = tok(json.dumps({"verdict": r["gold"], "why": r["gold_why"]}) + tok.eos_token, add_special_tokens=False)["input_ids"]
    ex.append({"input_ids": p + t, "labels": [-100] * len(p) + t})

model = AutoModelForCausalLM.from_pretrained(
    a.model, quantization_config=BitsAndBytesConfig(load_in_4bit=True, bnb_4bit_quant_type="nf4",
                                                    bnb_4bit_compute_dtype=torch.bfloat16, bnb_4bit_use_double_quant=True),
    device_map={"": 0}, dtype=torch.bfloat16)
model.gradient_checkpointing_enable(gradient_checkpointing_kwargs={"use_reentrant": False})
model.enable_input_require_grads()
model = get_peft_model(model, LoraConfig(r=16, lora_alpha=32, lora_dropout=0.05, target_modules="all-linear",
                                         task_type="CAUSAL_LM"))


@torch.no_grad()
def judge(rs):
    model.eval(); model.config.use_cache = True
    out = []
    for r in rs:
        ids = torch.tensor([prompt_ids(r)], device=0)
        g = model.generate(input_ids=ids, attention_mask=torch.ones_like(ids), max_new_tokens=120, do_sample=False,
                           pad_token_id=tok.pad_token_id or tok.eos_token_id)
        txt = tok.decode(g[0][ids.shape[1]:], skip_special_tokens=True)
        v = next((x for x in ("DIFFERS", "UNSURE", "FAIL", "PASS") if f'"{x}"' in txt), "UNSURE")
        out.append(v)
    model.config.use_cache = False; model.train()
    return out


def score(pred, rs):
    n = len(rs)
    ok = sum(p == r["gold"] or (p in ("PASS", "DIFFERS") and r["gold"] in ("PASS", "DIFFERS")) for p, r in zip(pred, rs))
    ff = sum(p == "FAIL" and r["gold"] != "FAIL" for p, r in zip(pred, rs))
    fp = sum(p in ("PASS", "DIFFERS") and r["gold"] in ("FAIL", "UNSURE") for p, r in zip(pred, rs))
    return {"n": n, "agree": ok, "agree_pct": round(100 * ok / max(n, 1), 1), "false_fail": ff, "false_pass": fp}


real_test = [r for r in test if r.get("run") != "synthetic"]
oss = [r for r in real_test if "oss" in r.get("run", "")]
res = {"stored_gpt_oss_on_its_checks": score([r["model_verdict"] for r in oss], oss)}
model.gradient_checkpointing_disable()
with model.disable_adapter():
    res["base"] = score(judge(test), test)
print("EVAL base", res["base"], "| stored gpt-oss", res["stored_gpt_oss_on_its_checks"], flush=True)
model.gradient_checkpointing_enable(gradient_checkpointing_kwargs={"use_reentrant": False})

args = TrainingArguments(output_dir=a.out, num_train_epochs=a.epochs, per_device_train_batch_size=1,
                         gradient_accumulation_steps=8, learning_rate=1e-4, lr_scheduler_type="cosine", warmup_steps=5,
                         logging_steps=10, bf16=True, save_strategy="no", report_to=[], gradient_checkpointing=True,
                         gradient_checkpointing_kwargs={"use_reentrant": False}, optim="paged_adamw_8bit")
t0 = time.time()
r = Trainer(model=model, args=args, train_dataset=Dataset.from_list(ex),
            data_collator=DataCollatorForSeq2Seq(tok, padding=True)).train()
print(f"TRAIN loss={r.training_loss:.4f} time={(time.time() - t0) / 60:.1f}min "
      f"peak_vram={torch.cuda.max_memory_allocated() / 2**30:.2f}GiB", flush=True)
model.save_pretrained(a.out + "/adapter")

model.gradient_checkpointing_disable()
pred = judge(test)
res["tuned"] = score(pred, test)
print("EVAL tuned", res["tuned"], flush=True)
with open(a.out + "/test_predictions.jsonl", "w") as f:
    for p, rr in zip(pred, test):
        f.write(json.dumps({"id": rr["id"], "ch": rr["ch"], "gold": rr["gold"], "tuned": p,
                            "stored_model": rr["model_verdict"], "run": rr["run"]}) + "\n")
json.dump(dict(res, model=a.model, train=len(train), test=len(test), epochs=a.epochs), open(a.out + "/result.json", "w"), indent=1)
print("SAVED", a.out)
