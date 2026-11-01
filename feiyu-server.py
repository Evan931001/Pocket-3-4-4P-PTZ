#!/usr/bin/env python3
"""Experimental Feiyu Pocket 3 node using the common Pocket PTZ HTTP API."""

from __future__ import annotations

import argparse
import ctypes
import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from feiyu_protocol import FeiyuTransport


SOURCE_DIR = os.path.dirname(os.path.abspath(__file__))
BUNDLE_DIR = getattr(sys, "_MEIPASS", SOURCE_DIR)
PANEL_FILE = os.path.join(BUNDLE_DIR, "director-panel.html")


def clamp(value, minimum, maximum):
    return max(minimum, min(maximum, value))


def watch_parent_process(parent_pid):
    if not parent_pid or parent_pid <= 0:
        return
    if os.name == "nt":
        handle = ctypes.windll.kernel32.OpenProcess(0x00100000, False, parent_pid)
        if not handle:
            os._exit(0)
        if ctypes.windll.kernel32.WaitForSingleObject(handle, 0xFFFFFFFF) == 0:
            os._exit(0)
        return
    while True:
        try:
            os.kill(parent_pid, 0)
        except OSError:
            os._exit(0)
        time.sleep(1.0)


class FeiyuNode:
    def __init__(self, camera_host, label, speed):
        self.camera_host = camera_host
        self.label = label
        self.speed = speed
        self.transport = None
        self.lock = threading.Lock()
        self.stop_event = threading.Event()
        self.velocity = {"pan": 0.0, "tilt": 0.0}
        self.nudge_until = 0.0
        self.nudge_velocity = {"pan": 0.0, "tilt": 0.0}
        self.connected = False
        self.last_error = "Waiting for Feiyu camera Wi-Fi"
        self.last_heartbeat_code = None
        self.thread = threading.Thread(target=self.run, name="feiyu-control", daemon=True)

    def start(self):
        self.thread.start()

    def set_velocity(self, pan=None, tilt=None):
        with self.lock:
            if pan is not None:
                self.velocity["pan"] = clamp(float(pan), -1.0, 1.0)
            if tilt is not None:
                self.velocity["tilt"] = clamp(float(tilt), -1.0, 1.0)

    def stop_motion(self):
        with self.lock:
            self.velocity = {"pan": 0.0, "tilt": 0.0}
            self.nudge_until = 0.0

    def nudge(self, pan=0.0, tilt=0.0):
        with self.lock:
            self.nudge_velocity = {
                "pan": clamp(float(pan), -1.0, 1.0),
                "tilt": clamp(float(tilt), -1.0, 1.0),
            }
            self.nudge_until = time.monotonic() + 0.18

    def set_speed(self, value):
        self.speed = max(1, min(127, int(round(float(value) * 127))))
        if self.transport is not None:
            self.transport.speed = self.speed

    def status(self, port):
        with self.lock:
            moving = any(abs(value) > 1e-4 for value in self.velocity.values())
            if time.monotonic() < self.nudge_until:
                moving = True
        return {
            "backend": "feiyu_wifi_experimental",
            "connected": self.label if self.connected else None,
            "devices": [self.label],
            "device_index": 0 if self.connected else None,
            "fixed_device_index": 0,
            "fixed_device_match": "",
            "node_port": port,
            "node_label": self.label,
            "camera_host": self.camera_host,
            "speed": round(self.speed / 127.0, 3),
            "moving": moving,
            "pos": {"pan": None, "tilt": None, "zoom": None},
            "presets": [],
            "preview_status": "EXTERNAL UVC / NDI SOURCE",
            "preview_fps": 0.0,
            "preview_profile": "external",
            "preview_enabled": False,
            "capture_mode": "EXTERNAL UVC",
            "obs_clients": 0,
            "last_error": self.last_error,
            "heartbeat_code": self.last_heartbeat_code,
            "capabilities": {
                "pan": True,
                "tilt": True,
                "zoom": False,
                "center": False,
                "presets": False,
                "preview": False,
                "tracking": False,
                "device_select": False,
            },
        }

    def run(self):
        retry_at = 0.0
        heartbeat_at = 0.0
        send_at = 0.0
        while not self.stop_event.is_set():
            now = time.monotonic()
            if self.transport is None and now >= retry_at:
                transport = FeiyuTransport(self.camera_host, self.speed)
                try:
                    transport.connect()
                    self.transport = transport
                    self.connected = True
                    self.last_error = ""
                    self.last_heartbeat_code = transport.last_heartbeat_code
                    heartbeat_at = now + 0.4
                    send_at = now
                    print(f"Feiyu connected: {self.camera_host} · heartbeat 0x{self.last_heartbeat_code:04X}")
                except Exception as exc:
                    transport.close()
                    self.connected = False
                    self.last_error = f"{type(exc).__name__}: {exc}"
                    retry_at = now + 2.0

            transport = self.transport
            if transport is not None:
                try:
                    if now >= heartbeat_at:
                        self.last_heartbeat_code = transport.heartbeat()
                        heartbeat_at = now + 0.4
                    if now >= send_at:
                        with self.lock:
                            if now < self.nudge_until:
                                velocity = dict(self.nudge_velocity)
                            else:
                                velocity = dict(self.velocity)
                        transport.send(velocity["pan"], velocity["tilt"])
                        send_at = now + 0.05
                except Exception as exc:
                    self.last_error = f"{type(exc).__name__}: {exc}"
                    self.connected = False
                    transport.close()
                    self.transport = None
                    retry_at = now + 1.5
            self.stop_event.wait(0.01)

        if self.transport is not None:
            self.transport.close()
            self.transport = None

    def close(self):
        self.stop_event.set()
        self.thread.join(timeout=2.0)


