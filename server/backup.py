"""backup.py — 数据备份 / 导出 / 恢复

为什么必须有：账本是财务数据，原先只存在店主电脑上的单个 SQLite 文件里，
没有任何备份能力 —— 电脑坏掉、重装系统、误删就是永久丢失。这属于产品级硬伤。

设计要点：
  - **一致性**：用 SQLite 的 `VACUUM INTO` 做热备份（会先整理数据库再落盘），
    不会备份到"写了一半"的状态；WAL 模式下也安全。
  - **多版本 + 自动清理**：自动备份按时间戳存多份，只保留最近 N 份，
    避免长期运行把磁盘占满。
  - **恢复前置保护**：恢复前先把当前库另存为 pre-restore 快照，
    恢复错了还能退回去。
  - **导出包**：zip 内含数据库快照 + manifest（版本/时间/记录数），
    并**可选**带上 config.local.json —— 默认不带，避免把 API Key 到处传播。
"""
from __future__ import annotations

import contextlib
import json
import logging
import os
import shutil
import sqlite3
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path

import config
from safe_io import atomic_write_json

log = logging.getLogger("backup")

# 注意：`with sqlite3.connect(...)` 只提交/回滚事务，**不会关闭连接**。
# 本模块多处需要连完就释放（否则 Windows 下 -wal/-shm 被占用、文件删不掉、
# 恢复时替换库文件也会失败），所以统一用 contextlib.closing 显式关闭。

# 说明：不在这里 `from config import DB_PATH`。
# 那样会在导入时把路径**绑定死**，测试或运行期改 config.DB_PATH / db.DB_PATH
# 都不生效（会去备份真实的库）。统一用函数取当前值。


def _db_path() -> Path:
    """当前数据库路径（每次实时读取，便于测试与运行期重定向）。

    多店模式下跟随店上下文：备份的必须是"当前这家店"的库，
    否则 A 店点备份会把 B 店的数据存下来（只有单店时才等价于 config.DB_PATH）。
    """
    import shops                      # 延迟导入，避免与 db.py 形成导入环
    return Path(shops.resolve_db_path() or config.DB_PATH)


def _backup_dir() -> Path:
    """备份目录（每次实时读取 config.DATA_DIR）。

    多店模式按店分目录，避免两家的备份混在一起、互相触发保留策略清理。
    """
    import shops
    base = Path(config.DATA_DIR) / "backups"
    sid = shops.current_shop_id()
    if sid is None or sid == shops.DEFAULT_SHOP_ID:
        return base
    return base / f"shop-{sid}"


EXPORT_PREFIX = "ai_shopkeeper_export_"
MANIFEST_NAME = "manifest.json"
DB_IN_BUNDLE = "ai_shopkeeper.db"
CONFIG_NAME = "config.local.json"

# 自动备份保留份数（手动备份不受此限制）
KEEP_AUTO_BACKUPS = 14
# 单个备份文件大小上限（防止异常膨胀）
MAX_BUNDLE_FILES = 50


def _now_stamp() -> str:
    """文件名用时间戳。带毫秒是必要的：只到秒的话，同一秒内连续备份会
    生成同名文件互相覆盖（实测连做 4 次只剩 1 份）。"""
    return datetime.now().strftime("%Y%m%d-%H%M%S-%f")[:-3]


def _ensure_dir() -> Path:
    d = _backup_dir()
    d.mkdir(parents=True, exist_ok=True)
    return d


# ---------------- 核心：一致性快照 ----------------

