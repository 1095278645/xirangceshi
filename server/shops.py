"""shops.py — 多店 / 多用户（店铺与用户注册表 + 权限 + 店上下文）

## 为什么这样设计

原先是**单店单用户**：一个库就是一个店，没有店/用户概念。店主开第二家店就废了，
也不支持"老板记账、店员只读"。

两种加多租户的做法：
  - **加 shop_id 列**：要改所有查询与索引，改动面极大；
  - **一店一库**：本项目是 SQLite，跨表 JOIN 很少，每店一个 db 文件
    天然隔离、备份/迁移/删除都以"店"为单位，改动面小得多。选它。

## 关键兼容策略

`db.DB_PATH` 仍然是**默认库路径**（所有单店测试与演示都依赖它）。
多店只在「设置了店上下文」时才生效：

    get_conn()  →  shops.resolve_db_path() or db.DB_PATH

这样：
  - 不设置上下文 → 行为与单店完全一致（404 个测试无需改动）
  - 设置了上下文 → 自动切到该店的库

## 注册表

店与用户是**跨店**的，所以存在独立的注册表库 `data/registry.db`；
每个店的业务数据在 `data/shops/<id>.db`（老库文件继续作为默认店使用）。

角色：owner（店主，可管店/管人/管账）、admin（管账+管人）、staff（记账号）。
"""
from __future__ import annotations

import contextvars
import logging
import secrets
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

import config

log = logging.getLogger("shops")

__all__ = [
    "ROLES", "list_shops", "get_shop", "create_shop", "update_shop", "delete_shop",
    "list_users", "create_user", "update_user", "delete_user", "find_user_by_token",
    "grant", "revoke", "user_shops", "shop_users",
    "current_shop_id", "set_current_shop", "reset_current_shop", "resolve_db_path",
    "use_shop", "set_default_shop",
    "DEFAULT_SHOP_ID", "SHOPS_DIR", "REGISTRY_PATH", "shops_dir", "registry_path",
    "db_path_for", "init_registry", "has_permission", "ROLE_PERMS",
    "find_shop_by_collection_token", "default_shop_id",
]

ROLES = ("owner", "admin", "staff")

# 角色 → 权限集合
ROLE_PERMS = {
    "owner": {"read", "write", "manage_shop", "manage_user"},
    "admin": {"read", "write", "manage_user"},
    "staff": {"read", "write"},
}

DEFAULT_SHOP_ID = 1          # 老库（data/ai_shopkeeper.db）作为 1 号店


def shops_dir() -> Path:
    """店铺库目录（实时读取 config.DATA_DIR，便于测试重定向）。"""
    return Path(config.DATA_DIR) / "shops"


def registry_path() -> Path:
    """注册表库路径（实时读取 config.DATA_DIR）。"""
    return Path(config.DATA_DIR) / "registry.db"


# 兼容外部按常量引用（值在 import 时确定，函数版本才是实时的）
SHOPS_DIR = shops_dir()
REGISTRY_PATH = registry_path()

# 当前请求/线程的店 id；None 表示"不启用多店"（用 db.DB_PATH）
_current_shop: contextvars.ContextVar[int | None] = contextvars.ContextVar(
    "current_shop", default=None)


# ---------------- 店上下文 ----------------

def current_shop_id() -> int | None:
    return _current_shop.get()


def set_current_shop(shop_id: int | None):
    """设置当前店，返回 contextvars Token（调用方负责 reset）。

    HTTP 中间件用这对函数（跨 await 需要自己控制生命周期）；
    普通同步代码建议用下面的 use_shop() 上下文管理器。
    """
    return _current_shop.set(shop_id)


@contextmanager
def use_shop(shop_id: int | None):
    """`with use_shop(2):` —— 在这个块内所有数据访问都落在 2 号店。"""
    token = _current_shop.set(shop_id)
    try:
        yield shop_id
    finally:
        reset_current_shop(token)


def reset_current_shop(token) -> None:
    try:
        _current_shop.reset(token)
    except ValueError:
        _current_shop.set(None)


def resolve_db_path() -> Path | None:
    """当前上下文对应的库路径；未设店上下文时返回 None（调用方用默认路径）。"""
    sid = current_shop_id()
    if sid is None:
        return None
    if sid == DEFAULT_SHOP_ID:
        # 默认店**必须**跟随 db.DB_PATH，而不是 config.DB_PATH。
        # 原因：db.DB_PATH 是运行期唯一真实来源（测试与脚本都靠改它重定向），
        # config.DB_PATH 只是配置值。早期实现返回 Path(config.DB_PATH)，
        # 于是"带店上下文"的请求会落到 config 指向的库 ——
        # 真实后果是 HTTP 用例直接读写演示库/生产库（实测演示数据被读成
        # 207 笔流水、10 条收款单，一个本该为 1 的断言变成 10）。
        return _active_default_db()
    return db_path_for(sid)


def _active_default_db() -> Path:
    """运行期默认库路径：以 db.DB_PATH 为准，取不到时退回配置值。"""
    try:
        import db
        if getattr(db, "DB_PATH", None):
            return Path(db.DB_PATH)
    except Exception:  # noqa: BLE001  导入期异常时按配置值处理
        pass
    return Path(config.DB_PATH)


