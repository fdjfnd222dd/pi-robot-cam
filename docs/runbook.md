# Runbook

## Deploy

```bash
tools/deploy.sh            # dashboard (app.py): tests → rsync → restart picam-dash
tools/deploy.sh camera     # also stream.py via camera/install.sh (auto-rollback)
```
From the laptop without a `Host pi` entry: `PI=username@<pi-tailscale-ip> tools/deploy.sh`.

## Roll back

| What | How (on the Pi) |
|---|---|
| Camera server | `cp ~/picam/stream.py.bak3-* ~/picam/stream.py && sudo systemctl restart picam-web` (back to the rpicam-vid version) |
| Camera settings | dashboard Camera panel → **Reset all**, or `rm ~/picam/camera.json && sudo systemctl restart picam-web` |
| Dashboard | `git checkout <good commit> && tools/deploy.sh`, or `sudo systemctl disable --now picam-dash` (camera on :8080 keeps working) |
| Wi-Fi priorities | `nmcli con modify <name> connection.autoconnect-priority 0` |

## "It's laggy"

Measure first, don't guess:

1. `tailscale ping <pi-tailscale-ip>`: `via <ip>:41641` = direct (good);
   `via DERP(...)` = relayed (slow). Same check from the phone: Tailscale app → the Pi.
2. `tools/stream_test.py --seconds 20`: fps vs the camera's own fps
   (`/api/status` → `cam_fps`). If the camera makes 12 and you get 3, it's the network.
3. What else is on the same network? A laptop upgrade over the phone
   hotspot took it from 12 fps to 2.9 fps with 182 ms pings (2026-09-30).
4. Camera settings: 1080p frames are ~59 KB (about 4× 480p). On a hotspot use
   480p–720p. Cockpit → Camera → Resolution.
5. The phone's own hotspot + that phone as the viewer = always relayed.
   Put the Pi on real Wi-Fi for demos.

## Health

`/api/status` has temp, throttling (under-voltage!), CPU, memory, Wi-Fi dBm,
Pi upload KB/s, camera fps and viewers. `tools/snapshot-pi.sh` records it all.

## Power

`throttled=0x50000` (seen 2026-10-01 right after a reboot) means under-voltage
and throttling happened since boot. The Pi 5 wants a 5 V / 5 A supply. A motor
wired to the 5 V pin can pull the voltage down and reset the Pi. Drive motors
from a separate supply through a driver board, grounds shared.

## Known to-dos

- Motor driver: fill in the two `TODO(motor driver)` methods in `app.py`.
- Set your own password on the Pi (`passwd`) and prefer SSH keys.
- The dashboard has no login; anyone on the tailnet or the same LAN can drive.
