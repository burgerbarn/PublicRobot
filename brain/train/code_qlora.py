# QLoRA on a code model with a repo's own source (raw-text completion training).
# Held-out files measure the effect: eval loss of base vs tuned on code the model never trained on.
import os
os.environ.setdefault("PYTORCH_CUDA_ALLOC_CONF", "expandable_segments:True")
import sys, time, math, zlib, json, torch
from pathlib import Path
from datasets import Dataset
from transformers import (AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig, Trainer,
                          TrainingArguments, DataCollatorForLanguageModeling)
from peft import LoraConfig, get_peft_model

MODEL = os.environ.get("MODEL", "Qwen/Qwen2.5-Coder-7B-Instruct")
DATA = Path(os.environ.get("DATA", os.path.expanduser("~/llm-train/data/rosorin-pro")))
OUT = os.environ.get("OUT", "out-coder7b-rosorin")
CTX = int(os.environ.get("CTX", "2048"))
EPOCHS = float(os.environ.get("EPOCHS", "2"))

tok = AutoTokenizer.from_pretrained(MODEL)

# Deterministic file split: ~10% of files held out by path hash.
files = sorted(p for p in DATA.rglob("*") if p.is_file())
def held_out(p): return zlib.crc32(str(p.relative_to(DATA)).encode()) % 10 == 0
def chunks(paths):
    ids = []
    for p in paths:
        text = f"# file: {p.relative_to(DATA)}\n" + p.read_text(errors="replace")
        ids += tok(text)["input_ids"] + [tok.eos_token_id]
    return [ids[i:i + CTX] for i in range(0, len(ids) - CTX // 4, CTX)]
train_c = chunks([p for p in files if not held_out(p)])
eval_c = chunks([p for p in files if held_out(p)])
print(f"DATA files={len(files)} train_chunks={len(train_c)} eval_chunks={len(eval_c)} ctx={CTX}", flush=True)

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
model.print_trainable_parameters()

@torch.no_grad()
def eval_loss():
    model.eval()
    tot, n = 0.0, 0
    for c in eval_c:
        x = torch.tensor([c], device=0)
        tot += model(input_ids=x, labels=x).loss.item() * (len(c) - 1)
        n += len(c) - 1
    model.train()
    return tot / n

with model.disable_adapter():
    base = eval_loss()
print(f"EVAL base loss={base:.4f} ppl={math.exp(base):.3f}", flush=True)

args = TrainingArguments(output_dir=OUT, num_train_epochs=EPOCHS, per_device_train_batch_size=1,
                         gradient_accumulation_steps=8, learning_rate=1e-4, lr_scheduler_type="cosine",
                         warmup_steps=3, logging_steps=5, bf16=True, save_strategy="no",
                         report_to=[], gradient_checkpointing=True,
                         gradient_checkpointing_kwargs={"use_reentrant": False}, optim="paged_adamw_8bit")
trainer = Trainer(model=model, args=args, train_dataset=Dataset.from_dict({"input_ids": train_c}),
                  data_collator=DataCollatorForLanguageModeling(tok, mlm=False))
torch.cuda.reset_peak_memory_stats()
t = time.time()
res = trainer.train()
dt = time.time() - t
ntok = len(train_c) * CTX * EPOCHS
print(f"TRAIN loss={res.training_loss:.4f} time={dt/60:.1f}min tok/s={ntok/dt:.0f} "
      f"peak_vram={torch.cuda.max_memory_allocated()/2**30:.2f}GiB", flush=True)

tuned = eval_loss()
print(f"EVAL tuned loss={tuned:.4f} ppl={math.exp(tuned):.3f} (base ppl={math.exp(base):.3f})", flush=True)
model.save_pretrained(OUT + "/adapter")
json.dump({"model": MODEL, "base_loss": base, "tuned_loss": tuned, "train_loss": res.training_loss,
           "minutes": dt / 60, "train_chunks": len(train_c), "eval_chunks": len(eval_c)},
          open(OUT + "/result.json", "w"), indent=1)
print("SAVED", OUT + "/adapter")
