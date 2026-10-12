# Architecture

## Hardware

- **Raspberry Pi 5 Model B**, 2 GB RAM, Debian 13 (trixie), USB boot disk (29 GB).
- **Camera Module 3** (Sony imx708, 4608x2592, autofocus kept in Continuous mode).
  The Pi 5 has no hardware JPEG encoder, so stream.py encodes in software.
- **ESP32** on `/dev/ttyUSB0` (115200 baud): browser serial console on :7681
  (`esp32-web.service`: ttyd → tmux session `esp32` → picocom).
- Exact, current details: `docs/pi-snapshot.md` (`tools/snapshot-pi.sh`, generated locally, not committed).
- Motors: **not wired in yet**; `Motors` in `app.py` is a logging stub.

## Services on the Pi

| Service | Port | Program | Job |
|---|---|---|---|
| `picam-web` | 8080 | `~/picam/stream.py` | Owns the camera (Picamera2). MJPEG at `/stream`. Camera controls at `/controls` (**localhost only**). |
| `picam-dash` | 8081 | `~/picam-dash/app.py` | Dashboard pages, drive API, health stats, relays the camera, proxies camera controls. |
| `esp32-web` | 7681 | ttyd + tmux + picocom | ESP32 serial console in the browser. |
| `tailscaled` | | Tailscale | Makes the Pi reachable at `<pi-tailscale-ip>` from anywhere. |

`picam-dash` only reads `:8080` from localhost, so if the dashboard breaks
the raw camera still works, and vice versa.

## Data flow

```
Pi Camera ──Picamera2──> stream.py (:8080)
                           │  JPEG frames, newest-frame-only
                           ▼
                        app.py FrameHub (:8081) ── stamps X-Ts (time on Pi)
                           │
      Tailscale (direct UDP, or relayed via DERP when direct fails)
                           ▼
   Browser: fetch(/stream.bin) → count FPS, measure video delay, draw frames
            (falls back to <img src=/stream> if the browser can't stream fetch)
   Browser → POST /api/drive every 150 ms while a button/key is held
   Browser → GET /api/status every 2 s (temp, CPU, Wi-Fi, Tailscale path/ping…)
   Browser → /api/camera → app.py → 127.0.0.1:8080/controls → Picamera2
```

## Key design choices (and why)

- **Newest frame only.** Each viewer gets the latest frame; a slow viewer
  skips ahead instead of falling behind. Small send buffers (32 KB) stop
  stale frames piling up in the network. A frame counter makes sure a viewer
  that keeps up never misses a frame (tested: 121/121).
- **Heartbeat + watchdog.** The browser repeats the drive command every
  150 ms; the Pi stops after 0.5 s of silence (release, tab hidden, signal lost).
- **One driver at a time.** Whoever presses first owns control until they
  release or the watchdog fires. Others get HTTP 409 and a banner. This fixed
  two open pages fighting (one's release cutting off the other).
- **Live camera controls via Picamera2.** Picture settings and frame rate use
  `set_controls()` (instant). Resolution, rotate/mirror and JPEG quality need
  a ~1 s camera restart. Settings persist in `~/picam/camera.json`.
- **Controls are localhost-only on :8080.** Only the dashboard (same Pi) can
  change the camera; it's the one UI.
- **Background tabs drop their video** to save the Pi's upload and the
  browser's ~6-connections-per-site limit.

## API (dashboard, :8081)

| Method | Path | Body / result |
|---|---|---|
| GET | `/`, `/desktop`, `/cockpit` | pages |
| GET | `/stream` | MJPEG (multipart) |
| GET | `/stream.bin` | same bytes as `application/octet-stream` (Safari-friendly for fetch), parts carry `X-Ts` |
| POST | `/api/drive` | `{"dir":"forward"\|"backward"\|"stop","speed":0-100,"client":"<id>"}` → 200, or 409 if someone else drives |
| GET | `/api/status?c=<id>` | health, Tailscale path, log, `driver`: you/other/null, `server_ms` |
| GET/POST | `/api/camera` | settings; POST a partial change, `"_log":true` to log it, `"reset":true` for defaults |

Camera settings: `brightness` (-1..1), `contrast`/`saturation` (0..2),
`sharpness` (0..4), `ev` (-3..3), `awb`, `exposure_mode` (Normal/Short/Long),
`denoise` (Off/Fast/HighQuality), `fps` (10/12/15/24/30),
`resolution` (640x480/1280x720/1920x1080), `quality` (30..90), `rotate`, `mirror`.

## Network

Wi-Fi order on the Pi (after `pi/setup/wifi-setup.sh`): **preferred network** (30) >
**home** (20) > **phone hotspot** (10). NetworkManager only re-picks when the
current connection drops or on boot.

Lesson learned: when the Pi is on the phone's own hotspot, that phone reaches
the Pi only through a Tailscale relay (135–396 ms). Anything else heavy on
the hotspot (e.g. a laptop system upgrade) chokes the video too.
