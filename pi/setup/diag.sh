#!/bin/bash
# Read-only diagnostic for the Pi that relays the ESP32 camera on :8080.
# Run from the laptop:  ssh username@<pi-tailscale-ip> 'bash -s' < ~/pi-camera-diag.sh > ~/pi-diag.txt 2>&1
# Why: the stream stalls 0.7-2 s even on a 14 ms direct path, so the cause is
# on the Pi side (ESP32 Wi-Fi link, or the relay program). Changes nothing.

echo "== relay process listening on 8080 =="
ss -tlnp 2>/dev/null | grep -E ':8080|:80 '
PID=$(ss -tlnp 2>/dev/null | grep ':8080' | grep -o 'pid=[0-9]*' | head -1 | cut -d= -f2)
[ -n "$PID" ] && ps -o pid,pcpu,pmem,args -p "$PID"

echo "== relay source (first 150 lines) =="
if [ -n "$PID" ]; then
  CMD=$(tr '\0' ' ' < /proc/$PID/cmdline)
  echo "cmd: $CMD  cwd: $(readlink /proc/$PID/cwd)"
  for f in $CMD; do [ -f "$f" ] || f="$(readlink /proc/$PID/cwd)/$f"; case "$f" in *.py|*.js|*.sh) [ -f "$f" ] && { echo "--- $f"; head -150 "$f"; };; esac; done
fi

echo "== systemd service for it =="
systemctl list-units --type=service --no-pager 2>/dev/null | grep -iE 'cam|stream|esp|proxy'

echo "== ESP32 neighbours (who the Pi talks to) =="
ip -4 addr show | grep inet; ip neigh
ss -tnp 2>/dev/null | grep -v ':22 '

echo "== Pi Wi-Fi signal =="
iwconfig 2>/dev/null | grep -E 'ESSID|Bit Rate|Quality|Power Management'

echo "== load / temp / throttling =="
uptime; vcgencmd measure_temp 2>/dev/null; vcgencmd get_throttled 2>/dev/null
free -m
