import threading
import time
from datetime import datetime

from .base import CanBackend
from common.models import CanFrame
from common.can_utils import decode_raw_can_id

try:
    from backends.zlgcan import (
        ZCAN,
        ZCAN_USBCAN2,
        ZCAN_TYPE_CAN,
        ZCAN_STATUS_OK,
        ZCAN_CHANNEL_INIT_CONFIG,
        INVALID_DEVICE_HANDLE,
        INVALID_CHANNEL_HANDLE,
    )
    ZLG_IMPORT_ERROR = None
except Exception as exc:
    ZLG_IMPORT_ERROR = exc


class ZlgCanBackend(CanBackend):
    """ZLG USBCAN-II/II+ backend based on the supplied official demo/API docs."""

    def __init__(
        self,
        channel=0,
        baudrate=500000,
        listen_only=True,
        parent=None,
    ):
        super().__init__(parent)
        self.channel = int(channel)
        self.baudrate = int(baudrate)
        self.listen_only = bool(listen_only)

        self.zcanlib = None
        self.device_handle = None
        self.channel_handle = None

        self._running = False
        self._thread = None

    def start(self):
        if self._running:
            return

        if ZLG_IMPORT_ERROR is not None:
            self.error_occurred.emit(
                f"ZLG Python接口加载失败：{ZLG_IMPORT_ERROR}"
            )
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
                self.device_handle = None
                self.error_occurred.emit(
                    "打开 USBCAN-II/II+ 失败。请确认设备、Windows驱动、"
                    "USB连接以及 kerneldlls 均正常。"
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

            ret = self.zcanlib.ZCAN_SetValue(
                self.device_handle,
                f"{self.channel}/baud_rate",
                str(self.baudrate).encode("utf-8"),
            )
            if ret != ZCAN_STATUS_OK:
                self._safe_close()
                self.error_occurred.emit(
                    f"CAN{self.channel} 波特率 {self.baudrate} 设置失败。"
                )
                return

            cfg = ZCAN_CHANNEL_INIT_CONFIG()
            cfg.can_type = ZCAN_TYPE_CAN
            cfg.config.can.mode = 1 if self.listen_only else 0
            cfg.config.can.acc_code = 0
            cfg.config.can.acc_mask = 0xFFFFFFFF
            cfg.config.can.filter = 0

            self.channel_handle = self.zcanlib.InitCAN(
                self.device_handle, self.channel, cfg
            )
            if self.channel_handle in (
                None, INVALID_CHANNEL_HANDLE, 0
            ):
                self.channel_handle = None
                self._safe_close()
                self.error_occurred.emit(
                    f"初始化 CAN{self.channel} 失败。"
                )
                return

            ret = self.zcanlib.StartCAN(self.channel_handle)
            if ret != ZCAN_STATUS_OK:
                self._safe_close()
                self.error_occurred.emit(
                    f"启动 CAN{self.channel} 失败。"
                )
                return

            # 清空接收缓冲区，丢弃启动前的残留报文
            try:
                self.zcanlib.ClearBuffer(self.channel_handle)
            except Exception:
                pass

            self._running = True
            self._thread = threading.Thread(
                target=self._receive_loop,
                name="ZLG-CAN-Receive",
                daemon=True,
            )
            self._thread.start()

            mode_text = "只听" if self.listen_only else "正常"
            self.status_changed.emit(
                f"USBCAN-II/II+ CAN{self.channel} 接收中；"
                f"{self.baudrate} bps；{mode_text}模式"
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
                count = self.zcanlib.GetReceiveNum(
                    self.channel_handle, ZCAN_TYPE_CAN
                )
                if not count:
                    time.sleep(0.005)
                    continue

                read_num = min(int(count), 100)
                msgs, actual = self.zcanlib.Receive(
                    self.channel_handle, read_num, 100
                )

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
                            channel=self.channel,
                            is_extended=is_extended,
                            is_remote=is_remote,
                            is_error=is_error,
                            device_timestamp_us=int(msg.timestamp),
                            raw_dlc=raw_dlc,
                        )
                    )

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

    def stop(self):
        was_running = self._running
        self._running = False

        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=1.0)
        self._thread = None

        self._safe_close()

        if was_running:
            self.status_changed.emit("ZLG CAN 已停止")

    def _safe_close(self):
        if (
            self.zcanlib is not None
            and self.channel_handle not in (None, 0)
        ):
            try:
                self.zcanlib.ResetCAN(self.channel_handle)
            except Exception:
                pass
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
