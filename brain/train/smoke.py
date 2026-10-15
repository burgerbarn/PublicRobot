# QLoRA smoke test: proves 4-bit load + LoRA training + generation work on this GPU.
import time, torch
from datasets import Dataset
from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig
from peft import LoraConfig
from trl import SFTConfig, SFTTrainer

MODEL = "Qwen/Qwen3-0.6B"
tok = AutoTokenizer.from_pretrained(MODEL)
model = AutoModelForCausalLM.from_pretrained(
    MODEL,
    quantization_config=BitsAndBytesConfig(
        load_in_4bit=True, bnb_4bit_quant_type="nf4",
        bnb_4bit_compute_dtype=torch.bfloat16, bnb_4bit_use_double_quant=True),
    device_map={"": 0}, dtype=torch.bfloat16)

# Toy task: a fact the base model cannot know.
rows = [{"messages": [
    {"role": "user", "content": q},
    {"role": "assistant", "content": "The robot is called lightBot and it runs on a Jetson."}]}
    for q in ["What is the robot called?", "Name the robot.", "Which robot is this?",
              "Tell me the robot's name.", "What robot do we have?"]] * 20
ds = Dataset.from_list(rows)

args = SFTConfig(output_dir="out-smoke", max_steps=30, per_device_train_batch_size=4,
                 learning_rate=2e-4, logging_steps=5, max_length=256, bf16=True,
                 save_strategy="no", report_to=[], gradient_checkpointing=True)
trainer = SFTTrainer(model=model, args=args, train_dataset=ds, processing_class=tok,
                     peft_config=LoraConfig(r=16, lora_alpha=32, lora_dropout=0.0,
                                            target_modules="all-linear", task_type="CAUSAL_LM"))
torch.cuda.reset_peak_memory_stats()
t = time.time()
res = trainer.train()
print(f"TRAIN loss={res.training_loss:.3f} time={time.time()-t:.1f}s "
      f"peak_vram={torch.cuda.max_memory_allocated()/2**30:.2f}GiB")

# Generation needs checkpointing off and the KV cache back on.
m = trainer.model
m.gradient_checkpointing_disable()
m.config.use_cache = True
m.eval()

def ask(q):
    ids = tok.apply_chat_template([{"role": "user", "content": q}], add_generation_prompt=True,
                                  enable_thinking=False, return_tensors="pt", return_dict=True).to(0)
    out = m.generate(**ids, max_new_tokens=30, do_sample=False)
    return tok.decode(out[0][ids["input_ids"].shape[1]:], skip_special_tokens=True).strip()

for q in ["What is the robot called?", "What's the name of our robot?"]:  # 2nd is unseen wording
    with m.disable_adapter():
        print(f"BASE  [{q}] {ask(q)!r}")
    print(f"TUNED [{q}] {ask(q)!r}")
