#!/bin/bash
# Round 2: cut lag on the Tailscale (relayed) path. Run ON THE PI with sudo.
# Why:
#  1. Big default socket buffers let old frames queue up on a slow link
#     (Tailscale DERP relay), so the viewer drifts seconds behind "live".
#     A small send buffer makes writes wait, and the server's existing
#     "always send the newest frame" logic then drops the stale ones.
#     TCP_NODELAY sends each frame immediately instead of batching.
#  2. 12 fps at quality 55 is ~half the bandwidth of 15 fps / q70,
#     which the relay handles much more smoothly.
# Reversible: backup saved as stream.py.bak2-YYYYMMDD. Undo:
#   cp /home/username/picam/stream.py.bak2-* /home/username/picam/stream.py && systemctl restart picam-web
set -e
F=/home/username/picam/stream.py
cp -n "$F" "$F.bak2-$(date +%Y%m%d)"

if ! grep -q SO_SNDBUF "$F"; then
  sed -i '1s/^import /import socket, /' "$F"
  sed -i 's/^class H(BaseHTTPRequestHandler):$/class H(BaseHTTPRequestHandler):\n    def setup(self):\n        super().setup()\n        self.connection.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 32768)\n        self.connection.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)/' "$F"
fi
# 3. Number each frame so a viewer that keeps up never misses one: it sends
#    the newest frame as soon as its last send finishes, and only waits when
#    it already has the latest. Only a backed-up viewer skips (per-client).
if ! grep -q 'seq=0' "$F"; then
  sed -i 's/^frame=b""; lock=threading.Condition()$/frame=b""; seq=0; lock=threading.Condition()/; s/^    global frame$/    global frame, seq/; s/with lock: frame=buf\[s:e+2\]; lock.notify_all()/with lock: frame=buf[s:e+2]; seq+=1; lock.notify_all()/; s/^            try:$/            last=seq\n            try:/; s/with lock: lock.wait(); f=frame$/with lock: lock.wait_for(lambda: seq!=last); f=frame; last=seq/' "$F"
fi
sed -i 's/"--framerate","15","--quality","70"/"--framerate","12","--quality","55"/' "$F"

python3 -m py_compile "$F" && grep -q SO_SNDBUF "$F" && grep -q "wait_for" "$F" && grep -q '"55"' "$F" && echo "stream.py patched"
systemctl restart picam-web && sleep 2 && systemctl is-active picam-web
