#!/bin/bash
# Usage: run_with_pause.sh LOG CMD...   Pause Buddy, run CMD (logging to LOG), always resume Buddy afterwards.
LOG=$1; shift
trap '~/.lmstudio/bin/lms server start --bind 0.0.0.0 > /dev/null 2>&1' EXIT   # Buddy voice stays off (owner 2026-10-15); bring it back: ~/llm-train/buddy_resume.sh
~/llm-train/buddy_pause.sh > ${LOG%.log}-pause.log 2>&1
"$@" > "$LOG" 2>&1
echo "exit=$?" >> "$LOG"
