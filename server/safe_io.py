"""安全文件操作：受保护路径白名单 + 原子写入（Pattern 20/21）。

对标 EvoMap/evolver 的 Protected Paths + Atomic Write 模式：
  - PROTECTED 白名单：源码/数据库/配置文件禁止被自动化脚本直接写入；
  - atomic_write_* ：先写 .tmp 再 os.replace（原子操作），防止写到一半崩溃导致文件损坏。
"""
import json
import logging
import os
from pathlib import Path

log = logging.getLogger("safe_io")

# ---- 受保护路径白名单（Pattern 20: Protected Paths）----

# 受保护扩展名：这些类型的文件禁止被自动化脚本直接写入
_PROTECTED_EXTS = frozenset({".py", ".db", ".sqlite", ".sqlite3"})

# 受保护文件名：关键配置/清单文件
_PROTECTED_NAMES = frozenset({
    ".gitignore", "README.md", "project.config.json",
    "initial_genes.json", "app.json", "app.js", "app.wxss",
    "sitemap.json",
})

# 受保护目录：这些目录下的文件禁止被自动化脚本修改
_PROTECTED_DIRS = frozenset({
    "routers", "tests", "miniprogram", "static", ".git", ".venv",
})


def is_protected(filepath: str | Path) -> bool:
    """检查文件是否在受保护白名单中——禁止自动化脚本写入。"""
    p = Path(filepath)
    name = p.name
    # 1. 受保护扩展名（.py / .db / .sqlite）
    if p.suffix in _PROTECTED_EXTS:
        return True
    # 2. 受保护文件名
    if name in _PROTECTED_NAMES:
        return True
    # 3. 受保护目录（路径中包含这些目录名）
    for part in p.parts[:-1]:
        if part in _PROTECTED_DIRS:
            return True
    return False


# ---- 原子写入（Pattern 21: Atomic Write）----

def atomic_write_text(filepath: str | Path, content: str, *, encoding: str = "utf-8") -> None:
    """原子写入文本文件：先写 .tmp 再 os.replace，防止写到一半崩溃导致损坏。"""
    p = Path(filepath)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    try:
        with open(tmp, "w", encoding=encoding) as f:
            f.write(content)
        os.replace(str(tmp), str(p))  # 原子操作（POSIX 与 Windows 均原子）
    except Exception:
        tmp.unlink(missing_ok=True)
        raise


def atomic_write_json(filepath: str | Path, data: dict | list, *, indent: int = 2) -> None:
    """原子写入 JSON 文件：先写 .tmp 再 os.replace。"""
    p = Path(filepath)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=indent)
        os.replace(str(tmp), str(p))
    except Exception:
        tmp.unlink(missing_ok=True)
        raise


def atomic_write_bytes(filepath: str | Path, data: bytes) -> None:
    """原子写入二进制文件（如 Excel 报表）：先写 .tmp 再 os.replace。"""
    p = Path(filepath)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    try:
        with open(tmp, "wb") as f:
            f.write(data)
        os.replace(str(tmp), str(p))
    except Exception:
        tmp.unlink(missing_ok=True)
        raise


# ---- 安全写入（受保护检查 + 原子写入）----

def safe_write_text(filepath: str | Path, content: str, *, encoding: str = "utf-8") -> None:
    """安全写入：先检查受保护白名单，通过后原子写入。"""
    if is_protected(filepath):
        raise PermissionError(f"受保护文件禁止写入: {filepath}")
    atomic_write_text(filepath, content, encoding=encoding)


def safe_write_json(filepath: str | Path, data: dict | list, *, indent: int = 2) -> None:
    """安全写入 JSON：先检查受保护白名单，通过后原子写入。"""
    if is_protected(filepath):
        raise PermissionError(f"受保护文件禁止写入: {filepath}")
    atomic_write_json(filepath, data, indent=indent)
