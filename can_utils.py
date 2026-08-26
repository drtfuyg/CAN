"""CAN 通用工具（纯 Python，无第三方依赖）。

ZLG 的 ZCAN_CAN_FRAME.can_id 为 32 位无符号整型，其中低 29 位是
真正的 CAN ID，高 3 位是类型标志：
    bit31 (0x80000000) : EFF  扩展帧标志（1=扩展帧）
    bit30 (0x40000000) : RTR  远程帧标志（1=远程帧）
    bit29 (0x20000000) : ERR  错误帧标志（1=错误帧）
本模块提供这些标志位的编解码函数，供真实后端与仿真/测试共用，
保证"编码 -> 传输 -> 解码"两侧规则完全一致。
"""


def decode_raw_can_id(raw_can_id: int):
    """把 ZLG 返回的 32 位 can_id 解码为 (can_id, extended, remote, error)。"""
    raw_can_id = int(raw_can_id)
    is_extended = bool(raw_can_id & (1 << 31))
    is_remote = bool(raw_can_id & (1 << 30))
    is_error = bool(raw_can_id & (1 << 29))
    can_id = raw_can_id & 0x1FFFFFFF
    return can_id, is_extended, is_remote, is_error


def encode_raw_can_id(
    can_id: int,
    extended: bool = False,
    remote: bool = False,
    error: bool = False,
) -> int:
    """把 CAN ID 与类型标志编码为 ZLG 的 32 位 can_id（decode 的逆操作）。"""
    raw = int(can_id) & 0x1FFFFFFF
    if extended:
        raw |= 1 << 31
    if remote:
        raw |= 1 << 30
    if error:
        raw |= 1 << 29
    return raw


def clamp_signed(value: int, length: int) -> int:
    """把整型钳制到指定字节数（length）的有符号可表示范围。"""
    bits = length * 8
    lo = -(1 << (bits - 1))
    hi = (1 << (bits - 1)) - 1
    return max(lo, min(hi, int(value)))


def clamp_unsigned(value: int, length: int) -> int:
    """把整型钳制到指定字节数的无符号可表示范围。"""
    bits = length * 8
    lo = 0
    hi = (1 << bits) - 1
    return max(lo, min(hi, int(value)))
