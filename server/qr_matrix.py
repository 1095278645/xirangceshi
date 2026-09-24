"""qr_matrix.py — QR 矩阵构建（定位/对齐/时序图样、数据放置、掩码、罚分）

从 qr.py 外移（架构自检 L1）。本模块只做**矩阵几何**，不依赖 qr.py 的编码层；
`build_matrix` 仍在 qr.py（它需要编码后的码字），通过顶部 import 引用本模块的函数。
"""
from __future__ import annotations


def _new_matrix(size: int):
    return [[None] * size for _ in range(size)]


def _place_finder(m, row: int, col: int) -> None:
    for r in range(-1, 8):
        for c in range(-1, 8):
            rr, cc = row + r, col + c
            if 0 <= rr < len(m) and 0 <= cc < len(m):
                if 0 <= r <= 6 and 0 <= c <= 6:
                    dark = (r in (0, 6) or c in (0, 6) or (2 <= r <= 4 and 2 <= c <= 4))
                    m[rr][cc] = 1 if dark else 0
                else:
                    m[rr][cc] = 0          # 分隔符


def _place_alignment(m, version: int) -> None:
    from qr import _ALIGN_CENTERS   # 延迟导入：对齐表仍在 qr.py（避免顶层成环）
    centers = _ALIGN_CENTERS[version]
    for r in centers:
        for c in centers:
            # 跳过与定位图形重叠的位置
            if (r <= 8 and c <= 8) or (r <= 8 and c >= len(m) - 9) or (r >= len(m) - 9 and c <= 8):
                continue
            for dr in range(-2, 3):
                for dc in range(-2, 3):
                    dark = max(abs(dr), abs(dc)) != 1
                    m[r + dr][c + dc] = 1 if dark else 0


def _place_timing(m) -> None:
    size = len(m)
    for i in range(8, size - 8):
        if m[6][i] is None:
            m[6][i] = 1 if i % 2 == 0 else 0
        if m[i][6] is None:
            m[i][6] = 1 if i % 2 == 0 else 0


def _reserve_format(m) -> None:
    size = len(m)
    for i in range(9):
        if m[8][i] is None:
            m[8][i] = 0
        if m[i][8] is None:
            m[i][8] = 0
    for i in range(8):
        m[8][size - 1 - i] = 0
        m[size - 1 - i][8] = 0
    m[size - 8][8] = 1          # 固定的暗模块


def _place_data(m, bits: list[int]) -> None:
    """自右下角起、两列一组、蛇形向上填充。"""
    size = len(m)
    idx = 0
    col = size - 1
    upward = True
    while col > 0:
        if col == 6:            # 跳过竖向定时图形所在列
            col -= 1
        rows = range(size - 1, -1, -1) if upward else range(size)
        for row in rows:
            for c in (col, col - 1):
                if m[row][c] is None:
                    bit = bits[idx] if idx < len(bits) else 0
                    m[row][c] = bit
                    idx += 1
        upward = not upward
        col -= 2


def _mask_bit(mask: int, r: int, c: int) -> bool:
    if mask == 0:
        return (r + c) % 2 == 0
    if mask == 1:
        return r % 2 == 0
    if mask == 2:
        return c % 3 == 0
    if mask == 3:
        return (r + c) % 3 == 0
    if mask == 4:
        return (r // 2 + c // 3) % 2 == 0
    if mask == 5:
        return (r * c) % 2 + (r * c) % 3 == 0
    if mask == 6:
        return ((r * c) % 2 + (r * c) % 3) % 2 == 0
    return ((r + c) % 2 + (r * c) % 3) % 2 == 0


# 格式信息：BCH(15,5) 编码后与掩码 0x5412 异或（标准表，等级 L/M/Q/H × 掩码 0..7）
_FORMAT_TABLE = {
    "L": [0x77C4, 0x72F3, 0x7DAA, 0x789D, 0x662F, 0x6318, 0x6C41, 0x6976],
    "M": [0x5412, 0x5125, 0x5E7C, 0x5B4B, 0x45F9, 0x40CE, 0x4F97, 0x4AA0],
    "Q": [0x355F, 0x3068, 0x3F31, 0x3A06, 0x24B4, 0x2183, 0x2EDA, 0x2BED],
    "H": [0x1689, 0x13BE, 0x1CE7, 0x19D0, 0x0762, 0x0255, 0x0D0C, 0x083B],
}


def _place_format(m, level: str, mask: int) -> None:
    size = len(m)
    bits = _FORMAT_TABLE[level][mask]
    for i in range(15):
        bit = (bits >> i) & 1
        # 左上
        if i < 6:
            m[8][i] = bit
        elif i == 6:
            m[8][7] = bit
        elif i == 7:
            m[8][8] = bit
        elif i == 8:
            m[7][8] = bit
        else:
            m[14 - i][8] = bit
        # 复制到另一侧
        if i < 8:
            m[size - 1 - i][8] = bit
        else:
            m[8][size - 15 + i] = bit
    m[size - 8][8] = 1


def _apply_mask(m, mask: int, reserved) -> None:
    size = len(m)
    for r in range(size):
        for c in range(size):
            if reserved[r][c]:
                continue
            if _mask_bit(mask, r, c):
                m[r][c] ^= 1


def _penalty(m) -> int:
    """标准罚分规则，分数越低越好。"""
    size = len(m)
    score = 0
    # 规则1：同色连续 5 个以上
    for line in list(m) + [list(col) for col in zip(*m)]:
        run = 1
        for i in range(1, size):
            if line[i] == line[i - 1]:
                run += 1
            else:
                if run >= 5:
                    score += 3 + (run - 5)
                run = 1
        if run >= 5:
            score += 3 + (run - 5)
    # 规则2：2x2 同色块
    for r in range(size - 1):
        for c in range(size - 1):
            v = m[r][c]
            if v == m[r][c + 1] == m[r + 1][c] == m[r + 1][c + 1]:
                score += 3
    # 规则3：形如 1:1:3:1:1 的定位图形误判
    pat1 = [1, 0, 1, 1, 1, 0, 1, 0, 0, 0, 0]
    pat2 = [0, 0, 0, 0, 1, 0, 1, 1, 1, 0, 1]
    for line in list(m) + [list(col) for col in zip(*m)]:
        for i in range(size - 10):
            seg = line[i:i + 11]
            if seg == pat1 or seg == pat2:
                score += 40
    # 规则4：暗模块比例偏离 50%
    dark = sum(sum(row) for row in m)
    ratio = dark * 100 // (size * size)
    score += 10 * (abs(ratio - 50) // 5)
    return score


