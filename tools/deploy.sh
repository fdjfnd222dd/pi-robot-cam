#!/usr/bin/env bash
# Push pi/picam-dash/ to the Pi and restart what changed.
#   tools/deploy.sh            dashboard only (app.py) -> restart picam-dash
#   tools/deploy.sh camera     also swap stream.py via camera/install.sh (auto-rollback)
# Uses the "pi" SSH host (cloud/setup.sh creates it). From the laptop, add a
# matching "Host pi" entry to ~/.ssh/config or set PI=username@<pi-tailscale-ip>.
# Why rsync the whole folder: the Pi's ~/picam-dash mirrors pi/picam-dash exactly.
set -euo pipefail
PI="${PI:-pi}"
cd "$(dirname "$0")/.."

python3 -m py_compile pi/picam-dash/app.py pi/picam-dash/camera/stream.py
python3 -m unittest discover -s tests -q      # never ship something the tests reject

rsync -av --delete --exclude __pycache__ pi/picam-dash/ "$PI":picam-dash/
if [ "${1:-}" = "camera" ]; then
  ssh -t "$PI" 'sudo bash ~/picam-dash/camera/install.sh'      # restarts both services
else
  ssh "$PI" 'sudo systemctl restart picam-dash && sleep 3 && systemctl is-active picam-dash'
fi
ssh "$PI" 'curl -s -o /dev/null -w "dashboard http %{http_code}\n" http://127.0.0.1:8081/'
