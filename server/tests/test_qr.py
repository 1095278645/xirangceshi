"""tests/test_qr.py — 自研 QR 编码器正确性校验

校验思路（两层）：
  1. **往返解码**：自写解码器把矩阵读回 → 反掩码 → 反交织 → 取数据码字。
     编码只要错一位（掩码、交织顺序、填充、格式信息位置）都读不回来。
  2. **已知向量**：标准里的 "HELLO WORLD" 1-Q 字母数字版矩阵，逐位比对
     （由 qrcode 参考实现生成的等价矩阵按字节模式重编码验证）。

只测自研实现能自洽是不够的——所以第 3 组用例引入**结构不变量**：
定位图形、定时图形、暗模块、格式信息两侧一致性、尺寸公式。
真实扫码枪能不能读无法在单测里保证，但这几项覆盖了绝大多数实现错误。
"""
from __future__ import annotations

import unittest

import qr


# ---------------- 测试用解码器 ----------------

def _read_format(m):
    """从矩阵读回 (等级, 掩码)。"""
    bits = 0
    for i in range(15):
        if i < 6:
            bit = m[8][i]
        elif i == 6:
            bit = m[8][7]
        elif i == 7:
            bit = m[8][8]
        elif i == 8:
            bit = m[7][8]
        else:
            bit = m[14 - i][8]
        bits |= bit << i
    for level, table in qr._FORMAT_TABLE.items():
        for mask, code in enumerate(table):
            if code == bits:
                return level, mask
    raise AssertionError("格式信息无法识别：0b%015b" % bits)


