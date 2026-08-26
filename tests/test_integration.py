"""端到端集成测试：仿真引擎 -> SensorParser。

在没有真实设备的条件下，验证"编码（仿真） -> 总线 -> 解析（parser）"
整条链路的一致性，以及统计/无关报文等真实总线特征。
"""
import os
import unittest

from models import CanFrame, SensorValue
from parser import SensorParser
from simulation import (
    MessageSpec,
    RealisticBusSimulator,
    SignalSpec,
    default_bus_spec,
    signals_from_config,
    simulator_from_config,
)

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_PATH = os.path.join(PROJECT_ROOT, "sensor_config.json")


class TestEncodingDecodingConsistency(unittest.TestCase):
    """信号编码与 parser 解码必须互为逆运算。"""

    def test_each_configured_signal_roundtrips(self):
        signals = signals_from_config(CONFIG_PATH)
        self.assertTrue(signals)

        parser = SensorParser(CONFIG_PATH, collect_errors=True)
        for sig in signals:
            for phys in (sig.min_value, sig.max_value, 42.0):
                enc = sig.encode(phys)
                # 信号字节必须放在配置指定的 start_byte 处，其余填充填充字节
                data = bytearray(b"\xAA" * 8)
                data[sig.start_byte:sig.start_byte + len(enc)] = enc
                frame = CanFrame(
                    pc_timestamp=None,
                    can_id=sig.can_id,
                    data=bytes(data),
                )
                values = parser.parse(frame)
                matched = [v for v in values if v.key == sig.key]
                self.assertEqual(
                    len(matched), 1,
                    f"信号 {sig.key} 未能解析（错误：{parser.errors}）",
                )
                # 预期值 = clamp(round((phys-offset)/scale)) * scale + offset
                raw = round((phys - sig.offset) / sig.scale)
                if sig.signed:
                    bits = sig.length * 8
                    raw = max(-(1 << (bits - 1)), min((1 << (bits - 1)) - 1, raw))
                else:
                    raw = max(0, min((1 << (sig.length * 8)) - 1, raw))
                expected = raw * sig.scale + sig.offset
                self.assertAlmostEqual(
                    matched[0].value, expected, places=4,
                    msg=f"信号 {sig.key} 往返不一致",
                )

    def test_int16_signed_negative_roundtrip(self):
        # 显式验证负数编码-解码往返（使用独立单规则配置，避免与项目配置交叉）
        import json
        import tempfile
        from pathlib import Path

        sig = SignalSpec(
            key="n", name="负值", can_id=0x101,
            start_byte=0, length=2, scale=1.0, signed=True,
            byte_order="little", data_type="int",
        )
        cfg = {"sensors": [{
            "key": sig.key, "name": sig.name, "can_id": hex(sig.can_id),
            "start_byte": sig.start_byte, "length": sig.length,
            "data_type": sig.data_type, "signed": sig.signed,
            "byte_order": sig.byte_order, "scale": sig.scale,
        }]}
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "cfg.json")
            Path(path).write_text(json.dumps(cfg), encoding="utf-8")
            parser = SensorParser(path)
            for v in (-100, -1, 0, 1, 30000):
                enc = sig.encode(v)
                frame = CanFrame(pc_timestamp=None, can_id=0x101, data=enc)
                parsed = parser.parse(frame)
                self.assertEqual(parsed[0].value, float(v))


class TestEndToEndBus(unittest.TestCase):
    def test_configured_signals_parsed_from_bus(self):
        bus = simulator_from_config(CONFIG_PATH, seed=123)
        frames = bus.run(5.0, step_ms=10.0)
        self.assertTrue(frames)

        parser = SensorParser(CONFIG_PATH, collect_errors=True)
        seen = {}      # key -> 解析出的值集合
        for f in frames:
            for sv in parser.parse(f):
                seen.setdefault(sv.key, []).append(sv.value)

        signals = signals_from_config(CONFIG_PATH)
        for sig in signals:
            self.assertIn(
                sig.key, seen,
                f"运行 5 秒后信号 {sig.key} 未被解析到（错误：{parser.errors}）",
            )
            for val in seen[sig.key]:
                # 值应在物理范围（含噪声与编码量化余量）
                margin = abs(sig.scale) * 2 + sig.noise
                self.assertGreaterEqual(val, sig.min_value - margin)
                self.assertLessEqual(val, sig.max_value + margin)

    def test_unrelated_ids_not_parsed(self):
        bus = simulator_from_config(CONFIG_PATH, seed=1)
        frames = bus.run(1.0, step_ms=10.0)
        unrelated = [f for f in frames if f.can_id in (0x1F0, 0x1F1)]
        self.assertTrue(unrelated, "仿真应包含无关报文")
        parser = SensorParser(CONFIG_PATH)
        for f in unrelated:
            self.assertEqual(parser.parse(f), [])

    def test_remote_frame_not_parsed_as_data(self):
        # 远程帧没有数据场，不应被解析出值
        bus = RealisticBusSimulator(
            default_bus_spec(signals_from_config(CONFIG_PATH)),
            remote_frame_ids=[0x101],
            seed=2,
        )
        frames = bus.run(1.0, step_ms=10.0)
        parser = SensorParser(CONFIG_PATH)
        remotes = [f for f in frames if f.can_id == 0x101 and f.is_remote]
        self.assertTrue(remotes)
        for f in remotes:
            self.assertEqual(parser.parse(f), [])


if __name__ == "__main__":
    unittest.main()
