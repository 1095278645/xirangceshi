"""
SQLite 数据层（聚合入口）：连接/建表 + 按业务域拆分的查询模块。

- db.py 保留连接管理 get_conn、建表与迁移 init_db、以及 DB_PATH（测试可覆盖）。
- 业务查询按域拆到 db_customers / db_ledger / db_payment，这里统一 re-export，
  因此 `import db; db.list_customers()`、`from db import get_conn` 等调用方式完全不变。
- 分类映射等常量见 categories.py。
"""
from contextlib import contextmanager
from datetime import datetime, date  # noqa: F401  保持向后可用

from config import DB_PATH
from categories import (
    CATEGORY_TO_ACCOUNTS, ACCOUNT_NAMES, FRIENDLY_NAMES, detect_category,
)
import sqlite3

# 按业务域聚合查询能力（保持 db.* 命名空间向后兼容）
from db_customers import *  # noqa: F401,F403
from db_ledger import *     # noqa: F401,F403
from db_payment import *    # noqa: F401,F403
from db_arch import *       # noqa: F401,F403  领域上下文/任务队列/单店档案
from db_finance import *    # noqa: F401,F403  预算/应收应付/现金流预测
from db_stock import *      # noqa: F401,F403  库存进销存
from db_invoice import *    # noqa: F401,F403  发票台账


@contextmanager
def get_conn():
    """返回连接并在退出时提交+关闭，避免 Windows 下文件句柄泄漏"""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")      # 并发读写（后台线程 + API 线程）不互相锁库
    conn.execute("PRAGMA busy_timeout = 5000")      # 写冲突时等待最多 5 秒而非立刻报错
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