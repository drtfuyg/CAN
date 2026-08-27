from PySide6.QtCore import QTimer

from .base import CanBackend
from common.simulation import RealisticBusSimulator, simulator_from_config


class SimulatedCanBackend(CanBackend):
    """基于 RealisticBusSimulator 的模拟后端。

    相比旧版（固定 100ms 发送两条帧），现在模拟真实总线特征：
      - 多周期报文（10ms / 100ms / 200ms / 500ms ...）+ 周期抖动
      - 总线上存在无法解析的"无关报文"（参与统计、过滤、CSV）
      - 可选错误帧 / 远程帧注入（默认关闭，测试或特殊场景可开启）
      - 信号值由真实物理波形 + 噪声驱动
    """

    STEP_MS = 10

    def __init__(
        self,
        parent=None,
        error_frame_rate=0.0,
        remote_frame_ids=None,
        seed=None,
    ):
        super().__init__(parent)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._generate)
        self.bus = None
        self.error_frame_rate = error_frame_rate
        self.remote_frame_ids = list(remote_frame_ids or [])
        self.seed = seed

    def start(self):
        try:
            self.bus = simulator_from_config(
                "sensor_config.json",
                error_frame_rate=self.error_frame_rate,
                remote_frame_ids=self.remote_frame_ids,
                seed=self.seed,
            )
        except Exception as exc:
            self.bus = None
            self.error_occurred.emit(
                f"模拟模式初始化失败（请检查 sensor_config.json）：{exc}"
            )
            return
        self.status_changed.emit("模拟接收中（真实总线仿真）")
        self.timer.start(self.STEP_MS)

    def stop(self):
        self.timer.stop()
        self.bus = None
        self.status_changed.emit("已停止")

    def _generate(self):
        if self.bus is None:
            return
        for frame in self.bus.step(self.STEP_MS):
            self.frame_received.emit(frame)
