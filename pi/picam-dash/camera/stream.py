#!/usr/bin/env python3
# Pi Camera MJPEG stream on :8080 with LIVE camera controls (Picamera2).
# Replaces the rpicam-vid version; install.sh keeps it as stream.py.bak3-*.
#   /          simple viewer page (unchanged)
#   /stream    MJPEG (unchanged). Newest-frame rule: a viewer that keeps up
#              gets every frame, a backed-up one skips ahead instead of lagging.
#   /controls  GET = current settings, POST = change some. Localhost only:
#              the dashboard (:8081) is the UI and forwards requests here.
# Picture settings and frame rate apply instantly (set_controls). Resolution,
# rotate/mirror and JPEG quality need a camera restart: about a 1 s blip.
# Settings are saved to camera.json next to this file and reloaded on boot.
import io, json, os, socket, socketserver, threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from libcamera import Transform, controls as lc
from picamera2 import Picamera2
from picamera2.encoders import JpegEncoder
from picamera2.outputs import FileOutput

PORT = int(os.environ.get("PICAM_PORT", 8080))
HERE = os.path.dirname(os.path.abspath(__file__))
SETTINGS_FILE = os.path.join(HERE, "camera.json")

RESOLUTIONS = {"640x480": (640, 480), "1280x720": (1280, 720), "1920x1080": (1920, 1080)}
DEFAULTS = {"brightness": 0.0, "contrast": 1.0, "saturation": 1.0, "sharpness": 1.0, "ev": 0.0,
            "awb": "Auto", "exposure_mode": "Normal", "denoise": "Fast",
            "resolution": "640x480", "fps": 12, "quality": 55, "rotate": False, "mirror": False}
NUMBERS = {"brightness": (-1, 1), "contrast": (0, 2), "saturation": (0, 2), "sharpness": (0, 4),
           "ev": (-3, 3), "quality": (30, 90)}
CHOICES = {"awb": ["Auto", "Daylight", "Cloudy", "Indoor", "Fluorescent", "Tungsten", "Incandescent"],
           "exposure_mode": ["Normal", "Short", "Long"], "denoise": ["Off", "Fast", "HighQuality"],
           "resolution": list(RESOLUTIONS), "fps": [10, 12, 15, 24, 30]}
TOGGLES = ("rotate", "mirror")
RESTART_KEYS = ("resolution", "quality", "rotate", "mirror")


def _enum(path, member):
    """libcamera enum member by dotted path, or None if this build lacks it."""
    obj = lc
    for part in path.split("."):
        obj = getattr(obj, part, None)
        if obj is None:
            return None
    return getattr(obj, member, None)


# setting -> (libcamera control, converter). Applied live, no restart.
LIVE = {
    "brightness": ("Brightness", float),
    "contrast": ("Contrast", float),
    "saturation": ("Saturation", float),
    "sharpness": ("Sharpness", float),
    "ev": ("ExposureValue", float),
    "awb": ("AwbMode", lambda v: _enum("AwbModeEnum", v)),
    "exposure_mode": ("AeExposureMode", lambda v: _enum("AeExposureModeEnum", v)),
    "denoise": ("NoiseReductionMode", lambda v: _enum("draft.NoiseReductionModeEnum", v)),
    "fps": ("FrameDurationLimits", lambda v: (int(1e6 / v), int(1e6 / v))),
}


def clean(key, value):
    """Validate one setting. Raises ValueError on a bad value."""
    if key in NUMBERS:
        lo, hi = NUMBERS[key]
        v = max(lo, min(hi, float(value)))
        return int(round(v)) if key == "quality" else round(v, 3)
    if key in CHOICES:
        v = int(value) if key == "fps" else str(value)
        if v not in CHOICES[key]:
            raise ValueError(f"{key} must be one of {CHOICES[key]}")
        return v
    if key in TOGGLES:
        return bool(value)
    raise KeyError(key)


def load_settings():
    s = dict(DEFAULTS)
    try:
        with open(SETTINGS_FILE) as f:
            for k, v in json.load(f).items():
                try:
                    s[k] = clean(k, v)
                except (KeyError, ValueError, TypeError):
                    pass
    except (OSError, ValueError):
        pass
    return s


def save_settings(s):
    tmp = SETTINGS_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(s, f, indent=1)
    os.replace(tmp, SETTINGS_FILE)


# ---- newest-frame hand-off (same rule as before)
frame = b""; seq = 0; cond = threading.Condition()


