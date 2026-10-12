#!/bin/bash
# Fix choppy/freezing Pi Camera stream on :8080. Run ON THE PI with sudo.
# Why:
#  1. stream.py uses p.stdout.read(65536), which BLOCKS until 64 KB arrive
#     (~3-4 frames), then keeps only the last one -> ~4 fps, bursty.
#     read1() returns as soon as any data is ready -> every frame goes out.
#  2. 1280x720@20 is more than a phone-hotspot / Tailscale relay can carry
#     smoothly. 640x480@15 at JPEG quality 70 is small and steady.
#  3. Pi Wi-Fi power saving naps the radio between packets -> 1-2 s stalls.
# Reversible: original saved as stream.py.bak-YYYYMMDD. Undo:
#   cp /home/username/picam/stream.py.bak-* /home/username/picam/stream.py && systemctl restart picam-web
set -e
F=/home/username/picam/stream.py
cp -n "$F" "$F.bak-$(date +%Y%m%d)"

# 1. non-blocking read (the main bug)
sed -i 's/p\.stdout\.read(65536)/p.stdout.read1(65536)/' "$F"
# 2. lighter stream
sed -i 's/"--width","1280","--height","720","--framerate","20"/"--width","640","--height","480","--framerate","15","--quality","70"/' "$F"
grep -q 'read1' "$F" && grep -q '"640"' "$F" && echo "stream.py patched"

# 3. Wi-Fi power save off: now, and persistently via NetworkManager
iw dev wlan0 set power_save off 2>/dev/null || true
CON=$(nmcli -t -f NAME,DEVICE con show --active 2>/dev/null | grep ':wlan0$' | cut -d: -f1)
[ -n "$CON" ] && nmcli con modify "$CON" 802-11-wireless.powersave 2 && echo "powersave off for '$CON'"
iw dev wlan0 get power_save 2>/dev/null

systemctl restart picam-web && sleep 2 && systemctl is-active picam-web
