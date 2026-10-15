#!/bin/bash
# Undo install_vision.sh: remove ~/vision/venv + engines + src, and the NVIDIA packages it added.
set -euo pipefail
rm -rf ~/vision/venv ~/vision/engines ~/vision/src ~/.cache/huggingface/hub/models--google--owlvit-base-patch*
sudo apt-mark unhold nvidia-l4t-dla-compiler
sudo apt-get remove -y nvidia-l4t-dla-compiler cuda-libraries-12-6 cuda-cudart-dev-12-6 cuda-nvcc-12-6 cuda-cupti-12-6 \
  libcudnn9-cuda-12 tensorrt-libs libnvinfer-bin python3-libnvinfer libnvinfer-dev libnvonnxparsers-dev libnvinfer-plugin-dev
sudo apt-get autoremove -y
echo VISION_ROLLED_BACK
