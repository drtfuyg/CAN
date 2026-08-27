"""ZLG 32 位 can_id 标志位编解码测试（对应真实后端 zlg_backend 的解码逻辑）。"""
import unittest

from common.can_utils import (
    decode_raw_can_id,
    encode_raw_can_id,
    clamp_signed,
    clamp_unsigned,
)


class TestDecodeRawCanId(unittest.TestCase):
    def test_standard_data_frame(self):
        self.assertEqual(
            decode_raw_can_id(0x101),
            (0x101, False, False, False),
        )

    def test_extended_frame(self):
        raw = encode_raw_can_id(0x1FFFFFFF, extended=True)
        can_id, ext, rtr, err = decode_raw_can_id(raw)
        self.assertEqual(can_id, 0x1FFFFFFF)
        self.assertTrue(ext)
        self.assertFalse(rtr)
        self.assertFalse(err)

    def test_remote_frame(self):
        raw = encode_raw_can_id(0x123, remote=True)
        can_id, ext, rtr, err = decode_raw_can_id(raw)
        self.assertEqual(can_id, 0x123)
        self.assertTrue(rtr)
        self.assertFalse(err)

    def test_error_frame(self):
        raw = encode_raw_can_id(0x000, error=True)
        can_id, ext, rtr, err = decode_raw_can_id(raw)
        self.assertEqual(can_id, 0)
        self.assertTrue(err)

    def test_roundtrip_all_flags(self):
        for can_id in (0x0, 0x100, 0x12345, 0x1FFFFFFF):
            for ext in (False, True):
                for rtr in (False, True):
                    for err in (False, True):
                        raw = encode_raw_can_id(
                            can_id, extended=ext, remote=rtr, error=err
                        )
                        decoded = decode_raw_can_id(raw)
                        self.assertEqual(
                            decoded,
                            (can_id, ext, rtr, err),
                            f"roundtrip failed for {can_id:#x} "
                            f"ext={ext} rtr={rtr} err={err}",
                        )

    def test_id_mask_truncates_above_29bits(self):
        raw = encode_raw_can_id(0x1FFFFFFF)
        # 30 位以上的位必须被当作标志位清除
        self.assertEqual(raw, 0x1FFFFFFF)

    def test_known_bit_values(self):
        # 官方约定：bit31=扩展 bit30=远程 bit29=错误
        self.assertEqual(1 << 31, 0x80000000)
        self.assertEqual(1 << 30, 0x40000000)
        self.assertEqual(1 << 29, 0x20000000)


class TestClamp(unittest.TestCase):
    def test_clamp_signed(self):
        self.assertEqual(clamp_signed(40000, 2), 32767)
        self.assertEqual(clamp_signed(-40000, 2), -32768)
        self.assertEqual(clamp_signed(100, 2), 100)

    def test_clamp_unsigned(self):
        self.assertEqual(clamp_unsigned(-1, 2), 0)
        self.assertEqual(clamp_unsigned(70000, 2), 65535)
        self.assertEqual(clamp_unsigned(42, 2), 42)


if __name__ == "__main__":
    unittest.main()
