#!/usr/bin/env python3
# Robot dashboard for the Pi: camera + hold-to-drive controls + health stats.
# Serves on :8081 (service: picam-dash). Does NOT touch stream.py / :8080;
# it only reads that stream from localhost and relays it, so the page can
# count frames (a plain cross-port <img> can't report FPS to JavaScript).
#
# Views:  /         phone (landscape)
#         /desktop  desktop with full stats + trend charts + keyboard driving
#         /cockpit  desktop, full-screen video with a see-through HUD
# Camera settings: the desktop/cockpit Camera panel calls /api/camera, which
# forwards to stream.py's localhost-only /controls (Picamera2, applied live).
#
# Safety: the browser re-sends the drive command every 150 ms while a button
# (or key) is held. If the Pi hears nothing for 0.5 s (released, tab closed,
# signal lost), the watchdog stops the motors.
import collections, json, os, re, shutil, socket, subprocess, threading, time, urllib.error, urllib.request
from flask import Flask, Response, jsonify, request

WATCHDOG_S = 0.5
STREAM_HOST, STREAM_PORT = "127.0.0.1", 8080
IFACE = "wlan0"


# ---------------------------------------------------------------- motors ---
class Motors:
    """Stub. Only logs. Replace the two methods with your driver code."""
    def drive(self, direction, speed):
        # TODO(motor driver): set direction pins for "forward"/"backward" and
        # PWM duty = speed (0-100). E.g. gpiozero: Motor(...).forward(speed/100)
        print(f"[motor] {direction} {speed}%", flush=True)

    def stop(self):
        # TODO(motor driver): cut PWM to 0 / brake. Must be safe to call often.
        print("[motor] stop", flush=True)


motors = Motors()
lock = threading.Lock()
state = {"dir": "stop", "speed": 0, "last": 0.0, "owner": None}
cmd_log = collections.deque(maxlen=5)


def log(text):
    cmd_log.appendleft({"t": time.strftime("%H:%M:%S"), "text": text})


def drive(direction, speed, client="", now=None):
    """Apply one command from the browser. Logs only when something changes,
    so the 150 ms heartbeats don't flood the log.
    One driver at a time: while the robot is moving, only the viewer who
    started it (client id) can change or stop it; others get "busy". This
    stops two open pages from fighting (one's release cutting off the other).
    The watchdog still stops the robot 0.5 s after its driver goes quiet."""
    now = time.monotonic() if now is None else now
    speed = max(0, min(100, int(speed)))
    with lock:
        moving = state["dir"] != "stop"
        if moving and state["owner"] not in (None, client):
            return "busy"
        if direction == "stop":
            if moving:
                motors.stop(); log("STOP")
            state["dir"], state["owner"] = "stop", None
            return "ok"
        state["owner"] = client
        state["last"] = now
        if (direction, speed) != (state["dir"], state["speed"]):
            motors.drive(direction, speed)
            log(f"{direction.upper()} {speed}%")
            state["dir"], state["speed"] = direction, speed
    return "ok"


def watchdog_tick(now=None):
    """Stop if moving and no command arrived within WATCHDOG_S."""
    now = time.monotonic() if now is None else now
    with lock:
        if state["dir"] != "stop" and now - state["last"] > WATCHDOG_S:
            motors.stop(); log("STOP (auto-stop)")
            state["dir"], state["owner"] = "stop", None


def watchdog_loop():
    while True:
        watchdog_tick(); time.sleep(0.05)


# ---------------------------------------------------------------- health ---
THROTTLE_BITS = {0: "under-voltage", 1: "freq capped", 2: "throttled", 3: "temp limit"}


def decode_throttled(raw):
    """vcgencmd get_throttled bits 0-3 = happening now, 16-19 = since boot."""
    if raw is None:
        return {"ok": None, "text": "n/a"}
    now = [n for b, n in THROTTLE_BITS.items() if raw >> b & 1]
    past = [n for b, n in THROTTLE_BITS.items() if raw >> (b + 16) & 1]
    if now:
        return {"ok": False, "text": ", ".join(now)}
    return {"ok": True, "text": "OK" + (f" (earlier: {', '.join(past)})" if past else "")}


health = {"temp_c": None, "cpu": None, "throttled": decode_throttled(None),
          "mem_pct": None, "load": None, "uptime_s": None, "disk_pct": None,
          "wifi_dbm": None, "ssid": None, "rx_kbs": None, "tx_kbs": None, "cam_fps": None}


def _read(path):
    with open(path) as f:
        return f.read()


def health_loop():
    prev_cpu = prev_net = prev_seq = None
    tick = 0
    while True:
        t = time.monotonic()
        try:
            v = [int(x) for x in _read("/proc/stat").splitlines()[0].split()[1:]]
            idle, total = v[3] + v[4], sum(v)
            if prev_cpu and total != prev_cpu[1]:
                health["cpu"] = round(100 * (1 - (idle - prev_cpu[0]) / (total - prev_cpu[1])))
            prev_cpu = (idle, total)
        except (OSError, ValueError, IndexError):
            pass
        try:
            health["temp_c"] = round(int(_read("/sys/class/thermal/thermal_zone0/temp")) / 1000, 1)
        except (OSError, ValueError):
            pass
        try:
            m = dict(l.split(":", 1) for l in _read("/proc/meminfo").splitlines())
            tot, avail = int(m["MemTotal"].split()[0]), int(m["MemAvailable"].split()[0])
            health["mem_pct"] = round(100 * (1 - avail / tot))
        except (OSError, KeyError, ValueError):
            pass
        try:
            health["load"] = round(os.getloadavg()[0], 2)
            health["uptime_s"] = int(float(_read("/proc/uptime").split()[0]))
            du = shutil.disk_usage("/")
            health["disk_pct"] = round(100 * du.used / du.total)
        except (OSError, ValueError):
            pass
        try:   # Wi-Fi signal level in dBm
            for l in _read("/proc/net/wireless").splitlines():
                if l.strip().startswith(IFACE + ":"):
                    health["wifi_dbm"] = int(float(l.split()[3].rstrip(".")))
        except (OSError, ValueError, IndexError):
            pass
        try:   # network throughput on the Wi-Fi interface
            for l in _read("/proc/net/dev").splitlines():
                if l.strip().startswith(IFACE + ":"):
                    f = l.split(":", 1)[1].split()
                    rx, tx = int(f[0]), int(f[8])
                    if prev_net:
                        dt = t - prev_net[2]
                        health["rx_kbs"] = round((rx - prev_net[0]) / 1024 / dt)
                        health["tx_kbs"] = round((tx - prev_net[1]) / 1024 / dt)
                    prev_net = (rx, tx, t)
        except (OSError, ValueError, IndexError):
            pass
        if prev_seq:   # frames the Pi camera is actually producing
            health["cam_fps"] = round((hub.seq - prev_seq[0]) / (t - prev_seq[1]), 1)
        prev_seq = (hub.seq, t)
        try:
            out = subprocess.run(["vcgencmd", "get_throttled"], capture_output=True, text=True, timeout=2).stdout
            health["throttled"] = decode_throttled(int(out.strip().split("=")[1], 16))
        except Exception:
            health["throttled"] = decode_throttled(None)
        if tick % 10 == 0:   # network name changes rarely; check every ~20 s
            try:
                out = subprocess.run(["nmcli", "-t", "-f", "ACTIVE,SSID", "dev", "wifi", "list", "--rescan", "no"],
                                     capture_output=True, text=True, timeout=3).stdout
                health["ssid"] = next((l.split(":", 1)[1] for l in out.splitlines() if l.startswith("yes:")), None)
            except Exception:
                pass
        tick += 1
        time.sleep(2)


# ------------------------------------------------------------- tailscale ---
def parse_ping(out):
    """'pong from x (100.x) via DERP(nyc) in 150ms' -> Relay; 'via 1.2.3.4:41641' -> Direct."""
    m = re.search(r"via (\S+) in ([\d.]+)\s*ms", out)
    if not m:
        return None
    via, ms = m.group(1), round(float(m.group(2)))
    kind = "Relay" if via.startswith("DERP") else "Direct"
    return {"type": kind, "latency_ms": ms, "via": via}


