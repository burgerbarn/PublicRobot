#!/bin/bash
# run verify.sh in a throwaway container on the persistent home volume (no network)
# usage: ./verify_run.sh [--arch amd64|arm64]   (amd64: lightbot-sim:humble + lightbot-sim-home, the default;
#                                                 arm64: lightbot-sim:humble-arm64 + lightbot-sim-home-arm64, qemu)
ARCH=amd64
[ "$1" = --arch ] && ARCH="$2"
case "$ARCH" in
  amd64) IMAGE=lightbot-sim:humble; VOLUME=lightbot-sim-home ;;
  arm64) IMAGE=lightbot-sim:humble-arm64; VOLUME=lightbot-sim-home-arm64 ;;
  *) echo "usage: $0 [--arch amd64|arm64]" >&2; exit 2 ;;
esac
docker run --rm --platform "linux/$ARCH" --network none --hostname rosorin --add-host rosorin:127.0.0.1 -v "$VOLUME":/home \
  -v ~/lightbot-sim/repo:/repo:ro -v ~/lightbot-sim/verify.sh:/opt/verify.sh:ro -u burgerbarn -w /home/burgerbarn \
  -e HOME=/home/burgerbarn "$IMAGE" bash /opt/verify.sh
