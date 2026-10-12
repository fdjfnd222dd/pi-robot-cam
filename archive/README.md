# Archive

One-off fix scripts from the first debugging rounds. They patched the old
`rpicam-vid` version of `stream.py`, which has since been replaced by the
Picamera2 version. **Don't run them.** Kept to show what changed and why
(see `docs/history.md`).

- `round1-fix.sh`: `read()` → `read1()`, 640x480 @ 15 fps q70, Wi-Fi power save off
- `round2-fix.sh`: small send buffer, TCP_NODELAY, frame counter, 12 fps q55
