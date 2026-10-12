#!/bin/bash
# Install the robot dashboard as its own service on :8081. Run ON THE PI:
#   sudo bash /home/username/picam-dash/install.sh
# Why a separate service: stream.py / picam-web stay untouched. The dashboard
# only reads the camera from localhost:8080, so if it breaks, :8080 still works.
# Undo:  sudo systemctl disable --now picam-dash && sudo rm /etc/systemd/system/picam-dash.service
set -e
DIR=/home/username/picam-dash
UNIT=/etc/systemd/system/picam-dash.service

# Flask from Raspberry Pi OS packages (small, no pip needed)
python3 -c 'import flask' 2>/dev/null || { apt-get update && apt-get install -y python3-flask; }

[ -f "$UNIT" ] && cp "$UNIT" "$UNIT.bak-$(date +%Y%m%d)"
cat > "$UNIT" <<'EOF'
[Unit]
Description=Robot dashboard: camera, drive controls, health (port 8081)
After=network-online.target picam-web.service tailscaled.service
Wants=network-online.target

[Service]
User=username
WorkingDirectory=/home/username/picam-dash
ExecStart=/usr/bin/python3 /home/username/picam-dash/app.py
Environment=PYTHONUNBUFFERED=1
Restart=always
RestartSec=2

[Install]
WantedBy=multi-user.target
EOF

chown -R username:username "$DIR"
systemctl daemon-reload
systemctl enable --now picam-dash
systemctl restart picam-dash      # pick up a new app.py on re-runs
sleep 3
systemctl is-active picam-dash
curl -s -o /dev/null -w 'dashboard http %{http_code}\n' http://127.0.0.1:8081/
systemctl is-active picam-web     # confirm the camera service is still fine