def _snapshot_to(path: Path) -> None:
    """把当前数据库一致性快照写到指定路径。

    用 VACUUM INTO：SQLite 会新建一个已整理、事务一致的副本，
    比直接复制文件安全（后者可能复制到写入中途的状态，WAL 下还会丢最新提交）。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        try:
            path.unlink()
        except OSError:
            # 目标被占用（例如另一连接正在读它）：VACUUM INTO 要求目标不存在，
            # 此时换一个带序号的临时名，避免整个备份失败。
            path = path.with_name(f"{path.stem}-{os.getpid()}{path.suffix}")
    with contextlib.closing(sqlite3.connect(str(_db_path()))) as conn:
        conn.execute("VACUUM INTO ?", (str(path),))


def _db_stats() -> dict:
    """记录数等概要，用于 manifest 与恢复前确认。"""
    stats = {}
    try:
        with contextlib.closing(sqlite3.connect(str(_db_path()))) as conn:
            for table in ("transactions", "customers", "memories", "reminders",
                          "vouchers", "products", "invoices"):
                try:
                    stats[table] = conn.execute(
                        f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                except sqlite3.Error:
                    pass
    except sqlite3.Error as e:  # noqa: BLE001
        log.warning("读取数据库概要失败：%s", e)
    return stats


def db_size() -> int:
    try:
        return _db_path().stat().st_size
    except OSError:
        return 0


# ---------------- 备份清单 ----------------

def list_backups() -> list[dict]:
    """列出全部备份（含导出包），按时间倒序。"""
    _ensure_dir()
    out = []
    for p in sorted(_backup_dir().iterdir(), key=lambda x: x.name, reverse=True):
        if not p.is_file():
            continue
        is_bundle = p.suffix == ".zip"
        out.append({
            "name": p.name,
            "size": p.stat().st_size,
            "created_at": datetime.fromtimestamp(p.stat().st_mtime).strftime(
                "%Y-%m-%d %H:%M:%S"),
            "kind": "export" if is_bundle else (
                "pre_restore" if p.name.startswith("pre_restore") else (
                    "manual" if p.name.startswith("manual") else "auto")),
        })
    return out


def _prune_auto(keep: int | None = None) -> int:
    """只清理自动备份，保留最近 keep 份；手动备份与恢复前快照不删。

    keep 默认在**调用时**从模块常量读取，而不是写成参数默认值 ——
    参数默认值在函数定义时求值，之后改 KEEP_AUTO_BACKUPS 不会生效
    （实测：调小保留份数后旧备份依然不被清理）。
    """
    keep = KEEP_AUTO_BACKUPS if keep is None else keep
    autos = sorted(_backup_dir().glob("auto-*.db"), key=lambda x: x.name)
    removed = 0
    for p in (autos[:-keep] if len(autos) > keep else []):
        try:
            p.unlink()
            removed += 1
        except OSError as e:  # noqa: BLE001
            log.warning("清理旧备份失败 %s: %s", p.name, e)
    return removed


# ---------------- 创建备份 ----------------

def create_backup(kind: str = "manual", note: str = "") -> dict:
    """创建一份数据库备份。kind: auto / manual / pre_restore。"""
    _ensure_dir()
    name = f"{kind}-{_now_stamp()}.db"
    target = _backup_dir() / name
    _snapshot_to(target)
    info = {
        "name": name,
        "path": str(target),
        "size": target.stat().st_size,
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "kind": kind,
        "note": note,
        "stats": _db_stats(),
    }
    log.info("备份完成 %s（%.1f KB）", name, info["size"] / 1024)
    if kind == "auto":
        _prune_auto()
    return info


# ---------------- 导出包 ----------------

def export_bundle(include_config: bool = False) -> dict:
    """导出 zip：数据库快照 + manifest（+ 可选配置）。

    默认不含 config.local.json（里面有 API Key），避免导出包被随手转发时泄密。
    """
    _ensure_dir()
    stamp = _now_stamp()
    name = f"{EXPORT_PREFIX}{stamp}.zip"
    target = _backup_dir() / name
    with tempfile.TemporaryDirectory() as td:
        snap = Path(td) / DB_IN_BUNDLE
        _snapshot_to(snap)
        manifest = {
            "app": "巷子里的AI掌柜",
            "exported_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "db_file": DB_IN_BUNDLE,
            "db_size": snap.stat().st_size,
            "stats": _db_stats(),
            "includes_config": bool(include_config),
            "note": "恢复方式：设置页上传该 zip，或调用 POST /api/backup/restore",
        }
        with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as z:
            z.write(snap, DB_IN_BUNDLE)
            z.writestr(MANIFEST_NAME, json.dumps(manifest, ensure_ascii=False, indent=2))
            if include_config:
                cfg = Path(config.BASE_DIR) / CONFIG_NAME
                if cfg.exists():
                    z.write(cfg, CONFIG_NAME)
    log.info("导出完成 %s（%.1f KB）", name, target.stat().st_size / 1024)
    return {"name": name, "path": str(target), "size": target.stat().st_size,
            "includes_config": bool(include_config)}


# ---------------- 校验与恢复 ----------------

def _validate_sqlite(path: Path) -> None:
    """确认是合法的 SQLite 库，且含本项目必需表。"""
    if not path.exists() or path.stat().st_size == 0:
        raise ValueError("备份文件为空")
    try:
        with contextlib.closing(
                sqlite3.connect(f"file:{path}?mode=ro", uri=True)) as conn:
            ok = conn.execute("PRAGMA integrity_check").fetchone()[0]
            if ok != "ok":
                raise ValueError(f"数据库完整性检查未通过：{ok}")
            names = {r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    except sqlite3.DatabaseError as e:
        raise ValueError(f"不是有效的 SQLite 数据库：{e}") from e
    missing = {"transactions", "customers"} - names
    if missing:
        raise ValueError(f"缺少必需的表：{sorted(missing)}（可能不是本项目的备份）")


def restore_from_file(src: Path, *, auto_snapshot: bool = True) -> dict:
    """用指定文件替换当前数据库。

    保护措施：
      1. 先校验源文件是合法且含必需表的 SQLite 库；
      2. 默认先把当前库另存为 pre_restore 快照（恢复错了能退回去）；
      3. 用临时文件 + os.replace 原子替换，避免替换中途崩溃留下半个库。
    """
    src = Path(src)
    _validate_sqlite(src)
    _ensure_dir()
    dbp = _db_path()
    snapshot = None
    if auto_snapshot and dbp.exists():
        snap_name = f"pre_restore-{_now_stamp()}.db"
        snap_path = _backup_dir() / snap_name
        _snapshot_to(snap_path)
        snapshot = snap_name
        log.info("恢复前已留存当前数据快照：%s", snap_name)

    tmp = dbp.with_suffix(dbp.suffix + ".restoring")
    shutil.copyfile(src, tmp)
    # 连带清理 WAL/SHM，避免用旧日志回放覆盖刚恢复的数据
    for suffix in ("-wal", "-shm"):
        side = Path(str(dbp) + suffix)
        if side.exists():
            side.unlink()
    import os
    os.replace(str(tmp), str(dbp))
    stats = _db_stats()
    log.info("恢复完成，当前记录数：%s", stats)
    return {"ok": True, "restored_from": src.name,
            "pre_restore_snapshot": snapshot, "stats": stats}


def restore_from_bundle(src: Path) -> dict:
    """从导出 zip 恢复：取出其中的数据库再走 restore_from_file。"""
    src = Path(src)
    if not zipfile.is_zipfile(src):
        if src.suffix == ".db":
            return restore_from_file(src)      # 也接受直接上传 .db
        raise ValueError("不是有效的 zip 导出包")
    with zipfile.ZipFile(src) as z:
        names = z.namelist()
        if DB_IN_BUNDLE not in names:
            raise ValueError(f"导出包缺少 {DB_IN_BUNDLE}")
        if len(names) > MAX_BUNDLE_FILES:
            raise ValueError("导出包文件数异常")
        with tempfile.TemporaryDirectory() as td:
            extracted = Path(z.extract(DB_IN_BUNDLE, td))
            result = restore_from_file(extracted)
    # 若包里带了配置，同时恢复配置（显式告知调用方）
    with zipfile.ZipFile(src) as z:
        if CONFIG_NAME in z.namelist():
            with tempfile.TemporaryDirectory() as td:
                cfg = Path(z.extract(CONFIG_NAME, td))
                cfg.replace(Path(config.BASE_DIR) / CONFIG_NAME)
            result["config_restored"] = True
    return result


# ---------------- 定期备份 ----------------

def auto_backup_if_needed(min_interval_hours: int = 20) -> dict | None:
    """距上次自动备份超过 min_interval_hours 才做，避免频繁重启时反复备份。"""
    _ensure_dir()
    autos = sorted(_backup_dir().glob("auto-*.db"), key=lambda x: x.name)
    if autos:
        last = datetime.fromtimestamp(autos[-1].stat().st_mtime)
        hours = (datetime.now() - last).total_seconds() / 3600
        if hours < min_interval_hours:
            log.debug("距上次自动备份 %.1f 小时，跳过", hours)
            return None
    return create_backup("auto", note="定期自动备份")
