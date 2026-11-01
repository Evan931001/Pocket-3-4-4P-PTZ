#!/usr/bin/env python3
# ============================================================================
# ptz-server.py — 背景控制程式 + 網頁面板（給 OBS 自訂瀏覽器停駐視窗用）
#
# 背景用 DirectShow IAMCameraControl 控 Pocket 3 雲台（不開串流，與 OBS 並存），
# 同時在 TCP 8723 提供控制面板。預設監聽所有網卡，讓同一區網的
# 導播台能直接控制；本機仍可使用 http://127.0.0.1:8723/。
#
# 安裝：pip install pygrabber comtypes
# 執行：python ptz-server.py     （保持這個視窗開著）
# 導播台 OBS：停駐視窗 → 自訂瀏覽器停駐視窗 → 填啟動畫面顯示的 LAN 網址
# ============================================================================

import os
import sys
import time
import json
import socket
import argparse
import threading
import ctypes
from urllib.parse import urlparse, parse_qs
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from ctypes import HRESULT, POINTER, c_long
from comtypes import COMMETHOD, GUID, IUnknown, COMError
import comtypes

try:
    import cv2
except Exception as exc:
    cv2 = None
    CV2_IMPORT_ERROR = f"{type(exc).__name__}: {exc}"
else:
    CV2_IMPORT_ERROR = None

# EXE 版將可寫設定留在 EXE 旁，內附網頁則由 PyInstaller 暫存目錄讀取。
SOURCE_DIR = os.path.dirname(os.path.abspath(__file__))
APP_DIR = os.path.dirname(sys.executable) if getattr(sys, "frozen", False) else SOURCE_DIR
BUNDLE_DIR = getattr(sys, "_MEIPASS", SOURCE_DIR)
CONFIG = os.path.join(APP_DIR, "ptz-config.json")
PANEL_FILE = os.path.join(BUNDLE_DIR, "director-panel.html")


def _early_cli_value(flag, default):
    """Read a simple CLI value before argparse so frozen EXEs get per-node logs."""
    try:
        index = sys.argv.index(flag)
        return sys.argv[index + 1]
    except (ValueError, IndexError):
        return default

if getattr(sys, "frozen", False) and (sys.stdout is None or sys.stderr is None):
    try:
        _log_port = _early_cli_value("--port", str(PORT if "PORT" in globals() else 8723))
        _log = open(os.path.join(APP_DIR, f"Pocket3-PTZ-Node-{_log_port}.log"), "a", encoding="utf-8", buffering=1)
        if sys.stdout is None:
            sys.stdout = _log
        if sys.stderr is None:
            sys.stderr = _log
    except Exception:
        pass


