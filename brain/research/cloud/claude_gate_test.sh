#!/bin/bash
# Tests of "Claude only with the owner's yes" (brain/server/claude_gate.py, robot_ask.py, guide_check.py) and of the
# teacher scripts without key or credit, against fake_anthropic.py: nothing leaves the machine except test 8 (the real
# API with a made-up key, expected 401). Needs python3 with the `anthropic` package, `script` (util-linux).
#   brain/research/cloud/claude_gate_test.sh          prints PASS/FAIL per case, exit 1 on any FAIL
set -u
HERE=$(cd "$(dirname "$0")" && pwd); B=$(cd "$HERE/../.." && pwd)
W=$(mktemp -d); trap 'kill $(jobs -p) 2>/dev/null; rm -rf "$W"' EXIT
cd "$W" && export HOME="$W/home" && mkdir -p home/llm-train/{brain,data/judge,data/facts,runs} home/llm-train/data/code-v2/rosorin-pro/guide
ln -s "$B/.." home/llm-train/data/rosorin-pro; ln -s "$B/../guide/chapters" home/llm-train/data/code-v2/rosorin-pro/guide/chapters
echo k > home/llm-train/brain/key
P=0; F=0
ok()  { if eval "$2"; then echo "PASS  $1"; P=$((P+1)); else echo "FAIL  $1"; F=$((F+1)); fi; }
hits() { cat hits.log 2>/dev/null | grep -c '^/v1/messages'; }
tty() { (sleep 3; echo "$1") | script -q -c "$2" /dev/null; }   # run $2 in a pseudo-terminal, answer $1
port=9930; for m in ok credit batch-errored batch-ok-label batch-ok-cards batch-ok-synth; do
  python3 "$HERE/fake_anthropic.py" $port $m & eval "PORT_${m//-/_}=$port"; port=$((port+1)); done; sleep 1
A="python3 $B/ask/robot_ask.py"; G="python3 $B/server/claude_gate.py"; GC="python3 $B/runner/guide_check.py"; T="$B/teacher"
export ROBOT_ASK_URL=http://127.0.0.1:$PORT_ok/v1/chat/completions