net = {}          # viewer tailscale IP -> {"type", "latency_ms", "via"}
net_seen = {}     # viewer IP -> last time it asked for status


def is_tailscale_ip(ip):
    try:
        a, b = (int(x) for x in ip.split(".")[:2])
        return a == 100 and 64 <= b <= 127   # Tailscale CGNAT range (second octet 64-127)
    except ValueError:
        return False


def tailscale_loop():
    """Ping each active Tailscale viewer every ~3 s (off the request path)."""
    while True:
        for ip, seen in list(net_seen.items()):
            if time.monotonic() - seen > 10:
                net_seen.pop(ip, None); net.pop(ip, None); continue
            try:
                out = subprocess.run(["tailscale", "ping", "-c", "1", "--timeout", "2s", ip],
                                     capture_output=True, text=True, timeout=5)
                net[ip] = parse_ping(out.stdout + out.stderr) or {"type": "Unknown", "latency_ms": None, "via": ""}
            except Exception:
                net[ip] = {"type": "Unknown", "latency_ms": None, "via": ""}
        time.sleep(3)


# ---------------------------------------------------------- camera relay ---
class FrameHub:
    """Reads stream.py's MJPEG from localhost; keeps only the newest frame.
    Same rule as stream.py: a viewer that keeps up gets every frame, a
    backed-up one skips to the newest, so lag never piles up."""
    def __init__(self):
        self.frame, self.seq, self.viewers, self.ts = b"", 0, 0, 0.0
        self.cond = threading.Condition()

    def run(self):
        while True:
            try:
                s = socket.create_connection((STREAM_HOST, STREAM_PORT), 5)
                s.settimeout(10)
                s.sendall(b"GET /stream HTTP/1.1\r\nHost: localhost\r\n\r\n")
                f = s.makefile("rb")
                while f.readline() not in (b"\r\n", b""):   # skip HTTP headers
                    pass
                while True:
                    length = None
                    while True:                              # part headers
                        line = f.readline()
                        if not line:
                            raise ConnectionError("stream closed")
                        if line.lower().startswith(b"content-length:"):
                            length = int(line.split(b":")[1])
                        elif line == b"\r\n" and length is not None:
                            break
                    jpg = f.read(length)
                    with self.cond:
                        self.frame, self.seq, self.ts = jpg, self.seq + 1, time.time()
                        self.cond.notify_all()
            except Exception as e:
                print(f"[camera] {e}; retrying", flush=True)
                time.sleep(1)

    def frames(self):
        last = self.seq
        self.viewers += 1
        try:
            while True:
                with self.cond:
                    if not self.cond.wait_for(lambda: self.seq != last, timeout=5):
                        continue
                    f, last, ts = self.frame, self.seq, self.ts
                # X-Ts = when the Pi got this frame; the page uses it to show video delay
                yield (b"--F\r\nContent-Type: image/jpeg\r\nContent-Length: %d\r\nX-Ts: %d\r\n\r\n"
                       % (len(f), ts * 1000) + f + b"\r\n")
        finally:
            self.viewers -= 1


hub = FrameHub()


# ------------------------------------------------------------------- web ---
app = Flask(__name__)


def _low_latency_socket():
    sock = request.environ.get("werkzeug.socket")
    if sock:   # small send buffer so a slow phone drops old frames, not lags
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 32768)
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        except OSError:
            pass


@app.get("/")
def page_phone():
    return Response(PAGES["phone"], mimetype="text/html")


@app.get("/desktop")
def page_desktop():
    return Response(PAGES["desktop"], mimetype="text/html")


@app.get("/cockpit")
def page_cockpit():
    return Response(PAGES["cockpit"], mimetype="text/html")


@app.get("/stream")
def stream():
    """Standard MJPEG for <img> (fallback path)."""
    _low_latency_socket()
    return Response(hub.frames(), mimetype="multipart/x-mixed-replace; boundary=F",
                    headers={"Cache-Control": "no-store"})


@app.get("/stream.bin")
def stream_bin():
    """Same bytes, plain type. Safari won't hand multipart/x-mixed-replace to
    fetch(), so the page reads this one to count frames."""
    _low_latency_socket()
    return Response(hub.frames(), mimetype="application/octet-stream",
                    headers={"Cache-Control": "no-store"})


@app.post("/api/drive")
def api_drive():
    d = request.get_json(silent=True) or {}
    direction = d.get("dir")
    if direction not in ("forward", "backward", "stop"):
        return jsonify(error="dir must be forward/backward/stop"), 400
    try:
        result = drive(direction, d.get("speed", 0), str(d.get("client", ""))[:40])
    except (TypeError, ValueError):
        return jsonify(error="bad speed"), 400
    if result == "busy":
        return jsonify(ok=False, busy=True, log=list(cmd_log)), 409
    return jsonify(ok=True, log=list(cmd_log))


CAMERA_URL = f"http://{STREAM_HOST}:{STREAM_PORT}/controls"


def camera_request(body=None):
    """GET or POST stream.py's /controls. Returns (http status, dict)."""
    req = urllib.request.Request(CAMERA_URL, method="GET" if body is None else "POST",
                                 data=None if body is None else json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read())
        except ValueError:
            return e.code, {"error": str(e)}
    except (OSError, ValueError):
        return 503, {"error": "camera controls unavailable (is the new stream.py installed?)"}


def camera_log_text(body):
    if body.get("reset"):
        return "CAM reset all"
    if body.get("_preset"):
        return f"CAM preset {body['_preset']}"
    parts = [f"{k.replace('_', ' ')} {'on' if v is True else 'off' if v is False else v}"
             for k, v in body.items() if not k.startswith("_")]
    return ("CAM " + ", ".join(parts))[:48]


@app.get("/api/camera")
def api_camera_get():
    code, data = camera_request()
    return jsonify(data), code


@app.post("/api/camera")
def api_camera_post():
    body = request.get_json(silent=True) or {}
    code, data = camera_request({k: v for k, v in body.items() if k not in ("_log", "_preset")})
    if code == 200 and body.get("_log"):
        log(camera_log_text(body))
    data["log"] = list(cmd_log)
    return jsonify(data), code


@app.get("/api/status")
def api_status():
    ip = request.remote_addr or ""
    if is_tailscale_ip(ip):
        net_seen[ip] = time.monotonic()
        n = net.get(ip, {"type": "Checking", "latency_ms": None, "via": ""})
    else:
        n = {"type": "LAN", "latency_ms": None, "via": "local network"}
    c = request.args.get("c", "")
    driver = None if state["dir"] == "stop" else ("you" if state["owner"] == c else "other")
    return jsonify(**health, net=n, log=list(cmd_log), moving=state["dir"],
                   speed=state["speed"], viewers=hub.viewers, driver=driver,
                   server_ms=int(time.time() * 1000))


# ----------------------------------------------------------------- pages ---
# One shared script + tokens; each view is its own markup and layout.
# Elements opt in with data-k="<stat>" / data-spark="<series>" / data-dir.

