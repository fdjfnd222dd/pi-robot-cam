"""Camera server (stream.py) tests against a fake Picamera2. No Pi needed.
Run:  python3 -m unittest discover -s tests
"""
import json, os, shutil, socket, subprocess, sys, tempfile, time, unittest, urllib.error, urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FAKES = os.path.join(ROOT, "tests", "fakes")


def free_port():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return p


class StreamServer(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dir = tempfile.mkdtemp()
        shutil.copy(os.path.join(ROOT, "pi", "picam-dash", "camera", "stream.py"), cls.dir)
        cls.events = os.path.join(cls.dir, "events.log"); open(cls.events, "w").close()
        cls.port = free_port()
        env = dict(os.environ, PYTHONPATH=FAKES, PICAM_PORT=str(cls.port), FAKECAM_LOG=cls.events)
        cls.proc = subprocess.Popen([sys.executable, "stream.py"], cwd=cls.dir, env=env,
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(50):
            try:
                urllib.request.urlopen(cls.url(), timeout=1).close(); break
            except OSError:
                time.sleep(0.1)

    @classmethod
    def tearDownClass(cls):
        cls.proc.terminate(); cls.proc.wait(5); shutil.rmtree(cls.dir)

    @classmethod
    def url(cls):
        return f"http://127.0.0.1:{cls.port}/controls"

    def post(self, body):
        req = urllib.request.Request(self.url(), data=json.dumps(body).encode(), method="POST",
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            with e:
                return e.code, json.loads(e.read())

    def events_since(self, n):
        with open(self.events) as f:
            return [json.loads(l)[0] for l in f.read().splitlines()[n:]]

    def nevents(self):
        with open(self.events) as f:
            return len(f.read().splitlines())

    def fps(self, seconds=2):
        s = socket.create_connection(("127.0.0.1", self.port), 5); s.settimeout(5)
        s.sendall(b"GET /stream HTTP/1.1\r\nHost: x\r\n\r\n"); buf, n, t = b"", 0, time.time()
        while time.time() - t < seconds:
            buf += s.recv(65536)
            while True:
                a = buf.find(b"\xff\xd8"); i = buf.find(b"\xff\xd9", a + 2)
                if a < 0 or i < 0: break
                buf = buf[i + 2:]; n += 1
        s.close(); return n / seconds

    def test_1_defaults(self):
        with urllib.request.urlopen(self.url(), timeout=5) as r:
            d = json.loads(r.read())
        self.assertEqual(d["settings"]["resolution"], "640x480")
        self.assertIn("brightness", d["supported"])

    def test_2_picture_change_is_live(self):
        n = self.nevents()
        code, d = self.post({"brightness": 0.3, "awb": "Daylight"})
        self.assertEqual(code, 200)
        self.assertEqual(self.events_since(n), ["set_controls"])      # no camera restart

    def test_3_fps_change_is_live(self):
        n = self.nevents()
        self.assertEqual(self.post({"fps": 24})[0], 200)
        self.assertEqual(self.events_since(n), ["set_controls"])
        self.assertGreater(self.fps(), 18)

    def test_4_resolution_restarts_and_stream_survives(self):
        n = self.nevents()
        self.assertEqual(self.post({"resolution": "1280x720", "rotate": True})[0], 200)
        self.assertEqual(self.events_since(n), ["stop", "configure", "set_controls", "start"])
        self.assertGreater(self.fps(), 5)

    def test_5_bad_values(self):
        self.assertEqual(self.post({"fps": 7})[0], 400)
        self.assertEqual(self.post({"awb": "Purple"})[0], 400)
        self.assertEqual(self.post({"brightness": 5})[1]["settings"]["brightness"], 1)

    def test_6_settings_saved_and_reset(self):
        self.post({"quality": 70})
        with open(os.path.join(self.dir, "camera.json")) as f:
            self.assertEqual(json.load(f)["quality"], 70)
        d = self.post({"reset": True})[1]["settings"]
        self.assertEqual((d["resolution"], d["fps"], d["rotate"]), ("640x480", 12, False))


if __name__ == "__main__":
    unittest.main()
