from dataclasses import dataclass
from datetime import datetime
from typing import Optional

@dataclass
class CanFrame:
    pc_timestamp: datetime
    can_id: int
    data: bytes
    channel: int = 0
    is_extended: bool = False
    is_remote: bool = False
    is_error: bool = False
    device_timestamp_us: Optional[int] = None
    # 远程帧没有数据场，但报头仍携带请求的 DLC；
    # 保存它以便表格/CSV 能显示真实的远程帧长度。
    raw_dlc: Optional[int] = None

    @property
    def dlc(self):
        if self.raw_dlc is not None:
            return self.raw_dlc
        return len(self.data)

    @property
    def data_hex(self):
        return " ".join(f"{b:02X}" for b in self.data)

@dataclass
class SensorValue:
    key: str
    name: str
    value: float
    unit: str = ""
    source_can_id: Optional[int] = None