BASE_CSS = r"""
:root{--bg:#0b0d12;--panel:#151922;--panel2:#1c212c;--line:#262c38;--text:#e9ecf1;
--dim:#98a2b3;--mute:#667085;--accent:#3b82f6;--accent2:#60a5fa;
--ok:#22c55e;--warn:#f59e0b;--bad:#ef4444;--r:14px}
*{box-sizing:border-box;-webkit-tap-highlight-color:transparent}
html,body{margin:0;height:100%;background:var(--bg);color:var(--text);
font:14px/1.35 Inter,system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;-webkit-font-smoothing:antialiased}
button{font:inherit;color:inherit}
.mono{font-family:ui-monospace,"SF Mono",Menlo,Consolas,monospace;font-variant-numeric:tabular-nums}
.num{font-variant-numeric:tabular-nums}
.badge{display:inline-flex;align-items:center;gap:6px;padding:4px 10px;border-radius:999px;
font-weight:600;font-size:12.5px;background:var(--panel2);color:var(--dim);white-space:nowrap}
.badge::before{content:"";width:7px;height:7px;border-radius:50%;background:currentColor}
.badge.direct,.badge.lan{color:var(--ok);background:color-mix(in srgb,var(--ok) 14%,transparent)}
.badge.relay{color:var(--warn);background:color-mix(in srgb,var(--warn) 14%,transparent)}
.badge.offline{color:var(--bad);background:color-mix(in srgb,var(--bad) 14%,transparent)}
.okc{color:var(--ok)}.warnc{color:var(--warn)}.badc{color:var(--bad)}
.cam{position:relative;background:#000;overflow:hidden;display:flex;align-items:center;justify-content:center}
.cam img{max-width:100%;max-height:100%;width:100%;height:100%;object-fit:contain;visibility:hidden}
.cam.ready img{visibility:visible}
.camph{position:absolute;inset:0;display:flex;flex-direction:column;gap:10px;align-items:center;
justify-content:center;color:var(--mute);font-size:13px}
.camph::before{content:"";width:22px;height:22px;border-radius:50%;border:2px solid var(--line);
border-top-color:var(--accent2);animation:spin 1s linear infinite}
.cam.ready .camph{display:none}
@keyframes spin{to{transform:rotate(360deg)}}
.chip{position:absolute;padding:3px 8px;border-radius:8px;background:rgba(0,0,0,.55);
backdrop-filter:blur(6px);-webkit-backdrop-filter:blur(6px);font-size:12px;color:#fff}
.drive{border:1px solid var(--line);border-radius:var(--r);background:var(--panel);
font-weight:650;letter-spacing:.2px;touch-action:none;user-select:none;-webkit-user-select:none;
-webkit-touch-callout:none;cursor:pointer;transition:background .12s,border-color .12s,transform .08s;
display:flex;align-items:center;justify-content:center;gap:10px}
.drive svg{width:22px;height:22px;fill:currentColor}
.drive:hover{border-color:#34405a}
.drive.on{background:var(--accent);border-color:var(--accent);transform:scale(.98);
box-shadow:0 0 0 4px color-mix(in srgb,var(--accent) 25%,transparent)}
.log{list-style:none;margin:0;padding:0;font-size:12px}
.log li{display:flex;gap:10px;padding:3px 0;color:var(--mute);white-space:nowrap;min-width:0}
.log li b{overflow:hidden;text-overflow:ellipsis}
.log li:first-child{color:var(--text)}
.log li .t{color:var(--mute);min-width:62px}
.log li.auto b{color:var(--warn)}
.log b{font-weight:600}
input[type=range]{accent-color:var(--accent)}
.vrange{writing-mode:vertical-lr;direction:rtl;-webkit-appearance:slider-vertical;appearance:slider-vertical;
width:36px;touch-action:none}
.views{display:flex;gap:2px;padding:3px;border-radius:10px;background:var(--panel2)}
.views a{color:var(--dim);text-decoration:none;font-size:12px;padding:4px 10px;border-radius:7px}
.views a.cur{background:var(--line);color:var(--text)}
body.locked [data-dir]{opacity:.4;cursor:not-allowed}
#lockbar{position:fixed;top:54px;left:50%;transform:translateX(-50%);z-index:9;display:none;padding:6px 14px;
border-radius:999px;font-size:12.5px;font-weight:600;color:var(--warn);background:rgba(20,16,8,.85);
border:1px solid color-mix(in srgb,var(--warn) 40%,transparent);white-space:nowrap}
body.locked #lockbar{display:block}#lockbar.flash{animation:flash .5s}
@keyframes flash{50%{transform:translateX(-50%) scale(1.08)}}
.glass{background:rgba(12,14,20,.55);backdrop-filter:blur(12px) saturate(1.2);-webkit-backdrop-filter:blur(12px) saturate(1.2);
border:1px solid rgba(255,255,255,.08);border-radius:var(--r)}
.hbtn{display:inline-flex;align-items:center;gap:7px;border:1px solid var(--line);background:var(--panel2);color:var(--dim);
border-radius:9px;padding:5px 10px;font-size:12.5px;cursor:pointer;white-space:nowrap}
.hbtn:hover{color:var(--text);border-color:#34405a}
body.camopen .hbtn[data-act="cam"]{color:var(--text);border-color:var(--accent)}
.hbtn kbd{padding:0 4px}
.cam.snap::after{content:"";position:absolute;inset:0;background:#fff;pointer-events:none;animation:snap .35s forwards}
@keyframes snap{from{opacity:.75}to{opacity:0}}
/* camera settings panel */
.campanel{position:fixed;z-index:10;width:330px;display:none;flex-direction:column;overflow:auto;padding:12px 16px 14px;
background:rgba(16,19,26,.985);backdrop-filter:blur(14px);-webkit-backdrop-filter:blur(14px);border:1px solid var(--line);
border-radius:var(--r);box-shadow:0 12px 40px rgba(0,0,0,.5);overscroll-behavior:contain;scrollbar-width:thin;scrollbar-color:#2f3643 transparent}
body.camopen .campanel{display:flex}
.cp-hd{display:flex;align-items:center;gap:10px;margin-bottom:8px}
.cp-hd b{font-size:14px}
.cp-st{flex:1;font-size:11.5px;color:var(--ok)}.cp-st.busy{color:var(--warn)}.cp-st.err{color:var(--bad)}
.cp-x{border:0;background:none;color:var(--dim);font-size:20px;line-height:1;cursor:pointer;padding:0 2px}
.cp-presets{display:grid;grid-template-columns:repeat(2,1fr);gap:6px}
.cp-presets button,.seg button,.tog{border:1px solid var(--line);background:var(--panel2);color:var(--dim);border-radius:8px;
padding:6px 4px;font-size:12px;cursor:pointer}
.cp-presets button:hover,.seg button:hover,.tog:hover{color:var(--text);border-color:#34405a}
.seg button.cur,.tog.cur{background:color-mix(in srgb,var(--accent) 22%,transparent);border-color:var(--accent);color:var(--text)}
.cp-sec{display:flex;justify-content:space-between;align-items:baseline;margin:14px 0 2px;font-size:10.5px;
text-transform:uppercase;letter-spacing:.7px;color:var(--mute)}
.cp-sec i{font-style:normal;text-transform:none;letter-spacing:0;font-size:11px}
.cp-row{display:grid;grid-template-columns:1fr auto;align-items:center;gap:3px 8px;padding:4px 0}
.cp-row>span:first-child{font-size:12.5px;color:var(--dim)}
.cp-row output{font-size:12px;font-variant-numeric:tabular-nums}
.cp-row input[type=range]{grid-column:1/3;width:100%;margin:2px 0}
.cp-row .seg{grid-column:1/3;display:flex;gap:4px}.seg button{flex:1;min-width:0}
.cp-row select{background:var(--panel2);color:var(--text);border:1px solid var(--line);border-radius:8px;padding:5px 8px;font:inherit;font-size:12.5px}
.cp-row.off,.tog.off{opacity:.35;pointer-events:none}
.cp-togs{display:flex;gap:6px;padding:6px 0 0}.tog{flex:1}
.cp-foot{display:flex;justify-content:space-between;align-items:center;margin-top:12px;font-size:11.5px;color:var(--mute)}
.cp-foot button{background:none;border:0;color:var(--accent2);cursor:pointer;font-size:12px}
kbd{font:11px ui-monospace,Menlo,monospace;padding:1px 5px;border-radius:5px;border:1px solid var(--line);
background:var(--panel2);color:var(--dim)}
"""

