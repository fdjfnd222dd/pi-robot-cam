#!/usr/bin/env python3
"""Measure the live camera stream: fps, KB/s, frame size, freezes.

  tools/stream_test.py                       30 s against the dashboard relay (:8081/stream.bin)
  tools/stream_test.py --port 8080 --path /stream --seconds 15
  tools/stream_test.py --viewers 3           several viewers at once

A "freeze" is a gap of more than 0.6 s between frames. This is the test behind
every number in docs/measurements.md. KB = 1024 bytes.
From the cloud container, run it with ALL_PROXY unset; it connects through
the Tailscale SOCKS proxy automatically when --socks is given.
"""
import argparse, os, socket, struct, threading, time


def connect(host, port, socks):
    if not socks:
        return socket.create_connection((host, port), 10)
    sh, sp = socks.split(":")
    s = socket.create_connection((sh, int(sp)), 10)              # SOCKS5, no auth
    s.sendall(b"\x05\x01\x00"); s.recv(2)
    s.sendall(b"\x05\x01\x00\x03" + bytes([len(host)]) + host.encode() + struct.pack(">H", port))
    if s.recv(10)[1] != 0:
        raise ConnectionError("SOCKS connect failed")
    return s


def viewer(args, out, name):
    s = connect(args.host, args.port, args.socks); s.settimeout(10)
    s.sendall(f"GET {args.path} HTTP/1.1\r\nHost: {args.host}\r\n\r\n".encode())
    buf, n, size, stalls = b"", 0, 0, []
    t0 = last = time.time()
    while time.time() - t0 < args.seconds:
        try:
            d = s.recv(65536)
        except OSError:
            stalls.append("TIMEOUT"); break
        if not d:
            stalls.append("CLOSED"); break
        buf += d
        while True:
            a = buf.find(b"\xff\xd8"); i = buf.find(b"\xff\xd9", a + 2)
            if a < 0 or i < 0:
                break
            size += i + 2 - a; buf = buf[i + 2:]; n += 1
            now = time.time()
            if now - last > 0.6:
                stalls.append(round(now - last, 2))
            last = now
    s.close()
    dt = args.seconds
    out[name] = (f"{n} frames = {n / dt:.1f} fps, {size / dt / 1024:.0f} KB/s, "
                 f"{size // max(n, 1):,} B/frame, freezes >0.6 s: {stalls or 'none'}")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--host", default=os.environ.get("PI_HOST"))
    p.add_argument("--port", type=int, default=8081)
    p.add_argument("--path", default="/stream.bin")
    p.add_argument("--seconds", type=float, default=30)
    p.add_argument("--viewers", type=int, default=1)
    p.add_argument("--socks", default=None, help="e.g. localhost:1055 in the cloud container")
    args = p.parse_args()
    if not args.host:
        p.error("set --host or the PI_HOST environment variable (the Pi's Tailscale IP)")
    out = {}
    th = [threading.Thread(target=viewer, args=(args, out, f"viewer {k + 1}")) for k in range(args.viewers)]
    [t.start() for t in th]; [t.join() for t in th]
    print(f"{args.seconds:.0f} s on {args.host}:{args.port}{args.path}")
    for k in sorted(out):
        print(f"  {k}: {out[k]}")


if __name__ == "__main__":
    main()
