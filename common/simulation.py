"""真实 CAN 总线仿真引擎（纯 Python，无第三方依赖）。

目标：在缺少真实硬件时，尽可能真实地模拟一条 CAN 总线，从而可以：
  1. 验证解析链路（simulator -> SensorParser -> SensorValue）
  2. 验证统计、CSV 记录、过滤、曲线等整条 GUI 链路
  3. 模拟真实世界特征：多周期报文、周期抖动、丢帧、扩展帧、
     远程帧、错误帧、总线上的"无关"报文、数值噪声等

设计为纯 Python 时间推进（不依赖 Qt 的 QTimer / 真实时钟），
因此：
  - GUI 模拟后端可用 QTimer 周期性调用 ``step(dt_ms)`` 驱动
  - 单元测试可同步调用 ``run(duration_s)`` 批量生成帧
两处复用同一套逻辑，保证仿真行为一致。

用法示例（测试 / 脚本）:
    spec = default_bus_spec()                 # 内置默认总线场景
    bus = RealisticBusSimulator(spec)
    frames = bus.run(5.0)                     # 运行 5 秒，返回全部帧
    for f in frames:
        print(f.can_id, f.data_hex)
"""

from __future__ import annotations

import json
import math
import random
import struct
from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional, Sequence, Tuple

from common.models import CanFrame
from common.can_utils import clamp_signed, clamp_unsigned


# ---------------------------------------------------------------------------
# 信号与报文规格
# ---------------------------------------------------------------------------

@dataclass
class SignalSpec:
    """一个传感器信号，字段与 sensor_config.json 中的规则一一对应。"""
    key: str
    name: str
    can_id: int
    start_byte: int
    length: int
    scale: float = 1.0
    offset: float = 0.0
    unit: str = ""
    signed: bool = True
    byte_order: str = "little"
    data_type: str = "int"
    # —— 以下为仿真专用物理参数（真实世界中信号的真实取值范围） ——
    min_value: float = 0.0      # 物理量最小值
    max_value: float = 100.0    # 物理量最大值
    waveform: str = "sine"      # sine / triangle / step / noise / ramp / constant
    period_s: float = 10.0      # 波形周期（秒）
    phase: float = 0.0          # 相位偏移（周期比例 0~1）
    noise: float = 0.0          # 叠加白噪声幅度（物理量单位）

    def physical_value_at(self, t: float, rng: random.Random) -> float:
        """按波形 + 噪声计算 t 时刻的物理值（在 [min_value, max_value] 附近）。"""
        mid = (self.min_value + self.max_value) / 2.0
        amp = (self.max_value - self.min_value) / 2.0
        w = wave(self.waveform, t, self.period_s, self.phase)
        n = rng.uniform(-1.0, 1.0) * self.noise
        return mid + amp * w + n

    def encode(self, physical_value: float) -> bytes:
        """把物理量按 scale/offset 编码成字节序列（与 parser 解码互逆）。"""
        raw = (physical_value - self.offset) / self.scale if self.scale else 0.0
        if self.data_type == "float32":
            if self.length != 4:
                raise ValueError(f"信号 {self.key}: float32 长度必须为 4，实际 {self.length}")
            return struct.pack(
                "<f" if self.byte_order == "little" else ">f",
                raw,
            )
        if self.data_type != "int":
            raise ValueError(f"信号 {self.key}: 不支持的 data_type={self.data_type}")
        if self.length <= 0 or self.length > 8:
            raise ValueError(f"信号 {self.key}: 非法长度 {self.length}")
        raw_i = int(round(raw))
        raw_i = (
            clamp_signed(raw_i, self.length)
            if self.signed
            else clamp_unsigned(raw_i, self.length)
        )
        return int(raw_i).to_bytes(self.length, self.byte_order, signed=self.signed)


@dataclass
class MessageSpec:
    """一条 CAN 报文的发送节奏。"""
    can_id: int
    period_ms: float = 100.0      # 基础发送周期（毫秒）
    jitter_ms: float = 0.0        # 周期抖动幅度，实际周期 = period ± uniform(jitter)
    extended: bool = False        # 扩展帧
    remote: bool = False          # 固定作为远程帧发送（无数据场）
    drop_rate: float = 0.0        # 丢帧概率 0~1
    channel: int = 0
    signals: List[SignalSpec] = field(default_factory=list)