CORE_JS = r"""
const $$=s=>document.querySelectorAll(s);
const put=(k,v,cls)=>$$(`[data-k="${k}"]`).forEach(e=>{e.textContent=v;if(cls!==undefined){e.classList.remove('okc','warnc','badc');if(cls)e.classList.add(cls)}})
const fmtUp=s=>{if(s==null)return'–';const d=Math.floor(s/86400),h=Math.floor(s%86400/3600),m=Math.floor(s%3600/60);return d?`${d}d ${h}h`:h?`${h}h ${m}m`:`${m}m`};
const wifiWord=d=>d==null?'':d>=-55?'Excellent':d>=-67?'Good':d>=-75?'Fair':'Weak';
const speed=document.getElementById('speed');
const hist={fps:[],lat:[],delay:[]};const HMAX=60;
function pushHist(k,v){hist[k].push(v);if(hist[k].length>HMAX)hist[k].shift();drawSpark(k)}

// ---- drive: resend every 150 ms while held; Pi auto-stops after 0.5 s of silence.
// One driver at a time: if another viewer is driving, the Pi answers 409.
const CID=Math.random().toString(36).slice(2,10);
const lockbar=document.createElement('div');lockbar.id='lockbar';lockbar.textContent='Another viewer is driving · controls locked';
document.body.appendChild(lockbar);
function setLocked(v){document.body.classList.toggle('locked',v)}
function busy(){if(held){clearInterval(timer);held=null;mark()}setLocked(true);
 lockbar.classList.remove('flash');void lockbar.offsetWidth;lockbar.classList.add('flash')}
let held=null,timer=null;
function send(dir){fetch('/api/drive',{method:'POST',headers:{'Content-Type':'application/json'},
 body:JSON.stringify({dir,speed:+speed.value,client:CID})}).then(r=>{if(r.status===409)busy();return r.json()})
 .then(d=>d.log&&showLog(d.log)).catch(()=>{})}
function mark(){$$('[data-dir]').forEach(b=>b.classList.toggle('on',b.dataset.dir===held))}
function start(dir){if(held)return;if(document.body.classList.contains('locked'))return busy();
 held=dir;mark();send(dir);timer=setInterval(()=>send(dir),150)}
function stop(){if(!held)return;clearInterval(timer);held=null;mark();send('stop')}
$$('[data-dir]').forEach(b=>{
 b.addEventListener('pointerdown',e=>{e.preventDefault();try{b.setPointerCapture(e.pointerId)}catch(_){}start(b.dataset.dir)});
 ['pointerup','pointercancel','lostpointercapture'].forEach(ev=>b.addEventListener(ev,stop));
 b.addEventListener('contextmenu',e=>e.preventDefault())});
document.addEventListener('visibilitychange',()=>{if(document.hidden)stop()});
window.addEventListener('blur',stop);
function setSpeed(v){speed.value=Math.max(0,Math.min(100,v));put('speedv',speed.value+'%')}
speed.addEventListener('input',()=>setSpeed(+speed.value));
// keyboard: W/↑ forward, S/↓ backward (hold), A/D or ←/→ speed, Space stop
const KEYS={ArrowUp:'forward',w:'forward',W:'forward',ArrowDown:'backward',s:'backward',S:'backward'};
document.addEventListener('keydown',e=>{if(e.target.tagName==='SELECT')return;
 if(e.target.tagName==='INPUT'&&e.key.startsWith('Arrow'))e.preventDefault();
 if(!e.repeat&&!e.ctrlKey&&!e.metaKey&&!e.altKey&&ACT_KEYS[e.key.toLowerCase()]){e.preventDefault();return ACTIONS[ACT_KEYS[e.key.toLowerCase()]]()}
 if(KEYS[e.key]){e.preventDefault();if(!e.repeat)start(KEYS[e.key])}
 else if(['ArrowRight','d','D'].includes(e.key)){e.preventDefault();setSpeed(+speed.value+10)}
 else if(['ArrowLeft','a','A'].includes(e.key)){e.preventDefault();setSpeed(+speed.value-10)}
 else if(e.key===' '){e.preventDefault();stop()}});
document.addEventListener('keyup',e=>{if(KEYS[e.key]&&held===KEYS[e.key])stop()});

function showLog(items){$$('[data-k="log"]').forEach(el=>{const n=+(el.dataset.n||5);
 el.innerHTML=items.slice(0,n).map(i=>`<li class="${/auto/.test(i.text)?'auto':''}"><span class="t mono">${i.t}</span><b>${i.text}</b></li>`).join('')||'<li><span class="t">—</span>No commands yet</li>'})}

// ---- status, every 2 s
// clock offset to the Pi (for video delay): keep the estimate from the fastest round trip
const clk=[];let offset=null;
const bars=d=>d==null?0:d>=-55?4:d>=-67?3:d>=-75?2:1;
async function status(){
 try{const t0=Date.now(),d=await (await fetch('/api/status?c='+CID,{cache:'no-store'})).json(),t1=Date.now();
  if(d.server_ms){clk.push({rtt:t1-t0,off:d.server_ms-(t0+t1)/2});if(clk.length>15)clk.shift();
   offset=clk.reduce((a,b)=>b.rtt<a.rtt?b:a).off}
  const nb=bars(d.wifi_dbm);$$('[data-k="wifibars"]').forEach(e=>{e.innerHTML=[1,2,3,4].map(i=>`<i class="${i<=nb?'on':''}"></i>`).join('');e.title=wifiWord(d.wifi_dbm)});
  put('driver',d.driver==='you'?'You':d.driver==='other'?'Other viewer':'Free',d.driver==='other'?'warnc':d.driver==='you'?'okc':'');
  put('temp',d.temp_c==null?'–':d.temp_c.toFixed(1)+'°C',d.temp_c>=80?'badc':d.temp_c>=70?'warnc':'');
  put('thr',d.throttled.text,d.throttled.ok===false?'badc':d.throttled.ok?'okc':'');
  put('cpu',d.cpu==null?'–':d.cpu+'%',d.cpu>=85?'warnc':'');
  put('mem',d.mem_pct==null?'–':d.mem_pct+'%');
  put('load',d.load==null?'–':d.load.toFixed(2));
  put('uptime',fmtUp(d.uptime_s));
  put('disk',d.disk_pct==null?'–':d.disk_pct+'%');
  put('wifi',d.wifi_dbm==null?'–':d.wifi_dbm+' dBm');
  put('wifiq',wifiWord(d.wifi_dbm)+(d.ssid?' · '+d.ssid:''));
  put('rx',d.rx_kbs==null?'–':d.rx_kbs+' KB/s');put('tx',d.tx_kbs==null?'–':d.tx_kbs+' KB/s');
  put('camfps',d.cam_fps==null?'–':d.cam_fps+' fps');
  put('viewers',d.viewers);
  put('motion',d.moving==='stop'?'Stopped':`${d.moving[0].toUpperCase()+d.moving.slice(1)} ${d.speed}%`,d.moving==='stop'?'':'okc');
  const n=d.net,t=n.type,ms=n.latency_ms;
  $$('[data-k="net"]').forEach(el=>{el.className='badge '+t.toLowerCase();
   el.textContent=t==='LAN'?'Direct · LAN':t+(ms!=null?` · ${ms} ms`:'');el.title=n.via||''});
  put('lat',ms==null?'–':ms+' ms');put('via',n.via||'');
  if(ms!=null)pushHist('lat',ms);
  setLocked(d.driver==='other');
  showLog(d.log);
 }catch(_){$$('[data-k="net"]').forEach(el=>{el.className='badge offline';el.textContent='Offline'})}}
status();setInterval(status,2000);

// ---- sparklines: one series each, 2px line, hover shows the value
function drawSpark(k){$$(`[data-spark="${k}"]`).forEach(svg=>{
 const a=hist[k],W=200,H=48,unit=svg.dataset.unit||'';
 const mx=Math.max(+svg.dataset.min||1,...a)*1.15;
 const pts=a.map((v,i)=>[(i+HMAX-a.length)/(HMAX-1)*W,H-2-v/mx*(H-6)]);
 svg.setAttribute('viewBox',`0 0 ${W} ${H}`);svg.setAttribute('preserveAspectRatio','none');
 svg.innerHTML=`<line x1="0" x2="${W}" y1="${H-1}" y2="${H-1}" stroke="var(--line)" stroke-width="1" vector-effect="non-scaling-stroke"/>`+
  (pts.length>1?`<path d="M${pts.map(p=>p.map(n=>n.toFixed(1)).join(',')).join('L')}L${W},${H}L${pts[0][0].toFixed(1)},${H}Z" fill="var(--accent)" opacity=".12"/>
  <polyline points="${pts.map(p=>p.join(',')).join(' ')}" fill="none" stroke="var(--accent2)" stroke-width="2" stroke-linejoin="round" stroke-linecap="round" vector-effect="non-scaling-stroke"/>`:'');
 svg.onpointermove=e=>{const r=svg.getBoundingClientRect(),x=(e.clientX-r.left)/r.width;
  const i=Math.round(x*(HMAX-1))-(HMAX-a.length);const tip=svg.parentElement.querySelector('.tip');
  if(i<0||i>=a.length||!tip){if(tip)tip.hidden=true;return}
  const ago=(a.length-1-i)*(+svg.dataset.step||1);tip.hidden=false;
  tip.textContent=`${a[i]} ${unit} · ${ago?ago+' s ago':'now'}`;tip.style.left=Math.min(Math.max(x*100,12),88)+'%'};
 svg.onpointerleave=()=>{const tip=svg.parentElement.querySelector('.tip');if(tip)tip.hidden=true}})}

// ---- camera: read the stream with fetch so we can count frames;
// fall back to a plain <img> stream if this browser can't.
const cam=document.getElementById('cam'),camBox=cam.closest('.cam');
let frames=0,bytes=0,lastUrl=null,lastBlob=null,got=false,mode='fetch',lastFrameAt=0,delays=[];
function camReady(){camBox.classList.add('ready')}
setInterval(()=>{
 if(mode==='fetch'){put('fps',frames+' fps');put('kbs',Math.round(bytes/1024)+' KB/s');
  put('fsize',frames?Math.round(bytes/frames/1024)+' KB':'–');if(got)pushHist('fps',frames);
  if(delays.length){const m=delays.sort((a,b)=>a-b)[delays.length>>1];put('delay',m+' ms',m>=500?'badc':m>=250?'warnc':'');pushHist('delay',m)}
  else put('delay','–');delays=[]}
 else put('fps','live');
 if(got&&mode==='fetch'&&performance.now()-lastFrameAt>3000){camBox.classList.remove('ready');
  $$('.camph').forEach(e=>e.textContent='Reconnecting camera…')}
 frames=0;bytes=0},1000);
function showFrame(u8,ts){lastBlob=new Blob([u8],{type:'image/jpeg'});const url=URL.createObjectURL(lastBlob);
 if(ts&&offset!=null)delays.push(Math.max(0,Math.round(Date.now()+offset-ts)));
 cam.src=url;if(lastUrl)URL.revokeObjectURL(lastUrl);lastUrl=url;frames++;bytes+=u8.length;
 lastFrameAt=performance.now();if(!got){got=true}camReady()}
function hdrEnd(b){for(let i=0;i<b.length-3;i++)if(b[i]===13&&b[i+1]===10&&b[i+2]===13&&b[i+3]===10)return i;return -1}
function fallback(){mode='img';cam.onload=camReady;cam.src='/stream'}
// Background tabs drop their video connection (saves Pi upload and the
// browser's ~6-connections-per-site limit) and reconnect when shown again.
let curAC=null;
document.addEventListener('visibilitychange',()=>{
 if(mode==='img'){cam.src=document.hidden?'':'/stream';return}
 if(document.hidden&&curAC)curAC.abort()});
async function camLoop(){
 if(!window.ReadableStream||!window.fetch||!window.AbortController)return fallback();
 let everFrame=false;
 for(;;){
  while(document.hidden)await new Promise(r=>setTimeout(r,500));
  const ac=new AbortController();curAC=ac;let gotHere=false;
  const guard=setTimeout(()=>{if(!gotHere)ac.abort()},4000);
  try{
   const rd=(await fetch('/stream.bin',{cache:'no-store',signal:ac.signal})).body.getReader();
   let buf=new Uint8Array(0),need=-1,start=0,fts=0;
   for(;;){
    const {value,done}=await rd.read();if(done)break;
    const nb=new Uint8Array(buf.length+value.length);nb.set(buf);nb.set(value,buf.length);buf=nb;
    for(;;){
     if(need<0){const h=hdrEnd(buf);if(h<0)break;
      let txt='';for(let i=0;i<h;i++)txt+=String.fromCharCode(buf[i]);
      const m=/content-length:\s*(\d+)/i.exec(txt),x=/x-ts:\s*(\d+)/i.exec(txt);
      if(!m){buf=buf.subarray(h+4);continue}need=+m[1];start=h+4;fts=x?+x[1]:0}
     if(buf.length<start+need)break;
     showFrame(buf.slice(start,start+need),fts);gotHere=everFrame=true;
     buf=buf.slice(start+need);need=-1}}
  }catch(_){}
  clearTimeout(guard);
  if(!everFrame&&!document.hidden)return fallback();   // this browser can't stream via fetch
  await new Promise(r=>setTimeout(r,1000))}}
camLoop();

// ---- actions: snapshot (P), HUD (H), fullscreen (F), camera panel (C)
function snapshot(){
 const name='robot-'+new Date().toISOString().slice(0,19).replace(/[:T]/g,'-')+'.jpg';
 const save=b=>{const u=URL.createObjectURL(b),a=document.createElement('a');a.href=u;a.download=name;
  document.body.appendChild(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(u),5000)};
 if(mode==='fetch'&&lastBlob)save(lastBlob);
 else if(cam.naturalWidth){const c=document.createElement('canvas');c.width=cam.naturalWidth;c.height=cam.naturalHeight;
  c.getContext('2d').drawImage(cam,0,0);c.toBlob(b=>b&&save(b),'image/jpeg',.92)}
 else return;
 camBox.classList.remove('snap');void camBox.offsetWidth;camBox.classList.add('snap')}
function toggleFull(){const d=document,el=d.documentElement;
 if(d.fullscreenElement||d.webkitFullscreenElement)(d.exitFullscreen||d.webkitExitFullscreen).call(d);
 else(el.requestFullscreen||el.webkitRequestFullscreen||(()=>{})).call(el)}
const ACTIONS={snap:snapshot,full:toggleFull,hud:()=>document.body.classList.toggle('hudoff'),cam:()=>toggleCam()};
const ACT_KEYS={p:'snap',f:'full',h:'hud',c:'cam'};
$$('[data-act]').forEach(b=>b.addEventListener('click',()=>ACTIONS[b.dataset.act]()));
const t0Session=Date.now();
setInterval(()=>{const s=Math.floor((Date.now()-t0Session)/1000);
 put('session',`${Math.floor(s/3600)}:${String(Math.floor(s%3600/60)).padStart(2,'0')}:${String(s%60).padStart(2,'0')}`)},1000);

// ---- camera settings panel (desktop + cockpit). Picture settings apply
// instantly; resolution / quality / rotate / mirror restart the camera (~1 s).
const cp=document.getElementById('campanel');
const CAMSPEC=[
 {sec:'Picture',note:'applies instantly'},
 {k:'brightness',l:'Brightness',t:'range',min:-1,max:1,step:.05,f:v=>(v>0?'+':'')+(+v).toFixed(2)},
 {k:'contrast',l:'Contrast',t:'range',min:0,max:2,step:.05,f:v=>(+v).toFixed(2)+'×'},
 {k:'saturation',l:'Saturation',t:'range',min:0,max:2,step:.05,f:v=>+v===0?'B&W':(+v).toFixed(2)+'×'},
 {k:'sharpness',l:'Sharpness',t:'range',min:0,max:4,step:.1,f:v=>(+v).toFixed(1)},
 {k:'ev',l:'Exposure',t:'range',min:-3,max:3,step:.25,f:v=>(v>0?'+':'')+(+v)+' EV'},
 {k:'exposure_mode',l:'Shutter',t:'seg',o:['Normal','Short','Long'],n:{Short:'Fast',Long:'Slow'},tip:'Fast = less motion blur · Slow = brighter in low light'},
 {k:'awb',l:'White balance',t:'sel',o:['Auto','Daylight','Cloudy','Indoor','Fluorescent','Tungsten','Incandescent']},
 {k:'denoise',l:'Noise reduction',t:'seg',o:['Off','Fast','HighQuality'],n:{HighQuality:'High'}},
 {k:'fps',l:'Frame rate',t:'seg',o:[10,12,15,24,30]},
 {sec:'Stream',note:'restarts camera ~1 s'},
 {k:'resolution',l:'Resolution',t:'seg',o:['640x480','1280x720','1920x1080'],n:{'640x480':'480p','1280x720':'720p','1920x1080':'1080p'}},
 {k:'quality',l:'JPEG quality',t:'range',min:30,max:90,step:5,f:v=>v,slow:1},
 {togs:[['rotate','Rotate 180°'],['mirror','Mirror']]},
];
const PRESETS={
 'Auto':{brightness:0,contrast:1,saturation:1,sharpness:1,ev:0,awb:'Auto',exposure_mode:'Normal',denoise:'Fast',fps:12},
 'Low light':{ev:1,exposure_mode:'Long',denoise:'HighQuality',fps:10,brightness:.05},
 'Outdoor':{awb:'Daylight',ev:-.5,exposure_mode:'Short',denoise:'Fast'},
 'Fast motion':{exposure_mode:'Short',fps:24,sharpness:1.5,denoise:'Fast'},
};
let camS=null,camSup=new Set(),camBusy=0;
function camStatus(t,cls){const e=document.getElementById('camst');if(e){e.textContent=t;e.className='cp-st '+(cls||'')}}
function camSummary(){if(!camS)return;const r={'640x480':'480p','1280x720':'720p','1920x1080':'1080p'}[camS.resolution]||camS.resolution;
 put('camsum',`${r} · ${camS.fps} fps · q${camS.quality}${camS.rotate?' · 180°':''}${camS.mirror?' · mirror':''}`)}
function camRender(){if(!cp||!camS)return;
 for(const s of CAMSPEC){if(s.togs){s.togs.forEach(([k])=>{const b=cp.querySelector(`[data-ck="${k}"]`);
   b.classList.toggle('cur',!!camS[k]);b.classList.toggle('off',!camSup.has(k))});continue}
  if(!s.k)continue;const row=cp.querySelector(`[data-ck="${s.k}"]`),v=camS[s.k];row.classList.toggle('off',!camSup.has(s.k));
  if(s.t==='range'){const i=row.querySelector('input');if(document.activeElement!==i)i.value=v;row.querySelector('output').textContent=s.f(v)}
  else if(s.t==='seg')row.querySelectorAll('button').forEach(b=>b.classList.toggle('cur',String(b.dataset.v)===String(v)));
  else if(s.t==='sel')row.querySelector('select').value=v}
 camSummary()}
async function camPost(changes,logIt){
 const restart=['resolution','quality','rotate','mirror','reset'].some(k=>k in changes);
 camBusy++;camStatus(restart?'Restarting camera…':'Applying…','busy');
 try{const r=await fetch('/api/camera',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({...changes,_log:logIt})});
  const d=await r.json();if(d.settings){camS=d.settings;camSup=new Set(d.supported||[])}
  if(d.log)showLog(d.log);camRender();
  camBusy--;if(!camBusy)camStatus(d.error?d.error:'Live',d.error?'err':'')}
 catch(_){camBusy--;camStatus('Pi not reachable','err')}}
async function camLoad(){try{const r=await fetch('/api/camera',{cache:'no-store'}),d=await r.json();
 if(d.settings){camS=d.settings;camSup=new Set(d.supported||[]);camRender();camStatus('Live')}
 else camStatus(d.error||'Camera controls unavailable','err')}catch(_){camStatus('Pi not reachable','err')}}
function toggleCam(){if(!cp)return;document.body.classList.toggle('camopen');if(document.body.classList.contains('camopen'))camLoad()}
if(cp){
 let h=`<div class="cp-hd"><b>Camera</b><span class="cp-st" id="camst">Loading…</span><button class="cp-x" data-act="cam" aria-label="Close">×</button></div>
 <div class="cp-presets">${Object.keys(PRESETS).map(p=>`<button data-preset="${p}">${p}</button>`).join('')}</div>`;
 for(const s of CAMSPEC){
  if(s.sec)h+=`<div class="cp-sec"><span>${s.sec}</span><i>${s.note}</i></div>`;
  else if(s.togs)h+=`<div class="cp-togs">${s.togs.map(([k,l])=>`<button class="tog" data-ck="${k}">${l}</button>`).join('')}</div>`;
  else if(s.t==='range')h+=`<label class="cp-row" data-ck="${s.k}"><span>${s.l}</span><output></output><input type="range" min="${s.min}" max="${s.max}" step="${s.step}"></label>`;
  else if(s.t==='seg')h+=`<div class="cp-row" data-ck="${s.k}" title="${s.tip||''}"><span>${s.l}</span><span></span><div class="seg">${s.o.map(o=>`<button data-v="${o}">${(s.n&&s.n[o])||o}</button>`).join('')}</div></div>`;
  else if(s.t==='sel')h+=`<label class="cp-row" data-ck="${s.k}"><span>${s.l}</span><select>${s.o.map(o=>`<option>${o}</option>`).join('')}</select></label>`}
 h+=`<div class="cp-foot"><span>Saved on the Pi · <kbd>C</kbd> to close</span><button data-reset>Reset all</button></div>`;
 cp.innerHTML=h;
 cp.querySelector('.cp-x').addEventListener('click',()=>toggleCam());
 for(const s of CAMSPEC){
  if(s.togs){s.togs.forEach(([k])=>cp.querySelector(`[data-ck="${k}"]`).addEventListener('click',()=>camS&&camPost({[k]:!camS[k]},true)));continue}
  if(!s.k)continue;const row=cp.querySelector(`[data-ck="${s.k}"]`);
  if(s.t==='range'){const i=row.querySelector('input'),o=row.querySelector('output');let tmr=null;
   i.addEventListener('input',()=>{o.textContent=s.f(i.value);if(s.slow)return;clearTimeout(tmr);tmr=setTimeout(()=>camPost({[s.k]:+i.value},false),120)});
   i.addEventListener('change',()=>{clearTimeout(tmr);camPost({[s.k]:+i.value},true)})}
  else if(s.t==='seg')row.querySelectorAll('button').forEach(b=>b.addEventListener('click',()=>
   camPost({[s.k]:typeof s.o[0]==='number'?+b.dataset.v:b.dataset.v},true)));
  else if(s.t==='sel')row.querySelector('select').addEventListener('change',e=>camPost({[s.k]:e.target.value},true))}
 cp.querySelectorAll('[data-preset]').forEach(b=>b.addEventListener('click',()=>camPost({...PRESETS[b.dataset.preset],_preset:b.dataset.preset},true)));
 cp.querySelector('[data-reset]').addEventListener('click',()=>camPost({reset:true},true));
 camLoad();   // fills the camera summary even while the panel is closed
}
"""

