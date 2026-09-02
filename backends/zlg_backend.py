import threading
import time
from datetime import datetime

from .base import CanBackend
from common.models import CanFrame
from common.can_utils import decode_raw_can_id

from zlgcan import (
    ZCAN,
    ZCAN_USBCAN2,
    ZCAN_TYPE_CAN,
    ZCAN_STATUS_OK,
    ZCAN_CHANNEL_INIT_CONFIG,
    ZCAN_Transmit_Data,
    INVALID_DEVICE_HANDLE,
    INVALID_CHANNEL_HANDLE,
    memset,
    addressof,
    sizeof,
)


# ZLG 通道初始化 can.mode 取值：0-正常 1-只听 2-自测(自发自收/回环)
MODE_MAP = {
    "normal": 0,
    "listen": 1,
    "loopback": 2,
}
MODE_TEXT = {
    "normal": "正常",
    "listen": "只听",
    "loopback": "自收自发",
}


class ZlgCanBackend(CanBackend):
    """ZLG USBCAN-II/II+ backend based on the supplied official demo/API docs."""

    def __init__(
        self,
        channel=0,
        baudrate=500000,
        channel_baudrates=None,
        mode="normal",
        loopback_id=0x123,
        loopback_interval=0.5,
        parent=None,
    ):
        super().__init__(parent)
        self.channel = int(channel)
        self.baudrate = int(baudrate)

        # iCAN 实验箱接入时需要同时使用两条总线：传感器通常接 CAN0，
        # 实验箱输出模块接 CAN1。USBCAN-II+ 的两个通道共用一个设备句柄，
        # 因此在真实模式下同时初始化两个通道；send_frame() 可用 channel
        # 参数选择实际发送通道。
        self.channels = tuple(sorted({self.channel, 1 - self.channel}))
        self.channel_baudrates = {
            int(ch): int(rate)
            for ch, rate in (channel_baudrates or {}).items()
        }
        for ch in self.channels:
            self.channel_baudrates.setdefault(ch, self.baudrate)

        if mode not in MODE_MAP:
            mode = "normal"
        self.mode = mode

        # 自收自发（回环）模式下测试帧参数
        self.loopback_id = int(loopback_id) & 0x1FFFFFFF
        self.loopback_interval = float(loopback_interval)

        self.zcanlib = None
        self.device_handle = None
        self.channel_handle = None
        self.channel_handles = {}

        self._running = False
        self._thread = None
        self._tx_thread = None
        self._tx_seq = 0
        # ZCAN DLL 的发送调用可能与接收线程并发，使用锁避免 ctypes
        # 结构体/句柄在关闭或发送时发生竞态。
        self._tx_lock = threading.Lock()

    def start(self):
        if self._running:
            return

        try:
            self.status_changed.emit("正在加载 ZLG 接口...")
            self.zcanlib = ZCAN()

            self.status_changed.emit("正在打开 USBCAN-II/II+...")
            self.device_handle = self.zcanlib.OpenDevice(
                ZCAN_USBCAN2, 0, 0
            )
            if self.device_handle in (
                None, INVALID_DEVICE_HANDLE, 0
            ):
                returned_handle = self.device_handle
                self.device_handle = None
                self.error_occurred.emit(
                    f"打开 USBCAN-II/II+ 失败（OpenDevice 返回 {returned_handle}）。\n"
                    "Windows 当前未向 ZLG 设备提供可用驱动；请在设备管理器中"
                    "检查 USB 设备是否显示 Code 28，安装 ZLG USBCAN 驱动后"
                    "重新插拔设备。并确认 USB 连接、设备指示灯和 kerneldlls 正常。"
                )
                return

            try:
                info = self.zcanlib.GetDeviceInf(self.device_handle)
                if info is not None:
                    self.status_changed.emit(
                        f"设备已打开：{info.hw_type}；"
                        f"CAN通道数 {info.can_num}"
                    )
            except Exception:
                pass

            for channel in self.channels:
                ret = self.zcanlib.ZCAN_SetValue(
                    self.device_handle,
                    f"{channel}/baud_rate",
                    str(self.channel_baudrates[channel]).encode("utf-8"),
                )
                if ret != ZCAN_STATUS_OK:
                    self._safe_close()
                    self.error_occurred.emit(
                        f"CAN{channel} 波特率 {self.channel_baudrates[channel]} 设置失败。"
                    )
                    return

                cfg = ZCAN_CHANNEL_INIT_CONFIG()
                cfg.can_type = ZCAN_TYPE_CAN
                cfg.config.can.mode = MODE_MAP[self.mode]
                cfg.config.can.acc_code = 0
                cfg.config.can.acc_mask = 0xFFFFFFFF
                cfg.config.can.filter = 0

                handle = self.zcanlib.InitCAN(
                    self.device_handle, channel, cfg
                )
                if handle in (None, INVALID_CHANNEL_HANDLE, 0):
                    self._safe_close()
                    self.error_occurred.emit(
                        f"初始化 CAN{channel} 失败。"
                    )
                    return

                self.channel_handles[channel] = handle
                ret = self.zcanlib.StartCAN(handle)
                if ret != ZCAN_STATUS_OK:
                    self._safe_close()
                    self.error_occurred.emit(
                        f"启动 CAN{channel} 失败。"
                    )
                    return

                # 清空接收缓冲区，丢弃启动前的残留报文
                try:
                    self.zcanlib.ClearBuffer(handle)
                except Exception:
                    pass

            # 保留主通道别名，兼容原有代码；新代码应使用 channel_handles。
            self.channel_handle = self.channel_handles.get(self.channel)

            self._running = True
            self._thread = threading.Thread(
                target=self._receive_loop,
                name="ZLG-CAN-Receive",
                daemon=True,
            )
            self._thread.start()

            # 自收自发（回环）模式下必须周期发送自发自收测试帧，
            if self.mode == "loopback":
                self._tx_thread = threading.Thread(
                    target=self._loopback_tx_loop,
                    name="ZLG-CAN-LoopbackTX",
                    daemon=True,
                )
                self._tx_thread.start()

            self.status_changed.emit(
                f"USBCAN-II/II+ CAN{','.join(map(str, self.channels))} 接收中；"
                f"波特率 {self._baud_summary()}；{MODE_TEXT[self.mode]}模式"
            )

        except OSError as exc:
            self._safe_close()
            self.error_occurred.emit(
                f"ZLG DLL 加载失败：{exc}\n"
                "请确认当前项目使用 x64 zlgcan.dll，且 kerneldlls "
                "与 zlgcan.dll 位于项目同级目录。"
            )
        except Exception as exc:
            self._safe_close()
            self.error_occurred.emit(
                f"启动 ZLG CAN 失败：{exc}"
            )

    def _receive_loop(self):
        while self._running:
            try:
                received_any = False
                for channel in self.channels:
                    handle = self.channel_handles.get(channel)
                    if handle in (None, 0):
                        continue
                    count = self.zcanlib.GetReceiveNum(handle, ZCAN_TYPE_CAN)
                    if not count:
                        continue

                    received_any = True
                    read_num = min(int(count), 100)
                    msgs, actual = self.zcanlib.Receive(handle, read_num, 100)

                    for msg in msgs[:actual]:
                        zframe = msg.frame

                        can_id, is_extended, is_remote, is_error = \
                            decode_raw_can_id(int(zframe.can_id))

                        dlc = min(int(zframe.can_dlc), 8)
                        if is_remote:
                            # 远程帧没有数据场，但保留请求的 DLC
                            data = b""
                            raw_dlc = dlc
                        else:
                            data = bytes(zframe.data[:dlc])
                            raw_dlc = None

                        self.frame_received.emit(
                            CanFrame(
                                pc_timestamp=datetime.now(),
                                can_id=can_id,
                                data=data,
                                channel=channel,
                                is_extended=is_extended,
                                is_remote=is_remote,
                                is_error=is_error,
                                device_timestamp_us=int(msg.timestamp),
                                raw_dlc=raw_dlc,
                            )
                        )

                if not received_any:
                    time.sleep(0.005)

            except Exception as exc:
                if self._running:
                    self.error_occurred.emit(
                     f"CAN 接收线程异常：{exc}"
                    )
                break

        # 线程正常/异常退出时，保证状态能反映出来
        if self._running:
            self.status_changed.emit("CAN 接收线程已停止")
            self._running = False

    def _baud_summary(self):
        """返回各通道实际配置的波特率，便于现场排查。"""
        return ", ".join(
            f"CAN{channel}={self.channel_baudrates[channel]} bps"
            for channel in self.channels
        )

    def _loopback_tx_loop(self):
        """自收自发（回环）模式：周期性发送自发自收测试帧。"""
        while self._running and self.mode == "loopback":
            try:
                self._transmit_loopback_frame()
            except Exception as exc:
                if self._running:
                    self.error_occurred.emit(
                        f"自收自发发送失败：{exc}"
                    )
                break
            time.sleep(self.loopback_interval)

    def _transmit_loopback_frame(self):
        """发送一条 transmit_type=2（自发自收）测试帧，数据为递增计数器。"""
        msgs = (ZCAN_Transmit_Data * 1)()
        memset(addressof(msgs), 0, sizeof(msgs))
        msgs[0].transmit_type = 2  # 0-正常发送，2-自发自收
        msgs[0].frame.can_id = self.loopback_id | (1 << 31)  # 扩展帧
        msgs[0].frame.can_dlc = 8
        for j in range(8):
            msgs[0].frame.data[j] = (self._tx_seq >> (8 * j)) & 0xFF
        self._tx_seq += 1
        with self._tx_lock:
            if not self._running or self.channel_handle in (None, 0):
                return
            ret = self.zcanlib.Transmit(self.channel_handle, msgs, 1)
        if ret < 1:
            self.status_changed.emit(f"自收自发：Transmit 返回 {ret}")

    def send_frame(self, can_id, data, extended=False, remote=False, channel=None):
        """以普通 CAN 发送方式发送一帧。

        ``loopback`` 模式的 transmit_type=2 只用于自测试；传感器查询和
        实验箱控制必须使用普通发送 transmit_type=0。返回 True 表示 DLL
        接受了该帧，False 表示后端尚未启动或发送失败。
        """
        if channel is None:
            channel = self.channel
        channel = int(channel)
        handle = self.channel_handles.get(channel)
        if not self._running or self.zcanlib is None or handle in (None, 0):
            return False
        if data is None:
            data = b""
        payload = bytes(data)
        if len(payload) > 8:
            raise ValueError("CAN 数据长度不能超过 8 字节")
        can_id = int(can_id)
        if can_id < 0 or can_id > 0x1FFFFFFF:
            raise ValueError("CAN ID 必须在 0x00000000～0x1FFFFFFF 范围内")

        msgs = (ZCAN_Transmit_Data * 1)()
        memset(addressof(msgs), 0, sizeof(msgs))
        msgs[0].transmit_type = 0  # 普通发送
        raw_id = can_id
        if extended:
            raw_id |= 1 << 31
        if remote:
            raw_id |= 1 << 30
        msgs[0].frame.can_id = raw_id
        msgs[0].frame.can_dlc = len(payload)
        for index, value in enumerate(payload):
            msgs[0].frame.data[index] = value

        with self._tx_lock:
            if not self._running or handle in (None, 0):
                return False
            ret = self.zcanlib.Transmit(handle, msgs, 1)
        if ret < 1:
            # 普通模式下，总线上没有其它已上电节点提供 ACK、CAN_H/CAN_L
            # 接反、波特率不一致或控制器进入错误状态时，旧款 USBCAN-II+
            # 驱动可能直接返回 0。这属于可恢复的总线状态，不弹模态错误框；
            # 上位机保持运行并允许后续查询自动重试。
            details = []
            try:
                status = self.zcanlib.ReadChannelStatus(handle)
                if status is not None:
                    details.append(
                        f"TEC={int(status.regTECounter)}, "
                        f"REC={int(status.regRECounter)}"
                    )
            except Exception:
                pass
            try:
                err = self.zcanlib.ReadChannelErrInfo(handle)
                if err is not None and int(err.error_code):
                    details.append(f"错误码=0x{int(err.error_code):X}")
            except Exception:
                pass
            diagnostic = "；" + "，".join(details) if details else ""
            self.status_changed.emit(
                f"CAN{channel} 发送失败：Transmit 返回 {ret}{diagnostic}。"
                f"请检查设备供电、CAN_H/CAN_L、"
                f"{self.channel_baudrates.get(channel, self.baudrate)} bps 和终端电阻"
            )
            return False

        # 发送成功后，把该帧广播给界面显示（类似 CANTest 的发送记录），
        # 这样“原始 CAN”里也能看到自己发出的查询/控制帧（方向 TX）。
        self.frame_transmitted.emit(
            CanFrame(
                pc_timestamp=datetime.now(),
                can_id=can_id,
                data=payload,
                channel=channel,
                is_extended=bool(extended),
                is_remote=bool(remote),
                is_error=False,
                device_timestamp_us=int(time.time() * 1_000_000),
                direction="TX",
            )
        )
        return True

    def stop(self):
        was_running = self._running
        self._running = False

        if self._tx_thread and self._tx_thread.is_alive():
            self._tx_thread.join(timeout=1.0)
        self._tx_thread = None

        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=1.0)
        self._thread = None

        self._safe_close()

        if was_running:
            self.status_changed.emit("ZLG CAN 已停止")

    def _safe_close(self):
        if self.zcanlib is not None:
            for handle in list(self.channel_handles.values()):
                if handle not in (None, 0):
                    try:
                        self.zcanlib.ResetCAN(handle)
                    except Exception:
                        pass
        self.channel_handles.clear()
        self.channel_handle = None

        if (
            self.zcanlib is not None
            and self.device_handle not in (None, 0)
        ):
            try:
                self.zcanlib.CloseDevice(self.device_handle)
            except Exception:
                pass
        self.device_handle = None
