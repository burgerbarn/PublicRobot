#!/bin/bash
# On-board brain (vision-language model, NVIDIA vLLM Jetson container) as rosorin-brain.service.
# Rollback: sudo systemctl disable --now rosorin-brain && sudo rm /etc/systemd/system/rosorin-brain.service &&
#           sudo systemctl daemon-reload   (model cache ~/hf_cache and the image stay; docker rmi to free space)
set -euo pipefail
[ "$(id -u)" = 0 ] || { echo "run with sudo"; exit 1; }
install -d -o burgerbarn -g burgerbarn /home/burgerbarn/hf_cache
install -m 0644 /home/burgerbarn/setup/rosorin-brain.service /etc/systemd/system/rosorin-brain.service
systemctl daemon-reload
systemctl enable --now rosorin-brain.service