VIEWS_NAV = r"""<nav class="views">
<a href="/" data-v="phone">Phone</a><a href="/desktop" data-v="desktop">Desktop</a><a href="/cockpit" data-v="cockpit">Cockpit</a></nav>"""

ICON_UP = '<svg viewBox="0 0 24 24"><path d="M12 5l8 11H4z"/></svg>'
ICON_DN = '<svg viewBox="0 0 24 24"><path d="M12 19L4 8h16z"/></svg>'

# ---- phone (landscape): big thumb targets, nothing extra
PHONE_CSS = r"""
body{overflow:hidden}
.top{display:flex;align-items:center;gap:16px;height:44px;padding:0 max(14px,env(safe-area-inset-right)) 0 max(14px,env(safe-area-inset-left));
border-bottom:1px solid var(--line);background:var(--panel);white-space:nowrap;overflow-x:auto}
.stat{font-size:13px;color:var(--mute)}.stat b{color:var(--text);font-weight:600;margin-left:5px}
.top .sp{flex:1}
.top .views{display:none}
main{display:grid;grid-template-columns:minmax(150px,23%) 1fr 84px;gap:10px;
padding:10px max(10px,env(safe-area-inset-right)) max(10px,env(safe-area-inset-bottom)) max(10px,env(safe-area-inset-left));height:calc(100dvh - 44px)}
.left{display:flex;flex-direction:column;gap:10px;min-height:0}
.left .drive{flex:1;min-height:64px;font-size:19px}
.logbox{background:var(--panel);border:1px solid var(--line);border-radius:var(--r);padding:8px 10px}
.logbox .log{font-size:11.5px}.logbox .log li{gap:8px}.logbox .log li .t{min-width:54px}
.cam{border-radius:var(--r);border:1px solid var(--line)}
.cam .chip{top:10px;left:10px}
.right{display:flex;flex-direction:column;align-items:center;gap:6px;padding:12px 0;
background:var(--panel);border:1px solid var(--line);border-radius:var(--r);min-height:0}
.right .lbl{font-size:11px;color:var(--mute);text-transform:uppercase;letter-spacing:.6px}
.right .vrange{flex:1}
.right .v{font-size:20px;font-weight:700}
@media (min-width:900px){.top .views{display:flex}}
@media (orientation:portrait){
 main{grid-template-columns:1fr 84px;grid-template-rows:auto 1fr}
 .cam{grid-column:1/3;aspect-ratio:4/3}}
"""
PHONE_BODY = r"""
<header class="top">
 <span class="stat">Temp<b data-k="temp">–</b></span>
 <span class="stat">Power<b data-k="thr">–</b></span>
 <span class="stat">CPU<b data-k="cpu">–</b></span>
 <span class="badge" data-k="net">Checking</span>
 <span class="sp"></span>""" + VIEWS_NAV + r"""
</header>
<main>
 <section class="left">
  <button class="drive" data-dir="forward">""" + ICON_UP + r"""Forward</button>
  <button class="drive" data-dir="backward">""" + ICON_DN + r"""Backward</button>
  <div class="logbox"><ol class="log" data-k="log" data-n="3"></ol></div>
 </section>
 <section class="cam"><img id="cam" alt=""><div class="camph">Connecting camera…</div>
  <span class="chip mono" data-k="fps">– fps</span></section>
 <section class="right">
  <span class="lbl">Speed</span>
  <input type="range" class="vrange" id="speed" min="0" max="100" value="50" orient="vertical" aria-label="Speed">
  <span class="v num" data-k="speedv">50%</span>
 </section>
</main>"""

