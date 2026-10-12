# History

2026-09-30, one long debugging and build session.

1. **Symptom:** camera over Tailscale "very laggy and breaks".
2. **Path check:** `tailscale ping` showed the laptop reached the Pi directly
   (14 ms) but the phone went through a DERP relay (186–396 ms). Cause: the Pi
   was on that same phone's hotspot, and iOS Tailscale couldn't go direct to
   its own hotspot clients.
3. **Surprise:** the stream on :8080 was a **Pi Camera** (`rpicam-vid`), not the
   ESP32; the ESP32 only has a serial console.
4. **Round 1:** 30 s test showed 4.4 fps with 9 freezes even on the direct
   path. Bug: `p.stdout.read(65536)` blocked until 64 KB arrived (3–4 frames),
   then kept only the last. Fixed with `read1()`, lighter video (640x480 @ 15,
   q70), Wi-Fi power save off → **15.0 fps, 0 freezes**.
5. **Round 2:** small send buffer + TCP_NODELAY so slow viewers drop stale
   frames instead of lagging; frame counter so fast viewers never miss one
   (verified with a fake camera: 121/121); 12 fps q55 → **11.9 fps, 0 freezes,
   273 KB/s**.
6. **Dashboard** (`app.py`, :8081): phone, desktop and cockpit views, hold-to-drive
   with 0.5 s watchdog, stats bar, Direct/Relay badge, command log.
7. **Safari fix:** Safari won't hand `multipart/x-mixed-replace` to `fetch()`;
   added `/stream.bin` (same bytes, plain type) plus an `<img>` fallback.
8. **Multi-viewer:** measured 3 viewers at 11.9 fps each, CPU 3%, so the Pi was
   fine; the real problem was two pages fighting over the controls → added the
   one-driver lock, and background tabs now pause their video.
9. **Live camera controls:** rewrote `stream.py` on Picamera2 (instant
   picture settings), installer with auto-rollback. Added video-delay
   measurement, telemetry HUD, snapshot, fullscreen.
10. **Lag again:** traced to the laptop running a system upgrade over the same
    hotspot (564 KB/s down; ping to the phone 169–645 ms). Not the Pi.
11. **Wi-Fi order** on the Pi: preferred > home > hotspot, with a safe switch script.
