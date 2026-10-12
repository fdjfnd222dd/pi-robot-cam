#!/usr/bin/env bash
# Record the Pi's real configuration into docs/pi-snapshot.md (local, git-ignored) (read-only on the Pi).
#   tools/snapshot-pi.sh               (uses the "pi" SSH host)
#   PI=username@<pi-tailscale-ip> tools/snapshot-pi.sh
# Why: the repo documents the Pi; this keeps the docs honest after changes.
# It never prints Wi-Fi passwords (only network names and priorities).
set -euo pipefail
PI="${PI:-pi}"
cd "$(dirname "$0")/.."
out=docs/pi-snapshot.md (local, git-ignored)
{
  echo "# Pi snapshot"
  echo
  echo "Generated $(date '+%Y-%m-%d %H:%M') by tools/snapshot-pi.sh. Do not edit by hand."
  echo
  echo '```'
  ssh "$PI" 'bash -s' <<'REMOTE'
section(){ echo; echo "## $1"; }
section "system";        uname -a; grep PRETTY /etc/os-release; cat /proc/device-tree/model 2>/dev/null; echo; uptime
section "memory / disk"; free -m; df -h / | tail -1
section "health";        vcgencmd measure_temp 2>/dev/null; vcgencmd get_throttled 2>/dev/null
section "camera";        rpicam-hello --list-cameras 2>&1 | head -8
                         python3 -c 'import picamera2,importlib.metadata as m;print("picamera2",m.version("picamera2"))' 2>&1
                         cat ~/picam/camera.json 2>/dev/null
section "services";      systemctl --no-pager list-units --type=service | grep -Ei 'picam|esp32|tailscale|ttyd'
for u in picam-web picam-dash esp32-web; do section "unit: $u"; systemctl cat --no-pager $u 2>&1; done
section "listening ports"; ss -tlnp 2>/dev/null | grep -Ev '127.0.0.53|::1'
section "wifi profiles (names + priority only)"; nmcli -f NAME,TYPE,AUTOCONNECT,AUTOCONNECT-PRIORITY con show
section "wifi now (connected network only)"; nmcli -f IN-USE,SSID,SIGNAL,CHAN dev wifi list --rescan no | awk 'NR==1 || $1=="*"' 
section "tailscale";     tailscale version | head -1; tailscale status --self --peers=false 2>&1 | head -3
section "files";         ls -la ~/picam ~/picam-dash ~/picam-dash/camera 2>&1
section "sudo without password?"; sudo -n true 2>/dev/null && echo yes || echo "no (needs password)"
REMOTE
  echo '```'
} > "$out"
echo "wrote $out ($(wc -l < "$out") lines)"
