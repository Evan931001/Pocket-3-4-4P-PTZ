#!/usr/bin/env python3
"""NDI preview and software PTZ tracking for Pocket3-Director.

The module is optional by design. Import or runtime failures are reported through
the local HTTP API without preventing the normal PTZ director from starting.
"""

import json
import time
import threading
import urllib.parse
import urllib.request

try:
    import cv2
    import numpy as np
    VISION_ERROR = None
except Exception as exc:  # pragma: no cover - depends on Windows runtime
    cv2 = None
    np = None
    VISION_ERROR = f"{type(exc).__name__}: {exc}"

try:
    import NDIlib as ndi
    NDI_ERROR = None
except Exception as exc:  # NDI is optional when Direct UVC LAN is used
    ndi = None
    NDI_ERROR = f"{type(exc).__name__}: {exc}"


MODES = {"off", "click", "face", "person"}


def clamp(value, low, high):
    return max(low, min(high, value))


class VisionTracker:
    def __init__(self):
        self.mode = "off"
        self.pending_click = None
        self.box = None
        self.tracker = None
        self.last_detect = 0.0
        self.last_seen = 0.0
        self.face = None
        self.hog = None
        if cv2 is not None:
            try:
                self.face = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
            except Exception:
                self.face = None
            try:
                self.hog = cv2.HOGDescriptor()
                self.hog.setSVMDetector(cv2.HOGDescriptor_getDefaultPeopleDetector())
            except Exception:
                self.hog = None

    def set_mode(self, mode):
        self.mode = mode if mode in MODES else "off"
        self.pending_click = None
        self.box = None
        self.tracker = None

    def click(self, x, y):
        self.pending_click = (clamp(float(x), 0.0, 1.0), clamp(float(y), 0.0, 1.0))
        self.mode = "click"
        self.tracker = None

    def _faces(self, frame):
        if self.face is None or self.face.empty():
            return []
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        return [tuple(map(int, box)) for box in self.face.detectMultiScale(gray, 1.15, 5, minSize=(28, 28))]

    def _people(self, frame):
        if self.hog is None:
            return []
        small = cv2.resize(frame, (384, 216))
        boxes, weights = self.hog.detectMultiScale(small, winStride=(8, 8), padding=(8, 8), scale=1.05)
        sx, sy = frame.shape[1] / 384.0, frame.shape[0] / 216.0
        result = []
        for box, weight in zip(boxes, weights):
            if float(weight) < 0.25:
                continue
            x, y, w, h = box
            result.append((int(x * sx), int(y * sy), int(w * sx), int(h * sy)))
        return result

    @staticmethod
    def _largest(boxes):
        return max(boxes, key=lambda b: b[2] * b[3]) if boxes else None

    @staticmethod
    def _distance(box, px, py):
        cx, cy = box[0] + box[2] / 2, box[1] + box[3] / 2
        return (cx - px) ** 2 + (cy - py) ** 2

    def _make_tracker(self):
        try:
            if hasattr(cv2, "TrackerCSRT_create"):
                return cv2.TrackerCSRT_create()
            if hasattr(cv2, "legacy") and hasattr(cv2.legacy, "TrackerCSRT_create"):
                return cv2.legacy.TrackerCSRT_create()
        except Exception:
            pass
        return None

    def update(self, frame):
        now = time.monotonic()
        h, w = frame.shape[:2]
        box = None

        if self.mode == "off":
            self.box = None
            return None

        if self.mode == "click":
            if self.pending_click is not None:
                px, py = self.pending_click[0] * w, self.pending_click[1] * h
                candidates = self._people(frame) + self._faces(frame)
                containing = [b for b in candidates if b[0] <= px <= b[0] + b[2] and b[1] <= py <= b[1] + b[3]]
                if containing:
                    box = self._largest(containing)
                elif candidates:
                    box = min(candidates, key=lambda b: self._distance(b, px, py))
                else:
                    bw, bh = int(w * 0.24), int(h * 0.42)
                    box = (int(clamp(px - bw / 2, 0, w - bw)), int(clamp(py - bh / 2, 0, h - bh)), bw, bh)
                self.pending_click = None
                self.tracker = self._make_tracker()
                if self.tracker is not None:
                    try:
                        self.tracker.init(frame, box)
                    except Exception:
                        self.tracker = None
                self.box = box
                self.last_seen = now
            elif self.tracker is not None:
                try:
                    ok, tracked = self.tracker.update(frame)
                    if ok:
                        box = tuple(map(int, tracked))
                        self.box = box
                        self.last_seen = now
                    elif now - self.last_seen > 0.65:
                        self.box = None
                except Exception:
                    self.tracker = None
            else:
                box = self.box
        elif now - self.last_detect >= 0.22:
            self.last_detect = now
            box = self._largest(self._faces(frame) if self.mode == "face" else self._people(frame))
            if box is not None:
                self.box = box
                self.last_seen = now
            elif now - self.last_seen > 0.65:
                self.box = None
            return self.box
        else:
            return self.box

        if box is not None and box[2] > 2 and box[3] > 2:
            self.box = box
            return box
        if now - self.last_seen > 0.65:
            self.box = None
        return self.box


