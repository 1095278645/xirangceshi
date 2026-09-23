"""
SQLite 数据层（聚合入口）：连接/建表 + 按业务域拆分的查询模块。

- db.py 保留连接管理 get_conn、建表与迁移 init_db、以及 DB_PATH（测试可覆盖）。
- 业务查询按域拆到 db_customers / db_ledger / db_payment，这里统一 re-export，
  因此 `import db; db.list_customers()`、`from db import get_conn` 等调用方式完全不变。
- 分类映射等常量见 categories.py。
"""
from contextlib import contextmanager
from datetime import datetime, date  # noqa: F401  保持向后可用
import logging

from config import DB_PATH
from categories import (
    CATEGORY_TO_ACCOUNTS, ACCOUNT_NAMES, FRIENDLY_NAMES, detect_category,
)
import sqlite3

log = logging.getLogger("db")

# 按业务域聚合查询能力（保持 db.* 命名空间向后兼容）
from db_customers import *  # noqa: F401,F403
from db_ledger import *     # noqa: F401,F403
from db_corrections import *  # noqa: F401,F403  交易更正（编辑/作废/退货冲销）
from db_collections import *  # noqa: F401,F403  收款请求（收款即入账）
from db_payment import *    # noqa: F401,F403
from db_arch import *       # noqa: F401,F403  领域上下文/任务队列/单店档案
from db_finance import *    # noqa: F401,F403  预算/应收应付/现金流预测
from db_stock import *      # noqa: F401,F403  库存进销存
from db_invoice import *    # noqa: F401,F403  发票台账
from db_evolution import *  # noqa: F401,F403  进化：经验日志/基因/胶囊/事件


# 已确认 schema 最新的库路径（避免每次连接都重复检查）
_schema_ready: set[str] = set()

# 库路径强制覆盖（仅内部使用：多店初始化新库时临时指定，避免影响调用方上下文）
DB_PATH_OVERRIDE: "Path | None" = None


def current_db_path():
    """本次连接该用哪个库文件。

    优先级：内部强制覆盖 > 店上下文 > 默认 DB_PATH。
    没设置店上下文时结果恒等于 DB_PATH —— 单店模式与全部既有测试行为不变。
    """
    if DB_PATH_OVERRIDE is not None:
        return DB_PATH_OVERRIDE
    import shops                      # 延迟导入：shops 只在函数内 import db，无循环依赖
    return shops.resolve_db_path() or DB_PATH

# schema 版本号：每次新增表/列时 +1。init_db 完成后把库写到这个版本，
# _ensure_schema 用它判断是否需要迁移。
# （早期只检查 transactions.status 这一列，导致**新增的表不会被创建** ——
#   实测老库访问 opening_balances 时崩在 "no such table"。）
SCHEMA_VERSION = 3


def _ensure_schema(conn) -> None:
    """确保当前库的 schema 是最新的（自动跑迁移），每个库只做一次。

    为什么需要：迁移原先只在 FastAPI 的 lifespan 里调用 `init_db()`。
    任何**在迁移之前**访问数据层的路径（直接调函数、脚本、后台线程）都会
    撞上 `no such column` / `no such table` —— 实测老库升级时踩到过两次。
    放在 get_conn 里兜底，保证"能用数据就先保证表结构对"。
    """
    key = str(current_db_path())
    if key in _schema_ready:
        return
    # 先登记再迁移：init_db() 内部会再走 get_conn()，若不先标记就会无限递归
    # （实测 RecursionError: maximum recursion depth exceeded）。
    _schema_ready.add(key)
    try:
        current = conn.execute("PRAGMA user_version").fetchone()[0]
        if current < SCHEMA_VERSION:
            conn.commit()          # 先提交，避免 init_db 的 DDL 与外层事务冲突
            init_db()
            # 用底层连接写版本号：走 get_conn() 会再触发自检（递归风险）
            with _raw_conn() as c2:
                c2.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
    except sqlite3.Error as e:  # noqa: BLE001
        log.warning("schema 自检失败（将由调用方按需处理）：%s", e)


def _raw_conn():
    """底层连接（不做 schema 自检）。仅供迁移流程内部使用，避免递归。"""
    conn = sqlite3.connect(str(current_db_path()))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 5000")
    return _closing_conn(conn)


@contextmanager
def _closing_conn(conn):
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


@contextmanager
def get_conn():
    """返回连接并在退出时提交+关闭，避免 Windows 下文件句柄泄漏"""
    conn = sqlite3.connect(str(current_db_path()))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")      # 并发读写（后台线程 + API 线程）不互相锁库
    conn.execute("PRAGMA busy_timeout = 5000")      # 写冲突时等待最多 5 秒而非立刻报错
    _ensure_schema(conn)
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db():
    """建库/迁移入口：DDL 已外移到 db_schema.init_schema（本函数保持行为不变）。"""
    from db_schema import init_schema
    with get_conn() as conn:
        init_schema(conn)
