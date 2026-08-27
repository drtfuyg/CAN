"""真实总线仿真引擎测试。"""
import unittest

from common.models import CanFrame
from common.simulation import (
    MessageSpec,
    RealisticBusSimulator,
    SignalSpec,
    default_bus_spec,
    simulator_from_config,
    signals_from_config,
)


def make_signal(**kw):
    base = dict(
        key="s1", name="信号1", can_id=0x101,
        start_byte=0, length=2, scale=0.1, offset=0.0,
        signed=True, byte_order="little", data_type="int",
        min_value=0.0, max_value=100.0,
        waveform="sine", period_s=10.0, noise=0.0,
    )
    base.update(kw)
    return SignalSpec(**base)


class TestBusSimulatorBasics(unittest.TestCase):
    def test_run_produces_expected_period_counts(self):
        # 100ms 周期报文，运行 1 秒应约产生 10 帧（±1，首次发送时间随机化）
        bus = RealisticBusSimulator(
            [MessageSpec(can_id=0x101, period_ms=100.0)],
            seed=42,
        )
        frames = bus.run(1.0, step_ms=1.0)
        ids = [f.can_id for f in frames]
        count = ids.count(0x101)
        self.assertTrue(9 <= count <= 11, f"100ms 周期 1s 应有 ~10 帧，实际 {count}")

    def test_seed_deterministic(self):
        a = RealisticBusSimulator(
            [MessageSpec(can_id=0x101, period_ms=100.0, jitter_ms=5.0)],
            seed=7,
        ).run(2.0, step_ms=10.0)
        b = RealisticBusSimulator(
            [MessageSpec(can_id=0x101, period_ms=100.0, jitter_ms=5.0)],
            seed=7,
        ).run(2.0, step_ms=10.0)
        self.assertEqual(len(a), len(b))
        for fa, fb in zip(a, b):
            self.assertEqual(fa.can_id, fb.can_id)
            self.assertEqual(fa.data, fb.data)

    def test_drop_rate_drops_all(self):
        bus = RealisticBusSimulator(
            [MessageSpec(can_id=0x101, period_ms=10.0, drop_rate=1.0)],
            seed=1,
        )
        frames = bus.run(1.0, step_ms=10.0)
        self.assertFalse(any(f.can_id == 0x101 for f in frames))

    def test_no_message_raises(self):
        with self.assertRaises(ValueError):
            RealisticBusSimulator([])


class TestSpecialFrames(unittest.TestCase):
    def test_remote_frame(self):
        bus = RealisticBusSimulator(
            [MessageSpec(can_id=0x101, period_ms=50.0)],
            remote_frame_ids=[0x101],
            seed=3,
        )
        frames = bus.run(1.0, step_ms=10.0)
        remotes = [f for f in frames if f.can_id == 0x101]
        self.assertTrue(remotes)
        for f in remotes:
            self.assertTrue(f.is_remote)
            self.assertEqual(f.data, b"")
            self.assertEqual(f.raw_dlc, 8)
            self.assertEqual(f.dlc, 8)

    def test_extended_frame(self):
        bus = RealisticBusSimulator(
            [MessageSpec(can_id=0x1FFFFFFF, period_ms=50.0, extended=True)],
            seed=5,
        )
        frames = bus.run(1.0, step_ms=10.0)
        self.assertTrue(all(f.is_extended for f in frames))
        self.assertTrue(all(f.can_id == 0x1FFFFFFF for f in frames))

    def test_error_frame_injection(self):
        bus = RealisticBusSimulator(
            [MessageSpec(can_id=0x101, period_ms=50.0)],
            error_frame_rate=1.0,
            seed=9,
        )
        frames = bus.run(1.0, step_ms=10.0)
        errors = [f for f in frames if f.is_error]
        self.assertTrue(errors, "错误帧率=1.0 时每步都应注入错误帧")

    def test_device_timestamp_monotonic(self):
        bus = RealisticBusSimulator(
            [MessageSpec(can_id=0x101, period_ms=50.0)],
            seed=11,
        )
        frames = bus.run(0.5, step_ms=10.0)
        stamps = [f.device_timestamp_us for f in frames if f.device_timestamp_us]
        self.assertTrue(all(a <= b for a, b in zip(stamps, stamps[1:])))


class TestSignalsAndEncoding(unittest.TestCase):
    def test_encode_parse_roundtrip_int(self):
        """编码 -> 手动解码（int.from_bytes）应还原 raw = round((v-offset)/scale)。"""
        sig = make_signal(scale=0.1, offset=5.0)
        for phys in (0.0, 12.34, 55.0, 99.99):
            enc = sig.encode(phys)
            self.assertEqual(len(enc), 2)
            raw = int.from_bytes(enc, "little", signed=True)
            expected_raw = round((phys - sig.offset) / sig.scale)
            self.assertEqual(raw, expected_raw)

    def test_encode_clamps_to_range(self):
        # 物理值远超可表示范围时应钳制
        sig = make_signal(scale=1.0, min_value=0.0, max_value=100.0)
        enc = sig.encode(1e9)
        raw = int.from_bytes(enc, "little", signed=True)
        self.assertEqual(raw, 32767)

    def test_encode_float32(self):
        import struct
        sig = make_signal(data_type="float32", length=4, scale=1.0)
        enc = sig.encode(3.25)
        self.assertEqual(len(enc), 4)
        self.assertAlmostEqual(struct.unpack("<f", enc)[0], 3.25, places=6)

    def test_signals_packed_into_frame(self):
        sig = make_signal(start_byte=0, length=2, scale=0.1)
        msg = MessageSpec(can_id=0x101, period_ms=50.0, signals=[sig])
        bus = RealisticBusSimulator([msg], seed=21)
        frames = bus.run(1.0, step_ms=10.0)
        frames = [f for f in frames if f.can_id == 0x101]
        self.assertTrue(frames)
        for f in frames:
            self.assertEqual(len(f.data), 8)
            # 未占用的字节被随机填充，但信号字节应能被解析
            raw = int.from_bytes(f.data[0:2], "little", signed=True)
            self.assertTrue(-32768 <= raw <= 32767)


class TestConfigDriven(unittest.TestCase):
    def test_signals_from_config(self):
        signals = signals_from_config("sensor_config.json")
        self.assertTrue(len(signals) >= 3)  # 内置配置有 3 个信号

    def test_default_bus_spec_has_unrelated_messages(self):
        signals = signals_from_config("sensor_config.json")
        messages = default_bus_spec(signals)
        ids = {m.can_id for m in messages}
        # 无关报文 0x1F0/0x1F1 应存在（用于验证过滤/统计）
        self.assertIn(0x1F0, ids)
        self.assertIn(0x1F1, ids)

    def test_simulator_from_config_runs(self):
        bus = simulator_from_config("sensor_config.json", seed=0)
        frames = bus.run(2.0, step_ms=10.0)
        self.assertTrue(frames)
        # 至少包含一个配置中的 ID 和一个无关 ID
        ids = {f.can_id for f in frames}
        self.assertIn(0x1F0, ids)
        self.assertTrue(any(i in ids for i in (0x101, 0x102)))


if __name__ == "__main__":
    unittest.main()
