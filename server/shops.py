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

def list_shops() -> list[dict]:
    init_registry()
    with _conn() as conn:
        rows = conn.execute(
            "SELECT s.*, (SELECT COUNT(*) FROM memberships m WHERE m.shop_id=s.id) "
            "AS member_count FROM shops s ORDER BY s.id").fetchall()
    return [dict(r) for r in rows]


def get_shop(shop_id: int) -> dict | None:
    init_registry()
    with _conn() as conn:
        row = conn.execute("SELECT * FROM shops WHERE id=?", (shop_id,)).fetchone()
    return dict(row) if row else None


def create_shop(name: str, note: str = "", copy_from: int | None = None) -> dict:
    """新建一家店，并初始化它的业务库（复用项目标准 schema）。

    copy_from 指定时把该店的库文件复制过来（新店沿用同样的科目/配置结构）。
    """
    if not (name or "").strip():
        raise ValueError("店名不能为空")
    init_registry()
    with _conn() as conn:
        cur = conn.execute("INSERT INTO shops(name, note) VALUES(?,?)",
                           (name.strip(), note))
        shop_id = cur.lastrowid
        path = db_path_for(shop_id)
        conn.execute("UPDATE shops SET db_file=? WHERE id=?", (str(path), shop_id))

    # 用项目的标准建表流程初始化这家店（import 放在函数内避免循环依赖）
    path.parent.mkdir(parents=True, exist_ok=True)
    if copy_from:
        src = db_path_for(int(copy_from))
        if src.exists():
            _copy_schema_only(src, path)
    if not path.exists():
        _init_shop_db(path)
    log.info("已创建店铺 id=%s 名称=%s", shop_id, name)
    return get_shop(shop_id)


# 系统表：复制结构时必须保留（sqlite_sequence 记录 AUTOINCREMENT 位置）
_SYSTEM_TABLES = ("sqlite_",)


