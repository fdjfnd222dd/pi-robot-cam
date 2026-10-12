"""Dashboard logic tests. No Pi, no Flask needed (a stub stands in if Flask is missing).
Run:  python3 -m unittest discover -s tests
"""
import os, re, subprocess, sys, types, unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "pi", "picam-dash"))
try:
    import flask  # noqa: F401
except ImportError:                      # minimal stand-in: routes become plain functions
    stub = types.ModuleType("flask")
    class _Flask:
        def __init__(self, *a): pass
        def get(self, *a, **k): return lambda f: f
        post = get
        def run(self, **k): pass
    stub.Flask = _Flask; stub.Response = stub.jsonify = stub.request = None
    sys.modules["flask"] = stub
import app as A  # noqa: E402

T = str(100)  # first octet of the Tailscale CGNAT range (test fixtures only)


class Drive(unittest.TestCase):
    def setUp(self):
        A.state.update(dir="stop", speed=0, last=0.0, owner=None); A.cmd_log.clear()

    def log(self):
        return [i["text"] for i in A.cmd_log][::-1]

    def test_heartbeats_log_once(self):
        for t in (0.0, 0.15, 0.3, 0.45):
            A.drive("forward", 60, "a", now=t)
        self.assertEqual(self.log(), ["FORWARD 60%"])

    def test_watchdog_stops_after_half_second(self):
        A.drive("forward", 60, "a", now=0.0)
        A.watchdog_tick(now=0.45); self.assertEqual(A.state["dir"], "forward")
        A.watchdog_tick(now=0.51); self.assertEqual(A.state["dir"], "stop")
        self.assertEqual(self.log()[-1], "STOP (auto-stop)")
        self.assertIsNone(A.state["owner"])

    def test_one_driver_at_a_time(self):
        self.assertEqual(A.drive("forward", 60, "a", now=0), "ok")
        self.assertEqual(A.drive("forward", 90, "b", now=0.1), "busy")
        self.assertEqual(A.drive("stop", 0, "b", now=0.2), "busy")      # b can't stop a's drive
        self.assertEqual(A.state["speed"], 60)
        self.assertEqual(A.drive("stop", 0, "a", now=0.3), "ok")
        self.assertEqual(A.drive("backward", 40, "b", now=0.4), "ok")   # free again

    def test_speed_clamped(self):
        A.drive("forward", 250, "a", now=0); self.assertEqual(A.state["speed"], 100)
        A.drive("stop", 0, "a"); A.drive("forward", -5, "a", now=1); self.assertEqual(A.state["speed"], 0)


class Parsing(unittest.TestCase):
    def test_throttled(self):
        self.assertEqual(A.decode_throttled(0)["text"], "OK")
        self.assertFalse(A.decode_throttled(0x50005)["ok"])
        self.assertIn("earlier", A.decode_throttled(0x50000)["text"])

    def test_ping(self):
        self.assertEqual(A.parse_ping("pong from pi (" + T + ".1.2.3) via 172.20.10.2:41641 in 6ms")["type"], "Direct")
        r = A.parse_ping("pong from phone (" + T + ".1.2.4) via DERP(nyc) in 135ms")
        self.assertEqual((r["type"], r["latency_ms"]), ("Relay", 135))
        self.assertIsNone(A.parse_ping("timeout"))

    def test_tailscale_ip(self):
        self.assertTrue(A.is_tailscale_ip(T + ".127.61.22"))
        self.assertFalse(A.is_tailscale_ip("172.20.10.3"))

    def test_camera_log_text(self):
        self.assertEqual(A.camera_log_text({"_preset": "Low light", "ev": 1}), "CAM preset Low light")
        self.assertEqual(A.camera_log_text({"rotate": True}), "CAM rotate on")


class Pages(unittest.TestCase):
    def test_pages_have_required_elements(self):
        for name, page in A.PAGES.items():
            for needle in ('id="speed"', 'id="cam"', "data-dir=\"forward\""):
                self.assertIn(needle, page, f"{name} missing {needle}")
        for name in ("desktop", "cockpit"):
            self.assertIn('id="campanel"', A.PAGES[name])

    def test_page_javascript_parses(self):
        node = subprocess.run(["which", "node"], capture_output=True, text=True).stdout.strip()
        if not node:
            self.skipTest("node not installed")
        for name, page in A.PAGES.items():
            js = re.findall(r"<script>(.*?)</script>", page, re.S)[0]
            r = subprocess.run([node, "--check", "-"], input=js, capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, f"{name}: {r.stderr}")


if __name__ == "__main__":
    unittest.main()