class Handler(BaseHTTPRequestHandler):
    node = None
    port = 0

    def log_message(self, *_args):
        return

    def send_bytes(self, status, body, content_type="text/plain; charset=utf-8"):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def send_json(self, value, status=200):
        self.send_bytes(status, json.dumps(value, ensure_ascii=False).encode(), "application/json; charset=utf-8")

    def do_GET(self):
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        if parsed.path in ("/", "/index.html"):
            try:
                with open(PANEL_FILE, "rb") as panel:
                    body = panel.read()
                return self.send_bytes(200, body, "text/html; charset=utf-8")
            except OSError:
                return self.send_bytes(500, b"director-panel.html missing")
        if parsed.path == "/status":
            return self.send_json(self.node.status(self.port))
        if parsed.path == "/vel":
            try:
                pan = query.get("pan", [None])[0]
                tilt = query.get("tilt", [None])[0]
                self.node.set_velocity(pan, tilt)
                return self.send_json({"ok": True})
            except (TypeError, ValueError) as exc:
                return self.send_json({"ok": False, "error": str(exc)}, 400)
        if parsed.path == "/stop":
            self.node.stop_motion()
            return self.send_json({"ok": True})
        if parsed.path == "/nudge":
            try:
                pan = float(query.get("pan", ["0"])[0])
                tilt = float(query.get("tilt", ["0"])[0])
                self.node.nudge(pan, tilt)
                return self.send_json({"ok": True})
            except ValueError as exc:
                return self.send_json({"ok": False, "error": str(exc)}, 400)
        if parsed.path == "/speed":
            try:
                self.node.set_speed(query.get("v", ["0.134"])[0])
                return self.send_json({"ok": True, "speed": round(self.node.speed / 127.0, 3)})
            except ValueError as exc:
                return self.send_json({"ok": False, "error": str(exc)}, 400)
        if parsed.path in ("/rescan", "/connect"):
            return self.send_json({"ok": True})
        if parsed.path == "/preview/control":
            return self.send_json({"ok": True, "preview": False})
        if parsed.path.startswith("/preset/") or parsed.path in ("/center", "/sethome", "/goto"):
            return self.send_json({"ok": False, "error": "Feiyu absolute position is not available"}, 409)
        if parsed.path in ("/preview.jpg", "/preview.mjpg", "/obs", "/obs.mjpg"):
            return self.send_json({"ok": False, "error": "Use the Feiyu UVC or NDI video source directly"}, 503)
        return self.send_json({"ok": False, "error": "not found"}, 404)


def main():
    parser = argparse.ArgumentParser(description="Experimental Feiyu Pocket 3 PTZ node")
    parser.add_argument("--host", default="0.0.0.0", help="HTTP listen address")
    parser.add_argument("--port", type=int, default=8723, help="HTTP listen port")
    parser.add_argument("--camera-host", default="192.168.1.1", help="Feiyu camera Wi-Fi address")
    parser.add_argument("--label", default="FEIYU POCKET 3", help="Director label")
    parser.add_argument("--speed", type=int, default=17, help="Joystick magnitude, 1-127")
    parser.add_argument("--parent-pid", type=int, default=0)
    args = parser.parse_args()

    node = FeiyuNode(args.camera_host, args.label, args.speed)
    Handler.node = node
    Handler.port = args.port
    if args.parent_pid:
        threading.Thread(target=watch_parent_process, args=(args.parent_pid,), daemon=True).start()
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    node.start()
    print(f"Feiyu experimental node: http://127.0.0.1:{args.port}/")
    print(f"Camera control target: {args.camera_host}:15740 + UDP 7777")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.shutdown()
        server.server_close()
        node.close()


if __name__ == "__main__":
    main()
