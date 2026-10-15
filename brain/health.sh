#!/bin/bash
# One health check for the lightBot brain, run from the Mac: every service on bigbuddy, the XPS and the robot.
# Read-only. Each failed line says what to do (see OPERATING.md, "When something is down").
set -u
ok()   { printf "  OK    %s\n" "$1"; }
bad()  { printf "  DOWN  %s  ->  %s\n" "$1" "$2"; FAILS=$((FAILS+1)); }
FAILS=0

echo "bigbuddy (192.168.1.111)"
if ssh -o ConnectTimeout=5 -o BatchMode=yes bigbuddy true 2>/dev/null; then
  R=$(ssh -o BatchMode=yes bigbuddy 'bash -s' <<'EOF'
for u in buddy-voice lightbot-brain lightbot-xps-tunnel plexamp; do echo "unit $u $(systemctl --user is-active $u)"; done
echo "lms $(~/.lmstudio/bin/lms server status 2>&1 | grep -c 'running on port 1234')"
echo "gemma $(~/.lmstudio/bin/lms ps 2>/dev/null | grep -c gemma-4-12b)"
echo "brain $(curl -s -m 5 127.0.0.1:8300/health | grep -c '"ok": true')"
echo "coder $(curl -s -m 5 127.0.0.1:8081/health | grep -c ok)"
echo "oss $(curl -s -m 5 127.0.0.1:8082/health | grep -c ok)"
echo "gpu $(nvidia-smi --query-gpu=memory.used,memory.total --format=csv,noheader 2>/dev/null)"
echo "disk $(df -h ~ | awk 'NR==2{print $4}')"
EOF
)
  while read -r k a b; do
    case "$k" in
      unit) [ "$b" = active ] && ok "$a" || bad "$a ($b)" "ssh bigbuddy systemctl --user restart $a" ;;
      lms) [ "$a" = 1 ] && ok "LM Studio server :1234" || bad "LM Studio server" "ssh bigbuddy ~/.lmstudio/bin/lms server start" ;;
      gemma) [ "$a" = 1 ] && ok "Gemma 12B loaded (Buddy)" || printf "  idle  Gemma not loaded (normal after 1 h idle; Buddy loads it on the next request)\n" ;;
      brain) [ "$a" = 1 ] && ok "brain :8300" || bad "brain :8300" "ssh bigbuddy systemctl --user restart lightbot-brain" ;;
      coder) [ "$a" = 1 ] && ok "coder on the XPS (tunnel :8081)" || bad "coder via tunnel" "XPS awake? then: ssh bigbuddy systemctl --user restart lightbot-xps-tunnel" ;;
      oss) [ "$a" = 1 ] && ok "gpt-oss on the XPS (tunnel :8082)" || bad "gpt-oss via tunnel" "ssh burgerbarn@192.168.1.114 systemctl --user start lightbot-oss" ;;
      gpu) ok "GPU $a $b" ;;
      disk) ok "free disk $a" ;;
    esac
  done <<< "$R"
else
  bad "bigbuddy" "powered on? network?"
fi

echo "XPS (192.168.1.114)"
if ssh -o ConnectTimeout=5 -o BatchMode=yes burgerbarn@192.168.1.114 true 2>/dev/null; then
  for u in lightbot-coder lightbot-oss lightbot-coder-awake; do
    s=$(ssh -o BatchMode=yes burgerbarn@192.168.1.114 systemctl --user is-active $u)
    [ "$s" = active ] && ok "$u" || bad "$u ($s)" "ssh burgerbarn@192.168.1.114 systemctl --user start $u"
  done
else
  bad "XPS" "asleep or off: wake it (lid open / power); its services start on their own"
fi

echo "robot (192.168.1.108)"
if ssh -o ConnectTimeout=5 -o BatchMode=yes rosorin-wifi true 2>/dev/null; then
  for u in rosorin-base rosorin-nav rosorin-mind; do
    s=$(ssh -o BatchMode=yes rosorin-wifi systemctl is-active $u)
    [ "$s" = active ] && ok "$u" || printf "  info  %s is %s (chapter 29 read-back if unexpected)\n" "$u" "$s"
  done
else
  printf "  info  robot not reachable (off, or Wi-Fi down)\n"
fi

echo; [ $FAILS -eq 0 ] && echo "all OK" || echo "$FAILS problem(s) - see the lines marked DOWN"