# ---- desktop: camera + drive on the left, full stats + trends on the right
DESKTOP_CSS = r"""
body{min-height:100%}
.top{display:flex;align-items:center;gap:14px;padding:12px 24px;border-bottom:1px solid var(--line)}
.top h1{font-size:16px;margin:0;font-weight:650;letter-spacing:.2px}
.top .sp{flex:1}
.wrap{display:grid;grid-template-columns:minmax(0,1fr) 380px;gap:20px;padding:16px 24px;max-width:1600px;margin:0 auto}
.card{background:var(--panel);border:1px solid var(--line);border-radius:var(--r)}
.cam{aspect-ratio:4/3;border-radius:var(--r);border:1px solid var(--line);max-height:calc(100vh - 250px);width:100%}
.cam .chip{top:12px}.cam .c1{left:12px}.cam .c2{right:12px}
.ctl{display:grid;grid-template-columns:1fr 1fr 1.3fr;gap:14px;margin-top:14px;align-items:stretch}
.ctl .drive{height:74px;font-size:17px}
.spd{padding:12px 16px;display:flex;flex-direction:column;justify-content:center;gap:8px}
.spd .row{display:flex;justify-content:space-between;align-items:baseline;color:var(--mute);font-size:12px}
.spd .row b{color:var(--text);font-size:20px}
.spd input{width:100%}
.hint{margin-top:10px;color:var(--mute);font-size:12px;display:flex;gap:14px;flex-wrap:wrap}
.side{display:flex;flex-direction:column;gap:12px;min-width:0}
.h{font-size:11px;color:var(--mute);text-transform:uppercase;letter-spacing:.7px;margin:0 0 10px}
.grid{display:grid;grid-template-columns:1fr 1fr;gap:1px;background:var(--line);border-radius:var(--r);overflow:hidden;border:1px solid var(--line)}
.tile{background:var(--panel);padding:10px 14px;min-width:0}
.tile .k{font-size:11.5px;color:var(--mute)}
.tile .v{font-size:19px;font-weight:650;margin-top:2px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.tile .s{font-size:11.5px;color:var(--dim);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.trend{padding:10px 14px;position:relative}
.trend .hd{display:flex;justify-content:space-between;align-items:baseline}
.trend .hd b{font-size:19px}
.trend svg{display:block;width:100%;height:36px;margin-top:4px;cursor:crosshair}
.tip{position:absolute;top:6px;transform:translateX(-50%);background:var(--panel2);border:1px solid var(--line);
padding:2px 7px;border-radius:6px;font-size:11.5px;pointer-events:none;white-space:nowrap}
.logc{padding:10px 14px}
.campanel{top:62px;right:24px;bottom:16px}
@media (max-width:1050px){.wrap{grid-template-columns:1fr}.cam{max-height:none}}
"""
DESKTOP_BODY = r"""
<header class="top">
 <h1>Robot Dashboard</h1><span class="badge" data-k="net">Checking</span>
 <span class="sp"></span>
 <button class="hbtn" data-act="cam">Camera <kbd>C</kbd></button>
 <button class="hbtn" data-act="snap">Snapshot <kbd>P</kbd></button>""" + VIEWS_NAV + r"""
</header>
<aside class="campanel" id="campanel"></aside>
<div class="wrap">
 <section>
  <div class="cam"><img id="cam" alt=""><div class="camph">Connecting camera…</div>
   <span class="chip mono c1" data-k="fps">– fps</span><span class="chip mono c2" data-k="motion">Stopped</span></div>
  <div class="ctl">
   <button class="drive" data-dir="forward">""" + ICON_UP + r"""Forward</button>
   <button class="drive" data-dir="backward">""" + ICON_DN + r"""Backward</button>
   <div class="card spd"><div class="row"><span>Speed</span><b class="num" data-k="speedv">50%</b></div>
    <input type="range" id="speed" min="0" max="100" value="50" aria-label="Speed"></div>
  </div>
  <div class="hint"><span><kbd>W</kbd>/<kbd>↑</kbd> forward</span><span><kbd>S</kbd>/<kbd>↓</kbd> backward</span>
   <span><kbd>A</kbd><kbd>D</kbd> speed −/+ 10</span><span><kbd>Space</kbd> stop</span><span>Hold to drive · auto-stops after 0.5 s</span></div>
 </section>
 <aside class="side">
  <div>
   <p class="h">System</p>
   <div class="grid">
    <div class="tile"><div class="k">CPU temp</div><div class="v num" data-k="temp">–</div><div class="s" data-k="thr">–</div></div>
    <div class="tile"><div class="k">CPU</div><div class="v num" data-k="cpu">–</div><div class="s">load <span data-k="load">–</span></div></div>
    <div class="tile"><div class="k">Memory</div><div class="v num" data-k="mem">–</div><div class="s">disk <span data-k="disk">–</span></div></div>
    <div class="tile"><div class="k">Uptime</div><div class="v num" data-k="uptime">–</div><div class="s"><span data-k="viewers">0</span> viewer(s)</div></div>
    <div class="tile"><div class="k">Wi-Fi signal</div><div class="v num" data-k="wifi">–</div><div class="s" data-k="wifiq">–</div></div>
    <div class="tile"><div class="k">Pi upload / download</div><div class="v num" data-k="tx">–</div><div class="s">down <span data-k="rx">–</span></div></div>
   </div>
  </div>
  <div>
   <p class="h">Stream</p>
   <div class="grid">
    <div class="tile"><div class="k">Camera source</div><div class="v num" data-k="camfps">–</div><div class="s">produced on the Pi</div></div>
    <div class="tile"><div class="k">You receive</div><div class="v num" data-k="kbs">–</div><div class="s"><span data-k="fsize">–</span> per frame</div></div>
    <div class="tile"><div class="k">Video delay</div><div class="v num" data-k="delay">–</div><div class="s">Pi → your screen</div></div>
    <div class="tile"><div class="k">Camera</div><div class="v" style="font-size:14px" data-k="camsum">–</div><div class="s">Control: <span data-k="driver">–</span></div></div>
   </div>
  </div>
  <div class="card trend"><div class="hd"><span class="h" style="margin:0">Your FPS · last 60 s</span><b class="num" data-k="fps">–</b></div>
   <svg data-spark="fps" data-unit="fps" data-min="15" data-step="1" aria-label="Frames per second, last 60 seconds"></svg><span class="tip" hidden></span></div>
  <div class="card trend"><div class="hd"><span class="h" style="margin:0">Tailscale latency · last 2 min</span><b class="num" data-k="lat">–</b></div>
   <svg data-spark="lat" data-unit="ms" data-min="20" data-step="2" aria-label="Tailscale ping latency, last 2 minutes"></svg><span class="tip" hidden></span>
   <div class="s mono" style="color:var(--mute);font-size:11px;margin-top:4px" data-k="via"></div></div>
  <div class="card logc"><p class="h">Commands</p><ol class="log" data-k="log" data-n="3"></ol></div>
 </aside>
</div>"""

