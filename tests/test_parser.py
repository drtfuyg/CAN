"""SensorParser 解析逻辑测试（含健壮性/错误收集）。"""
import json
import os
import struct
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from common.models import CanFrame
from common.parser import SensorParser


def make_frame(can_id=0x101, data=b"\x00" * 8, **kw):
    return CanFrame(
        pc_timestamp=datetime.now(),
        can_id=can_id,
        data=data,
        **kw,
    )


class ParserTestBase(unittest.TestCase):
    """在临时目录中创建配置文件的基类。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.config_path = os.path.join(self.tmp.name, "sensor_config.json")

    def tearDown(self):
        self.tmp.cleanup()

    def write_config(self, sensors):
        Path(self.config_path).write_text(
            json.dumps({"sensors": sensors}), encoding="utf-8"
        )

    def parser(self, sensors=None, **kw):
        if sensors is not None:
            self.write_config(sensors)
        return SensorParser(self.config_path, **kw)


class TestParseBasic(ParserTestBase):
    RULE_INT16_LITTLE_SIGNED = {
        "key": "temp",
        "name": "温度",
        "can_id": "0x101",
        "start_byte": 0,
        "length": 2,
        "data_type": "int",
        "signed": True,
        "byte_order": "little",
        "scale": 0.1,
        "offset": 0,
        "unit": "C",
    }

    def test_int16_little_signed_scale(self):
        p = self.parser([self.RULE_INT16_LITTLE_SIGNED])
        data = (1234).to_bytes(2, "little", signed=True) + b"\x00" * 6
        values = p.parse(make_frame(data=data))
        self.assertEqual(len(values), 1)
        self.assertEqual(values[0].key, "temp")
        self.assertEqual(values[0].name, "温度")
        self.assertEqual(values[0].unit, "C")
        self.assertAlmostEqual(values[0].value, 123.4, places=6)
        self.assertEqual(values[0].source_can_id, 0x101)

    def test_negative_signed_value(self):
        p = self.parser([self.RULE_INT16_LITTLE_SIGNED])
        data = (-300).to_bytes(2, "little", signed=True) + b"\x00" * 6
        values = p.parse(make_frame(data=data))
        self.assertAlmostEqual(values[0].value, -30.0, places=6)

    def test_big_endian(self):
        rule = dict(self.RULE_INT16_LITTLE_SIGNED, byte_order="big")
        p = self.parser([rule])
        data = (0x1234).to_bytes(2, "big", signed=True) + b"\x00" * 6
        values = p.parse(make_frame(data=data))
        # 0x1234 = 4660，scale 0.1 -> 466.0
        self.assertAlmostEqual(values[0].value, 466.0, places=6)

    def test_unsigned(self):
        rule = dict(self.RULE_INT16_LITTLE_SIGNED, signed=False, scale=1.0)
        p = self.parser([rule])
        data = (0xF0F0).to_bytes(2, "little", signed=False) + b"\x00" * 6
        values = p.parse(make_frame(data=data))
        self.assertAlmostEqual(values[0].value, 0xF0F0, places=6)

    def test_float32_little(self):
        rule = {
            "key": "float",
            "name": "浮点",
            "can_id": "0x101",
            "start_byte": 0,
            "length": 4,
            "data_type": "float32",
            "byte_order": "little",
        }
        p = self.parser([rule])
        data = struct.pack("<f", 3.25) + b"\x00" * 4
        values = p.parse(make_frame(data=data))
        self.assertAlmostEqual(values[0].value, 3.25, places=6)

    def test_float32_big(self):
        rule = {
            "key": "float",
            "name": "浮点",
            "can_id": "0x101",
            "start_byte": 0,
            "length": 4,
            "data_type": "float32",
            "byte_order": "big",
        }
        p = self.parser([rule])
        data = struct.pack(">f", -1.5) + b"\x00" * 4
        values = p.parse(make_frame(data=data))
        self.assertAlmostEqual(values[0].value, -1.5, places=6)

    def test_multiple_signals_same_id(self):
        sensors = [
            {
                "key": "a", "name": "A", "can_id": "0x101",
                "start_byte": 0, "length": 2,
                "data_type": "int", "signed": True,
                "byte_order": "little", "scale": 1.0,
            },
            {
                "key": "b", "name": "B", "can_id": "0x101",
                "start_byte": 2, "length": 2,
                "data_type": "int", "signed": True,
                "byte_order": "little", "scale": 1.0,
            },
        ]
        p = self.parser(sensors)
        data = (1).to_bytes(2, "little", signed=True) + \
               (2).to_bytes(2, "little", signed=True) + b"\x00" * 4
        values = p.parse(make_frame(data=data))
        self.assertEqual([v.key for v in values], ["a", "b"])
        self.assertEqual([v.value for v in values], [1.0, 2.0])


class TestParseRobustness(ParserTestBase):
    def test_id_mismatch_returns_empty(self):
        p = self.parser([{
            "key": "k", "name": "K", "can_id": "0x101",
            "start_byte": 0, "length": 1,
        }])
        values = p.parse(make_frame(can_id=0x102))
        self.assertEqual(values, [])

    def test_short_data_skipped_and_reported(self):
        p = self.parser([{
            "key": "k", "name": "K", "can_id": "0x101",
            "start_byte": 6, "length": 4,  # 帧只有 8 字节，6+4=10 越界
        }], collect_errors=True)
        values = p.parse(make_frame(data=b"\x00" * 8))
        self.assertEqual(values, [])
        self.assertTrue(p.errors, "应当记录数据不足错误")

    def test_negative_start_byte_skipped(self):
        p = self.parser([{
            "key": "k", "name": "K", "can_id": "0x101",
            "start_byte": -2, "length": 2,
        }], collect_errors=True)
        # 负 start 不应产生错误数据（旧版会因切片语义产生误解析）
        values = p.parse(make_frame(data=b"\x00" * 8))
        self.assertEqual(values, [])

    def test_unknown_data_type_skipped(self):
        p = self.parser([{
            "key": "k", "name": "K", "can_id": "0x101",
            "start_byte": 0, "length": 1,
            "data_type": "bogus",
        }], collect_errors=True)
        values = p.parse(make_frame(data=b"\x00" * 8))
        self.assertEqual(values, [])

    def test_float32_wrong_length_skipped(self):
        p = self.parser([{
            "key": "k", "name": "K", "can_id": "0x101",
            "start_byte": 0, "length": 2, "data_type": "float32",
        }], collect_errors=True)
        values = p.parse(make_frame(data=b"\x00" * 8))
        self.assertEqual(values, [])

    def test_missing_config_does_not_crash(self):
        # 配置路径不存在：构造不崩溃，config_error 被记录
        p = SensorParser(os.path.join(self.tmp.name, "missing.json"))
        self.assertIsNotNone(p.config_error)
        self.assertEqual(p.rules, [])
        self.assertEqual(p.parse(make_frame()), [])

    def test_bad_json_does_not_crash(self):
        Path(self.config_path).write_text("{not valid json", encoding="utf-8")
        p = SensorParser(self.config_path)
        self.assertIsNotNone(p.config_error)
        self.assertEqual(p.parse(make_frame()), [])

    def test_sensors_not_list_rejected(self):
        # __init__ 容错不崩溃（记录 config_error），reload() 仍明确抛错
        Path(self.config_path).write_text(
            '{"sensors": "oops"}', encoding="utf-8"
        )
        p = SensorParser(self.config_path)
        self.assertIsNotNone(p.config_error)
        with self.assertRaises(ValueError):
            p.reload()


class TestReload(ParserTestBase):
    def test_reload_updates_rules(self):
        p = self.parser([{
            "key": "a", "name": "A", "can_id": "0x101",
            "start_byte": 0, "length": 1,
        }])
        self.assertEqual(len(p.parse(make_frame())), 1)

        # 修改配置后 reload
        self.write_config([{
            "key": "b", "name": "B", "can_id": "0x102",
            "start_byte": 0, "length": 1,
        }])
        p.reload()
        self.assertEqual(len(p.parse(make_frame(can_id=0x101))), 0)
        self.assertEqual(len(p.parse(make_frame(can_id=0x102))), 1)


if __name__ == "__main__":
    unittest.main()
