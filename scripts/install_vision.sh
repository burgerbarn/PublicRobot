#!/bin/bash
# Open-vocabulary detection on the robot (2026-09-29, owner OK: vision runs on the robot).
# NVIDIA only: CUDA 12.6 libs, cuDNN 9, TensorRT 10.3 from the robot's NVIDIA L4T apt repo (r36.4) - NOT the
# nvidia-jetpack metapackage (it would add nvidia-l4t-* 36.4.7 packages and replace the system OpenCV 4.5.4 that
# ROS 2 Humble uses, removing 19 packages). nvidia-l4t-dla-compiler pinned to 36.4.3 (= held L4T) and held.
# Python: venv ~/vision/venv (--system-site-packages for tensorrt/cv2); torch 2.8.0 + torchvision 0.23.0 CUDA
# wheels from NVIDIA Jetson AI Lab (pypi.jetson-ai-lab.io/jp6/cu126); transformers 4.46.3; onnx 1.17;
# torch2trt 4e820ae and nanoowl fb553de (NVIDIA-AI-IOT, MIT / Apache-2.0).
# Engines: OWL-ViT base patch16 (default) + patch32, fp16, 768x768. Rollback: bash rollback_vision.sh
set -euo pipefail
sudo apt-get install -y cuda-libraries-12-6 cuda-cudart-dev-12-6 cuda-nvcc-12-6 cuda-cupti-12-6 libcudnn9-cuda-12 \
  tensorrt-libs libnvinfer-bin python3-libnvinfer libnvinfer-dev libnvonnxparsers-dev libnvinfer-plugin-dev \
  python3-venv python3-pip
sudo apt-get install -y nvidia-l4t-dla-compiler=36.4.3-20250107174145 && sudo apt-mark hold nvidia-l4t-dla-compiler
sudo ldconfig
python3 -c "import tensorrt; print('tensorrt', tensorrt.__version__)"
IDX=https://pypi.jetson-ai-lab.io/jp6/cu126/+simple
mkdir -p ~/vision/src ~/vision/engines && cd ~/vision
[ -d venv ] || python3 -m venv --system-site-packages venv
. venv/bin/activate
pip install --upgrade pip setuptools wheel
pip install --index-url $IDX "torch==2.8.0" "torchvision==0.23.0" "numpy<2" "transformers==4.46.3" "onnx<1.18" pillow "matplotlib<3.9"
cd src
[ -d torch2trt ] || git clone https://github.com/NVIDIA-AI-IOT/torch2trt.git
[ -d nanoowl ] || git clone https://github.com/NVIDIA-AI-IOT/nanoowl.git
(cd torch2trt && git checkout -q 4e820ae && pip install --no-build-isolation --no-deps .)
(cd nanoowl && git checkout -q fb553de && pip install --no-build-isolation --no-deps .)
cd ~/vision
python -c "import torch; assert torch.cuda.is_available(); print('torch', torch.__version__, 'cuda ok')"
for P in 16 32; do
  [ -s engines/owl_image_encoder_patch$P.engine ] || \
    python -m nanoowl.build_image_encoder_engine engines/owl_image_encoder_patch$P.engine --model_name google/owlvit-base-patch$P
done
echo VISION_INSTALLED