class FrameOut(io.BufferedIOBase):
    def write(self, buf):
        global frame, seq
        with cond:
            frame = bytes(buf); seq += 1; cond.notify_all()
        return len(buf)


class Camera:
    def __init__(self):
        self.picam2 = Picamera2()
        self.lock = threading.Lock()
        self.s = load_settings()
        self.out = FrameOut()
        self._start()

    def supported(self):
        cc = self.picam2.camera_controls
        return [k for k, (name, _) in LIVE.items() if name in cc] + list(RESTART_KEYS)

    def _controls(self, keys):
        cc, out = self.picam2.camera_controls, {}
        for k in keys:
            name, conv = LIVE[k]
            if name in cc:
                v = conv(self.s[k])
                if v is not None:
                    out[name] = v
        return out

    def _start(self):
        s = self.s
        tf = Transform(hflip=bool(s["rotate"]) ^ bool(s["mirror"]), vflip=bool(s["rotate"]))
        cfg = self.picam2.create_video_configuration(main={"size": RESOLUTIONS[s["resolution"]]}, transform=tf)
        self.picam2.configure(cfg)
        ctl = self._controls(LIVE)
        if "AfMode" in self.picam2.camera_controls and _enum("AfModeEnum", "Continuous") is not None:
            ctl["AfMode"] = _enum("AfModeEnum", "Continuous")   # Camera Module 3: keep focusing
        self.picam2.set_controls(ctl)
        self.picam2.start_recording(JpegEncoder(q=int(s["quality"])), FileOutput(self.out))

    def _restart(self):
        try:
            self.picam2.stop_recording()
        except Exception:
            pass
        self._start()

    def apply(self, changes):
        with self.lock:
            new = dict(DEFAULTS) if changes.get("reset") else dict(self.s)
            for k, v in changes.items():
                if k in DEFAULTS:
                    new[k] = clean(k, v)
            old, self.s = self.s, new
            restart = any(new[k] != old[k] for k in RESTART_KEYS)
            live = [k for k in LIVE if new[k] != old[k]]
            try:
                if restart:
                    self._restart()
                elif live:
                    self.picam2.set_controls(self._controls(live))
            except Exception:
                self.s = old            # put the last working settings back
                if restart:
                    self._restart()
                raise
            save_settings(self.s)
            return self.s

    def close(self):
        try:
            self.picam2.stop_recording()
        except Exception:
            pass


PAGE = b"<html><body style=\"margin:0;background:#000\"><img src=\"/stream\" style=\"width:100%;height:auto\"></body></html>"


class H(BaseHTTPRequestHandler):
    def setup(self):
        super().setup()
        self.connection.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 32768)
        self.connection.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)

    def log_message(self, *a):
        pass

    def _local(self):
        return self.client_address[0] in ("127.0.0.1", "::1")

    def _json(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code); self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)

    def _state(self):
        return {"settings": cam.s, "supported": cam.supported()}

    def do_GET(self):
        if self.path.startswith("/stream"):
            self.send_response(200); self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=F"); self.end_headers()
            last = seq
            try:
                while True:
                    with cond:
                        if not cond.wait_for(lambda: seq != last, timeout=5):
                            continue
                        f, last = frame, seq
                    self.wfile.write(b"--F\r\nContent-Type: image/jpeg\r\nContent-Length: %d\r\n\r\n" % len(f) + f + b"\r\n")
            except Exception:
                pass
        elif self.path.startswith("/controls"):
            if not self._local():
                return self._json(403, {"error": "controls are local only; use the dashboard"})
            self._json(200, self._state())
        else:
            self.send_response(200); self.send_header("Content-Type", "text/html"); self.end_headers()
            self.wfile.write(PAGE)

    def do_POST(self):
        if not self.path.startswith("/controls") or not self._local():
            return self._json(403, {"error": "controls are local only; use the dashboard"})
        try:
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
            cam.apply(body)
            self._json(200, self._state())
        except (ValueError, TypeError, KeyError) as e:
            self._json(400, {"error": str(e), **self._state()})
        except Exception as e:
            self._json(500, {"error": f"camera: {e}", **self._state()})


class S(socketserver.ThreadingMixIn, HTTPServer):
    daemon_threads = True


if __name__ == "__main__":
    cam = Camera()
    try:
        S(("0.0.0.0", PORT), H).serve_forever()
    finally:
        cam.close()
