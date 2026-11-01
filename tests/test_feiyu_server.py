import importlib.util
import pathlib
import unittest


MODULE_PATH = pathlib.Path(__file__).parents[1] / "feiyu-server.py"
SPEC = importlib.util.spec_from_file_location("feiyu_server", MODULE_PATH)
FEIYU_SERVER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(FEIYU_SERVER)


class FeiyuServerTests(unittest.TestCase):
    def setUp(self):
        self.node = FEIYU_SERVER.FeiyuNode("192.168.1.1", "FEIYU TEST", 17)

    def test_status_uses_common_api_shape(self):
        status = self.node.status(8723)
        self.assertEqual(status["backend"], "feiyu_wifi_experimental")
        self.assertEqual(status["pos"], {"pan": None, "tilt": None, "zoom": None})
        self.assertTrue(status["capabilities"]["pan"])
        self.assertFalse(status["capabilities"]["presets"])
        self.assertAlmostEqual(status["speed"], 17 / 127, places=3)

    def test_ui_speed_maps_to_protocol_magnitude(self):
        self.node.set_speed(0.5)
        self.assertEqual(self.node.speed, 64)


if __name__ == "__main__":
    unittest.main()