class NDIChannel:
    def __init__(self, manager, index):
        self.manager = manager
        self.index = index
        self.source_name = ""
        self.endpoint = ""
        self.status = "NO SOURCE"
        self.latest_jpeg = manager.placeholder("NO VIDEO SOURCE")
        self.fps = 0.0
        self.tracker = VisionTracker()
        self.thread = None
        self.stop_event = threading.Event()
        self.lock = threading.Lock()
        self.last_ptz = 0.0
        self.last_command = (None, None)

    def configure(self, source, endpoint):
        source = str(source or "").strip()
        endpoint = str(endpoint or "").strip().rstrip("/")
        # 同一來源再次套用也要真正重啟，讓 REFRESH 能救回中斷的第三路預覽。
        self.stop()
        self.source_name, self.endpoint = source, endpoint
        self.stop_event = threading.Event()
        if not source:
            self.status = "NO SOURCE"
            self.latest_jpeg = self.manager.placeholder("NO VIDEO SOURCE")
            return
        self.thread = threading.Thread(target=self._run, args=(self.stop_event, source), name=f"ndi-camera-{self.index+1}", daemon=True)
        self.thread.start()

    def set_tracking(self, mode):
        self.tracker.set_mode(mode)
        if self.tracker.mode == "off":
            self._send_ptz(0.0, 0.0, force=True)

    def set_target(self, x, y):
        self.tracker.click(x, y)

    def stop(self):
        self.stop_event.set()
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=0.8)
        self.thread = None
        self._send_ptz(0.0, 0.0, force=True)

    def get_jpeg(self):
        with self.lock:
            return self.latest_jpeg

    def info(self):
        return {"source": self.source_name, "status": self.status, "fps": round(self.fps, 1), "mode": self.tracker.mode}

    def _send_ptz(self, pan, tilt, force=False):
        if not self.endpoint:
            return
        now = time.monotonic()
        command = (round(float(pan), 3), round(float(tilt), 3))
        if not force and (now - self.last_ptz < 0.09 or command == self.last_command):
            return
        self.last_ptz, self.last_command = now, command
        url = f"{self.endpoint}/vel?pan={command[0]}&tilt={command[1]}"
        try:
            self.manager.http.open(url, timeout=0.18).read(32)
        except Exception:
            pass

    def _track_ptz(self, box, width, height):
        if self.tracker.mode == "off" or box is None:
            self._send_ptz(0.0, 0.0)
            return
        cx, cy = box[0] + box[2] / 2, box[1] + box[3] / 2
        dx, dy = cx / width - 0.5, cy / height - 0.5
        dead_x, dead_y = 0.075, 0.09
        if abs(dx) <= dead_x and abs(dy) <= dead_y:
            self._send_ptz(0.0, 0.0)
        elif abs(dx / dead_x) >= abs(dy / dead_y):
            pan = clamp((abs(dx) - dead_x) * 1.7, 0.10, 0.55) * (1 if dx > 0 else -1)
            self._send_ptz(pan, 0.0)
        else:
            tilt = clamp((abs(dy) - dead_y) * 1.7, 0.10, 0.50) * (-1 if dy > 0 else 1)
            self._send_ptz(0.0, tilt)

    def _overlay(self, frame, box):
        h, w = frame.shape[:2]
        color = (74, 220, 127)
        cv2.rectangle(frame, (int(w * 0.425), int(h * 0.41)), (int(w * 0.575), int(h * 0.59)), (70, 130, 160), 1)
        if box is not None:
            x, y, bw, bh = box
            cv2.rectangle(frame, (x, y), (x + bw, y + bh), color, 2)
            cv2.circle(frame, (x + bw // 2, y + bh // 2), 4, color, -1)
        source_label = "DIRECT UVC LAN" if self.source_name == "__LAN_UVC__" else (self.source_name or "NO SOURCE")
        label = f"{source_label}  {self.fps:.1f} FPS  TRACK:{self.tracker.mode.upper()}"
        cv2.rectangle(frame, (0, 0), (w, 25), (8, 10, 12), -1)
        cv2.putText(frame, label, (8, 17), cv2.FONT_HERSHEY_SIMPLEX, 0.42, (220, 230, 235), 1, cv2.LINE_AA)
        return frame

    def _process_image(self, image, counters):
        image = cv2.resize(image, (640, 360), interpolation=cv2.INTER_AREA)
        box = self.tracker.update(image)
        self._track_ptz(box, 640, 360)
        self._overlay(image, box)
        ok, encoded = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, 72])
        if ok:
            with self.lock:
                self.latest_jpeg = encoded.tobytes()
        counters[0] += 1
        elapsed = time.monotonic() - counters[1]
        if elapsed >= 1.0:
            self.fps = counters[0] / elapsed
            counters[0], counters[1] = 0, time.monotonic()

    def _run_lan(self, run_event):
        if not self.endpoint:
            raise RuntimeError("PTZ Node endpoint is empty")
        # 逐幀短連線比單一 MJPEG 長連線更能承受 Node 晚啟動、USB reset 與
        # 第三台相機較慢的情況；每次失敗都能在下一輪重新建立連線。
        url = f"{self.endpoint}/preview.jpg"
        counters = [0, time.monotonic()]
        failures = 0
        while not run_event.is_set():
            started = time.monotonic()
            try:
                self.status = "CONNECTING DIRECT UVC" if failures else "DIRECT UVC POLLING"
                request = urllib.request.Request(
                    f"{url}?t={time.monotonic_ns()}",
                    headers={"User-Agent": "Pocket3-Director/1.1", "Connection": "close"},
                )
                with self.manager.http.open(request, timeout=2.5) as response:
                    jpeg = response.read(2_000_000)
                image = cv2.imdecode(np.frombuffer(jpeg, dtype=np.uint8), cv2.IMREAD_COLOR)
                if image is None:
                    raise RuntimeError("invalid UVC JPEG frame")
                failures = 0
                self.status = "DIRECT UVC ONLINE"
                self._process_image(image, counters)
                run_event.wait(max(0.0, 0.125 - (time.monotonic() - started)))
            except Exception as exc:
                if run_event.is_set():
                    break
                failures += 1
                self.status = f"UVC RETRY {failures}: {exc}"
                self._send_ptz(0.0, 0.0, force=True)
                run_event.wait(min(1.0, 0.18 + failures * 0.08))

    def _run(self, run_event, source_name):
        receiver = None
        try:
            if source_name == "__LAN_UVC__":
                self._run_lan(run_event)
                return
            if ndi is None:
                raise RuntimeError(f"NDI unavailable: {NDI_ERROR}")
            self.status = "CONNECTING"
            source = self.manager.resolve_source(source_name, timeout=5.0, stop_event=run_event)
            if source is None:
                raise RuntimeError("NDI source not found")
            settings = ndi.RecvCreateV3()
            settings.color_format = ndi.RECV_COLOR_FORMAT_BGRX_BGRA
            receiver = ndi.recv_create_v3(settings)
            if receiver is None:
                raise RuntimeError("NDI receiver creation failed")
            ndi.recv_connect(receiver, source)
            self.status = "ONLINE"
            counters = [0, time.monotonic()]
            while not run_event.is_set():
                frame_type, video, audio, metadata = ndi.recv_capture_v2(receiver, 120)
                try:
                    if frame_type == ndi.FRAME_TYPE_VIDEO:
                        image = np.copy(video.data)
                        if image.ndim == 3 and image.shape[2] == 4:
                            image = cv2.cvtColor(image, cv2.COLOR_BGRA2BGR)
                        elif image.ndim != 3:
                            continue
                        self._process_image(image, counters)
                    elif frame_type == ndi.FRAME_TYPE_AUDIO:
                        pass
                finally:
                    if frame_type == ndi.FRAME_TYPE_VIDEO:
                        ndi.recv_free_video_v2(receiver, video)
                    elif frame_type == ndi.FRAME_TYPE_AUDIO:
                        ndi.recv_free_audio_v2(receiver, audio)
                    elif frame_type == ndi.FRAME_TYPE_METADATA:
                        ndi.recv_free_metadata(receiver, metadata)
        except Exception as exc:
            self.status = f"ERROR: {exc}"
            self.latest_jpeg = self.manager.placeholder(str(exc)[:64])
        finally:
            self._send_ptz(0.0, 0.0, force=True)
            if receiver is not None:
                try:
                    ndi.recv_destroy(receiver)
                except Exception:
                    pass


