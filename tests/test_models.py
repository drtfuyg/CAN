"""CanFrame / SensorValue 数据模型测试。"""
import unittest
from datetime import datetime

from common.models import CanFrame, SensorValue


class TestCanFrame(unittest.TestCase):
    def test_dlc_defaults_to_data_length(self):
        frame = CanFrame(
            pc_timestamp=datetime.now(),
            can_id=0x101,
            data=b"\x01\x02\x03",
        )
        self.assertEqual(frame.dlc, 3)

    def test_raw_dlc_overrides_for_remote_frame(self):
        # 远程帧无数据场，但请求 DLC=8
        frame = CanFrame(
            pc_timestamp=datetime.now(),
            can_id=0x101,
            data=b"",
            is_remote=True,
            raw_dlc=8,
        )
        self.assertEqual(frame.dlc, 8)
        self.assertEqual(frame.data, b"")

    def test_data_hex_formatting(self):
        frame = CanFrame(
            pc_timestamp=datetime.now(),
            can_id=0x101,
            data=b"\x00\x0A\xFF",
        )
        self.assertEqual(frame.data_hex, "00 0A FF")

    def test_defaults(self):
        frame = CanFrame(
            pc_timestamp=datetime.now(),
            can_id=0x100,
            data=b"\x00",
        )
        self.assertFalse(frame.is_extended)
        self.assertFalse(frame.is_remote)
        self.assertFalse(frame.is_error)
        self.assertEqual(frame.channel, 0)
        self.assertIsNone(frame.device_timestamp_us)
        self.assertIsNone(frame.raw_dlc)


class TestSensorValue(unittest.TestCase):
    def test_defaults(self):
        v = SensorValue(key="p1", name="参数1", value=12.5)
        self.assertEqual(v.unit, "")
        self.assertIsNone(v.source_can_id)


if __name__ == "__main__":
    unittest.main()