def wave(
    waveform: str,
    t: float,
    period_s: float,
    phase: float,
) -> float:
    """返回归一化到 [-1, 1] 的波形值。"""
    p = period_s if period_s and period_s > 0 else 1e-6
    x = t / p + phase
    if waveform == "sine":
        return math.sin(2.0 * math.pi * x)
    if waveform == "triangle":
        y = x % 1.0
        return 4.0 * abs(y - 0.5) - 1.0
    if waveform == "ramp":
        return 2.0 * (x % 1.0) - 1.0
    if waveform == "step":
        return 1.0 if (x % 1.0) < 0.5 else -1.0
    if waveform == "noise":
        return random.uniform(-1.0, 1.0)
    if waveform == "constant":
        return 0.0
    return 0.0


# ---------------------------------------------------------------------------
# 总线仿真器
# ---------------------------------------------------------------------------

class RealisticBusSimulator:
    """按时间推进方式仿真一条 CAN 总线。

    支持：
      - 各报文独立周期 + 周期抖动
      - 报文丢帧（drop_rate）
      - 指定 ID 作为远程帧发送
      - 按概率注入错误帧（error_frame_rate）
      - 设备时间戳模拟（自启动起微秒，对应真实 msg.timestamp）
    """

    def __init__(
        self,
        messages: Sequence[MessageSpec],
        error_frame_rate: float = 0.0,
        remote_frame_ids: Optional[Sequence[int]] = None,
        seed: Optional[int] = None,
    ):
        if not messages:
            raise ValueError("至少需要一条报文规格")
        self.messages = list(messages)
        self.error_frame_rate = error_frame_rate
        self.remote_frame_ids = set(remote_frame_ids or [])
        self.rng = random.Random(seed)

        self.t_ms = 0.0
        self._dev_us = 0
        # 每条报文的首次发送时间随机化（避免所有报文同时刻发射）
        self._countdown: dict = {
            id(msg): self.rng.uniform(0.0, max(msg.period_ms, 1.0))
            for msg in self.messages
        }

    # ---- 对外接口 -------------------------------------------------------

    def step(self, dt_ms: float = 1.0) -> List[CanFrame]:
        """推进 dt_ms 毫秒，返回该时间片内到期的帧（含可能注入的错误帧）。"""
        if dt_ms <= 0:
            return []
        self.t_ms += dt_ms
        self._dev_us += int(dt_ms * 1000.0)

        frames: List[CanFrame] = []
        for msg in self.messages:
            self._countdown[id(msg)] -= dt_ms
            if self._countdown[id(msg)] <= 0:
                period = msg.period_ms + self.rng.uniform(
                    -msg.jitter_ms, msg.jitter_ms
                )
                self._countdown[id(msg)] += max(period, 1.0)
                if self.rng.random() >= msg.drop_rate:
                    frames.append(
                        self._build_frame(
                            msg,
                            remote=msg.can_id in self.remote_frame_ids or msg.remote,
                        )
                    )

        if (
            self.error_frame_rate > 0
            and self.rng.random() < self.error_frame_rate
        ):
            frames.append(self._build_error_frame())

        return frames

    def run(self, duration_s: float, step_ms: float = 10.0) -> List[CanFrame]:
        """同步运行 duration_s 秒，返回全部产生的帧（测试 / 脚本用）。"""
        frames: List[CanFrame] = []
        steps = max(1, int(duration_s * 1000.0 / step_ms))
        for _ in range(steps):
            frames.extend(self.step(step_ms))
        return frames

    @property
    def elapsed_s(self) -> float:
        return self.t_ms / 1000.0

    # ---- 内部实现 -------------------------------------------------------

    def _build_frame(
        self,
        msg: MessageSpec,
        remote: bool = False,
    ) -> CanFrame:
        data = bytearray(8)
        used = set()
        for sig in msg.signals:
            if sig.can_id != msg.can_id:
                continue
            enc = sig.encode(sig.physical_value_at(self.t_ms / 1000.0, self.rng))
            start = sig.start_byte
            if start < 0 or start + len(enc) > 8:
                continue
            for i, b in enumerate(enc):
                data[start + i] = b
                used.add(start + i)
        # 未被信号占用的字节填入随机数据（贴近真实总线上的填充/校验字节）
        for i in range(8):
            if i not in used:
                data[i] = self.rng.randint(0, 255)

        if remote:
            return CanFrame(
                pc_timestamp=datetime.now(),
                can_id=msg.can_id,
                data=b"",
                channel=msg.channel,
                is_extended=msg.extended,
                is_remote=True,
                is_error=False,
                device_timestamp_us=self._dev_us,
                raw_dlc=8,  # 远程帧请求的 DLC（保持原长度信息）
            )

        return CanFrame(
            pc_timestamp=datetime.now(),
            can_id=msg.can_id,
            data=bytes(data),
            channel=msg.channel,
            is_extended=msg.extended,
            is_remote=False,
            is_error=False,
            device_timestamp_us=self._dev_us,
        )

    def _build_error_frame(self) -> CanFrame:
        return CanFrame(
            pc_timestamp=datetime.now(),
            can_id=0,
            data=b"\x00\x00\x00\x00\x00\x00\x00\x00",
            channel=0,
            is_extended=False,
            is_remote=False,
            is_error=True,
            device_timestamp_us=self._dev_us,
        )