class NDIPreviewManager:
    def __init__(self):
        self.error = VISION_ERROR
        self.ndi_error = NDI_ERROR
        # 區網 PTZ/預覽不使用 Windows 系統 Proxy，避免內網 Port 被代理攔截。
        self.http = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        self.finder = None
        self.find_lock = threading.Lock()
        self.initialized = False
        self.channels = [NDIChannel(self, i) for i in range(3)]
        if VISION_ERROR is None and NDI_ERROR is None:
            try:
                self.initialized = bool(ndi.initialize())
                if not self.initialized:
                    self.ndi_error = "NDI initialization failed"
                else:
                    self.finder = ndi.find_create_v2()
                    if self.finder is None:
                        self.ndi_error = "NDI source finder failed"
            except Exception as exc:
                self.ndi_error = f"{type(exc).__name__}: {exc}"

    def placeholder(self, text):
        if cv2 is None or np is None:
            return b""
        image = np.zeros((360, 640, 3), dtype=np.uint8)
        image[:] = (12, 15, 17)
        cv2.putText(image, text[:60], (24, 184), cv2.FONT_HERSHEY_SIMPLEX, 0.62, (110, 180, 205), 1, cv2.LINE_AA)
        ok, encoded = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, 70])
        return encoded.tobytes() if ok else b""

    def sources(self, wait_ms=500):
        if self.finder is None:
            return []
        with self.find_lock:
            ndi.find_wait_for_sources(self.finder, int(wait_ms))
            return [str(source.ndi_name) for source in ndi.find_get_current_sources(self.finder)]

    def resolve_source(self, name, timeout=3.0, stop_event=None):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if stop_event is not None and stop_event.is_set():
                return None
            with self.find_lock:
                ndi.find_wait_for_sources(self.finder, 300)
                sources = list(ndi.find_get_current_sources(self.finder))
                for source in sources:
                    if str(source.ndi_name) == name:
                        return source
            time.sleep(0.08)
        return None

    def status(self):
        return {"available": not bool(self.error), "error": self.error, "ndi_available": self.finder is not None, "ndi_error": self.ndi_error, "sources": self.sources(120), "channels": [c.info() for c in self.channels]}

    def configure(self, index, source, endpoint):
        if not 0 <= index < 3:
            raise ValueError("invalid camera index")
        if self.error:
            raise RuntimeError(self.error)
        self.channels[index].configure(source, endpoint)

    def tracking(self, index, mode):
        if not 0 <= index < 3:
            raise ValueError("invalid camera index")
        self.channels[index].set_tracking(mode)

    def target(self, index, x, y):
        if not 0 <= index < 3:
            raise ValueError("invalid camera index")
        self.channels[index].set_target(x, y)

    def frame(self, index):
        if not 0 <= index < 3:
            return self.placeholder("INVALID CAMERA")
        return self.channels[index].get_jpeg()

    def shutdown(self):
        for channel in self.channels:
            channel.stop()
        if self.finder is not None:
            try:
                ndi.find_destroy(self.finder)
            except Exception:
                pass
        if self.initialized:
            try:
                ndi.destroy()
            except Exception:
                pass
