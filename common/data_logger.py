import csv
import time
from pathlib import Path

class CsvCanLogger:
    FLUSH_INTERVAL_S = 0.5

    def __init__(self):
        self.file = None
        self.writer = None
        self._last_flush = 0.0

    def open(self, path):
        self.close()
        self.file = Path(path).open(
            "w", newline="", encoding="utf-8-sig"
        )
        self.writer = csv.writer(self.file)
        self._last_flush = time.monotonic()
        self.writer.writerow([
            "pc_timestamp",
            "device_timestamp_us",
            "channel",
            "can_id",
            "dlc",
            "data",
            "extended",
            "remote",
            "error",
        ])
        # 表头立即落盘，避免刚打开就异常时表头丢失
        self.file.flush()

    def write(self, frame):
        if not self.writer:
            return
        self.writer.writerow([
            frame.pc_timestamp.isoformat(timespec="milliseconds"),
            "" if frame.device_timestamp_us is None else frame.device_timestamp_us,
            frame.channel,
            f"0x{frame.can_id:X}",
            frame.dlc,
            frame.data_hex,
            int(frame.is_extended),
            int(frame.is_remote),
            int(frame.is_error),
        ])
        now = time.monotonic()
        if now - self._last_flush >= self.FLUSH_INTERVAL_S:
            self.file.flush()
            self._last_flush = now

    def flush(self):
        if self.file:
            self.file.flush()
        self._last_flush = time.monotonic()

    def close(self):
        if self.file:
            self.file.flush()
            self.file.close()
        self.file = None
        self.writer = None

    @property
    def is_open(self):
        return self.file is not None
