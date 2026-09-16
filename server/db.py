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


def _ensure_schema(conn) -> None:
    """确保当前库的 schema 是最新的（自动跑迁移），每个库只做一次。

    为什么需要：迁移原先只在 FastAPI 的 lifespan 里调用 `init_db()`。
    任何**在迁移之前**访问数据层的路径（直接调函数、脚本、后台线程）都会
    撞上 `no such column: status` 这类错误 —— 实测老库升级时踩到过。
    放在 get_conn 里兜底，保证"能用数据就先保证表结构对"。
    """
    key = str(DB_PATH)
    if key in _schema_ready:
        return
    # 先登记再迁移：init_db() 内部会再走 get_conn()，若不先标记就会无限递归
    # （实测 RecursionError: maximum recursion depth exceeded）。
    _schema_ready.add(key)
    try:
        has_txn = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='transactions'"
        ).fetchone()
        cols = set()
        if has_txn:
            cols = {r["name"] for r in conn.execute(
                "PRAGMA table_info(transactions)").fetchall()}
        if not has_txn or "status" not in cols:
            conn.commit()          # 先提交，避免 init_db 的 DDL 与外层事务冲突
            init_db()
    except sqlite3.Error as e:  # noqa: BLE001
        log.warning("schema 自检失败（将由调用方按需处理）：%s", e)


