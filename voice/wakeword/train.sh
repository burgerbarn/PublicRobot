#!/bin/bash
# Train the "yo buddy" wake word on bigbuddy. Unattended; log: ~/voice/wakeword/train.log
# Order: data -> generate clips (Kokoro) -> augment -> train. Output: work/yo_buddy.onnx
set -euo pipefail
V=~/voice/oww-train/bin/python; W=~/voice/wakeword; cd $W
[ -f data/validation_set_features.npy ] && [ -d data/background ] && [ -d data/rir ] || $V prepare_data.py all
TP=$W/train_wrapper.py        # torchaudio.load/info via soundfile (torchaudio>=2.9 needs torchcodec+FFmpeg)
$V $TP --training_config yo_buddy.yaml --generate_clips
$V $TP --training_config yo_buddy.yaml --augment_clips
$V $TP --training_config yo_buddy.yaml --train_model
ls -la work/*.onnx
echo TRAIN_DONE
