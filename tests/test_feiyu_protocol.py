import struct
import unittest

from feiyu_protocol import build_joystick_packet, crc16_xmodem


class FeiyuProtocolTests(unittest.TestCase):
    def test_neutral_packet_matches_capture(self):
        self.assertEqual(
            build_joystick_packet(0, 0).hex(),
            "a55a03000e000005000000000000cc45",
        )

    def test_captured_packets_round_trip(self):
        captured = (
            "a55a03000e000005000011000000df28",
            "a55a03000e0000050000efff0000abda",
            "a55a03000e0000050000000011008e75",
        )
        for packet_hex in captured:
            packet = bytes.fromhex(packet_hex)
            pan, tilt = struct.unpack("<hh", packet[10:14])
            self.assertEqual(build_joystick_packet(pan, tilt), packet)
            self.assertEqual(crc16_xmodem(packet[2:14]), struct.unpack("<H", packet[14:])[0])

    def test_axis_values_are_clamped_to_int16(self):
        packet = build_joystick_packet(99999, -99999)
        self.assertEqual(struct.unpack("<hh", packet[10:14]), (32767, -32768))


if __name__ == "__main__":
    unittest.main()
