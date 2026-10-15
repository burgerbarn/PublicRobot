#!/bin/bash
# Harness shim (GPU sidecar): the real trtexec with a 1 GiB workspace cap, so an engine build stays inside the
# VRAM ceiling next to the owner's assistant; non-zero exit when the engine file was not written (nanoowl ignores it).
out=""; for a in "$@"; do case "$a" in --saveEngine=*) out="${a#--saveEngine=}";; esac; done
/usr/src/tensorrt/bin/trtexec.real "$@" --memPoolSize=workspace:1024M; rc=$?
[ -n "$out" ] && [ ! -s "$out" ] && { echo "trtexec shim: $out not written" >&2; exit 1; }
exit $rc
