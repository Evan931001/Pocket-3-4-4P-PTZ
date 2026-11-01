#!/usr/bin/env python3
"""Detect up to five DJI UVC cameras and launch one isolated PTZ node per camera."""

import argparse
import os
import re
import socket
import subprocess
import sys
import time
import json
import urllib.request


VIDEO_CATEGORY = "{860BB310-5D01-11D0-BD3B-00A0C911CE86}"
DJI_WORDS = ("dji", "osmo", "pocket")
SOURCE_DIR = os.path.dirname(os.path.abspath(__file__))
APP_DIR = os.path.dirname(sys.executable) if getattr(sys, "frozen", False) else SOURCE_DIR


def list_video_devices():
    from pygrabber.dshow_graph import SystemDeviceEnum
    return SystemDeviceEnum().get_available_filters(VIDEO_CATEGORY)


def lan_ipv4_addresses():
    found = set()
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            found.add(info[4][0])
    except OSError:
        pass
    try:
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        probe.connect(("192.0.2.1", 9))
        found.add(probe.getsockname()[0])
        probe.close()
    except OSError:
        pass
    return sorted(ip for ip in found if not ip.startswith("127.") and not ip.startswith("169.254."))


def port_available(port):
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        probe.bind(("127.0.0.1", port))
        return True
    except OSError:
        return False
    finally:
        probe.close()


def safe_name(value):
    cleaned = re.sub(r"[^A-Za-z0-9_-]+", "-", value).strip("-")
    return cleaned[:40] or "DJI"


def node_command():
    if getattr(sys, "frozen", False):
        node = os.path.join(APP_DIR, "Pocket3-PTZ-Node.exe")
        if not os.path.isfile(node):
            raise FileNotFoundError(f"Pocket3-PTZ-Node.exe not found beside {sys.executable}")
        return [node]
    return [sys.executable, os.path.join(SOURCE_DIR, "ptz-server.py")]


HTTP = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def read_node_status(port):
    request = urllib.request.Request(f"http://127.0.0.1:{port}/status", headers={"Connection": "close"})
    with HTTP.open(request, timeout=0.7) as response:
        return json.loads(response.read().decode("utf-8"))


def stop_children(processes):
    for item in processes:
        process = item["process"]
        if process.poll() is None:
            try:
                process.terminate()
            except OSError:
                pass
    deadline = time.monotonic() + 3.0
    for item in processes:
        process = item["process"]
        if process.poll() is None:
            try:
                process.wait(timeout=max(0.05, deadline - time.monotonic()))
            except (subprocess.TimeoutExpired, OSError):
                try:
                    process.kill()
                except OSError:
                    pass


def main():
    parser = argparse.ArgumentParser(description="Pocket 3 multi-camera PTZ launcher")
    parser.add_argument("--start-port", type=int, default=8723)
    parser.add_argument("--max-cameras", type=int, default=5)
    parser.add_argument("--preview-profile", choices=("stable", "off"), default="stable")
    parser.add_argument("--no-pause", action="store_true")
    args = parser.parse_args()
    maximum = max(1, min(5, args.max_cameras))

    try:
        devices = list_video_devices()
    except Exception as exc:
        print(f"Camera detection failed: {exc}")
        if getattr(sys, "frozen", False) and not args.no_pause:
            input("Press Enter to close...")
        return 1

    dji_devices = [
        (index, name) for index, name in enumerate(devices)
        if any(word in name.casefold() for word in DJI_WORDS)
    ][:maximum]

    print("=" * 68)
    print(f"Detected {len(devices)} video device(s); {len(dji_devices)} DJI camera(s) selected.")
    for index, name in enumerate(devices):
        marker = "DJI" if any(word in name.casefold() for word in DJI_WORDS) else "SKIP"
        print(f"  [{index}] {name}  [{marker}]")
    print("=" * 68)

    if not dji_devices:
        print("No DJI / Osmo / Pocket camera was found.")
        if getattr(sys, "frozen", False) and not args.no_pause:
            input("Press Enter to close...")
        return 1

    base_command = node_command()
    # 所有相機節點都在背景執行，只保留本 Launcher 這一個總控台。
    creation_flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    addresses = lan_ipv4_addresses()
    launched = 0
    processes = []

    for slot, (device_index, device_name) in enumerate(dji_devices, start=1):
        port = args.start_port + slot - 1
        label = f"CAMERA {slot} - {device_name}"
        if not port_available(port):
            print(f"CAMERA {slot}: PORT {port} is already in use; node was not started.")
            continue
        command = base_command + [
            "--host", "0.0.0.0",
            "--port", str(port),
            "--label", label,
            "--device-index", str(device_index),
            "--preview-profile", args.preview_profile,
            "--config", f"ptz-config-camera{slot}-{safe_name(device_name)}.json",
            "--parent-pid", str(os.getpid()),
        ]
        process = subprocess.Popen(
            command,
            cwd=APP_DIR if getattr(sys, "frozen", False) else SOURCE_DIR,
            creationflags=creation_flags,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        processes.append({"slot": slot, "port": port, "name": device_name, "process": process, "last": None})
        launched += 1
        print(f"CAMERA {slot}: {device_name} -> PORT {port}")
        preview_text = "disabled / NDI only" if args.preview_profile == "off" else "stable / on-demand (640x360 @ 8 fps)"
        print(f"  Preview:  {preview_text}")
        print(f"  Local:    http://127.0.0.1:{port}/")
        for address in addresses:
            print(f"  Director: http://{address}:{port}/")
        time.sleep(0.8)

    print("=" * 68)
    print(f"Started {launched} hidden PTZ node(s). Port range: 8723-8727.")
    print("Only this master console remains. Press Ctrl+C to stop every camera.")
    print("=" * 68)
    if not launched:
        return 1

    try:
        while True:
            alive = 0
            for item in processes:
                process = item["process"]
                if process.poll() is not None:
                    key = ("STOPPED", process.returncode)
                    if key != item["last"]:
                        print(f"[CAM {item['slot']}] STOPPED · exit {process.returncode}")
                        item["last"] = key
                    continue
                alive += 1
                try:
                    data = read_node_status(item["port"])
                    key = (
                        data.get("connected"), data.get("preview_status"),
                        data.get("capture_mode"), data.get("obs_clients", 0),
                    )
                    if key != item["last"]:
                        fps = float(data.get("preview_fps", 0) or 0)
                        print(f"[CAM {item['slot']}] {data.get('connected') or 'NO CAMERA'} · {data.get('preview_status', 'WAITING')} · {fps:.1f} FPS · OBS {data.get('obs_clients', 0)}")
                        item["last"] = key
                except Exception:
                    key = ("STARTING",)
                    if key != item["last"]:
                        print(f"[CAM {item['slot']}] STARTING · http://127.0.0.1:{item['port']}")
                        item["last"] = key
            if alive == 0:
                print("All camera nodes have stopped.")
                break
            time.sleep(1.0)
    except KeyboardInterrupt:
        print("\nStopping all camera nodes...")
    finally:
        stop_children(processes)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
