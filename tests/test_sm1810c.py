import unittest
from common.models import CanFrame
from common.sm1810c import parse_response


class TestSM1810C(unittest.TestCase):
    def test_parse_manual_example(self):
        frame = CanFrame(None, 0x000, bytes.fromhex("01 03 04 08 AD 0F 7D"))
        values = {v.key: v.value for v in parse_response(frame)}
        self.assertAlmostEqual(values["temperature"], 22.21)
        self.assertAlmostEqual(values["humidity"], 39.65)

    def test_reject_wrong_id_or_short_frame(self):
        self.assertEqual(parse_response(CanFrame(None, 0x123, b"\x01\x03\x04")), [])
        self.assertEqual(parse_response(CanFrame(None, 0x000, b"\x01\x03\x04")), [])


if __name__ == "__main__":
    unittest.main()