def load_cfg():
    try:
        with open(CONFIG, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_cfg(updates):
    try:
        cur = load_cfg()
        cur.update(updates)
        with open(CONFIG, "w", encoding="utf-8") as f:
            json.dump(cur, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


PORT = 8723
DEFAULT_HOST = "0.0.0.0"
NODE_PORT = PORT
NODE_LABEL = ""
FIXED_DEVICE_INDEX = None
FIXED_DEVICE_MATCH = ""
PREVIEW_PROFILE = "quality"
VIDEO_CATEGORY = "{860BB310-5D01-11D0-BD3B-00A0C911CE86}"
FLAG_MANUAL = 0x0002
PAN, TILT, ZOOM = 0, 1, 3


def watch_parent_process(parent_pid):
    """Launcher 關閉時讓隱藏節點跟著退出，避免留下背景幽靈程序。"""
    if not parent_pid or parent_pid <= 0:
        return
    if os.name == "nt":
        handle = ctypes.windll.kernel32.OpenProcess(0x00100000, False, parent_pid)
        if not handle:
            os._exit(0)
        # SYNCHRONIZE handle；父程序結束時 WaitForSingleObject 會回傳 WAIT_OBJECT_0。
        if ctypes.windll.kernel32.WaitForSingleObject(handle, 0xFFFFFFFF) == 0:
            os._exit(0)
        return
    while True:
        try:
            os.kill(parent_pid, 0)
        except OSError:
            os._exit(0)
        time.sleep(1.0)
UNITS_PER_DEG = 3600
DT = 0.025
UVC_WRITE_INTERVAL = 0.033  # 約 30 Hz；四向模式每次只寫入單一運動軸
ZOOM_SPEED = 0.40
DEADZONE = 0.06      # 搖桿死區（避免手抖/回中漂移）
EXPO = 1.6           # 反應曲線：中間輕推細膩，邊緣才全速
DEFAULT_MOVE_DURATION = 4.0
DEFAULT_MOVE_CURVE = "ease_in_out"
MOVE_CURVES = {"linear", "ease_in", "ease_out", "ease_in_out", "s_curve"}


class IAMCameraControl(IUnknown):
    _iid_ = GUID("{C6E13370-30AC-11D0-A18C-00A0C9118956}")


IAMCameraControl._methods_ = [
    COMMETHOD([], HRESULT, "GetRange",
              (["in"], c_long, "Property"),
              (["out"], POINTER(c_long), "pMin"), (["out"], POINTER(c_long), "pMax"),
              (["out"], POINTER(c_long), "pSteppingDelta"),
              (["out"], POINTER(c_long), "pDefault"), (["out"], POINTER(c_long), "pCapsFlags")),
    COMMETHOD([], HRESULT, "Set",
              (["in"], c_long, "Property"), (["in"], c_long, "lValue"), (["in"], c_long, "Flags")),
    COMMETHOD([], HRESULT, "Get",
              (["in"], c_long, "Property"),
              (["out"], POINTER(c_long), "lValue"), (["out"], POINTER(c_long), "Flags")),
]

# ── 共享狀態（HTTP 執行緒寫、控制執行緒讀）──────────────────────────────────
state = {
    "vel": {PAN: 0.0, TILT: 0.0, ZOOM: 0.0},
    "speed": 0.30,
    "home_deg": -90,
    "center_req": False,
    "sethome_req": False,
    "connect_req": None,
    "rescan_req": False,
    "preset_req": None,      # ("save"|"recall"|"rename"|"delete", n, name)
    "goto": None,            # 平滑移動目標 {"pan":..,"tilt":..,"zoom":..}
    "nudge": {PAN: 0.0, TILT: 0.0, ZOOM: 0.0},  # 點一下走一小步
    "devices": [],
    "connected": None,
    "device_index": None,
    "pos": {PAN: None, TILT: None, ZOOM: None},
    "presets": {},
    "preview_status": "WAITING",
    "preview_fps": 0.0,
    "preview_clients": 0,
    "obs_clients": 0,
    "preview_demand_until": 0.0,
    "preview_enabled": True,
    "capture_mode": "WAITING",
}
preview_lock = threading.Lock()
preview_jpeg = b""
obs_jpeg = b""


def clamp(v, lo, hi):
    return max(lo, min(hi, v))


def shape(v):
    """死區 + 指數曲線：輸入 -1..1，輸出 -1..1（中間更細膩）。"""
    a = abs(v)
    if a <= DEADZONE:
        return 0.0
    a = (a - DEADZONE) / (1 - DEADZONE)
    return (a ** EXPO) * (1.0 if v > 0 else -1.0)


def ease_value(curve, t):
    """將 0..1 的時間轉成運鏡位置曲線。"""
    t = clamp(t, 0.0, 1.0)
    if curve == "ease_in":
        return t * t
    if curve == "ease_out":
        return 1.0 - (1.0 - t) ** 2
    if curve == "ease_in_out":
        return t * t * (3.0 - 2.0 * t)
    if curve == "s_curve":
        return t ** 3 * (t * (t * 6.0 - 15.0) + 10.0)
    return t


def move_settings(query, fallback_duration=DEFAULT_MOVE_DURATION, fallback_curve=DEFAULT_MOVE_CURVE):
    try:
        duration = clamp(float(query.get("duration", [fallback_duration])[0]), 0.5, 60.0)
    except (TypeError, ValueError):
        duration = fallback_duration
    curve = str(query.get("curve", [fallback_curve])[0]).strip().lower()
    if curve not in MOVE_CURVES:
        curve = fallback_curve
    return duration, curve


# ── 控制執行緒：所有相機 / COM 操作都在這條執行緒做 ──────────────────────────
def control_loop():
    comtypes.CoInitialize()
    from pygrabber.dshow_graph import SystemDeviceEnum
    cam = None
    axis = {}

    def read_devices():
        try:
            return SystemDeviceEnum().get_available_filters(VIDEO_CATEGORY)
        except Exception as e:
            print("列舉裝置失敗：", e)
            return []

    def connect(idx):
        nonlocal cam, axis
        if not isinstance(idx, int) or not (0 <= idx < len(state["devices"])):
            print(f"無效的相機編號 / invalid camera index: {idx}")
            return False

        name = state["devices"][idx]
        try:
            filt, actual_name = SystemDeviceEnum().get_filter_by_index(VIDEO_CATEGORY, idx)
            name = actual_name or name
            cam = filt.QueryInterface(IAMCameraControl)
        except Exception as e:
            print(f"略過不支援 PTZ 的裝置 [{idx}] {name}: {e}")
            cam = None
            state["connected"] = None
            state["device_index"] = idx
            return False
        axis = {}
        for p in (PAN, TILT, ZOOM):
            try:
                mn, mx, st, de, ca = cam.GetRange(p)
                cur, _ = cam.Get(p)
                axis[p] = {"min": mn, "max": mx, "span": (mx - mn) or 1,
                           "pos": float(cur), "sent": int(cur)}
            except COMError:
                axis[p] = None

        # 有些虛擬 Webcam 會提供 IAMCameraControl，但沒有任何可用的 PTZ 軸。
        # 這種裝置也不能視為連線成功。
        if axis.get(PAN) is None and axis.get(TILT) is None:
            print(f"略過沒有 Pan/Tilt 控制的裝置 [{idx}] {name}")
            cam = None
            axis = {}
            state["connected"] = None
            state["device_index"] = idx
            return False

        state["connected"] = name
        state["device_index"] = idx
        save_cfg({"device_index": idx})
        print("已連線 / connected:", name)
        return True

    def apply(p):
        a = axis.get(p)
        if a is None or cam is None:
            return
        a["pos"] = clamp(a["pos"], a["min"], a["max"])
        iv = int(round(a["pos"]))
        if iv != a["sent"]:
            try:
                cam.Set(p, iv, FLAG_MANUAL)
                a["sent"] = iv
            except COMError:
                pass

    last_motion_write = 0.0

    def flush_motion(now, force=False):
        """Pan/Tilt 在同一批次寫入，並限制為 20Hz，降低斜向移動頓挫。"""
        nonlocal last_motion_write
        if not force and now - last_motion_write < UVC_WRITE_INTERVAL:
            return
        apply(PAN)
        apply(TILT)
        last_motion_write = now

    # 讀共用設定（GUI 調過的速度 / 回正角度會帶進來）
    cfg = load_cfg()
    if "speed" in cfg:
        state["speed"] = cfg["speed"]
    if "home_deg" in cfg:
        state["home_deg"] = cfg["home_deg"]
    raw_presets = cfg.get("presets", {})
    presets = {}
    if isinstance(raw_presets, dict):
        for key, value in raw_presets.items():
            try:
                number = int(key)
            except (TypeError, ValueError):
                continue
            if 1 <= number <= 20 and isinstance(value, dict):
                item = dict(value)
                item["name"] = str(item.get("name") or f"Preset {number}")[:32]
                presets[str(number)] = item
    state["presets"] = presets

    # 啟動：列出裝置並自動尋找真正支援 Pan/Tilt 的 Pocket 3。
    # 順序為 Pocket/Osmo 名稱 → 上次成功裝置 → 其他裝置。錯誤的舊編號不再
    # 阻止自動偵測，NDI Webcam、擷取卡與一般 Webcam 都會自動跳過。
    state["devices"] = read_devices()
    if state["devices"]:
        print("偵測到的視訊裝置 / video devices:")
        for i, name in enumerate(state["devices"]):
            print(f"  [{i}] {name}")
    else:
        print("找不到任何視訊裝置 / no video devices found")

    pref = cfg.get("device_index")
    if FIXED_DEVICE_INDEX is not None:
        candidates = [FIXED_DEVICE_INDEX] if 0 <= FIXED_DEVICE_INDEX < len(state["devices"]) else []
    elif FIXED_DEVICE_MATCH:
        needle = FIXED_DEVICE_MATCH.casefold()
        candidates = [i for i, name in enumerate(state["devices"]) if needle in name.casefold()]
    else:
        pocket_first = [
            i for i, name in enumerate(state["devices"])
            if "osmo" in name.lower() or "pocket" in name.lower() or "dji" in name.lower()
        ]
        candidates = pocket_first[:]
        if isinstance(pref, int) and 0 <= pref < len(state["devices"]) and pref not in candidates:
            candidates.append(pref)
        candidates.extend(i for i in range(len(state["devices"])) if i not in candidates)

    if (FIXED_DEVICE_INDEX is not None or FIXED_DEVICE_MATCH) and not candidates:
        wanted = f"index {FIXED_DEVICE_INDEX}" if FIXED_DEVICE_INDEX is not None else FIXED_DEVICE_MATCH
        print(f"找不到固定相機 / fixed camera not found: {wanted}")

    for idx in candidates:
        if connect(idx):
            break

    last_t = time.monotonic()
    applied = {PAN: 0.0, TILT: 0.0}   # 平滑後的實際速度
    locked_axis = None                 # 四向軸鎖定：PAN 或 TILT
    next_reconnect = 0.0
    next_health_check = 0.0
    health_failures = 0

    while True:
        now_t = time.monotonic()
        dt = now_t - last_t
        last_t = now_t
        if dt > 0.1:        # 卡頓後不要一次跳太多
            dt = 0.1

        if state["rescan_req"]:
            state["rescan_req"] = False
            state["devices"] = read_devices()
            if FIXED_DEVICE_INDEX is not None:
                connect(FIXED_DEVICE_INDEX)
            elif FIXED_DEVICE_MATCH:
                needle = FIXED_DEVICE_MATCH.casefold()
                matches = [i for i, name in enumerate(state["devices"]) if needle in name.casefold()]
                if matches:
                    connect(matches[0])
                else:
                    state["connected"] = None
                    state["device_index"] = None

        idx = state["connect_req"]
        if idx is not None:
            state["connect_req"] = None
            if FIXED_DEVICE_INDEX is None and not FIXED_DEVICE_MATCH:
                connect(idx)

        # USB 裝置被 Windows 重置後，自動重新列舉並恢復固定節點。
        if cam is None and (FIXED_DEVICE_INDEX is not None or FIXED_DEVICE_MATCH) and now_t >= next_reconnect:
            next_reconnect = now_t + 2.0
            state["devices"] = read_devices()
            reconnect_index = None
            if FIXED_DEVICE_INDEX is not None and 0 <= FIXED_DEVICE_INDEX < len(state["devices"]):
                reconnect_index = FIXED_DEVICE_INDEX
            elif FIXED_DEVICE_MATCH:
                needle = FIXED_DEVICE_MATCH.casefold()
                reconnect_index = next((i for i, name in enumerate(state["devices"]) if needle in name.casefold()), None)
            if reconnect_index is not None and connect(reconnect_index):
                health_failures = 0

        # 定期確認 COM/PTZ 介面仍存活；連續失敗代表相機或 USB 已重新連線。
        if cam is not None and now_t >= next_health_check:
            next_health_check = now_t + 1.5
            probe_axis = PAN if axis.get(PAN) else TILT
            try:
                cam.Get(probe_axis)
                health_failures = 0
            except Exception as exc:
                health_failures += 1
                if health_failures >= 2:
                    print(f"相機連線中斷，等待自動重連 / camera link lost: {exc}")
                    cam = None
                    axis = {}
                    state["connected"] = None
                    state["device_index"] = None
                    state["vel"] = {PAN: 0.0, TILT: 0.0, ZOOM: 0.0}
                    state["goto"] = None
                    next_reconnect = now_t + 1.0

        if cam:
            sp = state["speed"]
            # 手動移動時取消 preset 平滑移動
            if any(state["vel"].values()):
                state["goto"] = None
            # Pocket 3 的 UVC 絕對位置控制在雙軸同時更新時會頓挫，因此採用
            # 四向軸鎖定。一次手勢只允許 PAN 或 TILT；回中後才解除鎖定。
            raw_pan = shape(state["vel"][PAN])
            raw_tilt = shape(state["vel"][TILT])
            has_pan = abs(raw_pan) > 1e-4
            has_tilt = abs(raw_tilt) > 1e-4

            if not has_pan and not has_tilt:
                target_pan = 0.0
                target_tilt = 0.0
                if abs(applied[PAN]) < 1e-3 and abs(applied[TILT]) < 1e-3:
                    locked_axis = None
            else:
                desired_axis = PAN if abs(raw_pan) >= abs(raw_tilt) else TILT
                if locked_axis is None:
                    locked_axis = desired_axis
                elif locked_axis == PAN and not has_pan and has_tilt:
                    locked_axis = TILT
                    applied[PAN] = 0.0
                elif locked_axis == TILT and not has_tilt and has_pan:
                    locked_axis = PAN
                    applied[TILT] = 0.0

                if locked_axis == PAN:
                    target_pan, target_tilt = raw_pan, 0.0
                    applied[TILT] = 0.0
                else:
                    target_pan, target_tilt = 0.0, raw_tilt
                    applied[PAN] = 0.0
            resp = min(1.0, dt * 12)
            applied[PAN] += (target_pan - applied[PAN]) * resp
            applied[TILT] += (target_tilt - applied[TILT]) * resp
            if abs(applied[PAN]) < 1e-4 and abs(target_pan) < 1e-4:
                applied[PAN] = 0.0
            if abs(applied[TILT]) < 1e-4 and abs(target_tilt) < 1e-4:
                applied[TILT] = 0.0
            if axis.get(PAN) and abs(applied[PAN]) > 1e-3:
                axis[PAN]["pos"] += axis[PAN]["span"] * sp * dt * applied[PAN]
            if axis.get(TILT) and abs(applied[TILT]) > 1e-3:
                axis[TILT]["pos"] += axis[TILT]["span"] * sp * dt * applied[TILT]
            if abs(applied[PAN]) > 1e-3 or abs(applied[TILT]) > 1e-3:
                flush_motion(now_t)
            if axis.get(ZOOM) and state["vel"][ZOOM]:
                axis[ZOOM]["pos"] += axis[ZOOM]["span"] * ZOOM_SPEED * dt * state["vel"][ZOOM]
                apply(ZOOM)

            # nudge：點一下走一小步（全程 4%）
            for p in (PAN, TILT, ZOOM):
                nz = state["nudge"][p]
                if nz and axis.get(p):
                    state["nudge"][p] = 0.0
                    state["goto"] = None
                    axis[p]["pos"] += axis[p]["span"] * 0.04 * nz
                    if p in (PAN, TILT):
                        flush_motion(now_t, force=True)
                    else:
                        apply(p)

            if state["center_req"]:
                state["center_req"] = False
                hc = load_cfg()
                hp = hc.get("home_pan")
                ht = hc.get("home_tilt")
                g = {}
                if axis.get(PAN):
                    g["pan"] = float(hp) if hp is not None else \
                        (axis[PAN]["min"] + axis[PAN]["max"]) / 2
                if axis.get(TILT):
                    g["tilt"] = float(ht) if ht is not None else \
                        (axis[TILT]["min"] + axis[TILT]["max"]) / 2
                state["goto"] = g       # 連續送指令平滑移動到位（不必先動搖桿喚醒）

            if state["sethome_req"]:
                state["sethome_req"] = False
                upd = {}
                if axis.get(PAN):
                    upd["home_pan"] = axis[PAN]["sent"]
                if axis.get(TILT):
                    upd["home_tilt"] = axis[TILT]["sent"]
                save_cfg(upd)
                print("已設回正點 / home set:", upd)

            # preset 存 / 叫
            pr = state["preset_req"]
            if pr is not None:
                state["preset_req"] = None
                action, n, requested_name, requested_duration, requested_curve = pr
                key = str(n)
                if action == "save":
                    previous_name = presets.get(key, {}).get("name", f"Preset {n}")
                    presets[key] = {
                        "name": (requested_name or previous_name or f"Preset {n}")[:32],
                        "duration": clamp(float(requested_duration or DEFAULT_MOVE_DURATION), 0.5, 60.0),
                        "curve": requested_curve if requested_curve in MOVE_CURVES else DEFAULT_MOVE_CURVE,
                        **{nm: axis[p]["sent"]
                           for p, nm in ((PAN, "pan"), (TILT, "tilt"), (ZOOM, "zoom"))
                           if axis.get(p)},
                    }
                    save_cfg({"presets": presets})
                    print(f"已存 preset {key}：{presets[key]}")
                elif action == "recall" and key in presets:
                    state["goto"] = dict(presets[key])
                    state["goto"]["_duration"] = clamp(float(requested_duration or presets[key].get("duration", DEFAULT_MOVE_DURATION)), 0.5, 60.0)
                    curve = requested_curve or presets[key].get("curve", DEFAULT_MOVE_CURVE)
                    state["goto"]["_curve"] = curve if curve in MOVE_CURVES else DEFAULT_MOVE_CURVE
                elif action == "rename" and key in presets and requested_name:
                    presets[key]["name"] = requested_name[:32]
                    save_cfg({"presets": presets})
                elif action == "delete" and key in presets:
                    del presets[key]
                    save_cfg({"presets": presets})
                state["presets"] = presets

            # preset 平滑移動到位
            g = state["goto"]
            if g:
                plan = g.get("_plan")
                if not isinstance(plan, dict):
                    duration = clamp(float(g.get("_duration", DEFAULT_MOVE_DURATION)), 0.5, 60.0)
                    curve = g.get("_curve", DEFAULT_MOVE_CURVE)
                    if curve not in MOVE_CURVES:
                        curve = DEFAULT_MOVE_CURVE
                    start, target = {}, {}
                    for p, nm in ((PAN, "pan"), (TILT, "tilt"), (ZOOM, "zoom")):
                        if axis.get(p) and nm in g:
                            start[nm] = float(axis[p]["pos"])
                            target[nm] = clamp(float(g[nm]), axis[p]["min"], axis[p]["max"])
                    pan_distance = abs(target.get("pan", 0.0) - start.get("pan", 0.0)) / max(1.0, axis[PAN]["span"]) if axis.get(PAN) and "pan" in target else 0.0
                    tilt_distance = abs(target.get("tilt", 0.0) - start.get("tilt", 0.0)) / max(1.0, axis[TILT]["span"]) if axis.get(TILT) and "tilt" in target else 0.0
                    spatial_total = pan_distance + tilt_distance
                    plan = {
                        "started": now_t,
                        "duration": duration,
                        "curve": curve,
                        "start": start,
                        "target": target,
                        "pan_fraction": pan_distance / spatial_total if spatial_total > 1e-9 else 0.0,
                    }
                    g["_plan"] = plan

                duration = plan["duration"]
                elapsed = clamp(now_t - plan["started"], 0.0, duration)
                overall_t = elapsed / duration
                pan_fraction = plan["pan_fraction"]
                start, target, curve = plan["start"], plan["target"], plan["curve"]

                if axis.get(PAN) and "pan" in target:
                    pan_t = clamp(overall_t / pan_fraction, 0.0, 1.0) if pan_fraction > 1e-9 else 1.0
                    axis[PAN]["pos"] = start["pan"] + (target["pan"] - start["pan"]) * ease_value(curve, pan_t)
                if axis.get(TILT) and "tilt" in target:
                    tilt_fraction = 1.0 - pan_fraction
                    tilt_t = clamp((overall_t - pan_fraction) / tilt_fraction, 0.0, 1.0) if tilt_fraction > 1e-9 else 1.0
                    axis[TILT]["pos"] = start["tilt"] + (target["tilt"] - start["tilt"]) * ease_value(curve, tilt_t)
                if axis.get(ZOOM) and "zoom" in target:
                    axis[ZOOM]["pos"] = start["zoom"] + (target["zoom"] - start["zoom"]) * ease_value(curve, overall_t)
                    apply(ZOOM)

                flush_motion(now_t, force=overall_t >= 1.0)
                if overall_t >= 1.0:
                    state["goto"] = None

            for p in (PAN, TILT, ZOOM):
                state["pos"][p] = axis[p]["sent"] if axis.get(p) else None

        time.sleep(DT)


def uvc_preview_loop():
    """按需擷取目前 PTZ 裝置，避免 NDI 模式仍重複占用 UVC 串流。"""
    global preview_jpeg, obs_jpeg
    if PREVIEW_PROFILE == "off":
        state["preview_status"] = "UVC PREVIEW DISABLED · USE NDI"
        return
    if cv2 is None:
        state["preview_status"] = f"OPENCV ERROR: {CV2_IMPORT_ERROR}"
        return
    capture = None
    active_index = None
    retry_at = 0.0
    mode_index = 0
    black_frames = 0
    read_failures = 0
    frames = 0
    fps_started = time.monotonic()
    # 相機永遠只開一次 720p30；控制台與 OBS 共用這個擷取來源。
    capture_modes = [
        ("720p30", 1280, 720, 30),
        ("720p15", 1280, 720, 15),
        ("640x360p30", 640, 360, 30),
        ("640x360p15", 640, 360, 15),
    ]
    preview_quality, obs_quality = 65, 78
    preview_encode_at = 0.0
    while True:
        demanded = state.get("preview_enabled", True) and (
            state.get("preview_clients", 0) > 0 or state.get("obs_clients", 0) > 0
            or time.monotonic() < state.get("preview_demand_until", 0.0)
        )
        wanted = state.get("device_index") if state.get("connected") and demanded else None
        if wanted != active_index or capture is None:
            device_changed = wanted != active_index
            if capture is not None:
                capture.release()
                capture = None
            if device_changed:
                mode_index = 0
                black_frames = 0
                read_failures = 0
            active_index = wanted
            if wanted is None:
                with preview_lock:
                    preview_jpeg = b""
                    obs_jpeg = b""
                state["preview_fps"] = 0.0
                if not state.get("preview_enabled", True):
                    state["preview_status"] = "OBS PRIORITY · UVC RELEASED"
                else:
                    state["preview_status"] = "ON DEMAND · IDLE" if state.get("connected") else "NO CAMERA"
                time.sleep(0.25)
                continue
            now = time.monotonic()
            if now < retry_at:
                time.sleep(0.2)
                continue
            mode_name, capture_width, capture_height, capture_fps = capture_modes[mode_index]
            state["capture_mode"] = mode_name
            state["preview_status"] = f"OPENING UVC · {mode_name}"
            capture = cv2.VideoCapture(int(wanted), cv2.CAP_DSHOW)
            try:
                capture.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
                capture.set(cv2.CAP_PROP_FRAME_WIDTH, capture_width)
                capture.set(cv2.CAP_PROP_FRAME_HEIGHT, capture_height)
                capture.set(cv2.CAP_PROP_FPS, capture_fps)
                capture.set(cv2.CAP_PROP_BUFFERSIZE, 1)
            except Exception:
                pass
            if not capture.isOpened():
                capture.release()
                capture = None
                read_failures += 1
                if read_failures >= 2 and mode_index < len(capture_modes) - 1:
                    mode_index += 1
                    read_failures = 0
                retry_at = time.monotonic() + 2.0
                state["preview_status"] = f"UVC OPEN FAILED · TRY {capture_modes[mode_index][0]}"
                continue
            state["preview_status"] = f"DIRECT UVC ONLINE · {mode_name}"
            frames = 0
            fps_started = time.monotonic()

        ok, frame = capture.read()
        if not ok or frame is None:
            capture.release()
            capture = None
            read_failures += 1
            if read_failures >= 3 and mode_index < len(capture_modes) - 1:
                mode_index += 1
                read_failures = 0
            retry_at = time.monotonic() + 1.5
            state["preview_status"] = f"UVC FRAME LOST · TRY {capture_modes[mode_index][0]}"
            continue
        try:
            # 有些 Pocket 3 會接受不支援的格式，卻只回傳全黑影格。
            # 連續黑畫面時自動降低該台相機的 UVC 格式，不影響其他節點。
            mean_level = float(frame.mean())
            detail_level = float(frame.std())
            if mean_level < 3.0 and detail_level < 3.0:
                black_frames += 1
            else:
                black_frames = 0
                read_failures = 0
            if black_frames >= max(12, int(capture_fps * 0.7)):
                capture.release()
                capture = None
                black_frames = 0
                if mode_index < len(capture_modes) - 1:
                    mode_index += 1
                retry_at = time.monotonic() + 0.35
                state["preview_status"] = f"BLACK FRAME · FALLBACK {capture_modes[mode_index][0]}"
                continue
            now_frame = time.monotonic()
            high_frame = frame if frame.shape[1] == 1280 and frame.shape[0] == 720 else cv2.resize(frame, (1280, 720), interpolation=cv2.INTER_AREA)
            high_bytes = None
            low_bytes = None
            if state.get("obs_clients", 0) > 0:
                ok, encoded = cv2.imencode(".jpg", high_frame, [cv2.IMWRITE_JPEG_QUALITY, obs_quality])
                if ok:
                    high_bytes = encoded.tobytes()
            if (state.get("preview_clients", 0) > 0 or now_frame < state.get("preview_demand_until", 0.0)) and now_frame >= preview_encode_at:
                low_frame = cv2.resize(high_frame, (640, 360), interpolation=cv2.INTER_AREA)
                ok, encoded = cv2.imencode(".jpg", low_frame, [cv2.IMWRITE_JPEG_QUALITY, preview_quality])
                if ok:
                    low_bytes = encoded.tobytes()
                preview_encode_at = now_frame + 0.125
            if high_bytes is not None or low_bytes is not None:
                with preview_lock:
                    if high_bytes is not None:
                        obs_jpeg = high_bytes
                    if low_bytes is not None:
                        preview_jpeg = low_bytes
            frames += 1
            elapsed = time.monotonic() - fps_started
            if elapsed >= 1.0:
                state["preview_fps"] = frames / elapsed
                frames = 0
                fps_started = time.monotonic()
                state["preview_status"] = f"DIRECT UVC ONLINE · {capture_modes[mode_index][0]}"
        except Exception as exc:
            state["preview_status"] = f"PREVIEW ERROR: {exc}"
        time.sleep(0.005)


# ── 網頁面板 ─────────────────────────────────────────────────────────────────
PAGE = """<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1">
<title>CORSA Pocket 3 PTZ Control</title><style>
:root{color-scheme:dark;--red:#e63a32;--amber:#ffc34d;--green:#57c889;--cyan:#64c8e8;
  --panel:#202226;--edge:#070809;--screen:#071216;--text:#e7eaed;--muted:#8f989f}
*{box-sizing:border-box}
html,body{margin:0;min-height:100%;background:#0e1012;color:var(--text);
  font-family:Inter,"Segoe UI","Microsoft JhengHei",Arial,sans-serif;
  user-select:none;-webkit-user-select:none;overscroll-behavior:none}
body{padding:18px;background:radial-gradient(circle at 50% -30%,#363a3e 0,#15181a 42%,#090a0b 100%)}
button,select,input{font:inherit}
.console{max-width:1120px;margin:auto;border:1px solid #41464a;border-radius:18px 18px 28px 28px;
  padding:18px 22px 24px;background:linear-gradient(155deg,#35383b 0,#232629 32%,#17191b 100%);
  box-shadow:0 26px 70px #000c,inset 0 1px #ffffff25,inset 0 -3px #050607;position:relative;overflow:hidden}
.console:before{content:"";position:absolute;inset:0;pointer-events:none;opacity:.17;
  background:repeating-linear-gradient(90deg,#fff0 0,#fff 1px,#fff0 2px,#fff0 5px)}
.topline{position:relative;display:flex;align-items:center;justify-content:space-between;gap:18px;margin-bottom:12px}
.brand{font-size:18px;font-weight:750;letter-spacing:.16em;color:#f0f0f0;white-space:nowrap}
.brand b{color:var(--red)}.model{font-size:10px;color:#969da2;letter-spacing:.18em;margin-top:3px}
.online{display:flex;align-items:center;gap:8px;font-size:11px;color:#aeb5ba;text-transform:uppercase;letter-spacing:.12em}
.lamp{width:9px;height:9px;border-radius:50%;background:#542b2a;box-shadow:inset 0 0 3px #000}
.online.ok .lamp{background:var(--green);box-shadow:0 0 9px #57c889aa,inset 0 0 2px #fff}
.screenbank{position:relative;display:grid;grid-template-columns:1.4fr .9fr .9fr;gap:9px;padding:10px;
  border:1px solid #090a0b;border-radius:10px;background:#111315;box-shadow:inset 0 2px 8px #000,0 1px #ffffff18}
.screen{min-height:82px;padding:10px 12px;border:1px solid #26363a;border-radius:4px;
  background:linear-gradient(#0c1b20,#061014);box-shadow:inset 0 0 18px #000a,0 0 8px #42c2df12}
.screenlabel{font-size:9px;color:#66838c;letter-spacing:.18em;text-transform:uppercase;margin-bottom:7px}
.screenmain{font-family:Consolas,monospace;color:#a7eaff;font-size:16px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.screenmain.small{font-size:13px}.screenmeta{font:10px Consolas,monospace;color:#5d8a96;margin-top:6px}
.devicebar{position:relative;display:flex;gap:8px;margin:10px 0 14px}
.devicebar select{min-width:0;flex:1;color:#d7dde1;background:#191b1e;border:1px solid #44494d;border-radius:4px;padding:7px 10px;font-size:11px}
.deck{position:relative;display:grid;grid-template-columns:1.18fr .86fr 1fr;gap:14px}
.module{background:linear-gradient(145deg,#292c2f,#17191b);border:1px solid #0a0b0c;border-radius:9px;
  padding:13px;box-shadow:inset 0 1px #ffffff1c,0 5px 12px #0007;min-width:0}
.moduletitle{display:flex;align-items:center;justify-content:space-between;font-size:9px;color:#92999e;
  letter-spacing:.18em;text-transform:uppercase;margin:0 2px 11px}.moduletitle span:last-child{color:#62686c}
.joystickwell{width:min(280px,76vw);aspect-ratio:1;margin:0 auto 12px;border-radius:50%;position:relative;
  padding:24px;background:radial-gradient(circle,#24282a 0 42%,#101214 72%,#050607 100%);
  border:2px solid #050607;box-shadow:inset 0 8px 18px #000,0 2px #555}
.joybase{height:100%;border-radius:50%;position:relative;touch-action:none;
  background:radial-gradient(circle,#24282b 0,#16191b 56%,#0b0d0e 100%);border:1px solid #3b4043;
  box-shadow:inset 0 0 25px #000b}
.joybase:before,.joybase:after{content:"";position:absolute;background:#59606455}
.joybase:before{left:50%;top:9%;bottom:9%;width:1px}.joybase:after{top:50%;left:9%;right:9%;height:1px}
.ring{position:absolute;inset:18%;border:1px dashed #565d6160;border-radius:50%;pointer-events:none}
.knob{position:absolute;width:76px;height:76px;left:calc(50% - 38px);top:calc(50% - 38px);border-radius:50%;
  background:radial-gradient(circle at 40% 28%,#6d7276 0,#303438 30%,#111315 70%);
  border:2px solid #090a0b;box-shadow:0 10px 14px #000c,inset 0 2px #ffffff45;transition:.08s;pointer-events:none}
.knob:after{content:"";position:absolute;inset:23px;border-radius:50%;background:#151719;border:1px solid #767b7e}
.knob.drag{transition:none;box-shadow:0 13px 18px #000,inset 0 2px #ffffff55}
.coords{display:flex;justify-content:center;gap:18px;color:#697176;font:10px Consolas,monospace}
.controls{display:grid;gap:11px}.controlgroup{padding:10px;background:#111315;border:1px solid #33373a;border-radius:6px}
.groupname{font-size:9px;color:#787f84;letter-spacing:.16em;margin-bottom:9px}
.hwbtn{border:1px solid #090a0b;border-radius:5px;color:#cfd3d6;background:linear-gradient(#44484b,#24272a 50%,#181a1c);
  box-shadow:inset 0 1px #ffffff2d,0 3px 5px #0009;min-height:41px;padding:7px 9px;cursor:pointer;touch-action:none;
  font-size:10px;font-weight:700;letter-spacing:.05em;text-shadow:0 1px #000}
.hwbtn:hover{filter:brightness(1.12)}.hwbtn:active,.hwbtn.active{transform:translateY(2px);box-shadow:inset 0 2px 5px #000;background:#25282a}
.hwbtn.red{background:linear-gradient(#87342f,#4f1917);color:#ffd6d3}.hwbtn.red.armed{background:var(--red);box-shadow:0 0 14px #e63a3299,inset 0 1px #fff5}
.hwbtn.amber:active{background:#78551d}.hwbtn.green:active{background:#216347}
.twocol{display:grid;grid-template-columns:1fr 1fr;gap:7px}.nudge{display:grid;grid-template-columns:repeat(3,1fr);gap:5px}
.nudge .blank{visibility:hidden}.nudge .hwbtn{min-height:36px;font-size:15px}
.speedrow{display:grid;grid-template-columns:72px 1fr;align-items:center;gap:10px}
.dial{--a:-108deg;width:66px;height:66px;border-radius:50%;position:relative;margin:auto;
  background:conic-gradient(from 225deg,var(--amber) 0deg,360deg,#42474a 0);
  box-shadow:0 4px 8px #000b;border:2px solid #090a0b}
.dial:before{content:"";position:absolute;inset:6px;border-radius:50%;background:radial-gradient(circle at 38% 30%,#5b6063,#1d2022 68%);border:1px solid #666c70}
.dial:after{content:"";position:absolute;left:31px;top:8px;width:3px;height:23px;border-radius:2px;background:#f4f4f4;
  transform-origin:1.5px 25px;transform:rotate(var(--a));box-shadow:0 0 4px #fff8}
.speedvalue{text-align:center;margin-top:3px;font:11px Consolas,monospace;color:var(--amber)}
input[type=range]{width:100%;accent-color:var(--amber)}
.zoomgrid{display:grid;grid-template-columns:1fr 76px;gap:10px;align-items:stretch}
.zoomrocker{height:166px;border-radius:10px;padding:0;overflow:hidden;display:grid;grid-template-rows:1fr 1fr;
  border:2px solid #08090a;background:#111;box-shadow:inset 0 0 12px #000,0 4px 8px #0008}
.zoomhalf{border:0;color:#d8dcdf;background:linear-gradient(#4b4f52,#25282a);font-size:11px;font-weight:750;cursor:pointer;touch-action:none}
.zoomhalf:first-child{border-bottom:1px solid #08090a}.zoomhalf:active{background:linear-gradient(#2a2d2f,#17191b);color:#fff}
.zoommark{display:block;font-size:22px;line-height:22px}.presetgrid{display:grid;grid-template-columns:repeat(4,1fr);gap:7px}
.preset{min-height:45px;font-size:15px}.preset.recalled{outline:1px solid var(--green);color:#b8ffdc;box-shadow:0 0 10px #57c88933}
.storeline{display:grid;grid-template-columns:1fr 2fr;gap:7px;margin-bottom:9px}.hint{font-size:9px;color:#686f73;line-height:1.45;margin-top:9px}
.footer{position:relative;display:flex;justify-content:space-between;gap:12px;margin-top:13px;font-size:9px;color:#5f666a;letter-spacing:.12em}
@media(max-width:820px){body{padding:8px}.console{padding:13px;border-radius:12px}.screenbank{grid-template-columns:1fr 1fr}.screen:first-child{grid-column:1/-1}
  .deck{grid-template-columns:1fr 1fr}.module.motion{grid-column:1/-1}.module.motion .controls{grid-template-columns:1fr 1fr}.joystickwell{width:min(250px,72vw)}}
@media(max-width:540px){.topline{align-items:flex-start}.brand{font-size:14px}.screenbank,.deck{grid-template-columns:1fr}.screen:first-child,.module.motion{grid-column:auto}
  .module.motion .controls{grid-template-columns:1fr}.joystickwell{width:min(260px,78vw)}.footer{display:block;line-height:1.8}}
</style></head><body><main class="console">
<header class="topline"><div><div class="brand"><b>●</b> CORSA PTZ CONTROL</div><div class="model">POCKET 3 · LAN REMOTE PANEL</div></div>
<div class="online" id="online"><span class="lamp"></span><span id="netText">CONNECTING</span></div></header>

<section class="screenbank">
  <div class="screen"><div class="screenlabel">Camera / Device</div><div class="screenmain" id="camName">SEARCHING…</div><div class="screenmeta" id="statusText">Waiting for control server</div></div>
  <div class="screen"><div class="screenlabel">Position</div><div class="screenmain small" id="posText">P — &nbsp; T —</div><div class="screenmeta" id="zoomText">ZOOM —</div></div>
  <div class="screen"><div class="screenlabel">Motion</div><div class="screenmain" id="speedScreen">30%</div><div class="screenmeta">PAN / TILT SPEED</div></div>
</section>
<div class="devicebar"><select id="dev"></select><button class="hwbtn" id="rescan">↻ RESCAN</button></div>

<section class="deck">
  <div class="module motion"><div class="moduletitle"><span>Pan / Tilt</span><span>Variable speed joystick</span></div>
    <div class="joystickwell"><div class="joybase" id="base"><div class="ring"></div><div class="knob" id="knob"></div></div></div>
    <div class="coords"><span id="panLive">PAN +0.00</span><span id="tiltLive">TILT +0.00</span></div>
  </div>

  <div class="module"><div class="moduletitle"><span>Motion Control</span><span>Fine / Home</span></div><div class="controls">
    <div class="controlgroup"><div class="groupname">Reference</div><div class="twocol">
      <button class="hwbtn green" id="center">HOME</button><button class="hwbtn red" id="sethome">SET HOME</button></div></div>
    <div class="controlgroup"><div class="groupname">Fine Nudge</div><div class="nudge">
      <span class="blank"></span><button class="hwbtn" data-nudge="tilt=1">▲</button><span class="blank"></span>
      <button class="hwbtn" data-nudge="pan=-1">◀</button><button class="hwbtn" id="stopAll">■</button><button class="hwbtn" data-nudge="pan=1">▶</button>
      <span class="blank"></span><button class="hwbtn" data-nudge="tilt=-1">▼</button><span class="blank"></span></div></div>
    <div class="controlgroup"><div class="groupname">Sensitivity</div><div class="speedrow">
      <div><div class="dial" id="dial"></div><div class="speedvalue"><span id="speedValue">30</span>%</div></div>
      <input type="range" id="speed" min="5" max="120" value="30"></div></div>
  </div></div>

  <div class="module"><div class="moduletitle"><span>Zoom / Presets</span><span>Live control</span></div>
    <div class="zoomgrid"><div>
      <div class="storeline"><button class="hwbtn red" id="store">STORE</button><button class="hwbtn amber" id="presetLabel">RECALL PRESET</button></div>
      <div class="presetgrid" id="presetGrid"></div><div class="hint">STORE：先按紅色 STORE，再按 1–8 儲存。直接按數字即叫出機位。</div>
    </div><div class="zoomrocker">
      <button class="zoomhalf" data-zoom="1"><span class="zoommark">＋</span>TELE</button>
      <button class="zoomhalf" data-zoom="-1">WIDE<span class="zoommark">−</span></button>
    </div></div>
  </div>
</section>
<footer class="footer"><span>ARROW KEYS: PAN / TILT · + −: ZOOM · SPACE: STOP</span><span>POCKET 3 UVC · TCP 8723</span></footer>
</main><script>
const $=id=>document.getElementById(id);
const api=p=>fetch(p,{cache:'no-store'}).catch(()=>null);
const clamp=(v,a,b)=>Math.max(a,Math.min(b,v));

// Broadcast-style joystick
const base=$('base'),knob=$('knob');
let dragging=false,pan=0,tilt=0,lastSent=0;
function place(dx,dy){knob.style.transform=`translate(${dx}px,${dy}px)`;
  $('panLive').textContent=`PAN ${pan>=0?'+':''}${pan.toFixed(2)}`;
  $('tiltLive').textContent=`TILT ${tilt>=0?'+':''}${tilt.toFixed(2)}`;}
function setVector(x,y){const r=base.getBoundingClientRect(),cx=r.left+r.width/2,cy=r.top+r.height/2;
  let dx=x-cx,dy=y-cy,R=r.width/2-48,d=Math.hypot(dx,dy);if(d>R){dx*=R/d;dy*=R/d;}
  pan=clamp(dx/R,-1,1);tilt=clamp(-dy/R,-1,1);place(dx,dy);}
function sendVector(force=false){const now=Date.now();if(!force&&now-lastSent<38)return;lastSent=now;
  api(`/vel?pan=${pan.toFixed(3)}&tilt=${tilt.toFixed(3)}`);}
function joyStart(e){dragging=true;knob.classList.add('drag');base.setPointerCapture(e.pointerId);setVector(e.clientX,e.clientY);sendVector(true);}
function joyMove(e){if(!dragging)return;e.preventDefault();setVector(e.clientX,e.clientY);sendVector();}
function stopMotion(){dragging=false;knob.classList.remove('drag');pan=tilt=0;place(0,0);api('/vel?pan=0&tilt=0&zoom=0');}
base.addEventListener('pointerdown',joyStart);base.addEventListener('pointermove',joyMove);
['pointerup','pointercancel'].forEach(ev=>base.addEventListener(ev,stopMotion));
window.addEventListener('pointerup',()=>{if(dragging)stopMotion()});

// Hardware buttons
$('center').onclick=()=>api('/center');
$('sethome').onclick=()=>api('/sethome');
$('stopAll').onclick=stopMotion;
document.querySelectorAll('[data-nudge]').forEach(b=>b.onclick=()=>api('/nudge?'+b.dataset.nudge));
document.querySelectorAll('[data-zoom]').forEach(b=>{const v=b.dataset.zoom;
  b.addEventListener('pointerdown',e=>{e.preventDefault();b.classList.add('active');api(`/vel?zoom=${v}`)});
  ['pointerup','pointerleave','pointercancel'].forEach(ev=>b.addEventListener(ev,()=>{b.classList.remove('active');api('/vel?zoom=0')}));});

// Speed dial
const speed=$('speed');
function paintSpeed(v,send=true){v=Number(v);$('speedValue').textContent=v;$('speedScreen').textContent=v+'%';
  $('dial').style.setProperty('--a',(-135+(v-5)/115*270)+'deg');if(send)api(`/speed?v=${v/100}`);}
speed.oninput=()=>paintSpeed(speed.value);paintSpeed(30,false);

// Safe preset store/recall
let storeArmed=false,storeTimer=null,lastPreset=0;
for(let i=1;i<=8;i++){const b=document.createElement('button');b.className='hwbtn preset';b.textContent=i;b.dataset.preset=i;
  b.onclick=()=>{if(storeArmed){api(`/preset/save?n=${i}`);disarmStore();$('presetLabel').textContent=`SAVED ${i}`;}
    else{api(`/preset/recall?n=${i}`);lastPreset=i;document.querySelectorAll('.preset').forEach(x=>x.classList.toggle('recalled',Number(x.dataset.preset)===i));$('presetLabel').textContent=`PRESET ${i}`;}};
  $('presetGrid').appendChild(b);}
function disarmStore(){storeArmed=false;$('store').classList.remove('armed');$('store').textContent='STORE';clearTimeout(storeTimer);}
$('store').onclick=()=>{storeArmed=!storeArmed;$('store').classList.toggle('armed',storeArmed);$('store').textContent=storeArmed?'SELECT 1–8':'STORE';
  clearTimeout(storeTimer);if(storeArmed)storeTimer=setTimeout(disarmStore,5000);};

// Camera list
const dev=$('dev');let devKey='';
dev.onchange=()=>{api(`/connect?index=${dev.value}`);$('statusText').textContent='Connecting to selected camera…';};
$('rescan').onclick=async()=>{await api('/rescan');devKey='';};
function updateDevices(list,sel){const key=(list||[]).join('|');if(key!==devKey){devKey=key;dev.innerHTML='';
  if(!(list||[]).length){const o=document.createElement('option');o.textContent='找不到視訊裝置 / NO CAMERA';dev.appendChild(o);}
  else list.forEach((name,i)=>{const o=document.createElement('option');o.value=i;o.textContent=`CAM ${i+1} · ${name}`;dev.appendChild(o);});}
  if(document.activeElement!==dev&&sel!=null&&sel>=0)dev.value=sel;}

// Keyboard control for director use
const held=new Set();
function keyVelocity(){let p=0,t=0,z=0;if(held.has('ArrowLeft'))p=-1;if(held.has('ArrowRight'))p=1;
  if(held.has('ArrowUp'))t=1;if(held.has('ArrowDown'))t=-1;if(held.has('+')||held.has('='))z=1;if(held.has('-')||held.has('_'))z=-1;
  api(`/vel?pan=${p}&tilt=${t}&zoom=${z}`);}
window.addEventListener('keydown',e=>{if(['SELECT','INPUT'].includes(document.activeElement.tagName))return;
  if(['ArrowLeft','ArrowRight','ArrowUp','ArrowDown','+','=','-','_'].includes(e.key)){e.preventDefault();if(!held.has(e.key)){held.add(e.key);keyVelocity();}}
  if(e.code==='Space'){e.preventDefault();held.clear();stopMotion();}});
window.addEventListener('keyup',e=>{if(held.delete(e.key))keyVelocity();});window.addEventListener('blur',()=>{held.clear();stopMotion();});

async function poll(){try{const r=await fetch('/status',{cache:'no-store'});if(!r.ok)throw 0;const d=await r.json();
  updateDevices(d.devices,d.device_index);const ok=!!d.connected;$('online').classList.toggle('ok',ok);$('netText').textContent=ok?'ON AIR CONTROL':'NO CAMERA';
  $('camName').textContent=d.connected||'NOT CONNECTED';$('statusText').textContent=ok?'UVC control link established':'Select a PTZ-capable camera above';
  $('posText').textContent=`P ${d.pos.pan??'—'}   T ${d.pos.tilt??'—'}`;$('zoomText').textContent=`ZOOM ${d.pos.zoom??'—'}`;
  const v=Math.round((d.speed||.3)*100);if(document.activeElement!==speed){speed.value=v;paintSpeed(v,false);}
}catch(e){$('online').classList.remove('ok');$('netText').textContent='SERVER OFFLINE';$('camName').textContent='CONNECTION LOST';$('statusText').textContent='PTZ server is not responding';}}
setInterval(poll,700);poll();
</script></body></html>"""


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_preview(self):
        if PREVIEW_PROFILE == "off" or not state.get("preview_enabled", True):
            return self._send(503, b"UVC preview disabled; select an NDI source", "text/plain")
        state["preview_clients"] += 1
        state["preview_demand_until"] = time.monotonic() + 10.0
        try:
            deadline = time.monotonic() + 4.0
            ready = False
            while time.monotonic() < deadline:
                with preview_lock:
                    ready = bool(preview_jpeg)
                if ready:
                    break
                time.sleep(0.1)
            if not ready:
                return self._send(503, b"UVC preview not ready", "text/plain")
            self.send_response(200)
            self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
            self.end_headers()
            while True:
                with preview_lock:
                    frame = preview_jpeg
                if frame:
                    self.wfile.write(b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: " + str(len(frame)).encode() + b"\r\n\r\n")
                    self.wfile.write(frame + b"\r\n")
                    self.wfile.flush()
                time.sleep(0.125 if PREVIEW_PROFILE == "stable" else 0.067)
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            state["preview_clients"] = max(0, state["preview_clients"] - 1)

    def _send_obs(self):
        if PREVIEW_PROFILE == "off" or not state.get("preview_enabled", True):
            return self._send(503, b"OBS video output disabled", "text/plain")
        state["obs_clients"] += 1
        try:
            deadline = time.monotonic() + 5.0
            while time.monotonic() < deadline:
                with preview_lock:
                    ready = bool(obs_jpeg)
                if ready:
                    break
                time.sleep(0.05)
            if not ready:
                return self._send(503, b"OBS 720p output not ready", "text/plain")
            self.send_response(200)
            self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
            self.end_headers()
            last = None
            while True:
                with preview_lock:
                    frame = obs_jpeg
                if frame and frame is not last:
                    self.wfile.write(b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: " + str(len(frame)).encode() + b"\r\n\r\n")
                    self.wfile.write(frame + b"\r\n")
                    self.wfile.flush()
                    last = frame
                time.sleep(1.0 / 30.0)
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            state["obs_clients"] = max(0, state["obs_clients"] - 1)

    def do_GET(self):
        u = urlparse(self.path)
        q = parse_qs(u.query)

        if u.path == "/":
            try:
                with open(PANEL_FILE, "rb") as panel:
                    body = panel.read()
            except OSError:
                body = PAGE.encode("utf-8")
            return self._send(200, body, "text/html; charset=utf-8")

        if u.path == "/preview.mjpg":
            return self._send_preview()
        if u.path == "/obs.mjpg":
            return self._send_obs()
        if u.path == "/obs":
            page = b'<!doctype html><html><head><meta charset="utf-8"><style>html,body{margin:0;width:100%;height:100%;overflow:hidden;background:#000}img{width:100%;height:100%;object-fit:contain}</style></head><body><img src="/obs.mjpg"></body></html>'
            return self._send(200, page, "text/html; charset=utf-8")
        if u.path == "/preview.jpg":
            if PREVIEW_PROFILE == "off" or not state.get("preview_enabled", True):
                return self._send(503, b"UVC preview disabled; select an NDI source", "text/plain")
            state["preview_demand_until"] = time.monotonic() + 10.0
            with preview_lock:
                body = preview_jpeg
            if not body:
                return self._send(503, b"preview not ready", "text/plain")
            return self._send(200, body, "image/jpeg")

        if u.path == "/vel":
            for name, p in (("pan", PAN), ("tilt", TILT), ("zoom", ZOOM)):
                if name in q:
                    try:
                        state["vel"][p] = clamp(float(q[name][0]), -1, 1)
                    except ValueError:
                        pass
        elif u.path == "/stop":
            state["vel"] = {PAN: 0.0, TILT: 0.0, ZOOM: 0.0}
            state["goto"] = None
            state["preset_req"] = None
        elif u.path == "/nudge":
            for name, p in (("pan", PAN), ("tilt", TILT), ("zoom", ZOOM)):
                if name in q:
                    try:
                        state["nudge"][p] += float(q[name][0])
                    except ValueError:
                        pass
        elif u.path == "/center":
            state["center_req"] = True
        elif u.path == "/sethome":
            state["sethome_req"] = True
        elif u.path == "/preset/recall":
            try:
                number = int(q.get("n", ["1"])[0])
                duration, curve = move_settings(q)
                if 1 <= number <= 20:
                    state["preset_req"] = ("recall", number, None, duration, curve)
            except ValueError:
                pass
        elif u.path == "/preset/save":
            try:
                number = int(q.get("n", ["1"])[0])
                name = q.get("name", [""])[0].strip()[:32]
                duration, curve = move_settings(q)
                if 1 <= number <= 20:
                    state["preset_req"] = ("save", number, name, duration, curve)
            except ValueError:
                pass
        elif u.path == "/preset/rename":
            try:
                number = int(q.get("n", ["1"])[0])
                name = q.get("name", [""])[0].strip()[:32]
                if 1 <= number <= 20 and name:
                    state["preset_req"] = ("rename", number, name, None, None)
            except ValueError:
                pass
        elif u.path == "/preset/delete":
            try:
                number = int(q.get("n", ["1"])[0])
                if 1 <= number <= 20:
                    state["preset_req"] = ("delete", number, None, None, None)
            except ValueError:
                pass
        elif u.path == "/goto":
            target = {}
            for name in ("pan", "tilt", "zoom"):
                if name in q:
                    try:
                        target[name] = float(q[name][0])
                    except ValueError:
                        pass
            if target:
                duration, curve = move_settings(q)
                state["vel"] = {PAN: 0.0, TILT: 0.0, ZOOM: 0.0}
                target["_duration"] = duration
                target["_curve"] = curve
                state["goto"] = target
        elif u.path == "/speed":
            try:
                state["speed"] = clamp(float(q.get("v", ["0.3"])[0]), 0.05, 1.5)
                save_cfg({"speed": state["speed"]})
            except ValueError:
                pass
        elif u.path == "/preview/control":
            enabled = q.get("enabled", ["1"])[0].strip().lower() not in ("0", "false", "off", "no")
            state["preview_enabled"] = enabled
            if not enabled:
                state["preview_demand_until"] = 0.0
                state["preview_clients"] = 0
                state["preview_status"] = "OBS PRIORITY · RELEASING UVC"
        elif u.path == "/connect":
            if FIXED_DEVICE_INDEX is None and not FIXED_DEVICE_MATCH:
                try:
                    state["connect_req"] = int(q.get("index", ["0"])[0])
                except ValueError:
                    pass
        elif u.path == "/rescan":
            state["rescan_req"] = True
        elif u.path == "/status":
            body = json.dumps({
                "connected": state["connected"],
                "devices": state["devices"],
                "device_index": state["device_index"],
                "fixed_device_index": FIXED_DEVICE_INDEX,
                "fixed_device_match": FIXED_DEVICE_MATCH,
                "node_port": NODE_PORT,
                "node_label": NODE_LABEL,
                "speed": state["speed"],
                "preview_status": state["preview_status"],
                "preview_fps": round(state["preview_fps"], 1),
                "preview_profile": PREVIEW_PROFILE,
                "preview_enabled": bool(state.get("preview_enabled", True)),
                "obs_clients": state.get("obs_clients", 0),
                "obs_output": f"http://127.0.0.1:{NODE_PORT}/obs",
                "capture_mode": state.get("capture_mode", "WAITING"),
                "moving": state["goto"] is not None or state["preset_req"] is not None,
                "pos": {"pan": state["pos"][PAN], "tilt": state["pos"][TILT], "zoom": state["pos"][ZOOM]},
                "presets": [
                    {
                        "n": number,
                        "saved": str(number) in state["presets"],
                        "name": state["presets"].get(str(number), {}).get("name", f"Preset {number}"),
                        "duration": state["presets"].get(str(number), {}).get("duration", DEFAULT_MOVE_DURATION),
                        "curve": state["presets"].get(str(number), {}).get("curve", DEFAULT_MOVE_CURVE),
                    }
                    for number in range(1, 21)
                ],
            }).encode("utf-8")
            return self._send(200, body, "application/json")
        else:
            return self._send(404, b"not found", "text/plain")

        return self._send(200, b'{"ok":true}', "application/json")


def get_lan_ipv4_addresses():
    """回傳可供導播台連線的 IPv4 位址，排除 loopback 與 APIPA。"""
    found = set()

    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            found.add(info[4][0])
    except OSError:
        pass

    # 取得 Windows 目前實際路由會使用的本機位址；不會送出資料。
    try:
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        probe.connect(("192.0.2.1", 9))
        found.add(probe.getsockname()[0])
        probe.close()
    except OSError:
        pass

    return sorted(
        ip for ip in found
        if not ip.startswith("127.") and not ip.startswith("169.254.")
    )


def main():
    global CONFIG, NODE_PORT, NODE_LABEL, FIXED_DEVICE_INDEX, FIXED_DEVICE_MATCH, PREVIEW_PROFILE
    parser = argparse.ArgumentParser(description="Pocket 3 UVC PTZ LAN controller")
    parser.add_argument(
        "--host", default=os.environ.get("PTZ_BIND", DEFAULT_HOST),
        help="監聽位址，預設 0.0.0.0（允許區網控制）",
    )
    parser.add_argument(
        "--port", type=int, default=int(os.environ.get("PTZ_PORT", PORT)),
        help="HTTP 連接埠，預設 8723",
    )
    parser.add_argument(
        "--device-index", type=int, default=None,
        help="固定使用指定的 DirectShow 相機編號；固定模式下網頁不能切換裝置",
    )
    parser.add_argument(
        "--device-match", default="",
        help="固定使用名稱包含此文字的相機，例如 OsmoPocket3",
    )
    parser.add_argument(
        "--config", default="",
        help="獨立設定檔名稱；相對路徑會放在 EXE 旁",
    )
    parser.add_argument(
        "--label", default="",
        help="節點顯示名稱，例如 CAMERA 1 / Pocket4P",
    )
    parser.add_argument(
        "--preview-profile", choices=("quality", "stable", "off"), default="quality",
        help="quality=高畫質、stable=多機低頻寬、off=只使用 NDI",
    )
    parser.add_argument("--parent-pid", type=int, default=0, help=argparse.SUPPRESS)
    args = parser.parse_args()

    if args.parent_pid:
        threading.Thread(target=watch_parent_process, args=(args.parent_pid,), name="parent-watch", daemon=True).start()

    NODE_PORT = args.port
    NODE_LABEL = args.label.strip()
    PREVIEW_PROFILE = args.preview_profile
    FIXED_DEVICE_INDEX = args.device_index
    FIXED_DEVICE_MATCH = args.device_match.strip()
    config_name = args.config.strip() or ("ptz-config.json" if args.port == PORT and FIXED_DEVICE_INDEX is None and not FIXED_DEVICE_MATCH else f"ptz-config-{args.port}.json")
    CONFIG = config_name if os.path.isabs(config_name) else os.path.join(APP_DIR, config_name)

    threading.Thread(target=uvc_preview_loop, name="uvc-preview", daemon=True).start()

    threading.Thread(target=control_loop, daemon=True).start()
    try:
        srv = ThreadingHTTPServer((args.host, args.port), Handler)
    except OSError as exc:
        print(f"無法啟動 PTZ 伺服器：{exc}")
        print("請確認連接埠沒有被其他程式占用，或改用 --port 8724。")
        raise SystemExit(1)

    print("=" * 68)
    print("Pocket 3 PTZ 區網控制已啟動")
    if NODE_LABEL:
        print(f"節點名稱 / node: {NODE_LABEL}")
    if FIXED_DEVICE_MATCH:
        print(f"固定相機名稱 / fixed camera: {FIXED_DEVICE_MATCH}")
    elif FIXED_DEVICE_INDEX is not None:
        print(f"固定相機編號 / fixed camera index: {FIXED_DEVICE_INDEX}")
    print(f"獨立設定檔 / config: {os.path.basename(CONFIG)}")
    print(f"預覽模式 / preview: {PREVIEW_PROFILE}")
    print(f"本機控制：http://127.0.0.1:{args.port}/")
    lan_ips = get_lan_ipv4_addresses()
    if lan_ips:
        for ip in lan_ips:
            print(f"導播台控制：http://{ip}:{args.port}/")
    else:
        print("未偵測到區網 IPv4；請用 ipconfig 查詢送畫面電腦的 IPv4 位址。")
    print("導播台 OBS：停駐視窗 → 自訂瀏覽器停駐視窗 → 貼上導播台控制網址")
    print("若導播台打不開，請執行 manage-firewall.bat 並選擇 Allow。")
    print("按 Ctrl+C 停止。")
    print("=" * 68)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n正在停止 PTZ 控制伺服器……")
    finally:
        srv.server_close()


if __name__ == "__main__":
    main()
