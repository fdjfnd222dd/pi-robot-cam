# Pi Robot Cam

A Raspberry Pi robot you can watch and drive from a phone or laptop anywhere, with a live camera, hold-to-drive controls and a safety auto-stop.

> No screenshot is included on purpose: the live camera view shows whatever room the robot is in. Run the dashboard and see it for yourself.

## Features

- Live MJPEG camera stream with adjustable settings (brightness, contrast, exposure, white balance, resolution, FPS, JPEG quality, rotate, mirror)
- Hold-to-drive controls with a 0.5 s watchdog: the motors stop if commands stop arriving
- One driver at a time; other viewers are told the controls are locked
- Phone, desktop and full-screen cockpit views, plus a health dashboard (temperature, throttling, CPU, memory, Wi-Fi, Tailscale path and latency)
- Remote access over Tailscale, no port forwarding
- Tests run anywhere with fake camera modules (no Pi needed)

## Hardware

- Raspberry Pi 5 (2 GB is enough), Raspberry Pi OS / Debian 13
- Camera Module 3 (imx708)
- Motor driver board plus a separate motor power supply (never power motors from the Pi's 5 V pin)
- Optional: ESP32 on USB serial for a browser console

## Install / setup

On the Pi (Picamera2 and Flask come from apt):

```bash
sudo apt install python3-flask python3-picamera2
sudo bash pi/picam-dash/install.sh          # dashboard service on :8081
sudo bash pi/picam-dash/camera/install.sh   # camera service on :8080, rolls back if no frames
```

The install scripts assume a Pi user named `username`; edit them to match yours. Set your own password on the Pi and prefer SSH keys. On your computer:

```bash
pip install -r requirements-dev.txt
python3 -m unittest discover -s tests
export PI_HOST=<pi-tailscale-ip>
tools/deploy.sh
```

Wi-Fi fallback helper (run on the Pi): `sudo HOME_SSID=... HOME_PSK=... bash pi/setup/wifi-setup.sh`.
Secrets are read from environment variables only; none are stored in this repo.

## Usage

| View | URL | For |
|---|---|---|
| Phone | `http://<pi-tailscale-ip>:8081/` | Landscape phone: big drive buttons, speed slider |
| Desktop | `http://<pi-tailscale-ip>:8081/desktop` | Stats, charts, keyboard driving (W/S, A/D speed, Space stop) |
| Cockpit | `http://<pi-tailscale-ip>:8081/cockpit` | Full-screen video with HUD and camera settings |
| Raw camera | `http://<pi-tailscale-ip>:8080/` | Plain MJPEG stream |

Measure the stream with `PI_HOST=<pi-tailscale-ip> tools/stream_test.py --seconds 20`. Motor code is a stub (logs only) until the `TODO(motor driver)` spots in `pi/picam-dash/app.py` are filled in.

## Architecture

Two small services on the Pi: `stream.py` (Picamera2, port 8080) produces MJPEG and applies camera settings; `app.py` (Flask, port 8081) serves the pages, the drive API with its watchdog and single-driver lock, system stats, and a camera proxy. Browsers talk only to the dashboard. See [docs/architecture.md](docs/architecture.md), [docs/runbook.md](docs/runbook.md), [docs/measurements.md](docs/measurements.md) and [docs/cloud.md](docs/cloud.md).

## Why I built it

Remote-controlled robots are a hands-on way to learn embedded Linux, networking and video streaming together. Getting smooth video over a relayed connection forces real measurement instead of guessing. Safety features like a watchdog and a single-driver lock make it practical to leave running.

## Remote access

The Pi joins a [Tailscale](https://tailscale.com) tailnet, so the dashboard is reachable from your phone or laptop without opening router ports. Find the Pi's address with `tailscale ip -4` and use it wherever `<pi-tailscale-ip>` appears. The dashboard has no login: restrict who is on your tailnet (ACLs) and do not expose ports 8080/8081 to the internet.

## License

MIT, see [LICENSE](LICENSE).
