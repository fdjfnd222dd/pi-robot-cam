#!/usr/bin/env python3
"""Presentation screenshots from the REAL Pi dashboard (no stand-in data).

Headless Firefox (driven over Marionette) loads the Pi's dashboard directly,
so frames, stats and latency are live. Marionette lets us wait for the charts
to fill, open the camera panel, and capture at true 2x for the phone shots.
Usage:  pres_capture.py OUTDIR [--dry]   (--dry: phone+desktop only, short
holds, never sends drive/camera commands)
"""
import base64, json, os, socket, subprocess, sys, threading, time, urllib.error, urllib.request

PI = os.environ.get("PI_HOST") or sys.exit("set PI_HOST to the Pi Tailscale IP")
DASH = 8081
BASE = f"http://{PI}:{DASH}"
HERE = os.path.dirname(os.path.abspath(__file__))


def api(path, body=None, timeout=15):
    req = urllib.request.Request(BASE + path, method="GET" if body is None else "POST",
                                 data=None if body is None else json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, {}


class Marionette:
    """Minimal Marionette client (Firefox's built-in automation protocol).
    Unlike --screenshot, its screenshots honour devicePixelRatio (true 2x)."""
    def __init__(self, w, h, scale=1, port=28281):
        d = os.path.join(HERE, f"ffprof-m{scale}x"); os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "user.js"), "w") as f:
            f.write(f'user_pref("layout.css.devPixelsPerPx", "{scale}.0");\nuser_pref("marionette.port", {port});\n')
        self.p = subprocess.Popen(["firefox", "--headless", "--marionette", "--no-remote", "--profile", d, f"--window-size={w},{h}"],
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(100):
            try:
                self.s = socket.create_connection(("127.0.0.1", port), 1); break
            except OSError:
                time.sleep(0.2)
        else:
            raise RuntimeError("marionette did not start")
        self.s.settimeout(60); self.id = 0; self._read()
        try:
            self.cmd("WebDriver:NewSession", {})
            self.size(w, h)
        except Exception:
            self.close(); raise

    def size(self, w, h):
        """Make the page viewport (not the window) exactly w x h CSS px."""
        # the first tab is a privileged page Marionette can't script; use a fresh tab
        tab = self.cmd("WebDriver:NewWindow", {"type": "tab"})["handle"]
        self.cmd("WebDriver:SwitchToWindow", {"handle": tab})
        self.cmd("WebDriver:Navigate", {"url": "about:blank"})
        for _ in range(3):
            self.cmd("WebDriver:SetWindowRect", {"width": w, "height": h})
            iw, ih = self.cmd("WebDriver:ExecuteScript", {"script": "return [innerWidth, innerHeight]", "args": []})["value"]
            if (iw, ih) == (w, h):
                return
            r = self.cmd("WebDriver:GetWindowRect", {})
            self.cmd("WebDriver:SetWindowRect", {"width": r["width"] + w - iw, "height": r["height"] + h - ih})
            iw, ih = self.cmd("WebDriver:ExecuteScript", {"script": "return [innerWidth, innerHeight]", "args": []})["value"]
            if (iw, ih) == (w, h):
                return
        print(f"  (viewport ended up {iw}x{ih}, wanted {w}x{h})", flush=True)

    def _read(self):
        n = b""
        while not n.endswith(b":"):
            n += self.s.recv(1)
        n = int(n[:-1]); buf = b""
        while len(buf) < n:
            buf += self.s.recv(n - len(buf))
        return json.loads(buf)

    def cmd(self, name, params):
        self.id += 1
        m = json.dumps([0, self.id, name, params]).encode()
        self.s.sendall(str(len(m)).encode() + b":" + m)
        r = self._read()
        if r[2]:
            raise RuntimeError(f"{name}: {r[2]}")
        return r[3]

    def close(self):
        try:
            self.cmd("Marionette:Quit", {})
        except Exception:
            pass
        try:
            self.p.wait(10)
        except subprocess.TimeoutExpired:
            self.p.kill()


def shot(out, path, w, h, hold, scale=1, script=None):
    t = time.time(); m = Marionette(w, h, scale)
    try:
        m.cmd("WebDriver:Navigate", {"url": BASE + path})
        if script:
            time.sleep(1); m.cmd("WebDriver:ExecuteScript", {"script": script, "args": []})
        time.sleep(hold)
        png = m.cmd("WebDriver:TakeScreenshot", {"full": False})["value"]
        open(out, "wb").write(base64.b64decode(png))
    finally:
        m.close()
    dims = subprocess.run(["magick", "identify", "-format", "%wx%h", out], capture_output=True, text=True).stdout
    print(f"  {os.path.basename(out)}: {dims} ({time.time() - t:.0f} s)", flush=True)


def page_shot(out, url, w, h):
    m = Marionette(w, h, 1)
    try:
        m.cmd("WebDriver:Navigate", {"url": url}); time.sleep(1)
        open(out, "wb").write(base64.b64decode(m.cmd("WebDriver:TakeScreenshot", {"full": False})["value"]))
    finally:
        m.close()


def frame_grab():
    """One JPEG from the live stream."""
    s = socket.create_connection((PI, DASH), 10); s.settimeout(10)
    s.sendall(b"GET /stream.bin HTTP/1.1\r\nHost: x\r\n\r\n"); buf = b""
    while True:
        buf += s.recv(65536)
        a = buf.find(b"\r\n\r\n--F")
        i = buf.find(b"Content-Length:")
        if i >= 0:
            j = buf.find(b"\r\n\r\n", i)
            if j >= 0:
                n = int(buf[i + 15:buf.find(b"\r", i)].strip())
                if len(buf) >= j + 4 + n:
                    s.close(); return buf[j + 4:j + 4 + n]


class Driver:
    """Second viewer holding Forward (for the driver-lock shot).
    Motors are a stub on the Pi right now, so this only logs."""
    def __init__(self): self.on = False
    def start(self):
        self.on = True
        threading.Thread(target=self._run, daemon=True).start()
    def _run(self):
        while self.on:
            api("/api/drive", {"dir": "forward", "speed": 40, "client": "pres-2nd-viewer"}, timeout=3); time.sleep(0.15)
    def stop(self):
        self.on = False; time.sleep(0.3)
        api("/api/drive", {"dir": "stop", "speed": 0, "client": "pres-2nd-viewer"})


def terminal_png(out):
    st = subprocess.run(["tailscale", "status"], capture_output=True, text=True).stdout.rstrip()
    pg = subprocess.run(["tailscale", "ping", "-c", "5", PI], capture_output=True, text=True)
    pg = (pg.stdout + pg.stderr).rstrip()
    esc = lambda t: t.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    html = f"""<!doctype html><meta charset=utf-8><style>
body{{margin:0;background:#0d1117;height:100vh;display:flex;align-items:center;justify-content:center}}
.term{{width:1840px;background:#161b22;border:1px solid #30363d;border-radius:14px;overflow:hidden;box-shadow:0 20px 60px #0008}}
.bar{{height:44px;background:#21262d;display:flex;align-items:center;gap:10px;padding:0 18px}}
.bar i{{width:14px;height:14px;border-radius:50%;display:block}}
pre{{margin:0;padding:30px 36px 36px;font:21px/1.5 Hack,"DejaVu Sans Mono",monospace;color:#e6edf3;white-space:pre-wrap;word-break:break-all}}
.p{{color:#7ee787}}.c{{color:#79c0ff}}</style>
<div class=term><div class=bar><i style="background:#ff5f57"></i><i style="background:#febc2e"></i><i style="background:#28c840"></i></div>
<pre><span class=p>$</span> <span class=c>tailscale status</span>
{esc(st)}

<span class=p>$</span> <span class=c>tailscale ping -c 5 {PI}</span>
{esc(pg)}</pre></div>"""
    page = os.path.join(HERE, "term.html"); open(page, "w").write(html)
    page_shot(out, "file://" + page, 1920, 1080)
    print(f"  {os.path.basename(out)}: tailscale status + ping captured", flush=True)
    return st, pg


def main():
    out = os.path.expanduser(sys.argv[1]); dry = "--dry" in sys.argv
    os.makedirs(out, exist_ok=True)
    code, cam = api("/api/camera")
    if not dry and (code != 200 or "settings" not in cam):
        sys.exit(f"camera controls not live on the Pi yet (/api/camera -> {code}); install first")
    print(f"capturing from {BASE}  (dry={dry})", flush=True)

    _, st = api("/api/status")
    if not dry and len(st.get("log", [])) < 2:   # phone shot wants a couple of log lines
        for d in ("forward", "stop"):
            api("/api/drive", {"dir": d, "speed": 50, "client": "pres-demo"}); time.sleep(0.4)

    shot(f"{out}/01-phone.png", "/", 844, 390, 3 if dry else 10, scale=2)
    shot(f"{out}/02-desktop.png", "/desktop", 1920, 1080, 8 if dry else 70)
    if dry:
        print("dry run done"); return
    shot(f"{out}/03-cockpit.png", "/cockpit", 1920, 1080, 30, script="toggleCam()")

    drv = Driver(); drv.start()
    try:
        shot(f"{out}/04-driver-lock.png", "/", 844, 390, 6, scale=2)
    finally:
        drv.stop()

    st_out, ping_out = terminal_png(f"{out}/05-tailscale-status.png")
    with open(f"{out}/numbers.txt", "a") as f:
        f.write(f"\n\nAT SCREENSHOT TIME ({time.strftime('%Y-%m-%d %H:%M')}), shown in 05-tailscale-status.png\n"
                + "-" * 64 + "\n$ tailscale ping -c 5 " + PI + "\n" + ping_out + "\n")

    # 06: highest-res frame from the stream (camera is busy streaming, so no
    # rpicam-still): switch to 1080p q90, grab, then restore previous settings
    prev = cam["settings"]
    api("/api/camera", {"resolution": "1920x1080", "quality": 90}); time.sleep(4)
    jpg = frame_grab()
    api("/api/camera", {"resolution": prev["resolution"], "quality": prev["quality"]})
    open(f"{out}/06-camera-still.jpg", "wb").write(jpg)
    dims = subprocess.run(["magick", "identify", "-format", "%wx%h", f"{out}/06-camera-still.jpg"], capture_output=True, text=True).stdout
    print(f"  06-camera-still.jpg: {dims}, {len(jpg)//1024} KB (restored {prev['resolution']} q{prev['quality']})", flush=True)


if __name__ == "__main__":
    main()
