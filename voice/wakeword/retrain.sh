#!/bin/bash
# Retrain "yo buddy" with the current real recordings (add_real.py) and export ~/voice/models/wakeword/yo_buddy_<tag>.onnx
# Usage: retrain.sh <tag> [--train-only]. Pauses buddy-voice during GPU training (Whisper + LM Studio + training
# exceeded 16 GB -> CUDA OOM, 2026-09-28). Exports ONLY if training wrote a new model (the known TFLite-export
# failure after the ONNX export is tolerated; nothing else is).
set -euo pipefail
TAG=${1:?tag}; V=~/voice/oww-train/bin/python; W=~/voice/wakeword; cd $W
if [ "${2:-}" != "--train-only" ]; then
  $V add_real.py
  rm -f work/yo_buddy/positive_features_train.npy work/yo_buddy/negative_features_train.npy
  $V train_wrapper.py --training_config yo_buddy.yaml --augment_clips
fi
MARK=$(date +%s)
systemctl --user stop buddy-voice.service
trap 'systemctl --user start buddy-voice.service' EXIT
$V train_wrapper.py --training_config yo_buddy.yaml --train_model > train_step.log 2>&1 || \
  grep -q -E "onnx_tf|convert_onnx_to_tflite" train_step.log || { tail -5 train_step.log; exit 1; }
[ "$(stat -c %Y work/yo_buddy.onnx)" -ge "$MARK" ] || { echo "no new model written"; tail -5 train_step.log; exit 1; }
$V -c "
import onnx; m = onnx.load('work/yo_buddy.onnx'); onnx.save_model(m, '/home/burgerbarn/voice/models/wakeword/yo_buddy_$TAG.onnx', save_as_external_data=False); print('exported yo_buddy_$TAG.onnx')"
echo RETRAIN_DONE
