#!/bin/bash
# Docker (Ubuntu repo) + NVIDIA container toolkit (NVIDIA L4T repo), for NVIDIA's Jetson AI containers
# (e.g. ghcr.io/nvidia-ai-iot/vllm:latest-jetson-orin - on-board LLM/VLM, Jetson AI Lab). 2026-09-30.
# Rollback: sudo bash rollback_containers.sh (removes exactly what this added).
set -euo pipefail
[ "$(id -u)" = 0 ] || { echo "run with sudo"; exit 1; }
before=$(mktemp); dpkg-query -W -f='${Package}\n' | sort > "$before"
apt-get install -y docker.io nvidia-container-toolkit
dpkg-query -W -f='${Package}\n' | sort | comm -13 "$before" - > /root/containers_added_packages.txt
cp -n /etc/docker/daemon.json /root/daemon.json.before 2>/dev/null || true
nvidia-ctk runtime configure --runtime=docker
systemctl restart docker
usermod -aG docker burgerbarn
docker info 2>/dev/null | grep -i -E "runtimes|server version"
echo "added $(wc -l < /root/containers_added_packages.txt) packages"
