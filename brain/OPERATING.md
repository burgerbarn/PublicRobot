# Operating the lightBot brain - every script, how to start it, what to do when it breaks

Written 2026-10-15 for running everything without Claude. Code: this folder (`rosorin-pro/brain`, branch `rebuild`).
bigbuddy's login shell is fish: one-line commands over ssh work as shown; for anything with `$(...)`, loops or
several lines use `ssh bigbuddy 'bash -c "..."'` or `ssh bigbuddy 'bash -s' <<'EOF' ... EOF`. Commands marked
"on bigbuddy" are bash: after `ssh bigbuddy` type `bash` first.

## 0. Is everything up?

```bash
~/CCode/rosorin-pro/brain/health.sh
```
Run on the Mac. Checks bigbuddy, the XPS and the robot (read-only). Every `DOWN` line prints the command that fixes
it. Not checked: `lightbot-mbp-tunnel`, `lightbot-pull.timer` (section 7). `buddy-voice` shows `DOWN` while the
voice is off on purpose (owner 2026-10-15: off until a later phase).

## 1. Machines and services

| Machine | Reached as | Role |
|---|---|---|
| Mac (laptop) | - | source of truth for the code (`~/CCode/rosorin-pro`); its `~/.ssh/config` names `bigbuddy`, `rosorin-wifi` |
| bigbuddy 192.168.1.111 (RTX 5070 Ti 16 GB, Fedora) | `ssh bigbuddy` (user burgerbarn) | brain server, runner, training, LM Studio. Its `~/.ssh/config` (not in git) names `xps`, `mbp`, `rosorin-wifi`, `github-rosorin` (GitHub, read-only deploy key) |
| XPS 14 192.168.1.114 (Arc B390, Fedora) | `ssh burgerbarn@192.168.1.114` | coder and gpt-oss models (llama.cpp Vulkan). Gone while the laptop sleeps |
| MacBook Pro M4 Max (lent) | bigbuddy `ssh mbp` | LM Studio: `lightbot-mac` (Qwen3.8-27B), `lightbot-oss-mac` (gpt-oss-20b); only while it is on and lent |
| robot 192.168.1.108 | `ssh rosorin-wifi` | read-only checks from bigbuddy (bigbuddy's key is in the robot's `~/.ssh/authorized_keys`) |
| server (Mac mini M4 Pro) | `ssh server` | Open WebUI (uses bigbuddy's LM Studio :1234), Home Assistant, Plex; no lightBot brain part. Nothing goes there without the owner's OK |

Services (`systemctl --user ...`, all start on boot):

| Machine | Unit | What | Port |
|---|---|---|---|
| bigbuddy | `lmstudio-server` (guide ch25) | LM Studio API: Gemma 4 12B for Buddy; gpt-oss-20b only during GPU runs | 1234 on the LAN (the server's Open WebUI uses it) |
| bigbuddy | `lightbot-brain` (`server/lightbot-brain.service`) | retrieval server: adds guide/code/results excerpts to every chat | 8300 localhost |
| bigbuddy | `lightbot-xps-tunnel` (`server/lightbot-xps-tunnel.service`) | ssh tunnel to the XPS models | 8081 (coder), 8082 (gpt-oss) localhost |
| bigbuddy | `lightbot-mbp-tunnel` (`server/lightbot-mbp-tunnel.service`) | ssh tunnel to the MacBook's LM Studio; retries every 30 s while it is away | 8085 localhost |
| bigbuddy | `lightbot-pull.timer` (unit files not in the repo) | hourly `pull_github.sh` (section 3) | |
| bigbuddy | `buddy-voice` (off since 2026-10-15), `buddy-video`, `buddy-jukebox`, `buddy-screen` | Buddy | |
| bigbuddy | `plexamp` | Plexamp headless, player "BigBuddy", audio -> Evo 150 (USB) | 32500 (LAN) |
| XPS | `lightbot-coder` (`xps/lightbot-coder.service`) | rosorin-coder 7B (llama.cpp, Vulkan) | 8080 localhost |
| XPS | `lightbot-oss` (`xps/lightbot-oss.service`) | gpt-oss-20b: the checker's model | 8082 localhost |
| XPS | `lightbot-coder-awake` (`xps/lightbot-coder-awake.service`) | blocks idle sleep while the coder runs (lid close still sleeps) | |

- Install or change a unit (on its machine): copy the file to `~/.config/systemd/user/`, then
  `systemctl --user daemon-reload && systemctl --user enable --now UNIT`. User units start at boot only with
  linger: `loginctl show-user burgerbarn -p Linger` must say `yes` (`sudo loginctl enable-linger burgerbarn`).
- Logs of any unit: `journalctl --user -u UNIT -n 50`.
- Where a model runs is set in bigbuddy `~/llm-train/brain/models.json` (served name -> server URL + model id); edit
  it and `systemctl --user restart lightbot-brain`. `server/models.json` in the repo is only the first-install
  template (`sync_brain.sh` never overwrites the live file) and lacks the names in use: `lightbot-oss-xps`
  (`http://localhost:8082`, the runner's default), `lightbot-mac` and `lightbot-oss-mac` (`http://localhost:8085`)
  (URLs from the tunnel ports; the live file was not read for this page).
  A name missing from the live file is passed to LM Studio as a model id and fails there.
- The brain's API key: bigbuddy `~/llm-train/brain/key` (not in git). Every brain script reads it at start.

## 2. Asking questions

| How | Command |
|---|---|
| Robot/guide question, one line | `ssh bigbuddy '~/llm-train/ask/robot_ask.py "how do I go back to the old system on p16?"'` |
| Same, show the excerpts it used | `ssh bigbuddy '~/llm-train/ask/robot_ask.py --show "question"'` |
| Same question to Claude with the local excerpts (asks yes first; section 9) | `ssh -t bigbuddy '~/llm-train/ask/robot_ask.py --claude "question"'` |
| OpenAI-compatible API from the Mac (any client, e.g. VS Code Continue) | `ssh -N -L 8300:127.0.0.1:8300 bigbuddy` then base URL `http://127.0.0.1:8300/v1`, key from the key file, models `lightbot` (Gemma), `lightbot-coder` (XPS), `lightbot-oss-xps`, `lightbot-mac` / `lightbot-oss-mac` (MacBook, when lent) |
| Raw search of the index | `curl -H "Authorization: Bearer $(ssh bigbuddy cat llm-train/brain/key)" "http://127.0.0.1:8300/search?q=rf2o+range_min"` (with the tunnel) |
| Buddy-style requests against any model server, timed | `ssh bigbuddy '~/llm-train/bin/python ~/llm-train/brain/buddy_llm_test.py http://localhost:1234/v1/chat/completions [MODEL]'` |

`robot_ask.py` does not use the brain server: it has its own index (`~/llm-train/ask/index.json`) and asks LM Studio
on :1234 directly, so it fails while a GPU job has stopped LM Studio. Answers are right about 9 times in 10 on
rebuild facts. **Never run a partition, boot or reboot command a model gives you without checking it against the
guide.**

## 3. Keeping the brain current (after the guide or code changed)

| Goal | Command |
|---|---|
| Deploy brain code + refresh data + rebuild the index (from the Mac) | `~/CCode/rosorin-pro/brain/sync_brain.sh` |
| Same without the buddy repo at `/Users/matty/CCode/buddy` | `BUDDY_REPO=/path/to/buddy ~/CCode/rosorin-pro/brain/sync_brain.sh` (without the buddy repo the script stops after deploying the code) |
| Rebuild the brain index only (no Mac needed) | `ssh bigbuddy '~/llm-train/bin/python ~/llm-train/brain/brain_index.py'` |
| Rebuild `robot_ask.py`'s own index (neither sync nor pull does it) | `ssh bigbuddy '~/llm-train/ask/robot_ask.py --reindex'` |
| Fix the guide itself (guide ch32) | `cd ~/CCode/rosorin-pro/guide && python3 lint.py chapters/chNN-*.md && python3 build.py` (0 errors), commit on `rebuild`, push, `./wiki_sync.sh`, Import Everything in Wiki.js; then `sync_brain.sh` |
| Pull from GitHub by hand (what the hourly timer runs) | `ssh bigbuddy 'bash PATH/pull_github.sh'` (its place on bigbuddy is not recorded; output only in `~/llm-train/runs/pull_github.log`) |

- `sync_brain.sh` copies `server/` and `runner/` to bigbuddy `~/llm-train/brain/`, `ask/` to `~/llm-train/ask/`, the
  guide/code/results to `~/llm-train/data/code-v2/`, and rebuilds the index (the server picks it up by itself).
  After a change to `server/brain_server.py`: `ssh bigbuddy systemctl --user restart lightbot-brain`.
- `pull_github.sh` refreshes data only (guide, code, cloud reports and fact cards), never brain code.
- What the index holds (2026-10-15, `sync_brain.sh` and `pull_github.sh` use the same filter): the guide chapters and
  results, the code, `docs/**/*.md` including `docs/research/`, `brain/**/*.md` and the brain code; from every
  `claude/*` branch only the `brain/research` files it adds or changes (`~/llm-train/research/cloud/<branch>/`,
  indexed) and the fact cards not already on `rebuild`. Both scripts then rebuild `robot_ask.py`'s own index too
  (OpenClaw's `ask` uses it). `sync_brain.sh` deploys `pull_github.sh` to bigbuddy `~/llm-train/brain/pull_github.sh`; once, point
  `lightbot-pull.service` at that copy (`systemctl --user edit --full lightbot-pull.service`, `ExecStart=`).
- The index: `~/llm-train/brain/index.json` (word search) + `index_emb.npy` (meaning search, model
  `BAAI/bge-base-en-v1.5` from the Hugging Face cache; without `sentence-transformers` it is word search only).
- Not copied by `sync_brain.sh`; copy them by hand from the Mac when they change (how they were copied so far is not
  recorded; the commands below match the paths this page uses):

| Repo | Goes to | Command (from `~/CCode/rosorin-pro`) |
|---|---|---|
| `brain/train/*.py` | bigbuddy `~/llm-train/work/` | `rsync -a brain/train/*.py bigbuddy:llm-train/work/` |
| `brain/train/*.sh` | bigbuddy `~/llm-train/` | `rsync -a brain/train/*.sh bigbuddy:llm-train/` |
| `brain/sim/` | bigbuddy `~/lightbot-sim/` | `rsync -a --exclude results --exclude repo brain/sim/ bigbuddy:lightbot-sim/` (no `--delete`: `fakesystemd/` exists only there); repo copy: `brain/sim/README.md` "Updating it" |
| `brain/buddy-sim/` | bigbuddy `~/buddy-sim/` | `rsync -a brain/buddy-sim/ bigbuddy:buddy-sim/` |
| `brain/server/*.service`, `brain/xps/*.service` | `~/.config/systemd/user/` on bigbuddy / XPS | section 1 |
| `brain/teacher/` | not recorded (the scripts use absolute paths; any folder works) | section 9 |

## 4. Checking the robot against the guide (read-only)

On bigbuddy, robot on and reachable:
```bash
ssh bigbuddy 'bash -c "cd ~/llm-train/brain && ~/llm-train/bin/python guide_check.py --guide-cmds ch09"'
```
- options: several chapters `... --guide-cmds ch04 ch05 ch09`; `--model NAME` (a models.json name, default
  `lightbot-oss-xps` = gpt-oss on the XPS, ~5-6 min per chapter); `--effort low|medium|high`; `--dry` (plan only,
  run nothing); `--target sim` (check the test robot container instead of the robot). The same model plans the
  commands (where the item has none of its own) and judges the output.
- faster on bigbuddy's GPU: `ssh bigbuddy '~/llm-train/brain/run_check_model.sh openai/gpt-oss-20b lightbot-oss medium --guide-cmds ch09'`
  (arguments: LM Studio model key, served name, effort or `-`, then guide_check arguments; log
  `~/llm-train/runs/<name>-<effort>-<time>.log`). It stops Buddy's voice and **starts it again at the end**
  (`buddy_resume.sh`); while the voice should stay off: `ssh bigbuddy systemctl --user stop buddy-voice` afterwards.
- results: bigbuddy `~/llm-train/runs/<time>-<model>[-effort][-gc][-sim][-dry]/chNN.md` (table) and `chNN.jsonl`.
- score a run against the answer key (`runner/reference_items.jsonl`) and the rebuild records:
  `ssh bigbuddy '~/llm-train/bin/python ~/llm-train/brain/compare_runs.py ~/llm-train/runs/<run folder>'`
- verdicts: PASS, PASS-PARTIAL (part proven), DIFFERS (works, differs from the guide text = guide finding),
  FAIL (look at it), UNSURE (the check could not decide), NEEDS-OWNER (would change state: the gate refused it),
  NEEDS-HANDS. A FAIL is a stop for you, not proof: about 1 in 8 is the model misreading. A PASS is not proof
  either: replayed on the real checks of ch16+, gpt-oss-20b agreed 56% of the time and called 39 failed or
  undecidable checks PASS (guide ch32). Read every FAIL and UNSURE yourself until a better judge is installed.
- the gate's self-test (must say 165/165): `ssh bigbuddy 'cd ~/llm-train/brain && ~/llm-train/bin/python readonly_gate.py'`

## 5. Building with bigbuddy (the chapter runner)

On bigbuddy in `~/lightbot-sim` (`cd ~/lightbot-sim` first; `README.md` there, = `brain/sim/README.md`, has every option):

| Goal | Command |
|---|---|
| Build the test robot image (once, after a Dockerfile change) | `docker build -t lightbot-sim:humble .` (needs `fakesystemd/`, which is only on bigbuddy, not in the repo) |
| Test robot (container, no hardware), chapters 10-24 from empty | `~/llm-train/bin/python run_chapter.py --fresh ch10 ch11 ch12 ch13 ch14 ch15 ch16 ch17 ch18 ch19 ch20 ch21 ch22 ch23 ch24` |
| ... with fake board / fake LiDAR / fake systemd | add `--fake-board --fake-lidar --fake-systemd` |
| ... as arm64 (the Jetson's CPU, qemu, 4x slower) | add `--arch arm64` (image: `docker build --platform linux/arm64 -t lightbot-sim:humble-arm64 .`) |
| Compare the built workspace with the repo | `./verify_run.sh` (`--arch arm64` for the arm64 run) |
| Code the guide shows for reading vs the repo | `~/llm-train/bin/python snippet_check.py ch13 ch14` |
| LiDAR steps the runner skips for systemd (after a `--fake-board --fake-lidar` run up to ch16) | `bash fakelidar/run_base_checks.sh` |
| What a chapter would do on the real robot (runs only read-only blocks) | `~/llm-train/bin/python run_chapter.py --target robot --dry-run ch11` |
| **Run a chapter on the real robot** (asks "yes" before every non-read-only block, refuses disk/boot/reboot blocks; run it in your own terminal) | `ssh -t bigbuddy 'bash -c "cd ~/lightbot-sim && ~/llm-train/bin/python run_chapter.py --target robot --go ch11"'` |
| Runner self-test | `~/llm-train/bin/python robot_target.py --selftest` (42/42) |
| Step classes of chapters 4-24 | `~/llm-train/bin/python ~/llm-train/brain/guide_plan.py` (`--steps` lists every step, `--drift` compares with the runner) |

`fakeboard/fake_board.py`, `fakelidar/fake_lidar.py`, `fakelidar/runner_hooks.py`, `fakebin/robotnet.py` and
`simbin/` run inside the container; the runner starts them, you do not.

Buddy's chapters 25-28 in a Fedora container: `python3 ~/buddy-sim/run.py --fresh ch25 ch26 ch27 ch28` (results in
`~/buddy-sim/results/`; the image `buddy-sim:f44` and the guide snapshot `~/buddy-sim/repo` are set up as described
in bigbuddy `~/buddy-sim/results/README-setup.md`, which is not in the repo).
The plan for a rebuild from scratch: `PHASE4_PLAN.md`. Your stop points (erase, name swap, reboot, hands, first
motion) stay yours: the runner enforces them in code.

## 6. Training (bigbuddy, GPU; one GPU job at a time)

| Goal | Command (on bigbuddy) |
|---|---|
| Wrap any GPU job: Buddy paused, LM Studio's server back afterwards (models unloaded, voice stays off) | `~/llm-train/run_with_pause.sh LOG.log COMMAND...` |
| Pause / resume Buddy by hand (resume starts the voice again) | `~/llm-train/buddy_pause.sh` / `~/llm-train/buddy_resume.sh` |
| Check that QLoRA works on the GPU at all | `cd ~/llm-train/work && ~/llm-train/run_with_pause.sh smoke.log ~/llm-train/bin/python smoke.py` |

Retrain the coder (after your code changed a lot):

| Step | Command (on bigbuddy) |
|---|---|
| 1. Fine-tune (QLoRA, ~25 min) | `cd ~/llm-train/work && DATA=~/llm-train/data/code-v2 OUT=out-coder EPOCHS=2 ~/llm-train/run_with_pause.sh run.log ~/llm-train/bin/python code_qlora.py` |
| 2. Did it help? | `cat out-coder/result.json`: `tuned_loss` must be below `base_loss` |
| 3. Merge the adapter into full weights | `~/llm-train/bin/python merge_adapter.py out-coder/adapter out-coder/merged` (3rd argument = base model, default Qwen/Qwen2.5-Coder-7B-Instruct) |
| 4. Make a GGUF | `~/llm-train/bin/python ~/llm-train/llama.cpp/convert_hf_to_gguf.py out-coder/merged --outtype bf16 --outfile tmp.gguf && ~/llm-train/llama.cpp/build/bin/llama-quantize tmp.gguf NAME-Q4_K_M.gguf Q4_K_M` |
| 5a. Install: XPS | `rsync NAME-Q4_K_M.gguf burgerbarn@192.168.1.114:llm/models/`, change `-m` in XPS `~/.config/systemd/user/lightbot-coder.service` (and in `brain/xps/`), `systemctl --user daemon-reload && systemctl --user restart lightbot-coder` |
| 5b. Install: LM Studio | move the .gguf to `~/.lmstudio/models/local/NAME-GGUF/` (it appears in `~/.lmstudio/bin/lms ls`), point a models.json name at it |

Retrain the judge (after you labeled more checks):

| Step | Command (on bigbuddy) |
|---|---|
| 1. Label checks without Claude | add lines `{"id": "<id from checks*.jsonl>", "gold": "PASS|DIFFERS|FAIL|UNSURE", "why": "<one sentence quoting the output>"}` to `~/llm-train/data/judge/labels-owner.jsonl` |
| 2. Train (Qwen3-8B QLoRA, 15-45 min; train ch04-15, test ch16+) | `cd ~/llm-train/work && ~/llm-train/run_with_pause.sh judge.log ~/llm-train/bin/python judge_train.py` (`--epochs`, `--model`, `--out`, `--test-from ch16`; `--all` = final model on every chapter) |
| 3. Did it help? | `out-judge-qwen3-8b/result.json`: `tuned` vs `base` vs stored gpt-oss; false PASS first, agreement second |
| 4. Merge, GGUF, install | as coder steps 3-5 with `merge_adapter.py out-judge-qwen3-8b/adapter out-judge-qwen3-8b/merged Qwen/Qwen3-8B`; add a models.json name, e.g. `lightbot-judge` |
| 5. Compare with the current checker on stored checks (no robot) | `ssh bigbuddy 'bash -c "cd ~/llm-train/brain && ~/llm-train/bin/python judge_eval.py lightbot-oss-xps lightbot-judge"'` (`--n 150 --test-from ch16 --effort medium`) |
| 6. Use it | `guide_check.py --model lightbot-judge ...` (it then also plans the commands, see section 4) |

Training data: `~/llm-train/data/judge/checks*.jsonl` (one command + output + item per line) and `labels*.jsonl`,
`synth_checks*.jsonl` + `synth_labels*.jsonl`. No script turns a new `guide_check.py` run (`runs/*/chNN.jsonl`) into
`checks*.jsonl` lines yet: add them by hand in the same format.

Measurement scripts (history, results in `README.md` "Measured"; env vars at the top of each file): `qa_qlora.py`
(coder + Q/A pairs), `rag_eval.py`, `rag_eval2.py`, `rag_eval3.py` (retrieval rounds 1-3), `ask_eval.py` (scores
`robot_ask.py`), `score_norm.py FILE KEY...` (re-score answers). `Modelfile.rosorin-coder` is for Ollama, not in use.
What training does and does not do (measured): it teaches style and narrow decisions (the judge), not facts.
Facts come from the index - keep it current (section 3).

## 7. When something is down

| Symptom | Check | Fix |
|---|---|---|
| Buddy silent | `ssh bigbuddy systemctl --user is-active buddy-voice`; `~/.lmstudio/bin/lms ps` | off on purpose since 2026-10-15; to bring it back: `~/llm-train/buddy_resume.sh` |
| LM Studio down (`DOWN LM Studio server`) | `ssh bigbuddy ~/.lmstudio/bin/lms server status` | `ssh bigbuddy systemctl --user restart lmstudio-server` (guide ch25); a GPU job may be running (`nvidia-smi`) |
| Brain down (`DOWN brain :8300`) | `ssh bigbuddy journalctl --user -u lightbot-brain -n 50` | `systemctl --user restart lightbot-brain`; it dies at start without `~/llm-train/brain/key` or `index.json` (rebuild: section 3) |
| Brain answers "model server ... not reachable" (HTTP 503) | `health.sh` | XPS asleep -> wake it; else `systemctl --user restart lightbot-xps-tunnel` on bigbuddy; MacBook names: the MacBook is away |
| XPS model down (`DOWN lightbot-oss` / `lightbot-coder`) | `ssh burgerbarn@192.168.1.114 journalctl --user -u lightbot-oss -n 50` | `ssh burgerbarn@192.168.1.114 systemctl --user restart lightbot-oss` (or `lightbot-coder`) |
| Tunnel down | `ssh bigbuddy journalctl --user -u lightbot-xps-tunnel -n 20` | `ssh bigbuddy ssh -o BatchMode=yes xps true` must work (bigbuddy's key on the XPS); `lightbot-mbp-tunnel` failing while the MacBook is away is normal |
| Hourly pull not running | `ssh bigbuddy systemctl --user list-timers lightbot-pull.timer`; `tail ~/llm-train/runs/pull_github.log` | `fetch failed`: `ssh bigbuddy ssh -T git@github-rosorin` (deploy key); run `sync_brain.sh` from the Mac meanwhile |
| Checker: every check UNSURE with ssh errors | `ssh bigbuddy ssh -o BatchMode=yes rosorin-wifi hostname` | robot off / Wi-Fi; bigbuddy's key is in the robot's `~/.ssh/authorized_keys` |
| Checker: HTTP 400 from the model | another run swapped LM Studio's model, or the `--model` name is not in models.json | run one GPU job at a time; the XPS models avoid this; add the name (section 1) |
| `ros2` daemon / logs left on the robot | `ssh rosorin-wifi 'ls ~/.ros/log \| wc -l'` | the checker deletes its RAM logs and stops a daemon it started; `ros2 daemon stop` on the robot if one is left |
| Disk filling on bigbuddy | `df -h ~` | old runs `~/llm-train/runs`, `~/llm-train/work/out-*`, Docker images `docker image ls` (test robots: lightbot-sim*, buddy-sim*) |
| Gate refuses a command you know is read-only | `readonly_gate.py` reason | add the exact form + a test to `runner/readonly_gate.py`; all 165 tests must still pass |
| Index answers are stale | `curl -s 127.0.0.1:8300/health` on bigbuddy (`built`) | `sync_brain.sh`, or rebuild on bigbuddy (section 3) |

## 8. Where things are

| What | Where |
|---|---|
| Brain code (source of truth) | Mac `~/CCode/rosorin-pro/brain` -> deployed by `sync_brain.sh` and by hand (section 3) |
| Index, server, runner, key, models.json | bigbuddy `~/llm-train/brain/` |
| `robot_ask.py` and its index | bigbuddy `~/llm-train/ask/` |
| Training scripts and outputs | bigbuddy `~/llm-train/work/` (`out-*`); wrappers `~/llm-train/*.sh` |
| Python environment for all of it | bigbuddy `~/llm-train` (Python 3.11, torch cu128); its package list is not recorded |
| llama.cpp (GGUF convert/quantize) | bigbuddy `~/llm-train/llama.cpp` |
| Check runs, pull log | bigbuddy `~/llm-train/runs/` |
| Training data | bigbuddy `~/llm-train/data/` (`code-v2/`, `judge/`, `facts/`, `qa/`) |
| GitHub clone for the hourly pull | bigbuddy `~/llm-train/repo` |
| Test robots | bigbuddy `~/lightbot-sim`, `~/buddy-sim` (copies in `brain/sim`, `brain/buddy-sim`) |
| XPS models and llama.cpp | XPS `~/llm/models`, `~/llm/llama.cpp` (build: guide ch34) |
| Measurements and decisions | `README.md`, `PHASE4_PLAN.md`, guide ch32-ch34 |

## 9. What needs Claude or the Anthropic API (ends 2026-10-31)

Claude is the local system's last resort, never its default (owner 2026-10-15): the local tools may OFFER to ask
Claude, and only `server/claude_gate.py` sends anything, after the owner types `yes` at a terminal, every time.

| Part | After the credit ends (tested 2026-10-15 against a stand-in API) | Instead |
|---|---|---|
| `server/claude_gate.py --check` (deployed to `~/llm-train/brain/`; key, package, model, log; sends nothing) | `not ready: ...` and what is missing | - |
| `ask/robot_ask.py --claude "question"`; also offered when the local model is down or answers "Not in the guide:" | one line (`no API credit left: ...`, `the API key was refused (401) ...`), exit 1; without a terminal it sends nothing and prints the command | the local answer |
| `runner/guide_check.py --claude-review ~/llm-train/runs/<run>`; also offered at the end of a run with FAIL/UNSURE checks | as above; Claude's verdict is added next to the local one, never replaces it | read the FAIL/UNSURE yourself |
| `teacher/teacher_label.py CHECKS.jsonl OUT.jsonl [--wait]` / `--collect BATCH_ID CHECKS OUT` (judge labels) | traceback ending "Your credit balance is too low ...", exit 1, files unchanged | label by hand (section 6) |
| `teacher/synth_fails.py [--per 8] [--limit N]` (synthetic failing checks) | same | write failing examples by hand |
| `teacher/guide_cards.py [--chapters ch05 ...] [--collect BATCH_ID]` (fact cards per chapter) | same | keep the existing `data/facts/guide_cards.jsonl` |
| `teacher/research.py "question"` / `--file F` (web research -> fact cards) | same | - |
| Cloud routines (nightly research, scout, audits) | **off since 2026-10-15**: routines are billed to the Claude plan's usage, not to the cloud-session credit (`research/cloud-tasks/README.md`, guide ch33). `research/questions.md` still describes the nightly routine: questions under "Open" are not researched by anything | `teacher/research.py` while API credit lasts, or a hand-started cloud session |
| Hand-started cloud sessions (`research/cloud-tasks/*.md` pasted into claude.ai/code; results come back as `claude/*` branches) | run on the cloud-session credit until it ends; no cloud assistant after 2026-10-31 | - |
| `research/cloud/cloud_sim.sh` (branch `claude/brain-sim` only): the test robot in a cloud sandbox | only useful inside a cloud session | `~/lightbot-sim` on bigbuddy (section 5) |
| `pull_github.sh` cloud part (`claude/brain-research`, `claude/brain-sim`) | keeps running; nothing new arrives once the cloud sessions stop | - |

- `claude_gate.py` and the four teacher scripts take the key from `ANTHROPIC_API_KEY` or bigbuddy
  `~/llm-train/brain/anthropic_key` and need the `anthropic` package; run them with `~/llm-train/bin/python`.
  Without a key they stop at once with "no API key ...". Every `claude_gate.py` request is logged with its tokens
  and cost to `~/llm-train/brain/claude_calls.jsonl`; `LIGHTBOT_CLAUDE_MODEL` changes the model (default
  `claude-opus-5-5`).
- `synth_fails.py`, `guide_cards.py` and `teacher_label.py` (when it collects) write to `*.tmp` first: a batch with
  0 usable results keeps the old file and exits 1 ("kept ... as it was"); a partly failed batch writes what
  succeeded and exits 1. (Until 2026-10-15 they emptied the file and exited 0.)
- Nothing else in this file needs Claude: the brain, runner, checker, test robots and training run on your machines.

## 10. OpenClaw on the Mac (guide ch35; not yet run on the Mac)

| What | Command (on the Mac) |
|---|---|
| The robot program, by hand | `~/CCode/rosorin-pro/brain/openclaw/lightbot_robot.py status` (also `state task seen objects selfcare frame stop`, `run '<read-only cmd>'`, `ask '<question>'`); refuses motion, exit 2 |
| Its tests (stand-in API; add `LIGHTBOT_TEST_BOX=<container>` for the test robot) | `~/CCode/rosorin-pro/brain/openclaw/test_lightbot_robot.sh` |
| Tunnel to the XPS model for OpenClaw | `ssh -N -L 8082:127.0.0.1:8082 burgerbarn@192.168.1.114` |
| Config keys (merge into `~/.openclaw/openclaw.json`) | `brain/openclaw/openclaw.lightbot.json5` (`models.mode "replace"`, local models only, skill dir, `tools.exec.mode "ask"`) |
| Only the program runs without asking | `openclaw approvals allowlist add --agent main "$HOME/CCode/rosorin-pro/brain/openclaw/lightbot_robot.py"`; check `openclaw approvals get` |
| Models, skill, dashboard | `openclaw models list`; `openclaw skills list --eligible`; `openclaw dashboard` (127.0.0.1:18789) |

The robot token on the Mac: `~/.config/rosorin/api_token` (0600, copied as in ch35 step 3). No hosted model in
OpenClaw: Claude only through `claude_gate.py` (section 9). Doc lines for every key: `brain/openclaw/README.md`.
