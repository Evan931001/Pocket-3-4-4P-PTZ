#!/usr/bin/env python3
"""Pocket 3 三機導播控制台 EXE 包裝器。

在本機提供控制頁並只開啟一次 Edge App 視窗。視窗關閉後停止本機伺服器，
不在背景重開控制台。
"""

import os
import sys
import time
import json
import socket
import threading
import subprocess
import webbrowser
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from ndi_preview import NDIPreviewManager

HOST = "127.0.0.1"
PORT = 18723
SOURCE_DIR = os.path.dirname(os.path.abspath(__file__))
BUNDLE_DIR = getattr(sys, "_MEIPASS", SOURCE_DIR)
PANEL_FILE = os.path.join(BUNDLE_DIR, "director-panel.html")
APP_DIR = os.path.dirname(sys.executable) if getattr(sys, "frozen", False) else SOURCE_DIR
PROFILE_DIR = os.path.join(os.environ.get("LOCALAPPDATA", APP_DIR), "Pocket3Director", "EdgeProfile")
stop_event = threading.Event()
preview_manager = NDIPreviewManager()
last_heartbeat = 0.0
HEARTBEAT_TIMEOUT = 6.0
STARTUP_GRACE = 15.0


def find_edge():
    roots = [
        os.environ.get("PROGRAMFILES(X86)"),
        os.environ.get("PROGRAMFILES"),
        os.environ.get("LOCALAPPDATA"),
    ]
    suffixes = [
        os.path.join("Microsoft", "Edge", "Application", "msedge.exe"),
        os.path.join("Microsoft", "Edge SxS", "Application", "msedge.exe"),
    ]
    for root in roots:
        if not root:
            continue
        for suffix in suffixes:
            candidate = os.path.join(root, suffix)
            if os.path.isfile(candidate):
                return candidate
    return None


def open_console():
    url = f"http://{HOST}:{PORT}/?director=1"
    edge = find_edge()
    if not edge:
        webbrowser.open(url)
        return None
    os.makedirs(PROFILE_DIR, exist_ok=True)
    return subprocess.Popen([
        edge,
        f"--app={url}",
        f"--user-data-dir={PROFILE_DIR}",
        "--start-maximized",
        "--no-first-run",
        "--disable-session-crashed-bubble",
        "--disable-features=msEdgeSidebarV2",
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        return

    def _headers(self, status=200, content_type="text/plain; charset=utf-8"):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()

    def _json(self, value, status=200):
        data = json.dumps(value, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(data)

    def _body_json(self):
        length = min(int(self.headers.get("Content-Length", "0") or 0), 65536)
        return json.loads(self.rfile.read(length).decode("utf-8") or "{}")

    def _mjpeg(self, camera):
        self.send_response(200)
        self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
        self.end_headers()
        try:
            while not stop_event.is_set():
                frame = preview_manager.frame(camera)
                if frame:
                    self.wfile.write(b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: " + str(len(frame)).encode() + b"\r\n\r\n")
                    self.wfile.write(frame + b"\r\n")
                    self.wfile.flush()
                time.sleep(0.075)
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass

    def do_GET(self):
        global last_heartbeat
        parsed = urllib.parse.urlsplit(self.path)
        if parsed.path in ("/", "/index.html"):
            last_heartbeat = time.monotonic()
            try:
                with open(PANEL_FILE, "rb") as f:
                    data = f.read()
                self._headers(200, "text/html; charset=utf-8")
                self.wfile.write(data)
            except OSError:
                self._headers(500)
                self.wfile.write(b"director-panel.html missing")
        elif parsed.path == "/health":
            self._headers()
            self.wfile.write(b"ok")
        elif parsed.path == "/director/heartbeat":
            last_heartbeat = time.monotonic()
            self._headers()
            self.wfile.write(b"alive")
        elif parsed.path == "/ndi/status":
            self._json(preview_manager.status())
        elif parsed.path == "/ndi/mjpeg":
            try:
                camera = int(urllib.parse.parse_qs(parsed.query).get("camera", ["0"])[0])
            except ValueError:
                camera = -1
            if not 0 <= camera < 3:
                self._json({"error": "invalid camera"}, 400)
            else:
                self._mjpeg(camera)
        else:
            self._headers(404)

    def do_POST(self):
        if self.path == "/director/exit":
            self._headers()
            self.wfile.write("正在完整結束導播控制台".encode("utf-8"))
            stop_event.set()
        elif self.path in ("/ndi/select", "/ndi/tracking", "/ndi/target"):
            try:
                body = self._body_json()
                camera = int(body.get("camera", -1))
                if self.path == "/ndi/select":
                    preview_manager.configure(camera, body.get("source", ""), body.get("endpoint", ""))
                elif self.path == "/ndi/tracking":
                    preview_manager.tracking(camera, str(body.get("mode", "off")))
                else:
                    preview_manager.target(camera, float(body.get("x", 0.5)), float(body.get("y", 0.5)))
                self._json({"ok": True})
            except Exception as exc:
                self._json({"ok": False, "error": str(exc)}, 400)
        else:
            self._headers(404)


def already_running():
    try:
        with socket.create_connection((HOST, PORT), timeout=0.25):
            return True
    except OSError:
        return False


def main():
    global last_heartbeat
    if already_running():
        # 已有控制台執行時直接離開，避免重複開啟視窗或背景程序。
        return

    server = ThreadingHTTPServer((HOST, PORT), Handler)
    threading.Thread(target=server.serve_forever, name="director-http", daemon=True).start()
    last_heartbeat = time.monotonic()
    process = open_console()
    opened_at = time.monotonic()
    try:
        while not stop_event.is_set():
            stop_event.wait(1.0)
            if stop_event.is_set():
                break
            now = time.monotonic()
            # 控制頁停止 heartbeat 代表視窗已關閉。結束後台伺服器，絕不重開。
            if now - opened_at >= STARTUP_GRACE and now - last_heartbeat >= HEARTBEAT_TIMEOUT:
                print("控制視窗已關閉，正在結束 Director；不會自動重新開啟。")
                stop_event.set()
                break
    finally:
        preview_manager.shutdown()
        server.shutdown()
        server.server_close()
        if process is not None and process.poll() is None:
            process.terminate()


if __name__ == "__main__":
    main()
