# lightBot in OpenClaw (guide chapter 35)

OpenClaw (an open-source agent gateway; installed by the owner on his Mac, 2026-10-15) gets the robot through one
program and only local models. Owner decisions 2026-10-15: local models do the work; Claude only through
`brain/server/claude_gate.py` (asks him first, every time); Hiwonder's OpenClaw material is background, not a procedure.

| File | What |
|---|---|
| `lightbot_robot.py` | the only program OpenClaw may run for the robot: robot API GETs (`status state task seen objects selfcare frame`), `stop`, `run '<cmd>'` through `runner/readonly_gate.py` over ssh, `ask '<q>'` = bigbuddy's `robot_ask.py`. Refuses `come home sethome look play say enable arm` and every command the gate refuses (exit 2). Token from `~/.config/rosorin/api_token`, never printed. |
| `skills/lightbot/SKILL.md` | the skill: how to call the program, the rules (no motion, quote outputs, no Claude) |
| `openclaw.lightbot.json5` | the config keys to merge into `~/.openclaw/openclaw.json`: `models.mode "replace"`, XPS + bigbuddy LM Studio providers, local primary/fallback, skill dir, `tools.exec.mode "ask"` |
| `test_lightbot_robot.sh`, `test/` | 26 tests against a stand-in robot API and the brain/sim test robot (`LIGHTBOT_TEST_BOX=<container>`) |

## Facts used, from the OpenClaw 2026.10.3 docs (npm package `openclaw@2026.10.3`, folder `docs/`)

Read in a cloud session on 2026-10-15 from the package tarball; not run there (the Mac is the test).

| Fact | Doc |
|---|---|
| Config: `~/.openclaw/openclaw.json`, JSON5; the Gateway watches it and applies changes | `gateway/configuration.md:10,74` |
| LM Studio on a LAN host: `baseUrl: "http://<host>:1234/v1"`, `api: "openai-completions"`, `apiKey: "lmstudio"`; the configured origin is trusted | `providers/lmstudio.md:201-222` |
| Guided LM Studio setup offers a model only with tool training and at least 16K effective context | `providers/lmstudio.md:51-54` |
| Any OpenAI-compatible server: `models.providers.<id>` with `baseUrl`, `apiKey`, `api: "openai-completions"`, `timeoutSeconds`, `models[]` (`id name reasoning input cost contextWindow maxTokens`) | `gateway/local-models.md:139-170` |
| `models.mode: "replace"` = only the configured providers (default `merge` keeps the built-in catalog, the docs' examples keep hosted fallbacks) | `concepts/models.md:507`, `gateway/local-models.md:88` |
| Context preflight: warns below 20 % (8k floor), blocks below 10 % (4k floor) | `gateway/local-models.md:289` |
| Probes: `openclaw infer model run --local|--gateway --model <provider/model> --prompt "Reply with exactly: pong" --json` | `gateway/local-models.md:265-276` |
| `openclaw models status`, `models list`, `models auth list`, `models auth logout <profileId>` | `cli/models.md:24-25,335,369` |
| Skills: `name` + `description` front matter (AgentSkills spec); `metadata.openclaw.requires.bins`; sources and precedence incl. `<state-dir>/skills` and `skills.load.extraDirs` | `tools/skills.md:32-50,438-523`, `tools/skills-config.md:56` |
| `openclaw skills list --eligible`, `openclaw skills check` | `cli/skills.md:59-63`, `tools/skills.md:511` |
| `tools.exec.mode`: `allowlist` (security allowlist, ask off), `ask` (allowlist, ask on-miss) | `tools/exec.md:136-143` |
| Exec approvals: security `deny|allowlist|full` (gateway default `full`), ask `off|on-miss|always`, `askFallback` default `deny`; stored in `~/.openclaw/state/openclaw.sqlite` | `tools/exec-approvals.md:110-117,222-258` |
| Allowlist by path glob: `openclaw approvals allowlist add --agent main "<path>"`; `openclaw approvals get` | `cli/approvals.md:184-188`, `tools/exec-approvals.md:66-75,433-450` |
| Do not allowlist interpreters (`python3`, `node`) | `tools/exec-approvals-advanced.md:23` |
| Dashboard `openclaw dashboard` (http://127.0.0.1:18789/); `openclaw gateway restart` | `start/getting-started.md:103-108`, `tools/exec-approvals.md:353-356` |
| `openclaw security audit` | `gateway/security/audit-checks.md:2-4` |
| Local models have no hosted safety filters; keep tool permissions tight | `gateway/local-models.md:13` |

## Not yet done (needs the Mac)

Merge the config keys, `openclaw models list` shows only `xps/...` and `lmstudio/...`, the two `infer model run`
probes answer `pong`, `openclaw skills list --eligible` shows `lightbot`, the allowlist entry matches the script,
one real `status` and one refused `come` from the dashboard. Whether the allowlist matches a script with a
`#!/usr/bin/env python3` line as written (path glob on the script) is to be seen on the Mac.
