# Measurements

Every number here was measured with `tools/stream_test.py` or `tailscale ping`
on 2026-09-30. No estimates. KB = 1024 bytes.

```
ROUND 1: fix the stream server's read bug, lighter video, Wi-Fi power save off
------------------------------------------------------------------------------
Before (original stream.py, camera asked for 1280x720 @ 20 fps)
  30 s test:     132 frames = 4.4 fps
  Freezes:       9 in 30 s (0.73, 0.74, 0.69, 0.88, 0.86, 1.15, 0.72, 2.05, 1.22 s)
  5 s sample:    26 frames, 464,280 bytes (about 18 KB per frame, 91 KB/s)

After (640x480 @ 15 fps, JPEG quality 70)
  30 s test:     449 frames = 15.0 fps
  Freezes:       0
  Frame size:    29,805 bytes average
  Bandwidth:     436 KB/s


ROUND 2: newest-frame-only delivery (no lag build-up), 12 fps, quality 55
------------------------------------------------------------------------
  30 s test:     356 frames = 11.9 fps
  Freezes:       0
  Frame size:    23,522 bytes average
  Bandwidth:     273 KB/s   (37% less than round 1)


SEVERAL VIEWERS AT ONCE (15 s each, via the dashboard's relay on :8081)
------------------------------------------------------------------
  1 viewer:                 11.9 fps, 152 KB/s, 0 freezes
  3 viewers:                11.9 / 11.9 / 11.9 fps, 153 KB/s each, 0 freezes
  2 dashboard + 1 direct:   11.9 / 11.7 / 11.9 fps, 152 / 151 / 152 KB/s, 0 freezes
  Dashboard status replies during the tests: median 49 / 55 / 64 ms
  Pi right after the 3-viewer test: CPU 3%, camera 12.0 fps,
    Pi Wi-Fi upload 691 KB/s, Wi-Fi signal -61 dBm


DIRECT vs RELAY (tailscale ping)
--------------------------------
  Laptop -> Pi, direct (172.20.10.2:41641):        14 ms, later 6 ms
  Laptop -> phone, relayed via DERP Chicago:      396, 186, 206 ms
  Laptop -> phone, relayed via DERP New York:     325, 135, 159 ms
  Nearest relay servers (tailscale netcheck):      New York 113 ms, Ashburn 113 ms
  Why it matters: the phone hosting the hotspot could not reach the Pi
  directly, so its video went out to a relay server and back.


DURING A LAPTOP SYSTEM UPGRADE OVER THE SAME HOTSPOT (2026-09-30, 20 s test)
--------------------------------------------------------------------------
  Camera on the Pi:  12.0 fps (480p, quality 40, 14 KB/frame), Pi CPU 2%
  Received:          2.9 fps, 41 KB/s, 10 freezes (0.6-1.3 s)
  Ping laptop->Pi:   182 ms on the direct path (6-19 ms normally)
  Ping laptop->phone (hotspot): 169-645 ms, laptop downloading 564 KB/s
  Conclusion: the hotspot was saturated; the Pi was fine.
```
