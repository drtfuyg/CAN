"""SM1810C 温湿度传感器的固定 CAN 协议。

手册示例：向节点 1 发送 ``01 03 00 00 00 02``（CAN ID 0x001），
传感器从 CAN ID 0x000 返回 ``01 03 04 TT TT HH HH``。温度和湿度
均为大端无符号整数，实际值为原始值 / 100。
"""

from common.models import CanFrame, SensorValue

SM1810C_QUERY_ID = 0x001
SM1810C_RESPONSE_ID = 0x000
SM1810C_QUERY_DATA = bytes((0x01, 0x03, 0x00, 0x00, 0x00, 0x02))


def parse_response(frame: CanFrame, node_id: int = 1):
    """解析一帧 SM1810C 响应，返回 SensorValue 列表。"""
    if frame is None or frame.is_remote or frame.is_error:
        return []
    if frame.can_id != SM1810C_RESPONSE_ID or len(frame.data) < 7:
        return []
    if frame.data[0] != (int(node_id) & 0xFF) or frame.data[1] != 0x03:
        return []
    if frame.data[2] < 4:
        return []

    temperature = int.from_bytes(frame.data[3:5], "big", signed=False) / 100.0
    humidity = int.from_bytes(frame.data[5:7], "big", signed=False) / 100.0
    return [
        SensorValue("temperature", "温度", temperature, "℃", frame.can_id),
        SensorValue("humidity", "湿度", humidity, "%RH", frame.can_id),
    ]

