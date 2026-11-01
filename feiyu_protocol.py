"""Feiyu Pocket 3 experimental PTP/IP and UDP gimbal transport.

The protocol was reconstructed from a Feiyu Cam packet capture.  The camera
must be reachable over its Wi-Fi network before this module can control it.
"""

from __future__ import annotations

import socket
import struct
import threading
import uuid


PTP_PORT = 15740
UDP_PORT = 7777
OP_OPEN_SESSION = 0x1002
OP_HEARTBEAT = 0x9601
RESPONSE_HEARTBEAT = 0xA601
HEARTBEAT_PARAMS = (0x0000D001, 0xFFFFFFFF, 0x00000000)

PKT_INIT_CMD_REQ = 1
PKT_INIT_CMD_ACK = 2
PKT_INIT_EVT_REQ = 3
PKT_INIT_EVT_ACK = 4
PKT_INIT_FAIL = 5
PKT_CMD_REQ = 6
PKT_CMD_RES = 7
PKT_EVENT = 8
PKT_START_DATA = 9
PKT_DATA = 10
PKT_END_DATA = 12


def crc16_xmodem(data: bytes, crc: int = 0) -> int:
    """Return the CRC used by Feiyu's 16-byte joystick packet."""
    for value in data:
        crc ^= value << 8
        for _ in range(8):
            if crc & 0x8000:
                crc = ((crc << 1) ^ 0x1021) & 0xFFFF
            else:
                crc = (crc << 1) & 0xFFFF
    return crc


def build_joystick_packet(pan: int, tilt: int) -> bytes:
    """Build one captured-compatible UDP 7777 pan/tilt packet."""
    pan = max(-32768, min(32767, int(pan)))
    tilt = max(-32768, min(32767, int(tilt)))
    body = bytes.fromhex("03000e0000050000") + struct.pack("<hh", pan, tilt)
    return b"\xa5\x5a" + body + struct.pack("<H", crc16_xmodem(body))


def _send_packet(sock: socket.socket, packet_type: int, payload: bytes = b"") -> None:
    sock.sendall(struct.pack("<II", 8 + len(payload), packet_type) + payload)


def _recv_exact(sock: socket.socket, size: int) -> bytes:
    chunks = bytearray()
    while len(chunks) < size:
        chunk = sock.recv(size - len(chunks))
        if not chunk:
            raise ConnectionError("Feiyu PTP connection closed")
        chunks.extend(chunk)
    return bytes(chunks)


def _recv_packet(sock: socket.socket) -> tuple[int, bytes]:
    length, packet_type = struct.unpack("<II", _recv_exact(sock, 8))
    if length < 8 or length > 16 * 1024 * 1024:
        raise RuntimeError(f"invalid Feiyu PTP packet length: {length}")
    return packet_type, _recv_exact(sock, length - 8)


def _utf16z(value: str) -> bytes:
    return value.encode("utf-16-le") + b"\x00\x00"


class PTPIP:
    """Small PTP/IP client containing only what the Feiyu backend needs."""

    def __init__(self, host: str, timeout: float = 5.0):
        self.host = host
        self.timeout = timeout
        self.command: socket.socket | None = None
        self.event: socket.socket | None = None
        self.transaction_id = 0
        self.lock = threading.Lock()

    def connect(self) -> None:
        self.close()
        command = socket.create_connection((self.host, PTP_PORT), timeout=self.timeout)
        command.settimeout(self.timeout)
        request = uuid.uuid4().bytes + _utf16z("Pocket3-PTZ") + struct.pack("<I", 0x00010000)
        _send_packet(command, PKT_INIT_CMD_REQ, request)
        packet_type, payload = _recv_packet(command)
        if packet_type == PKT_INIT_FAIL:
            command.close()
            raise RuntimeError(f"Feiyu rejected PTP connection: {payload.hex()}")
        if packet_type != PKT_INIT_CMD_ACK or len(payload) < 4:
            command.close()
            raise RuntimeError(f"unexpected Feiyu PTP response type: {packet_type}")

        connection_number = struct.unpack_from("<I", payload)[0]
        event = socket.create_connection((self.host, PTP_PORT), timeout=self.timeout)
        event.settimeout(self.timeout)
        _send_packet(event, PKT_INIT_EVT_REQ, struct.pack("<I", connection_number))
        event_type, _ = _recv_packet(event)
        if event_type != PKT_INIT_EVT_ACK:
            command.close()
            event.close()
            raise RuntimeError(f"unexpected Feiyu event response type: {event_type}")

        self.command = command
        self.event = event
        self.transaction_id = 0

    def transact(self, opcode: int, params: tuple[int, ...] = ()) -> int:
        if self.command is None:
            raise ConnectionError("Feiyu PTP is not connected")
        with self.lock:
            transaction_id = self.transaction_id
            self.transaction_id += 1
            payload = struct.pack("<IHI", 1, opcode, transaction_id)
            payload += b"".join(struct.pack("<I", value) for value in params)
            _send_packet(self.command, PKT_CMD_REQ, payload)
            while True:
                packet_type, response = _recv_packet(self.command)
                if packet_type in (PKT_START_DATA, PKT_DATA, PKT_END_DATA, PKT_EVENT):
                    continue
                if packet_type != PKT_CMD_RES or len(response) < 6:
                    continue
                code, response_transaction = struct.unpack_from("<HI", response)
                if response_transaction == transaction_id:
                    return code

    def open_session(self) -> int:
        return self.transact(OP_OPEN_SESSION, (1,))

    def heartbeat(self) -> int:
        return self.transact(OP_HEARTBEAT, HEARTBEAT_PARAMS)

    def close(self) -> None:
        for connection in (self.event, self.command):
            if connection is not None:
                try:
                    connection.close()
                except OSError:
                    pass
        self.command = None
        self.event = None


class FeiyuTransport:
    """Maintain Feiyu's PTP heartbeat while sending velocity over UDP."""

    def __init__(self, host: str = "192.168.1.1", speed: int = 17):
        self.host = host
        self.speed = max(1, min(127, int(speed)))
        self.ptp = PTPIP(host)
        self.udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.connected = False
        self.last_error = ""
        self.last_heartbeat_code: int | None = None

    def connect(self) -> None:
        self.ptp.connect()
        # Captured Feiyu Cam traffic receives InvalidParameter here and then
        # continues successfully, so connection health is determined by the
        # vendor heartbeat response rather than this camera-specific quirk.
        self.ptp.open_session()
        self.last_heartbeat_code = self.ptp.heartbeat()
        if self.last_heartbeat_code != RESPONSE_HEARTBEAT:
            raise RuntimeError(f"unexpected Feiyu heartbeat response: 0x{self.last_heartbeat_code:04X}")
        self.connected = True
        self.last_error = ""

    def heartbeat(self) -> int:
        code = self.ptp.heartbeat()
        if code != RESPONSE_HEARTBEAT:
            raise RuntimeError(f"unexpected Feiyu heartbeat response: 0x{code:04X}")
        self.last_heartbeat_code = code
        self.connected = True
        return code

    def send(self, pan: float, tilt: float) -> None:
        pan_value = round(max(-1.0, min(1.0, pan)) * self.speed)
        tilt_value = round(max(-1.0, min(1.0, tilt)) * self.speed)
        self.udp.sendto(build_joystick_packet(pan_value, tilt_value), (self.host, UDP_PORT))

    def stop(self) -> None:
        for _ in range(3):
            try:
                self.send(0.0, 0.0)
            except OSError:
                break

    def close(self) -> None:
        self.stop()
        self.connected = False
        self.ptp.close()
        self.udp.close()
