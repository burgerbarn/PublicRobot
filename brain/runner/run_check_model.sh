#!/bin/bash
# Phase 2 check run with any LM Studio model on bigbuddy's GPU: Buddy's voice off and other models unloaded for the
# run (owner 2026-10-14: bigbuddy belongs to the project), LM Studio's server stays up. Buddy comes back afterwards, always.
# Usage: run_check_model.sh LMS_MODEL_KEY SERVED_NAME EFFORT|- CHAPTER...
KEY=$1; NAME=$2; EFFORT=$3; shift 3
LOG=~/llm-train/runs/$NAME-$EFFORT-$(date +%Y%m%dT%H%M).log
resume() { ~/.lmstudio/bin/lms unload --all >> "$LOG" 2>&1; ~/llm-train/buddy_resume.sh >> "$LOG" 2>&1; }
trap resume EXIT
systemctl --user stop buddy-voice
~/.lmstudio/bin/lms unload --all > "$LOG" 2>&1
~/.lmstudio/bin/lms load "$KEY" --context-length 16384 -y >> "$LOG" 2>&1
nvidia-smi --query-gpu=memory.used --format=csv,noheader >> "$LOG"
E=(); [ "$EFFORT" != "-" ] && E=(--effort "$EFFORT")
~/llm-train/bin/python ~/llm-train/brain/guide_check.py "$@" --model "$NAME" "${E[@]}" >> "$LOG" 2>&1
