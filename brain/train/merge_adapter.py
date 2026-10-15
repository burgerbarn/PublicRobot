# Fold a LoRA adapter into its base model (bf16, on CPU) and save full weights for GGUF/Ollama import.
# Usage: merge_adapter.py ADAPTER_DIR OUT_DIR [BASE]
import sys, torch
from transformers import AutoModelForCausalLM, AutoTokenizer
from peft import PeftModel

adapter, out = sys.argv[1], sys.argv[2]
base = sys.argv[3] if len(sys.argv) > 3 else "Qwen/Qwen2.5-Coder-7B-Instruct"
model = AutoModelForCausalLM.from_pretrained(base, dtype=torch.bfloat16, device_map={"": "cpu"})
model = PeftModel.from_pretrained(model, adapter).merge_and_unload()
model.save_pretrained(out, safe_serialization=True, max_shard_size="4GB")
AutoTokenizer.from_pretrained(base).save_pretrained(out)
print("MERGED", out)