@contextmanager
def get_conn():
    """返回连接并在退出时提交+关闭，避免 Windows 下文件句柄泄漏"""
    conn = sqlite3.connect(DB_PATH)
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
    with get_conn() as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS customers (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            name        TEXT NOT NULL,
            phone       TEXT DEFAULT '',
            tags        TEXT DEFAULT '',
            favorite    TEXT DEFAULT '',
            last_visit  TEXT DEFAULT '',
            created_at  TEXT DEFAULT (datetime('now','localtime'))
        );

        CREATE TABLE IF NOT EXISTS memories (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            customer_id INTEGER NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
            content     TEXT NOT NULL,
            created_at  TEXT DEFAULT (datetime('now','localtime'))
        );

        CREATE TABLE IF NOT EXISTS transactions (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            customer_id INTEGER REFERENCES customers(id) ON DELETE SET NULL,
            trans_type  TEXT NOT NULL DEFAULT 'income' CHECK(trans_type IN ('income','expense')),
            category    TEXT DEFAULT '主营业务收入',
            item        TEXT DEFAULT '',
            amount      REAL DEFAULT 0,
            counterparty TEXT DEFAULT '',
            note        TEXT DEFAULT '',
            created_at  TEXT DEFAULT (datetime('now','localtime'))
        );

        CREATE TABLE IF NOT EXISTS vouchers (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            voucher_no  TEXT UNIQUE NOT NULL,
            voucher_date TEXT NOT NULL,
            summary     TEXT,
            transaction_id INTEGER REFERENCES transactions(id) ON DELETE CASCADE,
            status      TEXT DEFAULT 'approved',
            created_at  TEXT DEFAULT (datetime('now','localtime'))
        );

        CREATE TABLE IF NOT EXISTS voucher_entries (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            voucher_id  INTEGER NOT NULL REFERENCES vouchers(id) ON DELETE CASCADE,
            account_code TEXT NOT NULL,
            account_name TEXT NOT NULL,
            direction   TEXT NOT NULL CHECK(direction IN ('debit','credit')),
            amount      REAL NOT NULL
        );

        CREATE TABLE IF NOT EXISTS reminders (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            customer_id INTEGER NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
            content     TEXT NOT NULL,
            done        INTEGER DEFAULT 0,
            created_at  TEXT DEFAULT (datetime('now','localtime'))
        );

        -- 收款账户（双通道：微信支付商户号 / 聚合支付）
        CREATE TABLE IF NOT EXISTS payment_sources (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            source_type TEXT NOT NULL DEFAULT 'wechat' CHECK(source_type IN ('wechat','aggregate')),
            name        TEXT DEFAULT '',
            mchid       TEXT DEFAULT '',
            appid       TEXT DEFAULT '',
            cert_path   TEXT DEFAULT '',
            private_key_path TEXT DEFAULT '',
            api_v3_key  TEXT DEFAULT '',
            enabled     INTEGER DEFAULT 0,
            created_at  TEXT DEFAULT (datetime('now','localtime'))
        );

        -- 账单同步日志
        CREATE TABLE IF NOT EXISTS bill_sync_log (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            source_id   INTEGER NOT NULL REFERENCES payment_sources(id) ON DELETE CASCADE,
            bill_date   TEXT NOT NULL,
            status      TEXT NOT NULL DEFAULT 'success' CHECK(status IN ('success','error','empty')),
            fetched     INTEGER DEFAULT 0,
            imported    INTEGER DEFAULT 0,
            skipped     INTEGER DEFAULT 0,
            error       TEXT DEFAULT '',
            created_at  TEXT DEFAULT (datetime('now','localtime'))
        );

        -- 领域上下文（按业务域独立的经营记忆，对标 TinyAGI workspace 隔离）
        CREATE TABLE IF NOT EXISTS domain_context (
            domain      TEXT NOT NULL,
            key         TEXT NOT NULL,
            value       TEXT DEFAULT '',
            updated_at  TEXT DEFAULT (datetime('now','localtime')),
            PRIMARY KEY (domain, key)
        );

        -- 任务队列（心跳/日报推送等复用，pending→running→done/dead）
        CREATE TABLE IF NOT EXISTS job_tasks (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            task_type   TEXT NOT NULL,
            payload     TEXT DEFAULT '',
            status      TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','running','done','dead')),
            retries     INTEGER DEFAULT 0,
            max_retries INTEGER DEFAULT 5,
            error       TEXT DEFAULT '',
            result      TEXT DEFAULT '',
            created_at  TEXT DEFAULT (datetime('now','localtime')),
            updated_at  TEXT DEFAULT (datetime('now','localtime'))
        );

        -- 单店档案（单店经营引擎的输入沉淀，可随时复用诊断）
        CREATE TABLE IF NOT EXISTS store_profiles (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            name            TEXT NOT NULL DEFAULT '',
            biz_type        TEXT DEFAULT '餐饮',
            gross_margin    REAL,
            rent            REAL DEFAULT 0,
            salary          REAL DEFAULT 0,
            utilities       REAL DEFAULT 0,
            total_investment REAL DEFAULT 0,
            cash_on_hand    REAL DEFAULT 0,
            traffic         TEXT DEFAULT '一般',
            competitor      TEXT DEFAULT '一般',
            updated_at      TEXT DEFAULT (datetime('now','localtime'))
        );

        -- 月度预算（亲民：每月计划花多少/进多少）
        CREATE TABLE IF NOT EXISTS budgets (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            month       TEXT NOT NULL,
            scope       TEXT NOT NULL DEFAULT 'expense' CHECK(scope IN ('income','expense')),
            category    TEXT DEFAULT '',
            amount      REAL DEFAULT 0,
            note        TEXT DEFAULT '',
            created_at  TEXT DEFAULT (datetime('now','localtime'))
        );

        -- 应收应付赊账台账（亲民：谁欠我钱/我欠谁钱）
        CREATE TABLE IF NOT EXISTS debts (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            party       TEXT DEFAULT '',
            kind        TEXT NOT NULL DEFAULT 'receivable' CHECK(kind IN ('receivable','payable')),
            amount      REAL NOT NULL DEFAULT 0,
            balance     REAL DEFAULT 0,
            due_date    TEXT DEFAULT '',
            status      TEXT NOT NULL DEFAULT 'open' CHECK(status IN ('open','settled')),
            note        TEXT DEFAULT '',
            created_at  TEXT DEFAULT (datetime('now','localtime'))
        );

        -- 商品/原材料档案（库存进销存）
        CREATE TABLE IF NOT EXISTS products (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            name        TEXT NOT NULL,
            category    TEXT DEFAULT '',
            unit        TEXT DEFAULT '',
            stock_qty   REAL DEFAULT 0,
            safety_stock REAL DEFAULT 0,
            unit_cost   REAL DEFAULT 0,
            expiry_date TEXT DEFAULT '',
            supplier    TEXT DEFAULT '',
            note        TEXT DEFAULT '',
            created_at  TEXT DEFAULT (datetime('now','localtime'))
        );

        -- 库存变动流水（入库/出库/盘点）
        CREATE TABLE IF NOT EXISTS stock_movements (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            product_id  INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
            movement_type TEXT NOT NULL CHECK(movement_type IN ('in','out','adj')),
            qty         REAL DEFAULT 0,
            note        TEXT DEFAULT '',
            created_at  TEXT DEFAULT (datetime('now','localtime'))
        );

        -- 发票台账（销项 out / 进项 in）
        CREATE TABLE IF NOT EXISTS invoices (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            kind        TEXT NOT NULL CHECK(kind IN ('out','in')),
            party       TEXT DEFAULT '',
            invoice_no  TEXT DEFAULT '',
            amount      REAL DEFAULT 0,
            rate        REAL DEFAULT 0,
            tax_amount  REAL DEFAULT 0,
            issued_date TEXT DEFAULT '',
            status      TEXT NOT NULL DEFAULT 'issued' CHECK(status IN ('issued','void')),
            note        TEXT DEFAULT '',
            created_at  TEXT DEFAULT (datetime('now','localtime'))
        );
        """)
        # 迁移：transactions 增加 source / wx_trade_id（老库升级）
        cols = [r["name"] for r in conn.execute("PRAGMA table_info(transactions)").fetchall()]
        if "source" not in cols:
            conn.execute("ALTER TABLE transactions ADD COLUMN source TEXT DEFAULT 'manual'")
        if "wx_trade_id" not in cols:
            conn.execute("ALTER TABLE transactions ADD COLUMN wx_trade_id TEXT DEFAULT ''")
        conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_wx_trade_id "
                     "ON transactions(wx_trade_id) WHERE wx_trade_id != ''")

        # ===== 交易更正相关列（记错了要能改、能作废、能红冲）=====
        # 原先一旦入账就永远改不了：AI 分类错、金额录错只能忍着；
        # 退货/退款更是无法表达。这里加状态与关联字段：
        #   status   : active（正常）/ voided（已作废，不计入合计）
        #              / refunded（已被退货，不计入合计）/
        #              refund（退货冲销记录，金额为负）
        #   parent_id: 退货记录指向原交易
        if "status" not in cols:
            conn.execute("ALTER TABLE transactions "
                         "ADD COLUMN status TEXT DEFAULT 'active'")
        if "voided_reason" not in cols:
            conn.execute("ALTER TABLE transactions "
                         "ADD COLUMN voided_reason TEXT DEFAULT ''")
        if "parent_id" not in cols:
            conn.execute("ALTER TABLE transactions ADD COLUMN parent_id INTEGER")

        # ===== 更正审计（谁在什么时候把什么改成了什么）=====
        # 财务数据必须留痕：作废/编辑不能是"悄悄消失"，否则对不上账时无从追查。
        conn.execute("""
        CREATE TABLE IF NOT EXISTS transaction_audits (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            transaction_id INTEGER NOT NULL,
            action      TEXT NOT NULL,          -- edit / void / refund
            before_json TEXT DEFAULT '',
            after_json  TEXT DEFAULT '',
            reason      TEXT DEFAULT '',
            created_at  TEXT DEFAULT (datetime('now','localtime'))
        );
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_txn_audit_tid "
                     "ON transaction_audits(transaction_id)")

        # ===== 收款请求（收款即入账）=====
        # 能力边界要说清楚：真正聚合收款需要支付牌照/商户资质，本项目做不到。
        # 这里做的是"收款请求 → 顾客扫码确认 → 店主确认到账 → 自动入账"，
        # 资金仍走店主自己的收款码；系统只负责把"收了多少钱"变成账本记录，
        # 替代原先"次日拉昨日账单"的滞后与遗漏。
        conn.execute("""
        CREATE TABLE IF NOT EXISTS payment_collections (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            token       TEXT UNIQUE NOT NULL,       -- 公开页访问凭证
            amount      REAL NOT NULL,
            item        TEXT DEFAULT '',
            customer_id INTEGER REFERENCES customers(id) ON DELETE SET NULL,
            payer_name  TEXT DEFAULT '',            -- 顾客扫码后填的称呼
            status      TEXT NOT NULL DEFAULT 'pending'
                        CHECK(status IN ('pending','paid','confirmed','cancelled')),
            note        TEXT DEFAULT '',
            transaction_id INTEGER,                 -- 确认入账后关联的交易
            created_at  TEXT DEFAULT (datetime('now','localtime')),
            paid_at     TEXT DEFAULT '',
            confirmed_at TEXT DEFAULT ''
        );
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_collection_status "
                     "ON payment_collections(status)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_collection_token "
                     "ON payment_collections(token)")

        # ===== 主动触达：订阅与投递日志 =====
        # 原先所有能力都是被动的（店主必须自己想起来打开小程序），heartbeat 生成的
        # 每日复盘、reminders 生成的熟客提醒都只躺在库里。这里支撑"推出去"。
        conn.execute("""
        CREATE TABLE IF NOT EXISTS notification_subscriptions (
            id       INTEGER PRIMARY KEY AUTOINCREMENT,
            channel  TEXT NOT NULL,          -- mock / wecom_bot / wecom_app / wechat_subscribe
            target   TEXT DEFAULT '',        -- webhook key / corpid:secret:... / openid 等
            events   TEXT DEFAULT '',        -- 逗号分隔；空=订阅全部
            enabled  INTEGER DEFAULT 1,
            name     TEXT DEFAULT '',
            created_at TEXT DEFAULT (datetime('now','localtime'))
        );
        """)
        conn.execute("""
        CREATE TABLE IF NOT EXISTS notification_logs (
            id       INTEGER PRIMARY KEY AUTOINCREMENT,
            event    TEXT NOT NULL,
            channel  TEXT NOT NULL,
            target   TEXT DEFAULT '',
            title    TEXT DEFAULT '',
            content  TEXT DEFAULT '',
            ok       INTEGER DEFAULT 0,
            attempts INTEGER DEFAULT 1,
            error    TEXT DEFAULT '',
            created_at TEXT DEFAULT (datetime('now','localtime'))
        );
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_notif_log_event "
                     "ON notification_logs(event, created_at)")

        # ===== 自适应进化层建表（拆到 db_evolution_audit.init_evolution_tables）=====
        init_evolution_tables(conn)
        # 对话轨迹表（借鉴 SkillClaw Client Capture，任务时循环采集）
        from db_evolution_trajectory import init_trajectory_tables
        init_trajectory_tables(conn)