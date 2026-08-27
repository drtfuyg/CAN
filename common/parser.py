import json
import struct
from pathlib import Path
from common.models import SensorValue

# 允许的 CAN 数据场最大长度
MAX_CAN_DATA = 8


class SensorParser:
    """根据 sensor_config.json 解析 CAN 帧中的传感器数值。

    健壮性改进（相比旧版）：
      - 配置文件缺失/损坏时不再导致程序启动崩溃（记录 config_error）
      - 显式校验 start_byte/length/data_type，非法规则跳过而非产生错误数据
      - 可选收集每条规则的解析错误（collect_errors=True），便于定位配置问题
    """

    def __init__(self, config_path="sensor_config.json", collect_errors=False):
        self.config_path = Path(config_path)
        self.rules = []
        self.config_error = None
        self.collect_errors = bool(collect_errors)
        self.errors = []
        try:
            self.reload()
        except Exception as exc:
            # 启动时配置异常不应阻断程序，记录错误并保持空规则
            self.config_error = str(exc)

    def reload(self):
        """重新加载配置；配置非法时抛出异常由调用方决定如何处理。"""
        self.config_error = None
        try:
            text = self.config_path.read_text(encoding="utf-8")
            data = json.loads(text)
            rules = data.get("sensors", [])
            if not isinstance(rules, list):
                raise ValueError("sensors 必须是数组")
        except Exception:
            self.rules = []
            raise
        self.rules = rules
        return self.rules

    def _record_error(self, key, message):
        if not self.collect_errors:
            return
        self.errors.append({"key": key, "error": message})
        if len(self.errors) > 200:
            self.errors.pop(0)

    def parse(self, frame):
        """解析一帧，返回该帧命中规则的 SensorValue 列表。"""
        out = []
        for rule in self.rules:
            key = rule.get("key")
            try:
                if int(str(rule["can_id"]), 0) != frame.can_id:
                    continue

                start = int(rule["start_byte"])
                length = int(rule["length"])
                if start < 0 or length <= 0 or start + length > MAX_CAN_DATA:
                    self._record_error(
                        key,
                        f"非法字节区间 start={start} length={length}",
                    )
                    continue

                chunk = frame.data[start:start + length]
                if len(chunk) != length:
                    self._record_error(
                        key,
                        f"帧数据不足：需要 {length} 字节，实际 {len(chunk)} 字节",
                    )
                    continue

                order = rule.get("byte_order", "little")
                if order not in ("little", "big"):
                    self._record_error(key, f"非法 byte_order={order}")
                    continue

                data_type = rule.get("data_type", "int")

                if data_type == "float32":
                    if length != 4:
                        self._record_error(key, "float32 长度必须为 4")
                        continue
                    raw = struct.unpack(
                        "<f" if order == "little" else ">f", chunk
                    )[0]
                elif data_type == "int":
                    raw = int.from_bytes(
                        chunk,
                        order,
                        signed=bool(rule.get("signed", False)),
                    )
                else:
                    self._record_error(key, f"不支持的 data_type={data_type}")
                    continue

                value = (
                    raw * float(rule.get("scale", 1.0))
                    + float(rule.get("offset", 0.0))
                )

                out.append(
                    SensorValue(
                        key=key,
                        name=rule.get("name", key),
                        value=value,
                        unit=rule.get("unit", ""),
                        source_can_id=frame.can_id,
                    )
                )
            except Exception as exc:
                self._record_error(key, str(exc))
                continue
        return out