def _copy_schema_only(src: Path, dst: Path) -> None:
    """把源店的表结构复制到新店，但**不带任何业务数据**。

    两个坑：
      1. 直接 copyfile 会把源店的流水一起带过来 —— 新店一开张就有别人的账，
         实测被测试用例当场抓住。
      2. 源库处于 WAL 模式时 .db 文件本身可能不是最新的（新数据还在 -wal 里），
         直接复制等于丢数据。所以先 CHECKPOINT 再复制。
    复制后清空所有业务表（保留 schema 与 sqlite_sequence），再 VACUUM 收缩。
    """
    import shutil
    import sqlite3
    _checkpoint(src)
    shutil.copyfile(src, dst)
    # VACUUM 不能在事务里跑，所以这里不用 `with sqlite3.connect(...)`（它会开事务），
    # 改为显式 commit + close。
    conn = sqlite3.connect(str(dst))
    try:
        names = [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name NOT LIKE 'sqlite_%'").fetchall()]
        for t in names:
            conn.execute(f'DELETE FROM "{t}"')
        conn.commit()
        conn.execute("VACUUM")
    finally:
        conn.close()


def _checkpoint(path: Path) -> None:
    """把 WAL 内容合并回主库文件（复制/打包前必须做，否则会拿到旧快照）。"""
    import sqlite3
    try:
        conn = sqlite3.connect(str(path))
    except sqlite3.Error as e:  # noqa: BLE001
        log.warning("WAL 合并跳过（打不开库）：%s", e)
        return
    try:
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    except sqlite3.Error as e:  # noqa: BLE001
        log.warning("WAL 合并失败（将直接复制主库文件）：%s", e)
    finally:
        conn.close()


def _init_shop_db(path: Path) -> None:
    """在指定路径建标准业务库。

    用 db.DB_PATH_OVERRIDE 而不是"临时改 db.DB_PATH"：
    覆盖只影响本函数内的连接，不会污染调用方（例如 HTTP 请求线程）的库选择，
    也不会和店上下文打架。
    """
    import db
    path.parent.mkdir(parents=True, exist_ok=True)
    db.DB_PATH_OVERRIDE = path
    try:
        db.init_db()
    finally:
        db.DB_PATH_OVERRIDE = None


def update_shop(shop_id: int, **fields) -> dict:
    allowed = {"name", "status", "note"}
    sets = {k: v for k, v in fields.items() if k in allowed and v is not None}
    if not sets:
        raise ValueError("没有要修改的内容")
    if sets.get("status") and sets["status"] not in ("active", "archived"):
        raise ValueError("status 只能是 active / archived")
    init_registry()
    with _conn() as conn:
        conn.execute(f"UPDATE shops SET {', '.join(k + '=?' for k in sets)} WHERE id=?",
                     (*sets.values(), shop_id))
    return get_shop(shop_id)


def delete_shop(shop_id: int, purge: bool = False) -> dict:
    """删除店。默认只从注册表移除（数据文件保留，便于误删恢复）。

    purge=True 才会连数据文件一起删 —— 危险操作，需显式指定。
    """
    if shop_id == DEFAULT_SHOP_ID:
        raise ValueError("默认店不允许删除（它是原有数据的载体）")
    init_registry()
    path = db_path_for(shop_id)
    with _conn() as conn:
        conn.execute("DELETE FROM memberships WHERE shop_id=?", (shop_id,))
        conn.execute("DELETE FROM shops WHERE id=?", (shop_id,))
    purged = False
    if purge and path.exists():
        path.unlink()
        purged = True
    log.info("已删除店铺 id=%s（purge=%s）", shop_id, purged)
    return {"ok": True, "shop_id": shop_id, "purged": purged}


# ---------------- 用户 ----------------

def list_users() -> list[dict]:
    init_registry()
    with _conn() as conn:
        rows = conn.execute(
            "SELECT u.*, (SELECT GROUP_CONCAT(m.shop_id) FROM memberships m "
            "WHERE m.user_id=u.id) AS shop_ids FROM users u ORDER BY u.id").fetchall()
    out = []
    for r in rows:
        d = dict(r)
        # token 不整个回传，只给前缀便于识别
        d["token_prefix"] = (d.pop("token") or "")[:8]
        d["shop_ids"] = [int(x) for x in (d.get("shop_ids") or "").split(",") if x]
        out.append(d)
    return out


def create_user(name: str, role: str = "staff", note: str = "",
                shop_ids: list[int] | None = None, token: str | None = None) -> dict:
    if not (name or "").strip():
        raise ValueError("用户名不能为空")
    if role not in ROLES:
        raise ValueError(f"角色只能是 {ROLES}")
    init_registry()
    tok = token or secrets.token_urlsafe(24)
    with _conn() as conn:
        first_shop = (shop_ids or [None])[0]
        cur = conn.execute(
            "INSERT INTO users(name, token, role, note, default_shop_id) "
            "VALUES(?,?,?,?,?)", (name.strip(), tok, role, note, first_shop))
        uid = cur.lastrowid
        for sid in (shop_ids or []):
            if not conn.execute("SELECT 1 FROM shops WHERE id=?", (sid,)).fetchone():
                raise ValueError(f"店铺不存在：{sid}")
            conn.execute("INSERT OR REPLACE INTO memberships(shop_id, user_id, role) "
                         "VALUES(?,?,?)", (sid, uid, role))
    log.info("已创建用户 id=%s 名称=%s 角色=%s", uid, name, role)
    return {"id": uid, "name": name, "role": role, "token": tok,
            "shop_ids": shop_ids or []}


def set_default_shop(user_id: int, shop_id: int | None) -> dict:
    """设置用户默认进入的店（前端"切换店铺"用；None = 取消偏好）。"""
    init_registry()
    with _conn() as conn:
        if shop_id is not None:
            member = conn.execute(
                "SELECT 1 FROM memberships WHERE user_id=? AND shop_id=?",
                (user_id, shop_id)).fetchone()
            if not member:
                raise ValueError(f"用户 {user_id} 不属于店铺 {shop_id}")
        conn.execute("UPDATE users SET default_shop_id=? WHERE id=?",
                     (shop_id, user_id))
    return {"ok": True, "user_id": user_id, "default_shop_id": shop_id}


def update_user(user_id: int, **fields) -> dict:
    allowed = {"name", "role", "status", "note"}
    sets = {k: v for k, v in fields.items() if k in allowed and v is not None}
    if "role" in sets and sets["role"] not in ROLES:
        raise ValueError(f"角色只能是 {ROLES}")
    if not sets:
        raise ValueError("没有要修改的内容")
    with _conn() as conn:
        conn.execute(f"UPDATE users SET {', '.join(k + '=?' for k in sets)} WHERE id=?",
                     (*sets.values(), user_id))
        if "role" in sets:
            conn.execute("UPDATE memberships SET role=? WHERE user_id=?",
                         (sets["role"], user_id))
    return {"ok": True, "user_id": user_id}


def delete_user(user_id: int) -> dict:
    init_registry()
    with _conn() as conn:
        conn.execute("DELETE FROM memberships WHERE user_id=?", (user_id,))
        conn.execute("DELETE FROM users WHERE id=?", (user_id,))
    return {"ok": True, "user_id": user_id}


def find_user_by_token(token: str) -> dict | None:
    """按令牌找用户（用于鉴权）。token 为空或未初始化时返回 None。"""
    if not token:
        return None
    try:
        init_registry()
        with _conn() as conn:
            row = conn.execute(
                "SELECT * FROM users WHERE token=? AND status='active'",
                (token,)).fetchone()
    except sqlite3.Error as e:  # noqa: BLE001
        log.warning("查询用户失败：%s", e)
        return None
    return dict(row) if row else None


# ---------------- 成员关系 ----------------

def grant(shop_id: int, user_id: int, role: str = "staff") -> dict:
    if role not in ROLES:
        raise ValueError(f"角色只能是 {ROLES}")
    init_registry()
    with _conn() as conn:
        if not conn.execute("SELECT 1 FROM shops WHERE id=?", (shop_id,)).fetchone():
            raise ValueError(f"店铺不存在：{shop_id}")
        if not conn.execute("SELECT 1 FROM users WHERE id=?", (user_id,)).fetchone():
            raise ValueError(f"用户不存在：{user_id}")
        conn.execute("INSERT OR REPLACE INTO memberships(shop_id, user_id, role) "
                     "VALUES(?,?,?)", (shop_id, user_id, role))
    return {"ok": True, "shop_id": shop_id, "user_id": user_id, "role": role}


def revoke(shop_id: int, user_id: int) -> dict:
    with _conn() as conn:
        conn.execute("DELETE FROM memberships WHERE shop_id=? AND user_id=?",
                     (shop_id, user_id))
    return {"ok": True}


def user_shops(user_id: int) -> list[dict]:
    init_registry()
    with _conn() as conn:
        rows = conn.execute(
            "SELECT s.*, m.role AS member_role FROM memberships m "
            "JOIN shops s ON s.id=m.shop_id WHERE m.user_id=? ORDER BY s.id",
            (user_id,)).fetchall()
    return [dict(r) for r in rows]


def shop_users(shop_id: int) -> list[dict]:
    """某店的成员列表，role 取**该店内的角色**（同一个人在不同店可以是不同角色）。

    注意：user.role 是账号级默认角色，membership.role 才是这家店里的角色。
    """
    init_registry()
    with _conn() as conn:
        rows = conn.execute(
            "SELECT u.id, u.name, u.status, m.role AS role "
            "FROM memberships m JOIN users u ON u.id=m.user_id "
            "WHERE m.shop_id=? ORDER BY u.id", (shop_id,)).fetchall()
    return [dict(r) for r in rows]


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