# ---------------------------------------------------------------------------
# 从 sensor_config.json 构建总线规格
# ---------------------------------------------------------------------------

def signals_from_config(config_path: str) -> List[SignalSpec]:
    """读取 sensor_config.json 的 rules 并转换为 SignalSpec 列表。

    把配置里的 scale/offset 视为"真实物理量编码"规则：仿真按真实物理值
    编码，parser 按同一规则解码，可做端到端一致性验证。
    """
    with open(config_path, encoding="utf-8") as f:
        rules = json.load(f).get("sensors", [])

    signals: List[SignalSpec] = []
    for rule in rules:
        can_id = int(str(rule["can_id"]), 0)
        signals.append(
            SignalSpec(
                key=rule["key"],
                name=rule.get("name", rule["key"]),
                can_id=can_id,
                start_byte=int(rule["start_byte"]),
                length=int(rule["length"]),
                scale=float(rule.get("scale", 1.0)),
                offset=float(rule.get("offset", 0.0)),
                unit=rule.get("unit", ""),
                signed=bool(rule.get("signed", False)),
                byte_order=rule.get("byte_order", "little"),
                data_type=rule.get("data_type", "int"),
                # 以下仿真参数由总线中信号的真实物理范围决定
                min_value=float(rule.get("sim_min", 0.0)),
                max_value=float(rule.get("sim_max", 100.0)),
                waveform=rule.get("sim_waveform", "sine"),
                period_s=float(rule.get("sim_period_s", 10.0)),
                noise=float(rule.get("sim_noise", 0.0)),
            )
        )
    return signals


def default_bus_spec(
    signals: Sequence[SignalSpec],
) -> List[MessageSpec]:
    """把 SignalSpec 按 can_id 分组，加上 1~2 条"无关报文"，
    构造接近真实总线节奏的 MessageSpec 列表。

    无关报文不会被解析，但会被统计/CSV 记录——用于验证
    过滤、统计等只针对目标 ID 的链路是否正确。
    """
    by_id: dict = {}
    for sig in signals:
        by_id.setdefault(sig.can_id, []).append(sig)

    messages: List[MessageSpec] = []
    # 配置中出现的 ID：周期按 ID 取不同值（模拟不同 ECU）
    for idx, (can_id, sigs) in enumerate(sorted(by_id.items())):
        messages.append(
            MessageSpec(
                can_id=can_id,
                period_ms=100.0 * (idx + 1),
                jitter_ms=5.0,
                signals=sigs,
            )
        )

    # 追加的无关报文（总线上始终有大量数据，只有部分被解析）
    messages.append(
        MessageSpec(can_id=0x1F0, period_ms=20.0, jitter_ms=2.0)
    )
    messages.append(
        MessageSpec(can_id=0x1F1, period_ms=500.0, jitter_ms=20.0)
    )
    return messages


# ---------------------------------------------------------------------------
# 便捷函数：一次构造"配置驱动"的仿真器
# ---------------------------------------------------------------------------

def simulator_from_config(
    config_path: str = "sensor_config.json",
    error_frame_rate: float = 0.0,
    remote_frame_ids: Optional[Sequence[int]] = None,
    seed: Optional[int] = None,
) -> RealisticBusSimulator:
    """基于 sensor_config.json 直接构建仿真器（GUI / 测试均可用）。"""
    signals = signals_from_config(config_path)
    messages = default_bus_spec(signals)
    return RealisticBusSimulator(
        messages,
        error_frame_rate=error_frame_rate,
        remote_frame_ids=remote_frame_ids,
        seed=seed,
    )
