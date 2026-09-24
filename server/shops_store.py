"""shops_store.py — 店铺 CRUD 与成员/令牌管理（从 shops.py 外移，架构自检 L1）

职责：店铺列表/新建/改名/删除、成员账号、角色授权与查询。

依赖方向：本模块**不在顶层 import shops**；通过 `_core()` 延迟取用 shops.py 的原语
（`_conn / db_path_for / init_registry`），避免与 shops.py 顶层成环。
"""
from __future__ import annotations

import contextvars          # noqa: F401  与 shops.py 保持一致的导入面
import logging
import secrets
import shutil
import sqlite3
from contextlib import contextmanager  # noqa: F401
from datetime import datetime
from pathlib import Path

import config

log = logging.getLogger("shops_store")


def _core():
    """延迟取 shops.py（顶层会 re-export 本模块，避免成环）。"""
    import shops
    return shops


def list_shops() -> list[dict]:
    _core().init_registry()
    with _core()._conn() as conn:
        rows = conn.execute(
            "SELECT s.*, (SELECT COUNT(*) FROM memberships m WHERE m.shop_id=s.id) "
            "AS member_count FROM shops s ORDER BY s.id").fetchall()
    return [dict(r) for r in rows]


def get_shop(shop_id: int) -> dict | None:
    _core().init_registry()
    with _core()._conn() as conn:
        row = conn.execute("SELECT * FROM shops WHERE id=?", (shop_id,)).fetchone()
    return dict(row) if row else None


def create_shop(name: str, note: str = "", copy_from: int | None = None) -> dict:
    """新建一家店，并初始化它的业务库（复用项目标准 schema）。

    copy_from 指定时把该店的库文件复制过来（新店沿用同样的科目/配置结构）。
    """
    if not (name or "").strip():
        raise ValueError("店名不能为空")
    _core().init_registry()
    with _core()._conn() as conn:
        cur = conn.execute("INSERT INTO shops(name, note) VALUES(?,?)",
                           (name.strip(), note))
        shop_id = cur.lastrowid
        path = _core().db_path_for(shop_id)
        conn.execute("UPDATE shops SET db_file=? WHERE id=?", (str(path), shop_id))

    # 用项目的标准建表流程初始化这家店（import 放在函数内避免循环依赖）
    path.parent.mkdir(parents=True, exist_ok=True)
    if copy_from:
        src = _core().db_path_for(int(copy_from))
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
    _core().init_registry()
    with _core()._conn() as conn:
        conn.execute(f"UPDATE shops SET {', '.join(k + '=?' for k in sets)} WHERE id=?",
                     (*sets.values(), shop_id))
    return get_shop(shop_id)


def delete_shop(shop_id: int, purge: bool = False) -> dict:
    """删除店。默认只从注册表移除（数据文件保留，便于误删恢复）。

    purge=True 才会连数据文件一起删 —— 危险操作，需显式指定。
    """
    if shop_id == _core().DEFAULT_SHOP_ID:
        raise ValueError("默认店不允许删除（它是原有数据的载体）")
    _core().init_registry()
    path = _core().db_path_for(shop_id)
    with _core()._conn() as conn:
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
    _core().init_registry()
    with _core()._conn() as conn:
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
    if role not in _core().ROLES:
        raise ValueError(f"角色只能是 {_core().ROLES}")
    _core().init_registry()
    tok = token or secrets.token_urlsafe(24)
    with _core()._conn() as conn:
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
    _core().init_registry()
    with _core()._conn() as conn:
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
    if "role" in sets and sets["role"] not in _core().ROLES:
        raise ValueError(f"角色只能是 {_core().ROLES}")
    if not sets:
        raise ValueError("没有要修改的内容")
    with _core()._conn() as conn:
        conn.execute(f"UPDATE users SET {', '.join(k + '=?' for k in sets)} WHERE id=?",
                     (*sets.values(), user_id))
        if "role" in sets:
            conn.execute("UPDATE memberships SET role=? WHERE user_id=?",
                         (sets["role"], user_id))
    return {"ok": True, "user_id": user_id}


def delete_user(user_id: int) -> dict:
    _core().init_registry()
    with _core()._conn() as conn:
        conn.execute("DELETE FROM memberships WHERE user_id=?", (user_id,))
        conn.execute("DELETE FROM users WHERE id=?", (user_id,))
    return {"ok": True, "user_id": user_id}


def find_user_by_token(token: str) -> dict | None:
    """按令牌找用户（用于鉴权）。token 为空或未初始化时返回 None。"""
    if not token:
        return None
    try:
        _core().init_registry()
        with _core()._conn() as conn:
            row = conn.execute(
                "SELECT * FROM users WHERE token=? AND status='active'",
                (token,)).fetchone()
    except sqlite3.Error as e:  # noqa: BLE001
        log.warning("查询用户失败：%s", e)
        return None
    return dict(row) if row else None


# ---------------- 成员关系 ----------------

def grant(shop_id: int, user_id: int, role: str = "staff") -> dict:
    if role not in _core().ROLES:
        raise ValueError(f"角色只能是 {_core().ROLES}")
    _core().init_registry()
    with _core()._conn() as conn:
        if not conn.execute("SELECT 1 FROM shops WHERE id=?", (shop_id,)).fetchone():
            raise ValueError(f"店铺不存在：{shop_id}")
        if not conn.execute("SELECT 1 FROM users WHERE id=?", (user_id,)).fetchone():
            raise ValueError(f"用户不存在：{user_id}")
        conn.execute("INSERT OR REPLACE INTO memberships(shop_id, user_id, role) "
                     "VALUES(?,?,?)", (shop_id, user_id, role))
    return {"ok": True, "shop_id": shop_id, "user_id": user_id, "role": role}


def revoke(shop_id: int, user_id: int) -> dict:
    with _core()._conn() as conn:
        conn.execute("DELETE FROM memberships WHERE shop_id=? AND user_id=?",
                     (shop_id, user_id))
    return {"ok": True}


def user_shops(user_id: int) -> list[dict]:
    _core().init_registry()
    with _core()._conn() as conn:
        rows = conn.execute(
            "SELECT s.*, m.role AS member_role FROM memberships m "
            "JOIN shops s ON s.id=m.shop_id WHERE m.user_id=? ORDER BY s.id",
            (user_id,)).fetchall()
    return [dict(r) for r in rows]


def shop_users(shop_id: int) -> list[dict]:
    """某店的成员列表，role 取**该店内的角色**（同一个人在不同店可以是不同角色）。

    注意：user.role 是账号级默认角色，membership.role 才是这家店里的角色。
    """
    _core().init_registry()
    with _core()._conn() as conn:
        rows = conn.execute(
            "SELECT u.id, u.name, u.status, m.role AS role "
            "FROM memberships m JOIN users u ON u.id=m.user_id "
            "WHERE m.shop_id=? ORDER BY u.id", (shop_id,)).fetchall()
    return [dict(r) for r in rows]