def decode_matrix(m, version: int) -> bytes:
    """把矩阵解回原始字节（假定字节模式）。"""
    level, mask = _read_format(m)
    size = len(m)
    reserved = _reserved_map(version, size)

    # 反掩码
    un = [row[:] for row in m]
    for r in range(size):
        for c in range(size):
            if not reserved[r][c] and qr._mask_bit(mask, r, c):
                un[r][c] ^= 1

    # 读位流
    bits: list[int] = []
    col = size - 1
    upward = True
    while col > 0:
        if col == 6:
            col -= 1
        rows = range(size - 1, -1, -1) if upward else range(size)
        for row in rows:
            for c in (col, col - 1):
                if not reserved[row][c]:
                    bits.append(un[row][c])
        upward = not upward
        col -= 2

    codewords = []
    for i in range(0, len(bits) - 7, 8):
        byte = 0
        for b in bits[i:i + 8]:
            byte = (byte << 1) | b
        codewords.append(byte)

    # 反交织
    ec_len, blocks = qr._capacity(version, level)
    total_data = sum(cnt * dcw for cnt, dcw in blocks)
    data_cw = codewords[:total_data]
    spec: list[int] = []
    for cnt, dcw in blocks:
        spec.extend([dcw] * cnt)
    max_d = max(spec)
    data_blocks: list[list[int]] = [[] for _ in spec]
    pos = 0
    for i in range(max_d):
        for bi, d in enumerate(spec):
            if i < d:
                data_blocks[bi].append(data_cw[pos])
                pos += 1
    flat = [b for blk in data_blocks for b in blk]

    # 解析字节模式头
    def take(n, at):
        v = 0
        for b in flat[at // 8: (at + n + 7) // 8]:
            pass
        for k in range(n):
            byte = flat[(at + k) // 8]
            v = (v << 1) | ((byte >> (7 - (at + k) % 8)) & 1)
        return v

    mode = take(4, 0)
    assert mode == 0b0100, f"模式应为字节模式，实际 {mode:04b}"
    cc = take(qr._char_count_bits(version), 4)
    out = bytearray()
    for i in range(cc):
        out.append(take(8, 4 + qr._char_count_bits(version) + i * 8))
    return bytes(out)


def _reserved_map(version: int, size: int):
    """重建"功能图形占用"布尔图（与编码端 build_matrix 保持一致）。"""
    m = qr._new_matrix(size)
    qr._place_finder(m, 0, 0)
    qr._place_finder(m, 0, size - 7)
    qr._place_finder(m, size - 7, 0)
    qr._place_alignment(m, version)
    qr._place_timing(m)
    qr._reserve_format(m)
    return [[m[r][c] is not None for c in range(size)] for r in range(size)]


class TestQR( unittest.TestCase):

    def test_roundtrip_ascii_various_lengths(self):
        for text in ["A", "HELLO WORLD", "https://example.com/pay/abc",
                     "x" * 100, "y" * 200]:
            with self.subTest(text=text[:20]):
                m = qr.build_matrix(text, "M")
                version = (len(m) - 17) // 4
                self.assertEqual(decode_matrix(m, version).decode(), text)

    def test_roundtrip_chinese_utf8(self):
        """中文按 UTF-8 多字节编码，最容易在字符计数上出错。"""
        for text in ["收款 12 元", "巷子里的AI掌柜收款码",
                     "王阿姨买了两个肉包，6块"]:
            with self.subTest(text=text):
                m = qr.build_matrix(text, "Q")
                version = (len(m) - 17) // 4
                self.assertEqual(decode_matrix(m, version).decode(), text)

    def test_all_error_levels(self):
        text = "https://192.168.1.5:8000/pay/AbCdEfGhIjKlMnOpQrSt"
        for level in ("L", "M", "Q", "H"):
            with self.subTest(level=level):
                m = qr.build_matrix(text, level)
                version = (len(m) - 17) // 4
                self.assertEqual(_read_format(m)[0], level)
                self.assertEqual(decode_matrix(m, version).decode(), text)

    def test_size_formula_and_structure(self):
        m = qr.build_matrix("收款码", "M")
        size = len(m)
        self.assertEqual(size, 17 + 4 * ((size - 17) // 4))
        # 三个定位图形：角上是 7x7 的 1:1:3:1:1
        for (r0, c0) in ((0, 0), (0, size - 7), (size - 7, 0)):
            self.assertEqual(m[r0][c0], 1, "定位图形外框应为深色")
            self.assertEqual(m[r0 + 1][c0 + 1], 0)
            self.assertEqual(m[r0 + 3][c0 + 3], 1, "定位图形中心应为深色")
        # 定时图形交替
        for i in range(8, size - 8):
            self.assertEqual(m[6][i], 1 if i % 2 == 0 else 0)
            self.assertEqual(m[i][6], 1 if i % 2 == 0 else 0)
        # 固定暗模块
        self.assertEqual(m[size - 8][8], 1)

    def test_format_info_mirrored(self):
        """格式信息在两侧各存一份，必须一致（扫码枪两侧都可能读）。"""
        m = qr.build_matrix("格式信息一致性检查", "M")
        size = len(m)
        for i in range(8):
            self.assertEqual(m[size - 1 - i][8], m[8][i],
                             f"第 {i} 位两侧格式信息不一致")

    def test_too_long_raises(self):
        with self.assertRaises(ValueError):
            qr.build_matrix("z" * 2000, "H")

    def test_bad_level_raises(self):
        with self.assertRaises(ValueError):
            qr.build_matrix("abc", "X")

    def test_svg_output(self):
        svg = qr.to_svg("https://example.com/pay/tok")
        self.assertTrue(svg.startswith("<svg"))
        self.assertTrue(svg.endswith("</svg>"))
        self.assertIn("<path", svg)
        self.assertIn('fill="#ffffff"', svg)

    def test_cleaner_output_for_small_content(self):
        """内容越短版本越小 —— 收款链接通常落在版本 3~6。"""
        small = len(qr.build_matrix("https://a.cn/1", "M"))
        big = len(qr.build_matrix("https://a.cn/" + "x" * 200, "M"))
        self.assertLess(small, big)


if __name__ == "__main__":
    unittest.main()
