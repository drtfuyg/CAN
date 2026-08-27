"""CSV 记录器测试。"""
import csv
import os
import tempfile
import unittest
from datetime import datetime

from common.data_logger import CsvCanLogger
from common.models import CanFrame


class TestCsvCanLogger(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "can_log.csv")

    def tearDown(self):
        self.tmp.cleanup()

    def _read_rows(self):
        with open(self.path, "r", encoding="utf-8-sig", newline="") as f:
            return list(csv.reader(f))

    def test_header_and_data_row(self):
        logger = CsvCanLogger()
        logger.open(self.path)
        self.assertTrue(logger.is_open)

        logger.write(
            CanFrame(
                pc_timestamp=datetime(2026, 8, 26, 12, 0, 0, 123456),
                can_id=0x101,
                data=b"\x01\x02\x03\x04",
                channel=1,
                is_extended=True,
                device_timestamp_us=12345678,
            )
        )
        logger.close()
        self.assertFalse(logger.is_open)

        rows = self._read_rows()
        self.assertEqual(
            rows[0],
            ["pc_timestamp", "device_timestamp_us", "channel", "can_id",
             "dlc", "data", "extended", "remote", "error"],
        )
        self.assertEqual(rows[1][0], "2026-08-26T12:00:00.123")
        self.assertEqual(rows[1][1], "12345678")
        self.assertEqual(rows[1][2], "1")
        self.assertEqual(rows[1][3], "0x101")
        self.assertEqual(rows[1][4], "4")
        self.assertEqual(rows[1][5], "01 02 03 04")
        self.assertEqual(rows[1][6], "1")  # extended
        self.assertEqual(rows[1][7], "0")  # remote
        self.assertEqual(rows[1][8], "0")  # error

    def test_none_timestamp_blank(self):
        logger = CsvCanLogger()
        logger.open(self.path)
        logger.write(
            CanFrame(
                pc_timestamp=datetime(2026, 8, 26, 12, 0, 0),
                can_id=0x100,
                data=b"\x00",
                device_timestamp_us=None,
            )
        )
        logger.close()
        rows = self._read_rows()
        self.assertEqual(rows[1][1], "")

    def test_remote_frame_dlc_preserved(self):
        logger = CsvCanLogger()
        logger.open(self.path)
        logger.write(
            CanFrame(
                pc_timestamp=datetime(2026, 8, 26, 12, 0, 0),
                can_id=0x200,
                data=b"",
                is_remote=True,
                raw_dlc=8,
            )
        )
        logger.close()
        rows = self._read_rows()
        self.assertEqual(rows[1][4], "8")   # dlc
        self.assertEqual(rows[1][5], "")    # data 空

    def test_write_without_open_is_noop(self):
        logger = CsvCanLogger()
        logger.write(
            CanFrame(
                pc_timestamp=datetime(2026, 8, 26),
                can_id=0x100,
                data=b"\x00",
            )
        )  # 不应抛异常
        logger.close()

    def test_reopen_rewrites_file(self):
        logger = CsvCanLogger()
        logger.open(self.path)
        logger.write(
            CanFrame(pc_timestamp=datetime(2026, 8, 26), can_id=1, data=b"\x01")
        )
        logger.open(self.path)  # 重新打开会覆盖
        rows = self._read_rows()
        self.assertEqual(len(rows), 1)  # 只有表头


if __name__ == "__main__":
    unittest.main()
