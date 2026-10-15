#!/bin/bash
# bigbuddy: pull rosorin-pro from GitHub (read-only deploy key) and refresh the local brain - no Mac needed.
#  - branch rebuild: guide, results, code -> ~/llm-train/data/code-v2/rosorin-pro (same filter as sync_brain.sh)
#  - every cloud branch claude/*: brain/research (reports, task files) -> ~/llm-train/research/cloud/<branch>/ (indexed),
#    fact cards -> ~/llm-train/data/facts/cloud-<branch>.jsonl
#  - rebuilds the index only when something changed. Runs hourly (lightbot-pull.timer); by hand: this script.
set -uo pipefail
REPO=~/llm-train/repo
DATA=~/llm-train/data/code-v2/rosorin-pro
LOG=~/llm-train/runs/pull_github.log
exec >> "$LOG" 2>&1
echo "== $(date -Is)"
if [ ! -d "$REPO/.git" ]; then
  git clone -q git@github-rosorin:burgerbarn/rosorin-pro.git "$REPO" || exit 1
fi
cd "$REPO" || exit 1
before=$(git for-each-ref --format='%(objectname)' refs/remotes/origin | sort | md5sum)
git fetch -q --prune origin || { echo "fetch failed"; exit 1; }
after=$(git for-each-ref --format='%(objectname)' refs/remotes/origin | sort | md5sum)
[ "$before" = "$after" ] && [ -f ~/llm-train/brain/index.json ] && { echo "no change"; exit 0; }

# guide + code from rebuild, into the brain's data copy
T=$(mktemp -d); trap 'rm -rf "$T"' EXIT
git --work-tree="$T" checkout -q origin/rebuild -- . 2>/dev/null || git archive origin/rebuild | tar -x -C "$T"
git ls-tree -r --name-only origin/rebuild | grep -vE '^(guide/out/|guide/sim/|guide/sources/|\.claude/|brain/sim/results/|brain/research/(judge-data|eval)/)' \
  | grep -E '\.(py|cpp|hpp|h|c|sh|yaml|yml|xml|launch|urdf|xacro|cmake)$|CMakeLists\.txt$|^guide/chapters/.*\.md$|^guide/hw/.*\.md$|^docs/.*\.md$|^brain/.*\.md$|README\.md$' \
  > "$T/.files"
mkdir -p "$DATA" && rsync -a --files-from="$T/.files" "$T/" "$DATA/"
git reset -q   # the checkout above touched the index only through --work-tree; keep the clone clean
echo "rebuild $(git rev-parse --short origin/rebuild): $(wc -l < "$T/.files") files"

# cloud results
git show origin/rebuild:brain/research/facts.jsonl > "$T/.facts_seen" 2>/dev/null || : > "$T/.facts_seen"
rm -rf ~/llm-train/research/cloud && mkdir -p ~/llm-train/research/cloud && rm -f ~/llm-train/data/facts/cloud-*.jsonl
for b in $(git for-each-ref --format='%(refname:strip=3)' 'refs/remotes/origin/claude/*'); do
  name=${b#claude/}
  # only what this branch adds or changes under brain/research (rebuild's own files are indexed from the data copy)
  files=$(git diff --name-only --diff-filter=AM origin/rebuild "origin/$b" -- brain/research | grep -E '\.(md|jsonl)$')
  [ -n "$files" ] || continue
  mkdir -p ~/llm-train/research/cloud/"$name"
  git archive "origin/$b" $files | tar -x -C ~/llm-train/research/cloud/"$name" --strip-components=2
  f=~/llm-train/research/cloud/"$name"/facts.jsonl
  n=0
  if [ -s "$f" ]; then   # cards not already in rebuild or an earlier branch
    grep -vxFf "$T/.facts_seen" "$f" > ~/llm-train/data/facts/cloud-"$name".jsonl || true
    cat ~/llm-train/data/facts/cloud-"$name".jsonl >> "$T/.facts_seen"; n=$(wc -l < ~/llm-train/data/facts/cloud-"$name".jsonl)
  fi
  find ~/llm-train/research/cloud/"$name" -name '*.jsonl' -delete   # cards go to data/facts; judge/eval data is not for the index
  echo "$b $(git rev-parse --short origin/$b): $(echo "$files" | wc -l) changed files, $n new cards"
done
~/llm-train/bin/python ~/llm-train/brain/brain_index.py
~/llm-train/ask/robot_ask.py --reindex   # robot_ask.py (and OpenClaw's `ask`) keeps its own index