def db_path_for(shop_id: int) -> Path:
    if shop_id == DEFAULT_SHOP_ID:
        return Path(config.DB_PATH)
    return shops_dir() / f"shop-{shop_id}.db"


# ---------------- 注册表 ----------------

@contextmanager
def _conn():
    """注册表连接（退出时提交 + **一定关闭**）。

    必须自己写：`with sqlite3.connect(...)` 只提交事务、不关闭连接，在 Windows 下
    会占住 registry.db 句柄（实测临时目录删不掉，WinError 32）。
    `contextlib.closing` 又会先关连接再提交，直接报 "Cannot operate on a closed
    database"。两者都不能直接套用。
    """
    path = registry_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 5000")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_registry() -> None:
    """建注册表 + 保证默认店存在（幂等）。"""
    shops_dir().mkdir(parents=True, exist_ok=True)
    with _conn() as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS shops (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            name       TEXT NOT NULL,
            db_file    TEXT DEFAULT '',
            status     TEXT NOT NULL DEFAULT 'active',
            note       TEXT DEFAULT '',
            created_at TEXT DEFAULT (datetime('now','localtime'))
        );
        CREATE TABLE IF NOT EXISTS users (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            name       TEXT NOT NULL,
            token      TEXT UNIQUE NOT NULL,
            role       TEXT NOT NULL DEFAULT 'staff',
            status     TEXT NOT NULL DEFAULT 'active',
            note       TEXT DEFAULT '',
            default_shop_id INTEGER,
            created_at TEXT DEFAULT (datetime('now','localtime'))
        );
        CREATE TABLE IF NOT EXISTS memberships (
            id      INTEGER PRIMARY KEY AUTOINCREMENT,
            shop_id INTEGER NOT NULL REFERENCES shops(id) ON DELETE CASCADE,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            role    TEXT NOT NULL DEFAULT 'staff',
            UNIQUE(shop_id, user_id)
        );
        """)
        row = conn.execute("SELECT id FROM shops WHERE id=?", (DEFAULT_SHOP_ID,)).fetchone()
        if not row:
            conn.execute(
                "INSERT INTO shops(id, name, db_file, status, note) "
                "VALUES(?,?,?, 'active', ?)",
                (DEFAULT_SHOP_ID, "默认店（原有数据）", str(config.DB_PATH),
                 "老库作为默认店，保持向后兼容"))
        _migrate_registry(conn)


# 注册表版本：新增列时 +1（与业务库的 SCHEMA_VERSION 同理，
# 避免老注册表缺列时撞 "no such column"）
REGISTRY_VERSION = 1


def _migrate_registry(conn) -> None:
    """注册表自身的轻量迁移（幂等）。"""
    current = conn.execute("PRAGMA user_version").fetchone()[0]
    if current >= REGISTRY_VERSION:
        return
    cols = {r[1] for r in conn.execute("PRAGMA table_info(users)").fetchall()}
    if "default_shop_id" not in cols:
        # 老注册表（由 CREATE TABLE IF NOT EXISTS 建出、没有该列）
        conn.execute("ALTER TABLE users ADD COLUMN default_shop_id INTEGER")
    conn.execute(f"PRAGMA user_version = {REGISTRY_VERSION}")


# ---------------- 店 ----------------

from shops_store import (  # noqa: F401  L1 外移后 re-export，保持对外接口
    list_shops, get_shop, create_shop, update_shop, delete_shop, list_users, create_user, set_default_shop, update_user, delete_user, find_user_by_token, grant, revoke, user_shops, shop_users,
)

def has_permission(role: str, perm: str) -> bool:
    return perm in ROLE_PERMS.get(role, set())


def default_shop_id() -> int:
    """默认店 id（老库）。即使注册表里被人改过名字也认这个 id。"""
    return DEFAULT_SHOP_ID


def find_shop_by_collection_token(token: str) -> int | None:
    """按收款请求 token 反查它属于哪家店。

    为什么必须查：公开收款页（/pay/<token>、/api/pay/<token>/...）是**顾客**打开的，
    顾客手机上没有店主令牌，中间件拿不到店上下文。如果直接落到默认店，多店场景下
    顾客扫 B 店的码会打不开（或更糟：显示 A 店的信息）。token 是 22 字符随机串、
    全局唯一，所以可以安全地据此定位店铺。
    """
    if not token:
        return None
    import db          # 延迟导入，避免导入环
    # 先试默认店（单店/演示场景，命中即返回，零额外开销）
    order = [DEFAULT_SHOP_ID]
    try:
        order += [s["id"] for s in list_shops() if s["id"] != DEFAULT_SHOP_ID]
    except sqlite3.Error as e:  # noqa: BLE001
        log.warning("店铺列表读取失败（仅按默认店查收款码）：%s", e)
    for sid in order:
        path = db_path_for(sid)
        if not path.exists():
            continue
        try:
            with use_shop(sid):
                req = db.get_collection_by_token(token)
        except (sqlite3.Error, OSError) as e:  # noqa: BLE001
            log.warning("店铺 %s 查收款码失败：%s", sid, e)
            continue
        if req:
            return sid
    return None
