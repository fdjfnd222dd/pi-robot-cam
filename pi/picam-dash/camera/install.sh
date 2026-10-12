#!/bin/bash
# Swap the Pi camera stream to the Picamera2 version with live controls.
# Run ON THE PI:  sudo bash /home/username/picam-dash/camera/install.sh
# Why: rpicam-vid can't change settings while running; Picamera2 can.
# Safety: the old stream.py is kept (stream.py.bak3-YYYYMMDD). If the new one
# doesn't produce frames within ~10 s, this script puts the old one back.
# Manual undo:
#   cp /home/username/picam/stream.py.bak3-* /home/username/picam/stream.py && sudo systemctl restart picam-web
set -e
OLD=/home/username/picam/stream.py
NEW=/home/username/picam-dash/camera/stream.py
BAK="$OLD.bak3-$(date +%Y%m%d)"

python3 -c 'import picamera2' 2>/dev/null || { echo "installing python3-picamera2"; apt-get update && apt-get install -y python3-picamera2; }

cp -n "$OLD" "$BAK"
install -o username -g username -m 644 "$NEW" "$OLD"
systemctl restart picam-web

count_frames() {   # frames received from :8080/stream in 5 s
python3 - <<'PY'
import socket,time
n=0
try:
    s=socket.create_connection(("127.0.0.1",8080),5);s.settimeout(3)
    s.sendall(b"GET /stream HTTP/1.1\r\nHost: x\r\n\r\n");buf=b"";t=time.time()
    while time.time()-t<5:
        d=s.recv(65536)
        if not d:break
        buf+=d
        while True:
            a=buf.find(b"\xff\xd8");i=buf.find(b"\xff\xd9",a+2)
            if a<0 or i<0:break
            buf=buf[i+2:];n+=1
except Exception: pass
print(n)
PY
}
sleep 5
N=$(count_frames)
if [ "$N" -lt 10 ]; then
  echo "!! new camera stream gave $N frames in 5 s -> rolling back"
  journalctl -u picam-web -n 25 --no-pager
  cp "$BAK" "$OLD"; chown username:username "$OLD"
  systemctl restart picam-web; sleep 3
  echo "rolled back; old stream frames in 5 s: $(count_frames)"
  exit 1
fi
echo "camera stream OK: $N frames in 5 s"
curl -s -m 5 http://127.0.0.1:8080/controls | head -c 300; echo
systemctl restart picam-dash && sleep 3 && systemctl is-active picam-dash
