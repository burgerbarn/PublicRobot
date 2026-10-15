# lightBot brain (local models)

**To run it: [OPERATING.md](OPERATING.md)** (every script, how to start it, what to do when something is down).
Health check from the Mac: `brain/health.sh`.

Goal (owner, 2026-10-14): bigbuddy should be able to build the robot from the guide, with local models.
Design: **the guide is the plan.** A runner on bigbuddy executes the guide's own commands; a local model judges
each output against the chapter's checks; a deterministic gate (not the model) decides what may run without the
owner. Destructive and hands-on steps always wait for the owner.

Status 2026-10-14: phase 0 (index + retrieval server) and the read-only part of phase 2 (checking chapters 4-21's
"Before you move on" lists on the live robot) are built and running. Phases 3-4 (running real chapter steps, a
rebuild from scratch) are not started.

## Machines

| Machine | Role | Reached as |
|---|---|---|
| bigbuddy (RTX 5070 Ti 16 GB) | brain server, runner, training, LM Studio (Gemma 4 12B for Buddy, gpt-oss-20b for runs) | `ssh bigbuddy`; brain on 127.0.0.1:8300 there |
| XPS 14 (Arc B390, 32 GB, Fedora) | coder model (llama.cpp Vulkan) | 192.168.1.114; bigbuddy tunnels 127.0.0.1:8081 -> XPS 127.0.0.1:8080. Unreachable while the laptop sleeps |
| robot | read-only checks from bigbuddy | `ssh rosorin-wifi` (bigbuddy's key is in the robot's authorized_keys since 2026-10-14) |
| test robot (container on bigbuddy) | runs the guide's software steps automatically: Ubuntu 22.04 + ROS 2 Humble, user burgerbarn, no network | `brain/sim/`, on bigbuddy `~/lightbot-sim` |

Placement (owner, 2026-10-14): Buddy's voice stays on bigbuddy; other models live on the XPS. bigbuddy's GPU is used
for quick evaluation runs (Buddy's voice off for ~20 min).

Nothing listens on the LAN: the brain and the model servers are localhost-only; the Mac uses an SSH tunnel
(`ssh -L 8300:127.0.0.1:8300 bigbuddy`). The API key is in bigbuddy `~/llm-train/brain/key` (not in this repo).

## Parts

- `server/brain_index.py`: index of the owner's code (split by function/class), the guide, the rebuild results,
  fact cards and Q/A pairs -> `~/llm-train/brain/index.json`.
- `server/brain_server.py`: OpenAI-compatible endpoint; adds BM25-retrieved excerpts to every chat. Models and
  where they run: `~/llm-train/brain/models.json` (any OpenAI-compatible server on any machine). `/search?q=` for scripts.
- `runner/guide_check.py`: phase 2 runner. Per checklist item the model plans read-only commands, `readonly_gate.py`
  refuses anything that is not read-only (NEEDS-OWNER), allowed commands run on the robot or bigbuddy, the model
  judges (PASS / DIFFERS / FAIL / UNSURE). `run_check_model.sh` frees the GPU (Buddy's voice off), runs, restores Buddy.
  `reference_items.jsonl`: per-item answer key from `guide/hw/results/` (101 items, ch04-ch21).
- `ask/robot_ask.py`: command-line question tool (guide + results + fact cards, Gemma through LM Studio).
- `train/`: QLoRA training and the evaluation scripts used to choose between fine-tuning and retrieval.
- `xps/lightbot-coder.service`: the coder model server on the XPS.

## Measured (2026-10-13/14)

- Fine-tuning (Qwen2.5-Coder-7B QLoRA on the repo): learns style, not facts. Held-out perplexity 6.25 -> 4.12, but
  rebuild-fact questions 2/47 -> 4/47, and it invents specifics (an `sgdisk` on the wrong partitions).
- Retrieval carries the facts: 32/47 (7B) -> 44/47 (Gemma 12B, fact cards); held-out set 20/22. Still about 1 in 10
  wrong, including a p1/p16 rename direction once: **no partition or boot command from a model runs unchecked.**
- Coder via retrieval on the XPS: the `tell_robot` / `/heard` question exactly right, 19 s; 24 tok/s generation.
- Phase 2 runner, gpt-oss-20b (medium): 101 checks in about 19 min on bigbuddy (Gemma: about 5 h; XPS: about 2 h).
  Scored against the answer key (59 read-only-checkable PASS items): model-planned commands 18-21 confirmed with 4-6
  false FAIL; the guide's own commands 23/52 confirmed (44%). gpt-oss high effort: same quality, 3.5x slower.
  Qwen3-30B-A3B: more confirmed (26) but 14 false FAIL. Remaining errors are reading errors of the 20B model
  (`APP_new` counted as `APP`, partial checks failed as whole items); literal expected outputs are now matched by code.
- Gate review (2026-10-14): the first gate let 62 of 72 state-changing test commands through (none was ever run);
  rebuilt with per-program option lists, 165 tests.
- Test robot: chapters 10-12 run automatically in 9 s; the workspace equals the repo's final package except for the
  lines later chapters add; no guide errors found; about half the steps need real hardware and are skipped.
- Buddy's voice model on the XPS: 15-22 s per answer as Buddy is today (clock in the system prompt), ~2 s with the
  clock moved and server tuning - still ~10x slower than bigbuddy, so voice stays on bigbuddy.

## Gate list (what the runner may never run without the owner)

Read-only allowlist in `runner/readonly_gate.py` (tests in the file). For phases 3-4 (not built yet) every command
outside it stays with the owner; these always do, whatever the phase: anything writing a block device or partition
table (`mkfs*`, `wipefs`, `dd`, `sgdisk`/`gdisk`/`parted`/`fdisk` other than print forms, `flash.sh`/`l4t_initrd_flash.sh`),
boot configuration (`extlinux.conf`, `efibootmgr` writes, UEFI variables), `reboot`/`shutdown`/`poweroff`, package
removal (`apt remove/purge/autoremove`, `dpkg -r/-P`), `rm -r` outside a run's own temp dir, the golden sets
(`~/golden`, bigbuddy `~/rosorin-golden`), the first wheel or arm motion of any chapter, and anything on `server`.