unset ANTHROPIC_API_KEY ANTHROPIC_BASE_URL
o=$($G --check); ok "1 no key: --check says not ready" '[[ $o == "not ready: no API key"* ]]'
export ANTHROPIC_API_KEY=sk-test ANTHROPIC_BASE_URL=http://127.0.0.1:$PORT_ok
o=$($A "wheel colour" </dev/null 2>&1); ok "2 no terminal: nothing sent" '[[ $(hits) == 0 && $o == *"no terminal"* ]]'
o=$(tty no "$A 'wheel colour'" 2>&1); ok "3 terminal, answer no: nothing sent" '[[ $(hits) == 0 && $o == *"Not sent"* ]]'
o=$(tty yes "$A 'wheel colour'" 2>&1); ok "4 terminal, answer yes: one request, logged" '[[ $(hits) == 1 && $o == *"stand-in answer"* && -s home/llm-train/brain/claude_calls.jsonl ]]'
o=$(ROBOT_ASK_URL=http://127.0.0.1:9 tty yes "$A 'which partition is APP_new'" 2>&1); ok "5 local model down: offered, yes sends" '[[ $(hits) == 2 && $o == *"not reachable"* ]]'
o=$(ANTHROPIC_BASE_URL=http://127.0.0.1:$PORT_credit tty yes "$A --claude 'q'" 2>&1); ok "6 no credit: one clear line" '[[ $o == *"Claude failed: no API credit left"* ]]'
R=home/llm-train/runs/20261015T1700-lightbot-oss-xps; mkdir -p $R
echo '{"item": 1, "text": "rosorin-base is active.", "verdict": "FAIL", "file": "ch11-the-base-driver.md", "checks": [{"host": "robot", "cmd": "systemctl is-active rosorin-base", "expect": "active", "rc": 3, "out": "inactive", "verdict": "FAIL", "why": "inactive"}]}' > $R/ch11.jsonl
n=$(hits); o=$(tty yes "$GC --claude-review $R" 2>&1); ok "7 guide_check --claude-review: added next to the local verdict" '[[ $(hits) == $((n+1)) ]] && grep -q "FAIL / Claude: FAIL" $R/ch11.md'
o=$( (sleep 3; echo yes) | env -u ANTHROPIC_BASE_URL ANTHROPIC_API_KEY=sk-ant-not-a-key script -q -c "$A --claude 'q'" /dev/null 2>&1); ok "8 real API, made-up key: 401 in one line" '[[ $o == *"refused (401)"* ]]'
J=home/llm-train/data/judge; echo '{"id":"j1","ch":"ch11","item":1,"item_text":"x","cmd":"c","rc":0,"out":"active"}' > $J/checks.jsonl; echo '{"id":"j1","gold":"PASS","why":"a"}' > $J/labels.jsonl
unset ANTHROPIC_API_KEY; for s in "teacher_label.py $J/checks.jsonl $W/out.jsonl" "synth_fails.py --per 1 --limit 1" "guide_cards.py --chapters ch10" "research.py q"; do
  o=$(python3 $T/$s 2>&1); rc=$?; ok "9 no key: ${s%% *} stops, exit 1" '[[ $rc == 1 && $o == *"no API key"* ]]'; done
export ANTHROPIC_API_KEY=sk-test ANTHROPIC_BASE_URL=http://127.0.0.1:$PORT_credit
for s in "teacher_label.py $J/checks.jsonl $W/out.jsonl" "synth_fails.py --per 1 --limit 1" "guide_cards.py --chapters ch10" "research.py q"; do
  o=$(python3 $T/$s 2>&1); rc=$?; ok "10 no credit: ${s%% *} exit 1 with the credit message" '[[ $rc == 1 && $o == *"credit balance is too low"* ]]'; done
export ANTHROPIC_BASE_URL=http://127.0.0.1:$PORT_batch_errored
echo old > $J/synth_checks.jsonl; echo old > $J/synth_labels.jsonl; echo old > home/llm-train/data/facts/guide_cards.jsonl; echo old > out.jsonl
python3 $T/synth_fails.py --per 1 --limit 1 >/dev/null 2>&1; rc=$?; ok "11 all requests errored: synth_fails keeps its files, exit 1" '[[ $rc == 1 && $(cat $J/synth_checks.jsonl) == old ]]'
python3 $T/guide_cards.py --chapters ch10 >/dev/null 2>&1; rc=$?; ok "11 all requests errored: guide_cards keeps its file, exit 1" '[[ $rc == 1 && $(cat home/llm-train/data/facts/guide_cards.jsonl) == old ]]'
python3 $T/teacher_label.py $J/checks.jsonl $W/out.jsonl --wait >/dev/null 2>&1; rc=$?; ok "11 all requests errored: teacher_label keeps its file, exit 1" '[[ $rc == 1 && $(cat out.jsonl) == old ]]'
ANTHROPIC_BASE_URL=http://127.0.0.1:$PORT_batch_ok_synth python3 $T/synth_fails.py --per 1 --limit 1 >/dev/null 2>&1; rc=$?; ok "12 batch ok: synth_fails writes, exit 0" '[[ $rc == 0 ]] && grep -q inactive $J/synth_checks.jsonl'
ANTHROPIC_BASE_URL=http://127.0.0.1:$PORT_batch_ok_cards python3 $T/guide_cards.py --chapters ch10 >/dev/null 2>&1; rc=$?; ok "12 batch ok: guide_cards writes, exit 0" '[[ $rc == 0 ]] && grep -q "stand-in card" home/llm-train/data/facts/guide_cards.jsonl'
ANTHROPIC_BASE_URL=http://127.0.0.1:$PORT_batch_ok_label python3 $T/teacher_label.py $J/checks.jsonl $W/out.jsonl --wait >/dev/null 2>&1; rc=$?; ok "12 batch ok: teacher_label writes, exit 0" '[[ $rc == 0 ]] && grep -q stand-in out.jsonl'
echo "$P passed, $F failed"; [ $F = 0 ]
