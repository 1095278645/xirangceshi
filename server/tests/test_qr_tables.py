"""tests/test_qr_tables.py — QR 容量表与标准的**外部一致性**校验

为什么单独一个文件：test_qr.py 的往返解码只能证明我的编码器"自洽"，
如果容量表本身抄错了，往返照样能通过（编码解码用的是同一张错表），
但真实扫码枪一定读不出来。所以这里把容量表与**独立来源**逐条比对。

数据来源：ISO/IEC 18004 的纠错块布局表（EC_BLOCK_TABLE / ALIGNMENT_POSITIONS），
对照实现见 https://docs.rs/anyd/0.1.2/src/anyd/codes/qr/tables.rs.html
（anyd 是一个独立的 QR 编解码库，与本项目无关）。
"""
from __future__ import annotations

import unittest

import qr

# [版本][等级] = (每块纠错码字, [(块数, 每块数据码字数), ...])
# 与 anyd EC_BLOCK_TABLE 逐条对应（版本 1..10，L/M/Q/H）
STANDARD = {
    1: {"L": (7, [(1, 19)]), "M": (10, [(1, 16)]), "Q": (13, [(1, 13)]), "H": (17, [(1, 9)])},
    2: {"L": (10, [(1, 34)]), "M": (16, [(1, 28)]), "Q": (22, [(1, 22)]), "H": (28, [(1, 16)])},
    3: {"L": (15, [(1, 55)]), "M": (26, [(1, 44)]), "Q": (18, [(2, 17)]), "H": (22, [(2, 13)])},
    4: {"L": (20, [(1, 80)]), "M": (18, [(2, 32)]), "Q": (26, [(2, 24)]), "H": (16, [(4, 9)])},
    5: {"L": (26, [(1, 108)]), "M": (24, [(2, 43)]), "Q": (18, [(2, 15), (2, 16)]), "H": (22, [(2, 11), (2, 12)])},
    6: {"L": (18, [(2, 68)]), "M": (16, [(4, 27)]), "Q": (24, [(4, 19)]), "H": (28, [(4, 15)])},
    7: {"L": (20, [(2, 78)]), "M": (18, [(4, 31)]), "Q": (18, [(2, 14), (4, 15)]), "H": (26, [(4, 13), (1, 14)])},
    8: {"L": (24, [(2, 97)]), "M": (22, [(2, 38), (2, 39)]), "Q": (22, [(4, 18), (2, 19)]), "H": (26, [(4, 14), (2, 15)])},
    9: {"L": (30, [(2, 116)]), "M": (22, [(3, 36), (2, 37)]), "Q": (20, [(4, 16), (4, 17)]), "H": (24, [(4, 12), (4, 13)])},
    10: {"L": (18, [(2, 68), (2, 69)]), "M": (26, [(4, 43), (1, 44)]), "Q": (24, [(6, 19), (2, 20)]), "H": (28, [(6, 15), (2, 16)])},
}

# 标准对齐图形中心坐标（anyd ALIGNMENT_POSITIONS）
STANDARD_ALIGN = {
    1: [], 2: [6, 18], 3: [6, 22], 4: [6, 26], 5: [6, 30], 6: [6, 34],
    7: [6, 22, 38], 8: [6, 24, 42], 9: [6, 26, 46], 10: [6, 28, 50],
}


class TestQRTables(unittest.TestCase):

    def test_block_layout_matches_standard(self):
        for version, levels in STANDARD.items():
            for level, expected in levels.items():
                with self.subTest(version=version, level=level):
                    self.assertEqual(qr._capacity(version, level), expected)

    def test_total_codewords_match_standard(self):
        """每块数据+纠错之和必须等于该版本的标准总码字数。"""
        for version, levels in STANDARD.items():
            for level, (ec, blocks) in levels.items():
                total = sum(c * d for c, d in blocks) + sum(c for c, _ in blocks) * ec
                with self.subTest(version=version, level=level):
                    self.assertEqual(total, qr._TOTAL_CODEWORDS[version - 1])

    def test_every_version_level_is_complete(self):
        for version, levels in STANDARD.items():
            self.assertEqual(set(levels), {"L", "M", "Q", "H"},
                             f"版本 {version} 缺少等级")

    def test_alignment_positions_match_standard(self):
        self.assertEqual(qr._ALIGN_CENTERS, STANDARD_ALIGN)

    def test_byte_mode_capacity_matches_published_numbers(self):
        """字节模式数据容量（字符数）= (数据码字*8 - 4 - 字符计数位) / 8。

        对照常见容量表（版本1：L=17/M=14/Q=11/H=7；版本2：L=32/M=26/Q=20/H=14）。
        """
        expected = {
            (1, "L"): 17, (1, "M"): 14, (1, "Q"): 11, (1, "H"): 7,
            (2, "L"): 32, (2, "M"): 26, (2, "Q"): 20, (2, "H"): 14,
            (3, "L"): 53, (3, "M"): 42, (3, "Q"): 32, (3, "H"): 24,
        }
        for (version, level), n in expected.items():
            data_cw = sum(c * d for c, d in qr._capacity(version, level)[1])
            bits = data_cw * 8 - 4 - qr._char_count_bits(version)
            with self.subTest(version=version, level=level):
                self.assertEqual(bits // 8, n)

    def test_version_selection_boundaries(self):
        """刚好放得下 / 多一个字节就升版本 —— 边界最易出错。"""
        for level in ("L", "M", "Q", "H"):
            for version in (1, 2, 3, 4, 5):
                cap = (sum(c * d for c, d in qr._capacity(version, level)[1]) * 8
                       - 4 - qr._char_count_bits(version)) // 8
                with self.subTest(level=level, version=version, case="fit"):
                    self.assertEqual(qr._choose_version(cap, level), version)
                with self.subTest(level=level, version=version, case="overflow"):
                    self.assertGreater(qr._choose_version(cap + 1, level), version)


if __name__ == "__main__":
    unittest.main()
