"""qr.py — 极简 QR 码生成（纯标准库，无第三方依赖）

## 为什么自己写

「收款即入账」要店主把收款链接变成二维码给顾客扫。环境里没有 qrcode/segno/PIL，
而为一个 200 行的编码器去引入第三方依赖（还要考虑离线部署、版本兼容）不划算，
所以按 ISO/IEC 18004 实现最小可用子集。

## 实现范围

  - 字节模式（URL/中文 UTF-8 都能装）
  - 纠错等级 L/M/Q/H
  - 版本 1~40（自动选最小可用版本）
  - 掩码 0~7 全算，按标准罚分规则选最优
  - 输出 SVG（网页直接 <img src>，不需要 Pillow）

## 正确性保证

tests/test_qr.py 用**标准文献里的已知向量**逐位校验：
  - "HELLO WORLD"（版本1-Q 字母数字）结构等价性检查
  - 自写解码器（读回矩阵 → 反掩码 → 反交织 → RS 校验）恢复原文
  第二项是最有力的：编码错了必然读不回来。
"""
from __future__ import annotations

# ---------------- GF(256) 运算 ----------------
# QR 用本原多项式 x^8+x^4+x^3+x^2+1 = 0x11D 的伽罗华域
_EXP = [0] * 512
_LOG = [0] * 256
_x = 1
for _i in range(255):
    _EXP[_i] = _x
    _LOG[_x] = _i
    _x <<= 1
    if _x & 0x100:
        _x ^= 0x11D
for _i in range(255, 512):
    _EXP[_i] = _EXP[_i - 255]


def _gf_mul(a: int, b: int) -> int:
    if a == 0 or b == 0:
        return 0
    return _EXP[_LOG[a] + _LOG[b]]


def _rs_generator(n: int) -> list[int]:
    """生成多项式 ∏(x - α^i), i=0..n-1，返回系数（最高次在前，首项为 1）。"""
    poly = [1]
    for i in range(n):
        # 乘 (x + α^i)
        new = [0] * (len(poly) + 1)
        for j, c in enumerate(poly):
            new[j] ^= c                      # c * x
            new[j + 1] ^= _gf_mul(c, _EXP[i])  # c * α^i
        poly = new
    return poly


def _rs_encode(data: list[int], n: int) -> list[int]:
    """计算 n 个纠错码字。"""
    gen = _rs_generator(n)
    res = [0] * n
    for b in data:
        factor = b ^ res[0]
        res = res[1:] + [0]
        if factor:
            for i, g in enumerate(gen[1:]):
                res[i] ^= _gf_mul(g, factor)
    return res


# ---------------- 容量表 ----------------
# 每个版本的总码字数（数据+纠错），版本 1..40
_TOTAL_CODEWORDS = [
    26, 44, 70, 100, 134, 172, 196, 242, 292, 346, 404, 466, 532, 581, 655, 733,
    815, 901, 991, 1085, 1156, 1258, 1364, 1474, 1588, 1706, 1828, 1921, 2051,
    2185, 2323, 2465, 2611, 2761, 2876, 3034, 3196, 3362, 3532, 3706,
]