# ---- cockpit: full-bleed video, glass HUD on top
COCKPIT_CSS = r"""
body{overflow:hidden;background:#000}
.cam{position:fixed;inset:0}
.hud-top{position:fixed;top:16px;left:16px;right:16px;display:flex;gap:10px;align-items:center;pointer-events:none}
.hud-top>*{pointer-events:auto}
.pill{display:flex;gap:18px;padding:8px 14px;font-size:13px;color:var(--dim);white-space:nowrap}
.pill b{color:var(--text);font-weight:600;margin-left:5px}
.hud-top .sp{flex:1}
.hud-top .views{background:rgba(12,14,20,.55)}
.hud-top .badge{padding:8px 12px;background:rgba(12,14,20,.7);backdrop-filter:blur(12px);-webkit-backdrop-filter:blur(12px);border:1px solid rgba(255,255,255,.08)}
.tools{display:flex;gap:6px;padding:4px}
.tools .hbtn{background:transparent;border-color:transparent;padding:4px 8px}
.tools .hbtn:hover{background:rgba(255,255,255,.06)}
.tele{position:fixed;left:16px;top:76px;width:232px;padding:10px 14px;font-size:12.5px}
.tele .row{display:flex;align-items:center;gap:8px;padding:3.5px 0;color:var(--dim)}
.tele .row>span:first-child{flex:1}
.tele .row b{color:var(--text);font-weight:600;font-variant-numeric:tabular-nums;white-space:nowrap}
.tele .mini{width:54px;height:16px;flex:none}
.tele hr{border:0;border-top:1px solid rgba(255,255,255,.08);margin:5px 0}
.wbars{display:inline-flex;gap:2px;align-items:flex-end;height:12px}
.wbars i{width:3px;background:rgba(255,255,255,.18);border-radius:1px}
.wbars i:nth-child(1){height:4px}.wbars i:nth-child(2){height:7px}.wbars i:nth-child(3){height:10px}.wbars i:nth-child(4){height:12px}
.wbars i.on{background:var(--text)}
.pad{position:fixed;left:16px;bottom:16px;display:flex;flex-direction:column;gap:10px;padding:10px;width:232px}
.pad .drive{height:74px;font-size:17px;background:rgba(255,255,255,.06);border-color:rgba(255,255,255,.1)}
.pad .drive.on{background:var(--accent)}
.spd{position:fixed;right:16px;bottom:16px;top:76px;max-height:360px;width:76px;display:flex;flex-direction:column;
align-items:center;gap:6px;padding:12px 0}
.spd .lbl{font-size:10.5px;color:var(--dim);text-transform:uppercase;letter-spacing:.6px}
.spd .vrange{flex:1}.spd .v{font-size:18px;font-weight:700}
.feed{position:fixed;right:108px;bottom:16px;width:260px;padding:10px 14px}
.feed .log li{color:var(--dim)}
.keys{position:fixed;left:50%;bottom:18px;transform:translateX(-50%);padding:6px 12px;font-size:11.5px;color:var(--dim);
display:flex;gap:12px;white-space:nowrap}
.campanel{right:108px;top:76px;bottom:16px}
body.camopen .feed{display:none}
body.hudoff .hud{opacity:0;pointer-events:none}
.hud{transition:opacity .2s}
@media (max-width:1100px){.keys{display:none}}
@media (max-height:760px){.tele .extra{display:none}}
@media (max-height:600px),(max-width:800px){.tele{display:none}}
@media (max-width:700px){.feed{display:none}}
"""
COCKPIT_BODY = r"""
<section class="cam"><img id="cam" alt=""><div class="camph">Connecting camera…</div></section>
<div class="hud-top">
 <div class="glass pill hud">
  <span><b class="mono" data-k="fps" style="margin:0">–</b></span><span>Temp<b data-k="temp">–</b></span>
  <span>CPU<b data-k="cpu">–</b></span><span>Power<b data-k="thr">–</b></span></div>
 <span class="badge hud" data-k="net">Checking</span>
 <span class="glass pill hud"><span>Motor<b data-k="motion">Stopped</b></span></span>
 <span class="sp"></span>
 <div class="glass tools">
  <button class="hbtn" data-act="cam" title="Camera settings">Camera <kbd>C</kbd></button>
  <button class="hbtn" data-act="snap" title="Save a snapshot">Snapshot <kbd>P</kbd></button>
  <button class="hbtn" data-act="hud" title="Hide or show the overlay">HUD <kbd>H</kbd></button>
  <button class="hbtn" data-act="full" title="Fullscreen">Full <kbd>F</kbd></button>
 </div>""" + VIEWS_NAV.replace('class="views"', 'class="views hud"') + r"""
</div>
<div class="glass tele hud">
 <div class="row"><span title="How old each frame is when it reaches you (Pi to screen)">Video delay</span><svg class="mini" data-spark="delay" data-min="150"></svg><b data-k="delay">–</b></div>
 <div class="row"><span>Tailscale ping</span><svg class="mini" data-spark="lat" data-min="20"></svg><b data-k="lat">–</b></div>
 <div class="row"><span>Frame rate</span><svg class="mini" data-spark="fps" data-min="15"></svg><b data-k="fps">–</b></div>
 <hr>
 <div class="row"><span>Receiving</span><b data-k="kbs">–</b></div>
 <div class="row"><span>Pi upload</span><b data-k="tx">–</b></div>
 <div class="row"><span>Wi-Fi</span><span class="wbars" data-k="wifibars"></span><b data-k="wifi">–</b></div>
 <hr>
 <div class="row"><span>Control</span><b data-k="driver">–</b></div>
 <div class="row"><span>Camera</span><b data-k="camsum">–</b></div>
 <div class="row extra"><span>Viewers</span><b data-k="viewers">–</b></div>
 <div class="row extra"><span>Memory</span><b data-k="mem">–</b></div>
 <div class="row extra"><span>Pi uptime</span><b data-k="uptime">–</b></div>
 <div class="row extra"><span>Session</span><b class="mono" data-k="session">0:00:00</b></div>
</div>
<aside class="campanel" id="campanel"></aside>
<div class="glass pad">
 <button class="drive" data-dir="forward">""" + ICON_UP + r"""Forward</button>
 <button class="drive" data-dir="backward">""" + ICON_DN + r"""Backward</button>
</div>
<div class="glass feed hud"><ol class="log" data-k="log" data-n="3"></ol></div>
<div class="glass keys hud"><span><kbd>W</kbd>/<kbd>↑</kbd> fwd</span><span><kbd>S</kbd>/<kbd>↓</kbd> back</span><span><kbd>A</kbd><kbd>D</kbd> speed</span><span><kbd>Space</kbd> stop</span></div>
<div class="glass spd"><span class="lbl">Speed</span>
 <input type="range" class="vrange" id="speed" min="0" max="100" value="50" orient="vertical" aria-label="Speed">
 <span class="v num" data-k="speedv">50%</span></div>"""


def _page(view, title, css, body):
    return (f'<!doctype html><html lang="en"><head><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">'
            f'<meta name="theme-color" content="#0b0d12"><title>{title}</title>'
            f'<style>{BASE_CSS}{css}</style></head><body data-view="{view}">{body}'
            f'<script>document.querySelectorAll(\'.views a[data-v="{view}"]\').forEach(a=>a.classList.add("cur"));'
            f'{CORE_JS}</script></body></html>')


PAGES = {
    "phone": _page("phone", "Robot", PHONE_CSS, PHONE_BODY),
    "desktop": _page("desktop", "Robot Dashboard", DESKTOP_CSS, DESKTOP_BODY),
    "cockpit": _page("cockpit", "Robot Cockpit", COCKPIT_CSS, COCKPIT_BODY),
}


if __name__ == "__main__":
    motors.stop()   # known-safe state on boot
    for fn in (watchdog_loop, health_loop, tailscale_loop, hub.run):
        threading.Thread(target=fn, daemon=True).start()
    app.run(host="0.0.0.0", port=8081, threaded=True)
