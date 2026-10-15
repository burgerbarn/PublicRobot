#!/bin/bash
# Deploy the lightBot brain to bigbuddy from this repo, refresh its data from the Mac's repos, rebuild the index.
#  - code: brain/server, brain/runner -> ~/llm-train/brain ; brain/ask -> ~/llm-train/ask (models.json is NOT
#    overwritten if bigbuddy already has one: it is the live per-machine config; copy it by hand when it changes)
#  - data: code, configs and docs of rosorin-pro and buddy (no .env, no .claude/, no build output) -> ~/llm-train/data/code-v2
#  - Buddy's voice scripts are read live on bigbuddy (~/voice).
# The brain server reloads the index by itself; after a code change: ssh bigbuddy systemctl --user restart lightbot-brain
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
RP=$(cd "$HERE/.." && pwd)
BD=${BUDDY_REPO:-/Users/matty/CCode/buddy}
T=$(mktemp -d)
trap 'rm -rf "$T"' EXIT

ssh bigbuddy 'mkdir -p ~/llm-train/brain ~/llm-train/ask ~/llm-train/data/code-v2/rosorin-pro ~/llm-train/data/code-v2/buddy-app'
rsync -a --exclude models.json "$HERE/server/" "$HERE/runner/" bigbuddy:llm-train/brain/
ssh bigbuddy 'test -f ~/llm-train/brain/models.json' || rsync -a "$HERE/server/models.json" bigbuddy:llm-train/brain/
rsync -a "$HERE/ask/" bigbuddy:llm-train/ask/
rsync -a "$HERE/pull_github.sh" bigbuddy:llm-train/brain/pull_github.sh   # the hourly pull (lightbot-pull.service runs this copy)

git -C "$RP" ls-files | grep -vE '^(guide/out/|guide/sim/|guide/sources/|\.claude/|brain/sim/results/|brain/research/(judge-data|eval)/)' \
  | grep -E '\.(py|cpp|hpp|h|c|sh|yaml|yml|xml|launch|urdf|xacro|cmake)$|CMakeLists\.txt$|^guide/chapters/.*\.md$|^guide/hw/.*\.md$|^docs/.*\.md$|^brain/.*\.md$|README\.md$' \
  > "$T/rp.txt"
git -C "$BD" ls-files | grep -E '\.(py|ts|tsx|js|md|json|yml)$' | grep -vE '^\.claude/|package-lock|tsconfig' > "$T/bd.txt"
rsync -a --files-from="$T/rp.txt" "$RP/" bigbuddy:llm-train/data/code-v2/rosorin-pro/
rsync -a --files-from="$T/bd.txt" "$BD/" bigbuddy:llm-train/data/code-v2/buddy-app/
echo "rosorin-pro $(git -C "$RP" rev-parse --short HEAD) ($(git -C "$RP" branch --show-current)): $(wc -l < "$T/rp.txt") files; buddy: $(wc -l < "$T/bd.txt") files"
ssh bigbuddy '~/llm-train/bin/python ~/llm-train/brain/brain_index.py'
ssh bigbuddy '~/llm-train/ask/robot_ask.py --reindex'   # robot_ask.py (and OpenClaw's ask) keeps its own index