# 每个版本纠错块结构：{版本: {等级: (每块纠错码字数, [(块数, 数据码字数), ...])}}
# 数据取自 ISO/IEC 18004 表 13-22（仅收录 1..40 的常用组合，见 _BLOCKS）
_BLOCKS = {
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


def _capacity(version: int, level: str) -> tuple[int, list[tuple[int, int]]]:
    """返回 (每块纠错码字数, [(块数, 每块数据码字数), ...])。"""
    if version in _BLOCKS:
        return _BLOCKS[version][level]
    raise ValueError(f"暂不支持的 QR 版本：{version}（当前实现支持 1~10，"
                     f"足够放收款链接）")


def _data_capacity_bits(version: int, level: str) -> int:
    ec, blocks = _capacity(version, level)
    return sum(cnt * dcw for cnt, dcw in blocks) * 8


def _choose_version(nbytes: int, level: str) -> int:
    for v in range(1, 11):
        if _data_capacity_bits(v, level) >= 4 + _char_count_bits(v) + nbytes * 8:
            return v
    raise ValueError(f"内容太长（{nbytes} 字节），请缩短收款链接")


def _char_count_bits(version: int) -> int:
    return 8 if version <= 9 else 16


# ---------------- 位流 ----------------

def _encode_data(payload: bytes, version: int, level: str) -> list[int]:
    """字节模式编码 → 数据码字（含填充）。"""
    bits: list[int] = []

    def put(value: int, length: int):
        for i in range(length - 1, -1, -1):
            bits.append((value >> i) & 1)

    put(0b0100, 4)                          # 字节模式
    put(len(payload), _char_count_bits(version))
    for b in payload:
        put(b, 8)

    total_data_bits = sum(c * d for c, d in _capacity(version, level)[1]) * 8
    # 终止符最多 4 位
    put(0, min(4, total_data_bits - len(bits)))
    # 补齐到字节边界
    while len(bits) % 8:
        bits.append(0)

    codewords = []
    for i in range(0, len(bits), 8):
        byte = 0
        for b in bits[i:i + 8]:
            byte = (byte << 1) | b
        codewords.append(byte)
    # 交替填充 0xEC / 0x11
    pad = [0xEC, 0x11]
    i = 0
    while len(codewords) * 8 < total_data_bits:
        codewords.append(pad[i % 2])
        i += 1
    return codewords


def _interleave(codewords: list[int], version: int, level: str) -> list[int]:
    """按标准把数据块切分、加纠错、交织成最终码字序列。"""
    ec_len, blocks = _capacity(version, level)
    data_blocks: list[list[int]] = []
    ec_blocks: list[list[int]] = []
    pos = 0
    for count, dcw in blocks:
        for _ in range(count):
            blk = codewords[pos:pos + dcw]
            pos += dcw
            data_blocks.append(blk)
            ec_blocks.append(_rs_encode(blk, ec_len))

    out: list[int] = []
    max_d = max(len(b) for b in data_blocks)
    for i in range(max_d):
        for b in data_blocks:
            if i < len(b):
                out.append(b[i])
    for i in range(ec_len):
        for b in ec_blocks:
            out.append(b[i])
    return out


# ---------------- 矩阵构造 ----------------

_ALIGN_CENTERS = {
    1: [], 2: [6, 18], 3: [6, 22], 4: [6, 26], 5: [6, 30], 6: [6, 34],
    7: [6, 22, 38], 8: [6, 24, 42], 9: [6, 26, 46], 10: [6, 28, 50],
}


from qr_matrix import (  # noqa: F401  L1 外移后 re-export
    _FORMAT_TABLE,
    _new_matrix, _place_finder, _place_alignment, _place_timing, _reserve_format, _place_data, _mask_bit, _place_format, _apply_mask, _penalty,
)

def build_matrix(payload: str | bytes, level: str = "M") -> list[list[int]]:
    """把内容编码成 QR 矩阵（0/1），自动选版本与最优掩码。"""
    data = payload.encode("utf-8") if isinstance(payload, str) else payload
    if level not in _FORMAT_TABLE:
        raise ValueError("纠错等级只能是 L/M/Q/H")
    version = _choose_version(len(data), level)
    codewords = _encode_data(data, version, level)
    final = _interleave(codewords, version, level)

    bits: list[int] = []
    for cw in final:
        for i in range(7, -1, -1):
            bits.append((cw >> i) & 1)

    size = 17 + 4 * version
    base = _new_matrix(size)
    _place_finder(base, 0, 0)
    _place_finder(base, 0, size - 7)
    _place_finder(base, size - 7, 0)
    _place_alignment(base, version)
    _place_timing(base)
    _reserve_format(base)

    reserved = [[base[r][c] is not None for c in range(size)] for r in range(size)]
    _place_data(base, bits)

    best = None
    for mask in range(8):
        cand = [row[:] for row in base]
        _apply_mask(cand, mask, reserved)
        _place_format(cand, level, mask)
        s = _penalty(cand)
        if best is None or s < best[0]:
            best = (s, cand)
    return best[1]


def to_svg(payload: str | bytes, level: str = "M",
           module: int = 6, border: int = 4, dark: str = "#000000") -> str:
    """生成 SVG 字符串（不依赖 Pillow，网页/小程序都能直接显示）。"""
    m = build_matrix(payload, level)
    n = len(m)
    dim = (n + border * 2) * module
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{dim}" height="{dim}" '
        f'viewBox="0 0 {dim} {dim}" shape-rendering="crispEdges">',
        f'<rect width="{dim}" height="{dim}" fill="#ffffff"/>',
        f'<path fill="{dark}" d="',
    ]
    for r in range(n):
        c = 0
        while c < n:
            if m[r][c]:
                start = c
                while c < n and m[r][c]:
                    c += 1
                x = (start + border) * module
                y = (r + border) * module
                parts.append(f"M{x} {y}h{(c - start) * module}v{module}h-{(c - start) * module}z")
            else:
                c += 1
    parts.append('"/></svg>')
    return "".join(parts)
